"""Core results -> Blender data. Axes: the game is Y-up metres, Blender Z-up metres: (x, y, z)game -> (x, -z, y).
Every object keeps its placement in custom properties (ootr_*) so an export can write it back."""
import math
import time

import bpy
from mathutils import Euler, Matrix, Vector

from .core.banc import load_map

GAME_TO_BLENDER = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def placement_matrix(p):
    """The placement's world matrix in Blender axes. The game rotates XYZ (Rz Ry Rx applied to the model)."""
    t = Matrix.Translation(Vector(p.translate))
    r = Euler(p.rotate, 'XYZ').to_matrix().to_4x4()
    s = Matrix.Diagonal((*p.scale, 1.0))
    return GAME_TO_BLENDER @ t @ r @ s


def collection(name, parent):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if c.name not in parent.children: parent.children.link(c)
    return c


def collision_mesh(name, V, T):
    me = bpy.data.meshes.new(name)
    me.from_pydata(V.tolist(), [], T.tolist())
    me.update()
    return me


def import_map(context, game, banc, models=True, collision=True, report=print):
    t0 = time.time()
    places = load_map(game, banc)
    root = collection(banc, context.scene.collection)
    root['ootr_banc'] = banc
    from .core.banc import find_folder
    root['ootr_folder'] = find_folder(game, banc) or ''
    layers = {k: collection(f'{banc} {k}', root) for k in ('Static', 'Dynamic')}
    meshes = {}
    if models:
        from .models import model_mesh
    n_models = 0
    col_meshes, n_col = {}, 0
    if collision:
        from .core import collision as colcore
        shapes = colcore.ShapeIndex(game)
        col_root = collection(f'{banc} Collision', root)
    for p in places:
        data = None
        if models and p.model:
            if p.model not in meshes:
                try:
                    meshes[p.model] = model_mesh(game, p.model)
                except Exception as e:
                    report(f'{p.model}: {e}')
                    meshes[p.model] = None
            data = meshes[p.model]
        ob = bpy.data.objects.new(p.actor, data)
        if data is None:
            ob.empty_display_type = 'ARROWS'
            ob.empty_display_size = 0.5
        else:
            n_models += 1
        ob.matrix_world = placement_matrix(p)
        ob['ootr_actor'] = p.actor
        ob['ootr_hash'] = str(p.hash)
        ob['ootr_layer'] = p.layer
        ob['ootr_trs'] = [*p.translate, *p.rotate, *p.scale]     # (untouched objects export these exactly)
        layers[p.layer].objects.link(ob)
        if collision:
            for layer, (V, T) in colcore.layers_for(game, shapes, p.actor).items():
                key = (p.actor, layer)
                if key not in col_meshes: col_meshes[key] = collision_mesh(f'{p.actor}__{layer}', V, T)
                co = bpy.data.objects.new(f'{p.actor}__{layer}', col_meshes[key])
                co.matrix_world = ob.matrix_world
                co.display_type = 'WIRE'
                co.hide_render = True
                co['ootr_collision'] = layer
                collection(f'{banc} Collision {layer}', col_root).objects.link(co)
                n_col += 1
    if collision:                                    # (airwalls are invisible boundaries: hidden until wanted)
        for c in col_root.children:
            if 'Airwall' in c.name or 'AirWall' in c.name: c.hide_viewport = True
    return f'{banc}: {len(places)} placements, {n_models} with models ({len([m for m in meshes.values() if m])} meshes) {f", {n_col} collision objects" if collision else ""} in {time.time() - t0:.1f}s'


def game_trs(ob):
    """An object's world matrix -> (translate, rotate (euler XYZ radians), scale) in game axes."""
    m = GAME_TO_BLENDER.inverted() @ ob.matrix_world
    t, q, s = m.decompose()
    e = q.to_euler('XYZ')
    return tuple(t), (e.x, e.y, e.z), tuple(s)


def map_roots(context):
    return [c for c in bpy.data.collections if 'ootr_banc' in c]


def gather_edits(root):
    """Every object under the map's collection that carries a placement (ootr_hash), not collision helpers."""
    from .core.export import Edit
    bpy.context.view_layer.update()                  # (matrix_world follows location/rotation only after an update)
    edits, seen = [], set()
    def walk(c):
        for ob in c.objects:
            if 'ootr_hash' in ob and 'ootr_collision' not in ob and ob.name not in seen:
                seen.add(ob.name)
                t, r, s = game_trs(ob)
                if 'ootr_trs' in ob:                    # unchanged since import: the original values as they were
                    v = list(ob['ootr_trs'])
                    from types import SimpleNamespace
                    orig = placement_matrix(SimpleNamespace(translate=v[0:3], rotate=v[3:6], scale=v[6:9]))
                    if all(abs(a - b) < 1e-4 for ra, rb in zip(orig, ob.matrix_world) for a, b in zip(ra, rb)):
                        t, r, s = tuple(v[0:3]), tuple(v[3:6]), tuple(v[6:9])
                edits.append(Edit(int(ob['ootr_hash']), ob.get('ootr_layer', 'Dynamic'), t, r, s, ob.get('ootr_actor')))
        for ch in c.children: walk(ch)
    walk(root)
    return edits
