# bake_probes.py: Clock Town's own light probes (RESEARCH_NOTES.md §80r).
#   python3 bake_probes.py [mod folder name]       (after build_dev.py)
# The probe files (Bake/Probe/Scene/<banc>/): meta.bkprmb (+0x10 grid origin xyz, cell size 64; +0x20 cell counts),
# resident.bkprcb and X<i>_Y<j>_Z<k>_<lod>.bkprb. Each file: octree cells (8 corner-probe indices + local box), then a
# footer {u32 4, u32 probe count, 6 zero words}, then one 72-byte record per probe: float3 world position, u32 flag
# (1 lit, 0 occluded / inside), 28 half floats = 7 groups of (R, G, B, x): group 0 the ambient (DC) colour, the
# others directional terms of the same colour, x a separate (visibility) term.
# The layout (cells, bricks, LODs) is a donor map's; the grid is moved over Clock Town and every probe is baked
# from Clock Town's own mesh: cosine-weighted sky visibility by ray casting (MM3D room triangles), probes inside
# geometry flagged 0; the data blends the donor's own occluded and open-sky samples by that visibility.
import os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from rf import rf, ROMFS
from mod_manager.zstd_helper import compress_zs

GAME = os.path.expanduser(os.environ.get('HOOKSHOT_GAME_DIR', '~/Games/Hookshot'))
OUT = os.path.join(GAME, 'mods', sys.argv[1] if len(sys.argv) > 1 else '_070-ClockTownDev', 'romfs')
BANC = os.environ.get('DEV_BANC', 'ClockTownDev')
DONOR = os.environ.get('PROBE_DONOR', 'KakarikoVillage_Child')
MODEL_AT = np.array([0.0, 100.0, 0.0])                     # (the model's placement: Land_SageRoom_Base_A_01)
ORIGIN = np.array([float(v) for v in os.environ.get('PROBE_ORIGIN', '-100,78,-120').split(',')])
RAYS = int(os.environ.get('PROBE_RAYS', '48'))
AMBIENT_MIN = float(os.environ.get('PROBE_MIN', '0.15'))   # (visibility floor: light bounced off the walls)
TRIS = os.path.join(os.path.dirname(__file__), '..', 'ct_tris.npy')


def footer(d):
    for o in range(0x10, len(d) - 32, 4):
        a, n = struct.unpack_from('<II', d, o)
        if a == 4 and n and len(d) - o - 32 == 72 * n and not any(struct.unpack_from('<6I', d, o + 8)):
            return o + 32, n
    raise ValueError('no probe footer')


def records(d):
    o, n = footer(d)
    pos = np.array([struct.unpack_from('<3f', d, o + 72 * i) for i in range(n)])
    flag = np.array([struct.unpack_from('<I', d, o + 72 * i + 12)[0] for i in range(n)])
    h = np.frombuffer(b''.join(d[o + 72 * i + 16:o + 72 * i + 72] for i in range(n)), np.float16).reshape(n, 28)
    return o, pos, flag, h.astype(np.float64)


# ---- the mesh: MM3D Clock Town triangles in world space
T = np.load(TRIS) + MODEL_AT
A, E1, E2 = T[:, 0], T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
N = np.cross(E1, E2)


def cast(o, dirs):
    """o (3,), dirs (r,3) -> (hit (r,) bool, backface (r,) bool) for the nearest hit (Moller-Trumbore)."""
    p = np.cross(dirs[:, None, :], E2[None])                         # (r,t,3)
    det = np.einsum('tk,rtk->rt', E1, p)
    ok = np.abs(det) > 1e-9
    inv = np.where(ok, 1.0 / np.where(ok, det, 1), 0)
    s = o - A                                                        # (t,3)
    u = np.einsum('tk,rtk->rt', s, p) * inv
    q = np.cross(s, E1)                                              # (t,3)
    v = np.einsum('rk,tk->rt', dirs, q) * inv
    t = np.einsum('tk,tk->t', E2, q)[None] * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-3)
    tt = np.where(hit, t, np.inf)
    k = tt.argmin(1)
    any_hit = np.isfinite(tt[np.arange(len(dirs)), k])
    back = np.einsum('rk,rk->r', dirs, N[k]) > 0
    return any_hit, any_hit & back


rng = np.random.default_rng(1)
u1, u2 = rng.random(RAYS), rng.random(RAYS)                          # cosine-weighted upper hemisphere
r = np.sqrt(u1)
UP = np.stack([r * np.cos(2 * np.pi * u2), np.sqrt(1 - u1), r * np.sin(2 * np.pi * u2)], 1)
AXES = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1.0]])
lo, hi = T.reshape(-1, 3).min(0) - 2, T.reshape(-1, 3).max(0) + 2
CACHE = os.path.join(os.path.dirname(__file__), '..', 'probe_vis_cache.npy')
cache = np.load(CACHE, allow_pickle=True).item() if os.path.exists(CACHE) else {}


def bake(p):
    key = tuple(np.round(p, 2))
    if key in cache: return cache[key]
    if np.any(p < lo) or np.any(p > hi):
        res = (1.0, False) if p[1] > lo[1] + 2 else (0.0, True)       # (outside the town: open sky / underground)
    else:
        hit, _ = cast(p, UP)
        h6, b6 = cast(p, AXES)
        inside = b6.sum() >= 3
        res = (0.0 if inside else 1.0 - hit.mean(), inside)
    cache[key] = res
    return res


# ---- the donor's samples: open sky (lit, brightest ambient) and occluded
src = f'Bake/Probe/Scene/{DONOR}/'
files = sorted(f for f in os.listdir(os.path.join(ROMFS, src)) if not f.startswith('meta'))
allh, allf = [], []
for f in files:
    _o, _p, fl, h = records(rf(src + f))
    allh.append(h); allf.append(fl)
H, F = np.concatenate(allh), np.concatenate(allf)
lit = H[F == 1]
q70, q90 = np.quantile(lit[:, 0], [0.7, 0.9])                      # (robust: the lit tail reaches 20x the median)
OPEN = lit[(lit[:, 0] >= q70) & (lit[:, 0] <= q90)].mean(0) * float(os.environ.get('PROBE_GAIN', '1'))
DARK = np.median(H[F == 0], 0)
print(f'donor {DONOR}: {len(H)} probes; open ambient {OPEN[:3].round(3)}, occluded {DARK[:3].round(3)}')

meta = bytearray(rf(src + 'meta.bkprmb.zs'))
old_origin = np.array(struct.unpack_from('<3f', meta, 0x10))
cell = struct.unpack_from('<f', meta, 0x1c)[0]
count = struct.unpack_from('<3I', meta, 0x20)
shift = ORIGIN - old_origin
print(f'grid {old_origin} -> {ORIGIN} (cells {count} x {cell} m: to {ORIGIN + cell * np.array(count)})')
struct.pack_into('<3f', meta, 0x10, *ORIGIN)
dst = os.path.join(OUT, f'Bake/Probe/Scene/{BANC}')
os.makedirs(dst, exist_ok=True)
for f in os.listdir(dst): os.remove(os.path.join(dst, f))
open(os.path.join(dst, 'meta.bkprmb.zs'), 'wb').write(compress_zs(bytes(meta), f'Bake/Probe/Scene/{BANC}/meta.bkprmb.zs', ROMFS))

vis_all = []
for f in files:
    d = bytearray(rf(src + f))
    o, pos, _fl, _h = records(bytes(d))
    for i, p in enumerate(pos):
        q = p + shift
        v, inside = bake(q)
        w = AMBIENT_MIN + (1 - AMBIENT_MIN) * v
        data = DARK + w * (OPEN - DARK) if not inside else DARK
        rec = o + 72 * i
        struct.pack_into('<3fI', d, rec, *q, 0 if inside else 1)
        d[rec + 16:rec + 72] = data.astype(np.float16).tobytes()
        vis_all.append(v)
    rel = f'Bake/Probe/Scene/{BANC}/{f}'
    open(os.path.join(OUT, rel), 'wb').write(compress_zs(bytes(d), rel, ROMFS))
vis_all = np.array(vis_all)
np.save(CACHE, cache, allow_pickle=True)
print(f'{len(files)} files, {len(vis_all)} probes baked ({len(cache)} positions); sky visibility mean {vis_all.mean():.2f}, '
      f'inside {np.mean(vis_all == 0):.0%}')
