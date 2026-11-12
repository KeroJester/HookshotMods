#!/usr/bin/env python3
"""Grezzo CMB model reader (Majora's Mask 3D, header version 10).

Header: "cmb ", u32 size, u32 version, u32 ?, char name[16], u32 index count, then chunk offsets
  skl, qtrs, mats, tex, sklm, luts, vatr, index data, texture data.
skl : u32 count, u32 ?, bones 0x2C {i16 id (0x1000 = used for skinning), i16 parent, f32 scale[3],
      f32 euler[3], f32 trans[3], u32 ?}
mats: u32 count, materials 0x16C each (then the texture combiner table); texture binders (0x18 each) at +0x10 and +0x28:
      {i16 texture, u16 ?, u16 min, u16 mag, u16 wrapS, u16 wrapT, f32 minLod, f32 bias, u8 border[4]}
tex : u32 count, entries 0x24 {u32 size, u16 mips, u8 isEtc, u8 ?, u16 w, u16 h, u16 glFormat, u16 glType,
      u32 offset (from texture data), char name[16]}
sklm: u32 mshs offset, u32 shp offset (relative to sklm)
mshs: u32 count, u16, u16, then meshes 0x0C {u16 shape, u8 material, u8 id, u32, u32}
shp : u32 count, u32 ?, u16 sepd offsets (relative to shp)
sepd: u16 primSetCount, u16 attribute flags, f32 center[3], f32 offset[3], 9 attributes 0x1C
      {u32 start, f32 scale, u16 GL type, u16 mode (0 array, 1 constant), f32 constant[4]}
      (position, normal, tangent, color, uv0, uv1, uv2, bone index, bone weight),
      u16 bone dimension, u16 constant flags, u16 prms offsets...
prms: u32 ?, u16 skinning mode (0 single, 1 rigid, 2 smooth), u16 bone table count,
      u32 bone table offset, u32 prm offset (relative to prms); bone table u16[]
prm : u32 visible, u32 primitive mode, u32 GL index type, u16 count, u16 offset (16-bit units)
vatr: u32 vertex count, then 9 x {u32 size, u32 offset (relative to vatr)}
"""

import math
import struct
from typing import Dict, List

import numpy as np

GL_SIZE = {0x1400: 1, 0x1401: 1, 0x1402: 2, 0x1403: 2, 0x1406: 4}
GL_FMT = {0x1400: "b", 0x1401: "B", 0x1402: "h", 0x1403: "H", 0x1406: "f"}
ATTRS = ["pos", "nrm", "tan", "col", "uv0", "uv1", "uv2", "bidx", "bwt"]
ATTR_COMPONENTS = {"pos": 3, "nrm": 3, "tan": 3, "col": 4, "uv0": 2, "uv1": 2, "uv2": 2}


# ---- 3DS textures ----------------------------------------------------------------------------------
def _morton(i):
    x = (i & 1) | ((i >> 1) & 2) | ((i >> 2) & 4)
    y = ((i >> 1) & 1) | ((i >> 2) & 2) | ((i >> 3) & 4)
    return x, y


_MORTON = [_morton(i) for i in range(64)]
ETC_MOD = [[2, 8, -2, -8], [5, 17, -5, -17], [9, 29, -9, -29], [13, 42, -13, -42],
           [18, 60, -18, -60], [24, 80, -24, -80], [33, 106, -33, -106], [47, 183, -47, -183]]


def _etc1_block(blk: int):
    """64-bit ETC1 block -> 4x4x3."""
    out = np.zeros((4, 4, 3), np.int32)
    diff = (blk >> 33) & 1
    flip = (blk >> 32) & 1
    t1, t2 = (blk >> 37) & 7, (blk >> 34) & 7
    if diff:
        r1, g1, b1 = (blk >> 59) & 31, (blk >> 51) & 31, (blk >> 43) & 31
        dr, dg, db = [((blk >> s) & 7) - (8 if (blk >> s) & 4 else 0) for s in (56, 48, 40)]
        c1 = [(v << 3) | (v >> 2) for v in (r1, g1, b1)]
        c2 = [((v + dv) << 3) | ((v + dv) >> 2) for v, dv in zip((r1, g1, b1), (dr, dg, db))]
    else:
        c1 = [((blk >> s) & 15) * 17 for s in (60, 52, 44)]
        c2 = [((blk >> s) & 15) * 17 for s in (56, 48, 40)]
    for x in range(4):
        for y in range(4):
            k = x * 4 + y
            sub2 = (y >= 2) if flip else (x >= 2)
            base, tbl = (c2, t2) if sub2 else (c1, t1)
            idx = ((blk >> k) & 1) | (((blk >> (k + 16)) & 1) << 1)
            m = ETC_MOD[tbl][idx]
            out[y, x] = [min(255, max(0, c + m)) for c in base]
    return out


def decode_texture(data: bytes, w: int, h: int, gl_format: int, gl_type: int) -> np.ndarray:
    """3DS (PICA200) tiled texture -> [h, w, 4] uint8, top row first."""
    img = np.zeros((h, w, 4), np.uint8)
    img[..., 3] = 255
    if gl_format in (0x675A, 0x675B):          # ETC1 / ETC1A4: 8x8 tiles of 2x2 4x4 blocks
        alpha = gl_format == 0x675B
        p = 0
        for ty in range(0, h, 8):
            for tx in range(0, w, 8):
                for by, bx in ((0, 0), (0, 4), (4, 0), (4, 4)):
                    a = None
                    if alpha:
                        a = struct.unpack_from("<Q", data, p)[0]; p += 8
                    blk = struct.unpack_from("<Q", data, p)[0]; p += 8
                    px = _etc1_block(blk)
                    for x in range(4):
                        for y in range(4):
                            X, Y = tx + bx + x, ty + by + y
                            if X < w and Y < h:
                                img[Y, X, :3] = px[y, x]
                                if a is not None:
                                    img[Y, X, 3] = ((a >> (4 * (x * 4 + y))) & 15) * 17
        return img[::-1].copy()
    bpp = {(0x6754, 0x8363): 2, (0x6752, 0x1401): 4, (0x6754, 0x1401): 3, (0x6752, 0x8033): 2,
           (0x6752, 0x8034): 2, (0x6757, 0x1401): 1, (0x6756, 0x1401): 1, (0x6758, 0x1401): 2}.get((gl_format, gl_type), 2)
    p = 0
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for i in range(64):
                x, y = _MORTON[i]
                X, Y = tx + x, ty + y
                v = data[p:p + bpp]; p += bpp
                if X >= w or Y >= h or len(v) < bpp:
                    continue
                if (gl_format, gl_type) == (0x6754, 0x8363):          # RGB565
                    c = v[0] | v[1] << 8
                    img[Y, X, :3] = ((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)
                elif (gl_format, gl_type) == (0x6752, 0x1401):        # RGBA8 (stored ABGR)
                    img[Y, X] = (v[3], v[2], v[1], v[0])
                elif (gl_format, gl_type) == (0x6754, 0x1401):        # RGB8 (stored BGR)
                    img[Y, X, :3] = (v[2], v[1], v[0])
                elif (gl_format, gl_type) == (0x6752, 0x8034):        # RGBA5551
                    c = v[0] | v[1] << 8
                    img[Y, X] = ((c >> 11) * 255 // 31, ((c >> 6) & 31) * 255 // 31, ((c >> 1) & 31) * 255 // 31, 255 if c & 1 else 0)
                elif (gl_format, gl_type) == (0x6752, 0x8033):        # RGBA4444
                    c = v[0] | v[1] << 8
                    img[Y, X] = ((c >> 12) * 17, ((c >> 8) & 15) * 17, ((c >> 4) & 15) * 17, (c & 15) * 17)
                elif gl_format in (0x6757, 0x6756):                    # L8 / A8
                    img[Y, X, :3] = v[0]
                elif gl_format == 0x6758:                              # LA8
                    img[Y, X] = (v[1], v[1], v[1], v[0])
    return img[::-1].copy()


# ---- model ------------------------------------------------------------------------------------------
def _euler_zyx(rx, ry, rz):
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    X = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Z @ Y @ X


class CMB:
    def __init__(self, data: bytes):
        self.d = d = data
        assert d[:4] == b"cmb "
        self.version = struct.unpack_from("<I", d, 8)[0]
        self.name = d[0x10:0x20].split(b"\0")[0].decode()
        (self.n_indices, self.o_skl, self.o_qtrs, self.o_mats, self.o_tex, self.o_sklm, self.o_luts,
         self.o_vatr, self.o_idx, self.o_texdata) = struct.unpack_from("<10I", d, 0x20)
        self._bones()
        self._textures()
        self._materials()
        self._vatr()
        self._shapes()

    def u16(self, o): return struct.unpack_from("<H", self.d, o)[0]
    def u32(self, o): return struct.unpack_from("<I", self.d, o)[0]

    def _bones(self):
        d, o = self.d, self.o_skl
        n = self.u32(o + 8)
        self.bones = []
        for i in range(n):
            b = o + 0x10 + i * 0x2C
            bid, par = struct.unpack_from("<hh", d, b)
            self.bones.append(dict(id=bid & 0xFFF, skinned=bool(bid & 0x1000), parent=par,
                                   scale=struct.unpack_from("<3f", d, b + 4), rot=struct.unpack_from("<3f", d, b + 16),
                                   trans=struct.unpack_from("<3f", d, b + 28)))
        self.bone_world = []
        for b in self.bones:
            m = np.eye(4)
            m[:3, :3] = _euler_zyx(*b["rot"]) @ np.diag(b["scale"])
            m[:3, 3] = b["trans"]
            self.bone_world.append(self.bone_world[b["parent"]] @ m if b["parent"] >= 0 else m)

    def _textures(self):
        d, o = self.d, self.o_tex
        self.textures = []
        for i in range(self.u32(o + 8)):
            e = o + 0xC + i * 0x24
            size, mips, etc, _c, w, h, fmt, typ, off = struct.unpack_from("<IHBBHHHHI", d, e)
            name = d[e + 0x14:e + 0x24].split(b"\0")[0].decode()
            self.textures.append(dict(name=name, w=w, h=h, fmt=fmt, type=typ, size=size,
                                      off=self.o_texdata + off, image=None))

    def texture_image(self, i):
        t = self.textures[i]
        if t["image"] is None:
            t["image"] = decode_texture(self.d[t["off"]:t["off"] + t["size"]], t["w"], t["h"], t["fmt"], t["type"])
        return t["image"]

    def _materials(self):
        d, o = self.d, self.o_mats
        n = self.u32(o + 8)
        stride = 0x16C            # MM3D material size; texture combiners follow the material array
        self.materials = []
        for i in range(n):
            m = o + 0xC + i * stride
            binders = []
            for k in range(3):
                b = m + 0x10 + 0x18 * k
                tex, _u, mn, mg, ws, wt = struct.unpack_from("<hHHHHH", d, b)
                binders.append(dict(tex=tex, wrap=(ws, wt)))
            self.materials.append(dict(binders=binders, raw=d[m:m + stride]))

    def _vatr(self):
        o = self.o_vatr
        self.n_vertices = self.u32(o + 8)
        self.streams = {}
        for k, name in enumerate(ATTRS):
            size, off = struct.unpack_from("<II", self.d, o + 0xC + 8 * k)
            self.streams[name] = (o + off, size)

    def _shapes(self):
        d = self.d
        sklm = self.o_sklm
        mshs = sklm + self.u32(sklm + 8)
        shp = sklm + self.u32(sklm + 12)
        n_mesh = self.u32(mshs + 8)
        self.meshes = []
        for i in range(n_mesh):
            e = mshs + 0x10 + 0x0C * i
            shape, mat, mid = struct.unpack_from("<HBB", d, e)
            self.meshes.append(dict(shape=shape, material=mat, id=mid))
        n_shp = self.u32(shp + 8)
        self.shapes = []
        for i in range(n_shp):
            so = shp + self.u16(shp + 0x10 + 2 * i)
            self.shapes.append(self._sepd(so))

    def _sepd(self, so):
        d = self.d
        n_prims, flags = struct.unpack_from("<HH", d, so + 8)
        attrs = {}
        for k, name in enumerate(ATTRS):
            a = so + 0x24 + 0x1C * k
            start, scale, gtype, mode = struct.unpack_from("<IfHH", d, a)
            const = struct.unpack_from("<4f", d, a + 12)
            attrs[name] = dict(start=start, scale=scale, type=gtype, mode=mode, const=const)
        bone_dim, _cflags = struct.unpack_from("<HH", d, so + 0x120)
        prms_offs = [self.u16(so + 0x124 + 2 * k) for k in range(n_prims)]
        prims = []
        for po in prms_offs:
            p = so + po
            skin, n_bt = struct.unpack_from("<HH", d, p + 0x0C)
            bt_off, prm_off = struct.unpack_from("<II", d, p + 0x10)
            bone_table = [self.u16(p + bt_off + 2 * k) for k in range(n_bt)]
            pr = p + prm_off
            _vis, mode, itype, count, ioff = struct.unpack_from("<IIIHH", d, pr + 8)
            isz = GL_SIZE[itype]
            ib = self.o_idx + ioff * 2           # offset is in 16-bit units whatever the index size
            idx = list(struct.unpack_from(f"<{count}{GL_FMT[itype]}", d, ib))
            prims.append(dict(skin=skin, bone_table=bone_table, mode=mode, indices=idx))
        return dict(attrs=attrs, bone_dim=bone_dim, prims=prims, flags=flags)

    def _attr(self, attrs, name, i, comps):
        a = attrs[name]
        if a["mode"] == 1 or self.streams[name][1] == 0:
            return [c * a["scale"] for c in a["const"][:comps]]
        base, _size = self.streams[name]
        sz = GL_SIZE[a["type"]]
        o = base + a["start"] + i * sz * comps
        return [v * a["scale"] for v in struct.unpack_from(f"<{comps}{GL_FMT[a['type']]}", self.d, o)]

    def mesh_triangles(self, mesh_index):
        """Triangles of one mesh: list of dicts {p: model-space bind position, n, uv, bones: [(bone, weight)]}."""
        mesh = self.meshes[mesh_index]
        sh = self.shapes[mesh["shape"]]
        A = sh["attrs"]
        dim = max(1, sh["bone_dim"])
        tris = []
        for pr in sh["prims"]:
            verts = {}

            def vert(i):
                if i in verts:
                    return verts[i]
                p = np.array(self._attr(A, "pos", i, 3))
                n = np.array(self._attr(A, "nrm", i, 3)) if sh["flags"] & 2 else np.array([0.0, 1.0, 0.0])
                uv = self._attr(A, "uv0", i, 2) if sh["flags"] & 0x10 else [0.0, 0.0]
                bi = [int(round(v)) for v in self._attr(A, "bidx", i, dim)] if self.streams["bidx"][1] else [0]
                bw = self._attr(A, "bwt", i, dim) if (pr["skin"] == 2 and self.streams["bwt"][1]) else [1.0] + [0.0] * (dim - 1)
                bones = [(pr["bone_table"][b] if b < len(pr["bone_table"]) else 0, w) for b, w in zip(bi, bw) if w > 0]
                if pr["skin"] in (0, 1):                     # single / rigid: vertices are in bone space
                    bone = pr["bone_table"][0] if pr["skin"] == 0 else bones[0][0]
                    M = self.bone_world[bone]
                    p = M[:3, :3] @ p + M[:3, 3]
                    n = M[:3, :3] @ n
                    bones = [(bone, 1.0)]
                v = dict(p=p, n=n / (np.linalg.norm(n) or 1), uv=uv, bones=bones)
                verts[i] = v
                return v

            idx = pr["indices"]
            if pr["mode"] == 0:                              # triangles
                for k in range(0, len(idx) - 2, 3):
                    tris.append((vert(idx[k]), vert(idx[k + 1]), vert(idx[k + 2])))
        return tris


# ---- CSAB animations ------------------------------------------------------------------------------
class CSAB:
    """Grezzo CSAB (version 5, MM3D) skeletal animation.
    +0x20 u32 anim header offset (A). A+0x10 u32 duration (last frame), A+0x14 loop mode, A+0x18 node count,
    A+0x1C bone-table count, A+0x20 s16 bone->node table, then (4-aligned) u32 node offsets (from A).
    Node "anod": u16 bone, u16 flags, u16 track offsets[9] (tx ty tz rx ry rz sx sy sz; from the node).
    Track: u8 ?, u8 type, u16 key count, f32 scale, f32 bias, then u16 values (one per frame for type 1):
    value = bias + raw * scale (rotations: scale 2pi/4096, so wrap is harmless)."""

    def __init__(self, data: bytes):
        d = self.d = data
        assert d[:4] == b"csab"
        A = struct.unpack_from("<I", d, 0x20)[0]
        self.duration, self.loop, nn, nb = struct.unpack_from("<IIII", d, A + 0x10)
        self.bone_to_node = struct.unpack_from(f"<{nb}h", d, A + 0x20)
        oa = (A + 0x20 + 2 * nb + 3) & ~3
        offs = struct.unpack_from(f"<{nn}I", d, oa)
        self.nodes = {}
        for o in offs:
            n = A + o
            bone, flags = struct.unpack_from("<HH", d, n + 4)
            tracks = []
            for to in struct.unpack_from("<9H", d, n + 8):
                if to == 0:
                    tracks.append(None)
                    continue
                t = n + to
                _f, typ, cnt = struct.unpack_from("<BBH", d, t)
                sc, bi = struct.unpack_from("<ff", d, t + 4)
                if typ == 1:
                    raw = np.array(struct.unpack_from(f"<{cnt}H", d, t + 12), float)
                    tracks.append(dict(type=typ, values=bi + raw * sc))
                else:
                    tracks.append(dict(type=typ, raw=(t, cnt, sc, bi)))
            self.nodes[bone] = dict(flags=flags, tracks=tracks)

    def sample(self, bone, frame, bind):
        """(scale, euler, trans) for a bone at an integer frame; missing tracks use the bind values."""
        node = self.nodes.get(bone)
        out = [list(bind["scale"]), list(bind["rot"]), list(bind["trans"])]
        if node is None:
            return out
        for k, tr in enumerate(node["tracks"]):
            if tr is None or "values" not in tr:
                continue
            v = tr["values"]
            out[(k // 3) == 0 and 2 or (k // 3 == 1 and 1 or 0)][k % 3] = float(v[min(frame, len(v) - 1)])
        return out


def pose_world(model: "CMB", anim: "CSAB", frame: int):
    """World matrices of the model's bones posed by an animation frame."""
    W = []
    for i, b in enumerate(model.bones):
        sc, rot, tr = anim.sample(i, frame, b) if anim else (b["scale"], b["rot"], b["trans"])
        m = np.eye(4)
        m[:3, :3] = _euler_zyx(*rot) @ np.diag(sc)
        m[:3, 3] = tr
        W.append(W[b["parent"]] @ m if b["parent"] >= 0 else m)
    return W
