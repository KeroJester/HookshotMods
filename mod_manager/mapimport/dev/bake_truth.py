# bake_truth.py: Kakariko models sampled in their bake atlas (research): vertex normals (world) vs the atlas values.
import glob, os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from rf import rf, ROMFS
from mod_manager.byml import parse_byml
from mod_manager.zstd_helper import unpack_sarc_to_dict
from mod_manager.mapimport.bfres_build import u16, u32, u64
from bake_rd import Tex, bc7_block, bc4
from bc6 import decode_block

TILE = sys.argv[1] if len(sys.argv) > 1 else 'KakarikoVillage_Child_G_7_8'
BANC = TILE.split('_G')[0]
FOLDER = {'KakarikoVillage_Child': 'KakarikoVillage', 'Market_Child': 'Market'}[BANC]


def model_meshes(d):
    fm = u64(d, 0x28); FV, SH = u64(d, fm + 0x20), u64(d, fm + 0x28)
    gpu = u64(d, 0x170)
    s = lambda o: d[o + 2:o + 2 + u16(d, o)].decode()
    out = []
    for i in range(u16(d, fm + 0x6a)):
        f = SH + 0x60 * i
        v = FV + 0x58 * u16(d, f + 0x56)
        at0 = u64(d, v + 8)
        attrs = {s(u64(d, at0 + 0x10 * a)): (u32(d, at0 + 0x10 * a + 8), u16(d, at0 + 0x10 * a + 0xc), d[at0 + 0x10 * a + 0xe])
                 for a in range(d[v + 0x4c])}
        if 'bake' not in attrs: continue
        bufs = u64(d, v + 0x38)
        n = u32(d, v + 0x50)
        def col(name):
            fmt, off, bi = attrs[name]
            stride = u32(d, bufs + 0x10 * bi) if False else None
            return fmt, off, bi
        # buffers: each attribute names its buffer index; strides at the stride table, data offsets from the buffer table
        strides = u64(d, v + 0x38); bufinfo = u64(d, v + 0x30)
        res = {}
        for name in ('_p0', '_n0', 'bake'):
            fmt, off, bi = attrs[name]
            stride = u32(d, strides + 0x10 * bi)
            base = gpu + u32(d, v + 0x48) + sum(0 for _ in range(bi))
            res[name] = (fmt, off, bi, stride)
        out.append((u16(d, f + 0x52), attrs, res, v, n))
    return out


print('tile', TILE)
y = parse_byml(unpack_sarc_to_dict(rf(f'Bake/Scene/{TILE}.bkres.zs'))['bkdat.byaml'])
el = y['DataElements'][1]
print('elements', len(el['ModelElements']), 'textures', el['TextureNames'])
for me in el['ModelElements'][:5]:
    print(me['Guid'], me['ModelName'], [(m['MaterialName'], m['OriginalMaterialIndex'], m['TextureIndex']) for m in me['MaterialElements']])


def euler(r):
    x, y, z = r
    cx, sx, cy, sy, cz, sz = np.cos(x), np.sin(x), np.cos(y), np.sin(y), np.cos(z), np.sin(z)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]); Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


class Sampler:
    def __init__(self, name, kind):
        self.t = Tex(rf(f'Tex/{name}.bntx.zs')); self.kind = kind; self.cache = {}
        self.blocks = [self.t.layer_blocks(L) for L in range(self.t.array)]
    def texel(self, L, x, y):
        bx, by = x // 4, y // 4
        k = (L, bx, by)
        if k not in self.cache:
            b = bytes(self.blocks[L][by, bx])
            self.cache[k] = (decode_block(b) if self.kind == 'bc6' else bc7_block(b)[:, :3].astype(float))
        return self.cache[k][(y % 4) * 4 + x % 4]


if __name__ == '__main__' and os.environ.get('TRUTH', '1') == '1':
    acts = {}
    for k in ('Static', 'Dynamic'):
        for a in parse_byml(rf(f'Banc/{FOLDER}/{BANC}_{k}.bcett.byml.zs'))['Actors']: acts[str(a['Hash'])] = a
    names = el['TextureNames']
    uf = Sampler(names[0], 'bc6'); un = Sampler(names[1], 'bc7')
    W, H = un.t.width, un.t.height
    flip = os.environ.get('FLIPV') == '1'
    rows = []
    rng = np.random.default_rng(0)
    for me in el['ModelElements']:
        a = acts.get(me['Guid'].split('_')[0])
        if a is None: continue
        fs = glob.glob(ROMFS + f"/Model/*.{me['ModelName']}.bfres.zs")
        if not fs: continue
        d = rf(os.path.relpath(fs[0], ROMFS))
        R = euler(a.get('Rotate', [0, 0, 0]))
        mats = {m['OriginalMaterialIndex']: m for m in me['MaterialElements']}
        for (mat, attrs, res, v, n) in model_meshes(d):
            if mat not in mats or attrs['_n0'][0] != 0xe02 or attrs['bake'][0] != 0x1201: continue
            fmt, off, bi, stride = res['_n0']
            base = u64(d, 0x170) + u32(d, v + 0x48)
            raw = np.frombuffer(d, np.uint8, n * stride, base).reshape(n, stride)
            w = raw[:, off:off + 4].copy().view('<u4').reshape(n)
            nn = np.stack([((w >> s_) & 0x3FF).astype(np.int64) for s_ in (0, 10, 20)], 1)
            nn = np.where(nn >= 512, nn - 1024, nn) / 511.0
            bo = res['bake'][1]
            uv = raw[:, bo:bo + 4].copy().view('<u2').reshape(n, 2) / 65535.0
            m = mats[mat]
            au = uv[:, 0] * m['TexcoordScale']['X'] + m['TexcoordOffset']['X']
            av = uv[:, 1] * m['TexcoordScale']['Y'] + m['TexcoordOffset']['Y']
            if flip: av = 1 - av
            for i in rng.choice(n, min(n, 12), replace=False):
                x, yy = int(au[i] * W), int(av[i] * H)
                if not (0 <= x < W and 0 <= yy < H): continue
                nw = R @ nn[i]
                rows.append(np.concatenate([nw, [uf.texel(L, x, yy).mean() for L in range(3)],
                                            *[(un.texel(L, x, yy) - 127.5) / 127.5 for L in range(3)]]))
        if len(rows) > 3000: break
    A = np.array(rows)
    np.set_printoptions(precision=2, suppress=True, linewidth=200)
    print('samples', len(A))
    nw = A[:, :3]
    np.save('../truth.npy', A)
    for L in range(3):
        dirs = A[:, 6 + 3 * L:9 + 3 * L]
        M, *_ = np.linalg.lstsq(nw, dirs, rcond=None)
        pred = nw @ M
        r2 = 1 - ((dirs - pred) ** 2).sum() / ((dirs - dirs.mean(0)) ** 2).sum()
        print(f'L{L}: magnitude mean {A[:, 3 + L].mean():.3f}; corr(magnitude, normal.y) {np.corrcoef(A[:, 3 + L], nw[:, 1])[0, 1]:.2f}; dir = n @ M, R2 {r2:.2f}\n', M.T)
