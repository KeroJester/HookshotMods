#!/usr/bin/env python3
"""Core logic for Hookshot Mod Manager."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Set, Tuple

import sys

try:
    from . import codemods
    from .models import ConflictEntry, Mod, ModMetadata
    from .zstd_helper import compress_zs, decompress_zs, extract_sarc
except (ImportError, ValueError):
    import codemods
    from models import ConflictEntry, Mod, ModMetadata
    from zstd_helper import compress_zs, decompress_zs, extract_sarc


class GameDirNotFound(Exception):
    """No Hookshot game folder was given or found (the GUI asks for one, the CLI takes --game-dir)."""


def config_path() -> str:
    """%APPDATA%\\hookshot-mods\\config.json on Windows, $XDG_CONFIG_HOME/hookshot-mods/config.json elsewhere"""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "hookshot-mods", "config.json")


def load_config() -> dict:
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(config_path()), exist_ok=True)
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def is_game_dir(path: str) -> bool:
    """A Hookshot game folder: hookshot.exe (Windows) or bin/hookshot (Linux) beside its romfs/ or mods/."""
    if not path or not os.path.isdir(path):
        return False
    exe = os.path.isfile(os.path.join(path, "hookshot.exe")) or os.path.isfile(os.path.join(path, "bin", "hookshot"))
    return exe or os.path.isfile(os.path.join(path, "image.bin"))


def _locked(e: OSError) -> OSError:
    """Windows keeps a running game's files (a code mod's layer DLL) locked: say so instead of a bare error."""
    if isinstance(e, PermissionError) or getattr(e, "winerror", 0) in (5, 32):
        return PermissionError("A file in this mod is in use. Close the game, then try again.")
    return e


def _rename(src: str, dst: str) -> None:
    try:
        os.rename(src, dst)
    except OSError as e:
        raise _locked(e) from e


def find_game_dir() -> Optional[str]:
    """HOOKSHOT_GAME_DIR, the saved folder, the current folder, or a folder around the manager itself."""
    candidates = [os.environ.get("HOOKSHOT_GAME_DIR"), load_config().get("game_dir"), os.getcwd()]
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    for _ in range(4):                      # (the manager may sit in the game folder or in a subfolder of it)
        candidates.append(here)
        here = os.path.dirname(here)
    for c in candidates:
        if c and is_game_dir(c):
            return os.path.abspath(c)
    return None

KNOWN_ROMFS_ROOT_DIRS = {
    "ActorSystem", "CameraAnimation", "GameBalance", "Movie", "Shader",
    "agl", "Chemical", "GameData", "Pack", "SLink2",
    "AI", "Component", "GameParameter", "Perception", "Sound",
    "AISchedule", "Effect", "gsys", "Phive", "System",
    "AnimationEvent", "ELink2", "Layout2Shader", "PreComputedCulling", "TerrainArc",
    "AoC1", "Env", "Lib", "Reaction", "Tex",
    "AS", "Event", "Life", "RSDB", "UI",
    "Bake", "Foliage", "Logic", "Rumble", "Voice",
    "Banc", "Font2", "Mals", "Scene", "VolumeStats",
    "BSA", "Game", "Model", "Sequence", "WorldMgr",
}


class HookshotModManager:
    def __init__(self, game_dir: Optional[str] = None, sync: bool = True):
        if game_dir is None:
            game_dir = find_game_dir()
            if game_dir is None:
                raise GameDirNotFound("Hookshot game folder not found: pass --game-dir or pick it in the manager.")
        elif not is_game_dir(game_dir):
            raise GameDirNotFound(f"Not a Hookshot game folder (no hookshot.exe or bin/hookshot): {game_dir}")
        self.game_dir = os.path.abspath(game_dir)
        cfg = load_config()
        if cfg.get("game_dir") != self.game_dir:
            cfg["game_dir"] = self.game_dir
            try:
                save_config(cfg)
            except OSError:
                pass

        self.mods_dir = os.path.join(self.game_dir, "mods")
        self.romfs_dir = os.path.join(self.game_dir, "romfs")
        try:
            os.makedirs(self.mods_dir, exist_ok=True)
        except OSError:
            pass
        self.last_sync = ""
        if sync:
            self.sync_code_mods()          # (paths in hookshot_env.txt follow the game folder if it moved)

    # ---- code mods ------------------------------------------------------------------------------------------------
    def code_mods(self) -> List[Mod]:
        return [m for m in self.list_mods() if m.is_code_mod]

    def sync_code_mods(self) -> str:
        """Writes hookshot_env.txt's managed block for the enabled code mods (in load order)."""
        layers = [m.code_layer for m in self.list_mods() if m.enabled and m.is_code_mod]
        try:
            self.last_sync = codemods.sync_env(self.game_dir, layers)
        except OSError as e:
            self.last_sync = f"{codemods.ENV_FILE}: could not be written ({e})"
        return self.last_sync

    def list_mods(self) -> List[Mod]:
        """List all mods in the mods directory, ordered by Hookshot load order."""
        if not os.path.exists(self.mods_dir):
            return []

        mods: List[Mod] = []
        for item in os.listdir(self.mods_dir):
            full_path = os.path.join(self.mods_dir, item)
            if os.path.isdir(full_path):
                mod = Mod.from_folder(self.mods_dir, item)
                if mod:
                    mods.append(mod)

        # Hookshot uses strcasecmp to sort mods alphabetically by folder_name.
        # We sort by folder_name lower-cased so order matches Hookshot's internal loading.
        mods.sort(key=lambda m: m.folder_name.lower())
        return mods

    def get_mod(self, name_or_folder: str) -> Optional[Mod]:
        """Find a mod by folder name or display name."""
        mods = self.list_mods()
        # Direct folder match
        for m in mods:
            if m.folder_name == name_or_folder:
                return m
        # Lowercase folder match
        for m in mods:
            if m.folder_name.lower() == name_or_folder.lower():
                return m
        # Display name match
        for m in mods:
            if m.display_name.lower() == name_or_folder.lower():
                return m
        # Strip disabled prefix match
        target_clean = name_or_folder.lstrip("_-").lower()
        for m in mods:
            if m.folder_name.lstrip("_-").lower() == target_clean:
                return m
        return None

    def enable_mod(self, name_or_folder: str) -> bool:
        """Enable a mod by removing leading '_' or '-'."""
        mod = self.get_mod(name_or_folder)
        if not mod:
            raise ValueError(f"Mod '{name_or_folder}' not found.")
        if mod.enabled:
            return True  # already enabled

        new_folder = mod.folder_name.lstrip("_-")
        if not new_folder:
            new_folder = "mod"

        old_path = os.path.join(self.mods_dir, mod.folder_name)
        new_path = os.path.join(self.mods_dir, new_folder)

        # Avoid collision
        if os.path.exists(new_path) and new_path != old_path:
            raise FileExistsError(f"A folder named '{new_folder}' already exists.")

        _rename(old_path, new_path)
        self.sync_code_mods()
        return True

    def disable_mod(self, name_or_folder: str) -> bool:
        """Disable a mod by prepending '_'."""
        mod = self.get_mod(name_or_folder)
        if not mod:
            raise ValueError(f"Mod '{name_or_folder}' not found.")
        if not mod.enabled:
            return True  # already disabled

        new_folder = f"_{mod.folder_name}"
        old_path = os.path.join(self.mods_dir, mod.folder_name)
        new_path = os.path.join(self.mods_dir, new_folder)

        if os.path.exists(new_path) and new_path != old_path:
            raise FileExistsError(f"A folder named '{new_folder}' already exists.")

        _rename(old_path, new_path)
        self.sync_code_mods()
        return True

    def toggle_mod(self, name_or_folder: str) -> bool:
        """Toggle enabled/disabled state of a mod."""
        mod = self.get_mod(name_or_folder)
        if not mod:
            raise ValueError(f"Mod '{name_or_folder}' not found.")
        if mod.enabled:
            return self.disable_mod(mod.folder_name)
        else:
            return self.enable_mod(mod.folder_name)

    def reorder_mods(self, ordered_mod_folders: List[str]) -> None:
        """
        Reorder mods by applying 3-digit numeric prefixes ('010-', '020-', etc.)
        so Hookshot's alphabetical sorting loads them in the exact specified order.
        """
        mods = {m.folder_name: m for m in self.list_mods()}
        step = 10
        current_priority = step

        # First pass: rename to temporary names to prevent collision
        temp_renames = []
        for folder in ordered_mod_folders:
            if folder not in mods:
                continue
            mod = mods[folder]
            # Strip existing priority numbers
            clean = mod.folder_name.lstrip("_-")
            clean = re.sub(r"^\d+[-_ ]+", "", clean)
            prefix = "_" if not mod.enabled else ""
            new_name = f"{prefix}{current_priority:03d}-{clean}"
            temp_name = f"__tmp_reorder_{current_priority:03d}__{clean}"

            src = os.path.join(self.mods_dir, mod.folder_name)
            tmp = os.path.join(self.mods_dir, temp_name)
            dst = os.path.join(self.mods_dir, new_name)

            if src != dst:
                _rename(src, tmp)
                temp_renames.append((tmp, dst))

            current_priority += step

        # Second pass: move to final names
        for tmp, dst in temp_renames:
            _rename(tmp, dst)
        self.sync_code_mods()

    def get_conflicts(self) -> Dict[str, ConflictEntry]:
        """
        Identify files that collide across currently enabled mods.
        Returns a dict of relative_file_path -> ConflictEntry.
        The 'winning_mod' is the last mod loaded in Hookshot's strcasecmp order.
        """
        enabled_mods = [m for m in self.list_mods() if m.enabled]
        # In Hookshot, later alphabetical mods overwrite earlier ones
        file_to_mods: Dict[str, List[str]] = {}

        for mod in enabled_mods:
            for rel_file in mod.files:
                norm_file = os.path.normpath(rel_file)
                if norm_file not in file_to_mods:
                    file_to_mods[norm_file] = []
                file_to_mods[norm_file].append(mod.folder_name)

        conflicts = {}
        for rel_file, mod_list in file_to_mods.items():
            if len(mod_list) > 1:
                # Last mod in order wins
                winning = mod_list[-1]
                conflicts[rel_file] = ConflictEntry(
                    file_path=rel_file,
                    mods=mod_list,
                    winning_mod=winning,
                )

        return conflicts

    def install_mod(self, source_path: str, custom_name: Optional[str] = None) -> str:
        """
        Install a mod from a directory, .zip, .7z, .rar, or tar archive.
        Auto-detects and normalizes RomFS structure.
        Returns the created mod folder name.
        """
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Source file/folder not found: {source_path}")

        temp_dir = tempfile.mkdtemp(prefix="hookshot_mod_install_")
        try:
            if os.path.isdir(source_path):
                # Copy folder contents
                for item in os.listdir(source_path):
                    s = os.path.join(source_path, item)
                    d = os.path.join(temp_dir, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d)
                    else:
                        shutil.copy2(s, d)
                base_name = os.path.basename(os.path.normpath(source_path))
            else:
                base_name = os.path.splitext(os.path.basename(source_path))[0]
                # Strip second ext if .tar.gz etc
                if base_name.endswith(".tar"):
                    base_name = os.path.splitext(base_name)[0]

                # Extract archive
                lower_src = source_path.lower()
                if lower_src.endswith((".zip",)):
                    import zipfile
                    with zipfile.ZipFile(source_path, "r") as zf:
                        zf.extractall(temp_dir)
                elif lower_src.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
                    import tarfile
                    with tarfile.open(source_path, "r:*") as tf:
                        tf.extractall(temp_dir)
                elif lower_src.endswith((".7z",)):
                    subprocess.run(["7z", "x", f"-o{temp_dir}", source_path, "-y"], check=True, stdout=subprocess.DEVNULL)
                elif lower_src.endswith((".rar",)):
                    subprocess.run(["unrar", "x", "-o+", source_path, temp_dir], check=True, stdout=subprocess.DEVNULL)
                else:
                    # Fallback to 7z
                    subprocess.run(["7z", "x", f"-o{temp_dir}", source_path, "-y"], check=True, stdout=subprocess.DEVNULL)

            # A packaged mod folder (info.json with code, data folders...) is installed whole
            root = self._find_mod_root(temp_dir)
            if root:
                return self._install_folder(root, custom_name, base_name)

            # Now find romfs structure inside temp_dir
            found_romfs = None
            found_exefs = None
            found_meta = None

            # Look for metadata file
            for root, _, fnames in os.walk(temp_dir):
                for fn in fnames:
                    if fn.lower() in ("info.json", "mod.json"):
                        found_meta = os.path.join(root, fn)
                        break
                if found_meta:
                    break

            # 1. Search for explicit 'romfs' folder
            for root, dirs, _ in os.walk(temp_dir):
                for d in dirs:
                    if d.lower() == "romfs":
                        found_romfs = os.path.join(root, d)
                        break
                    if d.lower() == "exefs":
                        found_exefs = os.path.join(root, d)
                if found_romfs:
                    break

            # 2. Check if root or subfolder has known RomFS root dirs (RSDB, Pack, etc.)
            if not found_romfs:
                for root, dirs, _ in os.walk(temp_dir):
                    rel = os.path.relpath(root, temp_dir).split(os.sep)
                    if len(rel) > 2:
                        continue
                    overlap = set(dirs).intersection(KNOWN_ROMFS_ROOT_DIRS)
                    if len(overlap) >= 1:
                        # This directory itself represents romfs contents!
                        found_romfs = root
                        break

            if not found_romfs:
                raise ValueError("Could not find a valid 'romfs' structure or game asset directory inside the mod.")

            # Target mod name
            mod_title = custom_name or base_name
            # Sanitize name
            mod_title = re.sub(r"[^\w\s\.-]", "", mod_title).strip()
            if not mod_title:
                mod_title = "InstalledMod"

            # Assign next priority prefix
            existing = self.list_mods()
            max_prio = 0
            for m in existing:
                if m.priority > max_prio:
                    max_prio = m.priority
            new_prio = max_prio + 10
            final_folder = f"{new_prio:03d}-{mod_title}"
            final_mod_path = os.path.join(self.mods_dir, final_folder)

            os.makedirs(final_mod_path, exist_ok=True)
            dest_romfs = os.path.join(final_mod_path, "romfs")

            # Copy romfs
            if os.path.basename(found_romfs).lower() == "romfs":
                shutil.copytree(found_romfs, dest_romfs, dirs_exist_ok=True)
            else:
                # Copy known romfs subdirectories into dest_romfs
                os.makedirs(dest_romfs, exist_ok=True)
                for item in os.listdir(found_romfs):
                    s = os.path.join(found_romfs, item)
                    d = os.path.join(dest_romfs, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)

            # Copy exefs if present (for completeness, Hookshot logs a notice)
            if found_exefs:
                dest_exefs = os.path.join(final_mod_path, "exefs")
                shutil.copytree(found_exefs, dest_exefs, dirs_exist_ok=True)

            # Copy or write metadata
            dest_meta = os.path.join(final_mod_path, "info.json")
            if found_meta:
                try:
                    with open(found_meta, "r", encoding="utf-8") as f:
                        meta_data = json.load(f)
                    with open(dest_meta, "w", encoding="utf-8") as f:
                        json.dump(meta_data, f, indent=2)
                except Exception:
                    pass
            else:
                meta = ModMetadata(name=mod_title, description=f"Installed from {os.path.basename(source_path)}")
                with open(dest_meta, "w", encoding="utf-8") as f:
                    json.dump(meta.to_dict(), f, indent=2)

            return final_folder

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _find_mod_root(temp_dir: str) -> Optional[str]:
        """The shallowest folder with an info.json that is more than a plain RomFS mod (code, data folders)."""
        plain = {"romfs", "exefs", "info.json", "mod.json", "metadata.json"}
        best = None
        for root, dirs, files in os.walk(temp_dir):
            depth = len(os.path.relpath(root, temp_dir).split(os.sep))
            if depth > 3:
                dirs[:] = []
                continue
            if "info.json" in files:
                try:
                    with open(os.path.join(root, "info.json"), "r", encoding="utf-8") as f:
                        info = json.load(f)
                except Exception:
                    info = {}
                extra = [e for e in os.listdir(root) if e.lower() not in plain and not e.lower().startswith(("readme", "license"))]
                if info.get("code") or extra:
                    if best is None or depth < best[0]:
                        best = (depth, root)
        return best[1] if best else None

    def _install_folder(self, root: str, custom_name: Optional[str], base_name: str) -> str:
        """Copies a whole packaged mod folder. A mod with the same name is updated in place: its folder name (load
        order, enabled state) is kept, and so are the files its info.json lists under "keep" (the player's data)."""
        with open(os.path.join(root, "info.json"), "r", encoding="utf-8") as f:
            info = json.load(f)
        title = custom_name or info.get("name") or base_name
        title = re.sub(r"[^\w\s\.-]", "", title).strip() or "InstalledMod"
        existing = None
        for m in self.list_mods():
            if (m.metadata.name or "").lower() == (info.get("name") or title).lower():
                existing = m
                break
        if existing:
            target = existing.base_path
            keep = set(info.get("keep", [])) | set(existing.info.get("keep", []))
            saved = {}
            for rel in keep:
                p = os.path.join(target, *rel.replace("\\", "/").split("/"))
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        saved[rel] = f.read()
            try:
                shutil.rmtree(target)
            except OSError as e:
                raise _locked(e) from e
            shutil.copytree(root, target)
            for rel, data in saved.items():
                p = os.path.join(target, *rel.replace("\\", "/").split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
            folder = existing.folder_name
        else:
            folder = os.path.basename(os.path.normpath(root))
            if not re.match(r"^\d+[-_ ]", folder) or os.path.exists(os.path.join(self.mods_dir, folder)):
                prio = max([m.priority for m in self.list_mods()] + [0]) + 10
                folder = f"{prio:03d}-{title}"
            shutil.copytree(root, os.path.join(self.mods_dir, folder))
        self.sync_code_mods()
        return folder

    def create_mod(
        self,
        name: str,
        author: str = "Justin",
        version: str = "1.0.0",
        description: str = "",
        category: str = "General",
    ) -> str:
        """Create a new mod scaffold with romfs directory and info.json."""
        clean_name = re.sub(r"[^\w\s\.-]", "", name).strip()
        if not clean_name:
            clean_name = "NewMod"

        existing = self.list_mods()
        max_prio = 0
        for m in existing:
            if m.priority > max_prio:
                max_prio = m.priority
        new_prio = max_prio + 10
        folder_name = f"{new_prio:03d}-{clean_name}"
        mod_path = os.path.join(self.mods_dir, folder_name)

        if os.path.exists(mod_path):
            raise FileExistsError(f"Mod directory already exists: {folder_name}")

        os.makedirs(os.path.join(mod_path, "romfs"), exist_ok=True)

        meta = ModMetadata(
            name=name,
            author=author,
            version=version,
            description=description,
            category=category,
        )
        with open(os.path.join(mod_path, "info.json"), "w", encoding="utf-8") as f:
            json.dump(meta.to_dict(), f, indent=2)

        return folder_name

    def delete_mod(self, name_or_folder: str) -> bool:
        """Permanently delete a mod."""
        mod = self.get_mod(name_or_folder)
        if not mod:
            raise ValueError(f"Mod '{name_or_folder}' not found.")
        try:
            shutil.rmtree(mod.base_path)
        except OSError as e:
            raise _locked(e) from e
        self.sync_code_mods()
        return True

    def extract_game_asset(self, rel_path: str, dest_path: str, decompress: bool = True) -> int:
        """
        Extract and optionally decompress a base game RomFS asset into dest_path.
        Returns size in bytes written.
        """
        src = os.path.join(self.romfs_dir, rel_path)
        if not os.path.exists(src):
            raise FileNotFoundError(f"Base game asset not found: {rel_path}")

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(src, "rb") as f:
            data = f.read()

        if decompress and rel_path.lower().endswith(".zs"):
            dec = decompress_zs(data, os.path.basename(rel_path), self.romfs_dir)
            with open(dest_path, "wb") as f:
                f.write(dec)
            return len(dec)
        else:
            with open(dest_path, "wb") as f:
                f.write(data)
            return len(data)

    def search_romfs(self, query: str, limit: int = 50) -> List[str]:
        """Search relative file paths in base game romfs."""
        results = []
        q = query.lower()
        for root, _, filenames in os.walk(self.romfs_dir):
            for fn in filenames:
                rel = os.path.relpath(os.path.join(root, fn), self.romfs_dir)
                if q in rel.lower():
                    results.append(rel)
                    if len(results) >= limit:
                        return results
        return results

    def launch_game(self, overlay: bool = False) -> subprocess.Popen:
        """Starts the game (code mods load through hookshot_env.txt, synced first).
        overlay: Linux only, the developer overlay overlay/libhookshot_overlay.so when it exists (LD_PRELOAD)."""
        self.sync_code_mods()
        env = os.environ.copy()
        if codemods.platform_key() == "windows":
            target = os.path.join(self.game_dir, "hookshot.exe")
        else:
            binary = os.path.join(self.game_dir, "bin", "hookshot")
            launcher = os.path.join(self.game_dir, "hookshot")
            target = binary if os.path.exists(binary) and os.access(binary, os.X_OK) else launcher
            overlay_lib = os.path.join(self.game_dir, "overlay", "libhookshot_overlay.so")
            if overlay and os.path.exists(overlay_lib):
                env["LD_PRELOAD"] = f"{overlay_lib}:{env.get('LD_PRELOAD', '')}".strip(":")
        if not os.path.exists(target):
            raise FileNotFoundError(f"Game executable not found: {target}")
        return subprocess.Popen([target], cwd=self.game_dir, env=env)
