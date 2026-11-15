# build_dev.py: experiment E1, a new map "ClockTownDev" (development only, written into the game folder's mods/).
# The map holds one borrowed ground piece and a start position; the Prelude of Light's warp is pointed at it.
import copy, json, os, struct, sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from rf import rf, ROMFS
from mod_manager.byml import parse_byml, write_byml, U64
from mod_manager.zstd_helper import compress_zs
from mod_manager import rstb as rstbmod

NAME = os.environ.get('DEV_BANC', 'ClockTownDev')          # 12 characters, as "Market_Child" (flow string hijack)
assert len(NAME) == 12
GAME = os.path.expanduser(os.environ.get('HOOKSHOT_GAME_DIR', '~/Games/Hookshot'))
OUT = os.path.join(GAME, 'mods', os.environ.get('DEV_DIR', '_070-ClockTownDev'))
SCENE = f'Work/Banc/Scene/{NAME}/{NAME}.bcett.json'
files = {}          # romfs path (as stored, with .zs) -> decompressed bytes

def put(rel, data): files[rel] = data

# ---- GameBancParam (from Market_Child)
GBP_FROM = os.environ.get('DEV_GBP', 'SageRoom')        # (E9) whose map parameters the dev map copies
gbp = parse_byml(open(os.path.join(ROMFS, f'Banc/GameBancParam/{GBP_FROM}.game__banc__GameBancParam.bgyml'), 'rb').read())
gbp['Banc'] = {'Main': SCENE}
for k in ['OptimizeChangeBancUnits', 'FoliageScene', 'NpcIgnoreFootIKAreaInfoArray'] + os.environ.get('DEV_DROP', '').split(',') + [
          'OverwriteWaterFogParam', 'SariaSongPlaceMessageChild']:
    gbp.pop(k, None)
BAKE = os.environ.get('DEV_BAKE', '1') == '1'     # (E4) borrow Market_Child's bake files under the new name
for k in ([] if BAKE else ['IsEnableBakeAO', 'IsEnableBakeLight', 'IsEnableBakeLightResource']) + ['UseNpcIgnoreFootIKArea',
          'IsRaffleRichardAppearPos', 'IsGenerateAutogenGameData']:
    if k in gbp: gbp[k] = False
# (lighting) single keys copied from another map's parameters: DEV_SET_FROM=<map>, DEV_SET_KEYS=a,b,...
if os.environ.get('DEV_SET_FROM'):
    other = parse_byml(open(os.path.join(ROMFS, f"Banc/GameBancParam/{os.environ['DEV_SET_FROM']}.game__banc__GameBancParam.bgyml"), 'rb').read())
    for k in os.environ.get('DEV_SET_KEYS', '').split(','):
        if k in other: gbp[k] = other[k]; print('  set', k, '<-', os.environ['DEV_SET_FROM'])
# (lighting) static depth shadows only from Clock Town itself (the moon above must not shade the town)
if os.environ.get('DEV_SHADOW_BOX') and 'SceneShadowSetting' in gbp:
    x0, y0, z0, x1, y1, z1 = [float(v) for v in os.environ['DEV_SHADOW_BOX'].split(',')]
    sss = gbp['SceneShadowSetting']
    sss['ManualStaticDepthShadowAABBMin'] = {'X': x0, 'Y': y0, 'Z': z0}
    sss['ManualStaticDepthShadowAABBMax'] = {'X': x1, 'Y': y1, 'Z': z1}
    print('  static shadow box', sss['ManualStaticDepthShadowAABBMin'], sss['ManualStaticDepthShadowAABBMax'])
put(f'Banc/GameBancParam/{NAME}.game__banc__GameBancParam.bgyml', write_byml(gbp))

# ---- placement: the ground piece and the start position, as Market places them
mst = parse_byml(rf('Banc/Market/Market_Child_Static.bcett.byml.zs'))
ground = [a for a in mst['Actors'] if a.get('Gyaml') == 'Land_HrlCity_Ground_A_01'][0]
start = copy.deepcopy([a for a in mst['Actors'] if a.get('Gyaml') == 'StartPos'][0])
START_T = [float(x) for x in os.environ['DEV_START'].split(',')] if os.environ.get('DEV_START') else [0.0, 101.0, 0.0] if os.environ.get('DEV_SAGE_BCETT') == '1' and os.environ.get('DEV_ONLY_BASE') != '1' and os.environ.get('DEV_LIGHTS') != '1' else [-8.0, 101.0, 1.0]
start['Name'] = 'LightPrelude'
start['Translate'] = START_T
start['Rotate'] = [0.0, float(os.environ.get('DEV_START_ROTY', '0')), 0.0]
start['Hash'] = start['Phive']['Placement']['ID'] = 0x4D4D0001
start['SRTHash'] = 0x4D4D0001
ground = copy.deepcopy(ground)
ground['Translate'] = [0.0, 100.0, 0.0]; ground['Rotate'] = [0.0, 0.0, 0.0]
ground['Gyaml'] = os.environ.get('CT_ACTOR', 'Land_SageRoom_Base_A_01')
SAGE_ID = 4508210412473462298            # Land_SageRoom_Base_A_01's placement: instance 0 of the compound template
ground['Hash'] = U64(SAGE_ID); ground['Phive'] = {'Placement': {'ID': U64(SAGE_ID)}}
pots = []
for k, pot in enumerate([a for a in mst['Actors'] if a.get('Gyaml') == 'Obj_Pot_A_01'][:3]):
    pot = copy.deepcopy(pot)
    pot['Translate'] = [START_T[0] + 1.5 * (k + 1), START_T[1] + 0.5, START_T[2] + 2.0]
    pots.append(pot)
static = {'Actors': [ground, start] + (pots if os.environ.get('DEV_POTS', '1') == '1' else []), 'FilePath': SCENE}
DEV_EXTRA_STATIC = []
if os.environ.get('DEV_SAGE_BCETT') == '1':                  # (E10) the Sage room's own placement files
    sst = parse_byml(rf('Banc/SageRoom/SageRoom_Static.bcett.byml.zs'))
    sdy = parse_byml(rf('Banc/SageRoom/SageRoom_Dynamic.bcett.byml.zs'))
    sst['FilePath'] = sdy['FilePath'] = SCENE
    if os.environ.get('DEV_LIGHTS') == '1':                   # Base_A_01 (Clock Town) + the SageRoom's light / effect actors
        keep = lambda a: a['Gyaml'] == 'Land_SageRoom_Base_A_01' or a['Gyaml'].startswith('EftLoc_')
        sst['Actors'] = [a for a in sst['Actors'] if keep(a)]
        sdy['Actors'] = [a for a in sdy['Actors'] if keep(a)]
        sst['AiGroups'] = []
    if os.environ.get('DEV_BGM_ACTOR'):                       # (music) a BGM actor on Saria's placement entry
        saria = [a for a in sst['Actors'] + sdy['Actors'] if a['Gyaml'] == 'Npc_Kokiri_Saria_Child'][0]
        bgm = [a for a in mst['Actors'] if a.get('Gyaml') == os.environ['DEV_BGM_ACTOR']]
        bgm = copy.deepcopy(bgm[0]) if bgm else {'Gyaml': os.environ['DEV_BGM_ACTOR']}
        for k in ('Links', 'Rails', 'Rotate', 'Presence'): bgm.pop(k, None)
        for k in ('Hash', 'Phive', 'SRTHash', 'Translate'): bgm[k] = copy.deepcopy(saria[k])
        DEV_EXTRA_STATIC.append(bgm)
        print('  BGM actor', bgm['Gyaml'], 'on Saria\'s placement')
    if os.environ.get('DEV_ONLY_BASE') == '1':                # keep only Land_SageRoom_Base_A_01 (it carries Clock Town)
        sst['Actors'] = [a for a in sst['Actors'] if a['Gyaml'] == 'Land_SageRoom_Base_A_01'] + DEV_EXTRA_STATIC
        sdy['Actors'] = []
        sst['AiGroups'] = []
    sdy['Actors'].append(start)
    put(f'Banc/{NAME}/{NAME}_Static.bcett.byml.zs', write_byml(sst))
    put(f'Banc/{NAME}/{NAME}_Dynamic.bcett.byml.zs', write_byml(sdy))
else:
    static['PhiveHeader'] = {'PlacementGroups': []}
    put(f'Banc/{NAME}/{NAME}_Static.bcett.byml.zs', write_byml(static))
    put(f'Banc/{NAME}/{NAME}_Dynamic.bcett.byml.zs', write_byml({'Actors': [], 'FilePath': SCENE}))

# ---- tables
info = parse_byml(rf('RSDB/GameBancInfo.Product.111.rstbl.byml.zs'))
row = copy.deepcopy([r for r in info if r['__RowId'] == 'Market_Child'][0])
row['BancMain'] = SCENE
row['__RowId'] = NAME
info = sorted([r for r in info if r['__RowId'] != NAME] + [row], key=lambda r: r['__RowId'])
put('RSDB/GameBancInfo.Product.111.rstbl.byml.zs', write_byml(info))

sp = parse_byml(rf('Banc/BancBasedResource/StartPos.byml.zs'))
ROTY = float(os.environ.get('DEV_START_ROTY', '0'))
sp[NAME] = {'LightPrelude': {'Rot': [0.0, ROTY, 0.0], 'Trans': START_T}, '': {'Rot': [0.0, ROTY, 0.0], 'Trans': START_T}}
put('Banc/BancBasedResource/StartPos.byml.zs', write_byml(dict(sorted(sp.items()))))

# ---- the Prelude of Light's warp (child) goes to the new map: same-length string in the flow's string pool
fl = bytearray(rf('Event/EventFlow/Dm_SY_Ocarina.bfevfl.zs'))
n = fl.count(b'Market_Child\0')
assert n == 1, n
i = fl.index(b'Market_Child\0')
fl[i:i + 12] = NAME.encode()
# (dev) Event14's case 0 -> the Prelude of Light's warp chain (event 206), so the added songs (MajoraMod copies case 0
# to case 13) warp too
pat = struct.pack('<IHH', 0, 98, 0) + struct.pack('<IHH', 1, 100, 0)
assert fl.count(pat) == 1, fl.count(pat)
if os.environ.get('DEV_LULLABY_WARP') == '1':          # (old shortcut: every added song + Zelda's Lullaby warp)
    j = fl.index(pat)
    struct.pack_into('<H', fl, j + 4, 206)
put('Event/EventFlow/Dm_SY_Ocarina.bfevfl.zs', bytes(fl))

# ---- write, with resource size table entries for new / grown files
table_rel = 'System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs'

import shutil
shutil.rmtree(OUT, ignore_errors=True)
for rel, data in files.items():
    p = os.path.join(OUT, 'romfs', rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').write(compress_zs(data, rel, ROMFS) if rel.endswith('.zs') else data)
json.dump({'name': 'ClockTownDev', 'author': 'dev', 'version': '0.0.1', 'description': 'development test map'},
          open(os.path.join(OUT, 'info.json'), 'w'))
print('files:', *files, sep='\n  ')

# ---- (E1d) the map's static compound body: Market_Child's, copied as is (the ground keeps Market's placement ID)
if os.environ.get('DEV_COMPOUND', '1') == '1':
    rel = f'Phive/StaticCompoundBody/{NAME}/{NAME}.Nin_Ounce_NVN2.bphsc.zs'
    dst = os.path.join(OUT, 'romfs', rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, 'wb').write(compress_zs(open(os.path.join(os.path.dirname(__file__), '..', 'ct.bphsc'), 'rb').read(), rel, ROMFS))
    print('  + compound body (SageRoom template, Clock Town mesh in layer 0)')

if BAKE:
    import glob
    BAKE_FROM = os.environ.get('DEV_BAKE_FROM', 'Market_Child')
    for src, dst in [(f, f.replace(BAKE_FROM, NAME)) for f in
                     glob.glob(os.path.join(ROMFS, f'Bake/Scene/{BAKE_FROM}_G_*')) +
                     [os.path.join(ROMFS, f'Bake/Light/{BAKE_FROM}.bklight.zs'),
                      os.path.join(ROMFS, f'Game/BakeSceneLayerConfig/{BAKE_FROM}.game__bake__SceneLayerConfig.bgyml')]]:
        dst = os.path.join(OUT, 'romfs', os.path.relpath(dst, ROMFS))
        os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(src, dst)
    print(f'  + bake files (copies of {BAKE_FROM})')

# ---- light probes (Bake/Probe/Scene/<banc>/: the ambient light for characters): another map's, copied
if os.environ.get('DEV_PROBE_FROM'):
    src_dir = os.path.join(ROMFS, 'Bake/Probe/Scene', os.environ['DEV_PROBE_FROM'])
    dst_dir = os.path.join(OUT, 'romfs', 'Bake/Probe/Scene', NAME)
    os.makedirs(dst_dir, exist_ok=True)
    for f in os.listdir(src_dir): shutil.copy(os.path.join(src_dir, f), os.path.join(dst_dir, f))
    print('  + light probes (copies of', os.environ['DEV_PROBE_FROM'] + ')', len(os.listdir(dst_dir)), 'files')
