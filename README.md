# hookshot-mods

Mods and a cross-platform mod manager for the Ocarina of Time remake running under
[Hookshot](https://github.com/NotRexed/Hookshot).

**This repository contains no game files.** You need your own copy of the game, set up with Hookshot.

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

## Rules for commits

- Never commit game data or anything made from it: romfs files, patched archives, extracted icons,
  memory dumps, ROMs, saves. They belong in the game folder, never in this repo.
- Mods are built from the player's own game files; releases carry only our code and patches.

## License

GPL-3.0 (see `LICENSE`). The ASTC decoder used at build time comes from suyu (GPL-2.0-or-later) via the
Hookshot source; imgui (MIT) and Vulkan-Headers (Apache-2.0) are submodules.
