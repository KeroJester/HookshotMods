# bake_rd.py: research reader for the scene bake atlases (RBake_Scene_*): BC5 / BC6H / BC7, array layers.
#   python3 bake_rd.py <texture name> [x0 y0 size]  -> stats and a PNG crop per layer/channel
import os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from rf import rf
from mod_manager import bntx


class Tex(bntx.Texture):
    """bntx.Texture without the single-layer limit (layers follow each other, image_size / array apart)."""
    def __init__(self, data):
        self.d = bytearray(data)
        o = self.brti = self.d.find(b'BRTI')
        (self.flags, self.dim, self.tile, self.swz, self.mips, self.samples, self.format, self.access,
         self.width, self.height, self.depth, self.array, layout) = struct.unpack_from('<BBHHHIIIiiiii', self.d, o + 0x10)
        self.bh_log2 = layout & 7
        self.image_size = struct.unpack_from('<I', self.d, o + 0x50)[0]
        ptrs = struct.unpack_from('<Q', self.d, o + 0x70)[0]
        self.mip_offsets = [struct.unpack_from('<Q', self.d, ptrs + 8 * i)[0] for i in range(self.mips)]
        fmt = self.format >> 8
        self.bw, self.bhh, self.bpb = {0x1f: (4, 4, 16)}.get(fmt) or bntx.FORMATS[fmt]
        self.layer_size = self.image_size // self.array

    def layer_blocks(self, layer):
        base = self.mip_offsets[0]
        self.mip_offsets[0] = base + layer * self.layer_size
        try: return self.read_blocks(0)
        finally: self.mip_offsets[0] = base


def bc4(b):   # [n, 8] -> [n, 16] values 0..255 (vectorised)
    r0, r1 = b[:, 0].astype(np.int32), b[:, 1].astype(np.int32)
    pal = np.zeros((len(b), 8), np.int32)
    pal[:, 0], pal[:, 1] = r0, r1
    big = r0 > r1
    for i in range(6):
        pal[:, 2 + i] = np.where(big, ((6 - i) * r0 + (1 + i) * r1) // 7, 0)
    for i in range(4):
        pal[:, 2 + i] = np.where(big, pal[:, 2 + i], ((4 - i) * r0 + (1 + i) * r1) // 5)
    pal[:, 6] = np.where(big, pal[:, 6], 0)
    pal[:, 7] = np.where(big, pal[:, 7], 255)
    bits = np.zeros(len(b), np.uint64)
    for k in range(6): bits |= b[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
    idx = np.stack([((bits >> np.uint64(3 * i)) & np.uint64(7)).astype(np.int64) for i in range(16)], 1)
    return np.take_along_axis(pal, idx, 1)


# ---- BC7 (all 8 modes), per block
P2 = [0xCCCC,0x8888,0xEEEE,0xECC8,0xC880,0xFEEC,0xFEC8,0xEC80,0xC800,0xFFEC,0xFE80,0xE800,0xFFE8,0xFF00,0xFFF0,0xF000,
      0xF710,0x008E,0x7100,0x08CE,0x008C,0x7310,0x3100,0x8CCE,0x088C,0x3110,0x6666,0x366C,0x17E8,0x0FF0,0x718E,0x399C,
      0xaaaa,0xf0f0,0x5a5a,0x33cc,0x3c3c,0x55aa,0x9696,0xa55a,0x73ce,0x13c8,0x324c,0x3bdc,0x6996,0xc33c,0x9966,0x0660,
      0x0272,0x04e4,0x4e40,0x2720,0xc936,0x936c,0x39c6,0x639c,0x9336,0x9cc6,0x817e,0xe718,0xccf0,0x0fcc,0x7744,0xee22]
P3 = ["0011001102210222","0001001122112221","0000200122112211","0222002200110111","0000000011221122","0011001100220022",
      "0022002211111111","0011001122112211","0000000011112222","0000111111112222","0000111122222222","0012001200120012",
      "0112011201120112","0122012201220122","0011011211221222","0011200122002220","0001001101121122","0111001120012200",
      "0000112211221122","0022002200221111","0111011102220222","0001000122212221","0000001101220122","0000110022102210",
      "0122012200110000","0012001211222222","0110122112210110","0000011012211221","0022110211020022","0110011020022222",
      "0011012201220011","0000200022112221","0000000211221222","0222002200120011","0011001200220222","0120012001200120",
      "0000111122220000","0120120120120120","0120201212010120","0011220011220011","0011112222000011","0101010122222222",
      "0000000021212121","0022112200221122","0022001100220011","0220122102201221","0101222222220101","0000212121212121",
      "0101010101012222","0222011102220111","0002111200021112","0000211221122112","0222011101110222","0002111211121112",
      "0110011001100222","0000000021122112","0110011022222222","0022001100110022","0022112211220022","0000000000002112",
      "0002000100020001","0222122202221222","0101222222222222","0111201122012220"]
A2 = [15,15,15,15,15,15,15,15,15,15,15,15,15,15,15,15,15,2,8,2,2,8,8,15,2,8,2,2,8,8,2,2,
      15,15,6,8,2,8,15,15,2,8,2,2,2,15,15,6,6,2,6,8,15,15,2,2,15,15,15,15,15,2,2,15]
A3a = [3,3,15,15,8,3,15,15,8,8,6,6,6,5,3,3,3,3,8,15,3,3,6,10,5,8,8,6,8,5,15,15,
       8,15,3,5,6,10,8,15,15,3,15,5,15,15,15,15,3,15,5,5,5,8,5,10,5,10,8,13,15,12,3,3]
A3b = [15,8,8,3,15,15,3,8,15,15,15,15,15,15,15,8,15,8,15,3,15,8,15,8,3,15,6,10,15,15,10,8,
       15,3,15,10,10,8,9,10,6,15,8,15,3,6,6,8,15,3,15,15,15,15,15,15,15,15,15,15,3,15,15,8]
W = {2: [0, 21, 43, 64], 3: [0, 9, 18, 27, 37, 46, 55, 64], 4: [0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64]}


class Bits:
    def __init__(self, b): self.v = int.from_bytes(bytes(b), 'little'); self.p = 0
    def get(self, n):
        r = (self.v >> self.p) & ((1 << n) - 1); self.p += n; return r


def bc7_block(b):
    bs = Bits(b)
    mode = 0
    while mode < 8 and not bs.get(1): mode += 1
    if mode == 8: return np.zeros((16, 4), np.uint8)
    NS, PB, RB, ISB, CB, AB, EPB, SPB, IB, IB2 = [
        (3, 4, 0, 0, 4, 0, 1, 0, 3, 0), (2, 6, 0, 0, 6, 0, 0, 1, 3, 0), (3, 6, 0, 0, 5, 0, 0, 0, 2, 0),
        (2, 6, 0, 0, 7, 0, 1, 0, 2, 0), (1, 0, 2, 1, 5, 6, 0, 0, 2, 3), (1, 0, 2, 0, 7, 8, 0, 0, 2, 2),
        (1, 0, 0, 0, 7, 7, 1, 0, 4, 0), (2, 6, 0, 0, 5, 5, 1, 0, 2, 0)][mode]
    part = bs.get(PB); rot = bs.get(RB); isel = bs.get(ISB)
    ne = NS * 2
    ep = np.zeros((ne, 4), np.int32)
    for c in range(3):
        for e in range(ne): ep[e, c] = bs.get(CB)
    if AB:
        for e in range(ne): ep[e, 3] = bs.get(AB)
    else: ep[:, 3] = 255
    cb, ab = CB, AB
    if EPB:
        for e in range(ne):
            p = bs.get(1)
            ep[e, :3] = (ep[e, :3] << 1) | p
            if AB: ep[e, 3] = (ep[e, 3] << 1) | p
        cb += 1; ab += 1 if AB else 0
    if SPB:
        for s in range(NS):
            p = bs.get(1)
            for e in (2 * s, 2 * s + 1): ep[e, :3] = (ep[e, :3] << 1) | p
        cb += 1
    ep[:, :3] = (ep[:, :3] << (8 - cb)) | (ep[:, :3] >> (2 * cb - 8))
    if AB: ep[:, 3] = (ep[:, 3] << (8 - ab)) | (ep[:, 3] >> (2 * ab - 8))
    subset = [0] * 16
    if NS == 2: subset = [(P2[part] >> i) & 1 for i in range(16)]
    if NS == 3: subset = [int(c) for c in P3[part]]
    anchors = [0] + ([A2[part]] if NS == 2 else [A3a[part], A3b[part]] if NS == 3 else [])
    idx = []
    for i in range(16):
        n = IB - (1 if i in anchors else 0)
        idx.append(bs.get(n))
    idx2 = []
    if IB2:
        for i in range(16): idx2.append(bs.get(IB2 - (1 if i == 0 else 0)))
    out = np.zeros((16, 4), np.int32)
    for i in range(16):
        s = subset[i]; e0, e1 = ep[2 * s], ep[2 * s + 1]
        if IB2:
            ci, ai = (idx2[i], idx[i]) if isel else (idx[i], idx2[i])
            cbits, abits = (IB2, IB) if isel else (IB, IB2)
            wc, wa = W[cbits][ci], W[abits][ai]
        else:
            wc = wa = W[IB][idx[i]]
        c = ((64 - wc) * e0[:3] + wc * e1[:3] + 32) >> 6
        a = ((64 - wa) * e0[3] + wa * e1[3] + 32) >> 6
        px = [c[0], c[1], c[2], a]
        if rot: px[3], px[rot - 1] = px[rot - 1], px[3]
        out[i] = px
    return out.astype(np.uint8)


def decode(tex, layer, x0, y0, size):
    blocks = tex.layer_blocks(layer)
    bx0, by0, n = x0 // 4, y0 // 4, size // 4
    blk = blocks[by0:by0 + n, bx0:bx0 + n]
    fmt = tex.format >> 8
    flat = blk.reshape(-1, blk.shape[-1])
    if fmt == 0x1e:
        img = np.stack([bc4(flat[:, :8]), bc4(flat[:, 8:])], -1)
    elif fmt == 0x20:
        img = np.array([bc7_block(b) for b in flat])
    elif fmt == 0x1f:
        img = np.array([bc6_block(b) for b in flat])
    else:
        raise ValueError(hex(fmt))
    c = img.shape[-1]
    return img.reshape(n, n, 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(n * 4, n * 4, c)


# ---- BC6H (unsigned/signed by format), per block -> float RGB
BC6_MODES = {  # mode bits -> (index into table): handled by the generic field table below
}


def _half_to_float(h):
    return np.frombuffer(np.array(h, np.uint16).tobytes(), np.float16).astype(np.float32)


def bc6_block(b, signed=False):
    from bc6 import decode_block
    return decode_block(bytes(b), signed)


if __name__ == '__main__':
    name = sys.argv[1]
    x0, y0, size = (int(v) for v in sys.argv[2:5]) if len(sys.argv) > 4 else (0, 0, 256)
    t = Tex(rf(f'Tex/{name}.bntx.zs'))
    print(name, 'fmt', hex(t.format), t.width, t.height, 'layers', t.array)
    for L in range(t.array):
        img = decode(t, L, x0, y0, size).astype(np.float64)
        flat = img.reshape(-1, img.shape[-1])
        print(f' layer {L}: min {flat.min(0).round(3)} mean {flat.mean(0).round(3)} max {flat.max(0).round(3)} '
              f'nonzero {np.mean(np.any(flat != 0, 1)):.2f}')
        np.save(os.path.join(os.path.dirname(__file__), '..', f'bk_{name[-12:]}_{L}.npy'), img)
