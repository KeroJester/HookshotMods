"""A map's settings for the editor (no bpy, RESEARCH_NOTES.md §92, §93): GameBancParam keys with readable choices.
read_settings -> {setting: value}; write_settings writes the changed ones into the map's GameBancParam (and the
GameBancInfo row for the soundscape) in the mod."""
import copy
import os

from ..vendor.byml import write_byml, U32
from .enums import NAMES, murmur3, name_of
from .lights import banc_param, work_to_rel, rel_to_work

ENVSETS = ['Master_Field', 'Dungeon', 'KokiriForest', 'KakarikoVillage', 'HyruleField_Child', 'GerudoValley',
           'DeathMountain', 'ZoraDomain', 'LostWood']
AGES = [('keep', 'Keep', 'Link stays the age he enters with'), ('child', 'Child', 'Link becomes a child here'),
        ('adult', 'Adult', 'Link becomes an adult here')]


def envsets(game):
    """EnvSetName values the game's maps use (sky scattering sets)."""
    from .banc import list_maps
    seen = []
    for b, f in list_maps(game):
        if not f: continue
        v = banc_param(game, b)[2].get('EnvSetName')
        if v and v not in seen: seen.append(v)
    return sorted(seen)


def world_params(game):
    """The sky & light presets: every WorldParam file (one per area / map)."""
    import glob
    out = []
    for f in sorted(glob.glob(game.path('WorldMgr/WorldParam/*.game__wm__WorldParam.bgyml'))):
        n = os.path.basename(f).split('.')[0]
        if not n.startswith('ootr_'): out.append(n)
    return out


def read_settings(game, banc):
    """-> {setting: value} of the map (resolved GameBancParam)."""
    from .sound import scenery_by_label
    _, own, g = banc_param(game, banc)
    wrel = work_to_rel(g.get('WorldMgrName')) or ''
    sc = scenery_by_label(game, g.get('SoundSceneryName', '')) if g.get('SoundSceneryName') else None
    if g.get('IsForceSwitchChildOrAdult'):
        age = 'child' if g.get('IsChildWhenInitializingScene', g.get('IsChildWhenChangeBanc', True)) else 'adult'   # (absent: child)
    else:
        age = 'keep'
    return dict(
        rendering=name_of(g['RenderingMode']) if 'RenderingMode' in g else 'MainField',
        envset=g.get('EnvSetName', 'Master_Field'),
        sky_preset=os.path.basename(wrel).split('.')[0] if wrel else '',
        clouds=name_of(g['CloudEnvType']) if 'CloudEnvType' in g else 'None',
        effects=name_of(g['EnvEffectType']) if 'EnvEffectType' in g else 'None',
        soundscape=('Custom' if sc['name'].startswith('Ootr_') else sc['name']) if sc else 'None',
        custom_music=(sc['bgm'] or 'NONE') if sc else 'NONE',
        custom_ambience=(sc['ambient'] or 'NONE') if sc else 'NONE',
        map_location=name_of(g['WorldMapLocationType']) if 'WorldMapLocationType' in g else 'None',
        age=age,
        horse=bool(g.get('IsEnableRide', False)),
        sun_song=bool(g.get('IsSunSongMakesMiddleTime', False)),
        timer=bool(g.get('IsEnableTimerCounting', False)),
    )


def write_settings(game, banc, mod_dir, changes):
    """changes: {setting: value} (only what changed) -> ({rel: bytes} written, report lines)."""
    from .sound import sceneries
    from ..vendor.zstd_helper import compress_zs
    gbp_rel, own, _ = banc_param(game, banc)
    own = copy.deepcopy(own)
    files, report = {}, []

    def set_hash(key, name):
        if name in (None, '', 'None'): own.pop(key, None)
        else: own[key] = U32(murmur3(name))

    for k, v in changes.items():
        if k == 'rendering': set_hash('RenderingMode', v)
        elif k == 'envset': own['EnvSetName'] = v
        elif k == 'sky_preset' and v:
            own['WorldMgrName'] = rel_to_work(f'WorldMgr/WorldParam/{v}.game__wm__WorldParam.bgyml')
        elif k == 'clouds': set_hash('CloudEnvType', v)
        elif k == 'effects': set_hash('EnvEffectType', v)
        elif k == 'map_location': set_hash('WorldMapLocationType', v)
        elif k == 'horse': own['IsEnableRide'] = bool(v)
        elif k == 'sun_song': own['IsSunSongMakesMiddleTime'] = bool(v)
        elif k == 'timer': own['IsEnableTimerCounting'] = bool(v)
        elif k == 'age':
            if v == 'keep':
                for key in ('IsForceSwitchChildOrAdult', 'IsChildWhenChangeBanc', 'IsChildWhenInitializingScene'):
                    own.pop(key, None)
            else:
                own['IsForceSwitchChildOrAdult'] = True
                own['IsChildWhenChangeBanc'] = own['IsChildWhenInitializingScene'] = (v == 'child')
        elif k == 'soundscape' and isinstance(v, tuple):          # ('Custom', music, ambience): the map's own
            from .sound import write_soundscape
            music, amb = (None if x in ('', 'NONE') else x for x in v[1:3])
            sfiles, label = write_soundscape(game, banc, music, amb)
            files.update(sfiles)
            own['SoundSceneryName'] = label
            files.update(_set_row(game, banc, mod_dir, {'SoundSceneryName': label}))
            v = f'own (music {music or "none"}, ambience {amb or "none"})'
        elif k == 'soundscape':
            sc = sceneries(game).get(v)
            if sc:
                own['SoundSceneryName'] = sc['label']
                files.update(_set_row(game, banc, mod_dir, {'SoundSceneryName': sc['label']}))
        report.append(f'map setting {k}: {v}')
    files[gbp_rel] = write_byml(own)
    for rel, data in files.items():
        p = os.path.join(mod_dir, 'romfs', rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(compress_zs(data, rel, game.romfs) if rel.endswith('.zs') else data)
    return files, report


WORLD_STEP = 125      # (the game's world boxes are multiples of 125 m: +-125 (SageRoom) .. +-2000 (Hyrule Field))


def ensure_world_box(game, banc, mod_dir, lo, hi, margin=50.0):
    """Grows the map's physics world box (GameBancParam WorldAabbMin / Max) to hold geometry lo..hi (game axes, with a
    margin); a Blank map inherits the Chamber of Sages' +-125 m. Never shrinks it. -> report lines."""
    import math
    from ..vendor.zstd_helper import compress_zs
    rel, own, res = banc_param(game, banc)
    cur_lo = res.get('WorldAabbMin') or {'X': -WORLD_STEP, 'Y': -WORLD_STEP, 'Z': -WORLD_STEP}
    cur_hi = res.get('WorldAabbMax') or {'X': WORLD_STEP, 'Y': WORLD_STEP, 'Z': WORLD_STEP}
    new_lo, new_hi, grew = dict(cur_lo), dict(cur_hi), False
    for i, ax in enumerate('XYZ'):
        need_lo = math.floor((float(lo[i]) - margin) / WORLD_STEP) * WORLD_STEP
        need_hi = math.ceil((float(hi[i]) + margin) / WORLD_STEP) * WORLD_STEP
        if need_lo < cur_lo[ax]: new_lo[ax] = int(need_lo); grew = True
        if need_hi > cur_hi[ax]: new_hi[ax] = int(need_hi); grew = True
    if not grew: return []
    own = copy.deepcopy(own)
    own['WorldAabbMin'], own['WorldAabbMax'] = new_lo, new_hi
    p = os.path.join(mod_dir, 'romfs', rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = write_byml(own)
    with open(p, 'wb') as f:
        f.write(compress_zs(data, rel, game.romfs) if rel.endswith('.zs') else data)
    return [f'world box grown to {new_lo} .. {new_hi} (the map\'s geometry reaches {tuple(round(float(v)) for v in lo)} .. '
            f'{tuple(round(float(v)) for v in hi)})']


def _set_row(game, banc, mod_dir, values):
    """Changes the map's GameBancInfo row (the shared table: every other row kept, also other mods')."""
    from .newmap import merged_tables, INFO
    rows, _sp, _later = merged_tables(game, mod_dir)
    if banc not in rows: return {}
    row = dict(rows[banc]); row.update(values); rows[banc] = row
    return {INFO: write_byml([rows[k] for k in sorted(rows)])}
