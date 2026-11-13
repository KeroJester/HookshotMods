#!/usr/bin/env python3
"""Code mods: mods that carry native code (a Vulkan layer) besides RomFS files.

Hookshot itself loads only RomFS mods, and since v2.0.14 its hookshot_env.txt takes only the game's own switches,
never the Vulkan loader's variables: that file is passed around between players, so it must not load code. A code
mod therefore loads when the player starts the game from the mod manager, which sets the Vulkan loader's variables
for that one game process only:

    VK_ADD_LAYER_PATH=<mods>/050-ItemControls/layer/linux
    VK_INSTANCE_LAYERS=VK_LAYER_HOOKSHOT_item_controls

(`hookshot-mods env` prints the same lines, for starting the game some other way.)

A code mod declares its layer in info.json:

    "code": {"vulkan_layer": {"name": "VK_LAYER_HOOKSHOT_item_controls",
                              "linux": "layer/linux", "windows": "layer/windows"}}

where each platform entry is a folder (relative to the mod) holding the layer manifest (*.json) and its library.
"keep": ["item_controls/slots.txt"] in info.json lists the player's own files that survive a mod update.
"""

import glob
import json
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

ENV_FILE = "hookshot_env.txt"
BLOCK_BEGIN = "# >>> hookshot-mods: code mods (managed, do not edit between these lines)"
BLOCK_END = "# <<< hookshot-mods"
ENV_PATH_VAR = "VK_ADD_LAYER_PATH"
ENV_LAYERS_VAR = "VK_INSTANCE_LAYERS"


def platform_key() -> str:
    """'windows' or 'linux' (the keys code mods use for their per-platform files)."""
    return "windows" if sys.platform.startswith("win") else "linux"


def list_sep() -> str:
    """The separator the Vulkan loader uses in path and layer lists: ';' on Windows, ':' elsewhere."""
    return ";" if platform_key() == "windows" else ":"


@dataclass
class CodeLayer:
    name: str                  # the layer's name, as in its manifest
    folder: Optional[str]      # absolute folder with the manifest + library for this platform (None: not shipped)
    problem: str = ""          # why the layer cannot be used here ("" = fine)


def read_layer(mod_path: str, info: dict) -> Optional[CodeLayer]:
    """The mod's Vulkan layer for this platform, or None when the mod has no code."""
    spec = (info.get("code") or {}).get("vulkan_layer")
    if not isinstance(spec, dict) or not spec.get("name"):
        return None
    rel = spec.get(platform_key())
    if not rel:
        return CodeLayer(spec["name"], None, f"no {platform_key()} build in this mod")
    folder = os.path.normpath(os.path.join(mod_path, rel))
    manifests = glob.glob(os.path.join(folder, "*.json"))
    if not manifests:
        return CodeLayer(spec["name"], folder, f"layer manifest missing in {rel}")
    for m in manifests:                       # the manifest's library must be there too
        try:
            with open(m, "r", encoding="utf-8") as f:
                lib = json.load(f)["layer"]["library_path"]
        except Exception:
            return CodeLayer(spec["name"], folder, f"unreadable layer manifest {os.path.basename(m)}")
        lib_path = os.path.normpath(os.path.join(folder, lib.replace("\\", os.sep).replace("/", os.sep)))
        if not os.path.exists(lib_path):
            return CodeLayer(spec["name"], folder, f"layer library {os.path.basename(lib_path)} missing")
    return CodeLayer(spec["name"], folder)


def usable(layers: List[CodeLayer]) -> List[CodeLayer]:
    return [l for l in layers if l.folder and not l.problem]


def apply_layers(env: dict, layers: List[CodeLayer]) -> dict:
    """Adds these (enabled, usable) layers to an environment for the game process. Values already set there (the
    player's own layers) are kept in front."""
    use = usable(layers)
    if not use:
        return env
    sep = list_sep()
    paths = [p for p in env.get(ENV_PATH_VAR, "").split(sep) if p] + [l.folder for l in use]
    names = [n for n in env.get(ENV_LAYERS_VAR, "").split(sep) if n] + [l.name for l in use]
    env[ENV_PATH_VAR] = sep.join(dict.fromkeys(paths))
    env[ENV_LAYERS_VAR] = sep.join(dict.fromkeys(names))
    return env


def clean_env_file(game_dir: str) -> str:
    """Removes the block older versions of the manager wrote into hookshot_env.txt (Hookshot ignores those lines and
    complains about them). Lines outside the block stay; the file goes if it held only the block."""
    path = os.path.join(game_dir, ENV_FILE)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return ""
    if BLOCK_BEGIN not in text:
        return ""
    outside, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if s == BLOCK_BEGIN:
            inside = True
        elif s == BLOCK_END:
            inside = False
        elif not inside:
            outside.append(line)
    while outside and not outside[-1].strip():
        outside.pop()
    if outside:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(outside) + "\n")
    else:
        os.remove(path)
    return f"{ENV_FILE}: removed the old code-mod lines (Hookshot ignores them)"


def shell_lines(layers: List[CodeLayer]) -> List[str]:
    """The environment lines for starting the game with these layers by hand (sh on Linux, cmd on Windows)."""
    env = apply_layers({}, layers)
    if platform_key() == "windows":
        return [f'set "{k}={v}"' for k, v in env.items()]
    return [f"export {k}='{v}'" for k, v in env.items()]
