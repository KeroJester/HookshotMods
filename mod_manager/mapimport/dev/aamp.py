# aamp.py: minimal AAMP v2 reader (research). python3 aamp.py <file> [names...]
import struct, sys, zlib

TYPES = {0: 'bool', 1: 'f32', 2: 'int', 3: 'vec2', 4: 'vec3', 5: 'vec4', 6: 'color', 7: 'str32', 8: 'str64',
         9: 'curve1', 10: 'curve2', 11: 'curve3', 12: 'curve4', 13: 'bufint', 14: 'buff32', 15: 'str256', 16: 'quat',
         17: 'u32', 18: 'bufu32', 19: 'bufbin', 20: 'strref'}


def parse(d, names=()):
    hm = {zlib.crc32(n.encode()): n for n in names}
    for s in d.split(b'\0'):
        try:
            t = s.decode()
            if t.isprintable() and t: hm.setdefault(zlib.crc32(s), t)
        except Exception: pass
    nm = lambda h: hm.get(h, f'0x{h:08x}')
    out = []

    def param(o, ind):
        h, w = struct.unpack_from('<II', d, o)
        off = (w & 0xFFFFFF) * 4 + o
        t = w >> 24
        tn = TYPES.get(t, t)
        if t == 0: v = d[off] != 0
        elif t == 1: v = struct.unpack_from('<f', d, off)[0]
        elif t in (2, 17): v = struct.unpack_from('<i' if t == 2 else '<I', d, off)[0]
        elif t in (3, 4, 5, 6, 16): v = struct.unpack_from('<%df' % {3: 2, 4: 3, 5: 4, 6: 4, 16: 4}[t], d, off)
        elif t in (7, 8, 15, 20): v = d[off:d.find(b'\0', off)].decode('latin1')
        elif t in (13, 14, 18, 19):
            n = struct.unpack_from('<I', d, off - 4)[0]
            v = struct.unpack_from('<%d%s' % (n, {13: 'i', 14: 'f', 18: 'I', 19: 'B'}[t]), d, off)
            if len(v) > 12: v = f'[{n} values: {v[:12]}...]'
        else: v = f'@{off:#x}'
        out.append('  ' * ind + f'{nm(h)} ({tn}) = {v}')

    def obj(o, ind):
        h, w = struct.unpack_from('<II', d, o)
        po, pn = (w & 0xFFFF) * 4 + o, w >> 16
        out.append('  ' * ind + f'[obj {nm(h)}]')
        for i in range(pn): param(po + 8 * i, ind + 1)

    def lst(o, ind):
        h, w1, w2 = struct.unpack_from('<III', d, o)
        lo, ln = (w1 & 0xFFFF) * 4 + o, w1 >> 16
        oo, on = (w2 & 0xFFFF) * 4 + o, w2 >> 16
        out.append('  ' * ind + f'<list {nm(h)}>')
        for i in range(on): obj(oo + 8 * i, ind + 1)
        for i in range(ln): lst(lo + 12 * i, ind + 1)

    assert d[:4] == b'AAMP'
    pio = struct.unpack_from('<I', d, 0x14)[0]
    lst(0x30 + pio, 0)
    return out


if __name__ == '__main__':
    print('\n'.join(parse(open(sys.argv[1], 'rb').read(), sys.argv[2:])))
