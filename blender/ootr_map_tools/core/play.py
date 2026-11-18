"""Play in game: a test profile (a copy of the player's own profile, never the original) whose saves start in a chosen
map at a chosen position, then the game started with it (RESEARCH_NOTES.md §84). No game code is involved:

progress.sav (header u32 0x01020304, u32 version hash, u32 data offset; then {u32 key hash, u32 value} pairs, values that
do not fit in 4 bytes are offsets into the data part; key names are hashes, types in GameData/GameDataList):
  0x1d6189da  String64  the current map (banc) - e.g. "Market_Child"
  0xc884818d, 0x8773f02d, 0x960ad56a  Vector3  the player's saved position(s), game axes
Loading a save with the map and these positions changed puts Link in that map at that point (verified: Kakariko)."""
import os
import shutil
import struct
import subprocess
import sys

MAP_KEY = 0x1d6189da
POS_KEYS = (0xc884818d, 0x8773f02d, 0x960ad56a)


def profile_dir():
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return os.path.join(base, 'ootr_map_tools', 'play_user')


def find_profile(game_folder):
    """The player's profile folder (holding save/): <game folder>/user (Hookshot, older Longshot), else Longshot's
    data folder ($XDG_DATA_HOME/longshot or ~/.local/share/longshot; %APPDATA% / %LOCALAPPDATA%\\longshot on
    Windows; the binary notes "saves-moved.txt": "Longshot's saves are now in ...")."""
    cands = [os.path.join(game_folder, 'user')]
    xdg = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    for name in ('longshot', 'hookshot'):
        cands.append(os.path.join(xdg, name))
        for env in ('APPDATA', 'LOCALAPPDATA'):
            if os.environ.get(env): cands.append(os.path.join(os.environ[env], name))
    for c in cands:
        if os.path.isdir(os.path.join(c, 'save')):
            return c
    raise FileNotFoundError('no profile with saves found (looked in: ' + ', '.join(cands) + ')')


def make_profile(source_user, dest=None, refresh=True):
    """Copies the player's saves (source_user/save) into the test profile; the source is only read (Longshot's data
    folder also holds the launcher's files: only save/ is copied)."""
    dest = dest or profile_dir()
    if refresh and os.path.isdir(dest):
        shutil.rmtree(dest)
    if not os.path.isdir(dest):
        src = os.path.join(source_user, 'save')
        if not os.path.isdir(src):
            raise FileNotFoundError(f'no saves at {src}')
        shutil.copytree(src, os.path.join(dest, 'save'))
        os.makedirs(os.path.join(dest, 'temp'), exist_ok=True)
    return dest


def patch_save(path, banc, pos):
    """One progress.sav: map name and saved positions. -> True when both were found."""
    with open(path, 'rb') as f:
        d = bytearray(f.read())
    if struct.unpack_from('<I', d, 0)[0] != 0x01020304:
        return False
    data = struct.unpack_from('<I', d, 8)[0]
    found_map = found_pos = False
    name = banc.encode()
    assert len(name) < 64, banc
    for o in range(0x20, data, 8):
        h, v = struct.unpack_from('<II', d, o)
        if h == MAP_KEY and v + 64 <= len(d):
            d[v:v + 64] = name.ljust(64, b'\0'); found_map = True
        elif h in POS_KEYS and v + 12 <= len(d):
            struct.pack_into('<3f', d, v, *pos); found_pos = True
    if found_map:
        with open(path, 'wb') as f:
            f.write(d)
    return found_map and found_pos


def patch_profile(profile, banc, pos):
    n = 0
    for root, _dirs, files in os.walk(profile):
        for f in files:
            if f == 'progress.sav' and patch_save(os.path.join(root, f), banc, pos):
                n += 1
    return n


def game_binary(game_folder):
    for name in ('longshot', 'hookshot'):
        for exe in (name + '.exe', name):
            p = os.path.join(game_folder, 'bin', exe)
            if os.path.isfile(p):
                return p
    raise FileNotFoundError(f'no bin/longshot or bin/hookshot in {game_folder}')


def launch(game_folder, profile):
    """Starts the game itself (bin/<runtime>, not the folder's launcher script) with the test profile."""
    exe = game_binary(game_folder)
    flags = {'creationflags': 0x00000008} if sys.platform == 'win32' else {'start_new_session': True}   # (detached)
    return subprocess.Popen([exe, '--user', profile], cwd=game_folder, **flags)


def play(game_folder, banc, pos, refresh=True):
    """-> (profile, saves patched, process)."""
    profile = make_profile(find_profile(game_folder), refresh=refresh)
    n = patch_profile(profile, banc, pos)
    if n == 0:
        raise RuntimeError('no save in the profile has the map / position fields (start the game once and save)')
    return profile, n, launch(game_folder, profile)
