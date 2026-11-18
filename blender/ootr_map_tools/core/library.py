"""The asset library's catalogue (no bpy, RESEARCH_NOTES.md §85): every actor of RSDB/ActorInfo with a model, a
category path from its name, and a template placement (a real placement of the actor in some map) that new placements
are cloned from. Built from the player's own game files into a local folder; nothing of the game ships."""
import copy
import json
import os

from .banc import list_maps

TOP = {'Land': 'Map pieces', 'Obj': 'Objects', 'FldObj': 'Objects', 'Npc': 'NPCs', 'Enemy': 'Enemies',
       'Item': 'Items', 'Pouch': 'Items', 'Weapon': 'Items', 'Armor': 'Items', 'Shop': 'Shop items', 'Animal': 'Animals',
       'EnvironmentalAnimal': 'Animals', 'TBox': 'Objects', 'Dm': 'Cutscene props', 'CmnTex': 'Objects'}


def library_dir():
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return os.path.join(base, 'ootr_map_tools', 'asset_library')


def category(actor):
    """Land_Kakariko_Building_A_02 -> ('Map pieces', 'Kakariko', 'Building'); Obj_Door_ShopMask -> ('Objects', 'Door')."""
    parts = actor.split('_')
    top = TOP.get(parts[0], 'Other')
    rest = [p for p in parts[1:] if not (p.isdigit() or (len(p) == 1 and p.isalpha()))]
    if top == 'Map pieces':
        return (top, *rest[:2]) if rest else (top,)
    return (top, rest[0]) if rest else (top,)


def catalogue(game):
    """-> [{actor, model, category}] for every ActorInfo actor that resolves to a model file."""
    out = []
    for row in game.byml('RSDB/ActorInfo.Product.111.rstbl.byml.zs'):
        actor = row['__RowId']
        model = game.actor_model(actor)
        if model:
            out.append(dict(actor=actor, model=model, category='/'.join(category(actor))))
    return sorted(out, key=lambda e: (e['category'], e['actor']))


def template_index(game):
    """-> {actor: {'layer', 'banc', 'placement'}}: the first placement of each actor in the maps (Links dropped)."""
    out = {}
    for banc, folder in list_maps(game):
        if not folder: continue
        for layer in ('Static', 'Dynamic'):
            rel = f'Banc/{folder}/{banc}_{layer}.bcett.byml.zs'
            if not game.exists(rel): continue
            for a in game.byml(rel).get('Actors', []):
                name = a.get('Gyaml')
                if name and name not in out:
                    p = copy.deepcopy(a)
                    p.pop('Links', None)
                    out[name] = dict(layer=layer, banc=banc, placement=p)
    return out


def _jsonable(x):
    if isinstance(x, dict): return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [_jsonable(v) for v in x]
    if isinstance(x, (bytes, bytearray)): return {'__bytes__': bytes(x).hex()}
    if isinstance(x, int) and type(x).__name__ in ('U32', 'U64', 'I64'): return {'__' + type(x).__name__: int(x)}
    return x


def _from_json(x):
    from ..vendor.byml import U32, U64, I64
    if isinstance(x, dict):
        if len(x) == 1:
            k, v = next(iter(x.items()))
            if k == '__bytes__': return bytes.fromhex(v)
            if k in ('__U32', '__U64', '__I64'): return {'__U32': U32, '__U64': U64, '__I64': I64}[k](v)
        return {k: _from_json(v) for k, v in x.items()}
    if isinstance(x, list): return [_from_json(v) for v in x]
    return x


def save_index(game, folder=None):
    """Writes catalogue.json and templates.json into the library folder. -> (catalogue, templates)."""
    folder = folder or library_dir()
    os.makedirs(folder, exist_ok=True)
    cat, tpl = catalogue(game), template_index(game)
    with open(os.path.join(folder, 'catalogue.json'), 'w') as f: json.dump(cat, f, indent=0)
    with open(os.path.join(folder, 'templates.json'), 'w') as f: json.dump(_jsonable(tpl), f)
    return cat, tpl


def load_templates(folder=None):
    with open(os.path.join(folder or library_dir(), 'templates.json')) as f:
        return _from_json(json.load(f))
