#!/usr/bin/env python3
"""Developer tool: regenerates the Item Controls mod's item lists in plugins/item_controls/mod/item_controls/.

The mod itself ships no game files: its layer changes the HUD layout and button prompts in memory and decodes the
item icons from the player's own game files (plugins/item_controls/src/item_files.cpp, gamefiles.cpp). This script
only writes the three lists the layer reads, from GameData names (re/gamedatalist.pkl in the game folder, decoded
from the game's GameDataList; a value is the murmur3 hash of its name):

  items.txt   "<hash hex> <display name>" for every value of GameData Enum PlayerStatus.AssignableItem.Child/.Adult
  aim.txt     hashes of the aimed items (Boomerang, Slingshot, Bow + arrows, Hookshot, Longshot): their combo latches
              RT until the bumper is released or the face button is pressed again
  icons.txt   "item <item hash> <icon>", "bottle <bottle item hash> <contents enum key hash>",
              "content <contents hash> <icon>"; <icon> is a name in the game's UI/Tex/Icon/ (without .bntx.zs)

Then plugins/item_controls/build.sh (or package.sh) puts them into the mod.

Usage:
    python3 -m mod_manager.build_item_controls
"""

import json
import os
import pickle
import re
import struct
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                  # this source checkout
sys.path.insert(0, REPO)
from mod_manager.core import find_game_dir   # noqa: E402
ROOT = os.environ.get("HOOKSHOT_GAME_DIR") or find_game_dir()   # the game folder: romfs/ in, mods/ out
if not ROOT:
    raise SystemExit("Game folder not found: set HOOKSHOT_GAME_DIR or run the mod manager once")

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





def bottle_contents():
    data = pickle.load(open(os.path.join(ROOT, "re", "gamedatalist.pkl"), "rb"))["Data"]
    want = mm3(BOTTLE_KEY.format("A"))
    for entry in data["Enum"]:
        if isinstance(entry, dict) and entry.get("Hash") == want:
            return entry["RawValues"]
    raise SystemExit("bottle contents enum not found")


def main():
    out = os.path.join(REPO, "plugins", "item_controls", "mod", "item_controls")
    os.makedirs(out, exist_ok=True)
    names = enum_values()
    with open(os.path.join(out, "items.txt"), "w") as f:
        for n in names:
            f.write(f"{mm3(n):08x} {display(n) if n != 'Empty' else '-'}\n")
    with open(os.path.join(out, "aim.txt"), "w") as f:
        for n in AIM:
            assert n in names, n
            f.write(f"{mm3(n):08x} {n}\n")
    lines, icons = [], set()
    for n in names:
        if n in ICON:
            lines.append(f"item {mm3(n):08x} {ICON[n]}")
            icons.add(ICON[n])
    for letter in "ABCDE":
        lines.append(f"bottle {mm3('Bottle' + letter):08x} {mm3(BOTTLE_KEY.format(letter)):08x}")
    for c in bottle_contents():
        icon = CONTENT_ICON.get(c, "Item_Bottle_" + c)
        lines.append(f"content {mm3(c):08x} {icon}")
        icons.add(icon)
    with open(os.path.join(out, "icons.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    missing = [n for n in names if n not in ICON and n not in ("Empty", "SheikahWarp")]
    print(f"{out}: items.txt ({len(names)} items), aim.txt ({len(AIM)}), icons.txt ({len(icons)} icons)"
          + (f"; no icon: {missing}" if missing else ""))


if __name__ == "__main__":
    main()
