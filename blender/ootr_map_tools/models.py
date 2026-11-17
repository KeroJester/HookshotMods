"""Game models -> Blender meshes. Mesh data stays in game axes (Y-up); the object matrix (scene.placement_matrix)
turns it into Blender's Z-up. One Blender mesh per model file, shared by every placement of it."""
import bpy
import numpy as np

from .core.bfres import read_model


def material(name):
    """One Blender material per game material name (shared across models). Textures: textures.py (later)."""
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        h = abs(hash(name))
        m.diffuse_color = (0.35 + (h % 7) / 20, 0.35 + (h // 7 % 7) / 20, 0.35 + (h // 49 % 7) / 20, 1.0)
        m['ootr_material'] = name
    return m


def model_mesh(game, rel):
    md = read_model(game.read(rel))
    if not md.meshes:
        return None
    name = rel.split('/')[-1].split('.')[1]
    me = bpy.data.meshes.new(name)
    P, T, N, UV, MI, COL = [], [], [], [], [], []
    base = 0
    has_col = any(m.col is not None for m in md.meshes)
    for m in md.meshes:
        P.append(m.pos); T.append(m.tris + base)
        N.append(m.nrm if m.nrm is not None else np.zeros_like(m.pos))
        UV.append(m.uvs[0][1] if m.uvs else np.zeros((len(m.pos), 2)))
        if has_col: COL.append(m.col if m.col is not None else np.ones((len(m.pos), 4)))
        MI.append(np.full(len(m.tris), m.material))
        base += len(m.pos)
    P, T, N, UV, MI = (np.concatenate(a) for a in (P, T, N, UV, MI))
    me.vertices.add(len(P)); me.vertices.foreach_set('co', P.astype(np.float32).ravel())
    me.loops.add(T.size); me.loops.foreach_set('vertex_index', T.astype(np.int32).ravel())
    me.polygons.add(len(T))
    me.polygons.foreach_set('loop_start', np.arange(0, T.size, 3, dtype=np.int32))
    me.polygons.foreach_set('loop_total', np.full(len(T), 3, np.int32))
    # material slots: the model's materials in index order
    for mat in md.materials:
        me.materials.append(material(mat.name))
    me.polygons.foreach_set('material_index', MI.astype(np.int32))
    uvl = me.uv_layers.new(name='UVMap')
    uv = UV[T.ravel()].copy(); uv[:, 1] = 1.0 - uv[:, 1]          # (game V down -> Blender V up)
    uvl.data.foreach_set('uv', uv.astype(np.float32).ravel())
    if has_col:
        ca = me.color_attributes.new('Col', 'FLOAT_COLOR', 'POINT')
        ca.data.foreach_set('color', np.concatenate(COL).astype(np.float32).ravel())
    me.update(calc_edges=True)
    me.validate(clean_customdata=False)
    if np.any(N):
        n = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
        try:
            me.normals_split_custom_set_from_vertices(n.astype(np.float32))
        except Exception:
            pass
    me['ootr_model'] = rel
    return me
