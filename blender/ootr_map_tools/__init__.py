"""OoT Remake Map Tools: Blender add-on entry (preferences, the import operator, the menu).
The file readers live in core/ and need no bpy; this layer turns their results into Blender data (scene.py)."""
import os

try:
    import bpy
    from bpy.props import EnumProperty, StringProperty, BoolProperty
except ImportError:          # (core/ is usable without Blender)
    bpy = None

if bpy is not None:
    _detected = None

    def detected_folders():
        global _detected
        if _detected is None:
            from .core.game import find_game_folders
            _detected = find_game_folders()
        return _detected

    def _folder_changed(self, context):
        global _map_items
        _map_items = []

    class OOTR_Preferences(bpy.types.AddonPreferences):
        bl_idname = __package__
        game_folder: StringProperty(name='Game folder', subtype='DIR_PATH', default='', update=_folder_changed,
                                    description='Your Longshot or Hookshot game folder (the one holding romfs/ and mods/)')

        def draw(self, context):
            draw_game_folder(self.layout, context, self)

    def prefs(context):
        return context.preferences.addons[__package__].preferences

    def draw_game_folder(layout, context, p=None):
        from .core.game import is_game_folder
        p = p or prefs(context)
        layout.prop(p, 'game_folder')
        if is_game_folder(p.game_folder):
            return True
        box = layout.box()
        box.label(text='Choose your game folder (the Longshot / Hookshot folder holding romfs and mods)' if not p.game_folder
                  else 'This folder has no romfs: choose the game folder itself', icon='ERROR')
        for d in detected_folders()[:5]:
            box.operator('ootr.use_game_folder', text=f'Use {d}', icon='FILE_FOLDER').folder = d
        return False

    def game_folder(context):
        """The chosen game folder; raises a readable error when none is set."""
        from .core.game import is_game_folder
        f = prefs(context).game_folder
        if not is_game_folder(f):
            raise RuntimeError('Choose your game folder first (OoT sidebar tab, or Edit > Preferences > Add-ons > '
                               'OoT Remake Map Tools)')
        return f

    class OOTR_OT_use_game_folder(bpy.types.Operator):
        """Use this folder as the game folder"""
        bl_idname = 'ootr.use_game_folder'
        bl_label = 'Use Game Folder'
        folder: StringProperty()

        def execute(self, context):
            prefs(context).game_folder = self.folder
            bpy.ops.wm.save_userpref() if context.preferences.use_preferences_save else None
            self.report({'INFO'}, f'Game folder: {self.folder}')
            return {'FINISHED'}

    class OOTR_OT_choose_game_folder(bpy.types.Operator):
        """Browse for the game folder (the Longshot / Hookshot folder holding romfs and mods)"""
        bl_idname = 'ootr.choose_game_folder'
        bl_label = 'Choose Game Folder'
        directory: StringProperty(subtype='DIR_PATH')

        def invoke(self, context, event):
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}

        def execute(self, context):
            from .core.game import is_game_folder
            d = self.directory.rstrip('/\\')
            if not is_game_folder(d):
                self.report({'ERROR'}, f'{d} has no romfs folder: choose the game folder itself')
                return {'CANCELLED'}
            prefs(context).game_folder = d
            bpy.ops.wm.save_userpref() if context.preferences.use_preferences_save else None
            self.report({'INFO'}, f'Game folder: {d}')
            return {'FINISHED'}

    _map_items = []

    def map_items(self, context):
        global _map_items
        if not _map_items:
            try:
                from .core.game import Game
                from .core.banc import list_maps
                g = Game(game_folder(context))
                _map_items = [(name, name, f'{name} ({folder})') for name, folder in list_maps(g) if folder]
            except Exception as e:
                return [('NONE', f'({e})', '')]
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
                self.report({'ERROR'}, 'Choose your game folder first (OoT sidebar tab)')
                return {'CANCELLED'}
            from . import scene
            from .core.game import Game
            stats = scene.import_map(context, Game(game_folder(context)), self.banc, models=self.models, collision=self.collision,
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
            game = Game(game_folder(context))
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
            game = Game(game_folder(context))
            banc = root['ootr_banc']
            lines = write_mod(game, banc, scene.gather_edits(root), os.path.join(game.folder, 'mods', self.mod_name),
                              root.get('ootr_folder') or None)
            for line in lines: print('[ootr play]', line)
            pos = tuple(scene.GAME_TO_BLENDER.inverted() @ context.scene.cursor.location)
            profile, n, _proc = play(game.folder, banc, pos)
            self.report({'INFO'}, f'{banc}: starting the game at {tuple(round(v, 1) for v in pos)} '
                                  f'({n} saves of the test profile set; press Continue at the title)')
            return {'FINISHED'}

    _build_proc = None

    class OOTR_OT_build_library(bpy.types.Operator):
        """Build the asset library from the game files: every actor with a model as a draggable asset (runs in the
        background; about 15 minutes the first time; later builds only add missing groups)"""
        bl_idname = 'ootr.build_library'
        bl_label = 'Build Asset Library'

        def execute(self, context):
            import subprocess
            global _build_proc
            from .core.library import library_dir
            if _build_proc is not None and _build_proc.poll() is None:
                self.report({'WARNING'}, 'The asset library is already being built (progress in build.log)')
                return {'CANCELLED'}
            os.makedirs(library_dir(), exist_ok=True)
            here = os.path.dirname(os.path.abspath(__file__))
            cmd = [bpy.app.binary_path, '-b', '--factory-startup', '--gpu-backend', 'opengl', '--python',
                   os.path.join(here, 'library_build_cli.py'), '--', game_folder(context)]
            _build_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            from .core.game import is_game_folder
            if not is_game_folder(prefs(context).game_folder):
                draw_game_folder(self.layout, context)
                self.layout.operator('ootr.choose_game_folder', icon='FILEBROWSER')
                return
            col = self.layout.column(align=True)
            col.operator('ootr.import_map', icon='IMPORT')
            col.operator('ootr.export_map', icon='EXPORT')
            col.separator()
            col.operator('ootr.build_library', icon='ASSET_MANAGER')
            col.separator()
            col.operator('ootr.play', icon='PLAY')
            self.layout.label(text='Play starts at the 3D cursor')
            row = self.layout.row()
            row.label(text=os.path.basename(prefs(context).game_folder.rstrip('/\\')), icon='FILE_FOLDER')
            row.operator('ootr.choose_game_folder', text='', icon='FILEBROWSER')

    def menu_export(self, context):
        self.layout.operator(OOTR_OT_export_map.bl_idname, text='OoT Remake Map Edits')

    def menu_import(self, context):
        self.layout.operator(OOTR_OT_import_map.bl_idname, text='OoT Remake Map')

    classes = (OOTR_Preferences, OOTR_OT_use_game_folder, OOTR_OT_choose_game_folder, OOTR_OT_import_map, OOTR_OT_export_map, OOTR_OT_play, OOTR_OT_build_library, OOTR_PT_panel)

    def register():
        for c in classes: bpy.utils.register_class(c)
        bpy.types.TOPBAR_MT_file_import.append(menu_import)
        bpy.types.TOPBAR_MT_file_export.append(menu_export)

    def unregister():
        bpy.types.TOPBAR_MT_file_import.remove(menu_import)
        bpy.types.TOPBAR_MT_file_export.remove(menu_export)
        for c in reversed(classes): bpy.utils.unregister_class(c)
