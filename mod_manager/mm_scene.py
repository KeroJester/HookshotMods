#!/usr/bin/env python3
"""Majora's Mask scenes from the USA ROM: room geometry (F3DEX2 display lists), textures, preview.

Termina Field = scene file #1310 (Z2_00KEIKOKU, segment 2), room #1311 (Z2_00KEIKOKU_room_00,
segment 3), plus scene_texture_08 = file #1121 (segment 6). Identified by the decomp's cutscene offsets
(0x9D8, 0x11F8, 0x1A48) and file sizes.

Room geometry is in world space (no matrices) and lit by vertex colours. Texture format, size, TLUT and
wrap modes are read from the display list (G_SETTIMG / G_SETTILE / G_SETTILESIZE / G_LOADTLUT), so no
per-texture table is needed. G_BRANCH_Z (with G_RDPHALF_1) picks a detail level by depth; the near
(detailed) branch is followed.
"""

import struct
from typing import Dict, List, Tuple

import numpy as np

from . import mm_goron

SCENES = {
    "TerminaField": {"scene": 1310, "rooms": [1311], "segments": {6: 1121}},
}

FMT = {0: "rgba", 1: "yuv", 2: "ci", 3: "ia", 4: "i"}
SIZ_BITS = {0: 4, 1: 8, 2: 16, 3: 32}


def _rgba5551(v):
    return ((v >> 11 & 31) * 255 // 31, (v >> 6 & 31) * 255 // 31, (v >> 1 & 31) * 255 // 31, 255 if v & 1 else 0)


def decode_texture(data: bytes, off: int, fmt: str, bits: int, w: int, h: int, tlut: bytes = None) -> np.ndarray:
    n = w * h
    out = np.zeros((h, w, 4), np.uint8)
    raw = np.frombuffer(data, np.uint8)
    need = (n * bits + 7) // 8
    if off + need > len(raw):
        return np.full((h, w, 4), (255, 0, 255, 255), np.uint8)
    b = raw[off:off + need]
    if bits == 4:
        px = np.empty(n, np.uint8)
        px[0::2] = b[:(n + 1) // 2] >> 4
        px[1::2] = b[:n // 2] & 15
    elif bits == 8:
        px = b[:n].copy()
    elif bits == 16:
        px = (b[0:2 * n:2].astype(np.uint16) << 8) | b[1:2 * n:2]
    else:
        px = b[:4 * n].reshape(n, 4)
    if fmt == "rgba" and bits == 16:
        v = px.astype(np.int64)
        rgba = np.stack([(v >> 11 & 31) * 255 // 31, (v >> 6 & 31) * 255 // 31, (v >> 1 & 31) * 255 // 31,
                         np.where(v & 1, 255, 0)], -1)
    elif fmt == "rgba" and bits == 32:
        rgba = px
    elif fmt == "ci":
        npal = 16 if bits == 4 else 256
        pal = np.array([_rgba5551(struct.unpack_from(">H", tlut, 2 * i)[0]) if tlut and 2 * i + 2 <= len(tlut)
                        else (255, 0, 255, 255) for i in range(npal)], np.uint8)
        rgba = pal[px.astype(np.int64) % npal]
    elif fmt == "i":
        v = px.astype(np.int64) * (17 if bits == 4 else 1)
        rgba = np.stack([v, v, v, v], -1)
    elif fmt == "ia":
        v = px.astype(np.int64)
        if bits == 4:
            i, a = (v >> 1) * 255 // 7, np.where(v & 1, 255, 0)
        elif bits == 8:
            i, a = (v >> 4) * 17, (v & 15) * 17
        else:
            i, a = v >> 8, v & 255
        rgba = np.stack([i, i, i, a], -1)
    else:
        rgba = np.tile([255, 0, 255, 255], (n, 1))
    out[:] = np.asarray(rgba, np.uint8).reshape(h, w, 4)
    return out


class Tri:
    __slots__ = ("p", "uv", "col", "tex", "wrap", "texgen", "xlu", "lit", "prim", "env")

    def __init__(self, p, uv, col, tex, wrap, texgen, xlu, lit=False, prim=(255, 255, 255, 255), env=(255, 255, 255, 255)):
        self.p, self.uv, self.col, self.tex, self.wrap, self.texgen, self.xlu = p, uv, col, tex, wrap, texgen, xlu
        self.lit, self.prim, self.env = lit, prim, env


class Scene:
    def __init__(self, name: str = "TerminaField", rom=None):
        cfg = SCENES[name]
        self.rom = rom or mm_goron.Rom()
        self.seg = {2: self.rom.file(cfg["scene"])}
        for s, f in cfg["segments"].items():
            self.seg[s] = self.rom.file(f)
        self.rooms = [self.rom.file(r) for r in cfg["rooms"]]
        self.textures: Dict[tuple, np.ndarray] = {}

    def _read(self, addr, n):
        d = self.seg.get(addr >> 24)
        o = addr & 0xFFFFFF
        return d[o:o + n] if d is not None else None

    def room_dls(self, room_index=0):
        d = self.rooms[room_index]
        cmds = [(d[i], d[i + 1], struct.unpack_from(">I", d, i + 4)[0]) for i in range(0, 0x100, 8)]
        mh = next(b for c, a, b in cmds if c == 0x0A) & 0xFFFFFF
        typ, n = d[mh], d[mh + 1]
        start = struct.unpack_from(">I", d, mh + 4)[0] & 0xFFFFFF
        out = []
        if typ == 0:
            for i in range(n):
                opa, xlu = struct.unpack_from(">II", d, start + 8 * i)
                out.append((opa, xlu))
        elif typ == 2:
            for i in range(n):
                _x, _y, _z, _r, opa, xlu = struct.unpack_from(">hhhhII", d, start + 16 * i)
                out.append((opa, xlu))
        else:
            raise NotImplementedError(f"room mesh type {typ}")
        return out

    def triangles(self, room_index=0) -> List[Tri]:
        self.seg[3] = self.rooms[room_index]
        tris: List[Tri] = []
        for opa, xlu in self.room_dls(room_index):
            for dl, is_xlu in ((opa, False), (xlu, True)):
                if dl:
                    self._run(dl, tris, is_xlu)
        return tris

    def _run(self, addr, tris, xlu):
        vtx: Dict[int, tuple] = {}
        st = dict(timg=0, timg_fmt="rgba", timg_bits=16, tlut=None, fmt="rgba", bits=16, w=32, h=32,
                  cms="wrap", cmt="wrap", masks=0, maskt=0, sc=(1.0, 1.0), geom=0, rdphalf=0)
        tiles = {}       # tile index -> SETTILE fields (tmem word offset, ...)
        tmem = {}        # TMEM word offset -> texture image address loaded there
        stack = []
        pc = addr
        steps = 0
        while steps < 200000:
            steps += 1
            raw = self._read(pc, 8)
            if raw is None or len(raw) < 8:
                return
            w0, w1 = struct.unpack(">II", raw)
            op = w0 >> 24
            pc += 8
            if op == 0x01:
                n = (w0 >> 12) & 0xFF
                end = (w0 >> 1) & 0x7F
                data = self._read(w1, 16 * n)
                if data is None:
                    continue
                for k in range(n):
                    x, y, z, _f, s, t, r, g, b, a = struct.unpack_from(">hhhHhhBBBB", data, 16 * k)
                    vtx[end - n + k] = ((x, y, z), (s / 32.0, t / 32.0), (r, g, b, a))
            elif op in (0x05, 0x06, 0x07):
                groups = [((w0 >> 16) & 0xFF, (w0 >> 8) & 0xFF, w0 & 0xFF)]
                if op != 0x05:
                    groups.append(((w1 >> 16) & 0xFF, (w1 >> 8) & 0xFF, w1 & 0xFF))
                for g3 in groups:
                    vs = [vtx.get(v // 2) for v in g3]
                    if None in vs:
                        continue
                    rw = (1 << st["masks"]) if st["masks"] else st["w"]
                    rh = (1 << st["maskt"]) if st["maskt"] else st["h"]
                    uv = [(v[1][0] * st["sc"][0] / rw, v[1][1] * st["sc"][1] / rh) for v in vs]
                    t0 = tiles.get(0, {})
                    src = tmem.get(t0.get("tmem", 0))
                    key = (src, st["fmt"], st["bits"], st["w"], st["h"], st["tlut"] if st["fmt"] == "ci" else 0) if src else None
                    if key and key not in self.textures:
                        self._decode(key)
                    tris.append(Tri([v[0] for v in vs], uv, [v[2] for v in vs], key, (st["cms"], st["cmt"]),
                                    bool(st["geom"] & 0x40000), xlu, bool(st["geom"] & 0x20000),
                                    st.get("prim", (255, 255, 255, 255)), st.get("env", (255, 255, 255, 255))))
            elif op == 0xFD:
                st["timg"] = w1
                st["timg_fmt"] = FMT.get((w0 >> 21) & 7, "rgba")
                st["timg_bits"] = SIZ_BITS[(w0 >> 19) & 3]
            elif op == 0xF0:
                st["tlut"] = st["timg"]
            elif op in (0xF3, 0xF4):                          # LOADBLOCK / LOADTILE into the tile's TMEM
                lt = tiles.get((w1 >> 24) & 7, {})
                tmem[lt.get("tmem", 0)] = st["timg"]
            elif op == 0xF5:
                tiles[(w1 >> 24) & 7] = {"tmem": w0 & 0x1FF}
                if (w1 >> 24) & 7 == 0:
                    st["fmt"] = FMT.get((w0 >> 21) & 7, "rgba")
                    st["bits"] = SIZ_BITS[(w0 >> 19) & 3]
                    mode = lambda b: "clamp" if b & 2 else ("mirror" if b & 1 else "wrap")
                    st["cmt"], st["cms"] = mode((w1 >> 18) & 3), mode((w1 >> 8) & 3)
                    st["maskt"], st["masks"] = (w1 >> 14) & 15, (w1 >> 4) & 15
            elif op == 0xF2:
                if (w1 >> 24) & 7 == 0:
                    st["w"] = (((w1 >> 12) & 0xFFF) - ((w0 >> 12) & 0xFFF)) // 4 + 1
                    st["h"] = ((w1 & 0xFFF) - (w0 & 0xFFF)) // 4 + 1
            elif op == 0xD7:
                ss, tt = w1 >> 16, w1 & 0xFFFF
                st["sc"] = ((ss or 0x10000) / 65536.0, (tt or 0x10000) / 65536.0)
            elif op == 0xD9:
                st["geom"] = (st["geom"] & (w0 & 0xFFFFFF)) | w1
            elif op == 0xE1:
                st["rdphalf"] = w1
            elif op == 0xFA:
                st["prim"] = ((w1 >> 24) & 255, (w1 >> 16) & 255, (w1 >> 8) & 255, w1 & 255)
            elif op == 0xFB:
                st["env"] = ((w1 >> 24) & 255, (w1 >> 16) & 255, (w1 >> 8) & 255, w1 & 255)
            elif op == 0x04:                                    # G_BRANCH_Z: follow the near/detailed branch
                pc = st["rdphalf"]
            elif op == 0xDE:
                if (w0 >> 16) & 0xFF == 0:
                    stack.append(pc)
                pc = w1
            elif op == 0xDF:
                if not stack:
                    return
                pc = stack.pop()

    def _decode(self, key):
        timg, fmt, bits, w, h, tlut = key
        d = self.seg.get(timg >> 24)
        pal = None
        if fmt == "ci" and tlut:
            td = self.seg.get(tlut >> 24)
            if td is not None:
                pal = td[tlut & 0xFFFFFF:(tlut & 0xFFFFFF) + 512]
        if d is None or w <= 0 or h <= 0 or w * h > 512 * 512:
            self.textures[key] = np.full((max(1, h), max(1, w), 4), (255, 0, 255, 255), np.uint8)
            return
        self.textures[key] = decode_texture(d, timg & 0xFFFFFF, fmt, bits, w, h, pal)


def render_top(tris: List[Tri], textures, path, size=1024):
    """Top-down textured preview (X right, Z down), vertex colour modulated, z-buffered by height."""
    from PIL import Image
    P = np.array([t.p for t in tris], float)            # [T, 3, 3]
    lo = P[:, :, [0, 2]].reshape(-1, 2).min(0); hi = P[:, :, [0, 2]].reshape(-1, 2).max(0)
    span = (hi - lo).max() * 1.02
    img = np.full((size, size, 3), (20, 20, 26), np.uint8)
    zb = np.full((size, size), -np.inf)
    for t in sorted(tris, key=lambda t: t.xlu):
        p = np.array(t.p, float)
        x = (p[:, 0] - lo[0]) / span * size; y = (p[:, 2] - lo[1]) / span * size; z = p[:, 1]
        x0, x1 = int(max(0, x.min())), int(min(size - 1, x.max() + 1))
        y0, y1 = int(max(0, y.min())), int(min(size - 1, y.max() + 1))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        dd = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(dd) < 1e-9:
            continue
        a = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / dd
        b = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / dd
        c = 1 - a - b
        ins = (a >= -1e-3) & (b >= -1e-3) & (c >= -1e-3)
        zz = a * z[0] + b * z[1] + c * z[2]
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        vis = ins & (zz >= sub)
        if not vis.any():
            continue
        if t.lit:                                           # vertex "colours" are normals: light them
            nrm = np.array([[(x if x < 128 else x - 256) for x in v[:3]] for v in t.col], float)
            nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9
            k = np.clip(nrm @ np.array([0.3, 0.9, 0.3]) / 1.0, 0.25, 1.0)
            col = np.outer(k, [235, 235, 235])
        else:
            col = np.array([v[:3] for v in t.col], float)
        shade = a[..., None] * col[0] + b[..., None] * col[1] + c[..., None] * col[2]
        if t.tex is not None and not t.texgen:
            tex = textures[t.tex]
            th, tw = tex.shape[:2]
            uv = np.array(t.uv)
            u = a * uv[0, 0] + b * uv[1, 0] + c * uv[2, 0]
            v = a * uv[0, 1] + b * uv[1, 1] + c * uv[2, 1]
            ui = (np.floor(u * tw).astype(int) % tw) if t.wrap[0] != "clamp" else np.clip((u * tw).astype(int), 0, tw - 1)
            vi = (np.floor(v * th).astype(int) % th) if t.wrap[1] != "clamp" else np.clip((v * th).astype(int), 0, th - 1)
            texel = tex[vi, ui, :3].astype(float)
            rgb = texel * shade / 255.0
        else:
            rgb = shade
        sub[vis] = zz[vis]
        img[y0:y1 + 1, x0:x1 + 1][vis] = np.clip(rgb[vis], 0, 255).astype(np.uint8)
    Image.fromarray(img).save(path)
    return path


def render_perspective(tris: List[Tri], textures, path, eye, target, fov_deg=70, size=(960, 540)):
    """Perspective textured preview (MM units, Y up)."""
    from PIL import Image
    W, H = size
    eye = np.array(eye, float); fwd = np.array(target, float) - eye; fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 1, 0]); right /= np.linalg.norm(right); up = np.cross(right, fwd)
    f = 1 / np.tan(np.radians(fov_deg) / 2)
    img = np.full((H, W, 3), (120, 160, 210), np.uint8)
    img[H // 2:] = (60, 70, 60)
    zb = np.full((H, W), np.inf)
    for t in tris:
        p = np.array(t.p, float) - eye
        cz = p @ fwd
        if (cz < 10).any():
            continue
        x = (p @ right) / cz * f * (H / 2) + W / 2
        y = H / 2 - (p @ up) / cz * f * (H / 2)
        x0, x1 = int(max(0, x.min())), int(min(W - 1, x.max() + 1))
        y0, y1 = int(max(0, y.min())), int(min(H - 1, y.max() + 1))
        if x1 < x0 or y1 < y0 or (x1 - x0) * (y1 - y0) > 4_000_000:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        dd = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(dd) < 1e-9:
            continue
        a = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / dd
        b = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / dd
        c = 1 - a - b
        ins = (a >= 0) & (b >= 0) & (c >= 0)
        iz = a / cz[0] + b / cz[1] + c / cz[2]            # perspective-correct
        z = 1 / np.maximum(iz, 1e-12)
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        vis = ins & (z < sub)
        if not vis.any():
            continue
        wa, wb, wc = a / cz[0] * z, b / cz[1] * z, c / cz[2] * z
        if t.lit:
            nrm = np.array([[(q if q < 128 else q - 256) for q in v[:3]] for v in t.col], float)
            nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9
            col = np.outer(np.clip(nrm @ np.array([0.3, 0.9, 0.3]), 0.3, 1.0), [235, 235, 235])
        else:
            col = np.array([v[:3] for v in t.col], float)
        shade = wa[..., None] * col[0] + wb[..., None] * col[1] + wc[..., None] * col[2]
        if t.tex is not None and not t.texgen:
            tex = textures[t.tex]; th, tw = tex.shape[:2]; uv = np.array(t.uv)
            u = wa * uv[0, 0] + wb * uv[1, 0] + wc * uv[2, 0]
            v = wa * uv[0, 1] + wb * uv[1, 1] + wc * uv[2, 1]
            ui = (np.floor(u * tw).astype(int) % tw) if t.wrap[0] != "clamp" else np.clip((u * tw).astype(int), 0, tw - 1)
            vi = (np.floor(v * th).astype(int) % th) if t.wrap[1] != "clamp" else np.clip((v * th).astype(int), 0, th - 1)
            rgb = tex[vi, ui, :3].astype(float) * shade / 255.0
            if t.xlu:
                alpha = tex[vi, ui, 3] > 64
                vis = vis & alpha
        else:
            rgb = shade
        sub[vis] = z[vis]
        img[y0:y1 + 1, x0:x1 + 1][vis] = np.clip(rgb[vis], 0, 255).astype(np.uint8)
    Image.fromarray(img).save(path)
    return path
