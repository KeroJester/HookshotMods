"""The Map Settings panel (RESEARCH_NOTES.md §92, §93): the imported map's look, sound and gameplay settings with
readable choices. Loaded on import / New Map, written with the map on Export / Play (core/mapsettings.py)."""
import json

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty

_cache = {}


def _game(context):
    from . import get_game
    return get_game(context)


def _list(context, key, make):
    if key not in _cache:
        try:
            _cache[key] = make(_game(context))
        except Exception:
            _cache[key] = []
    return _cache[key]


def _pretty(n):
    from .core.sound import pretty
    return pretty(n)


def _keep(key, items):
    """Blender needs the enum item strings kept alive: computed lists are cached."""
    _cache['items_' + key] = items
    return items


def _items_soundscape(self, context):
    from .core.sound import soundscapes
    out = [('None', 'Silence', 'No music, no ambience'),
           ('Custom', 'Custom', 'This map\'s own soundscape: any map music with any ambience')]
    for s in _list(context, 'soundscapes', soundscapes):
        music = _pretty(s['bgm']) if s['bgm'] else 'no music'
        out.append((s['name'], _pretty(s['name']), f'Music: {music}; ambience: {s["ambient"] or "none"} ({s["label"]})'))
    return _keep('soundscape', out)


def _items_music(self, context):
    from .core.sound import music_choices
    return _keep('music', [('NONE', 'No music', '')] + [(n, _pretty(n), n) for n in _list(context, 'music', music_choices)])


def _items_ambience(self, context):
    from .core.sound import ambience_choices
    return _keep('ambience', [('NONE', 'No ambience', '')] + [(n, _pretty(n.replace('Amb_', '')), n) for n in _list(context, 'ambience', ambience_choices)])


def _items_sky(self, context):
    from .core.mapsettings import world_params
    return _keep('sky', [(n, _pretty(n), f'Sun, time of day, light palettes and fog of {n}') for n in _list(context, 'skies', world_params)]
                 or [('', '(none)', '')])


def _items_envset(self, context):
    from .core.mapsettings import envsets
    return _keep('envset', [(n, _pretty(n), 'Sky scattering set') for n in _list(context, 'envsets', envsets)] or [('Master_Field', 'Master Field', '')])


def _enum(key, none_label=None):
    from .core.enums import NAMES
    fixed = ([('None', none_label, '')] if none_label else []) + [(n, _pretty(n), '') for n in NAMES[key]]
    def items(self, context):
        return fixed
    return items


def _changed(self, context):
    pass


class OOTR_MapSettings(bpy.types.PropertyGroup):
    banc: StringProperty()
    original: StringProperty()
    rendering: EnumProperty(name='Rendering', items=[('MainField', 'Outdoor', 'Outdoor rendering (fields, towns)'),
                                                     ('Dungeon', 'Indoor', 'Indoor rendering (dungeons, houses)'),
                                                     ('Museum', 'Museum', 'The enemy museum')])
    envset: EnumProperty(name='Sky Set', items=_items_envset, description='Sky scattering (the sky dome\'s colours)')
    sky_preset: EnumProperty(name='Sky & Light', items=_items_sky,
                             description='Sun, time of day, light palettes and fog (edit them in OoT Lighting after export)')
    clouds: EnumProperty(name='Clouds', items=_enum('CloudEnvType', 'No clouds'))
    effects: EnumProperty(name='Weather Effects', items=_enum('EnvEffectType', 'None'),
                          description='Particles and effects of an area (dust, leaves, embers, snow, ...)')
    soundscape: EnumProperty(name='Soundscape', items=_items_soundscape,
                             description='The map\'s music and ambience (zones can play other music locally)')
    custom_music: EnumProperty(name='Music', items=_items_music, description='The music of the map\'s own soundscape')
    custom_ambience: EnumProperty(name='Ambience', items=_items_ambience,
                                  description='The background ambience of the map\'s own soundscape')
    map_location: EnumProperty(name='Pause Map', items=_enum('WorldMapLocationType', 'Not on the map'),
                               description='Where the map shows on the pause screen\'s world map')
    age: EnumProperty(name='Link\'s Age', items=[('keep', 'Keep', 'Link stays the age he enters with'),
                                                 ('child', 'Child', 'Link becomes a child here'),
                                                 ('adult', 'Adult', 'Link becomes an adult here')])
    horse: BoolProperty(name='Epona Allowed')
    sun_song: BoolProperty(name='Sun\'s Song Changes Time')
    timer: BoolProperty(name='Timer Counts Down')

    FIELDS = ('rendering', 'envset', 'sky_preset', 'clouds', 'effects', 'soundscape', 'custom_music', 'custom_ambience',
              'map_location', 'age', 'horse', 'sun_song', 'timer')

    def load(self, banc, values):
        self.banc = banc
        for k in self.FIELDS:
            if k in values:
                try: setattr(self, k, values[k])
                except (TypeError, ValueError): pass
        self.original = json.dumps(self.values())

    def values(self):
        return {k: getattr(self, k) for k in self.FIELDS}

    def changes(self):
        was = json.loads(self.original or '{}')
        return {k: v for k, v in self.values().items() if was.get(k) != v}


def load_for(context, game, banc):
    from .core.mapsettings import read_settings
    for k in [k for k in _cache if not k.startswith('items_')]: _cache.pop(k)
    context.scene.ootr_map.load(banc, read_settings(game, banc))


class OOTR_OT_load_map_settings(bpy.types.Operator):
    """Read this map's settings from the game folder (for maps imported before the Map Settings panel existed)"""
    bl_idname = 'ootr.load_map_settings'
    bl_label = 'Load Map Settings'
    banc: StringProperty()

    def execute(self, context):
        try:
            load_for(context, _game(context), self.banc)
        except Exception as ex:
            self.report({'ERROR'}, f'{self.banc}: {ex}'); return {'CANCELLED'}
        return {'FINISHED'}


class OOTR_PT_map_settings(bpy.types.Panel):
    bl_label = 'Map Settings'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OoT'

    def draw(self, context):
        m = context.scene.ootr_map
        lay = self.layout
        if not m.banc:
            from .scene import map_roots
            roots = map_roots(context)
            if roots:
                for r in roots:
                    lay.operator('ootr.load_map_settings', text=f'Load Settings of {r["ootr_banc"]}',
                                 icon='IMPORT').banc = r['ootr_banc']
            else:
                lay.label(text='Import or create a map')
            return
        lay.label(text=m.banc, icon='WORLD')
        box = lay.box(); box.label(text='Look', icon='SHADING_RENDERED')
        box.prop(m, 'rendering', expand=True)
        box.prop(m, 'sky_preset'); box.prop(m, 'envset'); box.prop(m, 'clouds'); box.prop(m, 'effects')
        box = lay.box(); box.label(text='Sound', icon='SPEAKER')
        box.prop(m, 'soundscape')
        if m.soundscape == 'Custom':
            col = box.column(align=True); col.prop(m, 'custom_music'); col.prop(m, 'custom_ambience')
        ob = context.active_object
        if ob is not None and getattr(ob, 'ootr_sound', None) is not None and ob.ootr_sound.kind != 'none':
            from .sound_ui import draw_object
            draw_object(box, ob)
        box.operator('ootr.add_sound', text='Add Sound Emitter', icon='SPEAKER').kind = 'emitter'
        row = box.row(align=True); row.enabled = False      # (zones need trigger boxes in the collision file: §93)
        row.operator('ootr.add_sound', text='Music Zone', icon='SOUND').kind = 'music'
        row.operator('ootr.add_sound', text='Ambience Zone', icon='OUTLINER_OB_SPEAKER').kind = 'ambience'
        box.label(text='Zones: coming with the collision editor', icon='INFO')
        box = lay.box(); box.label(text='Gameplay', icon='USER')
        box.prop(m, 'age'); box.prop(m, 'map_location')
        box.prop(m, 'horse'); box.prop(m, 'sun_song'); box.prop(m, 'timer')
        ch = m.changes()
        if ch: lay.label(text=f'{len(ch)} change(s): written on Export / Play', icon='INFO')


classes = (OOTR_MapSettings, OOTR_OT_load_map_settings, OOTR_PT_map_settings)


def register():
    for c in classes: bpy.utils.register_class(c)
    bpy.types.Scene.ootr_map = PointerProperty(type=OOTR_MapSettings)


def unregister():
    del bpy.types.Scene.ootr_map
    for c in reversed(classes): bpy.utils.unregister_class(c)
