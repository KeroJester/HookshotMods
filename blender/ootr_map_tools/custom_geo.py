"""Custom geometry in Blender (RESEARCH_NOTES.md §89): every plain mesh object in the scene (not imported from the
game, not a light) is part of the map. 'Visible in Game' meshes become the map's own model (core/custom.py), 'Enable
Collision' meshes become collision. Modifiers are applied, materials give the textures (the Principled BSDF's Base
Color image, or its colour)."""
import bpy
import numpy as np
from bpy.props import BoolProperty

from .scene import GAME_TO_BLENDER

B2G = GAME_TO_BLENDER.to_3x3().inverted()
IMPORTED_KEYS = ('ootr_actor', 'ootr_hash', 'ootr_collision', 'ootr_baked', 'ootr_light')


def is_custom(ob):
    return ob.type == 'MESH' and not any(k in ob for k in IMPORTED_KEYS)


def _material_image(mat):
    """-> RGBA uint8 [h, w, 4] (top row first) for a material: its Base Color image, else its colour."""
    def solid(c):
        lin = np.clip(np.array(c[:3], float), 0, 1)
        srgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
        img = np.empty((8, 8, 4), np.uint8); img[..., :3] = np.round(srgb * 255); img[..., 3] = 255
        return img
    if mat is None:
        return solid((0.8, 0.8, 0.8))
    if mat.use_nodes and mat.node_tree:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is not None:
            inp = bsdf.inputs.get('Base Color')
            if inp is not None and inp.is_linked:
                n = inp.links[0].from_node
                if n.type == 'TEX_IMAGE' and n.image is not None and n.image.size[0] > 0:
                    im = n.image
                    w, h = im.size
                    px = np.empty(w * h * 4, np.float32)
                    im.pixels.foreach_get(px)
                    a = px.reshape(h, w, 4)[::-1]
                    if im.colorspace_settings.name not in ('sRGB',) and not im.is_float:
                        pass
                    if im.is_float:                         # (float images are linear: to sRGB)
                        c = np.clip(a[..., :3], 0, 1)
                        a = a.copy(); a[..., :3] = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
                    return np.clip(np.round(a * 255), 0, 255).astype(np.uint8)
            if inp is not None:
                return solid(inp.default_value)
    return solid(mat.diffuse_color)


def gather_custom(context, root):
    """-> (meshes, images, collision (V, T) or None) for core.custom.write_custom, from the custom mesh objects of the
    scene that are not inside another imported map's collection."""
    from .scene import map_roots
    others = set()
    for r in map_roots(context):
        if r == root: continue
        def collect(c):
            for o in c.objects: others.add(o.name)
            for ch in c.children: collect(ch)
        collect(r)
    dg = context.evaluated_depsgraph_get()
    meshes, images, colV, colT = [], {}, [], []
    nV = 0
    for ob in context.scene.objects:
        if not is_custom(ob) or ob.name in others: continue
        visible = ob.ootr_game_visible and not ob.hide_render
        solid = ob.ootr_solid
        if not (visible or solid): continue
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            me.calc_loop_triangles()
            if not len(me.loop_triangles): continue
            M = ob.matrix_world
            co = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get('co', co); co = co.reshape(-1, 3)
            W = (np.c_[co, np.ones(len(co))] @ np.array(M).T)[:, :3]
            G = W @ np.array(B2G).T                                       # (game axes)
            tri_v = np.empty(len(me.loop_triangles) * 3, np.int32); me.loop_triangles.foreach_get('vertices', tri_v)
            tri_l = np.empty(len(me.loop_triangles) * 3, np.int32); me.loop_triangles.foreach_get('loops', tri_l)
            tri_m = np.empty(len(me.loop_triangles), np.int32); me.loop_triangles.foreach_get('material_index', tri_m)
            if solid:
                colV.append(G); colT.append(tri_v.reshape(-1, 3) + nV); nV += len(G)
            if visible:
                if hasattr(me, 'corner_normals'):
                    cn = np.empty(len(me.corner_normals) * 3, np.float32); me.corner_normals.foreach_get('vector', cn)
                else:
                    me.calc_normals_split()
                    cn = np.empty(len(me.loops) * 3, np.float32); me.loops.foreach_get('normal', cn)
                cn = cn.reshape(-1, 3)
                NM = np.array(M.to_3x3().inverted().transposed())
                N = cn[tri_l] @ NM.T @ np.array(B2G).T
                N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
                uvl = me.uv_layers.active
                if uvl is not None:
                    uv = np.empty(len(uvl.data) * 2, np.float32); uvl.data.foreach_get('uv', uv); uv = uv.reshape(-1, 2)[tri_l]
                    uv[:, 1] = 1.0 - uv[:, 1]                              # (the game's V runs down)
                else:
                    uv = np.zeros((len(tri_l), 2), np.float32)
                P = G[tri_v]
                for mi in np.unique(tri_m):
                    sel = np.repeat(tri_m == mi, 3)
                    mat = ob.material_slots[mi].material if mi < len(ob.material_slots) else None
                    key = mat.name if mat else 'Default'
                    if key not in images: images[key] = _material_image(mat)
                    k = int(sel.sum())
                    meshes.append(dict(name=f'{ob.name}_{key}'.replace(' ', '_'), material=key, pos=P[sel], nrm=N[sel],
                                       uv=uv[sel], tris=np.arange(k).reshape(-1, 3)))
        finally:
            ev.to_mesh_clear()
    collision = (np.concatenate(colV), np.concatenate(colT)) if colV else None
    return meshes, images, collision


def register():
    bpy.types.Object.ootr_solid = BoolProperty(
        name='Enable Collision', default=True,
        description='This mesh is solid in game (Link stands on it and bumps into it)')
    bpy.types.Object.ootr_game_visible = BoolProperty(
        name='Visible in Game', default=True,
        description='This mesh is part of the map\'s model in game (off: invisible, e.g. collision only)')


def unregister():
    del bpy.types.Object.ootr_solid
    del bpy.types.Object.ootr_game_visible
