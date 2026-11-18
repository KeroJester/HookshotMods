"""Collision shapes (RESEARCH_NOTES.md §80e): Phive/Shape/Dcc/<name>__<layer>.Nin_Ounce_NVN2.bphsh.zs holding
Havok hknpMeshShapes (no bpy). layers_for(game, actor) -> {layer: (V (n,3) metres, game axes; T (m,3))}."""
import os
from functools import lru_cache

import numpy as np

from ..vendor.hktag import Tag
from ..vendor.phsh import read_mesh

DCC = 'Phive/Shape/Dcc'


class ShapeIndex:
    def __init__(self, game):
        self.game = game
        self.by_name = {}
        for f in os.listdir(game.path(DCC)):
            if '__' in f and f.endswith('.bphsh.zs'):
                name, rest = f.split('__', 1)
                self.by_name.setdefault(name, []).append((rest.split('.')[0], f'{DCC}/{f}'))

    def layers(self, name):
        return self.by_name.get(name, [])


def read_shape(data: bytes):
    """A .bphsh -> (V, T) of all its hknpMeshShapes."""
    t = Tag(data)
    Vs, Ts, base = [], [], 0
    for it in t.items:
        if t.types[it['type']]['name'] == 'hknpMeshShape' if it['type'] else False:
            for k in range(it['count']):
                V, T = read_mesh(data, t.data[0] + it['off'] + 0xa0 * k)
                if len(T):
                    Vs.append(V); Ts.append(T + base); base += len(V)
    if not Vs: return np.zeros((0, 3)), np.zeros((0, 3), int)
    return np.concatenate(Vs), np.concatenate(Ts)


@lru_cache(maxsize=2048)
def _cached(game, rel):
    return read_shape(game.read(rel))


def layers_for(game, index, actor):
    out = {}
    for layer, rel in index.layers(actor):
        try:
            V, T = _cached(game, rel)
        except Exception:
            continue
        if len(T): out[layer] = (V, T)
    return out
