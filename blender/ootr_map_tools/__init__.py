"""OoT Remake Map Tools: Blender add-on entry (preferences, the import operator, the menu).
The file readers live in core/ and need no bpy; this layer turns their results into Blender data (scene.py)."""
import os

try:
    import bpy
    from bpy.props import EnumProperty, StringProperty, BoolProperty
except ImportError:          # (core/ is usable without Blender)
    bpy = None

DEFAULT_GAME = os.path.expanduser('~/Games/Longshot/nightly-80-7315957c')

if bpy is not None:
    class OOTR_Preferences(bpy.types.AddonPreferences):
        bl_idname = __package__
        game_folder: StringProperty(name='Game folder', subtype='DIR_PATH', default=DEFAULT_GAME,
                                    description='A Longshot or Hookshot folder (with romfs/), or an extracted romfs')

        def draw(self, context):
            self.layout.prop(self, 'game_folder')

    def prefs(context):
        return context.preferences.addons[__package__].preferences

    _map_items = []

    def map_items(self, context):
        global _map_items
        if not _map_items:
            try:
                from .core.game import Game
                from .core.banc import list_maps
                g = Game(prefs(context).game_folder)
                _map_items = [(name, name, f'{name} ({folder})') for name, folder in list_maps(g) if folder]
            except Exception as e:
                return [('NONE', f'(set the game folder in the preferences: {e})', '')]
        return _map_items

    class OOTR_OT_import_map(bpy.types.Operator):
        """Load an Ocarina of Time remake map: placed actors with their models"""
        bl_idname = 'ootr.import_map'
        bl_label = 'Import OoT Remake Map'
        bl_options = {'REGISTER', 'UNDO'}
        bl_property = 'banc'

        banc: EnumProperty(name='Map', items=map_items)
        models: BoolProperty(name='Models', default=True, description="Load the actors' models (else empties only)")
        collision: BoolProperty(name='Collision', default=True, description='Load collision shapes (wireframe, one collection per layer)')

        def invoke(self, context, event):
            context.window_manager.invoke_search_popup(self)
            return {'RUNNING_MODAL'}

        def execute(self, context):
            if self.banc == 'NONE':
                self.report({'ERROR'}, 'Set the game folder in the add-on preferences')
                return {'CANCELLED'}
            from . import scene
            from .core.game import Game
            stats = scene.import_map(context, Game(prefs(context).game_folder), self.banc, models=self.models, collision=self.collision,
                                     report=lambda m: self.report({'WARNING'}, m))
            self.report({'INFO'}, stats)
            return {'FINISHED'}

    def root_items(self, context):
        from . import scene
        return [(c.name, c['ootr_banc'], '') for c in scene.map_roots(context)] or [('NONE', '(import a map first)', '')]

    class OOTR_OT_export_map(bpy.types.Operator):
        """Write the map's placements (moved, copied and deleted objects) into a mod folder"""
        bl_idname = 'ootr.export_map'
        bl_label = 'Export OoT Remake Map Edits'
        root: EnumProperty(name='Map', items=root_items)
        mod_name: StringProperty(name='Mod folder', default='075-MapEdits',
                                 description='Folder under <game folder>/mods/ (names starting with _ are disabled)')

        def invoke(self, context, event):
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            if self.root == 'NONE':
                self.report({'ERROR'}, 'Import a map first'); return {'CANCELLED'}
            from . import scene
            from .core.game import Game
            from .core.export import write_mod
            root = bpy.data.collections[self.root]
            game = Game(prefs(context).game_folder)
            mod = os.path.join(game.folder, 'mods', self.mod_name)
            lines = write_mod(game, root['ootr_banc'], scene.gather_edits(root), mod, root.get('ootr_folder') or None)
            for line in lines: print('[ootr export]', line)
            warn = [l for l in lines if 'compound' in l]
            self.report({'WARNING'} if warn else {'INFO'}, '; '.join(l for l in lines if l.startswith(('Static:', 'Dynamic:'))) + (f' ({len(warn)} static warnings, see the console)' if warn else ''))
            return {'FINISHED'}

    def menu_export(self, context):
        self.layout.operator(OOTR_OT_export_map.bl_idname, text='OoT Remake Map Edits')

    def menu_import(self, context):
        self.layout.operator(OOTR_OT_import_map.bl_idname, text='OoT Remake Map')

    classes = (OOTR_Preferences, OOTR_OT_import_map, OOTR_OT_export_map)

    def register():
        for c in classes: bpy.utils.register_class(c)
        bpy.types.TOPBAR_MT_file_import.append(menu_import)
        bpy.types.TOPBAR_MT_file_export.append(menu_export)

    def unregister():
        bpy.types.TOPBAR_MT_file_import.remove(menu_import)
        bpy.types.TOPBAR_MT_file_export.remove(menu_export)
        for c in reversed(classes): bpy.utils.unregister_class(c)
