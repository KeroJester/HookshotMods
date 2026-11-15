"""Static compound bodies (Phive/StaticCompoundBody/<folder>/<banc>.Nin_Ounce_NVN2.bphsc.zs) (RESEARCH_NOTES.md §80i).

A map's static actors exist only through this file: their Phive placement IDs are in its tables and their shapes are
embedded, one Havok tagfile per collision layer, each holding one hknpCompoundShape:
  hknpCompoundShape (0xb8): hknpShape header (+0x18 type 9, dispatch 2, +0x1c key bits), +0x38 instances
    (hkFreeListRelArray: rel offset, count, capacity; +0x48 firstFree -1, numAllocated), +0x50 hkcdSimdTree over the
    instances (as in hknpMeshShape), +0x70 estimated shape keys, +0x74 isMutable, enableSAH3, +0x76 radius (half),
    +0x78 aabb (hkFloat3Pair: max x, y, z, then min z, x, y), +0xb0 0xffffffff
  hknpShapeInstance (0x40): rotation (quaternion xyzw), translation, leafIdAndFlags, scale, shapeTag (indexes the
    file's instance table), padding, shape (hkRelPtr from the field), userData
For now a template file is reused whole and one layer's tagfile is rebuilt with new shapes for its instances (same
instances, tags and placement tables), which is enough for a map whose actors take the template's placement IDs.
"""
import struct
from typing import Dict, List

import numpy as np

from . import phsh
from .hktag import Tag

HEADER_OFFSETS = 0x0c          # u32 section offsets follow the Phive magic / version


def _wrap(tag0: bytes) -> bytes:
    return b"Phive\0" + bytes(6) + struct.pack("<I", 0x30) + bytes(0x30 - 16) + tag0


def layer_tagfiles(d: bytes):
    """-> [(offset, size)] of the Havok tagfiles in a .bphsc."""
    out = []
    o = 0
    while True:
        o = d.find(b"TAG0", o)
        if o < 0: break
        start = o - 4
        size = struct.unpack_from(">I", d, start)[0] & 0x3FFFFFFF
        out.append((start, size))
        o = start + size
    return out


def _quat_rotate(q, v):
    x, y, z, w = q
    u = np.array([x, y, z]); v = np.asarray(v, float)
    return v + 2 * np.cross(u, np.cross(u, v) + w * v)


def rebuild_layer(tag0: bytes, shapes: Dict[int, tuple]) -> bytes:
    """tag0: one layer tagfile of a template; shapes: {instance index: (V, T) in the instance's space}. Instances
    not listed get a tiny stand-in triangle (their original shapes are not copied)."""
    t = Tag(_wrap(tag0))
    D = t.data[0]
    d = t.d
    comp = bytearray(d[D:D + 0xb8])
    ioff, icount, _cap = struct.unpack_from("<qii", comp, 0x38)
    inst = [bytearray(d[D + 0x38 + ioff + 0x40 * k:D + 0x38 + ioff + 0x40 * (k + 1)]) for k in range(icount)]
    td = phsh.Data(t)
    o_comp = td.put("hknpCompoundShape", bytes(comp), 1, 0x10)
    o_inst = td.put("hkFreeListArrayElement<hknpShapeInstance>", b"".join(inst), icount)
    boxes = []
    shape_at = []
    nodes_placeholder = None
    # the instance tree comes after the instances (as the game's files); its size depends only on the count
    from .phsh import _bvh4
    for k in range(icount):
        V, T = shapes.get(k, (np.array([[0, 0, 0], [0.01, 0, 0], [0, 0, 0.01]], float), np.array([[0, 2, 1]])))
        q = struct.unpack_from("<4f", inst[k], 0)
        tr = np.array(struct.unpack_from("<3f", inst[k], 0x10))
        sc = np.array(struct.unpack_from("<3f", inst[k], 0x20))
        W = np.array([_quat_rotate(q, np.asarray(v) * sc) for v in np.asarray(V, float)]) + tr
        boxes.append((W.min(0), W.max(0)))
    tree_nodes = _instance_tree(boxes)
    o_tree = td.put("hkcdSimdTreeNamespace::Node", b"".join(tree_nodes), len(tree_nodes))
    for k in range(icount):
        V, T = shapes.get(k, (np.array([[0, 0, 0], [0.01, 0, 0], [0, 0, 0.01]], float), np.array([[0, 2, 1]])))
        s, _lo, _hi = phsh.add_mesh(td, V, T)
        shape_at.append(s)
    dd = td.data
    struct.pack_into("<qii", dd, o_comp + 0x38, o_inst - (o_comp + 0x38), icount, 0)
    struct.pack_into("<iI", dd, o_comp + 0x48, -1, icount)
    struct.pack_into("<qii", dd, o_comp + 0x50, o_tree - (o_comp + 0x50), len(tree_nodes), 0)
    struct.pack_into("<IIIB", dd, o_comp + 0x60, 1, 0, 0, 1)
    lo = np.min([b[0] for b in boxes], 0); hi = np.max([b[1] for b in boxes], 0)
    struct.pack_into("<6f", dd, o_comp + 0x78, hi[0], hi[1], hi[2], lo[2], lo[0], lo[1])
    for k in range(icount):
        f = o_inst + 0x40 * k + 0x30
        struct.pack_into("<q", dd, f, shape_at[k] - f)
    return phsh.tagfile(td)


def _instance_tree(boxes):
    """hkcdSimdTree nodes over instance boxes (leaf data = instance indices), as phsh._simd_tree for sections."""
    secs = [dict(lo=np.asarray(a, float), hi=np.asarray(b, float)) for a, b in boxes]
    nodes = phsh._simd_tree(secs)
    return nodes


def replace_layer(bphsc: bytes, layer: int, new_tag0: bytes) -> bytes:
    """Puts a rebuilt tagfile in place of layer `layer`, moving what follows and the header offsets after it."""
    tags = layer_tagfiles(bphsc)
    start, size = tags[layer]
    delta = len(new_tag0) - size
    out = bytearray(bphsc[:start] + new_tag0 + bphsc[start + size:])
    for k in range(24):
        at = HEADER_OFFSETS + 4 * k
        v = struct.unpack_from("<I", out, at)[0]
        if v > start: struct.pack_into("<I", out, at, v + delta)
    return bytes(out)
