#!/usr/bin/env python3
"""
Build script for Mod Playground mod for Hookshot / OoT Remastered.
Generates all RomFS assets:
1. USen.Product.110.sarc.zs (Title_00.msbt with Mod Playground label)
2. OpenTitle.module.ainb (4 buttons setup)
3. NormalTitle.module.ainb (Pa_Button_03 string selector routing to playground)
4. PlaygroundOpening.module.ainb (Map and scene loader without intro cutscenes)
5. PlaygroundSetPouch.module.ainb (Full 20 hearts, full magic, all items pushed)
6. ModPlayground.game__banc__GameBancParam.bgyml (Banc parameter)
7. ModPlayground_Static.bcett.byml.zs & ModPlayground_Dynamic.bcett.byml.zs (Arena + StartPos + Master Sword Pedestal)
8. StartPos.byml.zs (Start position mapping)
"""

import os
import sys
import copy
import uuid
import struct

# Ensure mod_manager is on path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, 'mod_manager'))

from ainb import AINB, ChildPlug, StringSelectorPlug, TransitionPlug, Module
from ainb.node import Node
from byml import parse_byml, write_byml
import zstd_helper

ROMFS_DIR = os.path.join(ROOT_DIR, 'romfs')
MOD_ROMFS_DIR = os.path.join(ROOT_DIR, 'mods', '020-ModPlayground', 'romfs')


def build_msbt_title():
    """Build updated LayoutMsg/Title_00.msbt with label 0003: Mod Playground."""
    strings = [
        b'New Game\x00',
        b'Continue\x00',
        b'Options\x0e\x00\x00\x02\x00\x02\x00P\x00 / ZELDA NOTES Setting\x00',
        b'Mod Playground\x00'
    ]

    # Build TXT2
    txt2_body = bytearray()
    txt2_body.extend(struct.pack('<I', len(strings)))
    str_offset = 4 + len(strings) * 4
    str_data = bytearray()
    offsets = []
    for s in strings:
        offsets.append(str_offset + len(str_data))
        str_data.extend(s)
    for off in offsets:
        txt2_body.extend(struct.pack('<I', off))
    txt2_body.extend(str_data)

    txt2_sec = bytearray(b'TXT2')
    txt2_sec.extend(struct.pack('<I', len(txt2_body)))
    txt2_sec.extend(b'\x00' * 8)
    txt2_sec.extend(txt2_body)
    while len(txt2_sec) % 16 != 0:
        txt2_sec.append(0xAB)

    # Build LBL1
    # 2 slots:
    # Slot 0: 0000 (idx 0), 0002 (idx 2)
    # Slot 1: 0001 (idx 1), 0003 (idx 3)
    slots = [
        [(b'0000', 0), (b'0002', 2)],
        [(b'0001', 1), (b'0003', 3)]
    ]
    num_slots = len(slots)
    lbl1_body = bytearray()
    lbl1_body.extend(struct.pack('<I', num_slots))
    table_offset = 4 + num_slots * 8
    slot_data = bytearray()
    slot_entries = []
    for slot in slots:
        rel_off = table_offset + len(slot_data)
        slot_entries.append((len(slot), rel_off))
        for name, idx in slot:
            slot_data.append(len(name))
            slot_data.extend(name)
            slot_data.extend(struct.pack('<I', idx))
    for cnt, off in slot_entries:
        lbl1_body.extend(struct.pack('<II', cnt, off))
    lbl1_body.extend(slot_data)

    lbl1_sec = bytearray(b'LBL1')
    lbl1_sec.extend(struct.pack('<I', len(lbl1_body)))
    lbl1_sec.extend(b'\x00' * 8)
    lbl1_sec.extend(lbl1_body)
    while len(lbl1_sec) % 16 != 0:
        lbl1_sec.append(0xAB)

    # Total size includes 32-byte header
    total_file_size = 0x20 + len(lbl1_sec) + len(txt2_sec)
    # Standard MSBT Header (32 bytes):
    # 0x00: b'MsgStdBn' (8B)
    # 0x08: 0xFFFE (2B byte order)
    # 0x0A: 0x0000 (2B unknown)
    # 0x0C: 0x00 (1B UTF-8)
    # 0x0D: 0x03 (1B version 3)
    # 0x0E: 0x0002 (2B num sections)
    # 0x10: 0x0000 (2B padding)
    # 0x12: total_file_size (4B little-endian)
    # 0x16: 10 zero bytes padding to 0x20
    header = bytearray(b'MsgStdBn\xff\xfe\x00\x00\x00\x03\x02\x00\x00\x00')
    header.extend(struct.pack('<I', total_file_size))
    header.extend(b'\x00' * 10)

    return bytes(header + lbl1_sec + txt2_sec)


def step1_mals():
    print("Building USen.Product.110.sarc.zs...")
    # Base on 010-GoronMaskTransformation if present to retain Goron Mask text
    src_path = os.path.join(ROOT_DIR, 'mods', '010-GoronMaskTransformation', 'romfs', 'Mals', 'USen.Product.110.sarc.zs')
    if not os.path.exists(src_path):
        src_path = os.path.join(ROMFS_DIR, 'Mals', 'USen.Product.110.sarc.zs')

    with open(src_path, 'rb') as f:
        data = zstd_helper.decompress_zs(f.read(), 'USen.Product.110.sarc.zs', ROMFS_DIR)
    sarc = zstd_helper.unpack_sarc_to_dict(data)

    sarc['LayoutMsg/Title_00.msbt'] = build_msbt_title()

    new_sarc_data = zstd_helper.create_sarc(sarc)
    compressed = zstd_helper.compress_zs(new_sarc_data, 'USen.Product.110.sarc.zs', ROMFS_DIR)

    out_dir = os.path.join(MOD_ROMFS_DIR, 'Mals')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'USen.Product.110.sarc.zs')
    with open(out_file, 'wb') as f:
        f.write(compressed)
    print(f"  ✓ Saved {out_file} ({len(compressed)} bytes)")


def step2_open_title():
    print("Building Sequence/OpenTitle.module.ainb...")
    src_path = os.path.join(ROMFS_DIR, 'Sequence', 'OpenTitle.module.ainb')
    with open(src_path, 'rb') as f:
        a = AINB.from_binary(f.read())

    # 1. Update Node 11 (Save exists -> 4 buttons)
    for p in a.nodes[11].params.float_inputs:
        if p.name == 'Frame':
            p.default_value = 4.0

    # 2. Create Node 24: Set message for Pa_Button_03 (Save exists)
    n14_dict = copy.deepcopy(a.nodes[14]._as_dict())
    n14_dict['Node Index'] = len(a.nodes)
    n14_dict['GUID'] = str(uuid.uuid4())
    for p in n14_dict['Parameters']['Inputs']['String']:
        if p['Name'] == 'LabelName':
            p['Default Value'] = '0003'
        elif p['Name'] == 'TextID':
            p['Default Value'] = 'Pa_Button_03/T_Btn_00'
    n_btn3_msg = Node._from_dict(n14_dict, len(a.nodes))
    a.nodes.append(n_btn3_msg)
    a.nodes[10].child_plugs.append(ChildPlug._from_dict({'Name': '', 'Node Index': n_btn3_msg.index}))

    # 3. Update Node 6 (No save -> 3 buttons)
    for p in a.nodes[6].params.float_inputs:
        if p.name == 'Frame':
            p.default_value = 3.0

    # 4. Create Node 25: Set message for Pa_Button_02 (No save)
    n8_dict = copy.deepcopy(a.nodes[8]._as_dict())
    n8_dict['Node Index'] = len(a.nodes)
    n8_dict['GUID'] = str(uuid.uuid4())
    for p in n8_dict['Parameters']['Inputs']['String']:
        if p['Name'] == 'LabelName':
            p['Default Value'] = '0003'
        elif p['Name'] == 'TextID':
            p['Default Value'] = 'Pa_Button_02/T_Btn_00'
    n_btn2_nosave_msg = Node._from_dict(n8_dict, len(a.nodes))
    a.nodes.append(n_btn2_nosave_msg)
    a.nodes[5].child_plugs.append(ChildPlug._from_dict({'Name': '', 'Node Index': n_btn2_nosave_msg.index}))

    out_dir = os.path.join(MOD_ROMFS_DIR, 'Sequence')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'OpenTitle.module.ainb')
    with open(out_file, 'wb') as f:
        f.write(a.to_binary())
    print(f"  ✓ Saved {out_file} ({len(a.nodes)} nodes)")


def step3_normal_title():
    print("Building Sequence/NormalTitle.module.ainb...")
    src_path = os.path.join(ROMFS_DIR, 'Sequence', 'NormalTitle.module.ainb')
    with open(src_path, 'rb') as f:
        a = AINB.from_binary(f.read())

    # 1. Create playground_opening node (copy of Node 14)
    n14_dict = copy.deepcopy(a.nodes[14]._as_dict())
    n14_dict['Node Index'] = len(a.nodes)
    n14_dict['GUID'] = str(uuid.uuid4())
    n14_dict['Name'] = 'PlaygroundOpening.module'
    for p in n14_dict['Parameters']['Inputs']['String']:
        if p['Name'] == 'FirstBancParam':
            p['Default Value'] = 'Work/Banc/GameBancParam/ModPlayground.game__banc__GameBancParam.gyml'
            p['Flags'] = ['Uses Default']
            p['Node Index'] = -1
    n14_dict['Plugs']['String'] = []
    n14_dict['Queries'] = []
    n_pg_open = Node._from_dict(n14_dict, len(a.nodes))
    a.nodes.append(n_pg_open)

    a.modules.append(Module(path='PlaygroundOpening.module.ainb', category='Sequence', instance_count=1))

    # 2. Create seq_playground node (copy of Node 13)
    n13_dict = copy.deepcopy(a.nodes[13]._as_dict())
    n13_dict['Node Index'] = len(a.nodes)
    n13_dict['GUID'] = str(uuid.uuid4())
    n13_dict['Plugs']['Child'] = [
        {'Name': '', 'Node Index': 41},
        {'Name': '', 'Node Index': 24},
        {'Name': '', 'Node Index': 17},
        {'Name': '', 'Node Index': n_pg_open.index}
    ]
    n13_dict['Plugs']['Transition'] = [
        {'Node Index': 11, 'Transition Type': 1, 'Update Post Calc': False}
    ]
    n_seq_pg = Node._from_dict(n13_dict, len(a.nodes))
    a.nodes.append(n_seq_pg)

    # 3. Create end_playground_btn3 (copy of Node 51)
    n51_dict = copy.deepcopy(a.nodes[51]._as_dict())
    n51_dict['Node Index'] = len(a.nodes)
    n51_dict['GUID'] = str(uuid.uuid4())
    n51_dict['Plugs']['Transition'] = [
        {'Node Index': n_seq_pg.index, 'Transition Type': 1, 'Update Post Calc': True}
    ]
    n_end_pg = Node._from_dict(n51_dict, len(a.nodes))
    a.nodes.append(n_end_pg)

    # 4. Add plug to Node 1 (Element_StringSelector when save exists)
    default_plug = a.nodes[1].child_plugs.pop()
    a.nodes[1].child_plugs.append(StringSelectorPlug._from_dict({
        'Condition': 'Pa_Button_03',
        'Name': '',
        'Node Index': n_end_pg.index,
        'Is Default': False
    }))
    a.nodes[1].child_plugs.append(default_plug)

    # 5. Add plug to Node 6 (Element_StringSelector when no save exists)
    default_plug6 = a.nodes[6].child_plugs.pop()
    a.nodes[6].child_plugs.append(StringSelectorPlug._from_dict({
        'Condition': 'Pa_Button_02',
        'Name': '',
        'Node Index': n_end_pg.index,
        'Is Default': False
    }))
    a.nodes[6].child_plugs.append(default_plug6)

    out_dir = os.path.join(MOD_ROMFS_DIR, 'Sequence')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'NormalTitle.module.ainb')
    with open(out_file, 'wb') as f:
        f.write(a.to_binary())
    print(f"  ✓ Saved {out_file} ({len(a.nodes)} nodes)")


def step4_playground_opening():
    print("Building Sequence/PlaygroundOpening.module.ainb...")
    src_path = os.path.join(ROMFS_DIR, 'Sequence', 'Opening.module.ainb')
    with open(src_path, 'rb') as f:
        a = AINB.from_binary(f.read())

    a.filename = 'PlaygroundOpening.module'
    a.nodes[2].params.string_inputs[0].default_value = 'Work/Banc/GameBancParam/ModPlayground.game__banc__GameBancParam.gyml'
    a.nodes[26].name = 'PlaygroundSetPouch.module'
    for m in a.modules:
        if 'SetPouchAndStatus' in m.path:
            m.path = 'PlaygroundSetPouch.module.ainb'

    # Safe children for Node 3 (skip cutscene events)
    safe_indices = [26, 12, 13, 29, 19]
    a.nodes[3].child_plugs[:] = [p for p in a.nodes[3].child_plugs if p.node_index in safe_indices]

    out_dir = os.path.join(MOD_ROMFS_DIR, 'Sequence')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'PlaygroundOpening.module.ainb')
    with open(out_file, 'wb') as f:
        f.write(a.to_binary())
    print(f"  ✓ Saved {out_file} ({len(a.nodes)} nodes)")


def step5_playground_set_pouch():
    print("Building Sequence/PlaygroundSetPouch.module.ainb...")
    src_path = os.path.join(ROMFS_DIR, 'Sequence', 'SetPouchAndStatusForGameStart.module.ainb')
    with open(src_path, 'rb') as f:
        a = AINB.from_binary(f.read())

    a.filename = 'PlaygroundSetPouch.module'

    ALL_ITEMS = [
        # Swords
        ('Item_Sword_Kokiri', 1),
        ('Weapon_Sword_Master', 1),
        ('Item_Sword_Biggoron', 1),
        ('Item_Sword_Giant', 1),
        # Shields
        ('Weapon_Shield_Deku', 1),
        ('Weapon_Shield_Hylian', 1),
        ('Weapon_Shield_Mirror', 1),
        # Tunics / Armor
        ('Obj_Armor_Child_Normal', 1),
        ('Armor_Adult_Normal', 1),
        ('Armor_Adult_Goron', 1),
        ('Armor_Adult_Zora', 1),
        # Boots
        ('Item_HeavyBoot', 1),
        ('Item_HoverBoot', 1),
        # Masks (including MM Goron Mask)
        ('Item_Mask_Goron', 1),
        ('Item_Mask_Zora', 1),
        ('Item_Mask_Bunny', 1),
        ('Item_Mask_Keaton', 1),
        ('Item_Mask_Truth', 1),
        ('Item_Mask_Skull', 1),
        ('Item_Mask_Spooky', 1),
        ('Item_Mask_Gerudo', 1),
        # Tools & C-Items
        ('Item_Range_Bow', 1),
        ('Item_Quiver_Large', 1),
        ('Item_Arrow_Normal', 50),
        ('Item_Arrow_Fire', 1),
        ('Item_Arrow_Ice', 1),
        ('Item_Arrow_Light', 1),
        ('Item_Boomerang', 1),
        ('Item_Hookshot', 1),
        ('Item_Longshot', 1),
        ('Item_Hammer', 1),
        ('Item_BombBag_Large', 1),
        ('Item_Bomb', 40),
        ('Item_Bombchu', 50),
        ('Item_Range_SlingShot', 1),
        ('Item_BulletBag_Large', 1),
        ('Item_DekuSeed', 50),
        ('Item_NutBag_Large', 1),
        ('Item_DekuNut', 40),
        ('Item_StickBag_Large', 1),
        ('Item_DekuStick', 30),
        ('Item_DinFire', 1),
        ('Item_FaroreWind', 1),
        ('Item_NayruLove', 1),
        ('Item_GlassTruth', 1),
        ('Item_MagicBean', 10),
        ('Item_Ocarina_Time', 1),
        ('Item_FishingRod', 1),
        # Bottles with goods
        ('Item_Bottle', 1),
        ('Item_Bottle_Milk', 1),
        ('Item_Bottle_RedPotion', 1),
        ('Item_Bottle_Fairy', 1),
        # Upgrades & Key Items
        ('Item_Gauntlet_Gold', 1),
        ('Item_Scale_Gold', 1),
        ('Item_Wallet_Large', 1),
        ('Item_AgonyStone', 1),
        ('Item_Token_Gerudo', 1),
        ('Item_FairyBless_DefenseDouble', 1),
        ('Item_FairyBless_MPDouble', 1),
        # All 12 Ocarina Songs
        ('Item_Melody_ZeldaLullaby', 1),
        ('Item_Melody_EponaSong', 1),
        ('Item_Melody_SariaSong', 1),
        ('Item_Melody_SunSong', 1),
        ('Item_Melody_TimeSong', 1),
        ('Item_Melody_StormSong', 1),
        ('Item_Melody_ForestMinuet', 1),
        ('Item_Melody_FireBolero', 1),
        ('Item_Melody_WaterSerenade', 1),
        ('Item_Melody_ShadowNocturne', 1),
        ('Item_Melody_SpiritRequiem', 1),
        ('Item_Melody_LightPrelude', 1),
        # All 6 Medallions & 3 Spiritual Stones
        ('Item_Medal_Light', 1),
        ('Item_Medal_Forest', 1),
        ('Item_Medal_Fire', 1),
        ('Item_Medal_Water', 1),
        ('Item_Medal_Shadow', 1),
        ('Item_Medal_Spirit', 1),
        ('Item_Stone_Forest', 1),
        ('Item_Stone_Fire', 1),
        ('Item_Stone_Water', 1),
    ]

    # Set 20 hearts and full double-meter half-consumption magic
    a.nodes[4].params.int_inputs[1].default_value = 80   # Life (20 hearts)
    a.nodes[5].params.int_inputs[1].default_value = 80   # MaxLife (20 hearts)
    a.nodes[9].params.int_inputs[1].default_value = 64   # MP
    a.nodes[10].params.int_inputs[1].default_value = 1   # MPEconomy (1/2 cost)
    a.nodes[11].params.int_inputs[1].default_value = 64  # MPMax (double bar)

    # Clear existing initial items in Node 1
    a.nodes[1].child_plugs.clear()

    for actor_name, num in ALL_ITEMS:
        base_dict = copy.deepcopy(a.nodes[7]._as_dict())
        base_dict['Node Index'] = len(a.nodes)
        base_dict['GUID'] = str(uuid.uuid4())
        base_dict['Parameters']['Inputs']['String'][0]['Default Value'] = f'Work/Actor/{actor_name}.engine__actor__ActorParam.gyml'
        base_dict['Parameters']['Inputs']['Int'][0]['Default Value'] = num
        new_node = Node._from_dict(base_dict, len(a.nodes))
        a.nodes.append(new_node)
        a.nodes[1].child_plugs.append(ChildPlug._from_dict({'Name': '', 'Node Index': new_node.index}))

    out_dir = os.path.join(MOD_ROMFS_DIR, 'Sequence')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'PlaygroundSetPouch.module.ainb')
    with open(out_file, 'wb') as f:
        f.write(a.to_binary())
    print(f"  ✓ Saved {out_file} ({len(a.nodes)} nodes, {len(ALL_ITEMS)} items)")


def step6_mod_playground_banc():
    print("Building Banc/GameBancParam/ModPlayground.game__banc__GameBancParam.bgyml...")
    banc_param = {
        'AreaCategory': 2082234588,
        'AreaIndex': False,
        'Banc': {'Main': 'Work/Banc/Scene/DreamWorld/ModPlayground.bcett.json'},
        'BaseLocation': 'Work/Location/DreamWorld.game__location__Location.gyml',
        'DebugBootScript': '',
        'DungeonParam': 'Work/Scene/Component/DungeonParam/DreamWorld.game__scene__DungeonParam.gyml',
        'EnvEffectType': 3224227202,
        'EnvSetName': 'Dungeon',
        'HeightMapParam': {
            'BoundingBoxMax': {'X': 1117782016, 'Y': 1106247680, 'Z': 1117782016},
            'BoundingBoxMin': {'X': -1029701632, 'Y': -1054867456, 'Z': -1029701632},
            'FtxbName': 'EnemyMuseum',
            'SizePerTexel': 1048576000
        },
        'IsDreamWorldLobby': True,
        'IsEnableBakeAO': True,
        'IsEnableBakeLight': True,
        'IsEnableBakeLightResource': True,
        'IsEnableOpenEnemyInfoScreen': True,
        'IsEnableTimerCounting': False,
        'IsPlayerCameraWideMode': True,
        'LocationalLightScenePlacement': 'Work/System/GameGraphics/LocationalLightScenePlacement/EnemyMuseum.game__gfx__LocationalLightScenePlacement.gyml',
        'OptimizeChangeBancUnits': [],
        'RenderingMode': 1313265412,
        'SceneBakeType': 4224291234,
        'SceneShadowSetting': {
            'IsEnableAutoCalcSDShadowAABB': True,
            'IsEnableFadeStaticDepthShadow': True,
            'IsEnableStaticDepthShadow': True,
            'ManualStaticDepthShadowAABBMax': {'X': 1112014848, 'Y': 1112014848, 'Z': 1112014848},
            'ManualStaticDepthShadowAABBMin': {'X': -1035468800, 'Y': 0, 'Z': -1035468800},
            'UseJitteringAndBlurAtLDS': True
        },
        'SoundSceneryName': 'Mod Playground',
        'VolumeStatsForceCreate': True,
        'WorldAabbMax': {'X': False, 'Y': False, 'Z': False},
        'WorldAabbMin': {'X': False, 'Y': False, 'Z': False},
        'WorldMgrName': 'Work/WorldMgr/WorldParam/EnemyMuseum.game__wm__WorldParam.gyml'
    }

    out_dir = os.path.join(MOD_ROMFS_DIR, 'Banc', 'GameBancParam')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'ModPlayground.game__banc__GameBancParam.bgyml')
    with open(out_file, 'wb') as f:
        f.write(write_byml(banc_param))
    print(f"  ✓ Saved {out_file}")


def step7_scene_bcett():
    print("Building Banc/DreamWorld/ModPlayground_Static and _Dynamic...")
    out_dir = os.path.join(MOD_ROMFS_DIR, 'Banc', 'DreamWorld')
    os.makedirs(out_dir, exist_ok=True)

    # Static scene: Copy EnemyMuseum_Static
    src_static = os.path.join(ROMFS_DIR, 'Banc', 'DreamWorld', 'EnemyMuseum_Static.bcett.byml.zs')
    with open(src_static, 'rb') as f:
        static_raw = f.read()
    static_data = zstd_helper.decompress_zs(static_raw, 'EnemyMuseum_Static.bcett.byml.zs', ROMFS_DIR)
    static_byml = parse_byml(static_data)
    static_byml['FilePath'] = 'Work/Banc/Scene/DreamWorld/ModPlayground_Static.bcett.json'
    
    encoded_static = write_byml(static_byml)
    comp_static = zstd_helper.compress_zs(encoded_static, 'ModPlayground_Static.bcett.byml.zs', ROMFS_DIR)
    out_static = os.path.join(out_dir, 'ModPlayground_Static.bcett.byml.zs')
    with open(out_static, 'wb') as f:
        f.write(comp_static)
    print(f"  ✓ Saved {out_static} ({len(comp_static)} bytes)")

    # Dynamic scene: StartPos for ModPlayground + Master Sword Pedestal to switch age
    src_dyn = os.path.join(ROMFS_DIR, 'Banc', 'DreamWorld', 'EnemyMuseum_Dynamic.bcett.byml.zs')
    with open(src_dyn, 'rb') as f:
        dyn_raw = f.read()
    dyn_data = zstd_helper.decompress_zs(dyn_raw, 'EnemyMuseum_Dynamic.bcett.byml.zs', ROMFS_DIR)
    dyn_byml = parse_byml(dyn_data)
    dyn_byml['FilePath'] = 'Work/Banc/Scene/DreamWorld/ModPlayground_Dynamic.bcett.json'

    # Update StartPos
    for a in dyn_byml.get('Actors', []):
        if a.get('Gyaml') == 'StartPos':
            a['Name'] = 'ModPlayground'
            a['Translate'] = [0, 0, 0]

    # Add Time Temple Master Sword pedestal in the playground so Link can switch age
    pedestal_base = {
        'Bakeable': True,
        'Gyaml': 'Obj_TimeTemple_MasterSwordBase_A_01',
        'Hash': 3105928172,
        'Name': 'Playground_MasterSwordBase',
        'Phive': {'Placement': {'ID': 1492019482}},
        'Rotate': [0, 0, 0],
        'SRTHash': 2149581938,
        'Translate': [0, 0, 1084227584]  # 5 units in front of player
    }
    master_sword = {
        'Bakeable': True,
        'Gyaml': 'Obj_TimeTemple_MasterSword_A_01',
        'Hash': 3918274190,
        'Name': 'Playground_MasterSword',
        'Phive': {'Placement': {'ID': 2019481923}},
        'Rotate': [0, 0, 0],
        'SRTHash': 3918274190,
        'Translate': [0, 0, 1084227584]
    }
    dyn_byml['Actors'].append(pedestal_base)
    dyn_byml['Actors'].append(master_sword)

    encoded_dyn = write_byml(dyn_byml)
    comp_dyn = zstd_helper.compress_zs(encoded_dyn, 'ModPlayground_Dynamic.bcett.byml.zs', ROMFS_DIR)
    out_dyn = os.path.join(out_dir, 'ModPlayground_Dynamic.bcett.byml.zs')
    with open(out_dyn, 'wb') as f:
        f.write(comp_dyn)
    print(f"  ✓ Saved {out_dyn} ({len(comp_dyn)} bytes)")


def step8_start_pos():
    print("Building Banc/BancBasedResource/StartPos.byml.zs...")
    src_path = os.path.join(ROMFS_DIR, 'Banc', 'BancBasedResource', 'StartPos.byml.zs')
    with open(src_path, 'rb') as f:
        raw = f.read()
    data = zstd_helper.decompress_zs(raw, 'StartPos.byml.zs', ROMFS_DIR)
    sp = parse_byml(data)

    sp['ModPlayground'] = {
        'ModPlayground': {
            'Rot': [-1068953638, 1057360530, -1068953638],
            'Trans': [0, 0, 0]
        }
    }

    encoded = write_byml(sp)
    comp = zstd_helper.compress_zs(encoded, 'StartPos.byml.zs', ROMFS_DIR)

    out_dir = os.path.join(MOD_ROMFS_DIR, 'Banc', 'BancBasedResource')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'StartPos.byml.zs')
    with open(out_file, 'wb') as f:
        f.write(comp)
    print(f"  ✓ Saved {out_file} ({len(comp)} bytes)")


def main():
    print("=== Generating Mod Playground Assets ===")
    step1_mals()
    step2_open_title()
    step3_normal_title()
    step4_playground_opening()
    step5_playground_set_pouch()
    step6_mod_playground_banc()
    step7_scene_bcett()
    print("=== Mod Playground Assets Generated Successfully! ===")


if __name__ == '__main__':
    main()
