"""The add-on's runtime mod, BlenderMod (plugins/blender_mod, bundled in ./blendermod), and the code layers of the
enabled mods for games the add-on starts (RESEARCH_NOTES.md §89).
BlenderMod raises / adds the size table entries maps need (each mod's rstb.txt). The game reads one size table and the
last mod in load order wins: when MajoraMod 0.4.2+ is enabled it does the job and BlenderMod steps aside."""
import glob
import json
import os
import shutil
import sys

FOLDER = '065-BlenderMod'
BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'blendermod')


def _version(info_path):
    try:
        with open(info_path, encoding='utf-8-sig') as f:
            v = json.load(f).get('version', '0')
        return tuple(int(x) for x in v.split('.')[:3])
    except Exception:
        return (0,)


def enabled_mod(game, suffix):
    """The enabled mod folder whose name ends with suffix (e.g. 'MajoraMod'), or None."""
    for d in sorted(glob.glob(os.path.join(game.folder, 'mods', '*'))):
        n = os.path.basename(d)
        if os.path.isdir(d) and n[:1] not in '_-' and n.endswith(suffix):
            return d
    return None


def table_owner(game):
    """The mod folder whose size table copy the game reads for map edits: MajoraMod 0.4.2+ if enabled, else
    BlenderMod (installed here if needed)."""
    m = enabled_mod(game, 'MajoraMod')
    if m and _version(os.path.join(m, 'info.json')) >= (0, 4, 2):
        return m
    return ensure_blendermod(game)


def ensure_blendermod(game):
    """Installs / updates BlenderMod in the game's mods folder (its own size table copy is kept). -> its folder."""
    have = enabled_mod(game, 'BlenderMod')
    dst = have or os.path.join(game.folder, 'mods', FOLDER)
    if not os.path.isdir(BUNDLED):
        return dst if have else None
    if have and _version(os.path.join(have, 'info.json')) >= _version(os.path.join(BUNDLED, 'info.json')):
        return have
    os.makedirs(dst, exist_ok=True)
    shutil.copy2(os.path.join(BUNDLED, 'info.json'), os.path.join(dst, 'info.json'))
    for plat in ('linux', 'windows'):
        src = os.path.join(BUNDLED, 'layer', plat)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dst, 'layer', plat), dirs_exist_ok=True)
    os.makedirs(os.path.join(dst, 'blender'), exist_ok=True)
    return dst


def layer_env(game, env=None):
    """VK_ADD_LAYER_PATH / VK_INSTANCE_LAYERS for the enabled code mods (as the mod manager's Launch sets them), so a
    game started from Blender runs BlenderMod / MajoraMod too."""
    env = dict(os.environ if env is None else env)
    plat = 'windows' if sys.platform.startswith('win') else 'linux'
    sep = ';' if plat == 'windows' else ':'
    paths = [p for p in env.get('VK_ADD_LAYER_PATH', '').split(sep) if p]
    names = [n for n in env.get('VK_INSTANCE_LAYERS', '').split(sep) if n]
    for d in sorted(glob.glob(os.path.join(game.folder, 'mods', '*')), key=lambda p: os.path.basename(p).lower()):
        if not os.path.isdir(d) or os.path.basename(d)[:1] in '_-': continue
        try:
            with open(os.path.join(d, 'info.json'), encoding='utf-8-sig') as f:
                spec = json.load(f).get('code', {}).get('vulkan_layer')
        except Exception:
            continue
        if not spec or not spec.get(plat): continue
        folder = os.path.join(d, spec[plat])
        if not glob.glob(os.path.join(folder, '*.json')): continue
        paths.append(folder); names.append(spec['name'])
    if paths:
        env['VK_ADD_LAYER_PATH'] = sep.join(dict.fromkeys(paths))
        env['VK_INSTANCE_LAYERS'] = sep.join(dict.fromkeys(names))
    return env
