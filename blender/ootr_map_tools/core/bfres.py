"""BFRES (v10, Switch 2) model reader for the map tools (no bpy). Layout: RESEARCH_NOTES.md §80f.
read_model(data) -> Model with one Mesh per shape (LOD 0): positions, normals, UV sets, vertex colours, triangles,
material index; and the materials with their texture names. Static models (map pieces) are rigid: a shape with a
bone and no skinning is placed by that bone's world matrix (applied here)."""
import struct
from dataclasses import dataclass, field

import numpy as np


def u8(d, o): return d[o]
def u16(d, o): return struct.unpack_from('<H', d, o)[0]
def u32(d, o): return struct.unpack_from('<I', d, o)[0]
def u64(d, o): return struct.unpack_from('<Q', d, o)[0]


def string(d, o):
    return d[o + 2:o + 2 + u16(d, o)].decode('utf-8', 'replace') if o else ''


# vertex formats: high byte = layout -> (bits per component..., numpy dtype), low byte = 1 unorm 2 snorm 3 uint 4 sint 5 float
LAYOUT = {0x02: ((8,), 'u1'), 0x09: ((8, 8), 'u1'), 0x0b: ((8, 8, 8, 8), 'u1'), 0x0e: ((10, 10, 10, 2), None),
          0x0a: ((16,), 'u2'), 0x12: ((16, 16), 'u2'), 0x15: ((16, 16, 16, 16), 'u2'), 0x14: ((32,), 'u4'),
          0x17: ((32, 32), 'u4'), 0x18: ((32, 32, 32), 'u4'), 0x19: ((32, 32, 32, 32), 'u4')}


def decode_attr(raw, fmt):
    """raw [n, k] uint8 (the attribute's bytes per vertex) -> float [n, comps]"""
    layout, kind = fmt >> 8, fmt & 0xff
    bits, base = LAYOUT[layout]
    n = len(raw)
    if base is None:                                     # 10-10-10-2 packed in a u32
        w = raw[:, :4].copy().view('<u4').reshape(n)
        out = np.stack([(w >> s) & ((1 << b) - 1) for s, b in zip((0, 10, 20, 30), bits)], 1).astype(np.int64)
        if kind == 2:
            out[:, :3] = np.where(out[:, :3] >= 512, out[:, :3] - 1024, out[:, :3])
            return np.c_[out[:, :3] / 511.0, out[:, 3:]]
        return out / np.array([1023.0, 1023, 1023, 3])
    size = bits[0] // 8
    cnt = len(bits)
    if kind == 5:
        dt = {2: '<f2', 4: '<f4'}[size]
    else:
        dt = {1: 'u1', 2: '<u2', 4: '<u4'}[size] if kind in (1, 3) else {1: 'i1', 2: '<i2', 4: '<i4'}[size]
    v = raw[:, :size * cnt].copy().view(dt).reshape(n, cnt).astype(np.float64)
    if kind == 1: v /= float((1 << bits[0]) - 1)
    elif kind == 2: v = np.maximum(v / float((1 << (bits[0] - 1)) - 1), -1.0)
    return v


@dataclass
class Mesh:
    name: str
    material: int
    pos: np.ndarray
    tris: np.ndarray                          # (m, 3) int
    nrm: np.ndarray = None
    uvs: list = field(default_factory=list)   # [(attribute name, (n, 2))]
    col: np.ndarray = None                    # (n, 4) or None


@dataclass
class Material:
    name: str
    textures: list                            # texture names (Tex/<name>.bntx.zs)
    samplers: list                            # sampler names, same order (_a0 albedo, _n0 normal, ...)


@dataclass
class Model:
    name: str
    meshes: list
    materials: list


def _dict_names(d, o):
    """ResDic -> names in entry order (entries after the root, 0x10 each: u32 ref, u16 left, u16 right, u64 key)."""
    if not o: return []
    n = u32(d, o + 4)
    return [string(d, u64(d, o + 8 + 0x10 * (i + 1) + 8)) for i in range(n)]


def read_model(d: bytes, model_index: int = 0) -> Model:
    assert d[:4] == b'FRES', 'not a BFRES'
    fm = u64(d, 0x28) + 0x78 * model_index
    FV, SH, MT = u64(d, fm + 0x20), u64(d, fm + 0x28), u64(d, fm + 0x38)
    n_fvtx, n_shapes, n_mats = u16(d, fm + 0x68), u16(d, fm + 0x6a), u16(d, fm + 0x6c)
    gpu = u64(d, 0x170)
    mats = []
    for i in range(n_mats):
        m = MT + 0xb0 * i
        tn = u64(d, m + 0x20)
        ntex = d[m + 0xa3]
        mats.append(Material(string(d, u64(d, m + 8)), [string(d, u64(d, tn + 8 * k)) for k in range(ntex)],
                             _dict_names(d, u64(d, m + 0x38))))
    meshes = []
    for i in range(n_shapes):
        f = SH + 0x60 * i
        v = FV + 0x58 * u16(d, f + 0x56)
        at0 = u64(d, v + 8)
        n = u32(d, v + 0x50)
        nbuf = d[v + 0x4d]
        sizes = [u32(d, u64(d, v + 0x30) + 0x10 * b) for b in range(nbuf)]
        strides = [u32(d, u64(d, v + 0x38) + 0x10 * b) for b in range(nbuf)]
        starts, o = [], gpu + u32(d, v + 0x48)
        for b in range(nbuf):
            starts.append(o)
            o += (sizes[b] + 7) & ~7
        attrs = {}
        for a in range(d[v + 0x4c]):
            e = at0 + 0x10 * a
            name, fmt, off, buf = string(d, u64(d, e)), u32(d, e + 8), u16(d, e + 0xc), u16(d, e + 0xe)
            raw = np.frombuffer(d, np.uint8, n * strides[buf], starts[buf]).reshape(n, strides[buf])[:, off:off + 16]
            try:
                attrs[name] = decode_attr(raw, fmt)
            except (KeyError, ValueError):
                pass
        me = u64(d, f + 0x18)                                   # LOD 0
        ioff, fmt, cnt, first = u32(d, me + 0x20), u32(d, me + 0x28), u32(d, me + 0x2c), u32(d, me + 0x30)
        idt = '<u2' if fmt == 1 else '<u4' if fmt == 2 else 'u1'
        idx = np.frombuffer(d, idt, cnt, gpu + ioff).astype(np.int64) + first
        if '_p0' not in attrs: continue
        # (the vertex buffer is shared by every LOD: keep the vertices LOD 0 uses, renumbered)
        idx = idx[:len(idx) // 3 * 3]
        used, remap = np.unique(idx, return_inverse=True)
        pick = lambda a: a[used]
        mesh = Mesh(string(d, u64(d, f + 8)), u16(d, f + 0x52), pick(attrs['_p0'])[:, :3].copy(), remap.reshape(-1, 3),
                    nrm=pick(attrs['_n0'])[:, :3].copy() if '_n0' in attrs else None,
                    uvs=[(k, pick(attrs[k])[:, :2]) for k in sorted(attrs) if k.startswith('_u')],
                    col=pick(attrs['_c0'])[:, :4] if '_c0' in attrs and attrs['_c0'].shape[1] >= 3 else None)
        meshes.append(mesh)
    return Model(string(d, u64(d, fm + 8)), meshes, mats)


if __name__ == '__main__':          # python -m ootr_map_tools.core.bfres <game folder> <Model/...bfres.zs>
    import sys
    from .game import Game
    g = Game(sys.argv[1])
    md = read_model(g.read(sys.argv[2]))
    print(md.name, len(md.meshes), 'meshes', len(md.materials), 'materials')
    for m in md.meshes[:10]:
        print(f'  {m.name:30s} mat {m.material:2d} {md.materials[m.material].name:40s} v {len(m.pos):6d} t {len(m.tris):6d}'
              f' uv {[k for k, _ in m.uvs]} bounds {m.pos.min(0).round(2)} {m.pos.max(0).round(2)}')
    print('textures', md.materials[0].textures, md.materials[0].samplers)
