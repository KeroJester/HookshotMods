#!/usr/bin/env python3
"""Build the "Item Controls" mod: N64-style item buttons for the OoT remake.

LB/RB + X/Y/B use six assignable items (assigned in the pause menu's Items tab with the same combo), LB/RB no longer
open the Quick Select wheel, and RT rolls/dashes. Everything runs in the overlay (overlay/item_controls.inc); the mod
folder carries no romfs files. This script writes:

  info.json                     mod manager metadata
  item_controls/items.txt       "<hash hex> <display name>" for every value of GameData Enum
                                PlayerStatus.AssignableItem.Child/.Adult (the game's RT item), for the overlay HUD
  item_controls/aim.txt         hashes of the aimed items (Boomerang, Slingshot, Bow + arrows, Hookshot, Longshot):
                                their combo latches RT until the bumper is released or the face button is pressed again
  item_controls/icons/*.rgba    64x64 RGBA icons from the game's pouch icons (UI/Tex/Icon/*.bntx, ASTC 4x4, decoded
                                by mod_manager/tools/astc_decode = the ASTC decoder Hookshot bundles from suyu)
  item_controls/icons.txt       "item <item hash> <file>", "bottle <bottle item hash> <contents enum key hash>",
                                "content <contents hash> <file>" (a bottle shows what is in it)
  romfs/UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.blarc.zs
                                the HUD layout PlayerStatus_00.buil with the vanilla RT item box (Pa_SelectItem_00)
                                and its RB/page-dots header (N_Shortcut_00) scaled to 0: the overlay's six boxes
                                take their place
  romfs/UI/CtrlGuideSetTable/CtrlGuideSetTable.game__ui__CtrlGuideSetTable.bgyml
                                the button prompts with the RT entries of the item sets removed ("Throw RT",
                                "Swing RT", ...): RT no longer uses items. Fishing and carried-object sets keep theirs.
  item_controls/slots.txt       (kept if present) the six assigned items, written by the overlay

The value names come from GameData (re/gamedatalist.pkl, decoded from the game's GameDataList); a value is the
murmur3 hash of its name.

Usage:
    python3 -m mod_manager.build_item_controls
"""

import json
import os
import pickle
import re
import struct
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                  # this source checkout (reference/hookshot-src submodule)
sys.path.insert(0, REPO)
from mod_manager.core import find_game_dir   # noqa: E402
ROOT = os.environ.get("HOOKSHOT_GAME_DIR") or find_game_dir()   # the game folder: romfs/ in, mods/ out
if not ROOT:
    raise SystemExit("Game folder not found: set HOOKSHOT_GAME_DIR or run the mod manager once")

MOD_FOLDER = "050-ItemControls"
MOD_NAME = "ItemControls"
MOD_VERSION = "0.4.0"
MOD_DESCRIPTION = "N64-style item buttons: LB/RB + X/Y/B use six assignable items; RT rolls (work in progress)"
ENUM_KEY = "PlayerStatus.AssignableItem.Child"

# aimed items: RT stays held after the combo (aim with the camera stick), released by letting go of the bumper or by
# pressing the same face button again
AIM = ["Boomerang", "SlingShot", "Bow", "FireArrow", "IceArrow", "LightArrow", "Hookshot", "Longshot"]

# enum value -> icon (romfs/UI/Tex/Icon/<name>.bntx.zs)
ICON = {
    "DekuStick": "Item_DekuStick", "DekuNut": "Item_DekuNut", "Bomb": "Item_Bomb", "Boomerang": "Item_Boomerang",
    "SlingShot": "Weapon_Range_SlingShot", "Hookshot": "Item_Hookshot", "Longshot": "Item_Longshot",
    "Hammer": "Item_Hammer", "GlassTruth": "Armor_GlassTruth", "Bombchu": "Item_Bombchu", "DinFire": "Item_DinFire",
    "FaroreWind": "Item_FaroreWind", "NayruLove": "Item_NayruLove", "MagicBean": "Item_MagicBean",
    "BottleA": "Item_Bottle", "BottleB": "Item_Bottle", "BottleC": "Item_Bottle", "BottleD": "Item_Bottle",
    "BottleE": "Item_Bottle", "FishingRod": "Item_FishingRod", "Lure": "Weapon_Lure", "NormalLure": "Weapon_Lure",
    "SinkLure": "Weapon_SinkingLure", "KeatonMask": "Armor_Child_Mask_Keaton", "SkullMask": "Armor_Child_Mask_Skull",
    "SpookyMask": "Armor_Child_Mask_Spooky", "BunnyMask": "Armor_Child_Mask_Bunny", "TruthMask": "Armor_Child_Mask_Truth",
    "GoronMask": "Armor_Child_Mask_Goron", "ZoraMask": "Armor_Child_Mask_Zora", "GerudoMask": "Armor_Child_Mask_Gerudo",
    "HeavyBoot": "Armor_Adult_HeavyBoot", "HoverBoot": "Armor_Adult_HoverBoot", "Bow": "Weapon_Range_Bow",
    "FireArrow": "Item_Arrow_Fire", "IceArrow": "Item_Arrow_Ice", "LightArrow": "Item_Arrow_Light",
    "Amiibo": "Armor_Child_Mask_Amiibo0",
}
for _n in ["WeirdEgg", "AlarmCucco", "ZeldaLetter", "PocketEgg", "PocketCucco", "CojiroCucco", "OddMushroom", "OddPowder",
           "PoacherSaw", "BrokenSword", "Prescription", "EyeballFrog", "EyeDrop", "ClaimCheck", "ScrubBusinessSeed"]:
    ICON[_n] = "Item_" + _n
BOTTLE_KEY = "PlayerStatus.AssignableItem.BottleInfo.Bottle{}.BottleItem"
CONTENT_ICON = {"Empty": "Item_Bottle", "MaxMilk": "Item_Bottle_Milk"}   # others: Item_Bottle_<name>
ICON_PX = 64

# display names for the HUD where the enum name is not readable as is
NICE = {
    "SlingShot": "Slingshot", "GlassTruth": "Lens of Truth", "DinFire": "Din's Fire", "FaroreWind": "Farore's Wind",
    "NayruLove": "Nayru's Love", "HeavyBoot": "Iron Boots", "HoverBoot": "Hover Boots", "ZeldaLetter": "Zelda's Letter",
    "SheikahWarp": "Sheikah Warp", "ScrubBusinessSeed": "Scrub Seed", "Hammer": "Megaton Hammer",
    "BottleA": "Bottle 1", "BottleB": "Bottle 2", "BottleC": "Bottle 3", "BottleD": "Bottle 4", "BottleE": "Bottle 5",
}


def mm3(key, seed=0):
    key = key.encode()
    h, c1, c2, n = seed, 0xCC9E2D51, 0x1B873593, len(key)
    for i in range(0, n // 4 * 4, 4):
        k = struct.unpack_from("<I", key, i)[0]
        k = (k * c1) & 0xFFFFFFFF; k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF; k = (k * c2) & 0xFFFFFFFF
        h ^= k; h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF; h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    t, k = key[n // 4 * 4:], 0
    for i, b in enumerate(t):
        k |= b << (8 * i)
    if t:
        k = (k * c1) & 0xFFFFFFFF; k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF; k = (k * c2) & 0xFFFFFFFF; h ^= k
    h ^= n; h ^= h >> 16; h = (h * 0x85EBCA6B) & 0xFFFFFFFF; h ^= h >> 13; h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    return h ^ (h >> 16)


def display(name):
    if name in NICE:
        return NICE[name]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def enum_values():
    data = pickle.load(open(os.path.join(ROOT, "re", "gamedatalist.pkl"), "rb"))["Data"]
    want = mm3(ENUM_KEY)
    for entry in data["Enum"]:
        if isinstance(entry, dict) and entry.get("Hash") == want:
            names = entry["RawValues"]
            assert all(mm3(n) == v for n, v in zip(names, entry["Values"])), "enum value hashes do not match"
            return names
    raise SystemExit(f"{ENUM_KEY} not found in re/gamedatalist.pkl")


def decode_icon(name, tmp):
    """romfs/UI/Tex/Icon/<name>.bntx.zs -> ICON_PX x ICON_PX RGBA bytes"""
    from mod_manager import bntx, zstd_helper
    path = os.path.join(ROOT, "romfs", "UI", "Tex", "Icon", name + ".bntx.zs")
    tex = bntx.Texture(zstd_helper.decompress_zs(open(path, "rb").read(), path, os.path.join(ROOT, "romfs")))
    assert tex.format >> 8 == 0x2D, f"{name}: not ASTC 4x4"
    blocks, rgba = os.path.join(tmp, "b.bin"), os.path.join(tmp, "o.rgba")
    open(blocks, "wb").write(tex.read_blocks(0).tobytes())
    subprocess.check_call([os.path.join(HERE, "tools", "astc_decode"), blocks, str(tex.width), str(tex.height),
                           str(tex.bw), str(tex.bhh), rgba])
    img = np.fromfile(rgba, np.uint8).reshape(tex.height, tex.width, 4)
    return Image.fromarray(img, "RGBA").resize((ICON_PX, ICON_PX), Image.LANCZOS).tobytes()


def build_astc_tool():
    tool = os.path.join(HERE, "tools", "astc_decode")
    if os.path.exists(tool):
        return
    suyu = os.path.join(REPO, "reference", "hookshot-src", "gpu", "third_party", "suyu")
    subprocess.check_call(["g++", "-O2", "-std=c++20", "-I" + os.path.join(HERE, "tools", "shim"), "-I" + suyu,
                           os.path.join(HERE, "tools", "astc_decode.cpp"),
                           os.path.join(suyu, "video_core", "textures", "astc.cpp"), "-o", tool, "-lpthread"])


HUD_ARCHIVE = "UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.blarc.zs"
HUD_LAYOUT = "PlayerStatus_00.buil"
HIDE_PANES = {"N_Shortcut_00": "N_ScaleR_00", "Pa_SelectItem_00": "N_ScaleR_00"}   # pane -> expected parent


def hud_panes(buil):
    """PlayerStatus_00.buil (BFFH 'Nintendo.Ui.ResModels.LayoutResData'): {pane name: record offset}.
    Pane records are 0x44 bytes (+0x10/+0x14 scale x/y, +0x28 parent index) listed by the u64 pointer table at
    0x320 (count u32 at 0x318); their order is the order of the pane names in the string pool."""
    count = struct.unpack_from("<I", buil, 0x318)[0]
    recs = [struct.unpack_from("<Q", buil, 0x320 + 8 * i)[0] for i in range(count)]
    start = buil.index(b"RootPane\0")
    names = buil[start:].split(b"\0")[:count]
    names = [n.decode() for n in names]
    panes = dict(zip(names, recs))
    for pane, parent in HIDE_PANES.items():
        par = struct.unpack_from("<I", buil, panes[pane] + 0x28)[0]
        assert names[par] == parent, f"{HUD_LAYOUT}: unexpected layout ({pane} parent {names[par]})"
    return panes


def patch_hud(mod):
    """Hide the vanilla RT item box (and its RB header) in the HUD layout by scaling those panes to 0."""
    from mod_manager import zstd_helper
    romfs = os.path.join(ROOT, "romfs")
    src = os.path.join(romfs, HUD_ARCHIVE)
    sarc = bytearray(zstd_helper.decompress_zs(open(src, "rb").read(), src, romfs))
    files = zstd_helper.unpack_sarc_to_dict(bytes(sarc))
    buil = files[HUD_LAYOUT]
    panes = hud_panes(buil)
    copies, at = 0, sarc.find(buil)
    while at >= 0:                                   # (the archive holds the layout's bytes twice: patch every copy)
        for pane in HIDE_PANES:
            struct.pack_into("<ff", sarc, at + panes[pane] + 0x10, 0.0, 0.0)
        copies += 1
        at = sarc.find(buil, at + 1)
    assert copies, f"{HUD_LAYOUT} not found in the archive"
    out = os.path.join(mod, "romfs", HUD_ARCHIVE)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(zstd_helper.compress_zs(bytes(sarc), out, romfs))


GUIDE_TABLE = "UI/CtrlGuideSetTable/CtrlGuideSetTable.game__ui__CtrlGuideSetTable.bgyml"
GUIDE_KEY_RT = 3432537025          # GuideKey of RT (the item button) in GuideSetArray[].GuideArray[]
# sets that keep their RT prompt (GuideType hashes): fishing (ThrowLure, lure/rod RollUp, Brake, Hook, RollUp) and
# carried objects (Throw, Throw + Put)
GUIDE_KEEP_RT = {mm3("ThrowLure"), 3948661679, 141619505, 1887781522, 1708067740, mm3("RollUp"), mm3("Throw"), 2190618626}


def patch_guides(mod):
    """Drop the RT entries from the item button prompts (GuideType = the item's name hash, e.g. Boomerang)."""
    from mod_manager import byml
    src = os.path.join(ROOT, "romfs", GUIDE_TABLE)
    data = open(src, "rb").read()
    table = byml.parse_byml(data)
    removed = 0
    for gs in table["GuideSetArray"]:
        if int(gs.get("GuideType", 0)) in GUIDE_KEEP_RT:
            continue
        keep = [g for g in gs["GuideArray"] if int(g.get("GuideKey", 0)) != GUIDE_KEY_RT]
        removed += len(gs["GuideArray"]) - len(keep)
        gs["GuideArray"] = keep
    out = os.path.join(mod, "romfs", GUIDE_TABLE)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(byml.write_byml(table, version=struct.unpack_from("<H", data, 2)[0]))
    return removed


def bottle_contents():
    data = pickle.load(open(os.path.join(ROOT, "re", "gamedatalist.pkl"), "rb"))["Data"]
    want = mm3(BOTTLE_KEY.format("A"))
    for entry in data["Enum"]:
        if isinstance(entry, dict) and entry.get("Hash") == want:
            return entry["RawValues"]
    raise SystemExit("bottle contents enum not found")


def main():
    mod = os.path.join(ROOT, "mods", MOD_FOLDER)
    os.makedirs(os.path.join(mod, "item_controls"), exist_ok=True)
    with open(os.path.join(mod, "info.json"), "w") as f:
        json.dump({"name": MOD_NAME, "author": "KeroJester", "version": MOD_VERSION, "description": MOD_DESCRIPTION,
                   "category": "Gameplay", "homepage": "",
                   # a code mod: the mod manager loads this Vulkan layer when it starts the game
                   "code": {"vulkan_layer": {"name": "VK_LAYER_HOOKSHOT_item_controls",
                                             "linux": "layer/linux", "windows": "layer/windows"}},
                   # the player's own files, kept when the mod is updated
                   "keep": ["item_controls/slots.txt"]}, f, indent=2)
    names = enum_values()
    with open(os.path.join(mod, "item_controls", "items.txt"), "w") as f:
        for n in names:
            f.write(f"{mm3(n):08x} {display(n) if n != 'Empty' else '-'}\n")
    with open(os.path.join(mod, "item_controls", "aim.txt"), "w") as f:
        for n in AIM:
            assert n in names, n
            f.write(f"{mm3(n):08x} {n}\n")
    patch_hud(mod)
    print(f"  button prompts: {patch_guides(mod)} RT entries removed")
    build_astc_tool()
    icons = os.path.join(mod, "item_controls", "icons")
    os.makedirs(icons, exist_ok=True)
    done, lines = set(), []
    with tempfile.TemporaryDirectory() as tmp:
        def icon(file):
            if file not in done:
                open(os.path.join(icons, file + ".rgba"), "wb").write(decode_icon(file, tmp))
                done.add(file)
            return file + ".rgba"
        for n in names:
            if n in ICON:
                lines.append(f"item {mm3(n):08x} {icon(ICON[n])}")
        for letter in "ABCDE":
            lines.append(f"bottle {mm3('Bottle' + letter):08x} {mm3(BOTTLE_KEY.format(letter)):08x}")
        for c in bottle_contents():
            lines.append(f"content {mm3(c):08x} {icon(CONTENT_ICON.get(c, 'Item_Bottle_' + c))}")
    with open(os.path.join(mod, "item_controls", "icons.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    missing = [n for n in names if n not in ICON and n not in ("Empty", "SheikahWarp")]
    print(f"{MOD_FOLDER}: info.json, items.txt ({len(names)} items), aim.txt ({len(AIM)}), {len(done)} icons"
          + (f"; no icon: {missing}" if missing else ""))


if __name__ == "__main__":
    main()
