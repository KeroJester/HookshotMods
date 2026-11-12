#!/usr/bin/env python3
"""Code mods: mods that carry native code (a Vulkan layer) besides RomFS files.

Hookshot itself loads only RomFS mods. It does read ``hookshot_env.txt`` in the game folder before it starts Vulkan,
on Windows and Linux alike, and the Vulkan loader then loads the layers named there. So a code mod ships its layer
inside its own mod folder, and the manager keeps one marked block in ``hookshot_env.txt`` listing the layers of the
enabled code mods for this platform:

    # >>> hookshot-mods: code mods (managed, do not edit between these lines)
    VK_ADD_LAYER_PATH=<mods>/050-ItemControls/layer/linux
    VK_INSTANCE_LAYERS=VK_LAYER_HOOKSHOT_item_controls
    # <<< hookshot-mods

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


def _split_env(text: str):
    """(lines outside our block, the user's own values of the two variables)"""
    outside, inside = [], False
    user = {ENV_PATH_VAR: "", ENV_LAYERS_VAR: ""}
    for line in text.splitlines():
        s = line.strip()
        if s == BLOCK_BEGIN:
            inside = True
            continue
        if s == BLOCK_END:
            inside = False
            continue
        if inside:
            continue
        outside.append(line)
        if "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            if k.strip() in user:
                user[k.strip()] = v.strip()
    while outside and not outside[-1].strip():
        outside.pop()
    return outside, user


def sync_env(game_dir: str, layers: List[CodeLayer]) -> str:
    """Rewrites the managed block of hookshot_env.txt for these (enabled, usable) layers. Returns a status line.

    Lines outside the block are kept as they are. When the player set VK_ADD_LAYER_PATH or VK_INSTANCE_LAYERS
    themselves, their values are kept in front of ours (Hookshot applies the file top to bottom, so the block, which
    comes last, carries the merged values)."""
    path = os.path.join(game_dir, ENV_FILE)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    outside, user = _split_env(text)
    usable = [l for l in layers if l.folder and not l.problem]
    lines = list(outside)
    if usable:
        sep = list_sep()
        paths = [p for p in user[ENV_PATH_VAR].split(sep) if p] + [l.folder for l in usable]
        names = [n for n in user[ENV_LAYERS_VAR].split(sep) if n] + [l.name for l in usable]
        seen, uniq_names = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                uniq_names.append(n)
        if lines:
            lines.append("")
        lines += [BLOCK_BEGIN, f"{ENV_PATH_VAR}={sep.join(dict.fromkeys(paths))}",
                  f"{ENV_LAYERS_VAR}={sep.join(uniq_names)}", BLOCK_END]
    new_text = ("\n".join(lines) + "\n") if lines else ""
    if new_text == text:
        return f"{ENV_FILE}: up to date ({len(usable)} code mod{'s' if len(usable) != 1 else ''})"
    if not new_text and not os.path.exists(path):
        return f"{ENV_FILE}: no code mods"
    if new_text:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
    else:
        os.remove(path)                       # (it only ever held our block)
    return f"{ENV_FILE}: updated for {len(usable)} code mod{'s' if len(usable) != 1 else ''}"


def env_status(game_dir: str) -> List[str]:
    """The layer names currently active in hookshot_env.txt's managed block."""
    try:
        with open(os.path.join(game_dir, ENV_FILE), "r", encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    inside, names = False, []
    for line in text.splitlines():
        s = line.strip()
        if s == BLOCK_BEGIN:
            inside = True
        elif s == BLOCK_END:
            inside = False
        elif inside and s.startswith(ENV_LAYERS_VAR + "="):
            names = [n for n in s.split("=", 1)[1].split(list_sep()) if n]
    return names
