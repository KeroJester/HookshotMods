# hktag.py: Havok TAG0 tagfile reader (research). python3 hktag.py <file.bphsh>
import struct, sys

def sections(d, o, end):
    while o < end:
        w = struct.unpack_from('>I', d, o)[0]
        size, flags = w & 0x3FFFFFFF, w >> 30
        if size < 8: raise ValueError(f"section size {size} at {o:#x}")
        tag = d[o + 4:o + 8].decode('latin1')
        yield tag, flags, o + 8, o + size
        o += size

class VR:
    def __init__(self, d, o, end): self.d, self.o, self.end = d, o, end
    def more(self): return self.o < self.end
    def v(self):
        d = self.d; b = d[self.o]
        if b & 0x80 == 0: self.o += 1; return b
        if b & 0xC0 == 0x80: r = ((b & 0x3F) << 8) | d[self.o + 1]; self.o += 2; return r
        if b & 0xE0 == 0xC0: r = ((b & 0x1F) << 16) | (d[self.o + 1] << 8) | d[self.o + 2]; self.o += 3; return r
        if b & 0xF8 == 0xE0: r = ((b & 0x07) << 24) | int.from_bytes(d[self.o + 1:self.o + 4], 'big'); self.o += 4; return r
        if b & 0xF8 == 0xE8: r = ((b & 0x07) << 32) | int.from_bytes(d[self.o + 1:self.o + 5], 'big'); self.o += 5; return r
        if b & 0xF8 == 0xF0: r = ((b & 0x07) << 56) | int.from_bytes(d[self.o + 1:self.o + 8], 'big'); self.o += 8; return r
        if b & 0xF8 == 0xF8: r = int.from_bytes(d[self.o + 1:self.o + 9], 'big'); self.o += 9; return r
        raise ValueError(hex(b))

def strtab(d, a, b):
    return d[a:b].split(b'\0')[:-1]

class Tag:
    def __init__(self, d):
        self.d = d
        assert d[:5] == b'Phive'
        o = struct.unpack_from('<I', d, 0x0c)[0]          # (Phive header: tagfile offset at +0x0c)
        self.base = o
        tag, fl, a, b = next(sections(d, o, len(d)))
        assert tag == 'TAG0', tag
        self.sec = {}
        for t, f, s, e in sections(d, a, b):
            self.sec[t] = (s, e)
            if f == 0:
                for t2, f2, s2, e2 in sections(d, s, e): self.sec[t2] = (s2, e2)
        self.sdk = d[slice(*self.sec['SDKV'])].decode()
        self.data = self.sec['DATA']
        self.parse_types()
        self.parse_items()

    def parse_types(self):
        d = self.d
        self.tstr = [s.decode() for s in strtab(d, *self.sec['TST1'])]
        self.fstr = [s.decode() for s in strtab(d, *self.sec['FST1'])]
        r = VR(d, *self.sec['TNA1'])
        n = r.v()
        self.types = [None]
        for i in range(1, n):
            name = self.tstr[r.v()]
            tc = r.v()
            tmpl = []
            for _ in range(tc):
                tn = self.tstr[r.v()]; tv = r.v()
                tmpl.append((tn, tv))
            self.types.append(dict(name=name, tmpl=tmpl, idx=i))
        r = VR(d, *self.sec['TBDY'])
        while r.more():
            ti = r.v()
            if ti == 0: continue
            t = self.types[ti]
            t['parent'] = r.v()
            fl = r.v(); t['flags'] = fl
            if fl & 1: t['format'] = r.v()
            if fl & 2: t['subtype'] = r.v()
            if fl & 4: t['version'] = r.v()
            if fl & 8: t['size'] = r.v(); t['align'] = r.v()
            if fl & 0x10: t['tflags'] = r.v()
            if fl & 0x20:
                m = r.v(); t['members'] = []
                for _ in range(m):
                    t['members'].append(dict(name=self.fstr[r.v()], flags=r.v(), off=r.v(), type=r.v()))
            if fl & 0x40:
                m = r.v(); t['interfaces'] = [(r.v(), r.v()) for _ in range(m)]
            if fl & 0x80:
                t['attr'] = r.v()
        if 'THSH' in self.sec:
            r = VR(d, *self.sec['THSH'])
            for _ in range(r.v()):
                ti = r.v(); self.types[ti]['hash'] = struct.unpack_from('<I', d, r.o)[0]; r.o += 4

    def tname(self, ti):
        if not ti: return 'void'
        t = self.types[ti]
        if t['tmpl']:
            return t['name'] + '<' + ', '.join((self.tname(v) if n.startswith('t') else str(v)) for n, v in t['tmpl']) + '>'
        return t['name']

    def parse_items(self):
        d = self.d
        s, e = self.sec['ITEM']
        self.items = []
        for o in range(s, e, 12):
            a, off, cnt = struct.unpack_from('<III', d, o)
            self.items.append(dict(type=a & 0xFFFFFF, flags=a >> 24, off=off, count=cnt))
        self.patches = []
        if 'PTCH' in self.sec:
            s, e = self.sec['PTCH']
            o = s
            while o < e:
                ti, n = struct.unpack_from('<II', d, o)
                self.patches.append((ti, list(struct.unpack_from(f'<{n}I', d, o + 8))))
                o += 8 + 4 * n

    def describe(self, ti, ind=0):
        t = self.types[ti]
        s = '  ' * ind + f"[{ti}] {self.tname(ti)} parent={self.tname(t.get('parent', 0))} fmt={t.get('format', 0):#x} sub={self.tname(t.get('subtype', 0))} size={t.get('size')} align={t.get('align')} ver={t.get('version')}"
        for m in t.get('members', []):
            s += '\n' + '  ' * ind + f"    +{m['off']:#x} {m['name']}: {self.tname(m['type'])} (flags {m['flags']:#x})"
        return s

if __name__ == '__main__':
    t = Tag(open(sys.argv[1], 'rb').read())
    print('sdk', t.sdk, 'sections', {k: (hex(a), hex(b - a)) for k, (a, b) in t.sec.items()})
    for i in range(1, len(t.types)): print(t.describe(i))
    for i, it in enumerate(t.items): print('item', i, t.tname(it['type']), hex(it['flags']), hex(it['off']), it['count'])
    print('patches', [(t.tname(a), b) for a, b in t.patches])
