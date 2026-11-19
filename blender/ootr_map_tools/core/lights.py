"""Map lighting (no bpy, RESEARCH_NOTES.md §87): the runtime lights of a map, its baked lights and its environment.

Runtime lights: GameBancParam.LocationalLightScenePlacement -> System/GameGraphics/LocationalLightScenePlacement/<x>
  {GroupSetting: {group: {EnableGroupOffset, Hash, PlacementSetting: {key: light}}}, Hash}
  light: Position {X,Y,Z}, LightType (absent: point; 1420666488 spot; 399553389 amplifier), Latitude / Longitude
  (degrees; 0 / 0 points straight down, latitude tilts away from down, longitude turns about +Y), RandomHash (u32),
  PointLightSetting / SpotLightSetting / AmplifierLightSetting (Work/ paths of settings files, $parent chains):
  Color {R,G,B,A}, ColorTemperature, IsUseColorTemperature, Intensity, SourceRadius, SourceAttenuationDistance,
  InnerAngle / OuterAngle (spot, degrees), IsEnableFlickering, FlickeringSpeed, FlickeringIntensityRate, ...
  Verified in game: moved / recoloured / new lights and new settings files all work (screenshots/lighting_editor).
Baked lights: Bake/Light/<banc>.bklight.zs "BKLT" v1 {u32 points, u32 spots, u32 areas}: point 7 f32 (pos, rgb power,
  radius), spot 13 f32 (pos, dir, rgb, inner, outer, 1, radius), area 15 f32 (pos, axis u, axis v, rgb, width,
  height, w). Inputs of the offline bake only: zeroing them changes nothing in game.
Environment: GameBancParam.WorldMgrName -> WorldMgr/WorldParam/<x> (MainLightAzimuth / Elevation, CloudShadowOnOff,
  BasePalette_Bluesky -> a time table in Pack/EnvPalette.pack: one ResEnvPalette per time of day, $parent chains)."""
import copy
import math
import os
import random
import struct

from ..vendor.byml import parse_byml, write_byml, U32

GFX = 'System/GameGraphics/'
SPOT, AMPLIFIER = 1420666488, 399553389
SETTING_KEY = {'point': 'PointLightSetting', 'spot': 'SpotLightSetting', 'amplifier': 'AmplifierLightSetting'}
SETTING_DIR = {'point': 'LocationalLightPointSetting', 'spot': 'LocationalLightSpotSetting',
               'amplifier': 'LocationalLightRigAmplifierSetting'}


def work_to_rel(w):
    """Work/System/X/Y.game__gfx__Z.gyml -> System/X/Y.game__gfx__Z.bgyml"""
    return w[len('Work/'):].replace('.gyml', '.bgyml') if w and w.startswith('Work/') else None


def rel_to_work(rel):
    return 'Work/' + rel.replace('.bgyml', '.gyml')


def resolved(read, w, _seen=None):
    """A Work/ path's file with its $parent chain applied (read: rel -> dict or None); a chain that loops (a file
    naming itself) stops there."""
    _seen = set() if _seen is None else _seen
    if w in _seen: return {}
    _seen.add(w)
    y = read(work_to_rel(w))
    if y is None: return {}
    if '$parent' in y:
        base = resolved(read, y['$parent'], _seen)
        base.update({k: v for k, v in y.items() if k != '$parent'})
        return base
    return dict(y)


def banc_param(game, banc):
    """The map's GameBancParam: (rel, own file dict, resolved dict)."""
    rel = f'Banc/GameBancParam/{banc}.game__banc__GameBancParam.bgyml'
    if not game.exists(rel): return rel, {}, {}
    own = game.byml(rel)
    full = resolved(lambda r: game.byml(r) if r and game.exists(r) else None, rel_to_work(rel))
    return rel, own, full


def map_bounds(game, banc, margin=8.0):
    """The map's extent from its placements (min, max in game axes, with a margin). Rooms carved out of a bigger
    scene (DarkLinkStage: BakeUSD / lights of WaterTemple) share that scene's light files: only what is inside the
    map's own extent belongs to it."""
    from .banc import load_map
    try:
        ps = load_map(game, banc)
    except Exception:
        return None
    xs = [p.translate for p in ps if p.translate is not None]
    if not xs: return None
    return ([min(v[i] for v in xs) - margin for i in range(3)], [max(v[i] for v in xs) + margin for i in range(3)])


def inside(bounds, pos):
    return bounds is None or all(bounds[0][i] <= pos[i] <= bounds[1][i] for i in range(3))


# ---------------------------------------------------------------- directions
def direction(lat, lon):
    """Latitude / longitude (degrees) -> unit direction in game axes (Y up). 0 / 0 = straight down."""
    la, lo = math.radians(lat), math.radians(lon)
    s = math.sin(la)
    return (s * math.sin(lo), -math.cos(la), s * math.cos(lo))


def lat_lon(d):
    x, y, z = d
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    x, y, z = x / n, y / n, z / n
    la = math.degrees(math.acos(max(-1.0, min(1.0, -y))))
    if la < 1e-4 or la > 180 - 1e-4: return round(la, 4), 0.0
    return la, math.degrees(math.atan2(x, z))


# ---------------------------------------------------------------- colour
def kelvin_rgb(t):
    """Colour temperature -> linear RGB (Tanner Helland's fit, then sRGB -> linear), max channel 1."""
    t = max(1000.0, min(40000.0, t)) / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    c = [max(0.0, min(255.0, v)) / 255.0 for v in (r, g, b)]
    lin = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    m = max(lin) or 1.0
    return tuple(v / m for v in lin)


# ---------------------------------------------------------------- runtime lights
def light_kind(p):
    t = p.get('LightType')
    if not any(k in p for k in ('PointLightSetting', 'SpotLightSetting', 'AmplifierLightSetting')): return 'other'
    if t is None or (int(t) != AMPLIFIER and 'SpotLightSetting' not in p and 'PointLightSetting' in p):
        return 'point' if 'PointLightSetting' in p or t is None else 'other'
    if int(t) == SPOT: return 'spot'
    if int(t) == AMPLIFIER: return 'amplifier'
    return 'spot' if 'SpotLightSetting' in p else ('point' if 'PointLightSetting' in p else 'other')


def read_scene_lights(game, banc):
    """-> (placement rel or None, [light dict]): light = {group, key, kind, position, lat, lon, setting (resolved),
    setting_path (Work/), raw (the placement entry)}."""
    _, _, gbp = banc_param(game, banc)
    rel = work_to_rel(gbp.get('LocationalLightScenePlacement'))
    if not rel or not game.exists(rel): return None, []
    read = lambda r: game.byml(r) if r and game.exists(r) else None
    doc = game.byml(rel)
    out = []
    for group, gv in (doc.get('GroupSetting') or {}).items():
        for key, p in (gv.get('PlacementSetting') or {}).items():
            kind = light_kind(p)
            sp = p.get(SETTING_KEY.get(kind, ''), None) or p.get('PointLightSetting') or p.get('SpotLightSetting')
            P = p.get('Position', {})
            out.append(dict(group=group, key=key, kind=kind, position=(P.get('X', 0.0), P.get('Y', 0.0), P.get('Z', 0.0)),
                            lat=float(p.get('Latitude', 0.0)), lon=float(p.get('Longitude', 0.0)),
                            setting=resolved(read, sp) if sp else {}, setting_path=sp, raw=copy.deepcopy(p)))
    return rel, out


def light_color(setting):
    """A settings dict -> linear RGB the light emits (colour temperature when IsUseColorTemperature)."""
    if setting.get('IsUseColorTemperature', False) or 'Color' not in setting:
        return kelvin_rgb(float(setting.get('ColorTemperature', 6500.0)))
    c = setting['Color']
    return (float(c.get('R', 1)), float(c.get('G', 1)), float(c.get('B', 1)))


EDITABLE = ('Intensity', 'Color', 'ColorTemperature', 'IsUseColorTemperature', 'SourceRadius', 'SourceAttenuationDistance',
            'InnerAngle', 'OuterAngle', 'IsEnableFlickering', 'FlickeringSpeed', 'FlickeringIntensityRate')


def write_scene_lights(game, banc, lights, seed=None):
    """lights: [dict(group, key, kind, position, lat, lon, setting (full wanted values), setting_path or None,
    raw or None)] -> ({rel: bytes}, report). Unchanged settings keep their file; changed or new ones get a new
    settings file (the original as $parent, only the differing values). The placement file is rewritten (a new one
    and a GameBancParam pointing at it when the map had none)."""
    rng = random.Random(seed)
    read = lambda r: game.byml(r) if r and game.exists(r) else None
    gbp_rel, gbp_own, gbp = banc_param(game, banc)
    rel = work_to_rel(gbp.get('LocationalLightScenePlacement'))
    files, report, gbp_set = {}, [], {}
    if rel and game.exists(rel) and not os.path.basename(rel).startswith(banc + '.'):
        # (another map's light file, e.g. WaterTemple's for DarkLinkStage: never rewritten; this map gets its own copy)
        doc = game.byml(rel)
        report.append(f'{banc} used {os.path.basename(rel).split(".")[0]}\'s light file: it now has its own copy')
        rel = f'{GFX}LocationalLightScenePlacement/ootr_{banc}.game__gfx__LocationalLightScenePlacement.bgyml'
        gbp_set['LocationalLightScenePlacement'] = rel_to_work(rel)
    elif rel and game.exists(rel):
        doc = game.byml(rel)
    else:
        rel = f'{GFX}LocationalLightScenePlacement/ootr_{banc}.game__gfx__LocationalLightScenePlacement.bgyml'
        doc = {'GroupSetting': {}, 'Hash': U32(rng.getrandbits(32))}
        gbp_set['LocationalLightScenePlacement'] = rel_to_work(rel)
        report.append(f'{banc} had no runtime lights: new light file {os.path.basename(rel)}')
    original = copy.deepcopy(doc)
    template_hash = next((gv.get('Hash') for gv in (doc.get('GroupSetting') or {}).values()), U32(3534491336))
    groups = {}
    used_keys = set()
    new_settings = 0
    for L in lights:
        kind = L['kind']
        p = copy.deepcopy(L.get('raw') or {})
        p['Position'] = {'X': float(L['position'][0]), 'Y': float(L['position'][1]), 'Z': float(L['position'][2])}
        raw_kind = light_kind(L['raw']) if L.get('raw') else None
        if kind == 'spot':
            if raw_kind != 'spot': p['LightType'] = U32(SPOT)   # (other type codes with spot settings stay)
            old = (float(p.get('Latitude', 0.0)), float(p.get('Longitude', 0.0)))
            if not _same_dir(old, (L['lat'], L['lon'])):       # (unturned lights keep their own angles)
                p['Latitude'], p['Longitude'] = float(L['lat']), float(L['lon'])
        elif kind == 'point':
            if 'LightType' in p and int(p['LightType']) in (SPOT, AMPLIFIER): p.pop('LightType')
        if 'RandomHash' not in p: p['RandomHash'] = U32(rng.getrandbits(32))
        if kind in ('point', 'spot'):
            skey = SETTING_KEY[kind]
            src = L.get('setting_path') if L.get('setting_path') and SETTING_DIR[kind] in L['setting_path'] else None
            base = resolved(read, src) if src else {}
            want = L.get('setting') or {}
            diff = {k: v for k, v in want.items() if k in EDITABLE and not _same(base.get(k), v)}
            if src and not diff:
                p[skey] = src
            else:
                y = {'$parent': src} if src else {'Name': f'ootr_{banc}_{L["key"]}', 'IsEnableExposure': False}
                if not src: diff = {k: v for k, v in want.items() if k in EDITABLE}
                y.update(_typed(diff))
                h = rng.getrandbits(32)
                srel = f'{GFX}{SETTING_DIR[kind]}/ootr_{banc}_{h:08x}.game__gfx__{SETTING_DIR[kind]}.bgyml'
                files[srel] = write_byml(y)
                p[skey] = rel_to_work(srel)
                new_settings += 1
            if raw_kind != kind:                  # (a light turned into the other kind: drop the old settings)
                p.pop('SpotLightSetting' if kind == 'point' else 'PointLightSetting', None)
        group = L.get('group') or 'Editor'
        key = L.get('key') or 'Light'
        k, n = key, 1
        while (group, k) in used_keys: n += 1; k = f'{key}_{n}'
        used_keys.add((group, k))
        g = groups.setdefault(group, {'EnableGroupOffset': False, 'Hash': template_hash, 'PlacementSetting': {}})
        g['PlacementSetting'][k] = p
    old_groups = doc.get('GroupSetting') or {}
    for gname, g in groups.items():               # (keep the original group headers)
        if gname in old_groups:
            g.update({kk: vv for kk, vv in old_groups[gname].items() if kk != 'PlacementSetting'})
    doc['GroupSetting'] = groups
    if new_settings == 0 and doc == original:
        return {}, ['lights: unchanged'], {}
    files[rel] = write_byml(doc)
    report.append(f'lights: {len(lights)} runtime lights ({new_settings} new settings files)')
    return files, report, gbp_set


def _same_dir(a, b):
    da, db = direction(*a), direction(*b)
    return sum(x * y for x, y in zip(da, db)) > 1 - 1e-6


def _same(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return all(_same(a.get(k), b.get(k)) for k in set(a) | set(b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) < 1e-4
    return a == b


def _typed(d):
    out = {}
    for k, v in d.items():
        if isinstance(v, bool): out[k] = v
        elif isinstance(v, (int, float)): out[k] = float(v)
        elif isinstance(v, dict): out[k] = {kk: float(vv) for kk, vv in v.items()}
        else: out[k] = v
    return out


# ---------------------------------------------------------------- baked lights
def read_baked_lights(game, banc, bounds=None, min_power=1.0):
    """-> {'point': [(pos, rgb, radius)], 'spot': [(pos, dir, rgb, inner, outer, radius)],
    'area': [(pos, u, v, rgb, width, height)]} (empty when the map has none)."""
    rel = f'Bake/Light/{banc}.bklight.zs'
    out = {'point': [], 'spot': [], 'area': []}
    if not game.exists(rel): return out
    d = game.read(rel)
    if d[:4] != b'BKLT': return out
    np_, ns, na = struct.unpack_from('<III', d, 8)
    o = 0x14
    for i in range(np_):
        v = struct.unpack_from('<7f', d, o); o += 28
        if inside(bounds, v[0:3]) and max(v[3:6]) >= min_power: out['point'].append((v[0:3], v[3:6], v[6]))
    for i in range(ns):
        v = struct.unpack_from('<13f', d, o); o += 52
        if inside(bounds, v[0:3]) and max(v[6:9]) >= min_power: out['spot'].append((v[0:3], v[3:6], v[6:9], v[9], v[10], v[12]))
    for i in range(na):
        v = struct.unpack_from('<15f', d, o); o += 60
        if inside(bounds, v[0:3]) and max(v[9:12]) >= min_power: out['area'].append((v[0:3], v[3:6], v[6:9], v[9:12], v[12], v[13]))
    return out


# ---------------------------------------------------------------- environment
TIME_NAMES = ('Sunrise', 'Morning1', 'Morning2', 'Noon', 'Evening1', 'Evening2', 'Sunset', 'Night', 'MidNight')
ENV_KEYS = ('SkySunColor', 'SkySunColorIntensity', 'BgDifColor', 'BgDifIntensity', 'BgDifUseTemperature', 'BgDifTemperature',
            'HemiLightColorTop', 'HemiLightColorBottom', 'HemiLightIntensity', 'AmbientIntensity', 'ExposureValue',
            'ExposureShadowCorrect', 'WhiteBalanceTemperature', 'WhiteBalanceTint', 'FogColor', 'FogStart', 'FogEnd',
            'VolumeFogIsEnabled', 'BloomIntensity', 'SkyLightAdhocScale', 'DirectionalLightBounceScale', 'SpecularIntensity')


class EnvPack:
    """Pack/EnvPalette.pack (SARC) read access."""
    def __init__(self, game):
        from ..vendor.zstd_helper import unpack_sarc_to_dict
        self.raw = game.read('Pack/EnvPalette.pack.zs')
        self.files = unpack_sarc_to_dict(self.raw)

    def get(self, rel):
        return parse_byml(self.files[rel]) if rel in self.files else None


def _world_reader(game):
    """rel -> dict for WorldMgr files: loose in the game / mods, else in Pack/Bootup (MainField.game__wm__WorldParam)."""
    boot = {}
    def read(r):
        if not r: return None
        if game.exists(r): return game.byml(r)
        if not boot:
            from ..vendor.zstd_helper import unpack_sarc_to_dict
            try: boot.update(unpack_sarc_to_dict(game.read('Pack/Bootup.Nin_Ounce_NVN2.pack.zs')))
            except Exception: boot['-'] = b''
        return parse_byml(boot[r]) if r in boot else None
    return read


def read_env(game, banc, pack=None):
    """-> dict(world (own WorldParam file), world_rel, world_full, azimuth, elevation, cloud_shadow,
    times: [dict(name, hour, palette_path, palette (resolved), own (the palette file))]) or None."""
    _, _, gbp = banc_param(game, banc)
    wrel = work_to_rel(gbp.get('WorldMgrName'))
    if not wrel or not game.exists(wrel): return None
    pack = pack or EnvPack(game)
    loose = _world_reader(game)                      # (loose files, and the Bootup pack: MainField, every field
    world_own = game.byml(wrel)                      #  map's parent world, is in the pack)
    world = resolved(loose, rel_to_work(wrel))
    env = dict(world=world_own, world_rel=wrel, world_full=world, azimuth=float(world.get('MainLightAzimuth', 45.0)),
               elevation=float(world.get('MainLightElevation', 55.0)), cloud_shadow=bool(world.get('CloudShadowOnOff', False)),
               times=[])
    # where the map's palettes are (RESEARCH_NOTES.md §98): a time table (BasePalette_Bluesky), a time table chosen by
    # a story flag (ConditionEnvTimePalette[i].BasePalette_Bluesky: Hyrule Field), or one fixed palette
    # (DefaultEnvPalette: the Chamber of Sages)
    tpath, slot = world.get('BasePalette_Bluesky'), 'base'
    if not tpath:
        for i, c in enumerate(world.get('ConditionEnvTimePalette') or []):
            if c.get('BasePalette_Bluesky'):
                tpath, slot = c['BasePalette_Bluesky'], ('cond', i); break
    table = resolved(pack.get, tpath) if tpath else None        # (MainField_Bluesky_First: $parent holds the times)
    if table:
        for e in table.get('TimeTableArray', []):
            env['times'].append(dict(name=e.get('Name'), hour=int(e.get('Hour', 0)), palette_path=e.get('EnvPalette'),
                                     palette=resolved(pack.get, e.get('EnvPalette')), own=pack.get(work_to_rel(e.get('EnvPalette')))))
        env['table'] = table
        env['table_path'] = tpath
        env['slot'] = slot
    elif world.get('DefaultEnvPalette'):
        dp = world['DefaultEnvPalette']
        pal = resolved(pack.get, dp)
        if pal:
            env['times'].append(dict(name='Fixed', hour=12, palette_path=dp, palette=pal, own=pack.get(work_to_rel(dp))))
            env['slot'] = 'default'
    return env


def sarc_add_many(d, add):
    """Adds files to a SARC without moving the existing ones (each keeps its offset; RESEARCH_NOTES.md §81.2)."""
    from ..vendor.zstd_helper import sarc_hash
    dstart = struct.unpack_from('<I', d, 0xc)[0]
    n = struct.unpack_from('<H', d, 0x1a)[0]
    nodes = [list(struct.unpack_from('<IIII', d, 0x20 + 16 * i)) for i in range(n)]
    strs = bytearray(d[0x20 + 16 * n + 8:dstart]).rstrip(b'\0') + b'\0'
    strs += b'\0' * (-len(strs) & 3)
    data = bytearray(d[dstart:])
    have = {x[0] for x in nodes}
    for name, blob in add.items():
        h = sarc_hash(name)
        if h in have: raise ValueError(f'{name} is already in the pack')
        off = len(strs); strs += name.encode() + b'\0'; strs += b'\0' * (-len(strs) & 3)
        data += b'\0' * (-len(data) & 7)
        nodes.append([h, 0x01000000 | (off // 4), len(data), len(data) + len(blob)]); data += blob
    nodes.sort(key=lambda x: x[0])
    head = 0x20 + 16 * len(nodes) + 8 + len(strs)
    new_start = dstart + max(0, (head - dstart + 15) // 16 * 16)
    out = bytearray(d[:0x14]) + struct.pack('<4sHHI', b'SFAT', 0x0C, len(nodes), struct.unpack_from('<I', d, 0x1c)[0])
    for x in nodes: out += struct.pack('<IIII', *x)
    out += struct.pack('<4sHH', b'SFNT', 8, 0) + strs
    out += b'\0' * (new_start - len(out)) + data
    struct.pack_into('<II', out, 8, len(out), new_start)
    return bytes(out)


def _ours(path, banc):
    return bool(path) and f'/ootr_{banc}_' in path or bool(path) and f'/ootr_{banc}.' in path


def _original_palette(game, banc, env, i, t):
    """The game palette time i of this map builds on: the table's entry, or, when that is already this map's own
    edited palette (a re-export), that palette's $parent; a palette that lost it (an earlier bug made it name itself)
    gets it back from the game's time table with the same times of day."""
    p = t['palette_path']
    if not _ours(p, banc): return p
    q = (t.get('own') or {}).get('$parent')
    if q and not _ours(q, banc): return q
    names = [(x['name'], x['hour']) for x in env['times']]
    from .game import Game
    vpack = EnvPack(Game(game.folder))
    for rel in sorted(vpack.files):
        if 'ResEnvPaletteTimeTable/' not in rel: continue
        tb = resolved(vpack.get, rel_to_work(rel))
        arr = tb.get('TimeTableArray') or []
        if [(x.get('Name'), int(x.get('Hour', 0))) for x in arr] == names and i < len(arr):
            return arr[i].get('EnvPalette')
    raise ValueError(f'{t["name"]}: the original palette of this time of day was not found')


def write_env(game, banc, env, edits, mod_dir, pack=None):
    """edits: dict(azimuth, elevation, cloud_shadow, times: {time name: {palette key: value}}) -> ({rel: bytes},
    report). A WorldParam and time table of the map's own (ootr_<banc>) and one palette per time of day ($parent = the
    original, only the edited values) are made; the palettes go into EnvPalette.pack (every map exported into this mod:
    their palettes are kept in <mod>/ootr_env/ and re-added to the game's pack)."""
    pack = pack or EnvPack(game)
    files, report = {}, []
    world = dict(env['world'])
    for key, ek, cast in (('MainLightAzimuth', 'azimuth', float), ('MainLightElevation', 'elevation', float),
                          ('CloudShadowOnOff', 'cloud_shadow', bool)):
        v = cast(edits.get(ek, env[ek]))
        if key in env['world_full'] or v != cast(env[ek]):     # (absent keys stay absent: Hyrule Field's sun comes
            world[key] = v                                     #  from its time table)
    side = os.path.join(mod_dir, 'ootr_env')
    os.makedirs(side, exist_ok=True)
    for f in os.listdir(side):                         # (this map's previous palettes are replaced)
        if f.startswith(f'ootr_{banc}_') or f.startswith(f'ootr_{banc}.'): os.remove(os.path.join(side, f))
    mine = {}
    if env.get('table'):
        table = copy.deepcopy(env['table'])
        if _ours(env.get('table_path'), banc) and env['world'].get('$parent'):
            # (our own table, made by an earlier export: its sun settings from the parent world's table, the one the
            #  game uses without a story flag; 0.16.1-0.16.5 copied Hyrule Field's first-visit table, sun forced to 18)
            base_tp = resolved(_world_reader(game), env['world']['$parent']).get('BasePalette_Bluesky')
            base_t = resolved(pack.get, base_tp) if base_tp else {}
            for k in ('IsForceMainLight', 'MainLight_Azimuth', 'MainLight_Elevation', 'Name'):
                if k in base_t: table[k] = base_t[k]
        for i, (e, t) in enumerate(zip(table['TimeTableArray'], env['times'])):
            ed = (edits.get('times') or {}).get(t['name']) or {}
            parent = _original_palette(game, banc, env, i, t)
            orig = resolved(pack.get, parent)
            # (the palette's values as edited, against the original: unchanged keys are left to the original)
            diff = {k: v for k, v in ed.items() if not _same(orig.get(k), v)}
            if not diff:
                e['EnvPalette'] = parent; continue
            y = {'$parent': parent}
            y.update(_typed(diff))
            name = f'ootr_{banc}_{i}_{t["name"]}.game__wm__ResEnvPalette.bgyml'
            mine[f'WorldMgr/ResEnvPalette/{name}'] = write_byml(y)
            e['EnvPalette'] = f'Work/WorldMgr/ResEnvPalette/{name.replace(".bgyml", ".gyml")}'
        if True:          # (always: the map's world names this table, also when every palette is the game's again)
            tname = f'ootr_{banc}.game__wm__ResEnvPaletteTimeTable.bgyml'
            mine[f'WorldMgr/ResEnvPaletteTimeTable/{tname}'] = write_byml(table)
            tw = f'Work/WorldMgr/ResEnvPaletteTimeTable/{tname.replace(".bgyml", ".gyml")}'
            world['BasePalette_Bluesky'] = tw
            if isinstance(env.get('slot'), tuple):             # (only a flag-chosen table: ours there too)
                conds = [dict(c) for c in world.get('ConditionEnvTimePalette') or []]
                conds[env['slot'][1]]['BasePalette_Bluesky'] = tw
                world['ConditionEnvTimePalette'] = conds
            elif world.get('ConditionEnvTimePalette'):         # (0.16.1-0.16.5 put ours into Hyrule Field's first-visit
                conds = [c for c in world['ConditionEnvTimePalette']   # entry, forcing its 18-degree sun: dropped)
                         if not _ours(c.get('BasePalette_Bluesky'), banc)]
                if conds: world['ConditionEnvTimePalette'] = conds
                else: world.pop('ConditionEnvTimePalette', None)
    elif env.get('slot') == 'default' and env['times']:
        t = env['times'][0]
        ed = (edits.get('times') or {}).get(t['name']) or {}
        parent = _original_palette(game, banc, env, 0, t)
        orig = resolved(pack.get, parent)
        diff = {k: v for k, v in ed.items() if not _same(orig.get(k), v)}
        if diff:
            y = {'$parent': parent}
            y.update(_typed(diff))
            name = f'ootr_{banc}_0_Fixed.game__wm__ResEnvPalette.bgyml'
            mine[f'WorldMgr/ResEnvPalette/{name}'] = write_byml(y)
            world['DefaultEnvPalette'] = f'Work/WorldMgr/ResEnvPalette/{name.replace(".bgyml", ".gyml")}'
    for rel, data in mine.items():
        with open(os.path.join(side, os.path.basename(rel)), 'wb') as f: f.write(data)
    allp = {}
    for f in sorted(os.listdir(side)):
        kind = 'ResEnvPaletteTimeTable' if 'ResEnvPaletteTimeTable' in f else 'ResEnvPalette'
        allp[f'WorldMgr/{kind}/{f}'] = open(os.path.join(side, f), 'rb').read()
    from ..vendor.zstd_helper import compress_zs
    packrel = 'Pack/EnvPalette.pack.zs'
    if allp:
        # (the pack the game would load without this mod, e.g. another mod's own environments, plus ours: a mod's
        #  copy replaces the whole pack, so it must carry what the mods loading before it added)
        from .game import Game
        mine_dir = os.path.abspath(os.path.join(mod_dir, 'romfs'))
        others = [os.path.dirname(o) for o in game.overlays if os.path.abspath(o) != mine_dir]
        base = EnvPack(Game(game.folder, overlays=others))
        raw = base.raw
        from .sound import _sarc_drop
        for k in allp:
            if k in base.files: raw = _sarc_drop(raw, k)     # (ours replace any copy already there)
        files[packrel] = ('zs', sarc_add_many(raw, allp))
    else:
        p = os.path.join(mod_dir, 'romfs', packrel)
        if os.path.exists(p): os.remove(p)
    wrel = f'WorldMgr/WorldParam/ootr_{banc}.game__wm__WorldParam.bgyml'
    files[wrel] = write_byml(world)
    report.append(f'environment: sun {world.get("MainLightAzimuth", env["azimuth"]):.1f} / {world.get("MainLightElevation", env["elevation"]):.1f}, '
                  f'{sum(1 for k in mine if "ResEnvPalette/" in k)} edited palettes')
    return files, report, {'WorldMgrName': rel_to_work(wrel)}


def write_lighting(game, banc, mod_dir, lights=None, env_edits=None, seed=None):
    """Runtime lights and / or environment edits -> files written into <mod_dir>/romfs. -> report lines."""
    from ..vendor.zstd_helper import compress_zs
    files, report, gbp_set = {}, [], {}
    if lights is not None:
        f, r, g = write_scene_lights(game, banc, lights, seed); files.update(f); report += r; gbp_set.update(g)
    if env_edits is not None:
        env = read_env(game, banc)
        if env is None: report.append('environment: the map has no WorldParam (skipped)')
        else:
            f, r, g = write_env(game, banc, env, env_edits, mod_dir); files.update(f); report += r; gbp_set.update(g)
    if gbp_set:
        gbp_rel, own, _ = banc_param(game, banc)
        own = dict(own); own.update(gbp_set)
        files[gbp_rel] = write_byml(own)
    for rel, data in files.items():
        p = os.path.join(mod_dir, 'romfs', rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if isinstance(data, tuple): data = compress_zs(data[1], rel, game.romfs)
        with open(p, 'wb') as fh: fh.write(data)
    plain = {rel: (d[1] if isinstance(d, tuple) else d) for rel, d in files.items()}
    return report, plain
