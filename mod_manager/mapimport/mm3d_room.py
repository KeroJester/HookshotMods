"""Majora's Mask 3D rooms -> meshes and textures for bfres_build / bntx1 (RESEARCH_NOTES.md §80a).

A room file (scenes/<scene>_<n>_info.zsi, LzS-compressed) holds a CMB model at the "cmb " magic; cmb.CMB reads it.
Every CMB mesh becomes one mesh here (its material's first texture is the albedo). MM3D coordinates are Majora's Mask's
units (about 0.025 m); UVs are flipped vertically (3DS textures start at the bottom row).
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


def room_meshes(cmb: CMB, scale: float = SCALE):
    """-> (meshes [{name, texture (index into cmb.textures), pos, nrm, tan, uv, idx}], triangles (positions) for
    collision)."""
    meshes = []
    all_tris = []
    for mi, mesh in enumerate(cmb.meshes):
        tris = cmb.mesh_triangles(mi)
        if not tris: continue
        verts, index = {}, []
        P, N, U = [], [], []
        for tri in tris:
            for v in tri:
                key = (tuple(np.round(v["p"], 4)), tuple(np.round(v["n"], 3)), tuple(np.round(v["uv"], 5)))
                if key not in verts:
                    verts[key] = len(P)
                    P.append(v["p"]); N.append(v["n"]); U.append(v["uv"])
                index.append(verts[key])
        P = np.array(P) * scale
        N = np.array(N)
        U = np.array(U, float)
        U[:, 1] = 1.0 - U[:, 1]
        T = np.array(index).reshape(-1, 3)
        tex = cmb.materials[mesh["material"]]["binders"][0]["tex"]
        meshes.append(dict(name=f"Mesh{mi:02d}", texture=tex, pos=P, nrm=N, tan=_tangents(P, U, T, N), uv=U,
                           idx=np.array(index, np.uint16), tris=T))
        all_tris.append(P[T])
    return meshes, (np.concatenate(all_tris) if all_tris else np.zeros((0, 3, 3)))


def texture_rgba(cmb: CMB, i: int) -> np.ndarray:
    """RGBA8 [h, w, 4], top row first."""
    img = np.asarray(cmb.texture_image(i))
    if img.shape[-1] == 3:
        img = np.concatenate([img, np.full(img.shape[:2] + (1,), 255, img.dtype)], -1)
    return img.astype(np.uint8)
