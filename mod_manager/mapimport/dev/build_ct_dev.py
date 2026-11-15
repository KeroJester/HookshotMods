# build_ct_dev.py: (E2) South Clock Town's room model into the dev mod, in place of Market's ground model.
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from rf import rf, ROMFS
from mod_manager.mapimport import mm3d_room, bntx1, bfres_build
from mod_manager.zstd_helper import compress_zs
MM = os.path.expanduser('~/Games/Hookshot/mods/_MajoraAssets/extracted/mm3d_romfs/scenes/')
OUT = os.path.join(os.path.expanduser(os.environ.get('HOOKSHOT_GAME_DIR', '~/Games/Hookshot')), 'mods', sys.argv[1] if len(sys.argv) > 1 else '070-ClockTownDev', 'romfs')
t0 = time.time()
cmb = mm3d_room.load_room(open(MM + 'z2_clocktower_0_info.zsi', 'rb').read())
meshes, tris = mm3d_room.room_meshes(cmb)
P = np.concatenate([m['pos'] for m in meshes])
print('meshes', len(meshes), 'verts', len(P), 'bounds', P.min(0).round(2), P.max(0).round(2), f'{time.time()-t0:.1f}s')
tmpl_tex = rf('Tex/Etc_Gray50_A_Alb.bntx.zs')
used = sorted(set(m['texture'] for m in meshes))
tex_name = {}
for i in used:
    name = f'MMClockTown{i:02d}Alb'
    img = mm3d_room.texture_rgba(cmb, i)
    rel = f'Tex/{name}.bntx.zs'
    data = bntx1.build(tmpl_tex, name, img)
    p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').write(compress_zs(data, rel, ROMFS))
    tex_name[i] = name
# (moon) Majora's Mask's sky moon (actors/zelda2_lodmoon: model/moon.cmb), far behind and above the Clock Tower
if os.environ.get('CT_MOON', '1') == '1':
    import mod_manager.mm3d as mm3d
    mm3d.ROMFS = os.path.dirname(MM.rstrip('/'))
    from mod_manager.cmb import CMB
    mc = CMB(mm3d.actor_archive('zelda2_lodmoon')['model/moon.cmb'])
    MOON_SCALE = float(os.environ.get('CT_MOON_SCALE', '0.011'))
    MOON_AT = np.array([float(x) for x in os.environ.get('CT_MOON_AT', '60,130,-150').split(',')])
    moon, _ = mm3d_room.room_meshes(mc, MOON_SCALE)
    moon = [m for m in moon if not mc.textures[m['texture']]['name'].startswith('moon_sp')]   # (the halo sprite)
    ry = float(os.environ.get('CT_MOON_ROTY', '3.14159'))   # (its face is on -z in the model: turn it to the town)
    R = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]])
    for k, m in enumerate(moon):
        m['pos'] = m['pos'] @ R.T + MOON_AT
        m['nrm'] = m['nrm'] @ R.T; m['tan'] = m['tan'] @ R.T
        m['name'] = f'Moon{k:02d}'
        name = f'MMClockMoon{m["texture"]:02d}Alb'
        rel = f'Tex/{name}.bntx.zs'
        p = os.path.join(OUT, rel)
        if not os.path.exists(p):
            img = mm3d_room.texture_rgba(mc, m['texture']).astype(float)
            tint = [float(x) for x in os.environ.get('CT_MOON_TINT', '1.25,0.85,0.5').split(',')]   # (MM3D tints it in its material)
            img[..., :3] = np.clip(img[..., :3] * np.array(tint), 0, 255)
            open(p, 'wb').write(compress_zs(bntx1.build(tmpl_tex, name, img.astype(np.uint8)), rel, ROMFS))
        m['texture'] = 1000 + m['texture']
        tex_name[m['texture']] = name
        meshes.append(m)
    used = sorted(set(m['texture'] for m in meshes))
    print('moon meshes', len(moon))
# (tower) the Clock Tower's head and clock face: Majora's Mask's Obj_Tokeidai parts at their room placements
if os.environ.get('CT_TOWER', '1') == '1':
    import mod_manager.mm3d as mm3d
    mm3d.ROMFS = os.path.dirname(MM.rstrip('/'))
    from mod_manager.cmb import CMB
    ar = mm3d.actor_archive('zelda2_obj_tokeidai')
    U = 0.025                                   # metres per MM unit
    parts = [('z2_atama_model', (-280, 1103, -1020)),        # head (param 0x307f)
             ('z2_tannsin_model', (-280, 555, -833)),        # clock face (param 0x207f)
             ('z2_paneru_model', (-280, 555, -833)),
             ('z2_hari_model', (-280, 555, -830)),
             ('z2_hagu_model', (-280, 555, -833))]
    for name, at in parts:
        c = CMB(ar[f'model/{name}.cmb'])
        pm, _ = mm3d_room.room_meshes(c, 0.1 * U)
        for k, m in enumerate(pm):
            m['pos'] = m['pos'] + np.array(at) * U
            m['name'] = f'Tower_{name}_{k}'
            tname = f'MMClockTwr{len(tex_name):03d}Alb'     # (16 characters, as the template's name)
            rel = f'Tex/{tname}.bntx.zs'
            open(os.path.join(OUT, rel), 'wb').write(compress_zs(bntx1.build(tmpl_tex, tname, mm3d_room.texture_rgba(c, m['texture'])), rel, ROMFS))
            m['texture'] = 2000 + len(tex_name)
            tex_name[m['texture']] = tname
            meshes.append(m)
    used = sorted(set(m['texture'] for m in meshes))
    print('tower parts', len(parts))
mat_of = {i: k for k, i in enumerate(used)}
SAGE = os.environ.get('CT_TEMPLATE', 'sage') == 'sage'
extra = ['CmnTex_BakeDefault', 'CmnTex_BakeLightDefault'] if SAGE else []
materials = [dict(name=f'Mt_MMClockTown{i:04d}', textures=[tex_name[i], 'Dummy_Flat_Normal_CmnTex_Chara_DetailMap_Nrm',
                                                          'Land_HrlCity_Soil_A_01_Rgh'] + extra) for i in used]
for m in meshes: m['material'] = mat_of[m['texture']]
if os.environ.get('CT_GAME_TEX') == '1':                 # (E14) existing game textures only
    materials = [dict(m, textures=['Rock_SageRoom_Floor_A_Alb', 'Rock_SageRoom_Floor_A_Nrm', 'Rock_SageRoom_Floor_A_Rgh',
                                   'CmnTex_BakeDefault', 'CmnTex_BakeLightDefault']) for m in materials]
rel = 'Model/Land_SageRoom_Base_A.Land_SageRoom_Base_A_01.bfres.zs' if SAGE else 'Model/Land_HrlCity_Ground.Land_HrlCity_Ground_A_01.bfres.zs'
tmpl = rf(rel)
out = bfres_build.Writer(tmpl).build(meshes, materials, template_shape=1 if SAGE else 24)
if os.environ.get('CT_GROUND_AS_SAGE') == '1':          # (E13) Market's template, under the Sage room's model name
    materials = [dict(m, textures=m['textures'][:3]) for m in materials]
    out = bfres_build.Writer(rf('Model/Land_HrlCity_Ground.Land_HrlCity_Ground_A_01.bfres.zs')).build(
        meshes, materials, template_shape=24, model_name='Land_SageRoom_Base_A_01')
    rel = 'Model/Land_SageRoom_Base_A.Land_SageRoom_Base_A_01.bfres.zs'
p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
open(p, 'wb').write(compress_zs(out, rel, ROMFS))
np.save(os.path.join(os.path.dirname(__file__), '..', 'ct_tris.npy'), tris)
print('model', len(out), 'textures', len(used), f'{time.time()-t0:.1f}s')
