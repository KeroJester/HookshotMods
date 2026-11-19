"""Map lighting in Blender (RESEARCH_NOTES.md §87): the runtime lights as Blender lights (moved / turned / recoloured /
added / deleted in the viewport, exported back), the baked lights as reference lights for the Eevee look, and the
environment (sun, ambient, hemisphere fill, exposure, white balance, fog, bloom per time of day) as scene properties.

Units: the game's environment is physical (lux, EV). Blender gets sun strength = lux / 683 (W/m2) and a view exposure
of log2(683 / 1.2) - EV stops, so an EV 14 noon looks like a normal Blender daylight scene. Runtime lights (not
exposure linked) are scaled to cancel the view exposure (light_scale per unit of Intensity)."""
import json
import math

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)
from mathutils import Vector

from .core import lights as L
from .scene import GAME_TO_BLENDER, collection

G2B3 = GAME_TO_BLENDER.to_3x3()
B2G3 = G2B3.inverted()
EV_OFFSET = math.log2(683 / 1.2)          # (view exposure = EV_OFFSET - EV)


def env_props(context):
    return context.scene.ootr_env


def view_exposure(ev):
    return EV_OFFSET - ev


# ------------------------------------------------------------------------------------------------ light properties
def _light_update(self, context):
    apply_light(self.id_data, context)


class OOTR_LightProps(bpy.types.PropertyGroup):
    """The game's values of a runtime light (on the Blender light data)."""
    enabled: BoolProperty(default=False)
    kind: EnumProperty(name='Kind', items=[('point', 'Point', ''), ('spot', 'Spot', '')], update=_light_update)
    intensity: FloatProperty(name='Intensity', default=10.0, min=0.0, soft_max=200.0, update=_light_update,
                             description="The game's Intensity")
    use_temperature: BoolProperty(name='Use Temperature', default=True, update=_light_update)
    temperature: FloatProperty(name='Temperature', default=3000.0, min=1000.0, max=20000.0, subtype='TEMPERATURE' if hasattr(bpy.types, 'NodeSocketFloatColorTemperature') else 'NONE',
                               update=_light_update, description='Colour temperature in kelvin')
    color: FloatVectorProperty(name='Color', subtype='COLOR', size=3, default=(1, 1, 1), min=0, max=1, update=_light_update)
    radius: FloatProperty(name='Source Radius', default=0.1, min=0.0, soft_max=2.0, unit='LENGTH', update=_light_update)
    attenuation: FloatProperty(name='Attenuation Distance', default=2.5, min=0.0, soft_max=50.0, unit='LENGTH',
                               description="SourceAttenuationDistance: how far the light reaches", update=_light_update)
    inner: FloatProperty(name='Inner Angle', default=15.0, min=0.0, max=89.0, update=_light_update)
    outer: FloatProperty(name='Outer Angle', default=30.0, min=0.1, max=89.9, update=_light_update)
    flicker: BoolProperty(name='Flicker', default=False)
    flicker_speed: FloatProperty(name='Flicker Speed', default=0.2, min=0.0, soft_max=2.0)
    flicker_rate: FloatProperty(name='Flicker Amount', default=0.1, min=0.0, max=1.0)


SETTING_MAP = (('intensity', 'Intensity'), ('use_temperature', 'IsUseColorTemperature'), ('temperature', 'ColorTemperature'),
               ('radius', 'SourceRadius'), ('attenuation', 'SourceAttenuationDistance'), ('inner', 'InnerAngle'),
               ('outer', 'OuterAngle'), ('flicker', 'IsEnableFlickering'), ('flicker_speed', 'FlickeringSpeed'),
               ('flicker_rate', 'FlickeringIntensityRate'))


def apply_light(light, context=None):
    """Game values -> the Blender light's look."""
    p = light.ootr
    if not p.enabled: return
    want = 'SPOT' if p.kind == 'spot' else 'POINT'
    if light.type != want:
        light.type = want
    light.color = L.kelvin_rgb(p.temperature) if p.use_temperature else tuple(p.color)
    sc = (context or bpy.context).scene
    e = getattr(sc, 'ootr_env', None)
    scale = (e.light_scale if e else 30.0) / (2 ** view_exposure(e.current().exposure if e and e.palettes else 14.0))
    light.energy = p.intensity * scale
    light.shadow_soft_size = p.radius
    if hasattr(light, 'use_custom_distance'):
        light.use_custom_distance = p.attenuation > 0
        light.cutoff_distance = max(p.attenuation * 4.0, 0.5) if p.attenuation > 0 else 40.0
    if light.type == 'SPOT':
        outer = max(p.outer, 0.1)
        light.spot_size = math.radians(min(outer * 2.0, 179.0))
        light.spot_blend = max(0.0, min(1.0, 1.0 - p.inner / outer))


def light_from_setting(light, kind, setting):
    p = light.ootr
    p.enabled = True
    p.kind = 'spot' if kind == 'spot' else 'point'
    for attr, key in SETTING_MAP:
        if key in setting:
            setattr(p, attr, type(getattr(p, attr))(setting[key]))
    if 'Color' in setting:
        c = setting['Color']; p.color = (float(c.get('R', 1)), float(c.get('G', 1)), float(c.get('B', 1)))
    if 'IsUseColorTemperature' not in setting:
        p.use_temperature = 'Color' not in setting


def setting_from_light(light):
    p = light.ootr
    s = {key: getattr(p, attr) for attr, key in SETTING_MAP}
    if p.kind != 'spot':
        for k in ('InnerAngle', 'OuterAngle'): s.pop(k)
    s['Color'] = {'R': p.color[0], 'G': p.color[1], 'B': p.color[2], 'A': 1.0}
    return s


def orient(ob, lat, lon):
    d = G2B3 @ Vector(L.direction(lat, lon))
    ob.rotation_mode = 'QUATERNION'
    ob.rotation_quaternion = Vector((0, 0, -1)).rotation_difference(d)


def game_direction(ob):
    d = ob.matrix_world.to_3x3() @ Vector((0, 0, -1))
    return tuple(B2G3 @ d.normalized())


# ------------------------------------------------------------------------------------------------ environment
def _env_update(self, context):
    apply_env(context)


class OOTR_PaletteProps(bpy.types.PropertyGroup):
    """One time of day's palette (the values this editor exposes; the rest of the palette stays the game's)."""
    name: StringProperty()
    hour: IntProperty()
    original: StringProperty()     # (json: the resolved values when imported)
    sun_color: FloatVectorProperty(name='Sun Color', subtype='COLOR', size=3, default=(1, 1, 1), min=0, update=_env_update)
    sun_intensity: FloatProperty(name='Sun (lux)', default=100000.0, min=0.0, soft_max=200000.0, update=_env_update)
    ambient_color: FloatVectorProperty(name='Ambient Color', subtype='COLOR', size=3, default=(1, 1, 1), min=0, update=_env_update)
    ambient_use_temperature: BoolProperty(name='Ambient Uses Temperature', default=True, update=_env_update)
    ambient_temperature: FloatProperty(name='Ambient Temperature', default=5500.0, min=1000.0, max=20000.0, update=_env_update)
    ambient_intensity: FloatProperty(name='Ambient (BgDif)', default=110000.0, min=0.0, soft_max=300000.0, update=_env_update,
                                     description='BgDifIntensity: the overall ambient light')
    ambient_scale: FloatProperty(name='Ambient Scale', default=1.0, min=0.0, soft_max=4.0, update=_env_update,
                                 description='AmbientIntensity')
    hemi_top: FloatVectorProperty(name='Sky Fill', subtype='COLOR', size=3, default=(1, 1, 1), min=0, update=_env_update)
    hemi_bottom: FloatVectorProperty(name='Ground Fill', subtype='COLOR', size=3, default=(0.25, 0.25, 0.25), min=0, update=_env_update)
    hemi_intensity: FloatProperty(name='Fill (Hemi)', default=50.0, min=0.0, soft_max=2000.0, update=_env_update,
                                  description='HemiLightIntensity: fill light for surfaces facing away from the sun (softens shadows)')
    exposure: FloatProperty(name='Exposure (EV, lower = brighter)', default=14.0, soft_min=6.0, soft_max=18.0, update=_env_update,
                            description='ExposureValue: each step down doubles the brightness (the game: about 7.5 at night, 14 at noon)')
    shadow_exposure: FloatProperty(name='Shadow Exposure', default=-1.0, soft_min=-4.0, soft_max=4.0, update=_env_update,
                                   description='ExposureShadowCorrect: brightness of shadowed areas (game only)')
    white_balance: FloatProperty(name='White Balance (K)', default=6500.0, min=1500.0, max=15000.0, update=_env_update)
    tint: FloatProperty(name='Tint', default=0.0, soft_min=-1.0, soft_max=1.0, update=_env_update)
    fog_color: FloatVectorProperty(name='Fog Color', subtype='COLOR', size=3, default=(0, 0, 0), min=0, update=_env_update)
    fog_start: FloatProperty(name='Fog Start', default=0.0, min=0.0, unit='LENGTH', update=_env_update)
    fog_end: FloatProperty(name='Fog End', default=600.0, min=0.0, unit='LENGTH', update=_env_update)
    volume_fog: BoolProperty(name='Volume Fog', default=True, update=_env_update)
    fog_density: FloatProperty(name='Fog Density', default=1.0, min=0.0, soft_max=5.0, update=_env_update,
                               description='Scales every density of the volume fog (VolumeFogParams1 / 2): 0 clear, 1 as the game')
    fog_params: StringProperty()   # (json: the palette's VolumeFogParams1 / 2 when imported)
    bloom: FloatProperty(name='Bloom', default=0.1, min=0.0, soft_max=1.0, update=_env_update)


PALETTE_MAP = (('sun_intensity', 'SkySunColorIntensity'), ('ambient_use_temperature', 'BgDifUseTemperature'),
               ('ambient_temperature', 'BgDifTemperature'), ('ambient_intensity', 'BgDifIntensity'),
               ('ambient_scale', 'AmbientIntensity'), ('hemi_intensity', 'HemiLightIntensity'), ('exposure', 'ExposureValue'),
               ('shadow_exposure', 'ExposureShadowCorrect'), ('white_balance', 'WhiteBalanceTemperature'),
               ('tint', 'WhiteBalanceTint'), ('fog_start', 'FogStart'), ('fog_end', 'FogEnd'),
               ('volume_fog', 'VolumeFogIsEnabled'), ('bloom', 'BloomIntensity'))
PALETTE_COLORS = (('sun_color', 'SkySunColor'), ('ambient_color', 'BgDifColor'), ('hemi_top', 'HemiLightColorTop'),
                  ('hemi_bottom', 'HemiLightColorBottom'), ('fog_color', 'FogColor'))


FOG_DENSITY_KEYS = ('MieDensity', 'NearDensity', 'FarDensity', 'RayleighDensity')


def palette_values(pal, e=None):
    """The palette's values for export; e (the environment settings): the map-wide Fill / Ambient / Brightness
    adjustments applied."""
    out = {key: getattr(pal, attr) for attr, key in PALETTE_MAP}
    if e is not None:
        out['HemiLightIntensity'] = pal.hemi_intensity * e.all_fill
        out['BgDifIntensity'] = pal.ambient_intensity * e.all_ambient
        out['ExposureValue'] = pal.exposure - e.all_brightness
    for attr, key in PALETTE_COLORS:
        c = getattr(pal, attr); out[key] = {'R': c[0], 'G': c[1], 'B': c[2], 'A': 1.0}
    if abs(pal.fog_density - 1.0) > 1e-4 and pal.fog_params:     # (only when changed: the scaled fog blocks)
        for name, block in json.loads(pal.fog_params).items():
            out[name] = {k: (float(v) * pal.fog_density if k in FOG_DENSITY_KEYS else v) for k, v in block.items()}
    return out


def palette_from(pal, values):
    for attr, key in PALETTE_MAP:
        if key in values:
            setattr(pal, attr, type(getattr(pal, attr))(values[key]))
    for attr, key in PALETTE_COLORS:
        if key in values:
            c = values[key]; setattr(pal, attr, (float(c.get('R', 0)), float(c.get('G', 0)), float(c.get('B', 0))))
    if 'VolumeFogParams1' in values or 'VolumeFogParams2' in values:
        pal.fog_params = json.dumps({k: values[k] for k in ('VolumeFogParams1', 'VolumeFogParams2') if k in values})
    pal.fog_density = 1.0
    pal.original = json.dumps(palette_values(pal))


def _time_items(self, context):
    return [(str(i), f'{p.name} ({p.hour}:00)', '') for i, p in enumerate(self.palettes)] or [('0', '(no palettes)', '')]


class OOTR_EnvProps(bpy.types.PropertyGroup):
    banc: StringProperty()
    azimuth: FloatProperty(name='Sun Azimuth', default=45.0, soft_min=-180.0, soft_max=360.0, update=_env_update,
                           description='MainLightAzimuth (degrees)')
    elevation: FloatProperty(name='Sun Elevation', default=55.0, min=-90.0, max=90.0, update=_env_update,
                             description='MainLightElevation (degrees)')
    original_sun: StringProperty()
    cloud_shadow: BoolProperty(name='Cloud Shadows', default=False, description='CloudShadowOnOff')
    palettes: CollectionProperty(type=OOTR_PaletteProps)
    all_fill: FloatProperty(name='Fill ×', default=1.0, min=0.0, soft_max=20.0, update=_env_update,
                            description='Multiplies the fill light (softer shadows) of every time of day')
    all_ambient: FloatProperty(name='Ambient ×', default=1.0, min=0.0, soft_max=5.0, update=_env_update,
                               description='Multiplies the ambient light of every time of day')
    all_brightness: FloatProperty(name='Brightness (EV)', default=0.0, soft_min=-3.0, soft_max=3.0, update=_env_update,
                                  description='Brightens (+) or darkens (-) every time of day, in camera stops')
    time: EnumProperty(name='Time of Day', items=_time_items, update=_env_update)
    light_scale: FloatProperty(name='Light Preview Scale', default=30.0, min=0.0, soft_max=200.0,
                               update=lambda s, c: [apply_light(l, c) for l in bpy.data.lights if l.ootr.enabled],
                               description='Blender watts per unit of the game Intensity (preview only)')
    baked_scale: FloatProperty(name='Baked Light Scale', default=1.0, min=0.0, soft_max=50.0, update=_env_update,
                               description='Preview strength of the baked lights (preview only)')
    game_look: BoolProperty(name='Game Look', default=True, update=_env_update,
                            description='Drive the Blender sun, world and exposure from these values')

    def current(self):
        if not self.palettes: return None
        try: i = int(self.time)
        except (TypeError, ValueError): i = 0
        return self.palettes[min(i, len(self.palettes) - 1)]


def sun_object(context):
    sun = bpy.data.objects.get('OoT Sun')
    if sun is None:
        sun = bpy.data.objects.new('OoT Sun', bpy.data.lights.new('OoT Sun', 'SUN'))
        context.scene.collection.objects.link(sun)
    return sun


def to_sun_vector(az, el, offset=0.0, sign=1.0):
    a, e = math.radians(sign * az + offset), math.radians(el)
    return (G2B3 @ Vector((math.sin(a) * math.cos(e), math.sin(e), math.cos(a) * math.cos(e)))).normalized()


def apply_env(context):
    """Scene env properties -> Blender sun, world, exposure, white balance, bloom, baked light strengths."""
    sc = context.scene
    e = sc.ootr_env
    pal = e.current()
    if not e.game_look or pal is None: return
    sun = sun_object(context)
    to_sun = to_sun_vector(e.azimuth, e.elevation)
    sun.rotation_mode = 'XYZ'
    sun.rotation_euler = (-to_sun).to_track_quat('-Z', 'Y').to_euler()
    sun.data.color = tuple(pal.sun_color)
    sun.data.energy = pal.sun_intensity / 683.0 if e.elevation > -5 else 0.0
    sun.data.angle = math.radians(1.0)
    ev = pal.exposure - e.all_brightness
    try:
        sc.view_settings.view_transform = 'AgX'
    except TypeError:
        pass
    sc.view_settings.exposure = view_exposure(ev)
    vs = sc.view_settings
    if hasattr(vs, 'use_white_balance'):
        vs.use_white_balance = True
        vs.white_balance_temperature = pal.white_balance
        vs.white_balance_tint = pal.tint * 50.0
    # world: what the camera sees = the sky texture; what lights the scene = the ambient (BgDif) + hemisphere fill
    world = sc.world or bpy.data.worlds.new('OoT Sky')
    sc.world = world
    world.use_nodes = True
    nt = world.node_tree
    nodes = {n.name: n for n in nt.nodes}
    def node(name, typ, loc):
        n = nodes.get(name)
        if n is None or n.bl_idname != typ:
            if n is not None: nt.nodes.remove(n)
            n = nt.nodes.new(typ); n.name = name; n.location = loc
        return n
    out = next((n for n in nt.nodes if n.type == 'OUTPUT_WORLD'), None) or node('Output', 'ShaderNodeOutputWorld', (600, 0))
    sky = node('OoT Sky', 'ShaderNodeTexSky', (-500, 250))
    sky_bg = node('OoT Sky Background', 'ShaderNodeBackground', (-200, 250))
    geo = node('OoT Normal', 'ShaderNodeNewGeometry', (-900, -200))
    sep = node('OoT Up', 'ShaderNodeSeparateXYZ', (-700, -200))
    ramp = node('OoT Fill Ramp', 'ShaderNodeMapRange', (-500, -200))
    mix = node('OoT Fill Mix', 'ShaderNodeMix', (-300, -150))
    amb_bg = node('OoT Ambient', 'ShaderNodeBackground', (-100, -150))
    path = node('OoT Light Path', 'ShaderNodeLightPath', (-100, 100))
    sel = node('OoT Camera Or Light', 'ShaderNodeMixShader', (300, 0))
    links = nt.links
    def link(a, b):
        if not any(l.from_socket == a and l.to_socket == b for l in links): links.new(a, b)
    for attr, val in (('sun_elevation', math.radians(e.elevation)), ('sun_rotation', math.atan2(to_sun.x, to_sun.y)),
                      ('sun_disc', False)):
        if hasattr(sky, attr): setattr(sky, attr, val)
    link(sky.outputs['Color'], sky_bg.inputs['Color'])
    sky_bg.inputs['Strength'].default_value = 0.3 / max(2 ** view_exposure(ev), 1e-6) * 0.035
    link(geo.outputs['Normal'] if 'Normal' in geo.outputs else geo.outputs[1], sep.inputs[0])
    link(sep.outputs['Z'], ramp.inputs['Value'])
    ramp.inputs['From Min'].default_value, ramp.inputs['From Max'].default_value = -1.0, 1.0
    mix.data_type = 'RGBA'
    link(ramp.outputs['Result'], mix.inputs['Factor'])
    amb = L.kelvin_rgb(pal.ambient_temperature) if pal.ambient_use_temperature else tuple(pal.ambient_color)
    # BgDif is physical (lux-like, divided by the exposure like the sun); the hemisphere fill is exposure linked
    # (HemiLightIsEnableExposureLink): its on-screen brightness does not change with the EV
    a_rad = pal.ambient_intensity * e.all_ambient * pal.ambient_scale / (683.0 * math.pi) * 0.15
    h_rad = pal.hemi_intensity * e.all_fill * 0.001 / max(2 ** view_exposure(ev), 1e-9)
    top = [amb[i] * a_rad + pal.hemi_top[i] * h_rad for i in range(3)]
    bottom = [amb[i] * a_rad * 0.35 + pal.hemi_bottom[i] * h_rad for i in range(3)]
    mix.inputs[6].default_value = (*bottom, 1.0)
    mix.inputs[7].default_value = (*top, 1.0)
    link(mix.outputs[2], amb_bg.inputs['Color'])
    amb_bg.inputs['Strength'].default_value = 1.0
    link(path.outputs['Is Camera Ray'], sel.inputs['Fac'])
    link(amb_bg.outputs['Background'], sel.inputs[1])
    link(sky_bg.outputs['Background'], sel.inputs[2])
    link(sel.outputs['Shader'], out.inputs['Surface'])
    eevee = sc.eevee
    if hasattr(eevee, 'use_bloom'): eevee.use_bloom = pal.bloom > 0; eevee.bloom_intensity = pal.bloom * 0.5
    for ob in bpy.data.objects:
        if ob.type == 'LIGHT' and 'ootr_baked' in ob:
            ob.data.energy = float(ob['ootr_power']) * e.baked_scale / 683.0
    for l in bpy.data.lights:
        if l.ootr.enabled: apply_light(l, context)


# ------------------------------------------------------------------------------------------------ import
def import_lighting(context, game, banc, root, baked=True, report=print):
    """Runtime lights, baked lights and the environment of a map into the scene. -> summary text."""
    _, lights = L.read_scene_lights(game, banc)
    bounds = L.map_bounds(game, banc)
    lcol = collection(f'{banc} Lights', root)
    lcol['ootr_lights'] = banc
    n = 0
    away = [li for li in lights if not L.inside(bounds, li['position'])]
    root['ootr_lights_away'] = json.dumps([{k: li[k] for k in ('group', 'key', 'kind', 'position', 'lat', 'lon', 'setting_path')}
                                           | {'raw': li['raw']} for li in away], default=_json_default)
    for li in lights:
        if not L.inside(bounds, li['position']): continue     # (another part of a shared scene: kept, not shown)
        if li['kind'] not in ('point', 'spot'):
            ob = bpy.data.objects.new(f'{li["key"]} ({li["kind"]})', None)
            ob.empty_display_type = 'SPHERE'; ob.empty_display_size = 0.15
        else:
            ld = bpy.data.lights.new(li['key'], 'SPOT' if li['kind'] == 'spot' else 'POINT')
            light_from_setting(ld, li['kind'], li['setting'])
            ob = bpy.data.objects.new(li['key'], ld)
            apply_light(ld, context)
        ob.location = G2B3 @ Vector(li['position'])
        if li['kind'] == 'spot': orient(ob, li['lat'], li['lon'])
        ob['ootr_light'] = li['kind']
        ob['ootr_light_group'] = li['group']
        ob['ootr_light_key'] = li['key']
        ob['ootr_light_raw'] = json.dumps(li['raw'], default=_json_default)
        ob['ootr_light_setting'] = li['setting_path'] or ''
        ob['ootr_light_dir'] = [li['lat'], li['lon']]
        if ob.type == 'LIGHT':
            ob['ootr_light_values'] = json.dumps(setting_from_light(ob.data))   # (export: only what changed)
        lcol.objects.link(ob)
        n += 1
    nb = 0
    if baked:
        bk = L.read_baked_lights(game, banc, bounds)
        bcol = collection(f'{banc} Baked Lights', root)
        bcol['ootr_baked'] = banc
        subs = {}
        def add(name, typ, pos, rgb, size):
            nonlocal nb
            ld = bpy.data.lights.new(name, typ)
            m = max(rgb) or 1.0
            ld.color = tuple(c / m for c in rgb)
            ob = bpy.data.objects.new(name, ld)
            ob.location = G2B3 @ Vector(pos)
            ob['ootr_baked'] = typ
            ob['ootr_power'] = m
            ld.energy = m * env_props(context).baked_scale / 683.0
            ld.shadow_soft_size = size if typ != 'AREA' else 0.0
            ld.use_shadow = False                  # (stand-ins for precomputed light: soft, no shadow maps)
            if hasattr(ld, 'use_custom_distance'):
                ld.use_custom_distance = True
                ld.cutoff_distance = max(2.0, min(30.0, math.sqrt(m * 4 * math.pi / 683.0) * 2.0))
            bcol.objects.link(ob); nb += 1
            return ob, ld
        for pos, rgb, r in bk['point']:
            add('Baked Point', 'POINT', pos, rgb, r)
        for pos, d, rgb, inner, outer, r in bk['spot']:
            ob, ld = add('Baked Spot', 'SPOT', pos, rgb, r)
            ld.spot_size = math.radians(min(outer * 2, 179)); ld.spot_blend = max(0.0, 1 - inner / max(outer, 0.1))
            ob.rotation_mode = 'QUATERNION'; ob.rotation_quaternion = Vector((0, 0, -1)).rotation_difference(G2B3 @ Vector(d))
        for pos, u, v, rgb, w, h in bk['area']:
            ob, ld = add('Baked Area', 'AREA', pos, rgb, 0)
            ld.shape = 'RECTANGLE'; ld.size, ld.size_y = max(w, 0.01), max(h, 0.01)
            nrm = Vector(u).cross(Vector(v))
            ob.rotation_mode = 'QUATERNION'
            ob.rotation_quaternion = Vector((0, 0, -1)).rotation_difference(G2B3 @ nrm)
    env = load_env(context, game, banc)
    e = env_props(context)
    return f'{n} runtime lights, {nb} baked lights' + (f', environment with {len(e.palettes)} times of day' if env else '')


def load_env(context, game, banc):
    """The map's environment (sun, palettes per time of day) into the scene's OoT Lighting settings. -> env or None."""
    env = L.read_env(game, banc)
    e = env_props(context)
    if env and env['times']:
        e.banc = banc
        e.palettes.clear()
        e['azimuth'], e['elevation'] = env['azimuth'], env['elevation']
        e.cloud_shadow = env['cloud_shadow']
        e.original_sun = json.dumps([env['azimuth'], env['elevation'], env['cloud_shadow']])
        noon = 0
        for i, t in enumerate(env['times']):
            pal = e.palettes.add()
            pal.name, pal.hour = t['name'] or f'Time {i}', t['hour']
            palette_from(pal, t['palette'])
            if t['name'] == 'Noon': noon = i
        e['time'] = noon
        e.all_fill, e.all_ambient, e.all_brightness = 1.0, 1.0, 0.0     # (the loaded values already include them)
        apply_env(context)
        return env
    return None


class OOTR_OT_load_env(bpy.types.Operator):
    """Read this map's environment (sun, ambient, fill, fog... per time of day) for editing"""
    bl_idname = 'ootr.load_env'
    bl_label = 'Load Environment'
    banc: StringProperty()

    def execute(self, context):
        from . import get_game
        try:
            env = load_env(context, get_game(context), self.banc)
        except Exception as ex:
            self.report({'ERROR'}, f'{self.banc}: {ex}'); return {'CANCELLED'}
        if env is None:
            self.report({'WARNING'}, f'{self.banc}: no editable environment found'); return {'CANCELLED'}
        return {'FINISHED'}


def _json_default(o):
    return int(o) if isinstance(o, int) else str(o)


def raw_from_json(s):
    from .vendor.byml import U32
    def fix(x):
        if isinstance(x, dict): return {k: (U32(v) if k in ('LightType', 'RandomHash', 'SearchHash', 'BakeType') and isinstance(v, int) else fix(v)) for k, v in x.items()}
        if isinstance(x, list): return [fix(v) for v in x]
        return x
    return fix(json.loads(s)) if s else {}


# ------------------------------------------------------------------------------------------------ export
def lights_collection(root):
    for c in root.children:
        if c.get('ootr_lights'): return c
    return None


def gather_lights(context, root):
    """The map's runtime lights for export, or None when the map was imported without lights. Lights anywhere in
    the scene that are not in another map's collection count (new ones from Add OoT Light)."""
    lcol = lights_collection(root)
    if lcol is None: return None
    others = set()
    from .scene import map_roots
    for r in map_roots(context):
        if r == root: continue
        def collect(c):
            for ob in c.objects: others.add(ob.name)
            for ch in c.children: collect(ch)
        collect(r)
    out = []
    for ob in context.scene.objects:
        if 'ootr_light' not in ob or ob.name in others: continue
        kind = ob['ootr_light']
        if ob.type == 'LIGHT' and ob.data.ootr.enabled:
            kind = ob.data.ootr.kind
        pos = tuple(B2G3 @ ob.matrix_world.translation)
        lat, lon = tuple(ob.get('ootr_light_dir', (0.0, 0.0)))
        if kind == 'spot':
            d = game_direction(ob)
            if sum(a * b for a, b in zip(d, L.direction(lat, lon))) < 1 - 1e-5:
                lat, lon = L.lat_lon(d)
        setting = setting_from_light(ob.data) if ob.type == 'LIGHT' and ob.data.ootr.enabled else {}
        if setting and ob.get('ootr_light_setting') and ob.get('ootr_light_values'):
            was = json.loads(ob['ootr_light_values'])
            setting = {k: v for k, v in setting.items() if not L._same(was.get(k), v)}
            if not (ob.data.ootr.use_temperature) and 'Color' in setting:
                setting['IsUseColorTemperature'] = False
        out.append(dict(group=ob.get('ootr_light_group') or 'Editor', key=ob.get('ootr_light_key') or ob.name.replace(' ', '_'),
                        kind=kind, position=pos, lat=lat, lon=lon, setting=setting,
                        setting_path=ob.get('ootr_light_setting') or None, raw=raw_from_json(ob.get('ootr_light_raw', ''))))
    for li in json.loads(root.get('ootr_lights_away', '[]')):   # (outside the map: written back unchanged)
        li['position'] = tuple(li['position']); li['raw'] = raw_from_json(json.dumps(li['raw'])); li['setting'] = {}
        out.append(li)
    return out


def gather_env(context, banc):
    """The environment edits for export, or None when nothing differs from the imported values."""
    e = env_props(context)
    if e.banc != banc or not e.palettes: return None
    times, changed = {}, False
    for pal in e.palettes:
        now = palette_values(pal, e)
        orig = json.loads(pal.original or '{}')
        diff = {k: v for k, v in now.items() if not L._same(orig.get(k), v)}
        if diff: times[pal.name] = diff; changed = True
    sun = json.loads(e.original_sun or '[0, 0, false]')
    if not L._same(sun[0], e.azimuth) or not L._same(sun[1], e.elevation) or bool(sun[2]) != e.cloud_shadow: changed = True
    if not changed: return None
    return dict(azimuth=e.azimuth, elevation=e.elevation, cloud_shadow=e.cloud_shadow, times=times)


# ------------------------------------------------------------------------------------------------ operators / UI
class OOTR_OT_add_light(bpy.types.Operator):
    """Add a runtime light (exported into the map's light file) at the 3D cursor"""
    bl_idname = 'ootr.add_light'
    bl_label = 'Add OoT Light'
    bl_options = {'REGISTER', 'UNDO'}
    kind: EnumProperty(items=[('point', 'Point', ''), ('spot', 'Spot', '')])

    def execute(self, context):
        from .scene import map_roots
        roots = map_roots(context)
        target = context.scene.collection
        for r in roots:
            lc = lights_collection(r)
            if lc: target = lc; break
        ld = bpy.data.lights.new(f'OoT {self.kind.title()}', 'SPOT' if self.kind == 'spot' else 'POINT')
        light_from_setting(ld, self.kind, {'Intensity': 10.0, 'ColorTemperature': 3200.0, 'IsUseColorTemperature': True,
                                           'SourceRadius': 0.1, 'SourceAttenuationDistance': 3.0, 'InnerAngle': 15.0, 'OuterAngle': 35.0})
        ob = bpy.data.objects.new(ld.name, ld)
        ob.location = context.scene.cursor.location
        ob['ootr_light'] = self.kind
        ob['ootr_light_group'] = 'Editor'
        ob['ootr_light_key'] = ob.name.replace(' ', '_').replace('.', '_')
        ob['ootr_light_dir'] = [0.0, 0.0]
        if self.kind == 'spot': orient(ob, 0.0, 0.0)
        target.objects.link(ob)
        apply_light(ld, context)
        for o in context.selected_objects: o.select_set(False)
        ob.select_set(True); context.view_layer.objects.active = ob
        return {'FINISHED'}


class OOTR_OT_palette_to_all(bpy.types.Operator):
    """Copy this time of day's values to every time of day"""
    bl_idname = 'ootr.palette_to_all'
    bl_label = 'Copy to All Times'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        e = env_props(context); cur = e.current()
        if cur is None: return {'CANCELLED'}
        vals = palette_values(cur)
        for p in e.palettes:
            if p == cur: continue
            orig = p.original
            palette_from(p, vals); p.original = orig
        return {'FINISHED'}


def game_palettes(context, banc):
    """{time name: the game's own palette values} for the map's times of day (its edits in the map mod ignored)."""
    from . import get_game
    game = get_game(context)
    env = L.read_env(game, banc)
    pack = L.EnvPack(game)
    out = {}
    for i, t in enumerate(env['times'] if env else []):
        out[t['name']] = L.resolved(pack.get, L._original_palette(game, banc, env, i, t))
    return out


class OOTR_OT_reset_palette(bpy.types.Operator):
    """Reset this time of day to the game's own lighting"""
    bl_idname = 'ootr.reset_palette'
    bl_label = 'Reset to Game'
    bl_options = {'REGISTER', 'UNDO'}
    whole_map: BoolProperty(default=False)

    def execute(self, context):
        e = env_props(context)
        if not e.palettes: return {'CANCELLED'}
        try:
            game_vals = game_palettes(context, e.banc)
        except Exception as ex:
            self.report({'ERROR'}, f'{e.banc}: {ex}'); return {'CANCELLED'}
        targets = list(e.palettes) if self.whole_map else [e.current()]
        for pal in targets:
            vals = game_vals.get(pal.name)
            if not vals: continue
            orig = pal.original
            palette_from(pal, vals); pal.original = orig          # (export compares with what is in the mod now)
        if self.whole_map:
            e.all_fill, e.all_ambient, e.all_brightness = 1.0, 1.0, 0.0
        apply_env(context)
        self.report({'INFO'}, 'Game lighting restored: Export to apply')
        return {'FINISHED'}


class OOTR_OT_toggle_baked(bpy.types.Operator):
    """Show or hide the baked lights (their light is part of the game's precomputed lighting)"""
    bl_idname = 'ootr.toggle_baked'
    bl_label = 'Toggle Baked Lights'

    def execute(self, context):
        obs = [o for o in bpy.data.objects if 'ootr_baked' in o and o.type == 'LIGHT']
        hide = not (obs and obs[0].hide_render)
        for o in obs:
            o.hide_render = hide; o.hide_viewport = hide
        return {'FINISHED'}


class OOTR_PT_lighting(bpy.types.Panel):
    bl_label = 'OoT Lighting'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OoT'

    def draw(self, context):
        lay = self.layout
        e = env_props(context)
        ob = context.active_object
        if ob is not None and ob.type == 'LIGHT' and ob.data.ootr.enabled:
            p = ob.data.ootr
            box = lay.box()
            box.label(text=f'Light: {ob.name}', icon='LIGHT_SPOT' if p.kind == 'spot' else 'LIGHT_POINT')
            box.prop(p, 'kind', expand=True)
            box.prop(p, 'intensity')
            row = box.row(); row.prop(p, 'use_temperature', text='Temperature')
            row.prop(p, 'temperature' if p.use_temperature else 'color', text='')
            box.prop(p, 'radius'); box.prop(p, 'attenuation')
            if p.kind == 'spot':
                row = box.row(align=True); row.prop(p, 'inner'); row.prop(p, 'outer')
            row = box.row(); row.prop(p, 'flicker')
            if p.flicker:
                row = box.row(align=True); row.prop(p, 'flicker_speed', text='Speed'); row.prop(p, 'flicker_rate', text='Amount')
        elif ob is not None and ob.type == 'MESH' and __import__(__package__ + '.custom_geo', fromlist=['is_custom']).is_custom(ob):
            box = lay.box()
            box.label(text=f'Custom mesh: {ob.name}', icon='MESH_DATA')
            box.prop(ob, 'ootr_solid')
            box.prop(ob, 'ootr_game_visible')
        elif ob is not None and 'ootr_baked' in ob:
            lay.label(text='Baked light: part of the precomputed lighting', icon='INFO')
            lay.label(text='(moving it changes only the Blender preview)')
        row = lay.row(align=True)
        row.operator('ootr.add_light', text='Point', icon='LIGHT_POINT').kind = 'point'
        row.operator('ootr.add_light', text='Spot', icon='LIGHT_SPOT').kind = 'spot'
        lay.operator('ootr.toggle_baked', icon='HIDE_OFF')
        if not e.palettes:
            from .scene import map_roots
            roots = map_roots(context)
            for r in roots:
                lay.operator('ootr.load_env', text=f'Load Environment of {r["ootr_banc"]}', icon='WORLD').banc = r['ootr_banc']
            if not roots: lay.label(text='Import or create a map to edit its environment')
            return
        box = lay.box()
        box.label(text=f'Environment: {e.banc}', icon='WORLD')
        sub = box.box()
        sub.label(text='Whole map (every time of day)', icon='LIGHT_SUN')
        col = sub.column(align=True)
        col.prop(e, 'all_fill'); col.prop(e, 'all_ambient'); col.prop(e, 'all_brightness')
        op = sub.operator('ootr.reset_palette', text='Reset Whole Map to Game Lighting', icon='LOOP_BACK'); op.whole_map = True
        box.separator()
        box.prop(e, 'time')
        box.label(text='Settings below are for this time of day only', icon='INFO')
        pal = e.current()
        col = box.column(align=True)
        col.prop(e, 'azimuth'); col.prop(e, 'elevation')
        box.prop(e, 'cloud_shadow')
        col = box.column(align=True)
        col.label(text='Sun')
        col.prop(pal, 'sun_color', text=''); col.prop(pal, 'sun_intensity')
        col = box.column(align=True)
        col.label(text='Ambient')
        row = col.row(); row.prop(pal, 'ambient_use_temperature', text='Temperature')
        row.prop(pal, 'ambient_temperature' if pal.ambient_use_temperature else 'ambient_color', text='')
        col.prop(pal, 'ambient_intensity'); col.prop(pal, 'ambient_scale')
        col = box.column(align=True)
        col.label(text='Fill (softens shadows)')
        row = col.row(align=True); row.prop(pal, 'hemi_top', text=''); row.prop(pal, 'hemi_bottom', text='')
        col.prop(pal, 'hemi_intensity')
        col = box.column(align=True)
        col.label(text='Camera')
        col.prop(pal, 'exposure'); col.prop(pal, 'shadow_exposure')
        col.prop(pal, 'white_balance'); col.prop(pal, 'tint'); col.prop(pal, 'bloom')
        col = box.column(align=True)
        col.label(text='Fog')
        col.prop(pal, 'volume_fog'); col.prop(pal, 'fog_density')
        col.prop(pal, 'fog_color', text=''); row = col.row(align=True); row.prop(pal, 'fog_start'); row.prop(pal, 'fog_end')
        row = box.row(align=True)
        row.operator('ootr.palette_to_all', icon='DUPLICATE'); row.operator('ootr.reset_palette', icon='LOOP_BACK')
        col = lay.column(align=True)
        col.prop(e, 'game_look')
        col.prop(e, 'light_scale'); col.prop(e, 'baked_scale')


classes = (OOTR_LightProps, OOTR_PaletteProps, OOTR_EnvProps, OOTR_OT_add_light, OOTR_OT_palette_to_all,
           OOTR_OT_reset_palette, OOTR_OT_toggle_baked, OOTR_PT_lighting, OOTR_OT_load_env)


def register():
    for c in classes: bpy.utils.register_class(c)
    bpy.types.Light.ootr = PointerProperty(type=OOTR_LightProps)
    bpy.types.Scene.ootr_env = PointerProperty(type=OOTR_EnvProps)


def unregister():
    del bpy.types.Scene.ootr_env
    del bpy.types.Light.ootr
    for c in reversed(classes): bpy.utils.unregister_class(c)
