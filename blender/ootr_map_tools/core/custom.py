"""Custom geometry (no bpy, RESEARCH_NOTES.md §89): meshes modelled in Blender -> a new actor of the map (added next
to the game's: nothing existing is replaced) and collision in the map's static compound body.

Per map <m> (all names new):
  actor  Land_Ootr_<m>_A_01: Pack/Actor/<actor>.pack.zs (ActorParam: simple actor, default LOD / physics,
         UsePreActorRenderer; ModelInfo -> the model), a row appended to RSDB/ActorInfo (a copy of
         Land_SageRoom_Base_A_01's: a large map piece, display radius 1600 m) with the model's bounds
  model  Model/Land_Ootr_<m>_A.Land_Ootr_<m>_A_01.bfres.zs: bfres_build.Writer on Land_HrlCity_Ground_A_01 (shape 24,
         materials [albedo, flat normal, soil roughness]); positions are half floats, so the actor sits at the
         meshes' centre
  textures  Tex/Ootr<8 hex>_Alb.bntx.zs (16 characters, as the template Etc_Gray50_A_Alb), RGBA8 sRGB with mips
  placement  in the Dynamic layer (unregistered actors load there), made by the exporter (a new placement)
  collision  the map's compound body, layer 0 instance 0 (the template's ground: SageRoom's floor at (0, 100, 0)):
         the pristine body's triangles + the Blender collision triangles (in the instance's space). The pristine body
         is kept in <mod>/ootr_compound/ so exports do not pile up."""
import copy
import hashlib
import os
import struct

import numpy as np

from ..vendor.byml import write_byml, parse_byml

TEMPLATE_MODEL = 'Model/Land_HrlCity_Ground.Land_HrlCity_Ground_A_01.bfres.zs'
TEMPLATE_SHAPE = 24
TEMPLATE_TEX = 'Tex/Etc_Gray50_A_Alb.bntx.zs'
ROW_FROM = 'Land_SageRoom_Base_A_01'
ACTORINFO = 'RSDB/ActorInfo.Product.111.rstbl.byml.zs'
NORMAL_TEX = 'Dummy_Flat_Normal_CmnTex_Chara_DetailMap_Nrm'
ROUGH_TEX = 'Land_HrlCity_Soil_A_01_Rgh'


def actor_name(banc):
    return f'Land_Ootr_{banc}_A_01', f'Land_Ootr_{banc}_A'


def _tex_name(banc, key):
    return 'Ootr' + hashlib.sha1(f'{banc}/{key}'.encode()).hexdigest()[:8] + '_Alb'


def _pow2_image(img, limit=1024):
    """RGBA uint8 [h, w, 4] -> power-of-two sides (nearest), at most limit."""
    h, w = img.shape[:2]
    def p2(n): return max(4, min(limit, 1 << max(0, (int(n) - 1).bit_length())))
    H, W = p2(h), p2(w)
    if (H, W) == (h, w): return img
    ys = (np.arange(H) * h // H).clip(0, h - 1); xs = (np.arange(W) * w // W).clip(0, w - 1)
    return img[ys][:, xs]


def _tangents(nrm):
    a = np.where(np.abs(nrm[:, 1:2]) < 0.9, np.array([[0.0, 1.0, 0.0]]), np.array([[1.0, 0.0, 0.0]]))
    t = np.cross(a, nrm)
    return t / np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)


def _mapimport():
    """The model / texture / collision writers (the mod manager's mapimport package, vendored at build time or found in
    the source checkout)."""
    try:
        from ..vendor.mapimport import bfres_build, bntx1, bphsc, phsh
    except ImportError:
        import sys
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        if root not in sys.path: sys.path.insert(0, root)
        from mod_manager.mapimport import bfres_build, bntx1, bphsc, phsh
    return bfres_build, bntx1, bphsc, phsh


def merged_rows(game, mod_dir, rel):
    """A shared RSDB table's rows: the game's + every enabled mod's additions (this mod's own rows win)."""
    from .newmap import enabled_mods, _read_mod_byml
    rows = {r['__RowId']: r for r in game.byml_base(rel)}
    mine = os.path.normcase(os.path.abspath(mod_dir))
    mods = [m for m in enabled_mods(game) if os.path.normcase(os.path.abspath(m)) != mine] + [mod_dir]
    for m in mods:
        p = os.path.join(m, 'romfs', rel)
        if not os.path.exists(p): continue
        here = m is mod_dir
        for r in _read_mod_byml(game, p, rel):
            if here or r['__RowId'] not in rows: rows[r['__RowId']] = r
    return rows


def build_pack(actor, project):
    from ..vendor.zstd_helper import create_sarc
    ap = {'Components': {
        'ActorLogicNodeRef': 'Work/Logic/NodeDefinition/Node/ActorLogicSimpleActor.aidefn',
        'LODRef': '?Component/LODParam/Default.engine__component__LODParam.bgyml',
        'ModelInfoRef': f'?Component/ModelInfo/{actor}.engine__component__ModelInfo.bgyml',
        'PhysicsRef': '?Component/Physics/MapDefault.engine__component__PhysicsParam.bgyml',
        'PreActorSettingRef': '?ActorSystem/PreActorSetting/UsePreActorRenderer.game__actor__PreActorSetting.bgyml',
        'SystemSetting': '?ActorSystem/ActorSystemSetting/GameActorDefault.engine__actor__ActorSystemSetting.bgyml'}}
    mi = {'FmdbName': actor, 'ModelProjectName': project}
    return create_sarc({f'Actor/{actor}.engine__actor__ActorParam.bgyml': write_byml(ap),
                        f'Component/ModelInfo/{actor}.engine__component__ModelInfo.bgyml': write_byml(mi)})


def write_custom(game, banc, mod_dir, meshes, images, collision):
    """meshes: [dict(name, material (key), pos (n,3) game axes, nrm (n,3), uv (n,2), tris (m,3))];
    images: {material key: RGBA uint8 [h, w, 4]}; collision: (V (n,3) game axes, T (m,3)) or None.
    -> (report lines, {rel: decompressed bytes written}, placement dict(actor, translate) or None)."""
    from ..vendor.zstd_helper import compress_zs
    bfres_build, bntx1, bphsc, phsh = _mapimport()
    files, report, placement = {}, [], None
    actor, project = actor_name(banc)
    if meshes:
        allp = np.concatenate([m['pos'] for m in meshes])
        lo, hi = allp.min(0), allp.max(0)
        centre = (lo + hi) / 2
        keys = sorted({m['material'] for m in meshes})
        tmpl_tex = game.read(TEMPLATE_TEX)
        materials = []
        for k in keys:
            tname = _tex_name(banc, k)
            files[f'Tex/{tname}.bntx.zs'] = bntx1.build(tmpl_tex, tname, _pow2_image(images[k]))
            materials.append(dict(name=f'Mt_Ootr_{banc}_{len(materials):03d}', textures=[tname, NORMAL_TEX, ROUGH_TEX]))
        out_meshes = []
        for m in meshes:                                     # (u16 indices: split into chunks of 65535 vertices)
            P, N, U, T = m['pos'] - centre, m['nrm'], m['uv'], np.asarray(m['tris'])
            step = 65535 // 3 * 3
            flat = T.reshape(-1)
            for s in range(0, len(flat), step):
                sel = flat[s:s + step]
                used, inv = np.unique(sel, return_inverse=True)
                out_meshes.append(dict(name=f'{m["name"]}_{s // step}', material=keys.index(m['material']),
                                       pos=P[used], nrm=N[used], tan=_tangents(N[used]), uv=U[used],
                                       idx=inv.astype(np.uint16)))
        model = bfres_build.Writer(game.read(TEMPLATE_MODEL)).build(out_meshes, materials, template_shape=TEMPLATE_SHAPE,
                                                                    model_name=actor)
        files[f'Model/{project}.{actor}.bfres.zs'] = model
        files[f'Pack/Actor/{actor}.pack.zs'] = build_pack(actor, project)
        rows = merged_rows(game, mod_dir, ACTORINFO)
        row = copy.deepcopy(rows[ROW_FROM])
        row.update({'__RowId': actor, 'FmdbName': actor, 'ModelProjectName': project,
                    'ModelAabbMin': {'X': float(lo[0] - centre[0]), 'Y': float(lo[1] - centre[1]), 'Z': float(lo[2] - centre[2])},
                    'ModelAabbMax': {'X': float(hi[0] - centre[0]), 'Y': float(hi[1] - centre[1]), 'Z': float(hi[2] - centre[2])}})
        rows[actor] = row
        files[ACTORINFO] = write_byml([rows[k] for k in sorted(rows)])
        placement = dict(actor=actor, translate=tuple(float(v) for v in centre))
        report.append(f'custom model: {len(out_meshes)} meshes, {len(keys)} materials, {sum(len(m["idx"]) // 3 for m in out_meshes)} triangles ({actor})')
    if collision is not None and len(collision[1]):
        files.update(collision_files(game, banc, mod_dir, collision, report))
    for rel, data in files.items():
        p = os.path.join(mod_dir, 'romfs', rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(compress_zs(data, rel, game.romfs) if rel.endswith('.zs') else data)
    return report, files, placement


def compound_rel(game, banc):
    from .banc import find_folder
    folder = find_folder(game, banc)
    return f'Phive/StaticCompoundBody/{folder}/{banc}.Nin_Ounce_NVN2.bphsc.zs'


def pristine_compound(game, banc, mod_dir):
    """The map's compound body before any custom collision (kept in <mod>/ootr_compound/<map>.bphsc)."""
    side = os.path.join(mod_dir, 'ootr_compound', f'{banc}.bphsc')
    if os.path.exists(side):
        return open(side, 'rb').read()
    rel = compound_rel(game, banc)
    d = game.read(rel)                          # (the map mod's copy for new maps, else the game's)
    os.makedirs(os.path.dirname(side), exist_ok=True)
    with open(side, 'wb') as f: f.write(d)
    return d


def instance_transform(tag0, k=0):
    bfres_build, bntx1, bphsc, phsh = _mapimport()
    t = bphsc.Tag(bphsc._wrap(tag0)); D = t.data[0]; d = t.d
    ioff, icount, _ = struct.unpack_from('<qii', d, D + 0x38)
    b = D + 0x38 + ioff + 0x40 * k
    q = struct.unpack_from('<4f', d, b); tr = np.array(struct.unpack_from('<3f', d, b + 0x10))
    sc = np.array(struct.unpack_from('<3f', d, b + 0x20))
    e = b + 0x30; sa = e + struct.unpack_from('<q', d, e)[0]
    return q, tr, sc, phsh.read_mesh(d, sa, with_tags=True, quads=True), icount


def collision_files(game, banc, mod_dir, collision, report):
    """Custom collision triangles merged into layer 0 instance 0 of the pristine compound body."""
    bfres_build, bntx1, bphsc, phsh = _mapimport()
    base = pristine_compound(game, banc, mod_dir)
    layers = bphsc.layer_tagfiles(base)
    o, s = layers[0]
    tag0 = base[o:o + s]
    q, tr, sc, mesh, n = instance_transform(tag0, 0)
    V, T = np.asarray(collision[0], float), np.asarray(collision[1], int)
    x, y, z, w = q
    qi = (-x, -y, -z, w)                                     # (world -> instance: inverse rotation, then 1/scale)
    Vl = np.array([bphsc._quat_rotate(qi, v - tr) for v in V]) / sc
    V0, T0 = np.asarray(mesh[0], float), np.asarray(mesh[1], int)
    tags0 = list(mesh[2]) if len(mesh) > 2 and mesh[2] is not None else None
    keep_template = len(V0) > 0 and not (np.ptp(V0, 0).max() < 1.5 and V0[:, 1].mean() < -40)
    if keep_template:
        Vm = np.concatenate([V0, Vl]); Tm = np.concatenate([T0[:, :3] if T0.shape[1] > 3 else T0, T + len(V0)])
        tags = (tags0 + [tags0[0] if tags0 else 0] * len(T)) if tags0 else None
    else:
        Vm, Tm, tags = Vl, T, None
    shape = (Vm, Tm, tags) if tags else (Vm, Tm)
    new = bphsc.replace_layer(base, 0, bphsc.rebuild_layer2(tag0, {0: shape}))
    report.append(f'collision: {len(T)} triangles in the map\'s collision body')
    return {compound_rel(game, banc): new}


def empty_compound(game, banc, mod_dir):
    """New 'Empty' maps: the template's ground collision (layer 0 instance 0) becomes a tiny box far below, and that
    body is the pristine one (custom collision is added to it). -> {rel: bytes}"""
    bfres_build, bntx1, bphsc, phsh = _mapimport()
    rel = compound_rel(game, banc)
    d = game.read(rel)
    o, s = bphsc.layer_tagfiles(d)[0]
    V, T = bphsc.box_mesh(0.5)                  # (tiny shapes get a tiny quantisation scale: their offset overflows)
    V = V + np.array([0.0, -50.0, 0.0])         # (out of reach; much further overflows the quantised offsets)
    new = bphsc.replace_layer(d, 0, bphsc.rebuild_layer2(d[o:o + s], {0: (V, T)}))
    side = os.path.join(mod_dir, 'ootr_compound', f'{banc}.bphsc')
    os.makedirs(os.path.dirname(side), exist_ok=True)
    with open(side, 'wb') as f: f.write(new)
    return {rel: new}
