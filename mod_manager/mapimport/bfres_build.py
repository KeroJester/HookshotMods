"""New static land models (BFRES v10.4), made from one of the game's own land models used as a template
(RESEARCH_NOTES.md §80f, §80h).

Append-only editing: the template's main section stays byte for byte (its old shapes, vertex buffers and materials
become unused), and new structures are appended after it: one vertex buffer + shape + mesh per mesh, one material
per texture set (a copy of a template material whose texture names are replaced), the shape and material
dictionaries and the new strings. The model header (FMDL) is pointed at the new arrays. The GPU data (index buffers,
then vertex buffers) and the memory pool block are laid out again after the main section, and the relocation table
is rebuilt (its sections group pointers by the region they point into: 0 main, 1 GPU data, 4 memory pool).

Structures used (offsets within each):
  header: +0x18 _RLT offset, +0x1c file size, +0x28 FMDL array, +0xa8 memory pool, +0x168 buffer info {u32, u32 GPU
          data size, u64 GPU data offset}
  FMDL (0x78): +0x20 FVTX array, +0x28 FSHP array, +0x30 shape dict, +0x38 FMAT array, +0x40 material dict,
          +0x68 u16 counts: vertex buffers, shapes, materials
  FVTX (0x58): +0x08 attributes, +0x10 attribute dict, +0x18 memory pool, +0x20 / +0x28 runtime, +0x30 buffer
          sizes {u32 size, ...} 0x10 each, +0x38 strides {u32 stride, ...} 0x10 each, +0x48 offset in the vertex
          data, +0x4c attribute count, +0x4d buffer count, +0x4e index, +0x50 vertex count
  FSHP (0x60): +0x08 name, +0x10 FVTX, +0x18 meshes, +0x38 bounding boxes {centre, extent} 0x18 each, +0x40
          bounding spheres {centre, radius} 0x10 each, +0x50 u16 index, +0x52 material, +0x54 bone, +0x56 vertex
          buffer, +0x5b mesh (LOD) count
  mesh (0x38): +0x00 submeshes {u32 offset, u32 count}, +0x08 memory pool, +0x10 / +0x18 runtime, +0x20 index
          offset in the GPU data, +0x24 primitive (3 triangles), +0x28 index format (1 = u16), +0x2c index count,
          +0x30 first vertex, +0x34 submesh count
  FMAT (0xb0): +0x08 name, +0x20 texture names, +0x18 / +0x28 / +0x58 / +0x80 runtime, +0xa0 u16 index,
          +0xa3 u8 texture count
"""
import struct
from typing import Dict, List

import numpy as np

from . import resdic

TEMPLATE_SHAPE = 24          # Land_HrlCity_Ground_A_01: Mt_Land_HrlCity_Soil_A_01 (_p0 half4, _n0, _t0, _u0 half2)
STR_POOL_END = None          # (found from the _STR block)


def u16(d, o): return struct.unpack_from("<H", d, o)[0]
def u32(d, o): return struct.unpack_from("<I", d, o)[0]
def u64(d, o): return struct.unpack_from("<Q", d, o)[0]


def rlt_sections(d):
    """-> [(position, size, [pointer locations])] per relocation section."""
    r = u32(d, 0x18)
    n = u32(d, r + 8)
    e0 = r + 0x10 + 0x18 * n
    out = []
    for i in range(n):
        _p, pos, size, first, cnt = struct.unpack_from("<QIIII", d, r + 0x10 + 0x18 * i)
        locs = []
        for k in range(first, first + cnt):
            p, ns, nk, skip = struct.unpack_from("<IHBB", d, e0 + 8 * k)
            for s in range(ns):
                for j in range(nk):
                    locs.append(p + 8 * (s * (nk + skip) + j))
        out.append((pos, size, locs))
    return out


def pack_10_10_10_2(v):
    v = np.clip(np.asarray(v, float), -1, 1)
    x, y, z = (int(round(c * 511)) & 0x3FF for c in v[:3])
    return x | (y << 10) | (z << 20)


class Writer:
    def __init__(self, template: bytes):
        self.t = template
        assert template[:8] == b"FRES    " and u32(template, 8) == 0x000A0400
        self.secs = rlt_sections(template)
        self.main_end = self.secs[0][1]
        self.out = bytearray(template[:self.main_end])
        self.set0, self.set1, self.set4 = set(self.secs[0][2]), set(self.secs[1][2]), set(self.secs[4][2])
        self.ptrs_main = set(self.secs[0][2])                # locations of pointers into the main section
        self.ptrs_gpu = set(self.secs[1][2])
        self.ptrs_pool = set(self.secs[4][2])
        targets = sorted({u64(template, p) for p in self.secs[0][2]})
        self.targets = targets
        self.strings: Dict[str, int] = {}

    # ---- helpers
    def block_size(self, at):
        """Size of the template block at `at` (up to the next pointed-to address)."""
        i = np.searchsorted(self.targets, at, side="right")
        return (self.targets[i] if i < len(self.targets) else self.main_end) - at

    def alloc(self, data: bytes, align=8) -> int:
        self.out += b"\0" * ((-len(self.out)) % align)
        at = len(self.out)
        self.out += data
        return at

    def setp(self, loc, target, kind="main"):
        struct.pack_into("<Q", self.out, loc, target)
        {"main": self.ptrs_main, "gpu": self.ptrs_gpu, "pool": self.ptrs_pool}[kind].add(loc)

    def string(self, s: str) -> int:
        if s not in self.strings:
            b = s.encode()
            self.strings[s] = self.alloc(struct.pack("<H", len(b)) + b + b"\0", 2)
        return self.strings[s]

    def copy_struct(self, src, size, align=8):
        """Copies a template structure; its pointer slots stay registered (pointing at the template's blocks)."""
        at = self.alloc(self.t[src:src + size], align)
        for k in range(0, size, 8):
            if src + k in self.set0: self.ptrs_main.add(at + k)
            if src + k in self.set1: self.ptrs_gpu.add(at + k)
            if src + k in self.set4: self.ptrs_pool.add(at + k)
        return at

    def copy_block(self, loc_in_new, src_ptr_loc):
        """Gives the structure at loc_in_new its own copy of the block the template pointer at src_ptr_loc points to."""
        src = u64(self.t, src_ptr_loc)
        if not src: return 0
        size = self.block_size(src)
        at = self.copy_struct(src, size, 16)
        self.setp(loc_in_new, at)
        return at

    def copy_into(self, at, src, size):
        """Copies a template structure over already-allocated bytes at `at`, registering its pointer slots."""
        self.out[at:at + size] = self.t[src:src + size]
        for k in range(0, size, 8):
            if src + k in self.set0: self.ptrs_main.add(at + k)
            if src + k in self.set1: self.ptrs_gpu.add(at + k)
            if src + k in self.set4: self.ptrs_pool.add(at + k)

    # ---- build
    def build(self, meshes: List[dict], materials: List[dict]) -> bytes:
        """meshes: {name, material (index), pos (n,3) metres, nrm (n,3), tan (n,3), uv (n,2), idx (m,) u16};
        materials: {name, textures: [albedo, normal, roughness]}."""
        t = self.t
        fmdl = u64(t, 0x28)
        fvtx0, fshp0, fmat0 = u64(t, fmdl + 0x20), u64(t, fmdl + 0x28), u64(t, fmdl + 0x38)
        sh_t = fshp0 + 0x60 * TEMPLATE_SHAPE
        mat_t = fmat0 + 0xb0 * u16(t, sh_t + 0x52)
        vtx_t = fvtx0 + 0x58 * u16(t, sh_t + 0x56)
        mesh_t = u64(t, sh_t + 0x18)
        n, nm = len(meshes), len(materials)
        # ---- GPU data: indices first (section 1), then vertex buffers (section 2), as the template
        gpu = bytearray()
        idx_off, vtx_off = [], []
        for m in meshes:
            gpu += b"\0" * ((-len(gpu)) % 0x100); idx_off.append(len(gpu))
            gpu += np.asarray(m["idx"], "<u2").tobytes()
        gpu += b"\0" * ((-len(gpu)) % 0x1000)
        idx_size = len(gpu)
        for m in meshes:
            gpu += b"\0" * ((-len(gpu)) % 0x1000); vtx_off.append(len(gpu))
            k = len(m["pos"])
            v = np.zeros(k, dtype=[("p", "<f2", 4), ("n", "<u4"), ("t", "<u4"), ("u", "<f2", 2)])
            v["p"][:, :3] = m["pos"]; v["p"][:, 3] = 1.0
            v["n"] = [pack_10_10_10_2(x) for x in m["nrm"]]
            v["t"] = [pack_10_10_10_2(x) for x in m["tan"]]
            v["u"] = m["uv"]
            gpu += v.tobytes()
        gpu += b"\0" * ((-len(gpu)) % 0x1000)
        # ---- arrays first (each must be contiguous)
        fvtx_arr = self.alloc(bytes(0x58 * n), 8)
        fshp_arr = self.alloc(bytes(0x60 * n), 8)
        fmat_arr = self.alloc(bytes(0xb0 * nm), 8)
        mesh_arr = self.alloc(bytes(0x38 * n), 8)
        pool_loc = []                                   # pointer slots that point at the memory pool
        for i, m in enumerate(meshes):
            # vertex buffer
            fv = fvtx_arr + 0x58 * i
            self.copy_into(fv, vtx_t, 0x58)
            for f in (0x20, 0x28, 0x30, 0x38):
                self.copy_block(fv + f, vtx_t + f)
            struct.pack_into("<I", self.out, u64(self.out, fv + 0x30), 20 * len(m["pos"]))   # buffer size
            struct.pack_into("<I", self.out, u64(self.out, fv + 0x38), 20)                    # stride
            struct.pack_into("<I", self.out, fv + 0x48, vtx_off[i])
            struct.pack_into("<H", self.out, fv + 0x4e, i)
            struct.pack_into("<I", self.out, fv + 0x50, len(m["pos"]))
            pool_loc.append(fv + 0x18)
            # mesh (one LOD, one submesh)
            me = mesh_arr + 0x38 * i
            self.copy_into(me, mesh_t, 0x38)
            sub = self.alloc(struct.pack("<II", 0, len(m["idx"])) + bytes(8), 8)
            self.setp(me, sub)
            for f in (0x10, 0x18):
                self.copy_block(me + f, mesh_t + f)
            struct.pack_into("<IIIIII", self.out, me + 0x20, idx_off[i], 3, 1, len(m["idx"]), 0, 1)
            pool_loc.append(me + 0x08)
            # shape
            s = fshp_arr + 0x60 * i
            self.copy_into(s, sh_t, 0x60)
            self.setp(s + 0x08, self.string(m["name"]))
            self.setp(s + 0x10, fv)
            self.setp(s + 0x18, me)
            p = np.asarray(m["pos"], float)
            lo, hi = p.min(0), p.max(0)
            c, e = (lo + hi) / 2, (hi - lo) / 2
            r = float(np.linalg.norm(e))
            bb = self.alloc(struct.pack("<6f", *c, *e) * 2, 8)
            sp = self.alloc(struct.pack("<4f", *c, r) * 2, 8)
            self.setp(s + 0x38, bb)
            self.setp(s + 0x40, sp)
            struct.pack_into("<HHHH", self.out, s + 0x50, i, m["material"], 0, i)
            self.out[s + 0x5b] = 1
        for mi, mt in enumerate(materials):
            at = fmat_arr + 0xb0 * mi
            self.copy_into(at, mat_t, 0xb0)
            self.setp(at + 8, self.string(mt["name"]))
            names = self.alloc(bytes(8 * len(mt["textures"])), 8)
            for k, x in enumerate(mt["textures"]): self.setp(names + 8 * k, self.string(x))
            self.setp(at + 0x20, names)
            for f in (0x18, 0x28, 0x58, 0x80):
                self.copy_block(at + f, mat_t + f)
            struct.pack_into("<H", self.out, at + 0xa0, mi)
        # dictionaries
        empty = self.string("")
        def dic(names, ptrs):
            at = self.alloc(bytes(resdic.size(len(names))), 8)
            resdic.write(self.out, at, [x.encode() for x in names], ptrs, empty)
            for k in range(len(names) + 1): self.ptrs_main.add(at + 8 + 16 * k + 8)
            return at
        shp_dic = dic([m["name"] for m in meshes], [self.string(m["name"]) for m in meshes])
        mat_dic = dic([m["name"] for m in materials], [self.string(m["name"]) for m in materials])
        # model header
        self.setp(fmdl + 0x20, fvtx_arr)
        self.setp(fmdl + 0x28, fshp_arr)
        self.setp(fmdl + 0x30, shp_dic)
        self.setp(fmdl + 0x38, fmat_arr)
        self.setp(fmdl + 0x40, mat_dic)
        struct.pack_into("<HHH", self.out, fmdl + 0x68, n, n, nm)
        # ---- sections after the main one
        main_end = len(self.out)
        self.out += b"\0" * ((-len(self.out)) % 0x1000)
        gpu_at = len(self.out)
        self.out += gpu
        pool_at = len(self.out)
        old_pool = self.secs[4][0]
        self.out += t[old_pool:old_pool + self.secs[4][1]]
        # every pointer that pointed at the old memory pool / GPU data now points at the new ones
        for loc in list(self.ptrs_pool) + pool_loc:
            self.setp(loc, pool_at, "pool")
        for loc in self.ptrs_gpu:
            struct.pack_into("<Q", self.out, loc, gpu_at)
        struct.pack_into("<II", self.out, 0x168 + 4, len(gpu), 0)     # buffer info: GPU data size
        struct.pack_into("<Q", self.out, 0x170, gpu_at)
        # ---- relocation table (one entry per pointer)
        self.out += b"\0" * ((-len(self.out)) % 8)
        rlt_at = len(self.out)
        sec_ptrs = [sorted(p for p in self.ptrs_main if p < main_end), sorted(self.ptrs_gpu), [], [],
                    sorted(set(self.ptrs_pool)), []]
        ranges = [(0, main_end), (gpu_at, idx_size), (gpu_at + idx_size, len(gpu) - idx_size),
                  (pool_at, 0), (pool_at, self.secs[4][1]), (rlt_at, 0)]
        rlt = bytearray(b"_RLT" + struct.pack("<II", rlt_at, len(ranges)) + bytes(4))
        first = 0
        for (pos, size), ps in zip(ranges, sec_ptrs):
            rlt += struct.pack("<QIIII", 0, pos, size, first, len(ps)); first += len(ps)
        for ps in sec_ptrs:
            for p in ps: rlt += struct.pack("<IHBB", p, 1, 1, 0)
        self.out += rlt
        struct.pack_into("<I", self.out, 0x18, rlt_at)
        struct.pack_into("<I", self.out, 0x1c, len(self.out))
        return bytes(self.out)
