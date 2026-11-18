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

TEMPLATE_MODEL = 'Model/Land_SageRoom_Base_A.Land_SageRoom_Base_A_01.bfres.zs'   # (Clock Town's: renders in game, §80h)
TEMPLATE_SHAPE = 1
BAKE_TEX = ['CmnTex_BakeDefault', 'CmnTex_BakeLightDefault']
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


def _base_read(game, rel):
    """The game's own file (no mod copy: other mods may replace this template, e.g. ClockTownDev's ground)."""
    from ..vendor import zstd_helper
    with open(os.path.join(game.romfs, rel), 'rb') as f:
        return zstd_helper.decompress_zs(f.read(), rel, game.romfs)


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
        tmpl_tex = _base_read(game, TEMPLATE_TEX)
        materials = []
        for k in keys:
            tname = _tex_name(banc, k)
            files[f'Tex/{tname}.bntx.zs'] = bntx1.build(tmpl_tex, tname, _pow2_image(images[k]))
            materials.append(dict(name=f'Mt_Ootr_{banc}_{len(materials):03d}', textures=[tname, NORMAL_TEX, ROUGH_TEX] + BAKE_TEX))
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
        model = bfres_build.Writer(_base_read(game, TEMPLATE_MODEL)).build(out_meshes, materials, template_shape=TEMPLATE_SHAPE,
                                                                    model_name=actor, file_name=f"{project}.{actor}")
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


def compound_rels(game, banc):
    """The map's compound body files: one, or tiles <map>_Xi_Zj (Kokiri Forest, Dark Link's room, ...)."""
    from .banc import find_folder
    folder = find_folder(game, banc)
    d = f'Phive/StaticCompoundBody/{folder}'
    names = [n for n in game.listdir(d) if n.endswith('.bphsc.zs') and (n.startswith(banc + '.') or n.startswith(banc + '_X'))]
    tiles = [n for n in names if n.startswith(banc + '_X')]
    return [f'{d}/{n}' for n in (tiles or names)]


def pristine(game, rel, mod_dir):
    """A compound body file before any custom collision (kept in <mod>/ootr_compound/)."""
    side = os.path.join(mod_dir, 'ootr_compound', os.path.basename(rel)[:-3])
    if os.path.exists(side):
        return open(side, 'rb').read()
    d = game.read(rel)                          # (the map mod's copy for new maps, else the game's)
    os.makedirs(os.path.dirname(side), exist_ok=True)
    with open(side, 'wb') as f: f.write(d)
    return d


def instance_transform(tag0, k=0):
    bfres_build, bntx1, bphsc, phsh = _mapimport()
    t = bphsc.Tag(bphsc._wrap(tag0)); D = t.data[0]; d = t.d
    ioff, icount, _ = struct.unpack_from('<qii', d, D + 0x38)
    if icount <= k: raise ValueError('no such instance')
    b = D + 0x38 + ioff + 0x40 * k
    q = struct.unpack_from('<4f', d, b); tr = np.array(struct.unpack_from('<3f', d, b + 0x10))
    sc = np.array(struct.unpack_from('<3f', d, b + 0x20))
    e = b + 0x30; sa = e + struct.unpack_from('<q', d, e)[0]
    return q, tr, sc, phsh.read_mesh(d, sa, with_tags=True, quads=True), icount


def collision_files(game, banc, mod_dir, collision, report):
    """Custom collision in layer 0 instance 0 of the map's compound body (the tile nearest to it for tiled maps):
    added to the pristine triangles, or replacing them for 'Empty' new maps (<mod>/ootr_compound/<map>.empty).
    The same path that made Clock Town walkable (§80l)."""
    bfres_build, bntx1, bphsc, phsh = _mapimport()
    V, T = np.asarray(collision[0], float), np.asarray(collision[1], int)
    rels = compound_rels(game, banc)
    if not rels:
        report.append('collision: the map has no collision body (skipped)'); return {}
    centre = V.mean(0)
    best = None
    for rel in rels:
        base = pristine(game, rel, mod_dir)
        for li, (o, s) in enumerate(bphsc.layer_tagfiles(base)):
            try:
                q, tr, sc, mesh, n = instance_transform(base[o:o + s], 0)
            except Exception:                     # (a layer without a mesh instance)
                continue
            dist = float(np.linalg.norm(tr - centre))
            if best is None or dist < best[0]: best = (dist, rel, base, base[o:o + s], q, tr, sc, mesh, li)
            break
    if best is None:
        report.append('collision: no tile of the map can take custom collision (skipped)'); return {}
    _, rel, base, tag0, q, tr, sc, mesh, layer = best
    x, y, z, w = q
    qi = (-x, -y, -z, w)                                     # (world -> instance: inverse rotation, then 1/scale)
    Vl = np.array([bphsc._quat_rotate(qi, v - tr) for v in V]) / sc
    empty = os.path.exists(os.path.join(mod_dir, 'ootr_compound', f'{banc}.empty'))
    V0, T0 = np.asarray(mesh[0], float), np.asarray(mesh[1], int)
    tags0 = list(mesh[2]) if len(mesh) > 2 and mesh[2] is not None else None
    if not empty and len(V0):
        Vm = np.concatenate([V0, Vl]); Tm = np.concatenate([T0[:, :3], T + len(V0)])
        tags = (tags0 + [tags0[0]] * len(T)) if tags0 else None
    else:
        Vm, Tm, tags = Vl, T, None
    shape = (Vm, Tm, tags) if tags else (Vm, Tm)
    new = bphsc.replace_layer(base, layer, bphsc.rebuild_layer2(tag0, {0: shape}))
    report.append(f'collision: {len(T)} triangles in {os.path.basename(rel)} layer {layer}' + (' (replacing the template floor)' if empty else ''))
    return {rel: new}
