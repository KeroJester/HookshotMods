#!/usr/bin/env python3
"""Entry point of the packaged Hookshot Mods app.

    HookshotMods [--game-dir <folder>]              opens the mod manager window
    HookshotMods [--game-dir <folder>] <command>    the command-line tool (list, enable, install, play, code, ...;
                                                    `HookshotMods help` lists them). Output shows in a terminal
                                                    (Linux/AppImage); the Windows app has no console."""
import os
import sys

if __package__ in (None, ""):                    # (run as a script or frozen: make `mod_manager` importable)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


COMMANDS = {"list", "enable", "disable", "toggle", "install", "create", "delete", "conflicts", "search", "dump",
            "play", "code", "gamedir", "gui", "help", "--help", "-h"}


def main() -> int:
    if any(a in COMMANDS for a in sys.argv[1:]):
        from mod_manager.cli import main as cli_main
        if sys.argv[1:] and sys.argv[1] == "help":
            sys.argv[1] = "--help"
        return cli_main() or 0
    from mod_manager.gui import run_gui
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
