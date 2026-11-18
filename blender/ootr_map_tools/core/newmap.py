"""New maps (no bpy, RESEARCH_NOTES.md §88). A new map is ADDED next to the game's: every file it needs is a copy of
a template map's file under the new name, and the two shared tables that list maps get a row appended (all existing
rows kept, also the ones other enabled mods added: the last mod in load order wins, there is no merging).

Per-map files (named after the map, found by the template's name):
  Banc/GameBancParam/<m>.game__banc__GameBancParam.bgyml        map parameters (Banc.Main -> the scene path)
  Banc/<folder>/<m>_{Static,Dynamic}.bcett.byml.zs                placements (FilePath = the scene path)
  Phive/StaticCompoundBody/<folder>/<m>[_Xi_Zj].Nin_Ounce_NVN2.bphsc.zs   collision of the static actors
  Phive/NavMesh/<m>.Nin_Ounce_NVN2.bphnm.zs, Phive/VolumeStats/<folder>/<m>_Xi_Zj.*, Game/VolumeStatsAABB/<m>.*
  Bake/Scene/<m>_G*.bkres.zs, Bake/Light/<m>.bklight.zs, Bake/Probe/Scene/<m>/*, Game/BakeSceneLayerConfig/<m>.*
  Foliage/ResFoliageScene/<m>.* + Foliage/Placement/Scene/<folder>/<m>.*.flnt.zs   (grass etc.: full copies only)
Shared tables: RSDB/GameBancInfo.Product.111.rstbl.byml.zs (rows sorted by __RowId: BancMain, SoundSceneryName, ...),
  Banc/BancBasedResource/StartPos.byml.zs ({map: {start name: {Rot, Trans}}}).
The new map's folder is its own name (Banc/<m>/, Phive/StaticCompoundBody/<m>/), as the game derives it from
Banc.Main = Work/Banc/Scene/<m>/<m>.bcett.json (verified with ClockTownDev, §80b)."""
import copy
import glob
import os
import re

from ..vendor.byml import write_byml
from .banc import find_folder

INFO = 'RSDB/GameBancInfo.Product.111.rstbl.byml.zs'
STARTPOS = 'Banc/BancBasedResource/StartPos.byml.zs'
BLANK_TEMPLATE = 'SageRoom'   # (the Blank map's skeleton: a small mesh-only collision body, no kill plane, no terrain)
LOOK_KEYS = ('EnvSetName', 'CloudEnvType', 'EnvEffectType', 'WorldMgrName', 'AltSceneBakeType', 'RenderingMode')
KEEP_MODES = (('land', 'Ground pieces', 'The template\'s map pieces (Land_*) and start point, no NPCs, enemies or objects'),
              ('all', 'Full copy', 'Everything of the template, as a new map'))
LAND_KEEP = ('Land_', 'StartPos', 'VolumeFogArea', 'EnvPalette', 'BGM_', 'SoundSettingActor', 'LocationArea', 'EftLoc_')


def valid_name(name):
    return bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{2,40}', name or ''))


def enabled_mods(game):
    """The enabled mod folders in Hookshot's load order (alphabetical, case-insensitive; the last one wins)."""
    d = os.path.join(game.folder, 'mods')
    if not os.path.isdir(d): return []
    names = [n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n)) and n[:1] not in '_-']
    return [os.path.join(d, n) for n in sorted(names, key=str.lower)]


def _read_mod_byml(game, path, rel):
    from ..vendor.byml import parse_byml
    from ..vendor.zstd_helper import decompress_zs
    raw = open(path, 'rb').read()
    return parse_byml(decompress_zs(raw, rel, game.romfs) if rel.endswith('.zs') else raw)


def merged_tables(game, mod_dir):
    """GameBancInfo rows and StartPos entries: the game's plus every enabled mod's additions (and this mod's own).
    -> (rows by id, startpos dict, [mods whose copy loads after this mod's: they would hide the new map])."""
    rows = {r['__RowId']: r for r in game.byml_base(INFO)} if hasattr(game, 'byml_base') else {r['__RowId']: r for r in game.byml(INFO)}
    sp = dict(game.byml_base(STARTPOS) if hasattr(game, 'byml_base') else game.byml(STARTPOS))
    later = []
    mine = os.path.normcase(os.path.abspath(mod_dir))
    mods = enabled_mods(game)
    if mine not in [os.path.normcase(os.path.abspath(m)) for m in mods]: mods.append(mod_dir)
    passed = False
    for m in mods:
        here = os.path.normcase(os.path.abspath(m)) == mine
        for rel, kind in ((INFO, 'info'), (STARTPOS, 'sp')):
            p = os.path.join(m, 'romfs', rel)
            if not os.path.exists(p): continue
            if passed and not here: later.append(os.path.basename(m))
            try:
                doc = _read_mod_byml(game, p, rel)
            except Exception:
                continue
            if kind == 'info':
                for r in doc: rows.setdefault(r['__RowId'], r) if not here else rows.__setitem__(r['__RowId'], r)
            else:
                for k, v in doc.items():
                    if here or k not in sp: sp[k] = v
        if here: passed = True
    return rows, sp, sorted(set(later))


def template_files(game, template, folder):
    """{template rel: new-name rel pattern} of the template's per-map files (see the module notes)."""
    t = template
    pats = [f'Bake/Scene/{t}_G*.bkres.zs', f'Bake/Light/{t}.bklight.zs', f'Bake/Probe/Scene/{t}/*',
            f'Game/BakeSceneLayerConfig/{t}.*', f'Game/VolumeStatsAABB/{t}.*', f'Phive/NavMesh/{t}.*',
            f'Phive/StaticCompoundBody/{folder}/{t}.*', f'Phive/StaticCompoundBody/{folder}/{t}_X*',
            f'Phive/VolumeStats/{folder}/{t}_X*']
    out = []
    for p in pats:
        for f in sorted(glob.glob(game.path(p))):
            if os.path.isfile(f): out.append(os.path.relpath(f, game.romfs).replace(os.sep, '/'))
    return out


def renamed(rel, template, folder, name):
    d, b = rel.rsplit('/', 1)
    if d.endswith('/' + folder) and (d.startswith('Phive/StaticCompoundBody') or d.startswith('Phive/VolumeStats')):
        d = d[:-len(folder)] + name
    if d == f'Bake/Probe/Scene/{template}': d = f'Bake/Probe/Scene/{name}'
    if b.startswith(template): b = name + b[len(template):]
    return f'{d}/{b}'


def filter_actors(doc, keep):
    """Placements kept for a keep mode; links, AI groups and group lists to removed ones are dropped."""
    doc = copy.deepcopy(doc)
    acts = doc.get('Actors', [])
    if keep == 'start':
        acts = [a for a in acts if a.get('Gyaml') == 'StartPos' or str(a.get('Gyaml', '')).startswith(('BGM_', 'SoundSettingActor'))]
    elif keep == 'land':
        acts = [a for a in acts if str(a.get('Gyaml', '')).startswith(LAND_KEEP)]
    have = {int(a['Hash']) for a in acts if 'Hash' in a}
    for a in acts:
        if 'Links' in a:
            a['Links'] = [l for l in a['Links'] if int(l.get('Dst', 0)) in have]
            if not a['Links']: a.pop('Links')
    if 'Actors' in doc: doc['Actors'] = acts
    if keep != 'all':
        if 'AiGroups' in doc:
            doc['AiGroups'] = [g for g in doc['AiGroups']
                               if all(int(r.get('Reference', 0)) in have for r in g.get('References', []))]
        for k in ('SimultaneousGroups', 'FarDeleteGroups'):
            if k in doc:
                doc[k] = [[h for h in grp if int(h) in have] for grp in doc[k]]
                doc[k] = [grp for grp in doc[k] if len(grp) > 1]
        if keep == 'start': doc.pop('Rails', None)
    return doc


def create_map(game, name, template, keep, mod_dir, look_from=None, sound_from=None):
    """Writes a new map into <mod_dir>/romfs. -> (report lines, {rel: decompressed bytes} written)."""
    from ..vendor.zstd_helper import compress_zs
    if not valid_name(name):
        raise ValueError('map names are 3-41 letters, digits or _, starting with a letter')
    rows, sp, later = merged_tables(game, mod_dir)
    if name in rows or game.exists(f'Banc/GameBancParam/{name}.game__banc__GameBancParam.bgyml'):
        raise ValueError(f'a map named {name} already exists')
    if template not in rows:
        raise ValueError(f'no map {template}')
    folder = find_folder(game, template)
    if not folder:
        raise ValueError(f'{template} has no placement files')
    scene = f'Work/Banc/Scene/{name}/{name}.bcett.json'
    files, report = {}, []
    # map parameters
    gbp_rel = f'Banc/GameBancParam/{template}.game__banc__GameBancParam.bgyml'
    gbp = copy.deepcopy(game.byml(gbp_rel))
    gbp['Banc'] = {'Main': scene}
    if keep != 'all':
        for k in ('OptimizeChangeBancUnits', 'FoliageScene', 'NpcIgnoreFootIKAreaInfoArray', 'HeightMapParam'):
            gbp.pop(k, None)
        for k in ('UseNpcIgnoreFootIKArea', 'IsRaffleRichardAppearPos', 'IsGenerateAutogenGameData'):
            if k in gbp: gbp[k] = False
    if keep == 'start':
        lrel = f'System/GameGraphics/LocationalLightScenePlacement/ootr_{name}.game__gfx__LocationalLightScenePlacement.bgyml'
        files[lrel] = write_byml({'GroupSetting': {}, 'Hash': 0})
        gbp['LocationalLightScenePlacement'] = 'Work/' + lrel.replace('.bgyml', '.gyml')
    if look_from:                                       # (sky, clouds, weather effects, sun / time of day of another map)
        from .lights import banc_param
        look = banc_param(game, look_from)[2]
        for k in LOOK_KEYS:
            if k in look: gbp[k] = copy.deepcopy(look[k])
            else: gbp.pop(k, None)
        report.append(f'sky and light like {look_from}')
    sound = None
    if sound_from:                                      # (ambient sound scenery of another map)
        from .lights import banc_param
        snd = banc_param(game, sound_from)[2]
        if 'SoundSceneryName' in snd: gbp['SoundSceneryName'] = snd['SoundSceneryName']
        sound = rows.get(sound_from)
        report.append(f'ambient sound like {sound_from}')
    files[f'Banc/GameBancParam/{name}.game__banc__GameBancParam.bgyml'] = write_byml(gbp)
    # placements
    starts = []
    for layer in ('Static', 'Dynamic'):
        src = f'Banc/{folder}/{template}_{layer}.bcett.byml.zs'
        doc = game.byml(src) if game.exists(src) else {'Actors': []}
        doc = filter_actors(doc, keep)
        doc['FilePath'] = scene
        starts += [a for a in doc.get('Actors', []) if a.get('Gyaml') == 'StartPos']
        files[f'Banc/{name}/{name}_{layer}.bcett.byml.zs'] = write_byml(doc)
        report.append(f'{layer}: {len(doc.get("Actors", []))} placements')
    # per-map resources, renamed copies
    n = 0
    for rel in template_files(game, template, folder):
        files[renamed(rel, template, folder, name)] = game.read(rel); n += 1
    if keep == 'all' and gbp.get('FoliageScene'):
        fs = gbp['FoliageScene']
        frel = fs[len('Work/'):].replace('.gyml', '.bgyml')
        if game.exists(frel) and os.path.basename(frel).startswith(template):
            new = renamed(frel, template, folder, name)
            files[new] = game.read(frel)
            gbp['FoliageScene'] = 'Work/' + new.replace('.bgyml', '.gyml')
            files[f'Banc/GameBancParam/{name}.game__banc__GameBancParam.bgyml'] = write_byml(gbp)
        for f in glob.glob(game.path(f'Foliage/Placement/Scene/{folder}/{template}.*')):
            rel = os.path.relpath(f, game.romfs).replace(os.sep, '/')
            b = os.path.basename(rel)
            files[f'Foliage/Placement/Scene/{name}/{name}{b[len(template):]}'] = game.read(rel); n += 1
    report.append(f'{n} map resources copied from {template} (collision, navigation, lighting bake, probes)')
    if keep == 'start':
        report.append('empty: the template\'s floor collision stays until the map has collision of its own')
    # shared tables: rows appended
    row = copy.deepcopy(rows[template]); row['BancMain'] = scene; row['__RowId'] = name
    if sound:
        for k in ('SoundSceneryName', 'IsEnabledTimeSound'):
            if k in sound: row[k] = sound[k]
            else: row.pop(k, None)
    rows[name] = row
    files[INFO] = write_byml([rows[k] for k in sorted(rows)])
    if template in sp:
        sp[name] = copy.deepcopy(sp[template])
    else:
        t = starts[0].get('Translate', [0.0, 100.0, 0.0]) if starts else [0.0, 100.0, 0.0]
        sp[name] = {'': {'Rot': [0.0, 0.0, 0.0], 'Trans': [float(v) for v in t]}}
    files[STARTPOS] = write_byml(dict(sorted(sp.items())))
    report.append(f'registered {name} (map list: {len(rows)} maps)')
    if later:
        report.append('warning: ' + ', '.join(later) + ' load after this mod with their own map list: move the map '
                      'tools mod after them (rename its folder) or the new map stays hidden')
    # (everything is ready: only now is anything written, so a failure leaves no half-made map)
    blobs = {rel: (compress_zs(data, rel, game.romfs) if rel.endswith('.zs') else data) for rel, data in files.items()}
    for rel, blob in blobs.items():
        p = os.path.join(mod_dir, 'romfs', rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f: f.write(blob)
    if keep == 'start':                                 # (custom collision replaces the template floor: core/custom.py)
        side = os.path.join(mod_dir, 'ootr_compound')
        os.makedirs(side, exist_ok=True)
        open(os.path.join(side, f'{name}.empty'), 'w').close()
    return report, files
