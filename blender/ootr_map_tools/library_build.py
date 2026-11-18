"""Builds the asset library (run in a background Blender: library_build_cli.py). One .blend per category group
(top category + first sub-category, e.g. "Map pieces - Kakariko"), each actor an asset object: its textured model,
ootr_actor / ootr_layer properties (export turns a dropped asset into a new placement of that actor), a catalogue entry
and a preview. RESEARCH_NOTES.md §85."""
import os
import time
import uuid

import bpy

from .core.library import library_dir, save_index
from .models import model_mesh
from .scene import GAME_TO_BLENDER

NS = uuid.UUID('6f6f7472-6d61-7074-6f6f-6c7300000000')      # (stable catalogue ids: uuid5 of the path)


def catalog_id(path):
    return str(uuid.uuid5(NS, path))


def write_catalogs(folder, paths):
    """blender_assets.cats.txt with every category path (and its parents)."""
    allp = set()
    for p in paths:
        parts = p.split('/')
        for i in range(1, len(parts) + 1): allp.add('/'.join(parts[:i]))
    with open(os.path.join(folder, 'blender_assets.cats.txt'), 'w') as f:
        f.write('# OoT Remake asset library (generated from the game files by OoT Remake Map Tools)\nVERSION 1\n\n')
        for p in sorted(allp):
            f.write(f'{catalog_id(p)}:{p}:{p.replace("/", "-")}\n')


def group_of(category):
    parts = category.split('/')
    return ' - '.join(parts[:2])


def build(game, only=None, log=print, previews=True, rebuild=False):
    """only: a set of group names to (re)build, or None for all (groups already built are skipped unless rebuild).
    Writes <library>/<group>.blend files."""
    bpy.context.preferences.filepaths.save_version = 0      # (no .blend1 backups next to the library files)
    folder = library_dir()
    cat, tpl = save_index(game, folder)
    write_catalogs(folder, [e['category'] for e in cat])
    groups = {}
    for e in cat: groups.setdefault(group_of(e['category']), []).append(e)
    def built(g):
        return os.path.exists(os.path.join(folder, f'{g.replace("/", "-")}.blend'))
    names = sorted(g for g in groups if ((g in only) if only is not None else (rebuild or not built(g))))
    log(f'{len(cat)} actors in {len(groups)} groups; building {len(names)}')
    t0 = time.time()
    for gi, name in enumerate(names):
        bpy.ops.wm.read_homefile(use_empty=True)
        n = 0
        for e in groups[name]:
            try:
                me = model_mesh(game, e['model'])
            except Exception as ex:
                log(f'  {e["actor"]}: {ex}'); continue
            if me is None: continue
            ob = bpy.data.objects.new(e['actor'], me)
            bpy.context.scene.collection.objects.link(ob)
            me.transform(GAME_TO_BLENDER)               # (mesh in Blender axes: upright with no object rotation, which
            me['ootr_blender_axes'] = True              #  Blender's asset drop resets; scene.game_trs accounts for it)
            ob['ootr_actor'] = e['actor']
            ob['ootr_layer'] = tpl.get(e['actor'], {}).get('layer', 'Dynamic')
            ob['ootr_new'] = True               # (export: a new placement cloned from the actor's template)
            ob.asset_mark()
            ob.asset_data.catalog_id = catalog_id(e['category'])
            ob.asset_data.description = e['actor']
            ob.asset_data.tags.new(e['actor'].split('_')[0])
            if previews:
                try: ob.asset_generate_preview()
                except Exception: pass
            n += 1
        if previews:                            # (previews render asynchronously: give them a moment)
            end = time.time() + 2 + 0.05 * n
            while time.time() < end and any(o.preview is None or o.preview.image_size[0] == 0 for o in bpy.data.objects):
                time.sleep(0.1)
        path = os.path.join(folder, f'{name.replace("/", "-")}.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
        log(f'[{gi + 1}/{len(names)}] {name}: {n} assets ({time.time() - t0:.0f}s)')
    return folder
