#!/usr/bin/env python3
"""Retarget MM Goron Link (player) animations onto Child Link's skeleton.

Correspondence (shared with goron_model): MM pose pg_wait frame 0 <-> Link bind pose. For an MM limb L
attached to Link bone B with world correction C (identity for body parts; a rotation for limb segments):
    D(t)       = R_mm_world(L, t) @ R_mm_world(L, 0)^T          (MM-world delta from the reference pose)
    W_B(t)     = D(t) @ C^T @ W_B(bind)                           (Link world rotation; C^T undoes the mesh
                                                                  correction so MM's absolute pose shows)
    local_B(t) = W_parent(t)^T @ W_B(t)                           (parent = Link's animated parent)
Bones without an MM limb keep their bind local rotation. Skl_Root gets MM's root translation delta.
MM animations run at 20 fps; output is resampled to 30 fps.
"""

import math
import struct
from typing import Dict, List

import numpy as np

from . import goron_model, mm_goron

FPS_MM, FPS_OUT = 20.0, 30.0

# MM PlayerAnimationHeader offsets in gameplay_keep (zeldaret/mm assets/xml/objects/gameplay_keep.xml)
MM_ANIMS = {
    "pg_Tbox_open": 0xE170, "pg_climb_endAL": 0xE178, "pg_climb_endAR": 0xE180, "pg_climb_endBL": 0xE188,
    "pg_climb_endBR": 0xE190, "pg_climb_startA": 0xE198, "pg_climb_startB": 0xE1A0, "pg_climb_upL": 0xE1A8,
    "pg_climb_upR": 0xE1B0, "pg_doorA_open": 0xE1B8, "pg_doorB_open": 0xE1C0, "pg_gakkiplayA": 0xE1C8,
    "pg_gakkiplayD": 0xE1D0, "pg_gakkiplayL": 0xE1D8, "pg_gakkiplayR": 0xE1E0, "pg_gakkiplayU": 0xE1E8,
    "pg_gakkiplay": 0xE1F0, "pg_gakkistart": 0xE1F8, "pg_gakkiwait": 0xE200, "pg_maru_change": 0xE208,
    "pg_maskoffstart": 0xE210, "pg_punchA": 0xE218, "pg_punchAendR": 0xE220, "pg_punchAend": 0xE228,
    "pg_punchB": 0xE230, "pg_punchBendR": 0xE238, "pg_punchBend": 0xE240, "pg_punchC": 0xE248,
    "pg_punchCendR": 0xE250, "pg_punchCend": 0xE258, "pg_wait": 0xE260,
}


def mm_anim_all_frames(rom, header_offset):
    gk = rom.file(mm_goron.FILE_GAMEPLAY_KEEP)
    count, _pad, seg = struct.unpack_from(">hhI", gk, header_offset)
    la = rom.file(mm_goron.FILE_LINK_ANIMETION)
    frames = []
    for f in range(count):
        v = struct.unpack_from(">66hh", la, (seg & 0xFFFFFF) + f * 134)
        frames.append((v[0:3], [v[3 + 3 * j:6 + 3 * j] for j in range(21)]))
    return frames


def _lerp_frame(frames, t):
    """MM frame at fractional time t (in MM frames), looping; rotations interpolated with binang wrap."""
    n = len(frames)
    i0 = int(math.floor(t)) % n
    i1 = (i0 + 1) % n
    a = t - math.floor(t)
    r0, q0 = frames[i0]; r1, q1 = frames[i1]
    root = [x + (y - x) * a for x, y in zip(r0, r1)]
    rots = []
    for p, q in zip(q0, q1):
        rots.append(tuple(x + (((y - x + 32768) % 65536) - 32768) * a for x, y in zip(p, q)))
    return root, rots


def euler_xyz(R):
    """R = Rz @ Ry @ Rx -> (rx, ry, rz)."""
    sy = -R[2, 0]
    sy = max(-1.0, min(1.0, sy))
    ry = math.asin(sy)
    if abs(sy) < 0.99999:
        rx = math.atan2(R[2, 1], R[2, 2]); rz = math.atan2(R[1, 0], R[0, 0])
    else:
        rx = math.atan2(-R[1, 2], R[1, 1]); rz = 0.0
    return rx, ry, rz


def retarget(bf: "goron_model.Bfres", header_offset, log=print, n_frames=None, looping=True):
    """header_offset: one gameplay_keep PlayerAnimationHeader offset, or a list played back to back."""
    """Returns (n_frames, {bone index: [F, 3] euler local}, root_translation [F, 3], bones, W_bind)."""
    bones = bf.bones()
    W_bind = bf.bone_world(bones)
    by_name = {b["name"]: i for i, b in enumerate(bones)}
    goron_model.build_goron_bind_mesh(bf, log=lambda s: None)   # fills LIMB_FRAMES
    limb_frames = dict(goron_model.LIMB_FRAMES)
    rom = mm_goron.Rom()
    obj = mm_goron.GoronObject(rom)
    frames = []
    for h in (header_offset if isinstance(header_offset, (list, tuple)) else [header_offset]):
        frames += mm_anim_all_frames(rom, h)
    n_out = n_frames or int(round(len(frames) * FPS_OUT / FPS_MM))
    if looping:
        mm_t = lambda f: f * len(frames) / n_out                     # natural speed, wraps
    else:
        mm_t = lambda f: f * (len(frames) - 1) / max(1, n_out)       # stretched to fit, ends on the last frame
    ref_root, ref_rots = mm_goron.player_anim_frame(rom, mm_goron.ANIM_PG_WAIT, 0)
    mm_ref = mm_goron.pose_world(obj, ref_root, ref_rots)
    s = W_bind[by_name["Skl_Root"]][1, 3] / ref_root[1]

    bone_limb = {}
    for li, (bi, C) in limb_frames.items():
        if obj.limbs[li].name in goron_model.SKIP_LIMBS or obj.limbs[li].name in ("HAT", "COLLAR"):
            continue
        bname = bones[bi]["name"]
        if bname == "Head_2":                     # drive the Head joint (Head_2 is a zero-offset child)
            bi = by_name["Head"]
        bone_limb.setdefault(bi, (li, C))
    rot_w = {i: W_bind[i][:3, :3] / np.linalg.norm(W_bind[i][:3, :3], axis=0) for i in range(len(bones))}
    local_bind = {}
    for i, b in enumerate(bones):
        p = b["parent"]
        local_bind[i] = (rot_w[p].T @ rot_w[i]) if p >= 0 else rot_w[i]
    order = sorted(range(len(bones)), key=lambda i: _depth(bones, i))
    n_samples = n_out + (0 if looping else 1)
    out = {bi: np.zeros((n_samples, 3)) for bi in bone_limb}
    root_tr = np.zeros((n_samples, 3))
    skl = by_name["Skl_Root"]
    ankle_bind_y = min(W_bind[by_name["Ankle_L"]][1, 3], W_bind[by_name["Ankle_R"]][1, 3])
    for f in range(n_out + (0 if looping else 1)):
        t = mm_t(f)
        root, rots = _lerp_frame(frames, min(t, len(frames) - 1.0001) if not looping else t)
        mmW = mm_goron.pose_world(obj, root, rots)
        W = {}
        for i in order:
            p = bones[i]["parent"]
            if i in bone_limb:
                li, C = bone_limb[i]
                D = mmW[li][:3, :3] @ mm_ref[li][:3, :3].T
                # Show MM's absolute limb orientation: the bind mesh is rotated by C relative to MM's reference
                # pose, so undo C (arms hang like MM's instead of keeping Link's A-pose angle).
                W[i] = D @ C.T @ rot_w[i]
                parent_W = W[p] if p >= 0 else np.eye(3)
                out[i][f] = euler_xyz(parent_W.T @ W[i])
            else:
                W[i] = (W[p] if p >= 0 else np.eye(3)) @ local_bind[i]
        # Root: no horizontal motion (it fights the game's own movement: drift, then snap back). Height: keep
        # the lower ankle at its bind height, so MM's bent knees on Link-length legs don't lift the feet.
        P = {}
        for i in order:
            p = bones[i]["parent"]
            t = np.array(bones[i]["trans"], float)
            if i == skl:
                t = t.copy()
            P[i] = (P[p] + W[p] @ t) if p >= 0 else t
        lift = min(P[by_name["Ankle_L"]][1], P[by_name["Ankle_R"]][1]) - ankle_bind_y
        # Feet planted horizontally: shift the root so the ankles' midpoint stays where it was on frame 0
        # (MM moves its root to keep the feet still while the hips sway; dropping root motion made them slide).
        mid = (P[by_name["Ankle_L"]] + P[by_name["Ankle_R"]]) / 2
        if f == 0:
            mid0 = mid.copy()
        slide = mid - mid0
        root_tr[f] = np.array(bones[skl]["trans"]) - np.array([slide[0], lift, slide[2]])
    log(f"  retargeted {len(frames)} MM frames -> {n_out} frames on {len(out)} bones")
    return n_out, out, root_tr, bones, W_bind


def _depth(bones, i):
    d = 0
    while bones[i]["parent"] >= 0:
        i = bones[i]["parent"]; d += 1
    return d


def skin_pose(verts, bones, W_bind, local_rot_frame: Dict[int, np.ndarray], root_t=None):
    """CPU skinning of goron_model verts for one frame (each vertex has one bone)."""
    def rot(e):
        rx, ry, rz = e
        cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
        return np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]) @ np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]) @ np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    M = {}
    for i in sorted(range(len(bones)), key=lambda i: _depth(bones, i)):
        b = bones[i]
        m = np.eye(4)
        m[:3, :3] = rot(local_rot_frame[i]) if i in local_rot_frame else rot(b["rot"])
        m[:3, 3] = root_t if (root_t is not None and b["name"] == "Skl_Root") else b["trans"]
        M[i] = (M[b["parent"]] @ m) if b["parent"] >= 0 else m
    out = []
    for v in verts:
        bi = v["bone"]
        sk = M[bi] @ np.linalg.inv(W_bind[bi])
        out.append(sk[:3, :3] @ v["p"] + sk[:3, 3])
    return np.array(out)
