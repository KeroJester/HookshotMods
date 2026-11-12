#!/usr/bin/env python3
"""Goron Link from Majora's Mask 3D (Grezzo, 3DS) onto Child Link's Switch model and skeleton.

Replaces the N64 source (mm_goron / goron_model.build_goron_bind_mesh / goron_anim.retarget) with the
higher-fidelity MM3D assets:
  model      actors/zelda2_link_goron_new.gar.lzs : goron/model/link_goron.cmb  (smooth skinned, <= 3 weights)
  animations actors/zelda2_link_new.gar.lzs       : goron/anim/*.csab           (Goron-specific set)

Correspondence: the MM3D bind pose (A-pose) <-> Link's bind pose. Each MM3D bone b is attached to a Link
bone B and placed about an anchor joint pair:  p_link = O_link + M_b @ (p_mm - O_mm),  M_b = C_b @ Sw_b
(C_b = identity for body parts, a rotation turning the MM bone axis onto Link's for limb segments; Sw_b =
uniform scale, lengthwise fit for limb segments, vertical squash for the torso). A vertex with several
influences gets the weighted blend of its bones' placements, and keeps the weights on the Link bones.

Animation retarget (same formula as goron_anim, reference = MM3D bind):
    D(t) = R_mm(b, t) @ R_mm(b, bind)^T ;  W_B(t) = D(t) @ C_b^T @ W_B(bind) ;  local = W_parent^T @ W_B
"""

import math
import os
import struct
from typing import Dict, List

import numpy as np

from . import cmb, goron_model, mm3d
from .goron_anim import _depth, euler_xyz

MODEL_ARCHIVE, MODEL_FILE = "zelda2_link_goron_new", "goron/model/link_goron.cmb"
ANIM_ARCHIVE = "zelda2_link_new"
SKIP_MATERIALS = {2, 3, 4}          # bottle (p_bin_00/01): only drawn while holding one
ATLAS_SIZE = goron_model.ATLAS_SIZE

# MM3D bone -> (Link bone, kind). Bone roles from the bind skeleton (faces +Z, the character's left = +X):
#  0 root, 1 hip-height root, 2 lower body, 3-5 left thigh/shin/foot, 6-8 right, 9 upper root, 10 chest,
#  11 head, 12-13 hat, 14/18 clavicles, 15-17 left upper arm/forearm/hand, 19-21 right, 22 sheath, 23 extra
BONE_MAP = {
    0: ("Waist", "body"), 1: ("Waist", "body"), 2: ("Waist", "body"), 23: ("Waist", "body"),
    3: ("Leg_1_L", "chain"), 4: ("Leg_2_L", "chain"), 5: ("Ankle_L", "end"),
    6: ("Leg_1_R", "chain"), 7: ("Leg_2_R", "chain"), 8: ("Ankle_R", "end"),
    9: ("Spine_1", "torso"), 10: ("Spine_1", "torso"), 14: ("Spine_2", "torso"), 18: ("Spine_2", "torso"),
    11: ("Head_2", "head"), 12: ("Head_2", "head"), 13: ("Head_2", "head"), 22: ("Spine_2", "torso"),
    15: ("Arm_1_L", "chain"), 16: ("Arm_2_L", "chain"), 17: ("Wrist_L", "end"),
    19: ("Arm_1_R", "chain"), 20: ("Arm_2_R", "chain"), 21: ("Wrist_R", "end"),
}
CHAIN_CHILD = {3: 4, 4: 5, 6: 7, 7: 8, 15: 16, 16: 17, 19: 20, 20: 21}
# Animation: which MM3D bone drives which Link bone (world orientation)
ANIM_DRIVERS = {
    "Waist": 2, "Leg_1_L": 3, "Leg_2_L": 4, "Ankle_L": 5, "Leg_1_R": 6, "Leg_2_R": 7, "Ankle_R": 8,
    "Spine_1": 10, "Head": 11, "Arm_1_L": 15, "Arm_2_L": 16, "Wrist_L": 17,
    "Arm_1_R": 19, "Arm_2_R": 20, "Wrist_R": 21,
}
ROOT, UPPER, HEAD = 1, 9, 11

_model = None
_anims = None
_ball = None

# ---- rock ball (Goron roll): MM3D link_goron_iwa.cmb on a spare Link bone. Hiding is done by translation
# (parts parked HIDE_DIST below the ground): scale curves are ignored by this runtime for the player bones.
# MM draws gLinkGoronCurledDL instead of the skeleton while curled; here the ball shows only in the roll anims.
BALL_FILE = "goron/model/link_goron_iwa.cmb"
BALL_BONE = "Ph_Leg_Support_Waist_L"      # Waist child, own smooth matrix, animations only curve its rot Y
# Goron spikes (MM object_link_goron_DL_00C540; MM3D lg_spike_model.cmb): drawn with the curled ball's matrix in MM,
# so rigid on the other spare Waist leaf, laid out in the ball's frame; the overlay shows / scales it (unk_B86[1]).
SPIKE_FILE = "goron/model/lg_spike_model.cmb"
SPIKE_BONE = "Ph_Leg_Support_Waist_R"
# Charge fire (MM object_link_goron_DL_0127B0 / DL_0134D0, drawn translucent over the curled ball while charging;
# MM3D grt_01_model / grt_02_model): rigid on another spare Waist bone in the ball's frame, in a second (translucent)
# shape. Its children (skirt physics bones) are unused by the Goron mesh.
FIRE_FILES = ("goron/model/grt_01_model.cmb", "goron/model/grt_02_model.cmb")
FIRE_BONE = "Ph_WaistBelt_1"
# Fire textures in the Goron atlas (free strip under link_goron_01 / left of the ball), 64x32 each
FIRE_ATLAS = ((0, 202), (36, 202))
FIRE_TILE = (32, 54)                         # (w, h) in the atlas: the 32x64 masks resampled to fit the strip
FIRE_PARAMS = None
HIDE_DIST = 1000.0                        # metres below the ground
# chain roots moved away while curled (their children follow); Waist-skinned vertices stay inside the ball
BODY_ROOTS = ["Spine_1", "Leg_1_L", "Leg_1_R"]
BODY_HIDE = "scale"                       # "scale" (blend-friendly) or "translate"
# Bone carrying the ball spin (MM Player_Draw RotateXS(shape.rot.x)). Not Waist (a layer overrides it in the sprint)
# and not the ball's own Ph_ bone (the engine overrides its rotation).
SPIN_BONE = os.environ.get("GORON_SPIN_BONE", "Skl_Root")
BODY_SCALE = 1e-3
# (Waist is not hidden by scale: the ball is its child and scaling it stops the spin reaching the ball.)
BODY_BONES = ["Spine_1", "Spine_2", "Clavicle_L", "Clavicle_R", "Head", "Head_2",
              "Arm_1_L", "Arm_2_L", "Wrist_L", "Arm_1_R", "Arm_2_R", "Wrist_R",
              "Leg_1_L", "Leg_2_L", "Ankle_L", "Leg_1_R", "Leg_2_R", "Ankle_R"]


def ball_model() -> "cmb.CMB":
    global _ball
    if _ball is None:
        _ball = cmb.CMB(mm3d.actor_archive(MODEL_ARCHIVE)[BALL_FILE])
    return _ball


def ball_geometry():
    """(triangles [(v, v, v)], centre (MM3D units), radius) of the rock ball in MM3D model space."""
    m = ball_model()
    tris = [t for mi in range(len(m.meshes)) for t in m.mesh_triangles(mi)]
    P = np.array([v["p"] for t in tris for v in t])
    c = (P.min(0) + P.max(0)) / 2
    return tris, c, float(np.linalg.norm(P - c, axis=1).max()), float(P[:, 1].min())


def spike_triangles():
    """lg_spike_model triangles (MM3D model space, same frame as the ball). Its UVs run past [0, 1] on the
    rock texture (repeat); remapped linearly into [0, 1] so they fit the ball's atlas slot."""
    m = cmb.CMB(mm3d.actor_archive(MODEL_ARCHIVE)[SPIKE_FILE])
    tris = [t for mi in range(len(m.meshes)) for t in m.mesh_triangles(mi)]
    U = np.array([v["uv"] for t in tris for v in t])
    lo, hi = U.min(0), U.max(0)
    sc = np.maximum(hi - lo, 1.0)
    out = []
    for t in tris:
        out.append(tuple(dict(v, uv=tuple(((np.array(v["uv"]) - lo) / sc) * 0.984 + 0.008)) for v in t))
    return out


def fire_mesh(bf):
    """(verts, tris) of the charge fire shells in Link's bind space, rigid on FIRE_BONE, laid out like the ball,
    UVs in the Goron atlas (FIRE_ATLAS). Vertices shared within each source mesh."""
    fit = Fit(bf)
    bones, by = fit.bones, fit.by
    fb = by[FIRE_BONE]
    Wf = fit.W[fb]
    sm = bones[fb]["smooth"]
    _t, bc, _r, _y = ball_geometry()
    verts, tris = [], []
    for half, f in enumerate(FIRE_FILES):
        m = cmb.CMB(mm3d.actor_archive(MODEL_ARCHIVE)[f])
        tw, th = FIRE_TILE
        x0, y0 = FIRE_ATLAS[half]
        for mi in range(len(m.meshes)):
            vid = {}
            for tri in m.mesh_triangles(mi):
                out = []
                for v in tri:
                    key = (tuple(np.round(v["p"], 3)), tuple(v["uv"]))
                    if key not in vid:
                        pl = fit.s * (v["p"] - bc)
                        nn = Wf[:3, :3] @ v["n"]
                        u, w = (min(max(c, 0.0), 1.0) for c in v["uv"])
                        vid[key] = len(verts)
                        verts.append(dict(p=(Wf @ np.append(pl, 1.0))[:3], n=nn / (np.linalg.norm(nn) or 1),
                                          uv=((x0 + u * tw) / ATLAS_SIZE, (y0 + w * th) / ATLAS_SIZE),
                                          infl=[(sm, 1.0)], bone=None))
                    out.append(vid[key])
                tris.append(tuple(out))
    return verts, tris


_BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32) / 16.0 + 1.0 / 32


def stamp_fire(img):
    """MM3D's grayscale grt masks into the atlas: MM's env colour (155, 0, 0) brightened by the mask; alpha = the
    mask, thresholded to the atlas's 1-bit (BC1 punch-through) alpha for the alpha-masked material.
    Everything else is made opaque (MM3D's own textures carry alpha that the opaque Goron never used)."""
    img[..., 3] = 255
    for half, f in enumerate(FIRE_FILES):
        m = cmb.CMB(mm3d.actor_archive(MODEL_ARCHIVE)[f])
        src = m.texture_image(0)[..., 0].astype(np.float32) / 255.0
        w, h = FIRE_TILE
        ys = np.clip(((np.arange(h) + 0.5) / h * src.shape[0]).astype(int), 0, src.shape[0] - 1)
        xs = np.clip(((np.arange(w) + 0.5) / w * src.shape[1]).astype(int), 0, src.shape[1] - 1)
        mk = src[ys][:, xs]
        x0, y0 = FIRE_ATLAS[half]
        rgb = np.stack([155 + 100 * mk, 25 + 140 * mk * mk, 8 + 40 * mk ** 3], -1)
        dither = np.tile(_BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w]
        # 1-bit alpha: the mask thresholded (not dithered: at 32x54 texels dither reads as scattered diamonds);
        # the alpha test on the filtered alpha gives the flame tongues smooth edges
        alpha = np.where(mk > 0.33, 255, 0)
        del dither
        img[y0:y0 + h, x0:x0 + w, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        img[y0:y0 + h, x0:x0 + w, 3] = alpha.astype(np.uint8)
    return img


def ball_centre_height(bf):
    """Model-space height of the ball centre with its lowest point on the ground."""
    fit = Fit(bf)
    _, c, _r, ymin = ball_geometry()
    return fit.s * (c[1] - ymin)


def model() -> "cmb.CMB":
    global _model
    if _model is None:
        _model = cmb.CMB(mm3d.actor_archive(MODEL_ARCHIVE)[MODEL_FILE])
    return _model


def animation(name) -> "cmb.CSAB":
    global _anims
    if _anims is None:
        _anims = mm3d.actor_archive(ANIM_ARCHIVE)
    return cmb.CSAB(_anims[f"goron/anim/{name}.csab"])


def anim_names():
    global _anims
    if _anims is None:
        _anims = mm3d.actor_archive(ANIM_ARCHIVE)
    return sorted(k.split("/")[-1][:-5] for k in _anims if k.startswith("goron/anim/") and k.endswith(".csab"))


class Fit:
    """Scales, anchors and per-bone placements shared by the skeleton fit, the mesh and the retarget."""

    def __init__(self, bf: "goron_model.Bfres"):
        m = model()
        self.bones = bones = bf.bones()
        self.by = by = {b["name"]: i for i, b in enumerate(bones)}
        self.W = W = bf.bone_world(bones)
        self.mmW = mmW = [np.array(x) for x in m.bone_world]
        O_mm = lambda i: mmW[i][:3, 3]
        O_lk = lambda n: W[by[n]][:3, 3]
        self.O_mm, self.O_lk = O_mm, O_lk
        self.s = s = W[by["Skl_Root"]][1, 3] / O_mm(ROOT)[1]          # MM3D units -> metres (hip heights)
        self.s_up = np.linalg.norm(O_lk("Head") - O_lk("Spine_1")) / np.linalg.norm(O_mm(HEAD) - O_mm(UPPER))
        self.place = {}       # MM3D bone -> (Link bone index, O_mm, O_link, M, C)

    def compute_placements(self):
        bones, by, W, mmW, s = self.bones, self.by, self.W, self.mmW, self.s
        O_mm, O_lk = self.O_mm, self.O_lk
        m = model()

        def bone_dir(bname):
            bi = by[bname]
            child = next(i for i, b in enumerate(bones) if b["parent"] == bi and
                         b["name"].split("_")[0] in ("Arm", "Wrist", "Leg", "Ankle"))
            return W[child][:3, 3] - W[bi][:3, 3]

        C_of = {}
        for b in sorted(BONE_MAP):
            bname, kind = BONE_MAP[b]
            a_mm, a_lk = O_mm(b), O_lk(bname)
            if kind == "chain":
                d_lk = bone_dir(bname)
                d_mm = O_mm(CHAIN_CHILD[b]) - O_mm(b)
                C = goron_model._align(d_mm, d_lk)
                k = np.linalg.norm(d_lk) / (np.linalg.norm(d_mm) * s)
                u = d_mm / np.linalg.norm(d_mm)
                Sw = s * (np.eye(3) + (k - 1) * np.outer(u, u))
                C_of[b] = C
            elif kind == "end":
                C = C_of[m.bones[b]["parent"]]
                Sw = s * np.eye(3)
            elif kind == "torso":
                a_mm, a_lk = O_mm(UPPER), O_lk("Spine_1")
                C, Sw = np.eye(3), np.diag([s, self.s_up, s])
            elif kind == "head":
                a_mm, a_lk = O_mm(HEAD), O_lk("Head")
                C, Sw = np.eye(3), s * np.eye(3)
            else:
                a_mm, a_lk = O_mm(2), O_lk("Waist")
                C, Sw = np.eye(3), s * np.eye(3)
            self.place[b] = (by[bname], a_mm, a_lk, C @ Sw, C)
        return self.place


def apply_skeleton(bf: "goron_model.Bfres", log=print):
    """Goron proportions on Link's skeleton (from the MM3D bind): shoulder joints (Arm_1) where MM3D's sit on
    the placed torso, hip joints (Leg_1) with MM3D's spacing at Link's hip height, MM3D upper-arm/forearm
    lengths. Rewrites the inverse bind matrices. Returns {bone: new local translation}."""
    fit = Fit(bf)
    bones, by, W, s = fit.bones, fit.by, fit.W, fit.s
    O_mm, O_lk = fit.O_mm, fit.O_lk
    world_target = {}
    for side, (sh, th) in (("L", (15, 3)), ("R", (19, 6))):
        world_target[f"Arm_1_{side}"] = O_lk("Spine_1") + np.diag([s, fit.s_up, s]) @ (O_mm(sh) - O_mm(UPPER))
        hip = O_lk("Waist") + s * (O_mm(th) - O_mm(2))
        hip[1] = O_lk(f"Leg_1_{side}")[1]
        world_target[f"Leg_1_{side}"] = hip
    new_t = {}
    for name, tgt in world_target.items():
        p = bones[by[name]]["parent"]
        new_t[name] = (np.linalg.inv(W[p]) @ np.append(tgt, 1.0))[:3]
    for side, (sh, el, wr) in (("L", (15, 16, 17)), ("R", (19, 20, 21))):
        for bone, (a, b) in (("Arm_2", (sh, el)), ("Wrist", (el, wr))):
            name = f"{bone}_{side}"
            t = np.array(bones[by[name]]["trans"], float)
            new_t[name] = t / np.linalg.norm(t) * np.linalg.norm(O_mm(b) - O_mm(a)) * s
    arr = bf.u64(bf.fskl + 0x10)
    for name, t in new_t.items():
        struct.pack_into("<3f", bf.d, arr + by[name] * 0x58 + 0x4C, *t)
        log(f"  skeleton {name}: {np.round(bones[by[name]]['trans'], 3)} -> {np.round(t, 3)}")
    bones = bf.bones()
    W = bf.bone_world(bones)
    inv_arr = bf.u64(bf.fskl + 0x20)
    for b, Wb in zip(bones, W):
        if b["smooth"] >= 0:
            struct.pack_into("<12f", bf.d, inv_arr + b["smooth"] * 48, *np.linalg.inv(Wb)[:3].reshape(-1))
    return {n: tuple(float(x) for x in t) for n, t in new_t.items()}


def bone_translations(bfres_bytes: bytes):
    return apply_skeleton(goron_model.Bfres(bfres_bytes), log=lambda s: None)


def build_bind_mesh(bf: "goron_model.Bfres", log=print):
    """Goron mesh in Link's bind space. Returns (verts, tris, tex_keys): verts {p, n, uv, infl: [(smooth, w)]},
    tex_keys[i] = MM3D texture index of triangle i."""
    m = model()
    fit = Fit(bf)
    place = fit.compute_placements()
    bones, by = fit.bones, fit.by
    spine2_y = fit.O_lk("Spine_2")[1]
    verts, tris, keys = [], [], []
    vid = {}
    n_skip = 0
    for mi, mesh in enumerate(m.meshes):
        if mesh["material"] in SKIP_MATERIALS:
            continue
        tex = m.materials[mesh["material"]]["binders"][0]["tex"]
        for tri in m.mesh_triangles(mi):
            out = []
            for v in tri:
                key = (mi, id(v))          # dicts are alive for this mesh only
                if key not in vid:
                    p = np.zeros(3); n = np.zeros(3); infl = {}
                    tot = sum(w for _, w in v["bones"]) or 1.0
                    for b, w in v["bones"]:
                        if b not in place:
                            b = 2
                        bi, a_mm, a_lk, M, _C = place[b]
                        w /= tot
                        q = a_lk + M @ (v["p"] - a_mm)
                        p += w * q
                        n += w * (M @ v["n"])
                        if bones[bi]["name"] == "Spine_1" and q[1] > spine2_y:
                            bi = by["Spine_2"]                 # upper chest follows Spine_2 (as the N64 build)
                        sm = bones[bi]["smooth"]
                        infl[sm] = infl.get(sm, 0.0) + w
                    n /= np.linalg.norm(n) or 1
                    infl = sorted(infl.items(), key=lambda kv: -kv[1])[:4]
                    vid[key] = len(verts)
                    verts.append(dict(p=p, n=n, uv=tuple(v["uv"]), infl=infl, bone=None))
                out.append(vid[key])
            tris.append(tuple(out))
            keys.append(tex)
    # rock ball: rigid on BALL_BONE, laid out in that bone's local frame (p_local = s * (p_mm - centre)), so
    # the bone's animated world matrix places it directly: v_world = M_ball(t) @ p_local
    bb = by[BALL_BONE]
    Wb = fit.W[bb]
    btris, bc, _r, _ymin = ball_geometry()
    sm = bones[bb]["smooth"]
    bid = {}
    for tri in btris:
        out = []
        for v in tri:
            if id(v) not in bid:
                pl = fit.s * (v["p"] - bc)
                bid[id(v)] = len(verts)
                verts.append(dict(p=(Wb @ np.append(pl, 1.0))[:3], n=Wb[:3, :3] @ v["n"] / np.linalg.norm(Wb[:3, :3] @ v["n"]),
                                  uv=tuple(v["uv"]), infl=[(sm, 1.0)], bone=None))
            out.append(bid[id(v)])
        tris.append(tuple(out))
        keys.append("ball")
    # spikes: rigid on SPIKE_BONE in the same ball frame (MM draws DL_00C540 with the ball's matrix)
    sb = by[SPIKE_BONE]
    Ws = fit.W[sb]
    ssm = bones[sb]["smooth"]
    for tri in spike_triangles():
        out = []
        for v in tri:
            pl = fit.s * (v["p"] - bc)
            nn = Ws[:3, :3] @ v["n"]
            out.append(len(verts))
            verts.append(dict(p=(Ws @ np.append(pl, 1.0))[:3], n=nn / (np.linalg.norm(nn) or 1), uv=tuple(v["uv"]),
                              infl=[(ssm, 1.0)], bone=None))
        tris.append(tuple(out))
        keys.append("ball")
    log(f"  MM3D Goron mesh: {len(verts)} vertices, {len(tris)} triangles "
        f"(max {max(len(v['infl']) for v in verts)} weights/vertex)")
    return verts, tris, keys


ATLAS_LAYOUT = {   # texture -> (x, y) in the 256x256 atlas
    "link_goron_00": (0, 0),          # 256x128, full width (wraps onto itself horizontally)
    "link_goron_01": (4, 132),        # 64x64
    "link_goron_e00": (76, 132),      # 32x32 eyes
    "ball": (128, 128),               # link_goron_iwa_ 128x128 (rock ball)
}


def atlas(verts, tris, keys, log=print):
    """Pack the MM3D textures into one ATLAS_SIZE^2 image at native resolution (ATLAS_LAYOUT), with
    replicated-edge padding where there is room. Rewrites vertex UVs."""
    m, bm = model(), ball_model()
    pad = 4
    images = {}
    for t in set(keys):
        if t == "ball":
            images[t] = bm.texture_image(0)
        else:
            images[t] = m.texture_image(t)
    slots = {}
    img = np.zeros((ATLAS_SIZE, ATLAS_SIZE, 4), np.uint8)
    img[..., 3] = 255
    for t, tex in images.items():
        name = "ball" if t == "ball" else m.textures[t]["name"]
        x0, y0 = ATLAS_LAYOUT[name]
        if tex.shape[2] == 3:
            tex = np.concatenate([tex, np.full(tex.shape[:2] + (1,), 255, np.uint8)], 2)
        h, w = tex.shape[:2]
        padded = np.pad(tex, ((pad, pad), (pad, pad), (0, 0)), mode="edge")
        ys, xs = y0 - pad, x0 - pad
        sy0, sx0 = max(0, -ys), max(0, -xs)
        sy1 = padded.shape[0] - max(0, ys + padded.shape[0] - ATLAS_SIZE)
        sx1 = padded.shape[1] - max(0, xs + padded.shape[1] - ATLAS_SIZE)
        img[ys + sy0:ys + sy1, xs + sx0:xs + sx1] = padded[sy0:sy1, sx0:sx1]
        slots[t] = (x0, y0, w, h)
        log(f"  atlas: {name} {w}x{h} at ({x0},{y0})")
    for t, (x0, y0, w, h) in slots.items():          # re-stamp the textures over neighbours' padding
        tex = images[t]
        img[y0:y0 + h, x0:x0 + w, :tex.shape[2]] = tex
    tex_of = {}
    for tri, t in zip(tris, keys):
        for i in tri:
            assert tex_of.setdefault(i, t) == t
    out_v = []
    for i, v in enumerate(verts):
        v = dict(v)
        x0, y0, w, h = slots[tex_of[i]]
        u, vv = (min(max(c, 0.0), 1.0) for c in v["uv"])
        v["uv"] = ((x0 + u * w) / ATLAS_SIZE, (y0 + vv * h) / ATLAS_SIZE)
        out_v.append(v)
    return out_v, list(tris), img


def apply(bfres_bytes: bytes, log=print):
    """goron_model.apply_goron with the MM3D mesh. Returns (bfres bytes, verts, tris, albedo atlas)."""
    bf = goron_model.Bfres(bfres_bytes)
    apply_skeleton(bf, log)
    verts, tris, keys = build_bind_mesh(bf, log)
    verts, tris, img = atlas(verts, tris, keys, log)
    img = stamp_fire(img)
    fv, ft = fire_mesh(bf)                    # (before the parts are parked: it uses the fire bone's bind place)
    hide_ball_in_skeleton(bf, log)
    data = goron_model.write_mesh(bf, bfres_bytes, verts, tris, log,
                                  extra=[(goron_model.FIRE_SHAPE, fv, ft, goron_model.FIRE_MATERIAL,
                                          goron_model.FIRE_MATERIAL_TEXTURES,
                                          (next(i for i, b in enumerate(bf.bones()) if b["name"] == FIRE_BONE), 1.5),
                                          FIRE_PARAMS)])
    return data, verts, tris, img


def _rotw(W, i):
    return W[i][:3, :3] / np.linalg.norm(W[i][:3, :3], axis=0)


def ball_hidden_translation(bf, bone=None):
    """Local translation of BALL_BONE that parks it HIDE_DIST below the Waist (bind orientation)."""
    bones = bf.bones()
    by = {b["name"]: i for i, b in enumerate(bones)}
    W = bf.bone_world(bones)
    return tuple(float(x) for x in _rotw(W, by["Waist"]).T @ np.array([0.0, -HIDE_DIST, 0.0]))


def hide_ball_in_skeleton(bf, log=print):
    """Park the ball and spike bones below the ground in the skeleton bind (inverse bind matrices are already
    written, so the mesh is unaffected; animations that move the bone show the part)."""
    bones = bf.bones()
    t = ball_hidden_translation(bf)
    arr = bf.u64(bf.fskl + 0x10)
    for name in (BALL_BONE, SPIKE_BONE, FIRE_BONE):
        bi = next(i for i, b in enumerate(bones) if b["name"] == name)
        assert name == FIRE_BONE or not any(b["parent"] == bi for b in bones), f"{name} must be a leaf"
        struct.pack_into("<3f", bf.d, arr + bi * 0x58 + 0x4C, *t)
    log(f"  rock ball on {BALL_BONE}, spikes on {SPIKE_BONE}, charge fire on {FIRE_BONE}: parked {HIDE_DIST:.0f} m below in the skeleton")


def mm_roll_driver(n_frames, fps=30.0, v0=9.53, speed_target=15.6, start_tick=0, steady=False):
    """MM's curled-ball driver, simulated per 20 Hz tick and sampled at `fps` (Player_Action_96 + func_808577E0 +
    Player_Draw): av2 (spin, binang/frame) steps toward speed_target*900 by +0x7D0 (cap 0x4B0 down), shape.rot.x
    accumulates it; unk_ABC (squash/stretch) follows |av2|*0.00004 through the unk_B48 spring, clamped -0.7..0.3.
    Returns (spin angle [F] radians, abc [F]). steady=True: start at the steady state (sprint loop)."""
    import math
    av2 = int(14040 if steady else v0 * 500)
    abc, b48, ang = (0.3 if steady else 0.0), 0.0, 0.0
    target = int(speed_target * 900)
    ticks = int(math.ceil(n_frames * 20.0 / fps)) + 2
    hist_ang, hist_abc = [0.0], [abc]
    for t in range(ticks):
        if t >= start_tick:
            step = 0x7D0 if target - av2 >= 0 else 0x4B0          # Math_AsymStepToS(av2, spDC, 0x7D0, 0x4B0)
            av2 = min(av2 + step, target) if target >= av2 else max(av2 - step, target)
            # func_808577E0
            want = abs(av2) * 0.00004
            b48 += 0.08 if abc < want else -0.07
            b48 = min(max(b48, -0.2), 0.14)
            if abs(b48) < 0.12:
                abc += b48                                          # Math_StepUntilF(&abc, want, b48)
                if (b48 > 0 and abc >= want) or (b48 < 0 and abc <= want):
                    abc = want
                    b48 = 0.0
            else:
                abc = min(max(abc + b48, -0.7), 0.3)
            ang += av2 * math.pi / 32768.0                          # shape.rot.x += av2
        hist_ang.append(ang); hist_abc.append(min(max(abc, -0.7), 0.3))
    out_ang, out_abc = [], []
    for f in range(n_frames + 1):
        x = f * 20.0 / fps
        i = int(x); a = x - i
        out_ang.append(hist_ang[i] * (1 - a) + hist_ang[min(i + 1, len(hist_ang) - 1)] * a)
        out_abc.append(hist_abc[i] * (1 - a) + hist_abc[min(i + 1, len(hist_abc) - 1)] * a)
    return np.array(out_ang), np.array(out_abc)


MM_BALL_SCALE = 1.15                      # Player_Draw: Matrix_Scale(... * 1.15)


def roll_animation(bf, n_frames, spin_per_frame, uncurl=None, curl=None, spin=None, abc=None):
    """Bone curves for a curled roll: body chains parked below the ground, the rock ball centred above the
    ground spinning forward (about +X) by spin_per_frame radians per frame.
    uncurl=(k, rot, root_tr): from frame k on, show the body posed by a retarget result instead (ball off).
    curl=(k, rot, root_tr): frames 0..k-1 show the body posed by a retarget result (k+1 samples), then the ball."""
    from .goron_anim import euler_xyz as _e
    bones = bf.bones()
    by = {b["name"]: i for i, b in enumerate(bones)}
    W = bf.bone_world(bones)
    skl, waist = by["Skl_Root"], by["Waist"]
    centre_y = ball_centre_height(bf)
    F = n_frames + 1
    R_skl = _rotw(W, skl)                     # Skl_Root keeps its bind rotation (parent Root = identity)
    skl_local = R_skl                         # Root is identity, so local = world
    # while curled the Waist is world-aligned, so the ball's local rotation is a pure X spin (no euler
    # branch flips between keys)
    waist_local = R_skl.T
    skl_t = np.array([bones[skl]["trans"][0], centre_y, bones[skl]["trans"][2]])
    p_waist = skl_t + R_skl @ np.array(bones[waist]["trans"])
    on = np.ones(F, bool)
    if uncurl is not None:
        on[uncurl[0]:] = False
    if curl is not None:
        on[:curl[0]] = False
    down = np.array([0.0, -HIDE_DIST, 0.0])
    # The spin rides on Waist (a regular bone): the engine overrides the rotation of the Ph_ helper bone carrying the
    # ball, but the ball inherits its parent's. With Skl_Root's bind rotation = identity, Waist local = Rx(spin).
    assert np.allclose(R_skl, np.eye(3), atol=1e-4), "spin on Waist assumes Skl_Root bind rotation = identity"
    ang = spin if spin is not None else spin_per_frame * np.arange(F)
    waist_spin = np.stack([ang, np.zeros(F), np.zeros(F)], 1)
    if SPIN_BONE == "Skl_Root":
        # spin on Skl_Root (layers that override Waist in the sprint leave the lower-body root alone); Waist and the
        # ball keep identity so the ball turns about the model's lateral X axis
        spec = {"Skl_Root": {"trans": np.tile(skl_t, (F, 1)), "rot": waist_spin.copy()},
                "Waist": {"rot": np.tile(_e(waist_local), (F, 1))}}
    else:
        spec = {"Skl_Root": {"trans": np.tile(skl_t, (F, 1)), "rot": np.tile(_e(skl_local), (F, 1))},
                "Waist": {"rot": waist_spin.copy()}}

    centre = np.array([0.0, centre_y, 0.0])
    brot = np.zeros((F, 3))
    hidden_ball = np.array(ball_hidden_translation(bf))
    btr = np.array([(centre - p_waist) if on[f] else hidden_ball for f in range(F)])
    spec[BALL_BONE] = {"rot": brot, "trans": btr}
    if BODY_HIDE == "scale":
        # scale every body bone (works whether or not scale is inherited); engine blends then shrink/grow the body
        # instead of passing it through the ground
        sc = np.where(on, BODY_SCALE, 1.0)[:, None].repeat(3, 1)
        for n in BODY_BONES:
            if n in by:
                spec.setdefault(n, {})["scale"] = sc
    else:
        for n in BODY_ROOTS:
            bi = by[n]
            parent_w = R_skl if bones[bi]["parent"] == skl else np.eye(3)      # Spine_1 under Skl_Root, legs under Waist
            bind_t = np.array(bones[bi]["trans"])
            spec[n] = {"trans": np.array([parent_w.T @ down if on[f] else bind_t for f in range(F)])}
    if uncurl is not None:
        k, rot, root_tr = uncurl
        for bi, v in rot.items():
            v = np.unwrap(v, axis=0)
            name = bones[bi]["name"]
            full = np.vstack([np.tile(v[0], (k, 1)), v[:F - k]])
            if name == "Waist":
                full[:k] = waist_spin[:k] if SPIN_BONE == "Waist" else _e(waist_local)
            spec.setdefault(name, {})["rot"] = full
        spec["Skl_Root"]["trans"] = np.vstack([np.tile(skl_t, (k, 1)), root_tr[:F - k]])
    if curl is not None:
        k, rot, root_tr = curl
        for bi, v in rot.items():
            v = np.unwrap(v, axis=0)
            name = bones[bi]["name"]
            full = np.vstack([v[:k], np.tile(v[k - 1], (F - k, 1))])
            if name == "Waist":
                full[k:] = waist_spin[k:] if SPIN_BONE == "Waist" else _e(waist_local)
            spec.setdefault(name, {})["rot"] = full
        spec["Skl_Root"]["trans"] = np.vstack([root_tr[:k], np.tile(skl_t, (F - k, 1))])
    if SPIN_BONE == "Skl_Root":
        rot = spec["Skl_Root"]["rot"]
        rot[~on] = _e(skl_local)                                  # body frames: bind rotation
    if abc is not None:
        # Player_Draw: Scale(1 - abc/2, 1 + abc, max(..)) * 1.15, outside the spin (on Skl_Root, Waist carries the spin);
        # the centre is raised so the stretched ball still rests on the ground. Ball frames only.
        sy = (1.0 + abc) * MM_BALL_SCALE
        sxz = (1.0 - abc * 0.5) * MM_BALL_SCALE
        sz = np.maximum(1.0 + abc, 1.0 - abc * 0.5) * MM_BALL_SCALE
        scale = np.ones((F, 3))
        scale[on] = np.stack([sxz, sy, sz], 1)[on]
        spec["Skl_Root"]["scale"] = scale
        tr = spec["Skl_Root"]["trans"]
        tr[on, 1] = centre_y * sy[on]
    return spec


# ---- animations ---------------------------------------------------------------------------------------
FPS_MM, FPS_OUT = 20.0, 30.0


def _world_rots(anim: "cmb.CSAB", t: float):
    """World rotation matrices of the MM3D bones at fractional frame t (per-frame tracks, linear in euler
    with wrap-safe interpolation)."""
    m = model()
    f0 = int(math.floor(t)); a = t - f0
    out = []
    for i, b in enumerate(m.bones):
        _s0, r0, _t0 = anim.sample(i, f0, b)
        if a > 1e-6:
            _s1, r1, _t1 = anim.sample(i, f0 + 1, b)
            r = [x + (((y - x + math.pi) % (2 * math.pi)) - math.pi) * a for x, y in zip(r0, r1)]
        else:
            r = r0
        R = cmb._euler_zyx(*r)
        p = b["parent"]
        out.append(out[p] @ R if p >= 0 else R)
    return out


def retarget(bf: "goron_model.Bfres", names, log=print, n_frames=None, looping=True):
    """names: one MM3D Goron animation name or a list played back to back. Same contract as
    goron_anim.retarget: (n_frames, {bone index: [F, 3] local euler}, root translation [F, 3], bones, W_bind)."""
    names = [names] if isinstance(names, str) else list(names)
    fit = Fit(bf)
    place = fit.compute_placements()
    bones, by, W_bind = fit.bones, fit.by, fit.W
    m = model()
    anims = [animation(n) for n in names]
    seq = [(a, f) for a in anims for f in range(a.duration + 1)]      # frame list (each anim: 0..duration)
    n_src = len(seq)
    n_out = n_frames or int(round(n_src * FPS_OUT / FPS_MM))
    if looping:
        src_t = lambda f: f * n_src / n_out
    else:
        src_t = lambda f: f * (n_src - 1) / max(1, n_out)
    ref = [W[:3, :3] / np.linalg.norm(W[:3, :3], axis=0) for W in fit.mmW]

    def src_rots(t):
        i = int(math.floor(t))
        if looping:
            i %= n_src
        i = min(i, n_src - 1)
        a, f = seq[i]
        frac = t - math.floor(t)
        nxt = i + 1 if i + 1 < n_src else (0 if looping else i)
        if frac < 1e-6 or seq[nxt][0] is not a:
            return _world_rots(a, f)
        return _world_rots(a, f + frac)

    drive = {by[lk]: (b, place[b][4]) for lk, b in ANIM_DRIVERS.items()}
    rot_w = {i: W_bind[i][:3, :3] / np.linalg.norm(W_bind[i][:3, :3], axis=0) for i in range(len(bones))}
    local_bind = {i: (rot_w[b["parent"]].T @ rot_w[i]) if b["parent"] >= 0 else rot_w[i] for i, b in enumerate(bones)}
    order = sorted(range(len(bones)), key=lambda i: _depth(bones, i))
    n_samples = n_out + (0 if looping else 1)
    out = {bi: np.zeros((n_samples, 3)) for bi in drive}
    root_tr = np.zeros((n_samples, 3))
    skl = by["Skl_Root"]
    al, ar = by["Ankle_L"], by["Ankle_R"]
    ankle_bind_y = min(W_bind[al][1, 3], W_bind[ar][1, 3])
    mid0 = None
    for f in range(n_samples):
        R = src_rots(src_t(f))
        Wr = {}
        for i in order:
            p = bones[i]["parent"]
            pw = Wr[p] if p >= 0 else np.eye(3)
            if i in drive:
                b, C = drive[i]
                Wr[i] = R[b] @ ref[b].T @ C.T @ rot_w[i]
                out[i][f] = euler_xyz(pw.T @ Wr[i])
            else:
                Wr[i] = pw @ local_bind[i]
        P = {}
        for i in order:
            p = bones[i]["parent"]
            t = np.array(bones[i]["trans"], float)
            P[i] = (P[p] + Wr[p] @ t) if p >= 0 else t
        lift = min(P[al][1], P[ar][1]) - ankle_bind_y
        mid = (P[al] + P[ar]) / 2
        if mid0 is None:
            mid0 = mid.copy()
        slide = mid - mid0
        root_tr[f] = np.array(bones[skl]["trans"]) - np.array([slide[0], lift, slide[2]])
    log(f"  retargeted MM3D {'+'.join(names)} ({n_src} frames) -> {n_out} frames on {len(out)} bones")
    return n_out, out, root_tr, bones, W_bind


# ---- code-driven Goron animation (the overlay's MM animation player) -----------------------------------------------
# Clips built with driven_clip_spec read every pose channel from the archive's GORONDRV block (bfres_anim.Driver); the
# overlay (overlay/goron_hooks.inc) writes the slots each frame, playing pg_wait / pg_maru_change from the tables
# exported below or posing the curled ball exactly as MM's Player_Draw does.
DRIVER_BONES = ["Leg_1_L", "Leg_2_L", "Ankle_L", "Leg_1_R", "Leg_2_R", "Ankle_R", "Spine_1", "Head",
                "Arm_1_L", "Arm_2_L", "Wrist_L", "Arm_1_R", "Arm_2_R", "Wrist_R"]
SLOT_SKL_ROT, SLOT_SKL_SCALE, SLOT_SKL_TRANS, SLOT_WAIST_ROT, SLOT_BALL_TRANS, SLOT_BODY_SCALE = 0, 3, 6, 9, 12, 15
SLOT_BONE0 = 18                                   # + 3 * DRIVER_BONES index
SLOT_BALL_ROT = 60                                # (left at 0 by the overlay)
DRIVER_ANIMS = ("pg_wait", "pg_maru_change")


def _bind_local_euler(bones, i):
    from .goron_anim import euler_xyz as _e
    return tuple(float(x) for x in bones[i]["rot"])


def driven_clip_spec(bf):
    from .bfres_anim import Driver
    bones = bf.bones()
    by = {b["name"]: i for i, b in enumerate(bones)}
    skl = by["Skl_Root"]
    spec = {"Skl_Root": {"rot": Driver(SLOT_SKL_ROT, _bind_local_euler(bones, skl)),
                         "scale": Driver(SLOT_SKL_SCALE, (1.0, 1.0, 1.0)),
                         "trans": Driver(SLOT_SKL_TRANS, tuple(bones[skl]["trans"]))},
            "Waist": {"rot": Driver(SLOT_WAIST_ROT, _bind_local_euler(bones, by["Waist"]))},
            BALL_BONE: {"trans": Driver(SLOT_BALL_TRANS, ball_hidden_translation(bf)),
                        "rot": Driver(SLOT_BALL_ROT, (0.0, 0.0, 0.0))}}
    for n in BODY_BONES:
        if n in by:
            spec.setdefault(n, {})["scale"] = Driver(SLOT_BODY_SCALE, (1.0, 1.0, 1.0))
    for k, n in enumerate(DRIVER_BONES):
        spec.setdefault(n, {})["rot"] = Driver(SLOT_BONE0 + 3 * k, _bind_local_euler(bones, by[n]))
    return spec


def export_driver_tables(bf, path, log=print):
    """goron_driver.bin for the overlay:
    'GDRV' u32 version 1, u32 nbones (DRIVER_BONES), u32 nanims,
    f32 centre_y, f32 hidden ball translation[3], f32 Skl_Root bind translation[3], f32 Waist bind euler[3],
    f32 bone bind euler[nbones][3],
    per animation: char name[24], u32 nframes (MM native 20 fps frames), f32 frames[nframes][3 + 3 + 3*nbones]
    (Skl_Root translation, Waist euler, bone eulers; euler tracks unwrapped)."""
    bones = bf.bones()
    by = {b["name"]: i for i, b in enumerate(bones)}
    out = bytearray(b"GDRV" + struct.pack("<III", 5, len(DRIVER_BONES), len(DRIVER_ANIMS)))
    out += struct.pack("<f", ball_centre_height(bf))
    out += struct.pack("<3f", *ball_hidden_translation(bf))
    out += struct.pack("<3f", *bones[by["Skl_Root"]]["trans"])
    out += struct.pack("<3f", *_bind_local_euler(bones, by["Waist"]))
    for n in DRIVER_BONES:
        out += struct.pack("<3f", *_bind_local_euler(bones, by[n]))
    # v2: skeleton bone indices (Skl_Root, Waist, ball, DRIVER_BONES..., then the body-hide list)
    body = [by[n] for n in BODY_BONES if n in by]
    out += struct.pack("<3I", by["Skl_Root"], by["Waist"], by[BALL_BONE])
    out += struct.pack(f"<{len(DRIVER_BONES)}I", *[by[n] for n in DRIVER_BONES])
    out += struct.pack("<I", len(body)) + struct.pack(f"<{len(body)}I", *body)
    # v3: chain roots moved away while curled (no scale tricks): index + bind translation
    out += struct.pack("<I", len(BODY_ROOTS))
    for n in BODY_ROOTS:
        out += struct.pack("<I3f", by[n], *bones[by[n]]["trans"])
    out += struct.pack("<I", by[SPIKE_BONE])           # v4: spike bone (same local frame as the ball bone)
    out += struct.pack("<I", by[FIRE_BONE])            # v5: charge fire bone
    for name in DRIVER_ANIMS:
        a = animation(name)
        n_src = a.duration + 1
        n, rot, root_tr, _b, _w = retarget(bf, name, log=lambda s: None, n_frames=n_src - 1, looping=False)
        F = n + 1
        cols = [root_tr]
        w = rot.get(by["Waist"])
        cols.append(np.unwrap(w, axis=0) if w is not None else np.tile(_bind_local_euler(bones, by["Waist"]), (F, 1)))
        for bn in DRIVER_BONES:
            v = rot.get(by[bn])
            cols.append(np.unwrap(v, axis=0) if v is not None else np.tile(_bind_local_euler(bones, by[bn]), (F, 1)))
        table = np.hstack(cols).astype(np.float32)
        out += name.encode().ljust(24, b"\0") + struct.pack("<I", F) + table.tobytes()
        log(f"  driver table {name}: {F} frames x {table.shape[1]} values")
    with open(path, "wb") as f:
        f.write(out)
    return path
