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
