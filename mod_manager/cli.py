#!/usr/bin/env python3
"""Command-Line Interface for Hookshot Mod Manager."""

import argparse
import os
import sys
from typing import List, Optional

try:
    from rich import print as rprint
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

try:
    from . import codemods
    from .core import GameDirNotFound, HookshotModManager
except (ImportError, ValueError):
    import codemods
    from core import GameDirNotFound, HookshotModManager


def get_manager(game_dir: Optional[str] = None) -> HookshotModManager:
    try:
        return HookshotModManager(game_dir)
    except GameDirNotFound as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_code(args) -> int:
    """Code mods (Vulkan layers) and the hookshot_env.txt block that loads them."""
    mgr = get_manager(args.game_dir)
    status = mgr.sync_code_mods()
    active = set(codemods.env_status(mgr.game_dir))
    mods = mgr.code_mods()
    print(f"Game folder: {mgr.game_dir}  (platform: {codemods.platform_key()})")
    print(status)
    if not mods:
        print("No code mods installed.")
    for m in mods:
        lay = m.code_layer
        if not m.enabled:
            state = "disabled"
        elif lay.problem:
            state = f"cannot load: {lay.problem}"
        else:
            state = "loads" if lay.name in active else "not in hookshot_env.txt"
        print(f"  {m.folder_name:28s} {lay.name:36s} {state}")
    return 0


def cmd_list(args) -> int:
    mgr = get_manager(args.game_dir)
    mods = mgr.list_mods()
    conflicts = mgr.get_conflicts()

    if not mods:
        if HAVE_RICH:
            rprint("[yellow]No mods installed yet.[/yellow] Use [cyan]hookshot-mods create <name>[/cyan] or [cyan]hookshot-mods install <file.zip>[/cyan]")
        else:
            print("No mods installed yet. Use 'hookshot-mods create <name>' or 'hookshot-mods install <file.zip>'")
        return 0

    if HAVE_RICH:
        console = Console()
        table = Table(title="[bold green]Hookshot / OoT Remastered Mods[/bold green]", show_lines=True)
        table.add_column("Order", style="cyan", justify="right", width=6)
        table.add_column("State", justify="center", width=10)
        table.add_column("Mod Name", style="bold white", width=26)
        table.add_column("Version", style="dim", width=8)
        table.add_column("Files", justify="right", width=7)
        table.add_column("Conflicts", justify="center", width=11)
        table.add_column("Folder", style="dim", width=22)

        for i, m in enumerate(mods):
            # Check if this mod has any conflicts
            mod_conflicts = [c for c in conflicts.values() if m.folder_name in c.mods]
            conflict_str = f"[bold red]⚠️ {len(mod_conflicts)}[/bold red]" if mod_conflicts else "[green]None[/green]"

            state_str = "[bold green]ENABLED[/bold green]" if m.enabled else "[bold red]DISABLED[/bold red]"
            table.add_row(
                str(i + 1),
                state_str,
                m.display_name,
                m.metadata.version or "1.0",
                str(m.file_count),
                conflict_str,
                m.folder_name,
            )
        console.print(table)
        rprint(f"[dim]Mods Directory: {mgr.mods_dir}[/dim]")
        if conflicts:
            rprint(f"[bold yellow]Found {len(conflicts)} conflicting file(s) across enabled mods. Run [cyan]hookshot-mods conflicts[/cyan] for details.[/bold yellow]")
    else:
        print(f"{'Order':<6} {'State':<10} {'Name':<26} {'Version':<8} {'Files':<6} {'Folder':<22}")
        print("-" * 80)
        for i, m in enumerate(mods):
            state = "ENABLED" if m.enabled else "DISABLED"
            print(f"{i+1:<6} {state:<10} {m.display_name:<26} {m.metadata.version:<8} {m.file_count:<6} {m.folder_name:<22}")
        print(f"\nMods Directory: {mgr.mods_dir}")

    return 0


def cmd_enable(args) -> int:
    mgr = get_manager(args.game_dir)
    try:
        mgr.enable_mod(args.name)
        if HAVE_RICH:
            rprint(f"[bold green]✓[/bold green] Mod [cyan]{args.name}[/cyan] is now [bold green]ENABLED[/bold green].")
        else:
            print(f"Mod '{args.name}' enabled.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_disable(args) -> int:
    mgr = get_manager(args.game_dir)
    try:
        mgr.disable_mod(args.name)
        if HAVE_RICH:
            rprint(f"[bold yellow]✓[/bold yellow] Mod [cyan]{args.name}[/cyan] is now [bold red]DISABLED[/bold red].")
        else:
            print(f"Mod '{args.name}' disabled.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_toggle(args) -> int:
    mgr = get_manager(args.game_dir)
    try:
        mgr.toggle_mod(args.name)
        mod = mgr.get_mod(args.name)
        state = "ENABLED" if (mod and mod.enabled) else "DISABLED"
        if HAVE_RICH:
            color = "green" if state == "ENABLED" else "red"
            rprint(f"[bold]✓[/bold] Mod [cyan]{args.name}[/cyan] toggled to [bold {color}]{state}[/bold {color}].")
        else:
            print(f"Mod '{args.name}' toggled to {state}.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_install(args) -> int:
    mgr = get_manager(args.game_dir)
    try:
        folder = mgr.install_mod(args.path, custom_name=args.name)
        if HAVE_RICH:
            rprint(f"[bold green]✓ Successfully installed mod into:[/bold green] [cyan]{folder}[/cyan]")
        else:
            print(f"Successfully installed mod into: {folder}")
        return 0
    except Exception as e:
        print(f"Error installing mod: {e}", file=sys.stderr)
        return 1


def cmd_create(args) -> int:
    mgr = get_manager(args.game_dir)
    try:
        folder = mgr.create_mod(
            name=args.name,
            author=args.author,
            version=args.version,
            description=args.desc,
            category=args.category,
        )
        if HAVE_RICH:
            rprint(f"[bold green]✓ Created mod template:[/bold green] [cyan]{folder}[/cyan]")
            rprint(f"[dim]Place your RomFS edits inside: [bold]{os.path.join(mgr.mods_dir, folder, 'romfs')}[/bold][/dim]")
        else:
            print(f"Created mod template: {folder}")
            print(f"Place your RomFS edits inside: {os.path.join(mgr.mods_dir, folder, 'romfs')}")
        return 0
    except Exception as e:
        print(f"Error creating mod: {e}", file=sys.stderr)
        return 1


def cmd_delete(args) -> int:
    mgr = get_manager(args.game_dir)
    mod = mgr.get_mod(args.name)
    if not mod:
        print(f"Error: Mod '{args.name}' not found.", file=sys.stderr)
        return 1

    if not args.yes:
        confirm = input(f"Are you sure you want to permanently delete mod '{mod.display_name}' ({mod.folder_name})? [y/N]: ")
        if confirm.lower() != "y":
            print("Aborted.")
            return 0

    try:
        mgr.delete_mod(mod.folder_name)
        if HAVE_RICH:
            rprint(f"[bold green]✓[/bold green] Deleted mod [cyan]{mod.display_name}[/cyan].")
        else:
            print(f"Deleted mod {mod.display_name}.")
        return 0
    except Exception as e:
        print(f"Error deleting mod: {e}", file=sys.stderr)
        return 1


def cmd_conflicts(args) -> int:
    mgr = get_manager(args.game_dir)
    conflicts = mgr.get_conflicts()

    if not conflicts:
        if HAVE_RICH:
            rprint("[bold green]✓ No file conflicts detected among enabled mods![/bold green]")
        else:
            print("No file conflicts detected among enabled mods!")
        return 0

    if HAVE_RICH:
        console = Console()
        table = Table(title="[bold yellow]File Overlaps & Precedence[/bold yellow]", show_lines=True)
        table.add_column("RomFS File Path", style="bold white")
        table.add_column("Conflicting Mods (Load Order)", style="dim")
        table.add_column("Winning Mod (Applied in Game)", style="bold green")

        for file_path, entry in sorted(conflicts.items()):
            table.add_row(
                file_path,
                " -> ".join(entry.mods),
                f"[bold green]{entry.winning_mod}[/bold green]",
            )
        console.print(table)
        rprint("[dim]Note: Hookshot loads mods in alphabetical order (strcasecmp). The last mod in the list wins.[/dim]")
    else:
        print(f"{'RomFS File':<40} {'Winning Mod':<25} {'Mods in order'}")
        print("-" * 90)
        for file_path, entry in sorted(conflicts.items()):
            print(f"{file_path:<40} {entry.winning_mod:<25} {' -> '.join(entry.mods)}")

    return 0


def cmd_search(args) -> int:
    mgr = get_manager(args.game_dir)
    results = mgr.search_romfs(args.query, limit=args.limit)
    if not results:
        print(f"No files matching '{args.query}' found in base game RomFS.")
        return 0

    if HAVE_RICH:
        rprint(f"[bold green]Found {len(results)} matches for '{args.query}':[/bold green]")
        for r in results:
            rprint(f"  [cyan]{r}[/cyan]")
    else:
        print(f"Found {len(results)} matches:")
        for r in results:
            print(f"  {r}")
    return 0


def cmd_dump(args) -> int:
    mgr = get_manager(args.game_dir)
    try:
        size = mgr.extract_game_asset(args.asset_path, args.dest_path, decompress=not args.raw)
        if HAVE_RICH:
            rprint(f"[bold green]✓ Successfully dumped[/bold green] [cyan]{args.asset_path}[/cyan] -> [bold white]{args.dest_path}[/bold white] ({size} bytes)")
        else:
            print(f"Successfully dumped {args.asset_path} -> {args.dest_path} ({size} bytes)")
        return 0
    except Exception as e:
        print(f"Error dumping asset: {e}", file=sys.stderr)
        return 1


def cmd_play(args) -> int:
    mgr = get_manager(args.game_dir)
    overlay = not getattr(args, "no_overlay", False)
    overlay_desc = "with F2 Dev Playground Overlay" if overlay else "without overlay"
    if HAVE_RICH:
        rprint(f"[bold green]Starting Hookshot ({overlay_desc})...[/bold green]")
        if overlay:
            rprint("[dim]Press [bold yellow]F2[/bold yellow] anytime in-game to toggle the Mod Playground / Cheats Menu.[/dim]")
    else:
        print(f"Starting Hookshot ({overlay_desc})...")
        if overlay:
            print("Press [F2] anytime in-game to toggle the Mod Playground / Cheats Menu.")
    proc = mgr.launch_game(overlay=overlay)
    if args.wait:
        proc.wait()
    return 0


def cmd_gui(args) -> int:
    try:
        from .gui import run_gui
    except (ImportError, ValueError):
        from gui import run_gui
    return run_gui(args.game_dir)


def main():
    parser = argparse.ArgumentParser(
        prog="hookshot-mods",
        description="Mod Manager for Hookshot / The Legend of Zelda: Ocarina of Time Remastered",
    )
    parser.add_argument("--game-dir", "-g", help="Path to the Hookshot game folder (default: the last one used, or found)")

    subparsers = parser.add_subparsers(dest="command")

    # list
    p_list = subparsers.add_parser("list", help="List all installed mods")
    p_list.set_defaults(func=cmd_list)

    # enable
    p_en = subparsers.add_parser("enable", help="Enable a mod")
    p_en.add_argument("name", help="Name or folder of the mod")
    p_en.set_defaults(func=cmd_enable)

    # disable
    p_dis = subparsers.add_parser("disable", help="Disable a mod")
    p_dis.add_argument("name", help="Name or folder of the mod")
    p_dis.set_defaults(func=cmd_disable)

    # toggle
    p_tog = subparsers.add_parser("toggle", help="Toggle mod state")
    p_tog.add_argument("name", help="Name or folder of the mod")
    p_tog.set_defaults(func=cmd_toggle)

    # install
    p_inst = subparsers.add_parser("install", help="Install a mod from .zip, .7z, .rar, or folder")
    p_inst.add_argument("path", help="Path to archive or folder")
    p_inst.add_argument("--name", "-n", help="Custom name for the mod")
    p_inst.set_defaults(func=cmd_install)

    # create
    p_cre = subparsers.add_parser("create", help="Create a new mod template")
    p_cre.add_argument("name", help="Name of the new mod")
    p_cre.add_argument("--author", "-a", default="Justin", help="Author name")
    p_cre.add_argument("--version", "-v", default="1.0.0", help="Mod version")
    p_cre.add_argument("--desc", "-d", default="", help="Description")
    p_cre.add_argument("--category", "-c", default="General", help="Category")
    p_cre.set_defaults(func=cmd_create)

    # delete
    p_del = subparsers.add_parser("delete", help="Delete a mod")
    p_del.add_argument("name", help="Name or folder of the mod to delete")
    p_del.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_del.set_defaults(func=cmd_delete)

    # conflicts
    p_conf = subparsers.add_parser("conflicts", help="Show file conflicts among enabled mods")
    p_conf.set_defaults(func=cmd_conflicts)

    # search
    p_srch = subparsers.add_parser("search", help="Search base game RomFS files")
    p_srch.add_argument("query", help="Search term (e.g. Kokiri, Sword, Navi, ActorInfo)")
    p_srch.add_argument("--limit", "-l", type=int, default=50, help="Max results")
    p_srch.set_defaults(func=cmd_search)

    # dump
    p_dump = subparsers.add_parser("dump", help="Extract and decompress a base game RomFS file")
    p_dump.add_argument("asset_path", help="Relative path in romfs (e.g. Pack/Actor/Item_Sword_Kokiri.pack.zs)")
    p_dump.add_argument("dest_path", help="Output file destination")
    p_dump.add_argument("--raw", action="store_true", help="Do not decompress .zs files")
    p_dump.set_defaults(func=cmd_dump)

    # play
    p_play = subparsers.add_parser("play", help="Launch the game")
    p_play.add_argument("--wait", "-w", action="store_true", help="Wait for the game process to exit")
    p_play.add_argument("--no-overlay", action="store_true", help="Linux developers: launch without the F2 dev overlay")
    p_play.set_defaults(func=cmd_play)

    # code mods
    p_code = subparsers.add_parser("code", help="Show code mods (Vulkan layers) and update hookshot_env.txt for them")
    p_code.set_defaults(func=cmd_code)

    # gui
    p_gui = subparsers.add_parser("gui", help="Launch Graphical Mod Manager (PyQt6)")
    p_gui.set_defaults(func=cmd_gui)

    args = parser.parse_args()

    if not args.command:
        # If run without arguments in a graphical environment, default to GUI if stdout is a tty and WAYLAND/DISPLAY is set
        if ("WAYLAND_DISPLAY" in os.environ or "DISPLAY" in os.environ) and sys.stdin.isatty():
            return cmd_gui(args)
        else:
            return cmd_list(args)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
