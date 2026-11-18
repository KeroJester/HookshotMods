"""Game models -> Blender meshes. Mesh data stays in game axes (Y-up); the object matrix (scene.placement_matrix)
turns it into Blender's Z-up. One Blender mesh per model file, shared by every placement of it."""
import bpy
import numpy as np

from .core.bfres import read_model


ROLE = {'_a0': 'albedo', '_n0': 'normal', '_rgh0': 'rough', '_rah0': 'rah', '_mt0': 'metal', '_ao0': 'ao', '_em0': 'emission'}


def _image(path, colorspace):
    img = bpy.data.images.get(path.split('/')[-1]) or bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = colorspace
    return img


def material(name, game=None, mat=None, textures=True):
    """One Blender material per game material name (shared across models): Principled BSDF fed by the material's
    layer-0 textures (_a0 albedo, _n0 normal, _rgh0 / _rah0 roughness, _ao0 AO, _mt0 metal, _em0 emission)."""
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    m = bpy.data.materials.new(name)
    m['ootr_material'] = name
    h = abs(hash(name))
    m.diffuse_color = (0.35 + (h % 7) / 20, 0.35 + (h // 7 % 7) / 20, 0.35 + (h // 49 % 7) / 20, 1.0)
    m.use_nodes = True
    if not (textures and game is not None and mat is not None):
        return m
    from .core.textures import texture_png
    roles = {}
    # layer 0 first; materials blending the terrain array (ta / tc) carry their own layer only as index 1
    for layer in '012':
        for sampler, tex in zip(mat.samplers, mat.textures):
            r = ROLE.get(sampler[:-1] + '0') if sampler[-1:] == layer else None
            if r and r not in roles and not tex.startswith(('Dummy', 'CmnTex_Bake')):
                roles[r] = tex
    nt = m.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    if 'albedo' not in roles and ('Water' in name or 'water' in name or '_fx0' in mat.samplers):
        bsdf.inputs['Base Color'].default_value = (0.12, 0.3, 0.32, 1)
        bsdf.inputs['Roughness'].default_value = 0.05
        if 'Alpha' in bsdf.inputs: bsdf.inputs['Alpha'].default_value = 0.55
        if hasattr(m, 'surface_render_method'): m.surface_render_method = 'BLENDED'
        elif hasattr(m, 'blend_method'): m.blend_method = 'BLEND'
        m['ootr_water'] = True
    def tex_node(role, colorspace, x, y):
        try:
            p = texture_png(game, roles[role], 'normal' if role == 'normal' else None)
        except Exception as e:
            print(f'[ootr] {roles[role]}: {e}'); return None
        if not p: return None
        n = nt.nodes.new('ShaderNodeTexImage'); n.image = _image(p, colorspace); n.location = (x, y)
        n.label = role
        return n
    base = None
    if 'albedo' in roles:
        a = tex_node('albedo', 'sRGB', -700, 300)
        if a:
            base = a.outputs['Color']
            px = a.image.pixels
            # alpha that cuts out (foliage, fences): clipped; many albedo maps keep other data in alpha (left alone)
            if len(px) and a.image.channels == 4:
                alpha = np.empty(len(px), np.float32); px.foreach_get(alpha)
                al = alpha[3::4]
                if np.mean(al < 0.5) > 0.03 and np.mean(al > 0.9) > 0.3:
                    nt.links.new(a.outputs['Alpha'], bsdf.inputs['Alpha'])
                    m.blend_method = 'CLIP' if hasattr(m, 'blend_method') else None
                    if hasattr(m, 'surface_render_method'): m.surface_render_method = 'DITHERED'
            m['ootr_albedo'] = roles['albedo']
    if 'ao' in roles and base is not None:
        ao = tex_node('ao', 'Non-Color', -700, 0)
        if ao:
            mix = nt.nodes.new('ShaderNodeMix'); mix.data_type = 'RGBA'; mix.blend_type = 'MULTIPLY'
            mix.inputs['Factor'].default_value = 1.0; mix.location = (-350, 300)
            nt.links.new(base, mix.inputs['A']); nt.links.new(ao.outputs['Color'], mix.inputs['B'])
            base = mix.outputs['Result']
    if base is not None:
        nt.links.new(base, bsdf.inputs['Base Color'])
    if 'normal' in roles:
        n = tex_node('normal', 'Non-Color', -700, -300)
        if n:
            nm = nt.nodes.new('ShaderNodeNormalMap'); nm.location = (-350, -300)
            nt.links.new(n.outputs['Color'], nm.inputs['Color']); nt.links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])
    rough_role = 'rough' if 'rough' in roles else 'rah' if 'rah' in roles else None
    if rough_role:
        r = tex_node(rough_role, 'Non-Color', -700, -600)
        if r:
            sep = nt.nodes.new('ShaderNodeSeparateColor'); sep.location = (-350, -600)
            nt.links.new(r.outputs['Color'], sep.inputs['Color']); nt.links.new(sep.outputs['Red'], bsdf.inputs['Roughness'])
    if 'metal' in roles:
        mt = tex_node('metal', 'Non-Color', -700, -900)
        if mt:
            sep = nt.nodes.new('ShaderNodeSeparateColor'); sep.location = (-350, -900)
            nt.links.new(mt.outputs['Color'], sep.inputs['Color']); nt.links.new(sep.outputs['Red'], bsdf.inputs['Metallic'])
    if 'emission' in roles:
        em = tex_node('emission', 'sRGB', -700, -1200)
        if em:
            key = 'Emission Color' if 'Emission Color' in bsdf.inputs else 'Emission'
            nt.links.new(em.outputs['Color'], bsdf.inputs[key])
            if 'Emission Strength' in bsdf.inputs: bsdf.inputs['Emission Strength'].default_value = 1.0
    return m


def model_mesh(game, rel, textures=True):
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
        me.materials.append(material(mat.name, game, mat, textures))
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
