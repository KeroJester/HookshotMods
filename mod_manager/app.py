#!/usr/bin/env python3
"""Entry point of the packaged Hookshot Mods app: always opens the mod manager window.

    HookshotMods [--game-dir <folder>]

(The command-line tool is still `python3 hookshot-mods <command>` from a source checkout.)"""
import os
import sys

if __package__ in (None, ""):                    # (run as a script or frozen: make `mod_manager` importable)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mod_manager.gui import run_gui  # noqa: E402


def main() -> int:
    game_dir = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a in ("--game-dir", "-g") and i + 1 < len(args):
            game_dir = args[i + 1]
        elif a.startswith("--game-dir="):
            game_dir = a.split("=", 1)[1]
    return run_gui(game_dir)


if __name__ == "__main__":
    sys.exit(main())
