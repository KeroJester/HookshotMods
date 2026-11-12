#!/usr/bin/env python3
"""
Rebuild Goron Transformation Mod Assets:
1. Extracts authentic Majora's Mask Goron Link 3D model (limbs, vertices, faces) from MM ROM.
2. Applies head-relative coordinate transform (Y offset -1.05m so feet reach the ground at Y=0.0m).
3. Injects MM Goron Link mesh into Armor_Child_Mask_Goron.Armor_Child_Mask_Goron.bfres.zs.
4. Updates Armor_Child_Mask_Goron.pack.zs to hide Link's body materials when equipped.
5. Updates Player_Child and Player_Adult packs with HiddenMaterialGroupList and MM 24.0 m/s roll parameters.
6. Cleans up any leftover model/RSDB files that interfere with normal gameplay.
"""

import os
import sys
import struct
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import find_game_dir
ROOT_DIR = os.environ.get('HOOKSHOT_GAME_DIR') or find_game_dir()   # the game folder

import zstd_helper
import byml

ROMFS_DIR = os.path.join(ROOT_DIR, 'romfs')
MOD_ROMFS_DIR = os.path.join(ROOT_DIR, 'mods', '010-GoronMaskTransformation', 'romfs')
MM_ROM_PATH = os.path.join(ROOT_DIR, 'mods', '_MajoraAssets', "Legend of Zelda, The - Majora's Mask (USA).z64")
EXTRACTED_OBJ_PATH = os.path.join(ROOT_DIR, 'mods', '_MajoraAssets', 'extracted', 'object_link_goron.bin')


def decompress_yaz0(src):
    if src[:4] != b"Yaz0":
        return src
    uncomp_size = struct.unpack(">I", src[4:8])[0]
    dst = bytearray()
    src_idx = 16
    while len(dst) < uncomp_size and src_idx < len(src):
        b = src[src_idx]
        src_idx += 1
        for bit in range(7, -1, -1):
            if len(dst) >= uncomp_size:
                break
            if (b >> bit) & 1:
                dst.append(src[src_idx])
                src_idx += 1
            else:
                b1 = src[src_idx]
                b2 = src[src_idx + 1]
                src_idx += 2
                dist = ((b1 & 0xF) << 8) | b2
                length = b1 >> 4
                if length == 0:
                    length = src[src_idx] + 0x12
                    src_idx += 1
                else:
                    length += 2
                copy_src = len(dst) - (dist + 1)
                for _ in range(length):
                    dst.append(dst[copy_src])
                    copy_src += 1
    return bytes(dst)


def get_mm_data():
    if os.path.exists(EXTRACTED_OBJ_PATH):
        with open(EXTRACTED_OBJ_PATH, 'rb') as f:
            return f.read()
    with open(MM_ROM_PATH, 'rb') as f:
        rom = f.read()
    data = decompress_yaz0(rom[0x00EA96D0:0x00EBC190])
    os.makedirs(os.path.dirname(EXTRACTED_OBJ_PATH), exist_ok=True)
    with open(EXTRACTED_OBJ_PATH, 'wb') as f:
        f.write(data)
    return data


def extract_mm_goron_mesh():
    mm_data = get_mm_data()

    # Parse 27 limbs
    table_ptr = 0x1C848
    limb_count = 27
    limbs = []
    for i in range(limb_count):
        limb_p = struct.unpack(">I", mm_data[table_ptr + i*4 : table_ptr + i*4 + 4])[0] & 0x00FFFFFF
        x, y, z, child, sibling, dl = struct.unpack(">hhhBBI", mm_data[limb_p : limb_p + 12])
        limbs.append({
            "id": i,
            "pos": (x, y, z),
            "child": child if child < limb_count else None,
            "sibling": sibling if sibling < limb_count else None,
            "dl": (dl & 0x00FFFFFF) if dl != 0 else None
        })

    transforms = {}
    def traverse(limb_id, current_pos):
        limb = limbs[limb_id]
        lx, ly, lz = limb["pos"]
        pos = (current_pos[0] + lx, current_pos[1] + ly, current_pos[2] + lz)
        transforms[limb_id] = pos
        if limb["child"] is not None:
            traverse(limb["child"], pos)
        if limb["sibling"] is not None:
            traverse(limb["sibling"], current_pos)

    traverse(0, (0, 0, 0))

    verts = []
    faces = []

    for limb_id in range(limb_count):
        dl_off = limbs[limb_id]["dl"]
        if dl_off is None:
            continue
        tx, ty, tz = transforms[limb_id]
        
        vtx_cache = {}
        i = dl_off
        while i < len(mm_data) - 8:
            w0, w1 = struct.unpack(">II", mm_data[i:i+8])
            op = w0 >> 24
            if op == 0x01: # G_VTX
                num = (w0 >> 12) & 0xFF
                end = (w0 >> 1) & 0x7F
                dest = end - num
                vaddr = w1 & 0x00FFFFFF
                for vi in range(num):
                    v_off = vaddr + vi * 16
                    if v_off + 16 <= len(mm_data):
                        x, y, z, flag, u, v, r, g, b, a = struct.unpack(">hhhHhhBBBB", mm_data[v_off:v_off+16])
                        
                        # Head-relative transform for mask attachment:
                        # Link's Head bone is at Y = +1.05m.
                        # Offsetting by -1.05m places Goron Link's feet exactly on the ground (Y = 0.0m)
                        # and head at Y = 1.35m.
                        n64_h = (x + tx) / 100.0
                        n64_w = (z + tz) / 100.0
                        n64_d = (y + ty) / 100.0

                        botw_y = (n64_h + 0.18) * (1.35 / 72.83) - 1.05  # Feet at -1.05m rel to head (= 0.0m world)
                        botw_x = n64_w * (0.80 / 22.64)                  # Width -0.40m to +0.40m
                        botw_z = (n64_d - 36.575) * (0.80 / 14.87)       # Depth -0.40m to +0.40m

                        nx = (b if b < 128 else b - 256) / 127.0
                        ny = (r if r < 128 else r - 256) / 127.0
                        nz = (g if g < 128 else g - 256) / 127.0

                        v_idx = len(verts)
                        verts.append((botw_x, botw_y, botw_z, nx, ny, nz, u / 1024.0, (1024.0 - v) / 1024.0))
                        vtx_cache[dest + vi] = v_idx
            elif op == 0x05: # G_TRI1
                v0 = ((w0 >> 16) & 0xFF) // 2
                v1 = ((w0 >> 8) & 0xFF) // 2
                v2 = (w0 & 0xFF) // 2
                if v0 in vtx_cache and v1 in vtx_cache and v2 in vtx_cache:
                    faces.append((vtx_cache[v0], vtx_cache[v1], vtx_cache[v2]))
            elif op == 0x06: # G_TRI2
                v0 = ((w0 >> 16) & 0xFF) // 2
                v1 = ((w0 >> 8) & 0xFF) // 2
                v2 = (w0 & 0xFF) // 2
                v3 = ((w1 >> 16) & 0xFF) // 2
                v4 = ((w1 >> 8) & 0xFF) // 2
                v5 = (w1 & 0xFF) // 2
                if v0 in vtx_cache and v1 in vtx_cache and v2 in vtx_cache:
                    faces.append((vtx_cache[v0], vtx_cache[v1], vtx_cache[v2]))
                if v3 in vtx_cache and v4 in vtx_cache and v5 in vtx_cache:
                    faces.append((vtx_cache[v3], vtx_cache[v4], vtx_cache[v5]))
            elif op == 0xDF:
                break
            i += 8

    return verts, faces


def pack_normal(nx, ny, nz):
    ix = int(round(max(-1.0, min(1.0, nx)) * 511)) & 0x3FF
    iy = int(round(max(-1.0, min(1.0, ny)) * 511)) & 0x3FF
    iz = int(round(max(-1.0, min(1.0, nz)) * 511)) & 0x3FF
    return ix | (iy << 10) | (iz << 20)


def step1_goron_mask_bfres():
    print("Step 1: Building authentic MM Goron Link Mask BFRES model...")
    verts, faces = extract_mm_goron_mesh()
    print(f"  Extracted {len(verts)} vertices, {len(faces)} triangles from authentic MM ROM")

    # Load vanilla Armor_Child_Mask_Goron BFRES template (111,312 bytes)
    src_bfres = os.path.join(ROMFS_DIR, 'Model', 'Armor_Child_Mask_Goron.Armor_Child_Mask_Goron.bfres.zs')
    with open(src_bfres, 'rb') as f:
        bfres_bytes = bytearray(zstd_helper.decompress_zs(f.read(), 'Armor_Child_Mask_Goron.Armor_Child_Mask_Goron.bfres.zs', ROMFS_DIR))

    orig_sz = len(bfres_bytes)
    idx_buf_off = 0x04000
    vtx_buf_off = 0x09F00

    # Write indices
    idx_list = []
    for f in faces:
        idx_list.extend(f)

    for ii, idx in enumerate(idx_list):
        struct.pack_into("<H", bfres_bytes, idx_buf_off + ii * 2, idx)

    # Pad remaining index slots with vertex 0 (degenerate triangles)
    for ii in range(len(idx_list), 6786):
        struct.pack_into("<H", bfres_bytes, idx_buf_off + ii * 2, 0)

    # Write vertices
    for vi, v in enumerate(verts):
        off = vtx_buf_off + vi * 28
        p_norm = pack_normal(v[3], v[4], v[5])
        p_tang = pack_normal(-v[5], 0.0, v[3])
        b_vtx = struct.pack("<fffIIeeI", v[0], v[1], v[2], p_norm, p_tang, v[6], v[7], 0)
        bfres_bytes[off:off+28] = b_vtx

    last_vtx = bfres_bytes[vtx_buf_off + (len(verts)-1)*28 : vtx_buf_off + len(verts)*28]
    for vi in range(len(verts), 2472):
        off = vtx_buf_off + vi * 28
        bfres_bytes[off:off+28] = last_vtx

    assert len(bfres_bytes) == orig_sz, f"Size changed: {len(bfres_bytes)} vs {orig_sz}"

    comp = zstd_helper.compress_zs(bytes(bfres_bytes), "Armor_Child_Mask_Goron.Armor_Child_Mask_Goron.bfres.zs", ROMFS_DIR)
    out_dir = os.path.join(MOD_ROMFS_DIR, 'Model')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "Armor_Child_Mask_Goron.Armor_Child_Mask_Goron.bfres.zs")
    with open(out_file, 'wb') as f:
        f.write(comp)
    print(f"  ✓ Saved Armor_Child_Mask_Goron BFRES to {out_file} ({len(comp)} bytes)")

    # Ensure no corrupt or player-replacing BFRES files remain in mod Model dir
    for dead_file in ('Player_Child.Player_Child.bfres.zs', 'Player_Adult.Player_Adult.bfres.zs', 'Npc_Goron_GON_001.Npc_Goron_GON_001.bfres.zs'):
        p = os.path.join(out_dir, dead_file)
        if os.path.exists(p):
            os.remove(p)
            print(f"  ✓ Removed unneeded {dead_file}")


def step2_actor_pack():
    print("Step 2: Building Armor_Adult_Goron.pack.zs and Armor_Child_Mask_Goron.pack.zs...")
    out_dir = os.path.join(MOD_ROMFS_DIR, 'Pack', 'Actor')
    os.makedirs(out_dir, exist_ok=True)

    # 1. Adult Goron transformation pack (Armor_Adult_Goron.pack.zs)
    src_path_a = os.path.join(ROMFS_DIR, 'Pack', 'Actor', 'Armor_Adult_Goron.pack.zs')
    with open(src_path_a, 'rb') as f:
        data_a = zstd_helper.decompress_zs(f.read(), 'Armor_Adult_Goron.pack.zs', ROMFS_DIR)
    sarc_a = zstd_helper.unpack_sarc_to_dict(data_a)

    k_model_a = 'Component/ModelInfo/Armor_Goron.engine__component__ModelInfo.bgyml'
    model_a = byml.parse_byml(sarc_a[k_model_a])
    model_a['FmdbName'] = 'Armor_Adult_Goron'
    model_a['ModelProjectName'] = 'Armor_Adult_Goron'
    sarc_a[k_model_a] = byml.write_byml(model_a)

    k_armor_a = 'Component/ArmorParam/Armor_Goron.game__component__ArmorParam.bgyml'
    armor_a = byml.parse_byml(sarc_a[k_armor_a])
    armor_a['HideMaterialGroupNameList'] = [
        'G_Body', 'G_Angin', 'G_Angin_Acs', 'G_Vest', 'G_Leater_Upper', 'G_Feather',
        'G_Boots', 'G_Glove', 'G_Head_All', 'G_Head', 'G_Face_Off', 'G_Face_Only',
        'G_EyeBrow', 'G_Earring', 'G_Lower', 'G_Upper', 'G_Belt_A', 'G_Belt_C'
    ]
    sarc_a[k_armor_a] = byml.write_byml(armor_a)

    comp_a = zstd_helper.compress_zs(zstd_helper.create_sarc(sarc_a), 'Armor_Adult_Goron.pack.zs', ROMFS_DIR)
    with open(os.path.join(out_dir, 'Armor_Adult_Goron.pack.zs'), 'wb') as f:
        f.write(comp_a)
    print("  ✓ Saved Armor_Adult_Goron.pack.zs (Adult Link Goron playable character)")

    # 2. Child Goron transformation pack (Armor_Child_Mask_Goron.pack.zs)
    src_path_c = os.path.join(ROMFS_DIR, 'Pack', 'Actor', 'Armor_Child_Mask_Goron.pack.zs')
    with open(src_path_c, 'rb') as f:
        data_c = zstd_helper.decompress_zs(f.read(), 'Armor_Child_Mask_Goron.pack.zs', ROMFS_DIR)
    sarc_c = zstd_helper.unpack_sarc_to_dict(data_c)

    k_model_c = 'Component/ModelInfo/Item_Mask_Goron.engine__component__ModelInfo.bgyml'
    model_c = byml.parse_byml(sarc_c[k_model_c])
    model_c['FmdbName'] = 'Armor_Child_Mask_Goron'
    model_c['ModelProjectName'] = 'Armor_Child_Mask_Goron'
    sarc_c[k_model_c] = byml.write_byml(model_c)

    k_armor_c = 'Component/ArmorParam/Armor_Mask_Base.game__component__ArmorParam.bgyml'
    armor_c = byml.parse_byml(sarc_c[k_armor_c])
    armor_c['ArmorEffect'] = 'FireProof'
    armor_c['BaseDefense'] = 8
    armor_c['GroupName'] = 'GoronTransformation'
    armor_c['HasSoundCloth'] = False
    armor_c['HideMaterialGroupNameList'] = [
        'G_Head_All', 'G_Head', 'G_Face_Off', 'G_Face_Only', 'G_EyeBrow', 'G_Earring',
        'G_Upper', 'G_Lower', 'G_UpperBeltSet', 'G_Belt_A', 'G_Belt_C', 'G_Underwear',
        'G_Body', 'G_Angin', 'G_Angin_Acs', 'G_Vest', 'G_Leater_Upper', 'G_Feather',
        'G_Boots', 'G_Glove'
    ]
    sarc_c[k_armor_c] = byml.write_byml(armor_c)

    comp_c = zstd_helper.compress_zs(zstd_helper.create_sarc(sarc_c), 'Armor_Child_Mask_Goron.pack.zs', ROMFS_DIR)
    with open(os.path.join(out_dir, 'Armor_Child_Mask_Goron.pack.zs'), 'wb') as f:
        f.write(comp_c)
    print("  ✓ Saved Armor_Child_Mask_Goron.pack.zs (Child Link Goron playable character)")

    # Clean unneeded head packs if present
    for dead_pack in ('Armor_Child_Default_Head.pack.zs', 'Armor_Adult_Default_Head.pack.zs', 'Armor_Adult_Normal.pack.zs'):
        p = os.path.join(out_dir, dead_pack)
        if os.path.exists(p):
            os.remove(p)
            print(f"  ✓ Cleaned {dead_pack}")


def step3_clean_player_packs():
    print("Step 3: Ensuring vanilla player packs are preserved (no black screen overrides)...")
    out_dir = os.path.join(MOD_ROMFS_DIR, 'Pack', 'Actor')
    for actor_name in ('Player_Child.pack.zs', 'Player_Adult.pack.zs'):
        p = os.path.join(out_dir, actor_name)
        if os.path.exists(p):
            os.remove(p)
            print(f"  ✓ Cleaned {actor_name}")


def step4_clean():
    # Remove corrupt/unneeded RSDB override if present
    rsdb_dir = os.path.join(MOD_ROMFS_DIR, 'RSDB')
    if os.path.exists(rsdb_dir):
        shutil.rmtree(rsdb_dir)
        print("  ✓ Cleaned RSDB overrides")
    # Clean unwanted model overrides, but preserve Armor_Child_Mask_Goron BFRES
    model_dir = os.path.join(MOD_ROMFS_DIR, 'Model')
    if os.path.exists(model_dir):
        for f in os.listdir(model_dir):
            if f != 'Armor_Child_Mask_Goron.Armor_Child_Mask_Goron.bfres.zs':
                p = os.path.join(model_dir, f)
                if os.path.isfile(p):
                    os.remove(p)
                    print(f"  ✓ Cleaned unwanted model {f}")


def main():
    print("=== Building Authentic Majora's Mask Goron Transformation Mod ===")
    step1_goron_mask_bfres()
    step2_actor_pack()
    step3_clean_player_packs()
    step4_clean()
    print("=== Mod Assets Successfully Built! ===")


if __name__ == '__main__':
    main()
