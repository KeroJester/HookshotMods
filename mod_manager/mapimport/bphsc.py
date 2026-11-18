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
HEADER_SIZES = 0x28c           # u32 section sizes (offset k+1 - offset k), parallel to the offsets


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


def quat_rotate_many(q, V):
    """_quat_rotate for every row of V at once."""
    x, y, z, w = q
    u = np.array([x, y, z], float); V = np.asarray(V, float).reshape(-1, 3)
    return V + 2 * np.cross(u, np.cross(u, V) + w * V)


def box_mesh(half=0.25):
    """A closed box (12 triangles, counter-clockwise from outside) around the origin."""
    h = half
    V = np.array([[x, y, z] for x in (-h, h) for y in (-h, h) for z in (-h, h)], float)
    T = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                  [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, T


def rebuild_layer(tag0: bytes, shapes: Dict[int, tuple]) -> bytes:
    """tag0: one layer tagfile of a template; shapes: {instance index: (V, T) in the instance's space}. Instances
    not listed get a small closed box (their original shapes are not copied)."""
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
        V, T = shapes.get(k) or box_mesh()
        q = struct.unpack_from("<4f", inst[k], 0)
        tr = np.array(struct.unpack_from("<3f", inst[k], 0x10))
        sc = np.array(struct.unpack_from("<3f", inst[k], 0x20))
        W = quat_rotate_many(q, np.asarray(V, float) * sc) + tr
        boxes.append((W.min(0), W.max(0)))
    tree_nodes = _instance_tree(boxes)
    o_tree = td.put("hkcdSimdTreeNamespace::Node", b"".join(tree_nodes), len(tree_nodes))
    for k in range(icount):
        V, T = shapes.get(k) or box_mesh()
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
    if len(boxes) <= 4:                                  # (the game's compounds: the root itself is the leaf)
        leaf = bytearray(nodes[2])
        struct.pack_into("<II", leaf, 0x78, 0, len(boxes))
        nodes = [nodes[0], bytes(leaf)]
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
    # the section sizes (parallel to the offsets, at 0x28c): size k = offset k+1 - offset k
    offs = list(struct.unpack_from("<24I", out, HEADER_OFFSETS))
    for k in range(23):
        if offs[k + 1] == 0: break
        struct.pack_into("<I", out, HEADER_SIZES + 4 * k, offs[k + 1] - offs[k])
    return bytes(out)


def patch_layer(tag0: bytes, shapes: Dict[int, tuple]) -> bytes:
    """Surgical version of rebuild_layer: the template layer's DATA and items stay byte for byte; each listed
    instance gets a new mesh shape appended after them and its shape pointer moved to it; the instance tree and the
    compound's AABB are updated (a new tree array is appended if the node count changes)."""
    t = Tag(_wrap(tag0))
    D, E = t.data
    td = phsh.Data(t)
    td.data = bytearray(t.d[D:E])
    td.items = [(it['type'], it['flags'], it['off'], it['count']) for it in t.items]
    dd = td.data
    comp = 0                                               # (item 1, the compound, is at DATA offset 0)
    ioff, icount, _cap = struct.unpack_from("<qii", dd, comp + 0x38)
    inst = comp + 0x38 + ioff
    boxes = []
    for k in range(icount):
        e = inst + 0x40 * k
        q = struct.unpack_from("<4f", dd, e)
        tr = np.array(struct.unpack_from("<3f", dd, e + 0x10)); sc = np.array(struct.unpack_from("<3f", dd, e + 0x20))
        if k in shapes:
            V, T = shapes[k]
            s, _lo, _hi = phsh.add_mesh(td, V, T)
            dd = td.data
            struct.pack_into("<q", dd, e + 0x30, s - (e + 0x30))
        else:
            sa = e + 0x30 + struct.unpack_from("<q", dd, e + 0x30)[0]
            V, _T = phsh.read_mesh(bytes(dd), sa) if t.tname(next(i['type'] for i in t.items if i['off'] == sa)) == 'hknpMeshShape' else (None, None)
            if V is None:                                   # (not a mesh: keep its old tree box)
                V = None
        if V is not None:
            W = quat_rotate_many(q, np.asarray(V, float) * sc) + tr
            boxes.append((W.min(0), W.max(0)))
        else:
            boxes.append(None)
    # instance tree
    soff, scount, _ = struct.unpack_from("<qii", dd, comp + 0x50)
    old_tree = comp + 0x50 + soff
    if any(b is None for b in boxes):
        raise ValueError("instance without a mesh shape: tree box unknown")
    nodes = _instance_tree(boxes)
    blob = b"".join(nodes)
    if len(nodes) == scount:
        dd[old_tree:old_tree + len(blob)] = blob
    else:
        o = td.put("hkcdSimdTreeNamespace::Node", blob, len(nodes)); dd = td.data
        struct.pack_into("<qii", dd, comp + 0x50, o - (comp + 0x50), len(nodes), 0)
    lo = np.min([b[0] for b in boxes], 0); hi = np.max([b[1] for b in boxes], 0)
    struct.pack_into("<6f", dd, comp + 0x78, hi[0], hi[1], hi[2], lo[2], lo[0], lo[1])
    return phsh.tagfile(td)


def rebuild_layer2(tag0: bytes, shapes: Dict[int, tuple]) -> bytes:
    """Re-serialises a layer in the game's own layout: compound, instances, instance tree, then every instance's mesh
    root (in instance order), then the meshes' arrays. Listed instances get the given triangles; the others keep their
    own (read back from the template). Only for layers whose instances are all mesh shapes."""
    t = Tag(_wrap(tag0))
    D = t.data[0]
    d = t.d
    comp = bytearray(d[D:D + 0xb8])
    ioff, icount, _cap = struct.unpack_from("<qii", comp, 0x38)
    inst = [bytearray(d[D + 0x38 + ioff + 0x40 * k:D + 0x38 + ioff + 0x40 * (k + 1)]) for k in range(icount)]
    meshes = []
    for k in range(icount):
        if k in shapes:
            meshes.append(shapes[k])
        else:
            e = D + 0x38 + ioff + 0x40 * k + 0x30
            sa = e + struct.unpack_from("<q", d, e)[0]
            meshes.append(phsh.read_mesh(d, sa, with_tags=True, quads=True))
    boxes = []
    for k, m in enumerate(meshes):
        V = m[0]
        q = struct.unpack_from("<4f", inst[k], 0)
        tr = np.array(struct.unpack_from("<3f", inst[k], 0x10)); sc = np.array(struct.unpack_from("<3f", inst[k], 0x20))
        W = quat_rotate_many(q, np.asarray(V, float) * sc) + tr
        boxes.append((W.min(0), W.max(0)))
    td = phsh.Data(t)
    o_comp = td.put("hknpCompoundShape", bytes(comp), 1, 0x10)
    o_inst = td.put("hkFreeListArrayElement<hknpShapeInstance>", b"".join(inst), icount)
    tree = _instance_tree(boxes)
    o_tree = td.put("hkcdSimdTreeNamespace::Node", b"".join(tree), len(tree))
    roots = [td.put("hknpMeshShape", bytes(0xa0), 1, 0x10) for _ in meshes]
    for m, r in zip(meshes, roots):
        phsh.add_mesh(td, m[0], m[1], root_at=r, tags=m[2] if len(m) > 2 else None)
    dd = td.data
    struct.pack_into("<qii", dd, o_comp + 0x38, o_inst - (o_comp + 0x38), icount, 0)
    struct.pack_into("<iI", dd, o_comp + 0x48, -1, icount)
    struct.pack_into("<qii", dd, o_comp + 0x50, o_tree - (o_comp + 0x50), len(tree), 0)
    struct.pack_into("<IIIB", dd, o_comp + 0x60, 1, 0, 0, 1)
    lo = np.min([b[0] for b in boxes], 0); hi = np.max([b[1] for b in boxes], 0)
    struct.pack_into("<6f", dd, o_comp + 0x78, hi[0], hi[1], hi[2], lo[2], lo[0], lo[1])
    for k in range(icount):
        f = o_inst + 0x40 * k + 0x30
        struct.pack_into("<q", dd, f, roots[k] - f)
    return phsh.tagfile(td)
