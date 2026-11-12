# hookshot-mods

Mods and a cross-platform mod manager for the Ocarina of Time remake running under
[Hookshot](https://github.com/NotRexed/Hookshot).

**This repository contains no game files.** You need your own copy of the game, set up with Hookshot.

## Download (players)

Get the app from the [Releases](https://github.com/KeroJester/HookshotMods/releases) page:

- **Windows:** `HookshotMods.exe`, just run it.
- **Linux:** `HookshotMods-x86_64.AppImage`. Make it executable (`chmod +x`, or Properties → "Allow executing"), then run it.

The first time, it asks for your Hookshot game folder (the one with `hookshot.exe` or `bin/hookshot`).
Mods go into that folder's `mods/` folder; drag a mod's zip onto the app or use its Install button.
Installing `.rar` mods needs [7-Zip](https://www.7-zip.org) on Windows (or `unrar`/`7z` on Linux).

## What's here

| Folder | What it is |
| --- | --- |
| `mod_manager/` | The mod manager (PyQt6 GUI + CLI). It enables, orders and installs mods, and loads code mods by writing their Vulkan layers into `hookshot_env.txt`. Also holds the mod builders (`build_*.py`). |
| `plugins/item_controls/` | Source of the **Item Controls** code mod: six item slots on LB/RB + X/Y/B, roll on RT, an in-game HUD. Ships as a Vulkan layer for Linux (`.so`) and Windows (`.dll`). |
| `overlay/` | Linux-only developer overlay (LD_PRELOAD), used for research. Not shipped to players. |
| `re/` | Small reverse-engineering helper scripts. |
| `RESEARCH_NOTES.md` | Everything learned about the game and Hookshot so far. |

## Development setup

This repo is kept **outside** the game folder. The tools find the game folder through the mod manager
(`HOOKSHOT_GAME_DIR`, or the folder the manager last used; `./hookshot-mods gamedir` prints it), read
the game's files from there, and write built mods into its `mods/` folder.

```sh
git submodule update --init          # imgui, Vulkan-Headers, Hookshot source (for the ASTC decoder)
./start_mod_manager.sh               # GUI; asks for the game folder the first time
plugins/item_controls/build.sh       # builds the Linux .so and the Windows .dll into <game>/mods/050-ItemControls
python3 mod_manager/build_item_controls.py   # builds the rest of that mod from your own game files
```

## Building the app

```sh
pip install -r packaging/requirements-app.txt
python packaging/build_app.py        # Windows: dist/HookshotMods.exe · Linux: dist/AppDir (then appimagetool)
```

GitHub Actions (`.github/workflows/build.yml`) builds the Windows app, the Linux AppImage and the Item
Controls layer on every push; download them from the run's Artifacts. Pushing a tag like `v0.1.0` also
creates a draft Release with those files attached.

## Rules for commits

- Never commit game data or anything made from it: romfs files, patched archives, extracted icons,
  memory dumps, ROMs, saves. They belong in the game folder, never in this repo.
- Mods are built from the player's own game files; releases carry only our code and patches.

## License

GPL-3.0 (see `LICENSE`). The ASTC decoder used at build time comes from suyu (GPL-2.0-or-later) via the
Hookshot source; imgui (MIT) and Vulkan-Headers (Apache-2.0) are submodules. [AINB](https://github.com/dt-12345/AINB)
(GPL-2.0, by dt-12345) is a separate submodule in `third_party/AINB`, used only by the old Mod Playground builder.
