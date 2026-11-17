# bc6.py: BC6H block decoder (unsigned / signed), from the D3D11 functional spec's mode bit layouts.
import numpy as np

# per mode: (mode bits value, regions, transformed, endpoint bits, (delta r, g, b), layout)
# layout: list of (field, hi, lo) read in order; fields rw gw bw (endpoint 0), rx gx bx, ry gy by, rz gz bz, d
# (hi < lo means the bits are stored reversed: first bit read goes to hi... handled by the range direction)
def L(s):
    out = []
    for tok in s.split():
        f, r = tok.split('[')
        r = r.rstrip(']')
        if ':' in r:
            a, b = (int(x) for x in r.split(':'))
        else:
            a = b = int(r)
        out.append((f, a, b))
    return out

MODES = {
    0x00: (2, True, 10, (5, 5, 5), L('gy[4] by[4] bz[4] rw[9:0] gw[9:0] bw[9:0] rx[4:0] gz[4] gy[3:0] gx[4:0] bz[0] gz[3:0] bx[4:0] bz[1] by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]')),
    0x01: (2, True, 7, (6, 6, 6), L('gy[5] gz[4] gz[5] rw[6:0] bz[0] bz[1] by[4] gw[6:0] by[5] bz[2] gy[4] bw[6:0] bz[3] bz[5] bz[4] rx[5:0] gy[3:0] gx[5:0] gz[3:0] bx[5:0] by[3:0] ry[5:0] rz[5:0] d[4:0]')),
    0x02: (2, True, 11, (5, 4, 4), L('rw[9:0] gw[9:0] bw[9:0] rx[4:0] rw[10] gy[3:0] gx[3:0] gw[10] bz[0] gz[3:0] bx[3:0] bw[10] bz[1] by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]')),
    0x06: (2, True, 11, (4, 5, 4), L('rw[9:0] gw[9:0] bw[9:0] rx[3:0] rw[10] gz[4] gy[3:0] gx[4:0] gw[10] gz[3:0] bx[3:0] bw[10] bz[1] by[3:0] ry[3:0] bz[0] bz[2] rz[3:0] gy[4] bz[3] d[4:0]')),
    0x0a: (2, True, 11, (4, 4, 5), L('rw[9:0] gw[9:0] bw[9:0] rx[3:0] rw[10] by[4] gy[3:0] gx[3:0] gw[10] bz[0] gz[3:0] bx[4:0] bw[10] by[3:0] ry[3:0] bz[1] bz[2] rz[3:0] bz[4] bz[3] d[4:0]')),
    0x0e: (2, True, 9, (5, 5, 5), L('rw[8:0] by[4] gw[8:0] gy[4] bw[8:0] bz[4] rx[4:0] gz[4] gy[3:0] gx[4:0] bz[0] gz[3:0] bx[4:0] bz[1] by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]')),
    0x12: (2, True, 8, (6, 5, 5), L('rw[7:0] gz[4] by[4] gw[7:0] bz[2] gy[4] bw[7:0] bz[3] bz[4] rx[5:0] gy[3:0] gx[4:0] bz[0] gz[3:0] bx[4:0] bz[1] by[3:0] ry[5:0] rz[5:0] d[4:0]')),
    0x16: (2, True, 8, (5, 6, 5), L('rw[7:0] bz[0] by[4] gw[7:0] gy[5] gy[4] bw[7:0] gz[5] bz[4] rx[4:0] gz[4] gy[3:0] gx[5:0] gz[3:0] bx[4:0] bz[1] by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]')),
    0x1a: (2, True, 8, (5, 5, 6), L('rw[7:0] bz[1] by[4] gw[7:0] by[5] gy[4] bw[7:0] bz[5] bz[4] rx[4:0] gz[4] gy[3:0] gx[4:0] bz[0] gz[3:0] bx[5:0] by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]')),
    0x1e: (2, False, 6, (6, 6, 6), L('rw[5:0] gz[4] bz[0] bz[1] by[4] gw[5:0] gy[5] by[5] bz[2] gy[4] bw[5:0] gz[5] bz[3] bz[5] bz[4] rx[5:0] gy[3:0] gx[5:0] gz[3:0] bx[5:0] by[3:0] ry[5:0] rz[5:0] d[4:0]')),
    0x03: (1, False, 10, (10, 10, 10), L('rw[9:0] gw[9:0] bw[9:0] rx[9:0] gx[9:0] bx[9:0]')),
    0x07: (1, True, 11, (9, 9, 9), L('rw[9:0] gw[9:0] bw[9:0] rx[8:0] rw[10] gx[8:0] gw[10] bx[8:0] bw[10]')),
    0x0b: (1, True, 12, (8, 8, 8), L('rw[9:0] gw[9:0] bw[9:0] rx[7:0] rw[10:11] gx[7:0] gw[10:11] bx[7:0] bw[10:11]')),
    0x0f: (1, True, 16, (4, 4, 4), L('rw[9:0] gw[9:0] bw[9:0] rx[3:0] rw[10:15] gx[3:0] gw[10:15] bx[3:0] bw[10:15]')),
}
P2 = [0xCCCC,0x8888,0xEEEE,0xECC8,0xC880,0xFEEC,0xFEC8,0xEC80,0xC800,0xFFEC,0xFE80,0xE800,0xFFE8,0xFF00,0xFFF0,0xF000,
      0xF710,0x008E,0x7100,0x08CE,0x008C,0x7310,0x3100,0x8CCE,0x088C,0x3110,0x6666,0x366C,0x17E8,0x0FF0,0x718E,0x399C]
A2 = [15,15,15,15,15,15,15,15,15,15,15,15,15,15,15,15,15,2,8,2,2,8,8,15,2,8,2,2,8,8,2,2]
W3 = [0, 9, 18, 27, 37, 46, 55, 64]
W4 = [0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64]


def _sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def _unq(c, bits, signed):
    if signed:
        if bits >= 16: return c
        s = c < 0
        c = -c if s else c
        if c == 0: u = 0
        elif c >= (1 << (bits - 1)) - 1: u = 0x7FFF
        else: u = ((c << 15) + 0x4000) >> (bits - 1)
        return -u if s else u
    if bits >= 15: return c
    if c == 0: return 0
    if c == (1 << bits) - 1: return 0xFFFF
    return ((c << 16) + 0x8000) >> bits


def decode_block(b: bytes, signed=False):
    """-> [16, 3] float32 (row-major texels)"""
    v = int.from_bytes(b, 'little')
    pos = [0]
    def get(n):
        r = (v >> pos[0]) & ((1 << n) - 1); pos[0] += n; return r
    m = get(2)
    if m > 1: m |= get(3) << 2
    if m not in MODES: return np.zeros((16, 3), np.float32)
    regions, transformed, eb, db, layout = MODES[m]
    f = {k: 0 for k in ('rw', 'gw', 'bw', 'rx', 'gx', 'bx', 'ry', 'gy', 'by', 'rz', 'gz', 'bz', 'd')}
    for name, a, c in layout:
        if a >= c:
            for bit in range(c, a + 1): f[name] |= get(1) << bit
        else:                                      # reversed: [10:15] means the first bit read is bit 15
            for bit in range(c, a - 1, -1): f[name] |= get(1) << bit
    ep = [[f['rw'], f['gw'], f['bw']], [f['rx'], f['gx'], f['bx']], [f['ry'], f['gy'], f['by']], [f['rz'], f['gz'], f['bz']]]
    ne = 2 * regions
    for e in range(ne):
        for ch in range(3):
            if e == 0:
                if signed: ep[e][ch] = _sext(ep[e][ch], eb)
            elif transformed:
                d = _sext(ep[e][ch], db[ch])
                val = (ep[0][ch] + d) & ((1 << eb) - 1)
                ep[e][ch] = _sext(val, eb) if signed else val
            elif signed:
                ep[e][ch] = _sext(ep[e][ch], eb)
    ep = [[_unq(ep[e][ch], eb, signed) for ch in range(3)] for e in range(ne)]
    part = f['d']
    out = np.zeros((16, 3), np.int64)
    if regions == 2:
        anchor = A2[part]
        for i in range(16):
            n = 2 if i in (0, anchor) else 3
            idx = get(n)
            s = (P2[part] >> i) & 1
            w = W3[idx]
            for ch in range(3):
                out[i, ch] = ((64 - w) * ep[2 * s][ch] + w * ep[2 * s + 1][ch] + 32) >> 6
    else:
        for i in range(16):
            idx = get(3 if i == 0 else 4)
            w = W4[idx]
            for ch in range(3):
                out[i, ch] = ((64 - w) * ep[0][ch] + w * ep[1][ch] + 32) >> 6
    if signed:
        h = np.where(out < 0, ((-out * 31) >> 5) | 0x8000, (out * 31) >> 5)
    else:
        h = (out * 31) >> 6
    return np.frombuffer(h.astype(np.uint16).tobytes(), np.float16).astype(np.float32).reshape(16, 3)
