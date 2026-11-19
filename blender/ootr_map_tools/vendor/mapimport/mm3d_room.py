"""Majora's Mask 3D rooms -> meshes and textures for bfres_build / bntx1 (RESEARCH_NOTES.md §80a).

A room file (scenes/<scene>_<n>_info.zsi, LzS-compressed) holds a CMB model at the "cmb " magic; cmb.CMB reads it.
Every CMB mesh becomes one mesh here (its material's first texture is the albedo). MM3D coordinates are Majora's Mask's
units (about 0.025 m). UVs are used as they are: with cmb.decode_texture's top-row-first images they map directly
(a vertical flip put the moon's eye on every face of the sphere: RESEARCH_NOTES.md §80n).
"""
import struct
from typing import List

import numpy as np

from ..cmb import CMB
from ..mm3d import lzs_decompress

SCALE = 0.025       # metres per Majora's Mask unit (to be tuned against Link's size)


def load_room(data: bytes) -> CMB:
    if data[:3] == b"LzS":
        data = lzs_decompress(data)
    at = data.find(b"cmb ")
    size = struct.unpack_from("<I", data, at + 4)[0]
    return CMB(data[at:at + size])


def _tangents(p, uv, tris, n):
    t = np.zeros_like(p)
    for a, b, c in tris:
        e1, e2 = p[b] - p[a], p[c] - p[a]
        d1, d2 = uv[b] - uv[a], uv[c] - uv[a]
        r = d1[0] * d2[1] - d2[0] * d1[1]
        if abs(r) < 1e-12: continue
        tv = (e1 * d2[1] - e2 * d1[1]) / r
        t[a] += tv; t[b] += tv; t[c] += tv
    t -= n * np.sum(t * n, 1, keepdims=True)
    ln = np.linalg.norm(t, axis=1, keepdims=True)
    fallback = np.cross(n, [0.0, 1.0, 0.0]) + np.cross(n, [1.0, 0.0, 0.0]) * 1e-3
    t = np.where(ln > 1e-9, t / np.maximum(ln, 1e-9), fallback / np.linalg.norm(fallback, axis=1, keepdims=True))
    return t


def recompute_normals(P, T, crease_deg=35.0):
    """Per-corner normals from the faces: each corner averages (area-weighted) the faces around its position whose
    normal is within crease_deg of its own face, so harder edges stay sharp. MM3D rooms carry N64-style normals
    smoothed across walls and floors (RESEARCH_NOTES.md §80u). P (n,3), T (m,3) -> (m, 3, 3) corner normals."""
    fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])          # (length = 2 x area)
    unit = fn / np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    key = {}
    vid = np.array([key.setdefault(tuple(np.round(p, 4)), len(key)) for p in P])
    around = {}
    for t, tri in enumerate(T):
        for v in tri: around.setdefault(vid[v], []).append(t)
    around = {k: np.array(v) for k, v in around.items()}
    cos = np.cos(np.radians(crease_deg))
    out = np.zeros((len(T), 3, 3))
    for t, tri in enumerate(T):
        for j, v in enumerate(tri):
            fs = around[vid[v]]
            n = fn[fs[unit[fs] @ unit[t] >= cos]].sum(0)
            ln = np.linalg.norm(n)
            out[t, j] = n / ln if ln > 1e-12 else unit[t]
    return out


def room_meshes(cmb: CMB, scale: float = SCALE, crease_deg=None):
    """-> (meshes [{name, texture (index into cmb.textures), pos, nrm, tan, uv, idx}], triangles (positions) for
    collision). crease_deg: normals recomputed from the faces of the whole room (recompute_normals) instead of the
    CMB's (smoothed across hard edges in MM3D's rooms)."""
    meshes = []
    all_tris = []
    raw = [(mi, mesh, cmb.mesh_triangles(mi)) for mi, mesh in enumerate(cmb.meshes)]
    corner_n = {}
    if crease_deg is not None:
        RP = np.array([v["p"] for _, _, tris in raw for tri in tris for v in tri], float)
        RN = recompute_normals(RP, np.arange(len(RP)).reshape(-1, 3), crease_deg)
        k = 0
        for mi, _, tris in raw:
            corner_n[mi] = RN[k:k + len(tris)]
            k += len(tris)
    for mi, mesh, tris in raw:
        if not tris: continue
        verts, index = {}, []
        P, N, U, C = [], [], [], []
        for ti, tri in enumerate(tris):
            for j, v in enumerate(tri):
                n = corner_n[mi][ti, j] if crease_deg is not None else v["n"]
                key = (tuple(np.round(v["p"], 4)), tuple(np.round(n, 3)), tuple(np.round(v["uv"], 5)),
                       tuple(np.round(v["col"], 3)))
                if key not in verts:
                    verts[key] = len(P)
                    P.append(v["p"]); N.append(n); U.append(v["uv"]); C.append(v["col"])
                index.append(verts[key])
        P = np.array(P) * scale
        N = np.array(N)
        U = np.array(U, float)          # (CMB UVs start at the image's top row, as cmb.decode_texture returns it)
        T = np.array(index).reshape(-1, 3)
        tex = cmb.materials[mesh["material"]]["binders"][0]["tex"]
        meshes.append(dict(name=f"Mesh{mi:02d}", texture=tex, pos=P, nrm=N, tan=_tangents(P, U, T, N), uv=U,
                           col=np.clip(np.array(C, float), 0, 1),
                           idx=np.array(index, np.uint16), tris=T))
        all_tris.append(P[T])
    return meshes, (np.concatenate(all_tris) if all_tris else np.zeros((0, 3, 3)))


def texture_rgba(cmb: CMB, i: int) -> np.ndarray:
    """RGBA8 [h, w, 4], top row first."""
    img = np.asarray(cmb.texture_image(i))
    if img.shape[-1] == 3:
        img = np.concatenate([img, np.full(img.shape[:2] + (1,), 255, img.dtype)], -1)
    return img.astype(np.uint8)


def scene_collision(scene: bytes, scale: float = SCALE):
    """A scene file's collision mesh (scene command 0x03) -> (V (n,3) metres, T (m,3), polygon types (m,)).
    ZSI: commands from 0x10 {u8 cmd, u8 arg, u16, u32 offset from 0x10}; the collision header at 0x10 + offset:
    u16, min s16 x3, max s16 x3, u16 vertices, u16 polygons, u16 surface types, u16 cameras, u16 water boxes, u16,
    then u32 offsets (from 0x10) of the vertices (s16 x3), polygons (20 bytes: u16 type, u16 a | flags << 13, u16 b
    | flags << 13, u16 c, s16 normal x3 (0x7fff = 1), u16, f32 distance), surface types, cameras, water boxes.
    Triangles are returned counter-clockwise seen from the side the stored normal points to."""
    if scene[:3] == b"LzS":
        scene = lzs_decompress(scene)
    base = 0x10
    off = None
    for i in range(64):
        c, _a, _p, o = struct.unpack_from("<BBHI", scene, base + 8 * i)
        if c == 0x03: off = o
        if c == 0x14: break
    assert off is not None, "no collision"
    h = base + off
    nv, npoly = struct.unpack_from("<HH", scene, h + 14)
    o_v, o_p = struct.unpack_from("<II", scene, h + 24)
    V = np.array(struct.unpack_from(f"<{nv * 3}h", scene, base + o_v), float).reshape(-1, 3)
    T, types = [], []
    for i in range(npoly):
        typ, a, b, c, nx, ny, nz = struct.unpack_from("<HHHH3h", scene, base + o_p + 20 * i)
        a, b, c = a & 0x1FFF, b & 0x1FFF, c & 0x1FFF
        n = np.array([nx, ny, nz], float)
        if np.dot(np.cross(V[b] - V[a], V[c] - V[a]), n) < 0:
            b, c = c, b
        T.append((a, b, c)); types.append(typ)
    return V * scale, np.array(T, np.int64), np.array(types)
