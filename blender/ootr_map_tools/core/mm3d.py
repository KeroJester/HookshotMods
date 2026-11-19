"""Majora's Mask 3D scenes from the player's own copy (no bpy; RESEARCH_NOTES.md §80a, §95).

The source is a decrypted ("NoCrypto") .3ds / .cci image or an extracted RomFS folder. A .3ds has its scenes/ folder
extracted once into the cache. A scene is <name>_info.zsi (its collision, scene command 0x03) plus rooms
<name>_<n>_info.zsi (CMB models with their textures). Nothing from MM3D is shipped: everything is read here."""
import hashlib
import os
import re

import numpy as np

SCALE = 0.025          # metres per Majora's Mask unit

NAMES = {
    'z2_clocktower': 'South Clock Town', 'z2_backtown': 'North Clock Town', 'z2_town': 'East Clock Town',
    'z2_ichiba': 'West Clock Town', 'z2_alley': 'Laundry Pool', 'z2_00keikoku': 'Termina Field',
    'z2_tenmon_dai': 'Astral Observatory', 'z2_yousei_izumi': 'Fairy Fountain', 'z2_milk_bar': 'Milk Bar',
    'z2_bomya': 'Bomb Shop', 'z2_ayashiishop': 'Curiosity Shop', 'z2_8itemshop': 'Trading Post',
    'z2_yadoya': 'Stock Pot Inn', 'z2_sonchonoie': "Mayor's Residence", 'z2_syateki_mizu': 'Town Shooting Gallery',
    'z2_takarayas': 'Treasure Chest Shop', 'z2_doujou': 'Swordsman\'s School', 'z2_posthouse': 'Post Office',
    'z2_insidetower': 'Clock Tower Interior', 'z2_okujou': 'Clock Tower Rooftop', 'z2_lost_woods': 'Lost Woods',
    'z2_20sichitai': 'Southern Swamp', 'z2_24kemonomiti': 'Road to Southern Swamp', 'z2_26sarunomori': 'Woods of Mystery',
    'z2_deku_king': "Deku King's Chamber", 'z2_21miturinmae': 'Woodfall', 'z2_miturin': 'Woodfall Temple',
    'z2_10yukiyamanomura': 'Mountain Village', 'z2_11goronnosato': 'Goron Village', 'z2_16goron_house': 'Goron Shrine',
    'z2_17setugen': 'Path to Mountain Village', 'z2_hakugin': 'Snowhead Temple', 'z2_30gyoson': 'Great Bay Coast',
    'z2_31misaki': 'Zora Cape', 'z2_bandroom': "Zora Hall rooms", 'z2_33zoracity': 'Zora Hall',
    'z2_sea': 'Great Bay Temple', 'z2_f01': 'Romani Ranch', 'z2_omoya': 'Ranch House', 'z2_ikana': 'Ikana Canyon',
    'z2_ikanamae': 'Road to Ikana', 'z2_boti': 'Ikana Graveyard', 'z2_castle': 'Ancient Castle of Ikana',
    'z2_inisie_n': 'Stone Tower Temple', 'z2_f40': 'Stone Tower', 'z2_kaizoku': 'Pirates\' Fortress',
    'z2_sougen': 'The Moon (field)', 'z2_last_link': 'Moon: Link Trial', 'kakusiana': 'Grottos',
}


def cache_dir():
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return os.path.join(base, 'ootr_map_tools', 'mm3d')


def scenes_dir(source, log=print):
    """The folder holding the scene files: from an extracted RomFS (any folder with a scenes/ inside, or scenes/
    itself) or a .3ds image (scenes/ extracted once into the cache)."""
    source = os.path.abspath(os.path.expanduser(source or ''))
    if os.path.isdir(source):
        for c in (source, os.path.join(source, 'scenes'), os.path.join(source, 'romfs', 'scenes')):
            if os.path.isdir(c) and any(n.endswith('_info.zsi') for n in os.listdir(c)):
                return c
        raise FileNotFoundError(f'no MM3D scenes in {source} (choose the extracted RomFS or your .3ds)')
    if not os.path.isfile(source):
        raise FileNotFoundError('choose your Majora\'s Mask 3D copy (a decrypted .3ds, or its extracted RomFS) in the '
                                'add-on preferences')
    st = os.stat(source)
    key = hashlib.sha1(f'{source}|{st.st_size}|{int(st.st_mtime)}'.encode()).hexdigest()[:12]
    out = os.path.join(cache_dir(), key)
    done = os.path.join(out, '.complete')
    if not os.path.exists(done):
        from ..vendor import ctr_romfs
        os.makedirs(out, exist_ok=True)
        ctr_romfs.extract(source, out, want=['scenes/'], log=log)
        open(done, 'w').close()
    return scenes_dir(out)


def list_scenes(folder):
    """-> [(scene, readable name, room count)] sorted by readable name."""
    rooms = {}
    for n in os.listdir(folder):
        m = re.match(r'(.+)_(\d+)_info\.zsi$', n)
        if m: rooms.setdefault(m.group(1), 0); rooms[m.group(1)] += 1
    out = [(s, NAMES.get(s, s), k) for s, k in rooms.items() if os.path.exists(os.path.join(folder, f'{s}_info.zsi'))]
    return sorted(out, key=lambda x: (x[1] == x[0], x[1].lower()))


def room_files(folder, scene):
    rs = []
    for n in os.listdir(folder):
        m = re.match(re.escape(scene) + r'_(\d+)_info\.zsi$', n)
        if m: rs.append((int(m.group(1)), os.path.join(folder, n)))
    return [p for _, p in sorted(rs)]


def load_scene(folder, scene, log=print):
    """-> dict(rooms=[dict(name, meshes=[dict(name, texture, pos (game axes, m), nrm, uv, col, tris)],
    textures={index: RGBA [h, w, 4]})], collision=(V, T) or None)."""
    from ..vendor.mapimport import mm3d_room
    rooms = []
    for p in room_files(folder, scene):
        name = os.path.basename(p)[:-len('_info.zsi')]
        try:
            cmb = mm3d_room.load_room(open(p, 'rb').read())
            meshes, _ = mm3d_room.room_meshes(cmb, SCALE)
        except Exception as ex:
            log(f'{name}: skipped ({ex})'); continue
        tex = {}
        for m in meshes:
            if m['texture'] not in tex:
                try: tex[m['texture']] = mm3d_room.texture_rgba(cmb, m['texture'])
                except Exception: tex[m['texture']] = np.full((4, 4, 4), 200, np.uint8)
        rooms.append(dict(name=name, meshes=meshes, textures=tex))
    col = None
    try:
        V, T, _types = mm3d_room.scene_collision(open(os.path.join(folder, f'{scene}_info.zsi'), 'rb').read(), SCALE)
        col = (V, T)
    except Exception as ex:
        log(f'{scene}: no collision ({ex})')
    return dict(rooms=rooms, collision=col)
