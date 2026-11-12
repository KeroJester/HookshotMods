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

The repo sits inside the Hookshot game folder (everything else in that folder is ignored by `.gitignore`).

```sh
git submodule update --init          # imgui, Vulkan-Headers, Hookshot source (for the ASTC decoder)
plugins/item_controls/build.sh       # builds the Linux .so and the Windows .dll (needs g++ and mingw-w64)
python3 mod_manager/build_item_controls.py   # builds mods/050-ItemControls from your own game files
./start_mod_manager.sh               # GUI
```

## Rules for commits

- Never commit game data or anything made from it: romfs files, patched archives, extracted icons,
  memory dumps, ROMs, saves. The whitelist `.gitignore` keeps them out; don't force-add past it.
- Mods are built from the player's own game files; releases carry only our code and patches.

## License

GPL-3.0 (see `LICENSE`). The ASTC decoder used at build time comes from suyu (GPL-2.0-or-later) via the
Hookshot source; imgui (MIT) and Vulkan-Headers (Apache-2.0) are submodules.
