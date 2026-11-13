#!/usr/bin/env python3
"""Builds the standalone Hookshot Mods app with PyInstaller.

    pip install -r packaging/requirements-app.txt
    python packaging/build_app.py

Windows: dist/HookshotMods.exe (one file).
Linux:   dist/HookshotMods/ (folder) plus dist/AppDir/, ready for appimagetool:
             appimagetool dist/AppDir dist/HookshotMods-x86_64.AppImage
The CI workflow (.github/workflows/build.yml) runs exactly this on Windows and Linux."""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DIST = os.path.join(REPO, "dist")
WIN = sys.platform.startswith("win")
NAME = "HookshotMods"
BUNDLED = os.path.join(DIST, "bundled_mods")   # the mods that come with the app (plugins/*/package.sh)


def pyinstaller():
    print("included mods:", ", ".join(os.listdir(BUNDLED)) if os.path.isdir(BUNDLED) else "none")
    sep = ";" if WIN else ":"
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
            "--distpath", DIST, "--workpath", os.path.join(REPO, "build", "pyinstaller"),
            "--specpath", os.path.join(REPO, "build"),
            "--paths", REPO,
            "--add-data", f"{os.path.join(HERE, 'icon.png')}{sep}packaging",
            *(["--add-data", f"{BUNDLED}{sep}bundled_mods"] if os.path.isdir(BUNDLED) else []),
            "--icon", os.path.join(HERE, "icon.ico" if WIN else "icon.png"),
            # the mod builders' heavy dependencies are not part of the app
            "--exclude-module", "numpy", "--exclude-module", "PIL", "--exclude-module", "tkinter",
            "--onefile" if WIN else "--onedir",
            os.path.join(REPO, "mod_manager", "app.py")]
    subprocess.check_call(args)


def appdir():
    """dist/AppDir: the onedir build under usr/bin, plus AppRun, the .desktop entry and the icon."""
    root = os.path.join(DIST, "AppDir")
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(os.path.join(DIST, NAME), os.path.join(root, "usr", "bin"))
    shutil.copy(os.path.join(HERE, "icon.png"), os.path.join(root, "hookshot-mods.png"))
    with open(os.path.join(root, "hookshot-mods.desktop"), "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Hookshot Mods\nComment=Mod manager for Hookshot\n"
                "Exec=HookshotMods\nIcon=hookshot-mods\nCategories=Game;Utility;\nTerminal=false\n")
    apprun = os.path.join(root, "AppRun")
    with open(apprun, "w") as f:
        f.write('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/bin/HookshotMods" "$@"\n')
    os.chmod(apprun, 0o755)
    print(f"AppDir: {root}")


if __name__ == "__main__":
    pyinstaller()
    if not WIN:
        appdir()
