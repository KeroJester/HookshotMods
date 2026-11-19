"""Import MM3D Scene (RESEARCH_NOTES.md §95): a Majora's Mask 3D scene from the player's own copy into Blender, as
textured room meshes (visible in game, not solid) and the scene's own collision (solid, invisible), placed at the 3D
cursor in metres. They then export like any custom geometry."""
import bpy
import numpy as np
from bpy.props import EnumProperty, BoolProperty

from .scene import GAME_TO_BLENDER

_scene_items = []


def _prefs(context):
    from . import prefs
    return prefs(context)


def _scenes(self, context):
    global _scene_items
    if not _scene_items:
        try:
            from .core.mm3d import scenes_dir, list_scenes
            d = scenes_dir(_prefs(context).mm3d_source)
            _scene_items = [(s, f'{n} ({k} room{"s" if k != 1 else ""})' if n != s else f'{s} ({k})', s)
                            for s, n, k in list_scenes(d)]
        except Exception as ex:
            _scene_items = [('', f'({ex})', '')]
    return _scene_items


def reset_scene_items(self=None, context=None):
    global _scene_items
    _scene_items = []


def _image(name, rgba):
    h, w = rgba.shape[:2]
    im = bpy.data.images.new(name, w, h, alpha=True)
    im.pixels.foreach_set((rgba[::-1].astype(np.float32) / 255.0).reshape(-1))     # (Blender: bottom row first)
    im.pack()
    return im


def _material(name, image):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    tex = nt.nodes.new('ShaderNodeTexImage'); tex.image = image; tex.location = (-300, 300)
    nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    if 'Roughness' in bsdf.inputs: bsdf.inputs['Roughness'].default_value = 0.9
    return mat


def _to_blender(P, origin):
    M = np.array(GAME_TO_BLENDER.to_3x3())
    return P @ M.T + np.asarray(origin)


class OOTR_OT_import_mm3d(bpy.types.Operator):
    """Import a Majora's Mask 3D scene from your own copy (set in the add-on preferences): textured rooms and the
    scene's collision, at the 3D cursor"""
    bl_idname = 'ootr.import_mm3d'
    bl_label = 'Import MM3D Scene'
    bl_options = {'REGISTER', 'UNDO'}
    scene: EnumProperty(name='Scene', items=_scenes)
    collision: BoolProperty(name='Use MM3D\'s Collision', default=True,
                            description='Add the scene\'s own collision (solid, invisible) and make the rooms visual '
                                        'only; off: the rooms themselves are solid')

    def invoke(self, context, event):
        if not _prefs(context).mm3d_source:
            self.report({'ERROR'}, 'Choose your Majora\'s Mask 3D copy in the add-on preferences first')
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        from .core.mm3d import scenes_dir, load_scene, NAMES
        if not self.scene:
            self.report({'ERROR'}, 'No scene chosen'); return {'CANCELLED'}
        msgs = []
        d = scenes_dir(_prefs(context).mm3d_source, log=lambda *a: None)
        data = load_scene(d, self.scene, log=msgs.append)
        origin = context.scene.cursor.location.copy()
        title = NAMES.get(self.scene, self.scene)
        coll = bpy.data.collections.new(f'MM3D {title}')
        context.scene.collection.children.link(coll)
        nobj = ntri = 0
        for room in data['rooms']:
            imgs = {k: _image(f'{room["name"]}_t{k:02d}', v) for k, v in room['textures'].items()}
            mats, slot = [], {}
            P, F, UV, N, C, FM = [], [], [], [], [], []
            base = 0
            for m in room['meshes']:
                if m['texture'] not in slot:
                    slot[m['texture']] = len(mats)
                    mats.append(_material(f'{room["name"]}_m{m["texture"]:02d}', imgs[m['texture']]))
                T = np.asarray(m['tris'])
                P.append(m['pos']); F.append(T + base); base += len(m['pos'])
                UV.append(m['uv'][T].reshape(-1, 2)); N.append(m['nrm'][T].reshape(-1, 3))
                col = m['col'] if m['col'].shape[1] == 4 else np.c_[m['col'], np.ones(len(m['col']))]
                C.append(col[T].reshape(-1, 4)); FM.append(np.full(len(T), slot[m['texture']]))
            if not P: continue
            P, F = np.concatenate(P), np.concatenate(F)
            me = bpy.data.meshes.new(room['name'])
            me.from_pydata(_to_blender(P, origin).tolist(), [], F.tolist())
            for mat in mats: me.materials.append(mat)
            me.polygons.foreach_set('material_index', np.concatenate(FM).astype(np.int32))
            uv = np.concatenate(UV).astype(np.float32); uv[:, 1] = 1.0 - uv[:, 1]      # (MM3D's V runs down)
            me.uv_layers.new(name='UVMap').data.foreach_set('uv', uv.reshape(-1))
            ca = me.color_attributes.new('MM3D Vertex Colour', 'FLOAT_COLOR', 'CORNER')
            ca.data.foreach_set('color', np.concatenate(C).astype(np.float32).reshape(-1))
            Nb = np.concatenate(N) @ np.array(GAME_TO_BLENDER.to_3x3()).T
            me.update()
            me.normals_split_custom_set(Nb.tolist())
            ob = bpy.data.objects.new(room['name'], me)
            ob.ootr_solid = not (self.collision and data['collision'] is not None)
            ob.ootr_game_visible = True
            coll.objects.link(ob)
            nobj += 1; ntri += len(F)
        if self.collision and data['collision'] is not None:
            V, T = data['collision']
            me = bpy.data.meshes.new(f'{self.scene}_collision')
            me.from_pydata(_to_blender(V, origin).tolist(), [], np.asarray(T).tolist())
            me.update()
            ob = bpy.data.objects.new(f'{title} Collision', me)
            ob.ootr_solid, ob.ootr_game_visible = True, False
            ob.display_type = 'WIRE'; ob.hide_render = True
            coll.objects.link(ob)
        for m in msgs: self.report({'WARNING'}, m)
        self.report({'INFO'}, f'{title}: {nobj} room(s), {ntri} triangles'
                    + (f', collision {len(data["collision"][1])} triangles' if data['collision'] is not None else ''))
        return {'FINISHED'}


def menu_func(self, context):
    self.layout.operator(OOTR_OT_import_mm3d.bl_idname, text='Majora\'s Mask 3D Scene')


classes = (OOTR_OT_import_mm3d,)


def register():
    for c in classes: bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_func)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func)
    for c in reversed(classes): bpy.utils.unregister_class(c)
