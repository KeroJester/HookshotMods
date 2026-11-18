"""Map edits -> placement files (no bpy). The original files are re-read from the game, so every field that is not
edited (links, dynamic parameters, number types) is written back as it was.

edits: [Edit] - one per object in the scene that came from (or was copied from) a placement:
  hash  the placement it came from (a copy keeps its source's hash: the second and later users get a new hash),
  layer 'Static' / 'Dynamic', translate / rotate (radians, euler XYZ) / scale in game axes.
Placements of the original files without an edit are deleted.
RESEARCH_NOTES.md §83."""
import copy
import os
import math
import random
from dataclasses import dataclass

from ..vendor.byml import write_byml, U64
from .banc import find_folder


@dataclass
class Edit:
    hash: int
    layer: str
    translate: tuple
    rotate: tuple
    scale: tuple
    actor: str = None          # (for new placements: the Gyaml; copies take their source's)


def _close(a, b, eps=1e-4):
    return all(abs(float(x) - float(y)) <= eps for x, y in zip(a, b))


def build_layers(game, banc, edits, folder=None, seed=None):
    """-> ({layer: placement file bytes}, report lines)."""
    folder = folder or find_folder(game, banc)
    rng = random.Random(seed)
    report = []
    out = {}
    used = set()
    originals = {}
    for layer in ('Static', 'Dynamic'):
        rel = f'Banc/{folder}/{banc}_{layer}.bcett.byml.zs'
        if game.exists(rel):
            originals[layer] = (rel, game.byml(rel))
    all_hashes = {int(a['Hash']) for _, doc in originals.values() for a in doc.get('Actors', [])}
    compound = registered_ids(game, banc, folder)
    def new_hash():
        while True:
            h = rng.getrandbits(64) | 1
            if h not in all_hashes and h not in used: return h
    for layer, (rel, doc) in originals.items():
        by_hash = {int(a['Hash']): a for a in doc.get('Actors', [])}
        actors = []
        moved = added = 0
        for e in [e for e in edits if e.layer == layer]:
            src = by_hash.get(e.hash)
            if src is None:
                report.append(f'{layer}: no placement {e.hash} in the original file (skipped)')
                continue
            a = copy.deepcopy(src)
            copied = e.hash in used
            if copied:                               # (a copy: new identity)
                h = new_hash()
                a['Hash'] = U64(h)
                if 'Phive' in a and 'Placement' in a['Phive']:
                    a['Phive']['Placement']['ID'] = U64(new_hash())
                a.pop('Links', None)
                added += 1
                if 'Phive' in a:
                    report.append(f'copy of {a.get("Gyaml")}: shown, but without collision (the compound body is not extended yet)')
            used.add(int(a['Hash']))
            changed = not (_close(e.translate, src.get('Translate', (0, 0, 0))) and _close(e.rotate, src.get('Rotate', (0, 0, 0)))
                           and _close(e.scale, _scale(src.get('Scale'))))
            if changed:
                moved += 1
            pid = (src.get('Phive') or {}).get('Placement', {}).get('ID')
            is_registered = pid is not None and int(pid) in compound and not copied
            # SRTHash is checked against the transform for actors the compound body does not register (a moved one
            # with its old hash breaks the whole room); registered ones are checked against the body's record, which
            # keeps the old value (§83). So: dropped for moved unregistered actors and every copy.
            if (changed and not is_registered) or copied:
                a.pop('SRTHash', None)
            if changed and is_registered and 'Phive' in src:
                report.append(f'{a.get("Gyaml")} moved: shown at the new place; its collision stays in the compound body')
            if any(abs(v) > 1e-7 for v in e.translate) or 'Translate' in src: a['Translate'] = [float(v) for v in e.translate]
            if any(abs(v) > 1e-7 for v in e.rotate) or 'Rotate' in src: a['Rotate'] = [float(v) for v in e.rotate]
            if not _close(e.scale, (1, 1, 1)) or 'Scale' in src: a['Scale'] = [float(v) for v in e.scale]
            actors.append(a)
        deleted = len(by_hash) - len({e.hash for e in edits if e.layer == layer and e.hash in by_hash})
        new_doc = dict(doc)
        if actors or 'Actors' in doc: new_doc['Actors'] = actors
        out[layer] = (rel, write_byml(new_doc))
        report.append(f'{layer}: {len(actors)} placements ({moved} moved, {added} added, {deleted} deleted)')
    return out, report


def _scale(s):
    if s is None: return (1.0, 1.0, 1.0)
    if isinstance(s, (int, float)): return (float(s),) * 3
    return tuple(float(v) for v in s)


def write_mod(game, banc, edits, mod_dir, folder=None):
    """Writes the map's placement files into <mod_dir>/romfs (compressed as the game's). -> report lines."""
    import os
    from ..vendor.zstd_helper import compress_zs
    layers, report = build_layers(game, banc, edits, folder)
    for layer, (rel, data) in layers.items():
        p = os.path.join(mod_dir, 'romfs', rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(compress_zs(data, rel, game.romfs))
        report.append(f'wrote {rel} ({len(data)} bytes)')
    return report


def registered_ids(game, banc, folder):
    """Phive placement IDs present in the map's static compound body (RESEARCH_NOTES.md §80i, §83)."""
    import struct
    rel = f'Phive/StaticCompoundBody/{folder}/{banc}.Nin_Ounce_NVN2.bphsc.zs'
    if not game.exists(rel):
        return set()
    d = game.read(rel)
    # (table C: 0x20-byte records {u32, u32, u64 placement ID, u32 hash, 12 x 0}; a byte search is enough here)
    ids = set()
    from ..vendor.byml import parse_byml   # noqa (keeps the vendor import order)
    for layer in ('Static', 'Dynamic'):
        prel = f'Banc/{folder}/{banc}_{layer}.bcett.byml.zs'
        if not game.exists(prel): continue
        for a in game.byml(prel).get('Actors', []):
            pid = (a.get('Phive') or {}).get('Placement', {}).get('ID')
            if pid is not None and struct.pack('<Q', int(pid)) in d:
                ids.add(int(pid))
    return ids
