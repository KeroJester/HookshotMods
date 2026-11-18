# Headless test: blender -b --factory-startup --python blender/tests/export_map.py -- <game folder> <banc> <out dir> [actor-to-move dx dy dz]
# Imports the map, (optionally moves every object of one actor by dx/dy/dz metres in Blender axes and copies the first),
# exports into <out dir>/mod and checks the result.
import os, sys
import bpy
argv = sys.argv[sys.argv.index('--') + 1:]
game_dir, banc, out = argv[:3]
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from ootr_map_tools import scene
from ootr_map_tools.core.game import Game
from ootr_map_tools.core.export import write_mod, build_layers
from ootr_map_tools.vendor.byml import parse_byml
for ob in list(bpy.data.objects): bpy.data.objects.remove(ob)
g = Game(game_dir)
print('IMPORT', scene.import_map(bpy.context, g, banc, models=False, collision=False))
root = [c for c in bpy.data.collections if c.get('ootr_banc') == banc][0]
if len(argv) > 3:
    actor, d = argv[3], [float(v) for v in argv[4:7]]
    obs = [o for o in bpy.data.objects if any(str(o.get('ootr_actor', '')).startswith(a) for a in actor.split(','))]
    for o in obs: o.location = (o.location.x + d[0], o.location.y + d[1], o.location.z + d[2])
    if os.environ.get('NO_COPY') != '1':
        cp = obs[0].copy(); obs[0].users_collection[0].objects.link(cp); cp.location.z += 1.0
    print('MOVED', len(obs), actor, '' if os.environ.get('NO_COPY') == '1' else 'and copied one')
edits = scene.gather_edits(root)
for line in write_mod(g, banc, edits, os.path.join(out, 'mod'), root['ootr_folder']): print('EXPORT', line)
if len(argv) <= 3:
    layers, _ = build_layers(g, banc, edits, root['ootr_folder'])
    for layer, (rel, data) in layers.items():
        print('ROUNDTRIP', layer, parse_byml(g.read(rel)) == parse_byml(data))
