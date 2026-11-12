#!/usr/bin/env python3
"""Build the "Goron Link" mod from vanilla RomFS.

Milestone 1 (data only): Child Link takes on Goron Link's body dimensions, collision size and
roll speed, using ratios from Majora's Mask (see RESEARCH_NOTES.md section 4). The model is
still Child Link's; the Goron model comes in a later milestone.

Everything is rebuilt from vanilla romfs on every run (the script is idempotent), and the
output goes to a mod folder managed by the Hookshot mod manager.

Usage:
    python3 -m mod_manager.build_goron_link            # build into mods/<NNN>-GoronLink
    python3 -m mod_manager.build_goron_link --dry-run  # print the changes, write nothing
    python3 -m mod_manager.build_goron_link --probe    # also tag params so their memory layout can be mapped
"""

import argparse
import numpy as np
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from mod_manager import byml, zstd_helper  # noqa: E402
from mod_manager.core import HookshotModManager  # noqa: E402
from mod_manager.rstb import ResourceSizeTable, estimate_size  # noqa: E402

MOD_NAME = "GoronLink"
MOD_DESCRIPTION = "Play as Majora's Mask Goron Link (work in progress)"
RSTB_REL = "System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs"
PLAYER_PACK = "Pack/Actor/Player_Child.pack.zs"
MODEL_REL = "Model/Player_Child.Player_Child.bfres.zs"
# Link animation -> (MM Goron Link animation, "loop" | "fit"). "fit" stretches the MM animation to the Link
# animation's exact length so hit frames, door/chest sync and camera cuts stay aligned.
# Source assets: Majora's Mask 3D (CMB model + CSAB animations, mod_manager/goron3d.py); False = N64 ROM
USE_MM3D = True
PLAYER_ANIM_ARCHIVES = [f"Model/Player_Child_{k}_Animation.anim.bfres.zs" for k in ("01", "02", "03", "04", "Demo", "Cba")]
_PUNCH = {1: ("pg_punchA", "pg_punchAend"), 2: ("pg_punchB", "pg_punchBend"), 3: ("pg_punchC", "pg_punchCend")}
ANIM_SWAPS = {
    **{n: ("pg_wait", "loop") for n in ("Wait", "Wait_Sword", "Wait_Shield", "Wait_Sword_Shield", "Wait_Boomerang",
                                         "Wait_Boomerang_Shield", "Wait_Bottle", "Wait_Bottle_Shield", "Wait_DekuStick",
                                         "Wait_DekuStick_Burning", "Wait_Sling", "Wait_Sling_Empty", "Wait_Cucco",
                                         "Wait_Fishing")},
    # every sword combo step becomes the Goron 1-2-3 punch combo
    **{f"Sword_Attack_{d}{i}_From_{'L' if i % 2 else 'R'}": (_PUNCH[(i - 1) % 3 + 1], "fit")
       for d in ("Horizontal", "Vertical") for i in range(1, 7)},
    "Mask_Change_Off": ("pg_maskoffstart", "fit"),
    "Cliff_Climb_Ed": ("pg_climb_endBL", "fit"),
    "Jump_Clamber": ("pg_climb_startA", "fit"),
    "Evt_Door_Key": ("pg_doorA_open", "fit"),
    "Evt_Door_Failure": ("pg_doorB_open", "fit"),
    **{n: ("pg_Tbox_open", "fit") for n in (
        "Demo_Front_OpenTreasureBox_Child", "Demo_Front_OpenTreasureBox_Child_Ang90",
        "Demo_Front_OpenTreasureBox_Child_Ang90Minus", "Demo_Front_OpenTreasureBox_Child_StepBack",
        "Demo_Front_OpenTreasureBox_Child_StepFront", "Demo_Front_OpenTreasureBox_Child_StepMiddle")},
}

# --- Goron body, derived from MM sPlayerAgeProperties (GORON vs HUMAN) and this game's adult ---
# MM: ceilingCheckHeight 70 vs 40 (x1.75), wallCheckRadius 19.5 vs 14 (x1.39).
# Adult Link here is 1.68 m tall with a 0.35 m radius, which matches Goron Link's MM proportions
# relative to child Link (1.17 m / 0.23 m), so the adult values are used as the Goron size.
CHILD_HEIGHT, GORON_HEIGHT = 1.17, 1.68
CHILD_RADIUS, GORON_RADIUS = 0.23, 0.35
SCALE_V = GORON_HEIGHT / CHILD_HEIGHT   # vertical
SCALE_H = GORON_RADIUS / CHILD_RADIUS   # horizontal

# PlayerGameParam keys that describe body size: take the base (adult-sized) value.
DIMENSION_KEYS = [
    "PlayerHeight", "PlayerShoulderHeight", "PlayerStepHeight", "PlayerCrouchHeight", "CctRadius",
    "BoneSpine1Height", "ShieldHeight", "LiftHighLimitHeight", "PutHighLimitHeight",
    "FloatEffectiveHeight", "WaterSurfaceSwimRiseHeight", "InvalidFishsingWaterHeight",
    "InvalidOcarinaWaterHeight", "MinWaterSurfaceLockOnHeight", "EnableCrouchSinkLength",
    "AttentionNormalOffsetY", "AttentionCrouchOffsetY", "AttentionClimbOffsetY", "AttentionSwimOffsetY",
]
# Keys measured on the model's skeleton: leave alone while the model is still Child Link's.
MODEL_BOUND_KEYS = {"BoneSpine1Height"}
# Roll: MM Goron ball reaches 18 units/frame at 20 fps. With Link ~45 units tall (1 unit ~ 2.6 cm)
# that is ~9.4 m/s. Child Link's dodge roll is 5.625 m/s.
GORON_ROLL_SPEED = 9.0
SPEED_KEYS = {
    "RollingMaxSpeed": GORON_ROLL_SPEED,
    "LockOnRollingMaxSpeed": GORON_ROLL_SPEED,
    "RollingMaxSpeedHeavy": GORON_ROLL_SPEED / 2,
    "LockOnRollingMaxSpeedHeavy": GORON_ROLL_SPEED / 2,
}


def _scale_shape(node, key=None):
    """Scale a Phive ShapeParam tree: X/Z/Radius horizontally, Y/Height* vertically."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "Tensor":
                out[k] = {a: (c * SCALE_H * SCALE_H if isinstance(c, float) else c) for a, c in v.items()}
            elif k == "Volume" and isinstance(v, float):
                out[k] = v * SCALE_H * SCALE_H * SCALE_V
            else:
                out[k] = _scale_shape(v, k)
        return out
    if isinstance(node, list):
        return [_scale_shape(x, key) for x in node]
    if isinstance(node, float) and type(node) is float:
        if key in ("X", "Z", "Radius"):
            return node * SCALE_H
        if key == "Y" or (key or "").startswith("Height"):
            return node * SCALE_V
    return node


def patch_player_child(files, log):
    gp_child = "Player/PlayerGameParam/Player_Child.game__player__PlayerGameParam.bgyml"
    gp_base = "Player/PlayerGameParam/Player.game__player__PlayerGameParam.bgyml"
    child = byml.parse_byml(files[gp_child])
    base = byml.parse_byml(files[gp_base])
    for k in DIMENSION_KEYS:
        if k in base:
            old = child.get(k, "(inherited)")
            child[k] = base[k]
            log(f"  PlayerGameParam.{k}: {old} -> {base[k]}")
        elif k in child and k not in MODEL_BOUND_KEYS:
            child[k] = float(child[k] * SCALE_V)
            log(f"  PlayerGameParam.{k}: no base value; scaled x{SCALE_V:.3f} -> {child[k]:.3f}")
        elif k in child:
            log(f"  PlayerGameParam.{k}: tied to the skeleton; kept {child[k]} until the Goron model lands")
    for k, v in SPEED_KEYS.items():
        log(f"  PlayerGameParam.{k}: {child.get(k, base.get(k))} -> {v}")
        child[k] = float(v)
    files[gp_child] = byml.write_byml(child)

    shapes = sorted(n for n in files if n.startswith("Phive/ShapeParam/") and n.endswith("_Child.phive__ShapeParam.bgyml"))
    for n in shapes:
        files[n] = byml.write_byml(_scale_shape(byml.parse_byml(files[n])))
    log(f"  Scaled {len(shapes)} child collision shapes (H x{SCALE_H:.3f}, V x{SCALE_V:.3f})")
    return files


def _tag_float(v, idx):
    """Replace the low 8 mantissa bits with idx: a ~0.003% change that makes the value unique."""
    import struct as _s
    bits = _s.unpack("<I", _s.pack("<f", v))[0]
    return byml.float_bits_to_float((bits & ~0xFF) | idx)


def tag_params_for_probe(files, log):
    """Probe build: give every non-zero PlayerGameParam float a unique low-byte tag so its location
    in memory can be found exactly (overlay Param Tuner / tools/analyze_dump.py)."""
    gp_child = "Player/PlayerGameParam/Player_Child.game__player__PlayerGameParam.bgyml"
    gp_base = "Player/PlayerGameParam/Player.game__player__PlayerGameParam.bgyml"
    child = byml.parse_byml(files[gp_child])
    base = byml.parse_byml(files[gp_base])
    names = []
    for k in sorted(set(base) | set(child)):
        v = child.get(k, base.get(k))
        if type(v) is float:
            names.append((k, None))
        elif isinstance(v, dict):
            names += [(k, a) for a in ("X", "Y", "Z") if type(v.get(a)) is float]
    tags, idx = {}, 0
    for k, a in names:
        src = child if k in child else base
        v = src[k] if a is None else src[k][a]
        if v == 0.0:
            continue
        idx += 1
        if idx > 255:
            raise RuntimeError("too many floats to tag in one byte")
        nv = _tag_float(v, idx)
        if a is None:
            src[k] = nv
        else:
            src[k] = dict(src[k]); src[k][a] = nv
        tags[f"{k}.{a}" if a else k] = idx
    files[gp_child] = byml.write_byml(child)
    files[gp_base] = byml.write_byml(base)
    log(f"  PROBE: tagged {len(tags)} PlayerGameParam floats with unique low-byte ids")
    return tags


# Whole-model scale (skeleton + mesh + animations) via ModelInfo.DebugModelScale, which shipped actors use
# (dogs 0.85, boss projectiles 1.5). The Goron mesh on Child Link's skeleton is ~1.25 m tall; 1.35x makes him
# ~1.68 m, matching the Goron collision height set above (MM's Goron Link is far bigger than child Link).
GORON_MODEL_SCALE = 1.35
MODEL_INFO = "Component/ModelInfo/Player_Child.engine__component__ModelInfo.bgyml"


# Equipment Goron Link never carries (MM: no sword/shield/sheath as Goron). The player-held actors get their
# own ModelInfo scaled to nothing; shop/chest/NPC actors that share the model files are unaffected.
HIDDEN_EQUIPMENT = ["Weapon_Sword_Kokiri", "Weapon_Shield_Deku", "Weapon_Sheath_Kokiri"]
HIDDEN_SCALE = 0.0001

GORON_MODEL_INFO = "Component/ModelInfo/Player_Child_Goron.engine__component__ModelInfo.bgyml"
ACTOR_PARAM = "Actor/Player_Child.engine__actor__ActorParam.bgyml"


def scale_model(files, log):
    """The field player gets its own ModelInfo (only in Player_Child.pack) with the scale; the shared
    Player_Child ModelInfo (also used by the pause-menu PouchPlayer_Child) stays vanilla."""
    info = byml.parse_byml(files[MODEL_INFO])
    info["DebugModelScale"] = {"X": float(GORON_MODEL_SCALE), "Y": float(GORON_MODEL_SCALE), "Z": float(GORON_MODEL_SCALE)}
    files[GORON_MODEL_INFO] = byml.write_byml(info)
    ap = byml.parse_byml(files[ACTOR_PARAM])
    ref = "?" + GORON_MODEL_INFO
    old = ap["Components"].get("ModelInfoRef")
    ap["Components"]["ModelInfoRef"] = ref
    files[ACTOR_PARAM] = byml.write_byml(ap)
    log(f"  ModelInfoRef: {old} -> {ref} (DebugModelScale {GORON_MODEL_SCALE}x)")


def hide_actor_model(romfs, actor, log):
    """Give an actor its own ModelInfo (only in its own pack) with a ~0 DebugModelScale. Returns (rel, pack bytes)."""
    rel = f"Pack/Actor/{actor}.pack.zs"
    raw, files = load_pack(romfs, rel)
    ap_name = f"Actor/{actor}.engine__actor__ActorParam.bgyml"
    ap = byml.parse_byml(files[ap_name])
    ref = ap.get("Components", {}).get("ModelInfoRef")
    if ref is None:                      # inherited from a $parent ActorParam: find the ModelInfo file in the pack
        cands = [n for n in files if n.startswith("Component/ModelInfo/")]
        src = cands[0]
    else:
        src = ref.lstrip("?")
    info = byml.parse_byml(files[src])
    info["DebugModelScale"] = {"X": HIDDEN_SCALE, "Y": HIDDEN_SCALE, "Z": HIDDEN_SCALE}
    new_name = f"Component/ModelInfo/{actor}_GoronHidden.engine__component__ModelInfo.bgyml"
    files[new_name] = byml.write_byml(info)
    ap.setdefault("Components", {})["ModelInfoRef"] = "?" + new_name
    files[ap_name] = byml.write_byml(ap)
    log(f"  {actor}: hidden (own ModelInfo, scale {HIDDEN_SCALE}) [was {src.split('/')[-1]}]")
    return rel, raw, zstd_helper.create_sarc(files)


# Goron roll (visual): every Link dodge-roll animation becomes the curled rock ball (MM3D link_goron_iwa),
# spinning at the roll speed; the roll-to-run transitions uncurl with pg_maru_change played backwards.
DRIVEN_ANIMS = os.environ.get("GORON_DRIVEN", "0") == "1"   # idles / Rolling_* / Move_Dash* driven by the overlay
# the standard idles (Link's equipment picks the variant): code-driven so the overlay can curl him while standing
IDLE_RE = re.compile(r"^Wait(_(Sword|Shield|Sword_Shield|Boomerang|Boomerang_Shield|Bottle|Bottle_Shield|DekuStick|"
                     r"DekuStick_Burning|Sling|Sling_Empty|Cucco|Fishing))?$")
ROLL_UNCURL_AT = 2          # frames of ball at the start of Rolling_*_To_Run_*
ROLL_CURL_FRAMES = 3        # Rolling_*: body curls (pg_maru_change, fast) for these frames, then the ball pops in.
                            # Switching inside the animation (like MM's display-list swap) keeps the engine's
                            # blends body->body and ball->ball.


def build_roll_animations(archives, bf, touched, log):
    from mod_manager import goron3d, bfres_anim
    radius = goron3d.ball_centre_height(bf) * GORON_MODEL_SCALE          # metres in the world
    spin = GORON_ROLL_SPEED / 30.0 / radius                               # rad per 30 fps frame
    log(f"  roll ball: radius {radius:.2f} m, spin {spin:.3f} rad/frame at {GORON_ROLL_SPEED} m/s")
    n_ball = n_uncurl = n_dash = 0
    # The overlay draws the ball in code (MM Player_Draw) and poses the curl/uncurl itself, so these clips only need
    # plain body poses: anything shown without the override (a frame before the roll code starts, after it ends, or
    # mid-blend) is then a normal curled Goron, never scale/visibility tricks that blend into garbage.
    curl_cache = {}

    def plain_curl(n, reverse=False):
        key = (n, reverse)
        if key not in curl_cache:
            _n, rot, root_tr, bones, _w = goron3d.retarget(bf, "pg_maru_change", log=lambda s: None, n_frames=n, looping=False)
            spec = {}
            for bi, v in rot.items():
                v = np.unwrap(v, axis=0)
                if reverse:
                    v = v[::-1]
                else:
                    v = np.tile(v[-1], (n + 1, 1))                 # held curled pose
                spec[bones[bi]["name"]] = {"rot": v}
            tr = root_tr[::-1] if reverse else np.tile(root_tr[-1], (n + 1, 1))
            spec["Skl_Root"] = {"trans": tr}
            curl_cache[key] = spec
        return curl_cache[key]

    plain = os.environ.get("GORON_PLAIN_ROLL_CLIPS", "1") == "1"
    for rel, (_, arch, fs) in archives.items():
        for name, f in fs.items():
            if plain and (name.startswith("Move_Dash") or name.startswith("Rolling_")):
                n = arch.u32(f + 0x40)
                reverse = "_To_Run_" in name
                arch.replace_animation(name, n, plain_curl(n, reverse),
                                       looping=True if name.startswith("Move_Dash") else None)
                touched.add(rel)
                if reverse: n_uncurl += 1
                elif name.startswith("Move_Dash"): n_dash += 1
                else: n_ball += 1
                continue
            if DRIVEN_ANIMS and (IDLE_RE.match(name) or name.startswith("Move_Dash") or
                                 (name.startswith("Rolling_") and "_To_Run_" not in name)):
                # Code-driven (the overlay's MM animation player writes every pose channel each frame)
                n = arch.u32(f + 0x40)
                arch.replace_animation(name, n, goron3d.driven_clip_spec(bf),
                                       looping=True if (IDLE_RE.match(name) or name.startswith("Move_Dash")) else None)
                touched.add(rel)
                n_dash += 1
                continue
            if name.startswith("Move_Dash"):
                # Goron Link has no sprint: holding A keeps him rolling (MM Player_Action_96). The game's own
                # roll -> hold-A sprint chain therefore plays the ball. Looping clip: the spin per frame is rounded
                # so one loop is a whole number of turns.
                n = arch.u32(f + 0x40)
                mm_spin = 14040 * math.pi / 32768.0 * 20.0 / 30.0     # MM av2 at full roll speed, per 30 fps frame
                turns = max(1, round(mm_spin * n / (2 * math.pi)))
                abc = np.full(n + 1, 0.3)                              # unk_ABC at full spin (clamped)
                spec = goron3d.roll_animation(bf, n, turns * 2 * math.pi / n, abc=abc)
                if os.environ.get("GORON_DRIVER", "1") == "1":     # spin from the overlay's MM ball driver
                    spec["Skl_Root"]["rot"] = bfres_anim.Driver(0)
                if os.environ.get("GORON_DASH_MARK"):          # (test) ball height ramps 0..mark over the loop: shows the frame
                    spec[goron3d.BALL_BONE]["trans"][:, 1] += np.linspace(0.0, float(os.environ["GORON_DASH_MARK"]), n + 1)
                arch.replace_animation(name, n, spec, looping=True)
                touched.add(rel)
                n_dash += 1
                continue
            if not name.startswith("Rolling_"):
                continue
            n = arch.u32(f + 0x40)
            if "_To_Run_" in name:
                k = ROLL_UNCURL_AT
                _n, rot, root_tr, _b, _w = goron3d.retarget(bf, "pg_maru_change", log=lambda s: None,
                                                             n_frames=n - k, looping=False)
                rot = {bi: v[::-1] for bi, v in rot.items()}
                ang, abc = goron3d.mm_roll_driver(n, steady=True)
                spec = goron3d.roll_animation(bf, n, spin, uncurl=(k, rot, root_tr[::-1]), spin=ang, abc=abc)
                n_uncurl += 1
            else:
                n = int(os.environ.get("GORON_ROLL_FRAMES", n))      # (test: a longer ball animation = a longer roll)
                k = ROLL_CURL_FRAMES
                _n, rot, root_tr, _b, _w = goron3d.retarget(bf, "pg_maru_change", log=lambda s: None,
                                                             n_frames=k, looping=False)
                ang, abc = goron3d.mm_roll_driver(n, start_tick=int(round(k * 20.0 / 30.0)))
                spec = goron3d.roll_animation(bf, n, spin, curl=(k, rot, root_tr), spin=ang, abc=abc)
                if os.environ.get("GORON_ROLL_ROOTDIST"):   # (test) Root bone forward travel over the clip, metres
                    dist = float(os.environ["GORON_ROLL_ROOTDIST"])
                    tr = np.zeros((n + 1, 3)); tr[:, 2] = np.linspace(0.0, dist, n + 1)
                    spec["Root"] = {"trans": tr}
                n_ball += 1
            loop_roll = os.environ.get("GORON_ROLL_LOOP") == "1" and "_To_Run_" not in name
            arch.replace_animation(name, n, spec, looping=True if loop_roll else None)
            touched.add(rel)
    log(f"  roll: {n_ball} Rolling_* animations -> rock ball, {n_uncurl} Rolling_*_To_Run_* -> ball + uncurl, "
        f"{n_dash} driven / sprint clips")


def find_or_create_mod(mgr):
    for m in mgr.list_mods():
        if m.folder_name.split("-", 1)[-1] == MOD_NAME:
            return m.base_path
    folder = mgr.create_mod(MOD_NAME, description=MOD_DESCRIPTION, category="Gameplay", version="0.1.0")
    return os.path.join(mgr.mods_dir, folder)


PACK_INDEX_CACHE = os.path.join(HERE, "cache", "pack_index.json")


def pack_index(romfs, log):
    """{file path inside a pack: [pack paths relative to romfs]} for every Pack/**/*.pack.zs (cached)."""
    stamp = os.path.getmtime(os.path.join(romfs, "Pack"))
    try:
        with open(PACK_INDEX_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
        if cache.get("stamp") == stamp and cache.get("romfs") == romfs:
            return cache["index"]
    except (OSError, ValueError):
        pass
    log("Indexing packs (first run only)...")
    index = {}
    import glob
    for p in glob.glob(os.path.join(romfs, "Pack", "**", "*.pack.zs"), recursive=True):
        rel = os.path.relpath(p, romfs)
        try:
            with open(p, "rb") as f:
                files = zstd_helper.unpack_sarc_to_dict(zstd_helper.decompress_zs(f.read(), os.path.basename(p), romfs))
        except Exception:
            continue
        for n in files:
            index.setdefault(n, []).append(rel)
    os.makedirs(os.path.dirname(PACK_INDEX_CACHE), exist_ok=True)
    with open(PACK_INDEX_CACHE, "w", encoding="utf-8") as f:
        json.dump({"stamp": stamp, "romfs": romfs, "index": index}, f)
    return index


def load_pack(romfs, rel):
    with open(os.path.join(romfs, rel), "rb") as f:
        raw = zstd_helper.decompress_zs(f.read(), os.path.basename(rel), romfs)
    files = zstd_helper.unpack_sarc_to_dict(raw)
    assert zstd_helper.create_sarc(files) == raw, f"SARC writer does not reproduce {rel}; aborting"
    return raw, files


def build(dry_run=False, probe=False, with_model=True):
    mgr = HookshotModManager()
    romfs = mgr.romfs_dir
    lines = []
    log = lambda s: (print(s), lines.append(s))  # noqa: E731

    log(f"== Goron Link build (from vanilla {romfs})")
    _, vanilla = load_pack(romfs, PLAYER_PACK)
    files = dict(vanilla)
    log("Player_Child pack:")
    files = patch_player_child(files, log)
    if with_model:
        scale_model(files, log)
    tags = tag_params_for_probe(files, log) if probe else None
    changed = {n: d for n, d in files.items() if vanilla.get(n) != d}
    added = {n: d for n, d in changed.items() if n not in vanilla}

    # The engine caches resources by path: when several packs carry the same file, whichever pack
    # loads first supplies it (PouchPlayer_Child's copy of the PlayerGameParam is loaded before
    # Player_Child's). So every pack holding an identical vanilla copy gets the patched file.
    index = pack_index(romfs, log)
    targets = sorted({pk for n in changed for pk in index.get(n, [])} | ({PLAYER_PACK} if added else set()))
    rstb = ResourceSizeTable.load(os.path.join(romfs, RSTB_REL))
    out_packs = {}
    out_files = {}

    if with_model:
        log("Equipment:")
        for actor in HIDDEN_EQUIPMENT:
            rel, raw_pack, new_pack = hide_actor_model(romfs, actor, log)
            old_entry = rstb.get(rel)
            rstb.set(rel, max(old_entry or 0, estimate_size(rel, len(new_pack))))
            out_packs[rel] = new_pack

    # Goron model: MM Goron Link's mesh rebuilt on Child Link's skeleton (same file size, RSTB unchanged)
    if with_model:
        from mod_manager import goron_model
        log("Model:")
        with open(os.path.join(romfs, MODEL_REL), "rb") as f:
            bfres = zstd_helper.decompress_zs(f.read(), os.path.basename(MODEL_REL), romfs)
        if USE_MM3D:
            from mod_manager import goron3d
            new_bfres, _, _, palette_grid = goron3d.apply(bfres, log)
        else:
            new_bfres, _, _, palette_grid = goron_model.apply_goron(bfres, log)
        out_files[MODEL_REL] = zstd_helper.compress_zs(new_bfres, os.path.basename(MODEL_REL), romfs)
        for rel, data in goron_model.build_textures(romfs, palette_grid, log).items():
            out_files[rel] = zstd_helper.compress_zs(data, os.path.basename(rel), romfs)
        # Animations: MM Goron Link animations retargeted onto Link's skeleton, replacing Link's own
        from mod_manager import bfres_anim, goron_anim, mm_goron
        log("Animations:")
        bf_for_anim = goron_model.Bfres(new_bfres)          # the Goron-proportioned skeleton
        goron_trans = (goron3d.bone_translations(bfres) if USE_MM3D else goron_model.goron_bone_translations(bfres))
        archives = {}
        for rel in PLAYER_ANIM_ARCHIVES:
            with open(os.path.join(romfs, rel), "rb") as f:
                raw_anim = zstd_helper.decompress_zs(f.read(), os.path.basename(rel), romfs)
            arch = bfres_anim.AnimArchive(raw_anim)
            archives[rel] = (raw_anim, arch, arch.fskas())
        touched = set()
        for rel, (_, arch, _) in archives.items():           # Goron proportions in every animation's base pose
            done, skipped = goron_model.patch_animation_base_translations(arch, goron_trans)
            touched.add(rel)
            log(f"  {rel.split('/')[-1].split('.')[0]}: Goron bone translations in {done} bone tracks ({skipped} curve-driven, left)")
            if USE_MM3D:      # the rock ball stays hidden in every animation but the roll (base data: before any swap)
                done, other = goron_model.patch_animation_base_translations(
                    arch, {goron3d.BALL_BONE: goron3d.ball_hidden_translation(bf_for_anim),
                           goron3d.SPIKE_BONE: goron3d.ball_hidden_translation(bf_for_anim),
                           goron3d.FIRE_BONE: goron3d.ball_hidden_translation(bf_for_anim)})
                log(f"    {goron3d.BALL_BONE} (rock ball) parked below ground in {done} animations" + (f" (! {other} curve-driven)" if other else ""))
        for link_name, (mm_name, mode) in ANIM_SWAPS.items():
            if IDLE_RE.match(link_name) and USE_MM3D and DRIVEN_ANIMS:
                continue                                       # (Wait is code-driven: build_roll_animations)
            where = [rel for rel, (_, _, fs) in archives.items() if link_name in fs]
            if not where:
                log(f"  ! {link_name}: not found in the player archives; skipped")
                continue
            for rel in where:
                raw_anim, arch, fs = archives[rel]
                link_frames = arch.u32(fs[link_name] + 0x40)
                looping = mode == "loop"
                n = None if looping else link_frames
                if USE_MM3D:
                    n, rot, root_tr, bones, _ = goron3d.retarget(bf_for_anim, mm_name, log=lambda s: None,
                                                                 n_frames=n, looping=looping)
                else:
                    mm_hdr = [goron_anim.MM_ANIMS[m] for m in mm_name] if isinstance(mm_name, tuple) else goron_anim.MM_ANIMS[mm_name]
                    n, rot, root_tr, bones, _ = goron_anim.retarget(bf_for_anim, mm_hdr,
                                                                    log=lambda s: None, n_frames=n, looping=looping)
                spec = {}
                for bi, v in rot.items():
                    v = np.unwrap(v, axis=0)
                    spec[bones[bi]["name"]] = {"rot": np.vstack([v, v[:1]]) if looping else v}
                spec["Skl_Root"] = {"trans": np.vstack([root_tr, root_tr[:1]]) if looping else root_tr}
                arch.replace_animation(link_name, n, spec, looping=looping)
                touched.add(rel)
                log(f"  {link_name} ({link_frames}f) <- {'+'.join(mm_name) if isinstance(mm_name, tuple) else mm_name} ({'loop, ' + str(n) + 'f' if looping else 'stretched'}) [{rel.split('/')[-1].split('.')[0]}]")
        if USE_MM3D:
            build_roll_animations(archives, bf_for_anim, touched, log)
        for rel in sorted(touched):
            raw_anim, arch, _ = archives[rel]
            new_anim = arch.to_bytes()
            vanilla_anim = raw_anim
            old_entry = rstb.get(rel)
            new_entry = max(old_entry or 0, estimate_size(rel, len(new_anim), old_entry, len(vanilla_anim)))
            rstb.set(rel, new_entry)
            log(f"  RSTB {rel[:-3]}: {old_entry} -> {new_entry} ({len(vanilla_anim)} -> {len(new_anim)} bytes)")
            out_files[rel] = zstd_helper.compress_zs(new_anim, os.path.basename(rel), romfs)
    for pk in targets:
        raw, pfiles = load_pack(romfs, pk)
        done = []
        for n, d in changed.items():
            if n in added:
                if pk == PLAYER_PACK:
                    pfiles[n] = d
                    done.append(n)
                continue
            if n in pfiles:
                if pfiles[n] != vanilla[n]:
                    log(f"  ! {pk}: its {n} differs from Player_Child's vanilla copy; left alone")
                    continue
                pfiles[n] = d
                done.append(n)
        if not done:
            continue
        new_pack = zstd_helper.create_sarc(pfiles)
        old_entry = rstb.get(pk)
        new_entry = max(old_entry or 0, estimate_size(pk, len(new_pack)))
        rstb.set(pk, new_entry)
        out_packs[pk] = new_pack
        log(f"{pk}: {len(done)} files patched; RSTB {old_entry} -> {new_entry} ({len(raw)} -> {len(new_pack)} bytes)")

    if dry_run:
        log("(dry run: nothing written)")
        return

    mod_dir = find_or_create_mod(mgr)
    out_romfs = os.path.join(mod_dir, "romfs")
    if with_model and USE_MM3D:                          # pose tables + bone indices for the overlay's MM animation player
        from mod_manager import goron3d
        goron3d.export_driver_tables(bf_for_anim, os.path.join(mod_dir, "goron_driver.bin"), log)
    # MM3D's Goron roll sound effects (sequence SFX rendered offline), played by the overlay
    from mod_manager import mm3d
    bcsar = os.path.join(mm3d.ROMFS, "sound", "sequence", "JokerSound.bcsar") if USE_MM3D else None
    if bcsar and os.path.exists(bcsar):
        from mod_manager import cseq
        log("Sounds:")
        cseq.export_goron_sfx(bcsar, os.path.join(mod_dir, "sfx"), log)

    def write(rel, data):
        p = os.path.join(out_romfs, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        log(f"wrote {os.path.relpath(p, mgr.game_dir)} ({len(data)} bytes)")

    # drop packs a previous build wrote that this build no longer patches
    old_dir = os.path.join(out_romfs, "Pack")
    if os.path.isdir(old_dir):
        for dp, _, fns in os.walk(old_dir):
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), out_romfs)
                if rel not in out_packs:
                    os.remove(os.path.join(dp, fn))
                    log(f"removed stale {rel}")
    for pk, data in out_packs.items():
        write(pk, zstd_helper.compress_zs(data, os.path.basename(pk), romfs))
    for rel, data in out_files.items():
        write(rel, data)
    for sub in ("Model", "Tex"):  # (Model/ also holds the animation archives)                     # drop model/texture files a previous build wrote
        dpath = os.path.join(out_romfs, sub)
        if os.path.isdir(dpath):
            for fn in os.listdir(dpath):
                rel = f"{sub}/{fn}"
                if rel not in out_files:
                    os.remove(os.path.join(dpath, fn))
                    log(f"removed stale {rel}")
    write(RSTB_REL, rstb.to_zs())
    probe_path = os.path.join(mod_dir, "probe_tags.json")
    if tags:
        with open(probe_path, "w", encoding="utf-8") as f:
            json.dump(tags, f, indent=1, sort_keys=True)
        log(f"wrote {os.path.relpath(probe_path, mgr.game_dir)}")
    elif os.path.exists(probe_path):
        os.remove(probe_path)
    with open(os.path.join(mod_dir, "BUILD_LOG.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    info_path = os.path.join(mod_dir, "info.json")
    with open(info_path, encoding="utf-8") as f:
        info = json.load(f)
    info.update(version="0.15.5" + ("-probe" if probe else ""), description=MOD_DESCRIPTION)
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print(f"Done: {mod_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="tag every PlayerGameParam float with a unique low-byte id (for memory mapping)")
    ap.add_argument("--no-model", action="store_true", help="leave Child Link's model alone")
    a = ap.parse_args()
    build(a.dry_run, a.probe, not a.no_model)
