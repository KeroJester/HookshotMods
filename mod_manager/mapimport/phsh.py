# phsh_write.py: writes a Phive shape (.bphsh) holding an hknpMeshShape built from triangles (research prototype).
# Layout learned from the game's own shapes (RESEARCH_NOTES.md §80):
#   Phive header 0x30 {"Phive\0", 01 00, ff fe, 00 04, u32 tagfile offset (0x30), u32 materials offset, u32 filters
#   offset, u32 file size, u32 tagfile size, u32 materials size, u32 filters size, 8 x 0}, then the Havok TAG0
#   tagfile (SDK 20250200), then 16 bytes per material {u64 material, u64 flags} and 16 bytes 0xff per material.
#   hknpMeshShape: vertices u16 x3 at 1/512 m from the section's origin (u32 x3, same unit); faces are quads
#   {a,b,c,d} (triangles: c == d), counter-clockwise seen from outside; each section has a 4-wide tree of 8-bit
#   AABBs (q = floor(x / bitScale8Inv) - bitOffset, per axis), stored depth-first, whose children are faces when
#   data[2] > data[3] (leaf nodes pad as [a,b,1,0] / [a,0,1,0]) and nodes otherwise ([a,b,0,0] / [a,b,c,c]); the
#   sections are under a float 4-wide tree (hkcdSimdTree: node 0 empty, root 1, leaves flag 1, data = sections).
import math, struct, sys, os
import numpy as np

from .hktag import Tag

Q = 512.0                      # vertex units per metre
MAXV, MAXP = 250, 250          # per section (indices are bytes)
FLT_MAX = 3.40282e38

def al(n, a=16): return (n + a - 1) // a * a

def _split(tris, V):
    """Splits triangles (index triples) into groups small enough for a section."""
    out = []
    stack = [tris]
    while stack:
        t = stack.pop()
        verts = set(t.flatten().tolist())
        P = V[list(verts)]
        ext = P.max(0) - P.min(0)
        if len(t) <= MAXP and len(verts) <= MAXV and ext.max() < 120:
            out.append(t); continue
        c = V[t].mean(1)
        ax = int(np.argmax(c.max(0) - c.min(0)))
        order = np.argsort(c[:, ax], kind='stable')
        h = len(t) // 2
        stack += [t[order[:h]], t[order[h:]]]
    return out

def _bvh4(boxes, items):
    """4-wide BVH over items (indices) -> nested lists: ('leaf', [items]) or ('node', [children])."""
    if len(items) <= 4: return ('leaf', list(items))
    lo = boxes[items, 0].min(0); hi = boxes[items, 1].max(0)
    ax = int(np.argmax(hi - lo))
    c = (boxes[items, 0, ax] + boxes[items, 1, ax]) / 2
    items = [items[i] for i in np.argsort(c, kind='stable')]
    k = max(1, math.ceil(len(items) / 4))
    groups = [items[i:i + k] for i in range(0, len(items), k)]
    # (each group <= 4 items becomes a leaf node; larger groups recurse)
    return ('node', [_bvh4(boxes, g) for g in groups])

def _box_of(tree, boxes):
    if tree[0] == 'leaf':
        it = tree[1]; return boxes[it, 0].min(0), boxes[it, 1].max(0)
    bs = [_box_of(c, boxes) for c in tree[1]]
    return np.min([b[0] for b in bs], 0), np.max([b[1] for b in bs], 0)

def _section(tris, V):
    ids = sorted(set(tris.flatten().tolist()))
    remap = {v: i for i, v in enumerate(ids)}
    P = V[ids]                                            # metres
    lo = np.floor(P.min(0) * Q) / Q
    off = np.round(lo * Q).astype(np.int64)               # section origin, 1/512 m
    vu = np.round((P - lo) * Q).astype(np.int64)
    assert vu.max() < 65536
    prims = [(remap[a], remap[b], remap[c], remap[c]) for a, b, c in tris]
    # 8-bit quantization
    ext = np.maximum(P.max(0) - lo, 1e-3)
    inv = ext / 250.0
    bitoff = np.floor(lo / inv).astype(np.int64)
    def q(x, up):
        v = (np.ceil(x / inv) if up else np.floor(x / inv)) - bitoff
        return np.clip(v, 0, 255).astype(int)
    boxes = np.array([[P[[remap[a], remap[b], remap[c]]].min(0), P[[remap[a], remap[b], remap[c]]].max(0)] for a, b, c in tris])
    tree = _bvh4(boxes, list(range(len(prims))))
    nodes = []
    def emit(t):
        idx = len(nodes); nodes.append(None)
        kids = t[1]
        if t[0] == 'leaf':
            bxs = [(boxes[i, 0], boxes[i, 1]) for i in kids]; data = list(kids)
            data = data + {1: [0, 1, 0], 2: [1, 0], 3: [0], 4: []}[len(kids)]
            if len(kids) == 4 and not data[2] > data[3]: data[2], data[3] = data[3], data[2]; bxs[2], bxs[3] = bxs[3], bxs[2]
            if len(kids) == 4 and data[2] == data[3]: raise ValueError('leaf with equal faces')
        else:
            bxs = [_box_of(c, boxes) for c in kids]; data = []
            for c in kids: data.append(emit(c))
            data = data + {1: [0, 0, 0], 2: [0, 0], 3: [data[-1]], 4: []}[len(kids)]
            assert data[2] <= data[3]
        raw = bytearray(28)
        for k in range(4):
            if k < len(kids):
                l, h = q(bxs[k][0], False), q(bxs[k][1], True)
                vals = [l[0], h[0], l[1], h[1], l[2], h[2]]
            else:
                vals = [255, 0, 255, 0, 255, 0]
            for j, v in enumerate(vals): raw[j * 4 + k] = v
        raw[24:28] = bytes(data)
        nodes[idx] = bytes(raw)
        return idx
    emit(tree if tree[0] == 'node' else ('node', [tree]))
    assert len(nodes) <= 255, len(nodes)
    vb = struct.pack(f'<{vu.size}H', *vu.flatten().tolist()) + b'\0\0'
    return dict(nodes=nodes, prims=prims, vb=vb, off=off, inv=inv, bitoff=bitoff,
                lo=P.min(0), hi=P.max(0), interior=bytes((2 * len(prims) + 7) // 8))

def _simd_tree(secs):
    """hkcdSimdTree nodes (128 bytes each) over the sections; returns (nodes, root=1)."""
    boxes = np.array([[s['lo'], s['hi']] for s in secs])
    tree = _bvh4(boxes, list(range(len(secs))))
    if tree[0] == 'leaf': tree = ('node', [tree])
    nodes = [None, None]
    out = {}
    def emit(t, idx, parent):
        kids = t[1]
        leaf = t[0] == 'leaf'
        bxs = [(boxes[i, 0], boxes[i, 1]) for i in kids] if leaf else [_box_of(c, boxes) for c in kids]
        data = []
        depth = 1
        if not leaf:
            for c in kids:
                ci = len(nodes); nodes.append(None); data.append(ci)
            nl = 0
            for c, ci in zip(kids, data):
                dd, nn = emit(c, ci, idx); depth = max(depth, dd + 1); nl += nn
        else:
            data = list(kids); nl = len(kids)
        raw = bytearray(128)
        for comp, (sel, lohi) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]):
            for k in range(4):
                v = (bxs[k][lohi][sel] if k < len(kids) else (FLT_MAX if lohi == 0 else -FLT_MAX))
                struct.pack_into('<f', raw, comp * 16 + k * 4, float(v))
        pad = 0xFFFFFFFF if leaf else 0
        struct.pack_into('<4I', raw, 0x60, *(data + [pad] * (4 - len(data))))
        raw[0x74] = 1 if leaf else 0
        raw[0x75] = depth
        struct.pack_into('<II', raw, 0x78, parent, nl)
        nodes[idx] = bytes(raw)
        return depth, nl
    emit(tree, 1, 0)
    empty = bytearray(128)
    for comp in range(6):
        for k in range(4): struct.pack_into('<f', empty, comp * 16 + k * 4, FLT_MAX if comp % 2 == 0 else -FLT_MAX)
    nodes[0] = bytes(empty)
    return nodes

class Data:
    """A tagfile's DATA section under construction: items in order, 16-byte aligned (as the game's files)."""
    def __init__(self, tmpl):
        self.tmpl = tmpl
        self.tid = {tmpl.tname(i): i for i in range(1, len(tmpl.types))}
        self.data = bytearray()
        self.items = [(0, 0, 0, 0)]

    def put(self, type_name, blob, count, flags=0x20):
        o = al(len(self.data)); self.data.extend(b'\0' * (o - len(self.data))); self.data.extend(blob)
        self.items.append((self.tid[type_name], flags, o, count)); return o

    def rel(self, field, target, n):
        struct.pack_into('<ii', self.data, field, target - field, n)


def _prepare(V, T):
    """Triangles -> sections in tree leaf order + top tree nodes."""
    V = np.asarray(V, float); T = np.asarray(T, np.int64)
    secs = [_section(g, V) for g in _split(T, V)]
    tnodes = _simd_tree(secs)
    order = []
    def leaves(i):
        n = tnodes[i]
        dat = struct.unpack_from('<4I', n, 0x60)
        if n[0x74]: order.extend(x for x in dat if x != 0xFFFFFFFF)
        else:
            for x in dat:
                if x: leaves(x)
    leaves(1)
    remap = {old: new for new, old in enumerate(order)}
    fixed = []
    for n in tnodes:
        if n[0x74]:
            b = bytearray(n); dat = struct.unpack_from('<4I', n, 0x60)
            struct.pack_into('<4I', b, 0x60, *[remap[x] if x != 0xFFFFFFFF else x for x in dat]); fixed.append(bytes(b))
        else: fixed.append(n)
    return [secs[i] for i in order], fixed


def add_mesh(td: Data, V, T, tag=0):
    """Appends an hknpMeshShape (its root item and arrays) -> (offset of the shape, lo, hi)."""
    secs, tnodes = _prepare(V, T)
    nsec = len(secs)
    keybits = (max(1, nsec) - 1).bit_length() + 9
    s = td.put('hknpMeshShape', bytes(0xa0), 1, 0x10)
    tags = [(i << 9, tag) for i in range(nsec)] + [((nsec << 9) | 510, 0xFFFF)]
    o_tags = td.put('hknpMeshShape::ShapeTagTableEntry', b''.join(struct.pack('<IHH', k, t, 0) for k, t in tags), len(tags))
    o_nodes = td.put('hkcdSimdTreeNamespace::Node', b''.join(tnodes), len(tnodes))
    o_secs = td.put('hknpMeshShape::GeometrySection', bytes(64 * nsec), nsec)
    offs = {}
    for name, key in [('hknpAabb8TreeNode', 'nodes'), ('hknpMeshShape::GeometrySection::Primitive', 'prims'),
                      ('hkUint8', 'vb'), ('hkUint8', 'interior')]:
        for i, sc in enumerate(secs):
            if key == 'nodes': blob = b''.join(sc['nodes']); n = len(sc['nodes'])
            elif key == 'prims': blob = b''.join(bytes(p) for p in sc['prims']); n = len(sc['prims'])
            else: blob = sc[key]; n = len(blob)
            offs[(key, i)] = (td.put(name, blob, n), n)
    d = td.data
    d[s + 0x18] = 7; d[s + 0x19] = 2; struct.pack_into('<H', d, s + 0x1a, 0x4000); d[s + 0x1c] = keybits
    struct.pack_into('<I', d, s + 0x38, 0xFFFFFFFF)
    struct.pack_into('<4f', d, s + 0x40, Q, Q, Q, 0.0)
    struct.pack_into('<4f', d, s + 0x50, 1 / Q, 1 / Q, 1 / Q, 0.0)
    td.rel(s + 0x60, o_tags, len(tags))
    struct.pack_into('<qii', d, s + 0x68, o_nodes - (s + 0x68), len(tnodes), 0)
    struct.pack_into('<IIIB', d, s + 0x78, 1, 0, 0, 1)
    td.rel(s + 0x88, o_secs, nsec)
    for i, sc in enumerate(secs):
        g = o_secs + 64 * i
        for j, key in enumerate(['nodes', 'prims', 'vb', 'interior']):
            o, n = offs[(key, i)]; td.rel(g + 8 * j, o, n)
        struct.pack_into('<3I', d, g + 0x20, *[int(x) & 0xFFFFFFFF for x in sc['off']])
        struct.pack_into('<3f', d, g + 0x2c, *[float(x) for x in sc['inv']])
        struct.pack_into('<3h', d, g + 0x38, *[int(x) for x in sc['bitoff']])
    lo = np.min([sc['lo'] for sc in secs], 0); hi = np.max([sc['hi'] for sc in secs], 0)
    return s, lo, hi


def _sec(tag, body, container=False):
    return struct.pack('>I', (len(body) + 8) | (0 if container else 0x40000000)) + tag.encode() + body


def tagfile(td: Data) -> bytes:
    """TAG0 tagfile: the template's SDKV and TYPE sections, td's DATA and ITEM (padded to 16 bytes via TPAD)."""
    t = td.tmpl; d0 = t.d
    td.data.extend(b'\0' * (al(len(td.data)) - len(td.data)))
    subs = b''.join(_sec(k, d0[slice(*t.sec[k])]) for k in ['TPTR', 'TST1', 'TNA1', 'FST1', 'TBDY', 'THSH'])
    item_blob = b''.join(struct.pack('<III', ty | (f << 24), o, c) for ty, f, o, c in td.items)
    def build(padn):
        return _sec('SDKV', d0[slice(*t.sec['SDKV'])]) + _sec('DATA', bytes(td.data)) + \
            _sec('TYPE', subs + _sec('TPAD', b'\0' * padn), container=True) + \
            _sec('INDX', _sec('ITEM', item_blob), container=True)
    body = build(0)
    body = build((-(len(body) + 8)) % 16)
    return _sec('TAG0', body, container=True)


def write(V, T, template_path, material=(7, 0x200)):
    """A standalone .bphsh. V: (n,3) metres, T: (m,3) triangles (counter-clockwise from outside)."""
    td = Data(Tag(open(template_path, 'rb').read()))
    add_mesh(td, V, T)
    tag0 = tagfile(td)
    mats = struct.pack('<QQ', *material)
    filt = b'\xff' * 16
    o_tag = 0x30; o_mat = o_tag + len(tag0); o_flt = o_mat + len(mats); size = o_flt + len(filt)
    hdr = b'Phive\0' + bytes([1, 0, 0xff, 0xfe, 0, 4]) + struct.pack('<9I', o_tag, o_mat, o_flt, size, len(tag0), len(mats), len(filt), 0, 0)
    return hdr.ljust(0x30, b'\0') + tag0 + mats + filt


if __name__ == '__main__':
    # self test: a 40 m x 40 m floor of 2 m quads (as triangles) at y = 0
    n = 20
    xs = np.linspace(-20, 20, n + 1)
    V = np.array([[x, 0.0, z] for z in xs for x in xs])
    T = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i; b = a + 1; c = a + n + 1; d = c + 1
            T += [(a, c, d), (a, d, b)]
    out = write(V, T, sys.argv[1])
    open(sys.argv[2], 'wb').write(out)
    print(len(out))
