# bake_scene.py: Clock Town's own scene bake (RESEARCH_NOTES.md §80s).   python3 bake_scene.py [mod folder name]
# (after build_ct_dev.py, which writes the model's bake UVs and ct_bake_uv.npz)
# The game's static lighting: each model's `bake` UV points into the map's bake atlases (Bake/Scene/<banc>_G*.bkres
# binds placement Guid + material -> texture, offset, scale):
#   d0_t0 (BC5): R ambient occlusion, G a mask (0 almost everywhere);
#   d1_shgi0_uf3 (BC6H, 3 layers) + d1_shgi0_un3 (BC7, 3 layers): three mono-directional SH terms, layer i =
#   colour (HDR) and direction (world space, L1 order y z x -> stored R G B, 128 = 0): 0 bounced (sun) light,
#   1 sky visibility (grey, up to ~1.14), 2 a small ground-bounce term.
# Here every lightmap texel is ray traced against Clock Town's own triangles: cosine-weighted hemisphere rays;
# escaping rays -> sky visibility and its mean direction; hits on lit (sky-facing, unshadowed) surfaces -> bounced
# light (albedo of the hit material) and its direction. Scales are calibrated on Kakariko's bake.
import os, struct, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from rf import rf, ROMFS
from mod_manager.byml import parse_byml, write_byml
from mod_manager.zstd_helper import compress_zs, unpack_sarc_to_dict, create_sarc
from mod_manager.mapimport import lightmap, bcenc
from bake_rd import Tex

GAME = os.path.expanduser(os.environ.get('HOOKSHOT_GAME_DIR', '~/Games/Hookshot'))
OUT = os.path.join(GAME, 'mods', sys.argv[1] if len(sys.argv) > 1 else '_070-ClockTownDev', 'romfs')
BANC = os.environ.get('DEV_BANC', 'ClockTownDev')
GUID = os.environ.get('BAKE_GUID', '4508210412473462298_0')       # (Land_SageRoom_Base_A_01's placement)
MODEL_NAME = 'Land_SageRoom_Base_A_01'
MODEL_AT = np.array([0.0, 100.0, 0.0])
TEMPLATE = 'KakarikoVillage_Child_G1_0_0'
NEWBASE = 'ClockTownDev_MajoraBake_G0_0'                            # (same length as TEMPLATE: names patched in place)
RAYS = int(os.environ.get('BAKE_RAYS', '64'))
HERE = os.path.dirname(__file__)
assert len(NEWBASE) == len(TEMPLATE)

# ---- calibration (Kakariko's bake, bake_truth.py): typical magnitudes and the bounced light's colour
SKY_MAX = 1.137
L0_COLOUR = np.array([1.0, 0.54, 0.224])        # (Kakariko's mean bounced colour, R = 1)
L0_SCALE = float(os.environ.get('BAKE_L0', '60'))
L2_VALUE = np.array([0.022, 0.014, 0.008])
AO_FLOOR = float(os.environ.get('BAKE_AO_FLOOR', '0.35'))

t0 = time.time()
R = np.load(os.path.join(HERE, '..', 'ct_bake_uv.npz'))
AW, AH = R['atlas']; TEXN = int(R['tex'])
n = int(R['count'][0])
lin = lambda v: ((v / 255 + 0.055) / 1.055) ** 2.4
# ---- triangles (world space) with their material albedo
TP, TN, TA = [], [], []
for k in range(n):
    P = R[f'm{k}_pos'] + MODEL_AT; T = R[f'm{k}_tris']
    tri = P[T]
    TP.append(tri)
    TA.append(np.full(len(T), lin(R[f'm{k}_albedo']).mean()))
TP = np.concatenate(TP); TA = np.concatenate(TA)
A0, E1, E2 = TP[:, 0], TP[:, 1] - TP[:, 0], TP[:, 2] - TP[:, 0]
FN = np.cross(E1, E2); FN /= np.maximum(np.linalg.norm(FN, axis=1, keepdims=True), 1e-12)
print(f'{len(TP)} triangles, atlas {AW}x{AH}')


def cast(o, dirs):
    """nearest hit per ray -> (triangle index or -1)"""
    p = np.cross(dirs[:, None, :], E2[None])
    det = np.einsum('tk,rtk->rt', E1, p)
    ok = np.abs(det) > 1e-9
    inv = np.where(ok, 1.0 / np.where(ok, det, 1), 0)
    s = o - A0
    u = np.einsum('tk,rtk->rt', s, p) * inv
    q = np.cross(s, E1)
    v = np.einsum('rk,tk->rt', dirs, q) * inv
    t = np.einsum('tk,tk->t', E2, q)[None] * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 2e-3)
    tt = np.where(hit, t, np.inf)
    k = tt.argmin(1)
    return np.where(np.isfinite(tt[np.arange(len(dirs)), k]), k, -1)


# ---- which triangles are lit (facing the sun, unshadowed): the bounce sources
SUN = np.array([float(v) for v in os.environ.get('BAKE_SUN', '-0.4,0.75,0.5').split(',')]); SUN /= np.linalg.norm(SUN)
SKY_BOUNCE = float(os.environ.get('BAKE_SKY_BOUNCE', '0.15'))   # (sky light reflected by any surface, relative)
cen = TP.mean(1) + FN * 0.02
LITC = os.path.join(HERE, '..', f"bake_lit_{'_'.join(f'{v:.3f}' for v in SUN)}.npy")
if os.path.exists(LITC):
    sun_lit = np.load(LITC)
else:
    sun_lit = np.zeros(len(TP))
    for i in range(len(TP)):
        c = FN[i] @ SUN
        if c > 0 and cast(cen[i], SUN[None])[0] < 0: sun_lit[i] = c
    np.save(LITC, sun_lit)
lit = sun_lit + SKY_BOUNCE * (0.5 + 0.5 * FN[:, 1])
print(f'sun-lit triangles {np.mean(sun_lit > 0):.0%} ({time.time() - t0:.0f}s)')

# ---- the texels
tex = {}
for k in range(n):
    P = R[f'm{k}_pos'] + MODEL_AT; N = R[f'm{k}_nrm']; UV = R[f'm{k}_texel']; T = R[f'm{k}_tris']
    tex.update(lightmap.rasterize(P, N, UV, T, (AW, AH)))
print(f'{len(tex)} texels ({time.time() - t0:.0f}s)')

rng = np.random.default_rng(3)
u1, u2 = rng.random(RAYS), rng.random(RAYS)
r = np.sqrt(u1)
LOCAL = np.stack([r * np.cos(2 * np.pi * u2), r * np.sin(2 * np.pi * u2), np.sqrt(1 - u1)], 1)   # (z = normal)
CACHE = os.path.join(HERE, '..', 'bake_scene_hits.npz')
keys = sorted(tex)
P_ = np.array([tex[k][0] for k in keys]); N_ = np.array([tex[k][1] for k in keys])
ax = np.where(np.abs(N_[:, :1]) < 0.9, np.array([[1.0, 0, 0]]), np.array([[0, 1.0, 0]]))
T1 = np.cross(N_, ax); T1 /= np.linalg.norm(T1, axis=1, keepdims=True); T2 = np.cross(N_, T1)
def dirs_of(i):
    return LOCAL[:, :1] * T1[i] + LOCAL[:, 1:2] * T2[i] + LOCAL[:, 2:] * N_[i]
if os.path.exists(CACHE) and len(np.load(CACHE)['hits']) == len(keys):
    HITS = np.load(CACHE)['hits']
    print('ray hits from the cache')
else:
    def bake_range(rng_):
        return [cast(P_[i] + N_[i] * 0.03, dirs_of(i)).astype(np.int32) for i in rng_]
    import multiprocessing as mp
    chunks = [range(i, min(i + 2000, len(keys))) for i in range(0, len(keys), 2000)]
    with mp.get_context('fork').Pool(int(os.environ.get('BAKE_PROCS', '14'))) as pool:
        HITS = np.array([h for part in pool.map(bake_range, chunks) for h in part])
    np.savez(CACHE, hits=HITS)
DIRS = LOCAL[None, :, :1] * T1[:, None] + LOCAL[None, :, 1:2] * T2[:, None] + LOCAL[None, :, 2:] * N_[:, None]   # (k,r,3)
esc = HITS < 0
VIS = esc.mean(1)
VDIR = (DIRS * esc[..., None]).sum(1) / np.maximum(esc.sum(1), 1)[:, None]
hk = np.where(esc, 0, HITS)
W = np.where(esc, 0, TA[hk] * lit[hk] * np.einsum('krc,krc->kr', FN[hk], -DIRS).clip(0))
BNC = W.sum(1) / RAYS
BDIR = (DIRS * W[..., None]).sum(1) / np.maximum(W.sum(1), 1e-9)[:, None]
print(f'rays done ({time.time() - t0:.0f}s): sky visibility mean {VIS.mean():.2f}, bounce mean {BNC.mean():.3f}')

# ---- images over the atlas region (texels outside the charts: dilated from their neighbours)
H4, W4 = (AH + 7) // 4 * 4, (AW + 7) // 4 * 4
uf = np.zeros((3, H4, W4, 3), np.float32); un = np.full((3, H4, W4, 3), 127.5, np.float32); ao = np.zeros((H4, W4))
have = np.zeros((H4, W4), bool)
def enc_dir(d):                                    # world xyz -> stored (y, z, x) at 127.5 + 127.5 d
    return 127.5 + 127.5 * np.clip(np.array([d[1], d[2], d[0]]), -1, 1)
for i, (x, y) in enumerate(keys):
    v = VIS[i]
    uf[1, y, x] = SKY_MAX * v
    un[1, y, x] = enc_dir(VDIR[i])
    uf[0, y, x] = L0_SCALE * BNC[i] * L0_COLOUR
    un[0, y, x] = enc_dir(BDIR[i] * 0.6)
    uf[2, y, x] = L2_VALUE * (0.5 + v)
    un[2, y, x] = enc_dir(-0.28 * tex[(x, y)][1])
    ao[y, x] = AO_FLOOR + (1 - AO_FLOOR) * v
    have[y, x] = True
for _ in range(4):                                 # dilation into the padding
    for arr in (uf, un):
        for L in range(3):
            a = arr[L]
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                src = np.roll(have, (dy, dx), (0, 1)) & ~have
                a[src] = np.roll(a, (dy, dx), (0, 1))[src]
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        src = np.roll(have, (dy, dx), (0, 1)) & ~have
        ao[src] = np.roll(ao, (dy, dx), (0, 1))[src]
        have |= src
print(f'bounced light mean {uf[0][have].mean(0).round(3)}, sky visibility mean {uf[1][have].mean():.3f}')


# ---- textures: the template's, our region written, names patched
def write_layer(t, layer, bx0, by0, blocks):
    base = t.mip_offsets[0]
    t.mip_offsets[0] = base + layer * t.layer_size
    try:
        full = t.read_blocks(0)
        full[by0:by0 + blocks.shape[0], bx0:bx0 + blocks.shape[1]] = blocks
        t.write_blocks(0, full)
    finally:
        t.mip_offsets[0] = base


def save_tex(kind, fill):
    old = f'RBake_Scene_{TEMPLATE}_{kind}'
    new = f'RBake_Scene_{NEWBASE}_{kind}'
    t = Tex(rf(f'Tex/{old}.bntx.zs'))
    fill(t)
    d = t.to_bytes().replace(old.encode(), new.encode())
    rel = f'Tex/{new}.bntx.zs'
    p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').write(compress_zs(d, rel, ROMFS))
    print('  +', rel, t.width, t.height, t.array)
    return new

def fill_uf(t):
    for L in range(3): write_layer(t, L, 0, 0, bcenc.bc6h_mode11(uf[L]))
def fill_un(t):
    for L in range(3): write_layer(t, L, 0, 0, bcenc.bc7_mode6(np.clip(np.round(un[L]), 0, 255).astype(np.uint8)))
def fill_d0(t):
    a2 = np.repeat(np.repeat(ao, 2, 0), 2, 1)            # (twice the resolution of d1)
    img = np.stack([np.clip(a2 * 255, 0, 255), np.full_like(a2, float(os.environ.get('BAKE_D0G', '255')))], -1).astype(np.uint8)   # (G: a sun mask, 0 = never sunlit)
    write_layer(t, 0, 0, 0, bcenc.bc5(img))

n_uf = save_tex('d1_shgi0_uf3', fill_uf)
n_un = save_tex('d1_shgi0_un3', fill_un)
n_d0 = save_tex('d0_t0', fill_d0)

# ---- the bake index: Kakariko's tile layout with our model's materials (offset 0, scale 1: the model's bake UV is
# already the atlas position)
tmpl = parse_byml(unpack_sarc_to_dict(rf(f'Bake/Scene/{TEMPLATE}.bkres.zs'))['bkdat.byaml'])
mats = sorted(set(int(R[f'm{k}_mat'][0]) for k in range(n)))
names = [str(x) for x in R['mat_names']]
def model_el(tex_index):
    return [{'Guid': GUID, 'MaterialElements': [
        {'MaterialName': names[m], 'OriginalMaterialIndex': m, 'TexcoordOffset': {'X': 0.0, 'Y': 0.0},
         'TexcoordScale': {'X': 1.0, 'Y': 1.0}, 'TextureIndex': tex_index, 'TextureIndex1': -1} for m in mats],
        'ModelName': MODEL_NAME, 'OriginalMaterialCount': len(names)}]
bk = dict(tmpl, LevelOfDetail=0)          # (the template is a coarse G1 tile, LevelOfDetail 1; ours are near tiles)
el0, el1 = dict(tmpl['DataElements'][0]), dict(tmpl['DataElements'][1])
el0['ModelElements'] = model_el(0); el0['TextureNames'] = [n_d0]
el1['ModelElements'] = model_el(0); el1['TextureNames'] = [n_uf, n_un]
bk['DataElements'] = [el0, el1]
data = create_sarc({'bkdat.byaml': write_byml(bk)})
sdir = os.path.join(OUT, 'Bake/Scene')
tiles = [f for f in os.listdir(sdir) if f.startswith(BANC + '_G')] if os.path.isdir(sdir) else []
# (tiles: 128 m cells over x and z, a model belongs to the cell of its bounding-box centre; our model only there)
allp = np.concatenate([R[f'm{k}_pos'] for k in range(n)]) + MODEL_AT
c = (allp.min(0) + allp.max(0)) / 2
own = f'{BANC}_G_{int(np.floor(c[0] / 128)) + 8}_{int(np.floor(c[2] / 128)) + 8}.bkres.zs'
empty = dict(bk); empty['DataElements'] = [dict(el0, ModelElements=[], TextureNames=[]), dict(el1, ModelElements=[], TextureNames=[])]   # (a tile naming our textures could release them when it streams out)
edata = create_sarc({'bkdat.byaml': write_byml(empty)})
if own not in tiles: tiles.append(own)
for f in tiles:
    open(os.path.join(sdir, f), 'wb').write(compress_zs(data if f == own else edata, f'Bake/Scene/{f}', ROMFS))
print('model bound in', own)
print(f'bake index written to {len(tiles)} tiles: {tiles} ({time.time() - t0:.0f}s)')
