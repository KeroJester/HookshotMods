#!/usr/bin/env python3
"""Data models for Hookshot Mod Manager."""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

try:
    from .codemods import CodeLayer, read_layer
except (ImportError, ValueError):
    from codemods import CodeLayer, read_layer


@dataclass
class ModMetadata:
    name: str = ""
    author: str = "Unknown"
    version: str = "1.0.0"
    description: str = ""
    category: str = "General"
    homepage: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ModMetadata":
        return cls(
            name=data.get("name", ""),
            author=data.get("author", "Unknown"),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            category=data.get("category", "General"),
            homepage=data.get("homepage", ""),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "author": self.author,
            "version": self.version,
            "description": self.description,
            "category": self.category,
            "homepage": self.homepage,
        }


@dataclass
class Mod:
    folder_name: str
    base_path: str  # absolute path to mod folder
    enabled: bool = True
    priority: int = 0
    display_name: str = ""
    romfs_path: Optional[str] = None
    files: List[str] = field(default_factory=list)
    metadata: ModMetadata = field(default_factory=ModMetadata)
    has_exefs: bool = False
    info: dict = field(default_factory=dict)          # the raw info.json (code mods: "code", "keep", ...)
    code_layer: Optional["CodeLayer"] = None         # the mod's Vulkan layer for this platform (code mods)

    @property
    def is_code_mod(self) -> bool:
        return self.code_layer is not None

    @property
    def file_count(self) -> int:
        return len(self.files)

    @classmethod
    def from_folder(cls, mods_dir: str, folder_name: str) -> Optional["Mod"]:
        full_path = os.path.join(mods_dir, folder_name)
        if not os.path.isdir(full_path):
            return None

        # Determine enabled state: Hookshot disables folders starting with '_' or '-'
        enabled = not (folder_name.startswith("_") or folder_name.startswith("-"))

        # Strip disabled prefix
        clean_name = folder_name.lstrip("_-")

        # Extract numeric priority if present (e.g. "010-ModName" or "010_ModName")
        m = re.match(r"^(\d+)[-_ ]+(.*)$", clean_name)
        if m:
            priority = int(m.group(1))
            display_name = m.group(2)
        else:
            priority = 0
            display_name = clean_name

        # Look for metadata (info.json or mod.json)
        metadata = ModMetadata(name=display_name)
        info: dict = {}
        for meta_file in ["info.json", "mod.json", "metadata.json"]:
            meta_path = os.path.join(full_path, meta_file)
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    metadata = ModMetadata.from_dict(data)
                    info = data if isinstance(data, dict) else {}
                    if not metadata.name:
                        metadata.name = display_name
                except Exception:
                    pass
                break

        # Search for romfs directory (up to 3 levels deep like Hookshot's find_mods)
        romfs_path = None
        has_exefs = False
        files: List[str] = []

        for root, dirs, _ in os.walk(full_path):
            rel_depth = len(os.path.relpath(root, full_path).split(os.sep))
            if rel_depth > 3:
                continue
            if "romfs" in dirs:
                romfs_path = os.path.join(root, "romfs")
            if "exefs" in dirs:
                has_exefs = True

        if romfs_path and os.path.isdir(romfs_path):
            for root, _, filenames in os.walk(romfs_path):
                for fn in filenames:
                    abs_file = os.path.join(root, fn)
                    rel_file = os.path.relpath(abs_file, romfs_path)
                    files.append(rel_file)

        files.sort()

        return cls(
            folder_name=folder_name,
            base_path=full_path,
            enabled=enabled,
            priority=priority,
            display_name=metadata.name if metadata.name else display_name,
            romfs_path=romfs_path,
            files=files,
            metadata=metadata,
            has_exefs=has_exefs,
            info=info,
            code_layer=read_layer(full_path, info),
        )


@dataclass
class ConflictEntry:
    file_path: str
    mods: List[str]  # folder_names in load order (first to last)
    winning_mod: str  # last mod in load order overrides the previous ones
