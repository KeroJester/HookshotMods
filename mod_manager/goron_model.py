#!/usr/bin/env python3
"""Put MM Goron Link's mesh onto Child Link's Switch model (Model/Player_Child.Player_Child.bfres).

Approach (v1, "capacity reuse"): no structures are added or moved, so offsets and sizes in the
bfres never change.
- Every Goron limb is attached to the matching Child Link bone and rebuilt in Link's bind pose:
    v_bind = W_bone @ A_limb @ (S_limb * v_local)
  W_bone = Link bone world bind matrix (verified against the file's inverse bind matrices),
  A_limb = MM-limb-local -> Link-bone-local frame change (bone axis aligned, roll kept from MM's
  standing pose pg_wait frame 0), S_limb = uniform scale, plus lengthwise scale for limb segments
  so joints line up with Link's.
  So Link's own animations drive the Goron body unchanged.
- The Goron mesh is written into the Skin_Body shape's vertex buffer (LOD0 range) and index buffer;
  its three LOD meshes all point at it. Every other shape's meshes get an index count of 0.
- Each vertex is skinned 100% to one bone (blend index = the bone's smooth matrix index).

bfres v10.4 facts used here (Player_Child): FMDL@0xf0; FVTX stride 0x58; FSHP stride 0x60; mesh
stride 0x38 {+0x20 index offset, +0x2c index count, +0x30 first vertex}; submesh {offset,count};
GPU data region at file 0x30000; bones stride 0x58 with euler XYZ (R = Rz*Ry*Rx).
"""

import math
import os
import struct
from typing import Dict, List

import numpy as np

from . import mm_goron

# MM limb -> (Link bone name, chain segment?) ; chain segments get bone-axis alignment + length fit
LIMB_TO_BONE = {
    "WAIST": ("Waist", False),
    "R_THIGH": ("Leg_1_R", True), "R_SHIN": ("Leg_2_R", True), "R_FOOT": ("Ankle_R", False),
    "L_THIGH": ("Leg_1_L", True), "L_SHIN": ("Leg_2_L", True), "L_FOOT": ("Ankle_L", False),
    "TORSO": ("Spine_1", False), "COLLAR": ("Spine_2", False),
    "HEAD": ("Head_2", False), "HAT": ("Head_2", False), "SHEATH": ("Spine_2", False),
    "L_SHOULDER": ("Arm_1_L", True), "L_FOREARM": ("Arm_2_L", True), "L_HAND": ("Wrist_L", False),
    "R_SHOULDER": ("Arm_1_R", True), "R_FOREARM": ("Arm_2_R", True), "R_HAND": ("Wrist_R", False),
}
# The MM player draw code skips these for Goron Link in normal play.
SKIP_LIMBS = {"SHEATH"}
TARGET_SHAPE = "Skin_Body__Mt_Skin"
DATA_BASE = 0x30000


class Bfres:
    def __init__(self, data: bytes):
        self.d = bytearray(data)
        assert self.d[:8] == b"FRES    " and self.u32(8) == 0x000A0400, "expected BFRES v10.4"
        self.fmdl = self.u64(0x28)
        assert self.d[self.fmdl:self.fmdl + 4] == b"FMDL"
        self.fskl = self.u64(self.fmdl + 0x18)
        self.fvtx = self.u64(self.fmdl + 0x20)
        self.fshp = self.u64(self.fmdl + 0x28)
        self.n_vtx, self.n_shp = self.u16(self.fmdl + 0x68), self.u16(self.fmdl + 0x6a)

    def u64(self, o): return struct.unpack_from("<Q", self.d, o)[0]
    def u32(self, o): return struct.unpack_from("<I", self.d, o)[0]
    def u16(self, o): return struct.unpack_from("<H", self.d, o)[0]
    def s(self, o): return self.d[o + 2:o + 2 + self.u16(o)].decode()

    # skeleton
    def bones(self):
        n = self.u16(self.fskl + 0x38)
        arr = self.u64(self.fskl + 0x10)
        out = []
        for i in range(n):
            o = arr + i * 0x58
            idx, par, sm, rg = struct.unpack_from("<hhhh", self.d, o + 0x20)
            sc = struct.unpack_from("<3f", self.d, o + 0x30)
            rot = struct.unpack_from("<3f", self.d, o + 0x3c)
            tr = struct.unpack_from("<3f", self.d, o + 0x4c)
            out.append(dict(name=self.s(self.u64(o)), parent=par, smooth=sm, rigid=rg, scale=sc, rot=rot, trans=tr))
        return out

    def bone_world(self, bones):
        W: Dict[int, np.ndarray] = {}

        def rot(rx, ry, rz):
            cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
            X = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
            Y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
            Z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
            return Z @ Y @ X

        def get(i):
            if i in W:
                return W[i]
            b = bones[i]
            m = np.eye(4)
            m[:3, :3] = rot(*b["rot"]) @ np.diag(b["scale"])
            m[:3, 3] = b["trans"]
            W[i] = get(b["parent"]) @ m if b["parent"] >= 0 else m
            return W[i]

        return [get(i) for i in range(len(bones))]

    # shapes / buffers
    def shapes(self):
        out = []
        for i in range(self.n_shp):
            o = self.fshp + i * 0x60
            out.append(dict(off=o, name=self.s(self.u64(o + 8)), vbuf=self.u16(o + 0x56),
                            n_mesh=self.d[o + 0x5b], meshes=self.u64(o + 0x18)))
        return out

    def vbuf(self, index):
        v = self.fvtx + index * 0x58
        at = self.u64(v + 8)
        attrs = {}
        for i in range(self.d[v + 0x4c]):
            a = at + i * 0x10
            attrs[self.s(self.u64(a))] = (self.u16(a + 8), self.u16(a + 0xc))
        return dict(attrs=attrs, data=DATA_BASE + self.u32(v + 0x48), count=self.u32(v + 0x50),
                    stride=self.d[self.u64(v + 0x38)])

    def mesh(self, shape, lod):
        m = shape["meshes"] + lod * 0x38
        return dict(off=m, sub=self.u64(m), idx_off=self.u32(m + 0x20), prim=self.u32(m + 0x24),
                    fmt=self.u32(m + 0x28), count=self.u32(m + 0x2c), first=self.u32(m + 0x30))


def _pack_10_10_10_2(x, y, z):
    q = lambda v: int(round(max(-1.0, min(1.0, v)) * 511)) & 0x3FF
    return q(x) | q(y) << 10 | q(z) << 20


def _align(a, t):
    """Rotation taking unit vector a onto unit vector t (minimal)."""
    a = a / np.linalg.norm(a); t = t / np.linalg.norm(t)
    v = np.cross(a, t); c = float(np.dot(a, t))
    if c < -0.999999:
        axis = np.cross(a, [0, 1, 0] if abs(a[1]) < 0.9 else [1, 0, 0]); axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)


LIMB_FRAMES = {}   # MM limb index -> (Link bone index, C: MM-world -> Link-world rotation about the joint)


def apply_goron_skeleton(bf: "Bfres", log=print):
    """Give Link's skeleton Goron Link's proportions (MM pose pg_wait frame 0, same mapping as the mesh):
    shoulder joints (Arm_1) where MM's sit on the placed torso, hip joints (Leg_1) where MM's sit on the waist,
    and MM's segment lengths for the upper arm and forearm (legs keep Link's lengths and hip height so the
    feet stay planted). Writes the bone translations and recomputes the
    inverse bind matrices. Returns {bone name: new local translation} (to patch into animations)."""
    bones = bf.bones()
    by = {b["name"]: i for i, b in enumerate(bones)}
    W = bf.bone_world(bones)
    rom = mm_goron.Rom()
    obj = mm_goron.GoronObject(rom)
    root, rots = mm_goron.player_anim_frame(rom, mm_goron.ANIM_PG_WAIT, 0)
    mmW = mm_goron.pose_world(obj, root, rots)
    L = {l.name: l.index for l in obj.limbs}
    s = W[by["Skl_Root"]][1, 3] / root[1]
    O_mm = lambda n: mmW[L[n]][:3, 3]
    O_lk = lambda n: W[by[n]][:3, 3]
    s_up = np.linalg.norm(O_lk("Head") - O_lk("Spine_1")) / np.linalg.norm(O_mm("HEAD") - O_mm("UPPER_ROOT"))
    world_target = {}
    for side in ("L", "R"):
        world_target[f"Arm_1_{side}"] = O_lk("Spine_1") + np.diag([s, s_up, s]) @ (O_mm(f"{side}_SHOULDER") - O_mm("UPPER_ROOT"))
        # hips: MM's spacing (side/front), but Link's hip height so Link's leg animations keep the feet on the floor
        hip = O_lk("Waist") + s * (O_mm(f"{side}_THIGH") - O_mm("WAIST"))
        hip[1] = O_lk(f"Leg_1_{side}")[1]
        world_target[f"Leg_1_{side}"] = hip
    new_t = {}
    for name, tgt in world_target.items():
        p = bones[by[name]]["parent"]
        new_t[name] = (np.linalg.inv(W[p]) @ np.append(tgt, 1.0))[:3]
    lengths = {"Arm_2": "FOREARM", "Wrist": "HAND"}      # legs keep Link's lengths (animations plant the feet)
    for side in ("L", "R"):
        for bone, limb in lengths.items():
            name = f"{bone}_{side}"
            t = np.array(bones[by[name]]["trans"], float)
            new_t[name] = t / np.linalg.norm(t) * np.linalg.norm(obj.limbs[L[f"{side}_{limb}"]].pos) * s
    arr = bf.u64(bf.fskl + 0x10)
    for name, t in new_t.items():
        struct.pack_into("<3f", bf.d, arr + by[name] * 0x58 + 0x4C, *t)
        old = np.array(bones[by[name]]["trans"])
        log(f"  skeleton {name}: {np.round(old, 3)} -> {np.round(t, 3)}")
    # inverse bind matrices of every smooth-skinned bone
    bones = bf.bones()
    W = bf.bone_world(bones)
    inv_arr = bf.u64(bf.fskl + 0x20)
    for b, Wb in zip(bones, W):
        if b["smooth"] >= 0:
            struct.pack_into("<12f", bf.d, inv_arr + b["smooth"] * 48, *np.linalg.inv(Wb)[:3].reshape(-1))
    return {n: tuple(float(x) for x in t) for n, t in new_t.items()}


def patch_animation_base_translations(arch, new_trans, log=print):
    """Write the Goron bone translations into the base pose of every FSKA in an animation archive (bones whose
    translation is not driven by a curve). Returns (patched, skipped_with_curves)."""
    patched = skipped = 0
    for name, f in arch.fskas().items():
        ba = arch.u64(f + 0x28)
        for b in range(arch.u16(f + 0x4C)):
            o = ba + 0x38 * b
            bn = arch.s(arch.u64(o))
            if bn not in new_trans:
                continue
            fl = arch.u32(o + 0x28)
            if fl & 0xE000 or not fl & 0x20:
                skipped += 1
                continue
            base = arch.u64(o + 0x10)
            idx = (3 if fl & 0x08 else 0) + (4 if fl & 0x10 else 0)
            struct.pack_into("<3f", arch.d, base + 4 * idx, *new_trans[bn])
            patched += 1
    return patched, skipped


def build_goron_bind_mesh(bf: Bfres, log=print):
    bones = bf.bones()
    by_name = {b["name"]: i for i, b in enumerate(bones)}
    W = bf.bone_world(bones)
    rom = mm_goron.Rom()
    obj = mm_goron.GoronObject(rom)
    mesh = obj.mesh()
    root, rots = mm_goron.player_anim_frame(rom, mm_goron.ANIM_PG_WAIT, 0)
    mmW = mm_goron.pose_world(obj, root, rots)

    s = W[by_name["Skl_Root"]][1, 3] / root[1]          # MM units -> metres (hip heights match)
    log(f"  MM->Link scale {s:.3e} (Goron hip {root[1]} units = Link hip {W[by_name['Skl_Root']][1,3]:.3f} m)")
    L = {l.name: l.index for l in obj.limbs}
    O_mm = lambda li: mmW[li][:3, 3]
    O_lk = lambda name: W[by_name[name]][:3, 3]
    # torso height: MM upper-body root -> head joint must span Link Spine_1 -> Head
    s_up = np.linalg.norm(O_lk("Head") - O_lk("Spine_1")) / np.linalg.norm(O_mm(L["HEAD"]) - O_mm(L["UPPER_ROOT"]))
    log(f"  torso vertical scale {s_up / s:.2f}x")

    # Every limb is placed in world space around an anchor joint pair (MM limb origin <-> Link bone origin):
    #   p_link = O_link + C @ Sw @ (p_mm_world - O_mm)
    # C rotates MM world -> Link world about the joint (identity for body parts; for limb segments it turns
    # the MM bone direction onto Link's), Sw scales (lengthwise fit for limb segments, torso squash).
    place = {}   # limb index -> (bone index for skinning, O_mm, O_link, M = C @ Sw)
    def bone_dir(bname):
        bi = by_name[bname]
        child = next(i for i, b in enumerate(bones) if b["parent"] == bi and
                     b["name"].split("_")[0] in ("Arm", "Wrist", "Leg", "Ankle"))
        return W[child][:3, 3] - W[bi][:3, 3]
    C_of = {}
    for limb in obj.limbs:
        if limb.name not in LIMB_TO_BONE:
            continue
        bname, chain = LIMB_TO_BONE[limb.name]
        li = limb.index
        anchor_mm, anchor_lk = O_mm(li), O_lk(bname)
        if chain:
            d_lk = bone_dir(bname)
            d_mm = mmW[li][:3, :3] @ np.array([1.0, 0, 0])
            C = _align(d_mm, d_lk)
            mm_len = np.linalg.norm(obj.limbs[limb.child].pos)
            k = np.linalg.norm(d_lk) / (mm_len * s)          # lengthwise factor along the MM bone axis
            u = d_mm / np.linalg.norm(d_mm)
            Sw = s * (np.eye(3) + (k - 1) * np.outer(u, u))
            C_of[li] = C
        elif limb.name in ("L_HAND", "R_HAND", "L_FOOT", "R_FOOT"):
            C = C_of.get(obj.parent[li], np.eye(3))           # follow the segment above
            Sw = s * np.eye(3)
        elif limb.name in ("TORSO",):
            anchor_mm, anchor_lk = O_mm(L["UPPER_ROOT"]), O_lk("Spine_1")
            C, Sw = np.eye(3), np.diag([s, s_up, s])
        elif limb.name in ("HEAD", "HAT", "COLLAR"):
            anchor_mm, anchor_lk = O_mm(L["HEAD"]), O_lk("Head")
            C, Sw = np.eye(3), s * np.eye(3)
        else:
            C, Sw = np.eye(3), s * np.eye(3)
        place[li] = (by_name[bname], anchor_mm, anchor_lk, C @ Sw)
        LIMB_FRAMES[li] = (by_name[bname], C)      # for goron_anim: same correspondence as the mesh
    spine2_y = O_lk("Spine_2")[1]
    xform = place
    tri_colors = mm_goron.triangle_colors(obj, mesh)
    tri_tex = mm_goron.triangle_textures(obj, mesh)
    keep_tri = []
    keep_col = []
    for t, col, tt, tuv, wr in zip(mesh.tris, tri_colors, tri_tex, mesh.tri_uv, mesh.wrap):
        owners = {mesh.owner[i] for i in t}
        if any(obj.limbs[o].name in SKIP_LIMBS for o in owners):
            continue
        if all(mesh.vspace[i] in xform for i in t):
            keep_tri.append(t)
            keep_col.append((col, tt, tuv, wr))
    used = sorted({i for t in keep_tri for i in t})
    remap = {old: new for new, old in enumerate(used)}
    verts = []
    for old in used:
        li = mesh.vspace[old]
        bi, a_mm, a_lk, M = xform[li]
        Wm = mmW[li]
        p_mm = Wm[:3, :3] @ np.array(mesh.pos[old], dtype=float) + Wm[:3, 3]
        n_mm = Wm[:3, :3] @ np.array(mesh.nrm[old])
        p = a_lk + M @ (p_mm - a_mm)
        n = M @ n_mm; n /= np.linalg.norm(n) or 1
        if obj.limbs[li].name == "TORSO" and p[1] > spine2_y:
            bi = by_name["Spine_2"]                           # upper chest follows Spine_2
        verts.append(dict(p=p, n=n, uv=mesh.uv[old], bone=bi, smooth=bones[bi]["smooth"]))
    tris = [tuple(remap[i] for i in t) for t in keep_tri]
    log(f"  Goron mesh: {len(verts)} vertices, {len(tris)} triangles on {len(set(v['bone'] for v in verts))} bones")
    return verts, tris, keep_col


PALETTE_GRID = 8          # albedo is split into 8x8 solid cells (each 8x8 ASTC blocks: pure down to mip 3)


def make_palette(colors, k=PALETTE_GRID * PALETTE_GRID, iters=20):
    """k-means in RGB; returns (palette [k,3], label per colour)."""
    X = np.array(colors, dtype=float)
    uniq = np.unique(X, axis=0)
    if len(uniq) <= k:
        pal = uniq
    else:
        rng = np.random.default_rng(0)
        pal = uniq[rng.choice(len(uniq), k, replace=False)]
        for _ in range(iters):
            lab = np.argmin(((X[:, None] - pal[None]) ** 2).sum(-1), 1)
            for j in range(len(pal)):
                if np.any(lab == j):
                    pal[j] = X[lab == j].mean(0)
    lab = np.argmin(((X[:, None] - pal[None]) ** 2).sum(-1), 1)
    return pal, lab


def flat_colour_mesh(verts, tris, colors):
    """Give every triangle its own 3 vertices with UVs at the centre of its palette cell."""
    pal, lab = make_palette(colors)
    out_v, out_t = [], []
    for t, cell in zip(tris, lab):
        cu = ((cell % PALETTE_GRID) + 0.5) / PALETTE_GRID
        cv = ((cell // PALETTE_GRID) + 0.5) / PALETTE_GRID
        base = len(out_v)
        for i in t:
            v = dict(verts[i]); v["uv"] = (cu, cv); out_v.append(v)
        out_t.append((base, base + 1, base + 2))
    grid = np.zeros((PALETTE_GRID, PALETTE_GRID, 4), np.uint8)
    grid[..., 3] = 255
    for j, c in enumerate(pal):
        grid[j // PALETTE_GRID, j % PALETTE_GRID, :3] = np.clip(np.round(c), 0, 255)
    return out_v, out_t, grid, len(pal)


ATLAS_SIZE = 256


def _tile_image(img, nx, ny, wrap):
    """Repeat (or mirror) a texture nx by ny times, following the N64 tile wrap modes."""
    rows = []
    for j in range(ny):
        row = []
        for i in range(nx):
            t = img
            if wrap[0] == "mirror" and i % 2:
                t = t[:, ::-1]
            if wrap[1] == "mirror" and j % 2:
                t = t[::-1]
            row.append(t)
        rows.append(np.concatenate(row, 1))
    return np.concatenate(rows, 0)


MAX_TILES = 3


def atlas_mesh(verts, tris, tri_info, log=print):
    """Pack every distinct MM texture into one ATLAS_SIZE^2 image (largest integer upscale that fits) and
    give each triangle its own 3 vertices with UVs inside its texture's atlas slot. Textures that triangles
    tile across are stored repeated/mirrored as needed (up to MAX_TILES per axis) instead of clamped."""
    base, span, wraps, tri_uv = {}, {}, {}, []
    for col, (key, img), tuv, wr in tri_info:
        if key is None:
            key, img = ("solid",) + tuple(col), np.tile(np.array(list(col) + [255], np.uint8), (2, 2, 1))
        base.setdefault(key, img)
        wraps.setdefault(key, wr[:2])
        uv = np.array(tuv, dtype=float)
        if key[0] in ("solid", "texgen"):
            uv = np.full((3, 2), 0.5)
        need = np.ones(2)
        for ax in (0, 1):
            if wr[ax] == "clamp":
                uv[:, ax] = np.clip(uv[:, ax], 0.0, 1.0)  # N64 clamp: outside 0..1 shows the edge texels
            else:
                uv[:, ax] -= np.floor(uv[:, ax].min())    # wrap/mirror: move into the first repeat
                need[ax] = min(np.ceil(uv[:, ax].max() - 1e-6), MAX_TILES)
        need = need.clip(1)
        sx, sy = span.get(key, (1, 1))
        span[key] = (max(sx, int(need[0])), max(sy, int(need[1])))
        tri_uv.append((key, uv))
    entries = {k: _tile_image(img, *span[k], wraps[k]) for k, img in base.items()}
    for scale in (8, 6, 4, 3, 2, 1):
        pad = max(2, scale)
        slots, ok = {}, True
        sky = np.zeros(ATLAS_SIZE, int)                    # skyline packer: lowest free spot per rectangle
        for key, img in sorted(entries.items(), key=lambda kv: (-kv[1].shape[0], -kv[1].shape[1])):
            h, w = img.shape[0] * scale + 2 * pad, img.shape[1] * scale + 2 * pad
            if w > ATLAS_SIZE:
                ok = False
                break
            best = None
            for x in range(0, ATLAS_SIZE - w + 1, 2):
                y = int(sky[x:x + w].max())
                if y + h <= ATLAS_SIZE and (best is None or y < best[1]):
                    best = (x, y)
            if best is None:
                ok = False
                break
            x, y = best
            sky[x:x + w] = y + h
            slots[key] = (x + pad, y + pad, w - 2 * pad, h - 2 * pad)
        if ok:
            break
    else:
        raise RuntimeError("Goron textures do not fit the atlas even at 1x")
    atlas = np.zeros((ATLAS_SIZE, ATLAS_SIZE, 4), np.uint8)
    atlas[..., 3] = 255
    for key, (x0, y0, w, h) in slots.items():
        img = np.repeat(np.repeat(entries[key], scale, 0), scale, 1)
        padded = np.pad(img, ((pad, pad), (pad, pad), (0, 0)), mode="edge")
        atlas[y0 - pad:y0 + h + pad, x0 - pad:x0 + w + pad] = padded
    out_v, out_t = [], []
    for t, (key, uv) in zip(tris, tri_uv):
        x0, y0, w, h = slots[key]
        nx, ny = span[key]
        uv = np.clip(uv, 0.0, [nx, ny])
        b = len(out_v)
        for k, i in enumerate(t):
            v = dict(verts[i])
            v["uv"] = ((x0 + uv[k, 0] / nx * w) / ATLAS_SIZE, (y0 + uv[k, 1] / ny * h) / ATLAS_SIZE)
            out_v.append(v)
        out_t.append((b, b + 1, b + 2))
    tiled = sum(1 for k in span if span[k] != (1, 1))
    log(f"  atlas: {len(entries)} textures ({tiled} stored tiled) packed at {scale}x into {ATLAS_SIZE}x{ATLAS_SIZE}")
    return out_v, out_t, atlas


def apply_goron(bfres_bytes: bytes, log=print):
    bf = Bfres(bfres_bytes)
    apply_goron_skeleton(bf, log)
    verts, tris, tri_info = build_goron_bind_mesh(bf, log)
    verts, tris, atlas = atlas_mesh(verts, tris, tri_info, log)
    return write_mesh(bf, bfres_bytes, verts, tris, log), verts, tris, atlas


def write_mesh(bf: "Bfres", bfres_bytes: bytes, verts, tris, log=print, extra=()):
    """Write a mesh into TARGET_SHAPE (all LODs) and hide every other shape. Vertices: {p, n, uv, and either
    infl: [(smooth matrix index, weight)] (up to 4) or smooth: one index}.
    extra: [(shape name, verts, tris, material, texture map)] more meshes in other shapes (e.g. translucent parts)."""
    shapes = bf.shapes()
    target = next(sh for sh in shapes if sh["name"] == TARGET_SHAPE)
    _write_shape(bf, target, verts, tris)
    d = bf.d

    def set_mesh(m, off, count, first):
        struct.pack_into("<I", d, m["off"] + 0x20, off)
        struct.pack_into("<I", d, m["off"] + 0x2c, count)
        struct.pack_into("<I", d, m["off"] + 0x30, first)
        struct.pack_into("<II", d, m["sub"], 0, count)

    keep = {TARGET_SHAPE} | {e[0] for e in extra}
    hidden = 0
    for sh in shapes:
        if sh["name"] in keep:
            continue
        for lod in range(sh["n_mesh"]):
            m = bf.mesh(sh, lod)
            set_mesh(m, m["idx_off"], 0, m["first"])
        hidden += 1
    log(f"  wrote Goron into {TARGET_SHAPE} (all {target['n_mesh']} LODs); hid {hidden} other shapes")
    redirect_material_textures(bf, "Mt_Skin", REDIRECT_TEXTURES, log)
    use_material(bf, target, GORON_MATERIAL, GORON_MATERIAL_TEXTURES, log)
    for name, ev, et, mat, texmap, *rest in extra:
        sh = next(x for x in shapes if x["name"] == name)
        _write_shape(bf, sh, ev, et)
        use_material(bf, sh, mat, texmap, log)
        if rest:                                             # (bone index, radius): culling follows that bone
            _set_shape_bounds(bf, sh, *rest[0])
        if len(rest) > 1 and rest[1]:                         # material parameter floats: {index: value}
            _, mo = _material_offset(bf, mat)
            data = bf.u64(mo + 0x58)
            for k, v in rest[1].items():
                struct.pack_into("<f", d, data + 4 * k, v)
            log(f"  {mat} parameters {rest[1]}")
        log(f"  wrote {len(et)} triangles into {name}")
    assert len(d) == len(bfres_bytes)
    return bytes(d)


def _set_shape_bounds(bf: "Bfres", shape, bone, radius):
    """The engine culls a skinned shape by its skin-bone list (FSHP +0x20 u16 array, count u16 +0x58) and bounds
    (+0x38 boxes, +0x40 spheres: centre xyz + radius per LOD). Vertex matrix indices are global smooth indices, so the
    list only drives culling: point every entry at `bone` and make the bounds cover `radius` metres around it."""
    d, o = bf.d, shape["off"]
    arr, cnt = bf.u64(o + 0x20), bf.u16(o + 0x58)
    for i in range(cnt):
        struct.pack_into("<H", d, arr + 2 * i, bone)
    bb, sp = bf.u64(o + 0x38), bf.u64(o + 0x40)
    for lod in range(shape["n_mesh"]):
        struct.pack_into("<6f", d, bb + 24 * lod, 0.0, 0.0, 0.0, radius, radius, radius)
        struct.pack_into("<4f", d, sp + 16 * lod, 0.0, 0.0, 0.0, radius)


def _write_shape(bf: "Bfres", target, verts, tris):
    """Vertices + indices into one shape (all LODs draw the full mesh)."""
    TARGET_SHAPE_NAME = target["name"]
    vb = bf.vbuf(target["vbuf"])
    lod0 = bf.mesh(target, 0)
    cap_v = (bf.mesh(target, 1)["first"] if target["n_mesh"] > 1 else vb["count"])
    cap_i = lod0["count"]
    idx = [i for t in tris for i in t]
    if len(verts) > cap_v or len(idx) > cap_i or len(verts) > 0xFFFF:
        raise RuntimeError(f"Goron mesh too big for {TARGET_SHAPE_NAME}: {len(verts)}/{cap_v} verts, {len(idx)}/{cap_i} idx")
    a = vb["attrs"]
    need = {"_p0": 0x1805, "_n0": 0x0E02, "_u0": 0x1201, "_i0": 0x0B03, "_w0": 0x0B01}
    for k, f in need.items():
        if a.get(k, (None,))[0] != f:
            raise RuntimeError(f"unexpected vertex layout: {k}={a.get(k)} (expected format {f:#x})")
    d = bf.d
    for vi, v in enumerate(verts):
        base = vb["data"] + vi * vb["stride"]
        struct.pack_into("<3f", d, base + a["_p0"][1], *v["p"])
        struct.pack_into("<I", d, base + a["_n0"][1], _pack_10_10_10_2(*v["n"]))
        if "_t0" in a:
            t = np.cross(v["n"], [0, 1, 0]); t = t / (np.linalg.norm(t) or 1)
            struct.pack_into("<I", d, base + a["_t0"][1], _pack_10_10_10_2(*t))
        u, w = v["uv"]
        struct.pack_into("<2H", d, base + a["_u0"][1], int(min(max(u, 0.0), 1.0) * 65535), int(min(max(w, 0.0), 1.0) * 65535))
        infl = v.get("infl") or [(v["smooth"], 1.0)]
        ids = [i for i, _ in infl] + [0] * (4 - len(infl))
        ws = [int(round(w * 255)) for _, w in infl] + [0] * (4 - len(infl))
        ws[0] += 255 - sum(ws)                                  # weights sum to exactly 255
        struct.pack_into("<4B", d, base + a["_i0"][1], *ids)
        struct.pack_into("<4B", d, base + a["_w0"][1], *ws)
        if "_c0" in a:
            struct.pack_into("<4B", d, base + a["_c0"][1], 255, 255, 255, 255)
        for k in ("_i1", "_w1"):                                # second influence set: unused (zero)
            if k in a:
                n = 4 if (a[k][0] >> 8) == 0x0B else 2              # 0x0B..: 4 x u8, 0x09..: 2 x u8
                struct.pack_into(f"<{n}B", d, base + a[k][1], *([0] * n))
    assert lod0["fmt"] == 1 and lod0["prim"] == 3, "expected 16-bit triangle lists"
    struct.pack_into(f"<{len(idx)}H", d, DATA_BASE + lod0["idx_off"], *idx)

    def set_mesh(m, off, count, first):
        struct.pack_into("<I", d, m["off"] + 0x20, off)
        struct.pack_into("<I", d, m["off"] + 0x2c, count)
        struct.pack_into("<I", d, m["off"] + 0x30, first)
        struct.pack_into("<II", d, m["sub"], 0, count)

    for lod in range(target["n_mesh"]):
        set_mesh(bf.mesh(target, lod), lod0["idx_off"], len(idx), 0)


def goron_bone_translations(bfres_bytes: bytes):
    """The skeleton changes apply_goron makes, for patching animations."""
    return apply_goron_skeleton(Bfres(bfres_bytes), log=lambda s: None)


# The Goron body is drawn with Link's leather material, not Mt_Skin: Mt_Skin is the skin shader (subsurface
# lookup CmnTex_Chara_Skin_LUT_A_Slt, thickness), which made the rock-skinned Goron look waxy / plastic under
# Hookshot v2.0.14's fixed shading (RESEARCH_NOTES §47). Mt_PantsParts has the same vertex attributes and skin
# weight count (FSHP +0x5a = 7) as Skin_Body, so its shader variant exists (Mt_Leather, count 5: flat orange), and
# plain PBR samplers (_a0 _n0 _rgh0 _ao0). Its slots are pointed at the Goron's textures (other shapes are hidden).
GORON_MATERIAL = "Mt_PantsParts"
GORON_MATERIAL_TEXTURES = {      # slot texture -> texture it shows instead (names taken from Mt_Skin's list)
    "Mt_PantsParts_Player_Child_Alb": "Player_Child_Skin_Alb",  # the Goron atlas
    "Mt_PantsParts_Player_Child_Nrm": "Player_Child_Skin_Nrm",  # flat normal
    "Mt_PantsParts_Player_Child_AO": "Player_Child_Skin_AO",    # white
}                                                               # (_rgh0 keeps its own file, flattened: FLATTEN)


# Charge fire: a second shape. Link's only translucent material (Mt_EyeShadow_test) is a darkening decal ("seal");
# so the fire uses the alpha-masked Mt_PantsParts (the Goron body's material: same atlas, shader variant for skin
# weight count 7) in the hidden pants shape, with dithered alpha. Culling follows the fire bone.
FIRE_SHAPE = os.environ.get("GORON_FIRE_SHAPE", "Pants_Main_low__Mt_Cloth_Pants")
FIRE_MATERIAL = os.environ.get("GORON_FIRE_MATERIAL", "Mt_PantsParts")
FIRE_MATERIAL_TEXTURES = {}


def _material_offset(bf, name):
    fm = bf.u64(bf.fmdl + 0x38)
    for i in range(bf.u16(bf.fmdl + 0x6c)):
        if bf.s(bf.u64(fm + i * 0xB0 + 8)) == name:
            return i, fm + i * 0xB0
    raise RuntimeError(f"material {name} not found")


def _texture_name_slots(bf, mo):
    arr = bf.u64(mo + 0x20)
    out = []
    for j in range(16):
        p = bf.u64(arr + 8 * j)
        if not (0 < p < len(bf.d)):
            break
        try:
            nm = bf.s(p)
        except Exception:
            break
        if not nm or not nm.isprintable():
            break
        out.append((arr + 8 * j, p, nm))
    return out


def use_material(bf, shape, material, textures, log=print):
    """Draw `shape` with `material` (FSHP +0x52 material index) and point that material's texture-name
    slots at other texture names already in the file (string pointers copied; no size change)."""
    mi, mo = _material_offset(bf, material)
    pool = {}
    fm = bf.u64(bf.fmdl + 0x38)
    for i in range(bf.u16(bf.fmdl + 0x6c)):                     # texture names of every material
        for _a, p, nm in _texture_name_slots(bf, fm + i * 0xB0):
            pool.setdefault(nm, p)
    for addr, _p, nm in _texture_name_slots(bf, mo):
        if nm in textures:
            struct.pack_into("<Q", bf.d, addr, pool[textures[nm]])
            log(f"  {material} slot {nm} -> {textures[nm]}")
    struct.pack_into("<H", bf.d, shape["off"] + 0x52, mi)
    log(f"  {shape['name']} drawn with {material} (material {mi})")


def redirect_material_textures(bf: "Bfres", material: str, mapping, log=print):
    """Point a material's texture-name slots (FMAT +0x20 name array) at other names already in
    the same list, by copying string pointers (no new strings, no size change)."""
    d = bf.d
    fm = bf.u64(bf.fmdl + 0x38)
    n_mat = bf.u16(bf.fmdl + 0x6c)
    for i in range(n_mat):
        mo = fm + i * 0xB0
        if bf.s(bf.u64(mo + 8)) != material:
            continue
        for arr_off in (0x20,):          # (+0x98 is the runtime texture pointer table)
            arr = bf.u64(mo + arr_off)
            names, ptrs = [], []
            for j in range(16):
                p = bf.u64(arr + 8 * j)
                if not (0 < p < len(d)):
                    break
                try:
                    nm = bf.s(p)
                except Exception:
                    break
                if not nm or not nm.isprintable():
                    break
                names.append(nm); ptrs.append(p)
            for j, nm in enumerate(names):
                if nm in mapping and mapping[nm] in names:
                    struct.pack_into("<Q", d, arr + 8 * j, ptrs[names.index(mapping[nm])])
                    log(f"  {material} texture slot {j} (+{arr_off:#x} list): {nm} -> {mapping[nm]}")
        return
    raise RuntimeError(f"material {material} not found")


# ---- textures of the Mt_Skin material -----------------------------------------------------------------
ALBEDO = "Tex/Player_Child_Skin_Alb.bntx.zs"
BC1_TEMPLATE = "Tex/Mt_PantsParts_Player_Child_Alb.bntx.zs"
FLATTEN = {   # texture -> constant value written to every block (keeps Link's skin detail off the Goron)
    "Tex/Player_Child_Skin_AO.bntx.zs": "white",
    "Tex/Player_Child_Skin_Nrm.bntx.zs": "flat_normal",
    "Tex/Player_Child_Skin_Gn2.bntx.zs": "matte",            # _gn0: R = roughness, G = spec mask (RESEARCH_NOTES §28, §46)
    "Tex/Player_Child_Skin_Thc.bntx.zs": "white",            # thickness: fully thick = no light through (§46)
    "Tex/Mt_PantsParts_Player_Child_Rgh.bntx.zs": "rough",   # the Goron's roughness (Mt_PantsParts _rgh0, §47)
}
# Mt_Skin texture slots that point at shared detail textures: redirect them to our flattened ones
REDIRECT_TEXTURES = {"Skin_Detail_A_Nrm": "Player_Child_Skin_Nrm"}
FLAT_COLOURS = {"white": (255, 255, 255, 255), "flat_normal": (128, 128, 255, 255),
                "flat_normal_rg": (128, 128, 0, 255), "black": (0, 0, 0, 255),
                "rough": (235, 235, 235, 255),   # BC4 roughness (R): rock-like, a touch below fully rough
                "matte": (255, 64, 0, 255)}    # fully rough, low spec mask (210/200 looked plasticky). Hookshot v2.0.14
                                               # fixed the material shading: the old (0,0,0) is now a mirror (§46)


def build_textures(romfs, palette_grid, log=print):
    """{romfs path: new decompressed bntx bytes}"""
    import os
    from . import bntx, zstd_helper
    out = {}

    def load(rel):
        with open(os.path.join(romfs, rel), "rb") as f:
            return bntx.Texture(zstd_helper.decompress_zs(f.read(), os.path.basename(rel), romfs))

    alb = load(ALBEDO)
    tmpl = load(BC1_TEMPLATE)                      # a real BC1 256x256 texture: copy its mip layout
    assert (tmpl.format, tmpl.width, tmpl.height) == (0x1A06, ATLAS_SIZE, ATLAS_SIZE)
    bntx.retype(alb, tmpl.format, tmpl.width, tmpl.height, tmpl.mips, tmpl.mip_offsets, tmpl.image_size,
                tmpl.bh_log2)
    for level, img in enumerate(bntx.mip_chain(palette_grid, alb.mips)):
        wb, hb = alb.mip_blocks(level)
        canvas = np.zeros((hb * 4, wb * 4, 4), np.uint8)
        canvas[:img.shape[0], :img.shape[1]] = img
        if img.shape[0] < hb * 4:                  # tiny mips: fill the block with the average colour
            canvas[:] = img.reshape(-1, 4).mean(0)
        alb.write_blocks(level, bntx.encode_bc1(canvas))
    out[ALBEDO] = alb.to_bytes()
    log(f"  {ALBEDO}: re-typed ASTC 12x12 768^2 -> BC1 {ATLAS_SIZE}^2 ({alb.mips} mips) holding the Goron atlas")
    for rel, kind in FLATTEN.items():
        t = load(rel)
        fmt = t.format >> 8
        for level in range(t.mips):
            wb, hb = t.mip_blocks(level)
            c = FLAT_COLOURS[kind]
            if fmt == 0x1D:                                   # BC4: constant (red channel)
                blk = np.frombuffer(bytes([c[0], c[0], 0, 0, 0, 0, 0, 0]), np.uint8)
            elif fmt == 0x1A:                                 # BC1: constant colour
                blk = bntx.encode_bc1(np.tile(np.array(c[:3], np.uint8), (4, 4, 1)))[0, 0]
            elif fmt in bntx.ASTC:
                blk = np.frombuffer(bntx.astc_void_extent(*c), np.uint8)
            else:
                raise RuntimeError(f"cannot flatten {rel}: format {t.format:#x}")
            t.write_blocks(level, np.broadcast_to(blk, (hb, wb, t.bpb)).copy())
        out[rel] = t.to_bytes()
        log(f"  {rel}: flattened ({kind})")
    return out
