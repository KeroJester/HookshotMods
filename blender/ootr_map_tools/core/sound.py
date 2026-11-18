"""The game's sound for maps (no bpy, RESEARCH_NOTES.md §93), read from the player's own files.

A map's sound is made of:
  soundscape  GameBancParam.SoundSceneryName (+ the GameBancInfo row) = the Label (Japanese) of a scenery,
              Sound/Scenery/<Name>.game__sound__SceneryParam.bgyml in Pack/Bootup.Nin_Ounce_NVN2.pack, listed in
              Sound/Product.111.alto__AltoConfig.bgyml Scenery. A scenery has BgmName (the map's music: a bgm.byml
              BGM of L_Scene/Field), BaseAmbientSound, EnvSoundType, Env*SoundVolume, ...; $parent Base.
  music zones BGM_<track> actors linked ("Reference") to Area boxes (Scale = size; Dynamic.RigidBodyShapeType for
              other shapes): the track plays inside (L_Area layer; Market's music is BGM_Market, its scenery has none).
  ambience zones  SoundSettingActor {Dynamic: SceneryName (an Area_* scenery), Distance (fade, m)} linked to Areas.
  emitters    SoundLocator_<sound> actors (Scale = their extent)."""
from functools import lru_cache

from ..vendor.byml import parse_byml


@lru_cache(maxsize=4)
def _sceneries(game_key, game):
    from ..vendor.zstd_helper import unpack_sarc_to_dict
    pk = unpack_sarc_to_dict(game.read('Pack/Bootup.Nin_Ounce_NVN2.pack.zs'))
    out = {}
    for k, v in pk.items():
        if k.startswith('Sound/Scenery/') and k.endswith('.bgyml'):
            name = k.split('/')[-1].split('.')[0]
            y = parse_byml(v)
            out[name] = dict(name=name, label=y.get('Label', name), bgm=y.get('BgmName'),
                             ambient=y.get('BaseAmbientSound'), parent=y.get('$parent'))
    return out


def sceneries(game):
    """{English name: dict(name, label, bgm, ambient, parent)} of every scenery (map soundscapes and Area_* zone
    ambiences)."""
    return _sceneries(game.romfs, game)


def soundscapes(game):
    """Map-wide soundscapes (not the Area_* zone presets), sorted, with what they play."""
    return [s for n, s in sorted(sceneries(game).items()) if not n.startswith(('Area_', 'Ootr_')) and n != 'Base']


def music_choices(game):
    """Map music a soundscape can name: the BgmName values of the game's sceneries (each one matches a bgm.byml
    start condition)."""
    return sorted({s['bgm'] for s in sceneries(game).values() if s['bgm']})


def ambience_choices(game):
    return sorted({s['ambient'] for s in sceneries(game).values() if s['ambient']})


BOOTUP = 'Pack/Bootup.Nin_Ounce_NVN2.pack.zs'
ALTO = 'Sound/Product.111.alto__AltoConfig.bgyml'


def custom_name(banc):
    return f'Ootr_{banc}'


def write_soundscape(game, banc, music, ambience):
    """A map's own soundscape: scenery Ootr_<map> {BgmName: music, BaseAmbientSound: ambience} (the rest copied
    from a game scenery with that ambience) added to the Bootup pack the game would load (other mods' sceneries
    kept) and to the AltoConfig scenery list. -> ({rel: bytes}, label)."""
    import struct
    from ..vendor.byml import write_byml
    from ..vendor.zstd_helper import unpack_sarc_to_dict
    from .lights import sarc_add_many
    name = custom_name(banc)
    rel = f'Sound/Scenery/{name}.game__sound__SceneryParam.bgyml'
    pk_raw = game.read(BOOTUP)
    pk = unpack_sarc_to_dict(pk_raw)
    base = next((s for s in sceneries(game).values() if ambience and s['ambient'] == ambience and not s['name'].startswith('Ootr_')), None) \
        or sceneries(game).get('HyruleField_Child') or next(iter(sceneries(game).values()))
    sc = dict(parse_byml(pk[f'Sound/Scenery/{base["name"]}.game__sound__SceneryParam.bgyml']))
    label = f'ootr_{banc}'
    sc['Label'] = label
    if music: sc['BgmName'] = music
    else: sc.pop('BgmName', None)
    if ambience: sc['BaseAmbientSound'] = ambience
    else: sc.pop('BaseAmbientSound', None)
    blob = write_byml(sc)
    if rel in pk:                                  # (re-export: the pack without our old copy, then ours)
        pk_raw = _sarc_drop(pk_raw, rel)
    files = {BOOTUP: sarc_add_many(pk_raw, {rel: blob})}
    alto = dict(parse_byml(game.read(ALTO)))
    work = 'Work/' + rel.replace('.bgyml', '.gyml')
    if work not in alto['Scenery']:
        alto['Scenery'] = list(alto['Scenery']) + [work]
    files[ALTO] = write_byml(alto)
    _sceneries.cache_clear()
    return files, label


def _sarc_drop(d, name):
    """The SARC without one file's node (its bytes stay as unused padding; every other offset unchanged)."""
    import struct
    from ..vendor.zstd_helper import sarc_hash
    h = sarc_hash(name)
    dstart = struct.unpack_from('<I', d, 0xc)[0]
    n = struct.unpack_from('<H', d, 0x1a)[0]
    nodes = [struct.unpack_from('<IIII', d, 0x20 + 16 * i) for i in range(n)]
    keep = [x for x in nodes if x[0] != h]
    sfnt = 0x20 + 16 * n
    strs = d[sfnt + 8:dstart]
    out = bytearray(d[:0x14]) + struct.pack('<4sHHI', b'SFAT', 0x0C, len(keep), struct.unpack_from('<I', d, 0x1c)[0])
    for x in keep: out += struct.pack('<IIII', *x)
    out += struct.pack('<4sHH', b'SFNT', 8, 0) + strs
    out += b'\0' * (dstart - len(out)) + d[dstart:]
    struct.pack_into('<I', out, 8, len(out))
    return bytes(out)


def zone_ambiences(game):
    return [s for n, s in sorted(sceneries(game).items()) if n.startswith('Area_')]


def scenery_by_label(game, label):
    return next((s for s in sceneries(game).values() if s['label'] == label), None)


@lru_cache(maxsize=4)
def _actor_ids(game_key, game):
    return [r['__RowId'] for r in game.byml('RSDB/ActorInfo.Product.111.rstbl.byml.zs')]


def zone_tracks(game):
    """Music a zone can play: the BGM_* actor types."""
    return [i for i in _actor_ids(game.romfs, game) if i.startswith('BGM_')]


def emitters(game):
    """Placeable sound emitters: the SoundLocator_* actor types."""
    return [i for i in _actor_ids(game.romfs, game) if i.startswith('SoundLocator_')]


def pretty(name):
    """BGM_Shop_Market_Mask -> 'Shop Market Mask'; SoundLocator_HyruleField_RiverLarge -> 'Hyrule Field River Large'."""
    import re
    for p in ('BGM_', 'SoundLocator_', 'Area_'):
        if name.startswith(p): name = name[len(p):]
    name = name.replace('_', ' ')
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name)
