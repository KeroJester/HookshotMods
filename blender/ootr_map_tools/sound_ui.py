"""Placeable sound (RESEARCH_NOTES.md §93): music zones (BGM_<track> + an Area box, linked), ambience zones
(SoundSettingActor {SceneryName, Distance} + an Area box) and sound emitters (SoundLocator_*), as Blender empties.
Export turns each into the game's own placements."""
import math

import bpy
from bpy.props import EnumProperty, FloatProperty, PointerProperty
from mathutils import Vector

from .scene import GAME_TO_BLENDER

B2G = GAME_TO_BLENDER.to_3x3().inverted()
_items = {}


def _game(context):
    from . import get_game
    return get_game(context)


def _cached(context, key, make):
    if key not in _items:
        try:
            _items[key] = make(_game(context))
        except Exception:
            _items[key] = [('', '(choose your game folder)', '')]
    return _items[key]


def _tracks(self, context):
    from .core.sound import zone_tracks, pretty
    return _cached(context, 'tracks', lambda g: [(t, pretty(t), t) for t in zone_tracks(g)])


def _ambiences(self, context):
    from .core.sound import zone_ambiences, pretty
    return _cached(context, 'amb', lambda g: [(s['name'], pretty(s['name']), s['label']) for s in zone_ambiences(g)])


def _emitters(self, context):
    from .core.sound import emitters, pretty
    return _cached(context, 'emit', lambda g: [(t, pretty(t), t) for t in emitters(g)])


class OOTR_SoundProps(bpy.types.PropertyGroup):
    kind: EnumProperty(items=[('none', 'None', ''), ('music', 'Music Zone', ''), ('ambience', 'Ambience Zone', ''),
                              ('emitter', 'Sound Emitter', '')], default='none')
    track: EnumProperty(name='Music', items=_tracks, description='The music played inside the zone')
    ambience: EnumProperty(name='Ambience', items=_ambiences, description='The ambience inside the zone')
    fade: FloatProperty(name='Fade Distance', default=3.0, min=0.0, soft_max=30.0, unit='LENGTH',
                        description='How far outside the zone the ambience fades in')
    emitter: EnumProperty(name='Sound', items=_emitters, description='The sound this emitter makes')


class OOTR_OT_add_sound(bpy.types.Operator):
    """Add a music zone, ambience zone or sound emitter at the 3D cursor"""
    bl_idname = 'ootr.add_sound'
    bl_label = 'Add OoT Sound'
    bl_options = {'REGISTER', 'UNDO'}
    kind: EnumProperty(items=[('music', 'Music Zone', ''), ('ambience', 'Ambience Zone', ''), ('emitter', 'Emitter', '')])

    def execute(self, context):
        names = {'music': 'Music Zone', 'ambience': 'Ambience Zone', 'emitter': 'Sound Emitter'}
        ob = bpy.data.objects.new(names[self.kind], None)
        ob.empty_display_type = 'CUBE' if self.kind != 'emitter' else 'SPHERE'
        ob.empty_display_size = 0.5                       # (a 1 m cube: the object's scale is the zone's size)
        ob.location = context.scene.cursor.location
        ob.scale = (10.0, 10.0, 5.0) if self.kind != 'emitter' else (2.0, 2.0, 2.0)
        ob.show_name = True
        ob.ootr_sound.kind = self.kind
        context.scene.collection.objects.link(ob)
        for o in context.selected_objects: o.select_set(False)
        ob.select_set(True); context.view_layer.objects.active = ob
        return {'FINISHED'}


def draw_object(layout, ob):
    p = ob.ootr_sound
    box = layout.box()
    box.label(text=ob.name, icon='SPEAKER')
    if p.kind == 'music':
        box.prop(p, 'track')
        box.label(text='Plays while Link is inside the box', icon='INFO')
    elif p.kind == 'ambience':
        box.prop(p, 'ambience'); box.prop(p, 'fade')
    elif p.kind == 'emitter':
        box.prop(p, 'emitter')


def game_box(ob):
    """An empty -> (centre, rotate (yaw only), size) in game axes (the box's x / y / z = Blender x / z / y)."""
    m = ob.matrix_world
    centre = tuple(B2G @ m.translation)
    s = ob.scale
    yaw = ob.matrix_world.to_euler('XYZ').z
    return centre, (0.0, float(yaw), 0.0), (abs(s.x), abs(s.z), abs(s.y))


def gather_sound(context, root):
    """Export edits (core.export.Edit) for the sound objects of the scene (not inside other imported maps)."""
    from .core.export import Edit
    from .scene import map_roots
    others = set()
    for r in map_roots(context):
        if r == root: continue
        def collect(c):
            for o in c.objects: others.add(o.name)
            for ch in c.children: collect(ch)
        collect(r)
    edits = []
    for ob in context.scene.objects:
        p = getattr(ob, 'ootr_sound', None)
        if p is None or p.kind == 'none' or ob.name in others: continue
        centre, rot, size = game_box(ob)
        key = f'ootr_sound_{ob.name}'
        if p.kind in ('music', 'ambience'):
            edits.append(Edit(0, 'Dynamic', centre, rot, size, 'Area', key=key + '_area'))   # (a box: the Area default, as the game's own)
            if p.kind == 'music' and p.track:
                edits.append(Edit(0, 'Dynamic', centre, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), p.track,
                                  links=[('Reference', key + '_area')]))
            elif p.kind == 'ambience' and p.ambience:
                edits.append(Edit(0, 'Dynamic', centre, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), 'SoundSettingActor',
                                  links=[('Reference', key + '_area')],
                                  dynamic={'SceneryName': p.ambience, 'Distance': float(p.fade)}))
        elif p.kind == 'emitter' and p.emitter:
            edits.append(Edit(0, 'Dynamic', centre, rot, size, p.emitter))
    return edits


classes = (OOTR_SoundProps, OOTR_OT_add_sound)


def register():
    for c in classes: bpy.utils.register_class(c)
    bpy.types.Object.ootr_sound = PointerProperty(type=OOTR_SoundProps)


def unregister():
    del bpy.types.Object.ootr_sound
    for c in reversed(classes): bpy.utils.unregister_class(c)
