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
    # Every placement of the game has an SRTHash (its function is unknown, §83). The Dynamic layer loads placements
    # without one; the Static layer does not: one placement without an SRTHash and the whole layer is missing in game
    # (§86). So new placements, copies and moved unregistered actors of the Static layer are written to Dynamic.
    result = {layer: [] for layer in originals}
    stats = {layer: [0, 0, 0] for layer in originals}       # moved, added, moved to Dynamic
    def place(layer, a):
        if layer == 'Static' and 'SRTHash' not in a and 'Dynamic' in result:
            result['Dynamic'].append(a); stats['Static'][2] += 1
        else:
            result[layer].append(a)
    for layer, (rel, doc) in originals.items():
        by_hash = {int(a['Hash']): a for a in doc.get('Actors', [])}
        moved = added = 0
        for e in [e for e in edits if e.layer == layer]:
            if e.hash == 0:                          # (new: an asset dropped from the library)
                a = new_placement(game, e.actor)
                a['Hash'] = U64(new_hash())
                a.setdefault('Phive', {}).setdefault('Placement', {})['ID'] = U64(int(a['Hash']))   # (ID == Hash, always present)
                used.add(int(a['Hash']))
                for k, v, default in (('Translate', e.translate, (0, 0, 0)), ('Rotate', e.rotate, (0, 0, 0)), ('Scale', e.scale, (1, 1, 1))):
                    if not _close(v, default): a[k] = [float(x) for x in v]
                    else: a.pop(k, None)
                place(layer, a)
                added += 1
                continue
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
                    a['Phive']['Placement']['ID'] = U64(h)                  # (the game's placements: ID == Hash)
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
            place(layer, a)
        deleted = len(by_hash) - len({e.hash for e in edits if e.layer == layer and e.hash in by_hash})
        stats[layer][:2] = [moved, added]
        stats[layer].append(deleted)
    for layer, (rel, doc) in originals.items():
        actors = result[layer]
        new_doc = dict(doc)
        if actors or 'Actors' in doc: new_doc['Actors'] = actors
        out[layer] = (rel, write_byml(new_doc))
        moved, added, to_dyn, deleted = stats[layer]
        extra = f'; {to_dyn} written to Dynamic (no SRTHash)' if to_dyn else ''
        report.append(f'{layer}: {len(actors)} placements ({moved} moved, {added} added, {deleted} deleted{extra})')
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
    report += update_size_requests(game, mod_dir, {rel: data for rel, data in layers.values()})
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


_templates = {}


def new_placement(game, actor):
    """A new placement of an actor: a copy of its first placement in the game's maps (fields such as Dynamic
    parameters as the game uses them), without Links / SRTHash; a bare {Gyaml} for actors no map places."""
    if game.romfs not in _templates:
        from .library import template_index
        _templates[game.romfs] = template_index(game)
    t = _templates[game.romfs].get(actor)
    a = copy.deepcopy(t['placement']) if t else {'Gyaml': actor}
    for k in ('Links', 'SRTHash', 'Hash', 'Translate', 'Rotate', 'Scale', 'Presence'):   # (Presence: a flag condition)
        a.pop(k, None)
    a['Gyaml'] = actor
    return a


def update_size_requests(game, mod_dir, files):
    """files {romfs rel: decompressed bytes} just written into the mod -> its rstb.txt updated (requests of other files
    kept) and MajoraMod's saved size table raised (apply_to_majora_table). -> report lines."""
    req = size_requests(game, files)
    rp = os.path.join(mod_dir, 'rstb.txt')
    os.makedirs(mod_dir, exist_ok=True)
    mine = {rel[:-3] if rel.endswith('.zs') else rel for rel in files}
    keep = []
    if os.path.exists(rp):
        keep = [l.strip() for l in open(rp) if l.strip() and l.split('=')[0] not in mine]
    report = []
    if req or keep:
        with open(rp, 'w') as f: f.write('\n'.join(keep + req) + '\n')
        report += [f'size request: {l}' for l in req]
        if req: report += apply_to_majora_table(game, keep + req)
    elif os.path.exists(rp):
        os.remove(rp)
    return report


def size_requests(game, files, margin=8192):
    """{romfs rel (.zs): decompressed bytes} -> lines "path=size" for files over their size table entry."""
    import glob
    from ..vendor.rstb import ResourceSizeTable
    tabs = glob.glob(game.path('System/Resource/ResourceSizeTable.Product.*.rsizetable.zs'))
    if not tabs: return []
    t = ResourceSizeTable.load(tabs[0])
    out = []
    for rel, data in files.items():
        key = rel[:-3] if rel.endswith('.zs') else rel
        e = t.get(key)
        if e is not None and len(data) + 512 > e:        # (entries are the size + ~270 bytes the game needs: 512 to be safe)
            out.append(f'{key}={(len(data) + margin + 4095) // 4096 * 4096}')
    return out


TABLE = 'System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs'


def majora_dir(game):
    """The enabled MajoraMod folder in the game's mods folder (None if it is missing or disabled)."""
    import glob
    for d in sorted(glob.glob(os.path.join(game.folder, 'mods', '*'))):
        n = os.path.basename(d)
        if os.path.isdir(d) and n[:1] not in '_-' and n.endswith('MajoraMod'):
            return d
    return None


def apply_to_majora_table(game, lines):
    """MajoraMod raises the size table from the mods' rstb.txt when the game starts, but Longshot reads the table
    before MajoraMod runs: it uses MajoraMod's saved copy (<MajoraMod>/romfs/<table>) from the previous launch. Raising
    the same entries in that copy now makes the first launch after an export work (MajoraMod writes the same sizes
    again when it starts). Entries are only ever raised. -> report lines."""
    from ..vendor.rstb import ResourceSizeTable
    md = majora_dir(game)
    if md is None:
        return ['MajoraMod is not installed or is disabled: the grown map files will not load (the game would show '
                'an empty map); install MajoraMod 0.4.1 or later']
    dst = os.path.join(md, 'romfs', TABLE)
    src = dst if os.path.exists(dst) else next((p for p in (os.path.join(game.folder, 'prompts', 'rsizetable.zs'),
                                                            game.path(TABLE)) if os.path.exists(p)), None)
    if src is None:
        return ['the size table was not found: the first launch may show an empty map']
    t = ResourceSizeTable.load(src)
    changed = []
    for l in lines:
        k, _, v = l.partition('=')
        if not v.strip().isdigit(): continue
        cur = t.get(k)
        if cur is not None and cur < int(v):
            t.set(k, int(v)); changed.append(k)
    if changed:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst + '.tmp', 'wb') as f: f.write(t.to_zs())
        os.replace(dst + '.tmp', dst)
        return [f'size table raised in {os.path.basename(md)} for {len(changed)} file(s)']
    return []
