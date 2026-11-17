# Headless test: blender -b --factory-startup --python blender/tests/import_map.py -- <game folder> <banc> <out dir>
# Registers the add-on from this checkout, imports the map, saves a .blend and renders an overhead preview.
import math, os, sys, time
import bpy

argv = sys.argv[sys.argv.index('--') + 1:]
game, banc, out = argv[0], argv[1], argv[2]
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import ootr_map_tools as addon
from ootr_map_tools import scene
from ootr_map_tools.core.game import Game

for ob in list(bpy.data.objects): bpy.data.objects.remove(ob)
t0 = time.time()
print('IMPORT', scene.import_map(bpy.context, Game(game), banc, report=lambda m: print('WARN', m)))
obs = [o for o in bpy.data.objects if o.type == 'MESH']
print(f'OBJECTS {len(bpy.data.objects)} meshes-objects {len(obs)} mesh-data {len(bpy.data.meshes)} materials {len(bpy.data.materials)}')

# overhead preview (Workbench, material colours)
import mathutils
# frame on where most placements are (far backdrops would dominate the bounds); hide meshes larger than 600 m
locs = sorted(o.matrix_world.translation.copy() for o in bpy.data.objects)
med = mathutils.Vector([sorted(v[i] for v in locs)[len(locs) // 2] for i in range(3)])
big = []
for o in obs:
    w = [o.matrix_world @ mathutils.Vector(cc) for cc in o.bound_box]
    if max(max(p[i] for p in w) - min(p[i] for p in w) for i in range(3)) > 600:
        big.append(o.name); o.hide_render = True
print('HIDDEN (larger than 600 m):', big[:10], len(big))
size = 300.0
lo = med - mathutils.Vector((150, 150, 60)); hi = med + mathutils.Vector((150, 150, 80))
c = med
print(f'CENTRE {tuple(round(v, 1) for v in med)}')
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam'))
bpy.context.scene.collection.objects.link(cam)
cam.data.type = 'ORTHO'; cam.data.ortho_scale = size
cam.location = (c.x, c.y, hi.z + 50); cam.data.clip_end = 2000
bpy.context.scene.camera = cam
sc = bpy.context.scene
sc.render.engine = 'BLENDER_WORKBENCH'
sc.display.shading.light = 'STUDIO'; sc.display.shading.color_type = 'MATERIAL'
sc.render.resolution_x = sc.render.resolution_y = 1024
os.makedirs(out, exist_ok=True)
sc.render.filepath = os.path.join(out, f'{banc}_top.png')
bpy.ops.render.render(write_still=True)
# a perspective view from the start position side
cam2 = bpy.data.objects.new('cam2', bpy.data.cameras.new('cam2'))
sc.collection.objects.link(cam2)
cam2.location = (c.x, c.y - 120, c.z + 70)
cam2.rotation_euler = (math.radians(62), 0, 0); cam2.data.clip_end = 3000
sc.camera = cam2
sc.render.resolution_x, sc.render.resolution_y = 1280, 720
sc.render.filepath = os.path.join(out, f'{banc}_persp.png')
bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, f'{banc}.blend'))
print(f'DONE {time.time() - t0:.1f}s')
