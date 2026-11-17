"""Block-compression encoders for the scene bake atlases (RESEARCH_NOTES.md §80s): BC5, BC7 (mode 6), BC6H
unsigned (mode 11). Plain min/max endpoints per block, indices by projection: enough for smooth lighting data.
Input images are [H, W, C] with H and W multiples of 4; output [H/4, W/4, 16] block arrays (row-major blocks)."""
import numpy as np

W4 = np.array([0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64])


def _blocks(img):
    H, W = img.shape[:2]
    c = img.shape[2] if img.ndim == 3 else 1
    return img.reshape(H // 4, 4, W // 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(H // 4, W // 4, 16, c)


def _pack_bits(fields, nblocks):
    """fields: list of (values [n] int, nbits) in bit order -> [n, 16] uint8"""
    out = np.zeros((nblocks, 2), np.uint64)          # 128 bits as two u64
    pos = 0
    for v, n in fields:
        v = np.asarray(v, np.uint64) & np.uint64((1 << n) - 1)
        for k in range(n):
            bit = (v >> np.uint64(k)) & np.uint64(1)
            word, sh = divmod(pos, 64)
            out[:, word] |= bit << np.uint64(sh)
            pos += 1
    assert pos == 128, pos
    return out.view(np.uint8).reshape(nblocks, 16)


def _bc4(v):
    """v [n, 16] uint8 -> [n, 8]"""
    v = v.astype(np.int32)
    r0, r1 = v.max(1), v.min(1)
    pal = np.stack([r0, r1] + [((6 - i) * r0 + (1 + i) * r1) // 7 for i in range(6)], 1)
    idx = np.abs(v[:, :, None] - pal[:, None, :]).argmin(2)
    idx = np.where((r0 == r1)[:, None], 0, idx)
    out = np.zeros((len(v), 8), np.uint8)
    out[:, 0], out[:, 1] = r0, r1
    bits = np.zeros(len(v), np.uint64)
    for i in range(16): bits |= idx[:, i].astype(np.uint64) << np.uint64(3 * i)
    for k in range(6): out[:, 2 + k] = ((bits >> np.uint64(8 * k)) & np.uint64(255)).astype(np.uint8)
    return out


def bc5(img):
    """[H, W, 2] uint8 -> [H/4, W/4, 16]"""
    b = _blocks(img)
    hb, wb = b.shape[:2]
    f = b.reshape(-1, 16, 2)
    return np.concatenate([_bc4(f[:, :, 0]), _bc4(f[:, :, 1])], 1).reshape(hb, wb, 16)


def _project(v, e0, e1):
    """v [n,16,c], e0/e1 [n,c] -> 4-bit indices [n,16] (anchor fix done by the caller)"""
    d = (e1 - e0).astype(np.float64)
    dd = (d * d).sum(1)
    t = ((v - e0[:, None, :]) * d[:, None, :]).sum(2) / np.where(dd > 0, dd, 1)[:, None]
    t = np.clip(t, 0, 1)
    return np.abs(t[:, :, None] * 64 - W4[None, None, :]).argmin(2)


def bc7_mode6(img):
    """[H, W, 3 or 4] uint8 (alpha 255 if 3) -> [H/4, W/4, 16]"""
    if img.shape[2] == 3:
        img = np.concatenate([img, np.full(img.shape[:2] + (1,), 255, np.uint8)], 2)
    b = _blocks(img)
    hb, wb = b.shape[:2]
    v = b.reshape(-1, 16, 4).astype(np.int32)
    lo, hi = v.min(1), v.max(1)
    # endpoints (7 bits + a shared p bit each): min with p 0, max with p 1
    c0 = np.clip(lo >> 1, 0, 127); c1 = np.clip((hi - 1) >> 1, 0, 127)
    c1 = np.where(hi == 0, 0, c1)
    e0 = c0 << 1; e1 = (c1 << 1) | 1
    idx = _project(v, e0, e1)
    swap = idx[:, 0] >= 8
    c0, c1 = np.where(swap[:, None], c1, c0), np.where(swap[:, None], c0, c1)
    p0, p1 = np.where(swap, 1, 0), np.where(swap, 0, 1)
    idx = np.where(swap[:, None], 15 - idx, idx)
    n = len(v)
    fields = [(np.full(n, 1 << 6), 7)]
    for ch in range(4):
        fields += [(c0[:, ch], 7), (c1[:, ch], 7)]
    fields += [(p0, 1), (p1, 1)]
    fields += [(idx[:, 0], 3)] + [(idx[:, i], 4) for i in range(1, 16)]
    return _pack_bits(fields, n).reshape(hb, wb, 16)


def _half_bits(x):
    return np.asarray(np.clip(x, 0, 65504), np.float16).view(np.uint16).astype(np.int64)


def bc6h_mode11(img):
    """[H, W, 3] float (>= 0) -> [H/4, W/4, 16] (unsigned BC6H)"""
    b = _blocks(img.astype(np.float32))
    hb, wb = b.shape[:2]
    h = _half_bits(b.reshape(-1, 16, 3))
    u = h * 64 / 31.0                                  # (the decoder's interpolated value before (x * 31) >> 6)
    def q(x):                                          # unquantised value -> 10-bit endpoint
        return np.clip(np.round((x * 1024 - 0x8000) / 65536.0), 0, 1023).astype(np.int64)
    def unq(c):
        return np.where(c == 0, 0, np.where(c == 1023, 0xFFFF, ((c << 16) + 0x8000) >> 10))
    c0, c1 = q(u.min(1)), q(u.max(1))
    e0, e1 = unq(c0), unq(c1)
    idx = _project(u, e0, e1)
    swap = idx[:, 0] >= 8
    c0, c1 = np.where(swap[:, None], c1, c0), np.where(swap[:, None], c0, c1)
    idx = np.where(swap[:, None], 15 - idx, idx)
    n = len(u)
    fields = [(np.full(n, 0x03), 5)]
    fields += [(c0[:, 0], 10), (c0[:, 1], 10), (c0[:, 2], 10), (c1[:, 0], 10), (c1[:, 1], 10), (c1[:, 2], 10)]
    fields += [(idx[:, 0], 3)] + [(idx[:, i], 4) for i in range(1, 16)]
    return _pack_bits(fields, n).reshape(hb, wb, 16)
