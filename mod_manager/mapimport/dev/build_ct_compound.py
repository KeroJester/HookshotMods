# build_ct_compound.py: Clock Town's collision (MM3D's own collision mesh) into the SageRoom compound template -> ct.bphsc
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from mod_manager.mapimport import mm3d_room, bphsc
MM = os.path.expanduser('~/Games/Hookshot/mods/_MajoraAssets/extracted/mm3d_romfs/scenes/')
V, T, types = mm3d_room.scene_collision(open(MM + 'z2_clocktower_info.zsi', 'rb').read())
here = os.path.join(os.path.dirname(__file__), '..')
d = open(os.path.join(here, 'sage.bphsc'), 'rb').read()
o, s = bphsc.layer_tagfiles(d)[0]
out = bphsc.replace_layer(d, 0, bphsc.rebuild_layer2(d[o:o + s], {0: (V, T)}))
open(os.path.join(here, 'ct.bphsc'), 'wb').write(out); open(os.path.join(here, 'ct.bphsc.mine'), 'wb').write(out)
print('collision', len(V), 'verts', len(T), 'tris ->', len(out), 'bytes')
