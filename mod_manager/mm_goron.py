#!/usr/bin/env python3
"""Majora's Mask Goron Link: read the mesh, skeleton and animations straight from the MM (USA) ROM.

- object_link_goron   = ROM file #655 (gLinkGoronSkel at 0x17A84, 21 LOD limbs), Yaz0
- link_animetion      = ROM file #7 (player animation frames, uncompressed)
- gameplay_keep       = ROM file #649 (PlayerAnimationHeader table)
Offsets come from the zeldaret/mm decomp (assets/xml/objects/object_link_goron.xml, gameplay_keep.xml).

Display lists are F3DEX2. Player limbs weld joints by loading some vertices under another limb's
matrix (gsSPMatrix from segment 0x0D, one 0x40-byte matrix per limb): those vertices are tagged
with that limb so they can be skinned to the matching bone.
"""

import math
import os
import struct
from typing import Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(HERE)
ROM_PATH = os.path.join(GAME, "mods", "_MajoraAssets", "Legend of Zelda, The - Majora's Mask (USA).z64")
DMADATA = 0x1A500
FILE_OBJECT_LINK_GORON = 655
FILE_LINK_ANIMETION = 7
FILE_GAMEPLAY_KEEP = 649
SKEL_OFFSET = 0x17A84

LIMB_NAMES = ["ROOT", "WAIST", "LOWER_ROOT", "R_THIGH", "R_SHIN", "R_FOOT", "L_THIGH", "L_SHIN", "L_FOOT",
              "UPPER_ROOT", "HEAD", "HAT", "COLLAR", "L_SHOULDER", "L_FOREARM", "L_HAND", "R_SHOULDER",
              "R_FOREARM", "R_HAND", "SHEATH", "TORSO"]

# gameplay_keep offsets of PlayerAnimationHeaders (zeldaret/mm gameplay_keep.xml)
ANIM_PG_WAIT = 0xE260


def yaz0(src: bytes) -> bytes:
    if src[:4] != b"Yaz0":
        return src
    size = struct.unpack(">I", src[4:8])[0]
    dst = bytearray()
    i = 16
    while len(dst) < size:
        code = src[i]; i += 1
        for bit in range(7, -1, -1):
            if len(dst) >= size:
                break
            if code >> bit & 1:
                dst.append(src[i]); i += 1
            else:
                b1, b2 = src[i], src[i + 1]; i += 2
                dist = ((b1 & 0xF) << 8 | b2) + 1
                n = b1 >> 4
                if n == 0:
                    n = src[i] + 0x12; i += 1
                else:
                    n += 2
                for _ in range(n):
                    dst.append(dst[-dist])
    return bytes(dst)


class Rom:
    def __init__(self, path: str = ROM_PATH):
        with open(path, "rb") as f:
            self.rom = f.read()

    def file(self, index: int) -> bytes:
        vs, ve, ps, pe = struct.unpack_from(">IIII", self.rom, DMADATA + index * 16)
        raw = self.rom[ps:pe] if pe else self.rom[ps:ps + (ve - vs)]
        return yaz0(raw)


class Limb:
    def __init__(self, index, pos, child, sibling, dl):
        self.index, self.pos, self.child, self.sibling, self.dl = index, pos, child, sibling, dl
        self.name = LIMB_NAMES[index] if index < len(LIMB_NAMES) else f"LIMB_{index}"


class Mesh:
    """Per-vertex arrays plus triangles. pos is in the local space of vspace[i] (a limb index)."""
    def __init__(self):
        self.pos: List[Tuple[float, float, float]] = []
        self.nrm: List[Tuple[float, float, float]] = []
        self.uv: List[Tuple[float, float]] = []
        self.col: List[Tuple[int, int, int, int]] = []
        self.vspace: List[int] = []        # limb whose matrix the vertex was loaded under
        self.owner: List[int] = []         # limb whose display list drew it
        self.tex: List[int] = []           # texture segment address active when the triangle was drawn (per tri)
        self.tlut: List[int] = []          # TLUT segment address active for the triangle (CI textures)
        self.prim: List[Tuple[int, int, int, int]] = []   # primitive colour (modulates I/IA textures)
        self.texgen: List[bool] = []       # G_TEXTURE_GEN on: UVs are sphere-mapped at runtime, not stored
        self.st: List[Tuple[float, float]] = []            # raw vertex texture coords (s/32, t/32)
        self.tri_uv: List[List[Tuple[float, float]]] = []  # per triangle: normalised UVs with draw-time tile state
        self.wrap: List[Tuple[str, str]] = []              # per triangle: ("wrap"|"mirror"|"clamp") for s, t
        self.tris: List[Tuple[int, int, int]] = []


class GoronObject:
    def __init__(self, rom: Optional[Rom] = None):
        self.rom = rom or Rom()
        self.data = self.rom.file(FILE_OBJECT_LINK_GORON)
        seg, count = struct.unpack_from(">IB", self.data, SKEL_OFFSET)
        if seg >> 24 != 6 or count != 21:
            raise ValueError("object_link_goron does not look right (wrong ROM?)")
        table = seg & 0xFFFFFF
        self.limbs: List[Limb] = []
        for i in range(count):
            lp = struct.unpack_from(">I", self.data, table + 4 * i)[0] & 0xFFFFFF
            x, y, z, ch, sib, dl0, _dl1 = struct.unpack_from(">hhhBBII", self.data, lp)
            self.limbs.append(Limb(i, (x, y, z), ch if ch != 0xFF else None, sib if sib != 0xFF else None,
                                   dl0 if dl0 else None))
        self.parent = [None] * count
        for l in self.limbs:
            c = l.child
            while c is not None:
                self.parent[c] = l.index
                c = self.limbs[c].sibling
        # 0x0D matrices are uploaded for limbs that have a display list, in limb order
        self.matrix_slot_to_limb = [l.index for l in self.limbs if l.dl]

    # ---- display lists -----------------------------------------------------------------------
    def mesh(self) -> Mesh:
        m = Mesh()
        for limb in self.limbs:
            if limb.dl:
                self._run_dl(limb.dl, limb.index, m)
        return m

    def _run_dl(self, addr: int, limb: int, m: Mesh):
        d = self.data
        cache: Dict[int, int] = {}
        space = limb
        tex = 0
        tlut = 0
        timg = 0
        prim = (255, 255, 255, 255)
        geom = 0
        tile_w, tile_h = 32, 32
        tscale = (1.0, 1.0)
        cms, cmt = "wrap", "wrap"
        masks, maskt = 0, 0
        tile_lr = (32.0, 32.0)
        stack = []
        pc = addr & 0xFFFFFF
        while True:
            w0, w1 = struct.unpack_from(">II", d, pc)
            op = w0 >> 24
            pc += 8
            if op == 0x01:  # G_VTX
                n = (w0 >> 12) & 0xFF
                end = (w0 >> 1) & 0x7F
                if w1 >> 24 != 6:
                    continue
                va = w1 & 0xFFFFFF
                for k in range(n):
                    x, y, z, _f, s, t, r, g, b, a = struct.unpack_from(">hhhHhhbbbB", d, va + 16 * k)
                    cache[end - n + k] = len(m.pos)
                    m.pos.append((x, y, z))
                    nl = math.sqrt(r * r + g * g + b * b) or 1.0
                    m.nrm.append((r / nl, g / nl, b / nl))
                    m.uv.append((s / 32.0 / tile_w * tscale[0], t / 32.0 / tile_h * tscale[1]))
                    m.st.append((s / 32.0, t / 32.0))
                    m.col.append((r & 0xFF, g & 0xFF, b & 0xFF, a))
                    m.vspace.append(space)
                    m.owner.append(limb)
            elif op in (0x05, 0x06, 0x07):  # TRI1 / TRI2 / QUAD
                groups = [((w0 >> 16) & 0xFF, (w0 >> 8) & 0xFF, w0 & 0xFF)]
                if op != 0x05:
                    groups.append(((w1 >> 16) & 0xFF, (w1 >> 8) & 0xFF, w1 & 0xFF))
                for g3 in groups:
                    idx = [cache.get(v // 2) for v in g3]
                    if None not in idx:
                        m.tris.append(tuple(idx))
                        m.tex.append(tex)
                        m.tlut.append(tlut)
                        m.prim.append(prim)
                        m.texgen.append(bool(geom & 0x40000))
                        # texture-space UVs: 1.0 = one repeat of the texture (2^mask texels, or the tile)
                        rw = (1 << masks) if masks else tile_lr[0]
                        rh = (1 << maskt) if maskt else tile_lr[1]
                        m.tri_uv.append([(m.st[i][0] * tscale[0] / rw, m.st[i][1] * tscale[1] / rh) for i in idx])
                        # clamp edge (in repeats): the tile's extent
                        m.wrap.append((cms, cmt, tile_lr[0] / rw, tile_lr[1] / rh))
            elif op == 0xDA:  # G_MTX
                if w1 >> 24 == 0x0D:
                    slot = (w1 & 0xFFFFFF) // 0x40
                    if slot < len(self.matrix_slot_to_limb):
                        space = self.matrix_slot_to_limb[slot]
            elif op == 0xD8:  # G_POPMTX
                space = limb
            elif op == 0xFD:  # G_SETTIMG
                timg = w1
            elif op == 0xF0:  # G_LOADTLUT
                tlut = timg
            elif op in (0xF3, 0xF4):  # G_LOADBLOCK / G_LOADTILE
                tex = timg
            elif op == 0xD9:  # G_GEOMETRYMODE
                geom = (geom & (w0 & 0xFFFFFF)) | w1
            elif op == 0xFA:  # G_SETPRIMCOLOR
                prim = ((w1 >> 24) & 0xFF, (w1 >> 16) & 0xFF, (w1 >> 8) & 0xFF, w1 & 0xFF)
            elif op == 0xF5:  # G_SETTILE: remember the render tile's wrap modes
                if (w1 >> 24) & 7 == 0:
                    mode = lambda b: "clamp" if b & 2 else ("mirror" if b & 1 else "wrap")
                    cmt, cms = mode((w1 >> 18) & 3), mode((w1 >> 8) & 3)
                    maskt, masks = (w1 >> 14) & 15, (w1 >> 4) & 15
            elif op == 0xF2:  # G_SETTILESIZE
                tile_w = (((w1 >> 12) & 0xFFF) >> 2) + 1
                tile_h = ((w1 & 0xFFF) >> 2) + 1
                if (w1 >> 24) & 7 == 0:
                    tile_lr = (tile_w, tile_h)
            elif op == 0xD7:  # G_TEXTURE
                ss, st = w1 >> 16, w1 & 0xFFFF
                tscale = ((ss or 0x10000) / 65536.0, (st or 0x10000) / 65536.0)
            elif op == 0xDE:  # G_DL
                if w1 >> 24 == 6:
                    if (w0 >> 16) & 0xFF == 0:
                        stack.append(pc)
                    pc = w1 & 0xFFFFFF
            elif op == 0xDF:  # G_ENDDL
                if not stack:
                    return
                pc = stack.pop()


# ---- textures ----------------------------------------------------------------------------------
# object_link_goron textures (zeldaret/mm assets/xml/objects/object_link_goron.xml): offset -> (format, w, h)
TEXTURES = {
    0x0: ("ci8", 64, 32), 0x800: ("ci8", 64, 32), 0x1000: ("ci8", 64, 32), 0x1800: ("ci8", 64, 32),
    0x2400: ("rgba16", 8, 32), 0x2600: ("ci8", 16, 16), 0x2700: ("rgba16", 8, 8), 0x2780: ("rgba16", 8, 16),
    0x2880: ("rgba16", 8, 8), 0x2900: ("ci8", 8, 8), 0x2940: ("rgba16", 32, 16), 0x2D40: ("rgba16", 16, 32),
    0x3140: ("ci8", 32, 64), 0x3940: ("ci8", 16, 16), 0x3A40: ("ci8", 32, 32), 0x3E40: ("ci8", 32, 32),
    0x4240: ("ci8", 8, 32), 0x4340: ("i8", 8, 8), 0xC6B8: ("ci8", 32, 64), 0xCEB8: ("rgba16", 8, 16),
    0xCFB8: ("ci8", 16, 16), 0xD0B8: ("rgba16", 16, 16), 0xD2B8: ("rgba16", 8, 16), 0xD3B8: ("rgba16", 8, 16),
    0xD4B8: ("rgba16", 16, 32), 0xD8B8: ("i8", 8, 8), 0xE078: ("rgba16", 16, 16), 0x109B8: ("ci8", 16, 16),
    0x10AB8: ("ci8", 16, 8), 0x10B38: ("ci8", 16, 16), 0x10C38: ("ci8", 32, 32), 0x11628: ("ci8", 16, 16),
    0x11728: ("rgba16", 8, 32), 0x11C10: ("i4", 32, 32), 0x11E10: ("i4", 64, 64), 0x12930: ("i4", 64, 64),
}
EYES_OPEN, SKIN_TLUT = 0x0, 0x2200   # segment 0x08 = current eye texture (default: open), its TLUT


def _rgba5551(v):
    return ((v >> 11 & 31) * 255 // 31, (v >> 6 & 31) * 255 // 31, (v >> 1 & 31) * 255 // 31, 255 if v & 1 else 0)


def decode_texture(data: bytes, offset: int, tlut_offset: int = 0) -> np.ndarray:
    """[h, w, 4] uint8 for an object_link_goron texture."""
    fmt, w, h = TEXTURES[offset]
    out = np.zeros((h, w, 4), np.uint8)
    pal = None
    if fmt.startswith("ci"):
        n = 256 if fmt == "ci8" else 16
        pal = [_rgba5551(struct.unpack_from(">H", data, tlut_offset + 2 * i)[0]) for i in range(n)]
    for y in range(h):
        for x in range(w):
            i = y * w + x
            if fmt == "rgba16":
                c = _rgba5551(struct.unpack_from(">H", data, offset + 2 * i)[0])
            elif fmt == "ci8":
                c = pal[data[offset + i]]
            elif fmt == "ci4":
                b = data[offset + i // 2]; c = pal[(b >> 4) if i % 2 == 0 else (b & 15)]
            elif fmt == "i8":
                v = data[offset + i]; c = (v, v, v, 255)
            elif fmt == "i4":
                b = data[offset + i // 2]; v = ((b >> 4) if i % 2 == 0 else (b & 15)) * 17; c = (v, v, v, v)
            else:
                c = (255, 0, 255, 255)
            out[y, x] = c
    return out


def triangle_textures(obj: "GoronObject", mesh: Mesh):
    """Per triangle: (key, image) where key identifies a unique decoded texture (offset, TLUT, tint) and
    image is [h, w, 4] uint8, or (None, None) for untextured triangles."""
    cache = {}
    out = []
    for tex, tlut, prim, tg in zip(mesh.tex, mesh.tlut, mesh.prim, mesh.texgen):
        seg, off = tex >> 24, tex & 0xFFFFFF
        if seg == 0x08:
            off, tl = EYES_OPEN, SKIN_TLUT
        else:
            tl = tlut & 0xFFFFFF
        if seg not in (0x06, 0x08) or off not in TEXTURES:
            out.append((None, None))
            continue
        if tg:
            # sphere-mapped highlight (combiner ~ texel * shade): use the texture's mean as a solid colour
            key = ("texgen", off, tl)
            if key not in cache:
                c = decode_texture(obj.data, off, tl)[..., :3].reshape(-1, 3).mean(0)
                cache[key] = np.tile(np.append(np.round(c), 255).astype(np.uint8), (2, 2, 1))
            out.append((key, cache[key]))
            continue
        tint = tuple(prim[:3]) if TEXTURES[off][0] in ("i4", "i8") else None
        key = (off, tl if TEXTURES[off][0].startswith("ci") else 0, tint)
        if key not in cache:
            img = decode_texture(obj.data, off, tl).astype(np.float64)
            if tint:
                img[..., :3] *= np.array(tint) / 255.0
            cache[key] = np.round(img).astype(np.uint8)
        out.append((key, cache[key]))
    return out


def triangle_colors(obj: "GoronObject", mesh: Mesh) -> List[Tuple[int, int, int]]:
    """Average texture colour of each triangle (samples at the corners and centroid)."""
    cache = {}
    cols = []
    for t, tex, tlut, prim in zip(mesh.tris, mesh.tex, mesh.tlut, mesh.prim):
        seg, off = tex >> 24, tex & 0xFFFFFF
        if seg == 0x08:
            off, tl = EYES_OPEN, SKIN_TLUT
        else:
            tl = tlut & 0xFFFFFF
        if seg not in (0x06, 0x08) or off not in TEXTURES:
            cols.append(tuple(prim[:3]))
            continue
        key = (off, tl)
        if key not in cache:
            cache[key] = decode_texture(obj.data, off, tl)
        img = cache[key]
        h, w = img.shape[:2]
        uvs = [mesh.uv[i] for i in t]
        uvs.append(tuple(sum(c) / 3 for c in zip(*uvs)))
        samples = [img[int(v * h) % h, int(u * w) % w] for u, v in uvs]
        c = np.mean([s_[:3] for s_ in samples], axis=0)
        if TEXTURES[off][0] in ("i4", "i8"):
            c = c * np.array(prim[:3]) / 255.0
        cols.append(tuple(int(round(x)) for x in c))
    return cols


# ---- animation ---------------------------------------------------------------------------------
def player_anim_frame(rom: Rom, header_offset: int = ANIM_PG_WAIT, frame: int = 0):
    """(root_pos, [rot (x,y,z) binangs] * 21) for a PlayerAnimation in gameplay_keep."""
    gk = rom.file(FILE_GAMEPLAY_KEEP)
    count, _pad, seg = struct.unpack_from(">hhI", gk, header_offset)
    la = rom.file(FILE_LINK_ANIMETION)
    f = struct.unpack_from(">66hh", la, (seg & 0xFFFFFF) + (frame % count) * 134)
    root = f[0:3]
    rots = [f[3 + 3 * j:6 + 3 * j] for j in range(21)]
    return root, rots


def _rot_zyx(rx, ry, rz):
    """Matrix_TranslateRotateZYX rotation part (binangs): R = Rz * Ry * Rx."""
    a, b, c = (v * math.pi / 32768.0 for v in (rx, ry, rz))
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    Rx = np.array([[1, 0, 0], [0, ca, -sa], [0, sa, ca]])
    Ry = np.array([[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]])
    Rz = np.array([[cc, -sc, 0], [sc, cc, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def pose_world(obj: GoronObject, root_pos, rots) -> List[np.ndarray]:
    """4x4 world matrix per limb (MM model units)."""
    world: List[Optional[np.ndarray]] = [None] * len(obj.limbs)

    def walk(i, parent_m):
        l = obj.limbs[i]
        t = np.array(root_pos if i == 0 else l.pos, dtype=float)
        m = np.eye(4)
        m[:3, :3] = _rot_zyx(*rots[i])
        m[:3, 3] = t
        world[i] = parent_m @ m
        if l.child is not None:
            walk(l.child, world[i])
        if l.sibling is not None:
            walk(l.sibling, parent_m)

    walk(0, np.eye(4))
    return world


def posed_vertices(mesh: Mesh, world: List[np.ndarray]) -> np.ndarray:
    p = np.array(mesh.pos, dtype=float)
    out = np.empty_like(p)
    sp = np.array(mesh.vspace)
    for li in set(mesh.vspace):
        sel = sp == li
        M = world[li]
        out[sel] = p[sel] @ M[:3, :3].T + M[:3, 3]
    return out


# ---- preview -------------------------------------------------------------------------------------
def render_preview(verts: np.ndarray, tris, path: str, views=("front", "side"), size=512, colors=None):
    """Flat-shaded orthographic preview (painter's algorithm). verts: Nx3 (Y up)."""
    from PIL import Image, ImageDraw
    tris = np.array(tris)
    imgs = []
    for view in views:
        if view == "front":
            P = verts[:, [0, 1]]; depth = verts[:, 2]
        elif view == "side":
            P = verts[:, [2, 1]]; depth = -verts[:, 0]
        else:  # top
            P = verts[:, [0, 2]]; depth = verts[:, 1]
        lo, hi = verts.min(0), verts.max(0)
        span = max(hi - lo) or 1
        c = (verts.max(0) + verts.min(0)) / 2
        cx, cy = (c[[0, 1]] if view == "front" else c[[2, 1]] if view == "side" else c[[0, 2]])
        sc = size * 0.9 / span
        pts = np.column_stack([(P[:, 0] - cx) * sc + size / 2, size / 2 - (P[:, 1] - cy) * sc])
        img = Image.new("RGB", (size, size), (30, 30, 36))
        dr = ImageDraw.Draw(img)
        a, b, cc = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
        n = np.cross(b - a, cc - a)
        n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
        light = np.array({"front": (0.3, 0.5, 0.8), "side": (0.8, 0.5, -0.3), "top": (0.3, 0.8, 0.5)}[view])
        shade = np.abs(n @ (light / np.linalg.norm(light)))
        order = np.argsort(depth[tris].mean(1))
        for t in order:
            base = colors[t] if colors is not None else (200, 160, 110)
            k = 0.35 + 0.65 * shade[t]
            dr.polygon([tuple(pts[i]) for i in tris[t]], fill=tuple(int(v * k) for v in base))
        dr.text((6, 6), view, fill=(220, 220, 220))
        imgs.append(img)
    out = Image.new("RGB", (size * len(imgs), size))
    for i, im in enumerate(imgs):
        out.paste(im, (i * size, 0))
    out.save(path)
    return path


if __name__ == "__main__":
    import sys
    rom = Rom()
    obj = GoronObject(rom)
    mesh = obj.mesh()
    print(f"{len(mesh.pos)} vertices, {len(mesh.tris)} triangles")
    root, rots = player_anim_frame(rom, ANIM_PG_WAIT, 0)
    world = pose_world(obj, root, rots)
    v = posed_vertices(mesh, world)
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(GAME, "overlay", "goron_preview.png")
    colors = [((li * 97) % 200 + 55, (li * 57) % 200 + 55, (li * 31) % 200 + 55) for li in
              [mesh.owner[t[0]] for t in mesh.tris]]
    print(render_preview(v, mesh.tris, out, views=("front", "side"), colors=colors))


def render_textured(P: np.ndarray, UV: np.ndarray, tris, atlas: np.ndarray, path: str, size=512,
                    views=("front", "side", "back")):
    """Z-buffered, textured, orthographic software render (Y up). P: Nx3, UV: Nx2 in [0,1], atlas HxWx3+."""
    from PIL import Image
    tris = np.asarray(tris)
    ah, aw = atlas.shape[:2]
    imgs = []
    for view in views:
        if view == "front":
            X, Y, Z = P[:, 0], P[:, 1], P[:, 2]
        elif view == "back":
            X, Y, Z = -P[:, 0], P[:, 1], -P[:, 2]
        else:
            X, Y, Z = P[:, 2], P[:, 1], -P[:, 0]
        lo = np.array([min(X.min(), -1e-3), Y.min()]); span = max(X.max() - X.min(), Y.max() - Y.min()) * 1.1
        cx = (X.max() + X.min()) / 2; cy = (Y.max() + Y.min()) / 2
        sx = (X - cx) / span * size + size / 2
        sy = size / 2 - (Y - cy) / span * size
        img = np.full((size, size, 3), (30, 30, 36), np.uint8)
        zb = np.full((size, size), -np.inf)
        light = np.array([0.4, 0.6, 0.7]); light /= np.linalg.norm(light)
        for t in tris:
            x, y, z = sx[t], sy[t], Z[t]
            x0, x1 = int(max(0, np.floor(x.min()))), int(min(size - 1, np.ceil(x.max())))
            y0, y1 = int(max(0, np.floor(y.min()))), int(min(size - 1, np.ceil(y.max())))
            if x1 < x0 or y1 < y0:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
            if abs(d) < 1e-9:
                continue
            w0 = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / d
            w1 = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / d
            w2 = 1 - w0 - w1
            inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            if not inside.any():
                continue
            zz = w0 * z[0] + w1 * z[1] + w2 * z[2]
            sub = zb[y0:y1 + 1, x0:x1 + 1]
            vis = inside & (zz > sub)
            if not vis.any():
                continue
            uv = UV[t]
            u = w0 * uv[0, 0] + w1 * uv[1, 0] + w2 * uv[2, 0]
            v = w0 * uv[0, 1] + w1 * uv[1, 1] + w2 * uv[2, 1]
            tx = np.clip((u * aw).astype(int), 0, aw - 1); ty = np.clip((v * ah).astype(int), 0, ah - 1)
            col = atlas[ty, tx, :3].astype(float)
            n = np.cross(P[t[1]] - P[t[0]], P[t[2]] - P[t[0]]); n /= np.linalg.norm(n) + 1e-9
            if view == "back": n = n * [-1, 1, -1]
            elif view == "side": n = np.array([n[2], n[1], -n[0]])
            k = 0.45 + 0.55 * abs(float(n @ light))
            sub[vis] = zz[vis]
            img[y0:y1 + 1, x0:x1 + 1][vis] = np.clip(col[vis] * k, 0, 255).astype(np.uint8)
        im = Image.fromarray(img)
        imgs.append(im)
    out = Image.new("RGB", (size * len(imgs), size))
    for i, im in enumerate(imgs):
        out.paste(im, (i * size, 0))
    out.save(path)
    return path
