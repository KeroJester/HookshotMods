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

    class OOTR_OT_play(bpy.types.Operator):
        """Export the map's edits, then start the game in this map at the 3D cursor (separate test profile)"""
        bl_idname = 'ootr.play'
        bl_label = 'Play in Game'
        root: EnumProperty(name='Map', items=root_items)
        mod_name: StringProperty(name='Mod folder', default='075-MapEdits')

        def invoke(self, context, event):
            from . import scene
            roots = scene.map_roots(context)
            if len(roots) > 1:
                return context.window_manager.invoke_props_dialog(self)
            return self.execute(context)

        def execute(self, context):
            from . import scene
            from .core.game import Game
            from .core.export import write_mod
            from .core.play import play
            roots = scene.map_roots(context)
            if not roots:
                self.report({'ERROR'}, 'Import a map first'); return {'CANCELLED'}
            root = bpy.data.collections[self.root] if self.root not in ('', 'NONE') and self.root in bpy.data.collections else roots[0]
            game = Game(prefs(context).game_folder)
            banc = root['ootr_banc']
            lines = write_mod(game, banc, scene.gather_edits(root), os.path.join(game.folder, 'mods', self.mod_name),
                              root.get('ootr_folder') or None)
            for line in lines: print('[ootr play]', line)
            pos = tuple(scene.GAME_TO_BLENDER.inverted() @ context.scene.cursor.location)
            profile, n, _proc = play(game.folder, banc, pos)
            self.report({'INFO'}, f'{banc}: starting the game at {tuple(round(v, 1) for v in pos)} '
                                  f'({n} saves of the test profile set; press Continue at the title)')
            return {'FINISHED'}

    class OOTR_OT_build_library(bpy.types.Operator):
        """Build the asset library from the game files: every actor with a model as a draggable asset (runs in the
        background; about 10 minutes the first time)"""
        bl_idname = 'ootr.build_library'
        bl_label = 'Build Asset Library'

        def execute(self, context):
            import subprocess
            from .core.library import library_dir
            os.makedirs(library_dir(), exist_ok=True)
            here = os.path.dirname(os.path.abspath(__file__))
            cmd = [bpy.app.binary_path, '-b', '--factory-startup', '--gpu-backend', 'opengl', '--python',
                   os.path.join(here, 'library_build_cli.py'), '--', prefs(context).game_folder]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            register_library(context)
            self.report({'INFO'}, f'Building the asset library in the background: {library_dir()} (progress in build.log)')
            return {'FINISHED'}

    def register_library(context):
        from .core.library import library_dir
        libs = context.preferences.filepaths.asset_libraries
        if not any(os.path.normpath(l.path) == os.path.normpath(library_dir()) for l in libs):
            lib = libs.new(name='OoT Remake', directory=library_dir())
            if hasattr(lib, 'import_method'): lib.import_method = 'APPEND'

    class OOTR_PT_panel(bpy.types.Panel):
        bl_label = 'OoT Remake'
        bl_space_type = 'VIEW_3D'
        bl_region_type = 'UI'
        bl_category = 'OoT'

        def draw(self, context):
            col = self.layout.column(align=True)
            col.operator('ootr.import_map', icon='IMPORT')
            col.operator('ootr.export_map', icon='EXPORT')
            col.separator()
            col.operator('ootr.build_library', icon='ASSET_MANAGER')
            col.separator()
            col.operator('ootr.play', icon='PLAY')
            self.layout.label(text='Play starts at the 3D cursor')

    def menu_export(self, context):
        self.layout.operator(OOTR_OT_export_map.bl_idname, text='OoT Remake Map Edits')

    def menu_import(self, context):
        self.layout.operator(OOTR_OT_import_map.bl_idname, text='OoT Remake Map')

    classes = (OOTR_Preferences, OOTR_OT_import_map, OOTR_OT_export_map, OOTR_OT_play, OOTR_OT_build_library, OOTR_PT_panel)

    def register():
        for c in classes: bpy.utils.register_class(c)
        bpy.types.TOPBAR_MT_file_import.append(menu_import)
        bpy.types.TOPBAR_MT_file_export.append(menu_export)

    def unregister():
        bpy.types.TOPBAR_MT_file_import.remove(menu_import)
        bpy.types.TOPBAR_MT_file_export.remove(menu_export)
        for c in reversed(classes): bpy.utils.unregister_class(c)
