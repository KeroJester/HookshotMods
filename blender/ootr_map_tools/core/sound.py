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
    return [s for n, s in sorted(sceneries(game).items()) if not n.startswith('Area_') and n != 'Base']


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
