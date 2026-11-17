# build_ct_env.py: Clock Town's own environment (world parameters, time table, palettes) (RESEARCH_NOTES.md §80t).
#   python3 build_ct_env.py [mod folder name]       (after build_dev.py)
# The map's lighting: GameBancParam.WorldMgrName -> WorldMgr/WorldParam/<x> (sun azimuth / elevation, cloud shadows,
# BasePalette_Bluesky = a time table in Pack/EnvPalette.pack -> one ResEnvPalette per time of day). The palettes
# hold the ambient: AmbientIntensity, HemiLightIntensity (hemisphere light), BgDifColor / BgDifIntensity,
# SkyLightAdhocScale / SkyLightBounceScale, DirectionalLightBounceScale, ExposureShadowCorrect (the exposure of
# shadowed areas), ... each palette inherits from $parent (MainField_* -> Parent_Field -> Default).
# Here: a WorldParam "ClockTownDev" (a copy of WORLD_FROM's, cloud shadows as CT_CLOUD_SHADOW), a time table
# "ClockTown_Bluesky" and one palette per time of day: copies of the donor's with the knobs below applied to the
# resolved values (CT_AMBIENT, CT_HEMI, CT_BGDIF, CT_SKYLIGHT, CT_BOUNCE are multipliers; CT_SHADOW_EXPOSURE is
# added to ExposureShadowCorrect; CT_BGDIF_COLOR=r,g,b sets the colour).
import copy, os, struct, sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from rf import rf, ROMFS
from mod_manager.byml import parse_byml, write_byml
from mod_manager.zstd_helper import compress_zs, unpack_sarc_to_dict, sarc_hash

GAME = os.path.expanduser(os.environ.get('HOOKSHOT_GAME_DIR', '~/Games/Hookshot'))
OUT = os.path.join(GAME, 'mods', sys.argv[1] if len(sys.argv) > 1 else '_070-ClockTownDev', 'romfs')
BANC = os.environ.get('DEV_BANC', 'ClockTownDev')
WORLD_FROM = os.environ.get('CT_WORLD_FROM', 'KakarikoVillage_Child')
f = lambda k, d: float(os.environ.get(k, d))
KNOBS = dict(AmbientIntensity=f('CT_AMBIENT', '1'), HemiLightIntensity=f('CT_HEMI', '1'), BgDifIntensity=f('CT_BGDIF', '1'),
             SkyLightAdhocScale=f('CT_SKYLIGHT', '1'), SkyLightBounceScale=f('CT_SKYLIGHT', '1'),
             DirectionalLightBounceScale=f('CT_BOUNCE', '1'))
SHADOW_EXPOSURE = f('CT_SHADOW_EXPOSURE', '0')
BGDIF_COLOR = os.environ.get('CT_BGDIF_COLOR')

pack = rf('Pack/EnvPalette.pack.zs')
files = unpack_sarc_to_dict(pack)
def work_to_rel(w):   # Work/WorldMgr/X/Y.game__wm__Z.gyml -> WorldMgr/X/Y.game__wm__Z.bgyml
    return w[len('Work/'):].replace('.gyml', '.bgyml')
def resolved(rel):
    y = parse_byml(files[rel])
    if '$parent' in y:
        base = resolved(work_to_rel(y['$parent']))
        base.update({k: v for k, v in y.items() if k != '$parent'})
        return base
    return dict(y)

world = parse_byml(open(os.path.join(ROMFS, f'WorldMgr/WorldParam/{WORLD_FROM}.game__wm__WorldParam.bgyml'), 'rb').read())
world['CloudShadowOnOff'] = os.environ.get('CT_CLOUD_SHADOW', '0') == '1'
table_rel = work_to_rel(world['BasePalette_Bluesky'])
table = parse_byml(files[table_rel])
new_files = {}
for i, entry in enumerate(table['TimeTableArray']):
    src = work_to_rel(entry['EnvPalette'])
    pal = parse_byml(files[src])                     # (keeps its $parent; the knobs are written resolved)
    full = resolved(src)
    for k, m in KNOBS.items():
        if m != 1 and k in full: pal[k] = float(full[k]) * m
        elif m != 1 and k == 'AmbientIntensity': pal[k] = 1.0 * m
    if SHADOW_EXPOSURE:
        pal['ExposureShadowCorrect'] = float(full.get('ExposureShadowCorrect', 0.0)) + SHADOW_EXPOSURE
    if BGDIF_COLOR:
        r, g, b = (float(v) for v in BGDIF_COLOR.split(','))
        pal['BgDifColor'] = {'A': 0.0, 'B': b, 'G': g, 'R': r}
    name = f"ClockTown_Bluesky_{i}_{entry['Name']}"
    rel = f'WorldMgr/ResEnvPalette/{name}.game__wm__ResEnvPalette.bgyml'
    new_files[rel] = write_byml(pal)
    entry['EnvPalette'] = 'Work/' + rel.replace('.bgyml', '.gyml')
    print(f"  {entry['Name']:9s} ({entry.get('Hour')}h) from {os.path.basename(src)[:40]}: " +
          ', '.join(f'{k} {pal[k]:.3g}' for k in list(KNOBS) + ['ExposureShadowCorrect'] if k in pal and isinstance(pal[k], float)))
table['Name'] = 'ClockTown_Bluesky'
trel = 'WorldMgr/ResEnvPaletteTimeTable/ClockTown_Bluesky.game__wm__ResEnvPaletteTimeTable.bgyml'
new_files[trel] = write_byml(table)
world['BasePalette_Bluesky'] = 'Work/' + trel.replace('.bgyml', '.gyml')
world['Name'] = 'ClockTownDev'


def sarc_add_many(d, add):
    dstart = struct.unpack_from('<I', d, 0xc)[0]
    n = struct.unpack_from('<H', d, 0x1a)[0]
    nodes = [list(struct.unpack_from('<IIII', d, 0x20 + 16 * i)) for i in range(n)]
    strs = bytearray(d[0x20 + 16 * n + 8:dstart]).rstrip(b'\0') + b'\0'
    strs += b'\0' * (-len(strs) & 3)
    data = bytearray(d[dstart:])
    have = {x[0] for x in nodes}
    for name, blob in add.items():
        h = sarc_hash(name)
        assert h not in have, name
        off = len(strs); strs += name.encode() + b'\0'; strs += b'\0' * (-len(strs) & 3)
        data += b'\0' * (-len(data) & 7)
        nodes.append([h, 0x01000000 | (off // 4), len(data), len(data) + len(blob)]); data += blob
    nodes.sort(key=lambda x: x[0])
    head = 0x20 + 16 * len(nodes) + 8 + len(strs)
    new_start = dstart + max(0, (head - dstart + 15) // 16 * 16)
    out = bytearray(d[:0x14]) + struct.pack('<4sHHI', b'SFAT', 0x0C, len(nodes), struct.unpack_from('<I', d, 0x1c)[0])
    for x in nodes: out += struct.pack('<IIII', *x)
    out += struct.pack('<4sHH', b'SFNT', 8, 0) + strs
    out += b'\0' * (new_start - len(out)) + data
    struct.pack_into('<II', out, 8, len(out), new_start)
    return bytes(out)


newpack = sarc_add_many(pack, new_files)
check = unpack_sarc_to_dict(newpack)
assert all(check[k] == v for k, v in files.items()) and all(check[k] == v for k, v in new_files.items())
def put(rel, data, zs=False):
    p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').write(compress_zs(data, rel, ROMFS) if zs else data)
put('Pack/EnvPalette.pack.zs', newpack, zs=True)
put(f'WorldMgr/WorldParam/{BANC}.game__wm__WorldParam.bgyml', write_byml(world))
p = os.path.join(OUT, f'Banc/GameBancParam/{BANC}.game__banc__GameBancParam.bgyml')
gbp = parse_byml(open(p, 'rb').read())
gbp['WorldMgrName'] = f'Work/WorldMgr/WorldParam/{BANC}.game__wm__WorldParam.gyml'
open(p, 'wb').write(write_byml(gbp))
print(f'EnvPalette.pack {len(pack)} -> {len(newpack)} (+{len(new_files)} files); WorldParam {BANC} (cloud shadows {world["CloudShadowOnOff"]})')
