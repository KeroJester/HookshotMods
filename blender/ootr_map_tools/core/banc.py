"""Maps ("bancs", RESEARCH_NOTES.md §80b): the list of maps and a map's placed actors (no bpy)."""
import math
import os
from dataclasses import dataclass, field

from .game import Game


@dataclass
class Placement:
    actor: str                   # Gyaml (actor name)
    hash: int                    # placement ID
    layer: str                   # 'Static' or 'Dynamic'
    translate: tuple = (0.0, 0.0, 0.0)
    rotate: tuple = (0.0, 0.0, 0.0)      # radians, game axes
    scale: tuple = (1.0, 1.0, 1.0)
    model: str = None            # Model/<project>.<fmdb>.bfres.zs or None (no model: logic / sound / area actors)
    raw: dict = field(default_factory=dict, repr=False)   # the whole placement entry (kept for export)


def list_maps(game: Game):
    """-> [(banc name, folder)] from RSDB/GameBancInfo (the folder holding its placement files)."""
    rows = game.byml('RSDB/GameBancInfo.Product.111.rstbl.byml.zs')
    out = []
    for r in rows:
        name = r['__RowId']
        out.append((name, find_folder(game, name)))
    return sorted(out)


def find_folder(game: Game, banc: str):
    for folder in sorted(os.listdir(game.path('Banc'))):
        if os.path.exists(game.path(f'Banc/{folder}/{banc}_Static.bcett.byml.zs')):
            return folder
    return None


def load_map(game: Game, banc: str, folder: str = None):
    """-> [Placement] of the map's static and dynamic placement files."""
    folder = folder or find_folder(game, banc)
    if folder is None:
        raise FileNotFoundError(f'no placement files for {banc}')
    out = []
    for layer in ('Static', 'Dynamic'):
        rel = f'Banc/{folder}/{banc}_{layer}.bcett.byml.zs'
        if not game.exists(rel):
            continue
        for a in game.byml(rel).get('Actors', []):
            name = a.get('Gyaml', '')
            out.append(Placement(actor=name, hash=int(a.get('Hash', 0)), layer=layer,
                                 translate=tuple(float(v) for v in a.get('Translate', (0, 0, 0))),
                                 rotate=tuple(float(v) for v in a.get('Rotate', (0, 0, 0))),
                                 scale=_scale(a.get('Scale')), model=game.actor_model(name), raw=a))
    return out


def _scale(s):
    if s is None: return (1.0, 1.0, 1.0)
    if isinstance(s, (int, float)): return (float(s),) * 3
    return tuple(float(v) for v in s)


if __name__ == '__main__':                 # python -m ootr_map_tools.core.banc <game folder> [banc]
    import sys
    g = Game(sys.argv[1])
    if len(sys.argv) < 3:
        for name, folder in list_maps(g): print(f'{name:40s} {folder}')
    else:
        ps = load_map(g, sys.argv[2])
        with_model = [p for p in ps if p.model]
        print(f'{len(ps)} placements, {len(with_model)} with a model, {len({p.model for p in with_model})} distinct models')
        for p in with_model[:15]:
            print(f'  {p.layer:7s} {p.actor:40s} {p.model}  at {tuple(round(v, 2) for v in p.translate)}')
        missing = sorted({p.actor for p in ps if not p.model and p.actor.startswith(('Land_', 'Obj_'))})
        print('Land_/Obj_ actors without a model file:', missing[:20])
