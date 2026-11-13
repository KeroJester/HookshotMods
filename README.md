# hookshot-mods

Mods and a cross-platform mod manager for the Ocarina of Time remake running under Hookshot.

**This repository contains no game files.** You need your own copy of the game, set up with Hookshot.

## Download (players)

Get the app from the [Releases](https://github.com/KeroJester/HookshotMods/releases) page:

- **Windows:** `HookshotMods.exe`, just run it.
- **Linux:** `HookshotMods-x86_64.AppImage`. Make it executable (`chmod +x`, or Properties → "Allow executing"), then run it.

The first time, it asks for your Hookshot game folder (the one with `hookshot.exe` or `bin/hookshot`).
Mods go into that folder's `mods/` folder; drag a mod's zip onto the app or use its Install button.

**Included mod: Item Controls.** The app comes with it and offers to install it on first start (or later via
**Included Mods...**). It is also on the Releases page as `ItemControls.zip`: unzip it into your `mods/` folder.
Hold LB or RB and press X, Y or B to use one of six items; on the pause menu's Items tab, hover an item and press the
same combo to put it there. The game draws the six item boxes in its own HUD and on the Items tab. RT is for movement
only (roll, sprint, side hop), and a sprint keeps going once started. The mod carries no game files: it reads what it
needs from your copy.
**Code mods** (like Item Controls) load only when you start the game with the app's **Launch** button: Hookshot
itself never loads code from mod folders. On Linux, `eval "$(./HookshotMods-x86_64.AppImage env)"` before
starting the game another way does the same.
Installing `.rar` mods needs [7-Zip](https://www.7-zip.org) on Windows (or `unrar`/`7z` on Linux).

## What's here

| Folder | What it is |
| --- | --- |
| `mod_manager/` | The mod manager (PyQt6 GUI + CLI). It enables, orders and installs mods, and loads code mods (their Vulkan layers) when it starts the game. Also holds the mod builders (`build_*.py`). |
| `plugins/item_controls/` | The **Item Controls** code mod: six item slots on LB/RB + X/Y/B, roll on RT, an in-game HUD. `src/` is its Vulkan layer (Linux `.so`, Windows `.dll`), `mod/` its other files; `package.sh` makes the mod zip. |
| `overlay/` | Linux-only developer overlay (LD_PRELOAD), used for research. Not shipped to players. |
| `re/` | Small reverse-engineering helper scripts. |
| `RESEARCH_NOTES.md` | Everything learned about the game and Hookshot so far. |

## Development setup

This repo is kept **outside** the game folder. The tools find the game folder through the mod manager
(`HOOKSHOT_GAME_DIR`, or the folder the manager last used; `./hookshot-mods gamedir` prints it), read
the game's files from there, and write built mods into its `mods/` folder.

```sh
git submodule update --init          # imgui, Vulkan-Headers, zstd (suyu's ASTC decoder is in third_party/suyu)
./start_mod_manager.sh               # GUI; asks for the game folder the first time
plugins/item_controls/build.sh       # builds the mod (Linux .so, Windows .dll, its files) into <game>/mods/050-ItemControls
plugins/item_controls/package.sh     # or: the player zip, dist/bundled_mods/ItemControls.zip (the app bundles it)
```

## Building the app

```sh
pip install -r packaging/requirements-app.txt
plugins/item_controls/package.sh     # (optional) the included mod, bundled when present
python packaging/build_app.py        # Windows: dist/HookshotMods.exe · Linux: dist/AppDir (then appimagetool)
```

GitHub Actions (`.github/workflows/build.yml`) builds the Item Controls mod, then the Windows app and the Linux
AppImage with that mod inside, on every push; download them from the run's Artifacts. Pushing a tag like `v0.9.4` also
creates a draft Release with those files attached.

## Rules for commits

- Never commit game data or anything made from it: romfs files, patched archives, extracted icons,
  memory dumps, ROMs, saves. They belong in the game folder, never in this repo.
- Mods are built from the player's own game files; releases carry only our code and patches.

## License

GPL-3.0 (see `LICENSE`). The Item Controls layer includes suyu's ASTC decoder (GPL-2.0-or-later, via the Hookshot
source submodule) and zstd's decoder (BSD, `third_party/zstd`); imgui (MIT) and Vulkan-Headers (Apache-2.0) are
submodules. [AINB](https://github.com/dt-12345/AINB)
(GPL-2.0, by dt-12345) is a separate submodule in `third_party/AINB`, used only by the old Mod Playground builder.
