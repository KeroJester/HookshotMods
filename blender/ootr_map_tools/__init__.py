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

        work_mod: StringProperty(name='Map mod folder', default='075-MapEdits', update=_folder_changed,
                                 description='The mod folder (under <game folder>/mods/) that exports, new maps and Play use; '
                                             'maps in it are read back as they are now')

        mm3d_source: StringProperty(name='Majora\'s Mask 3D copy', subtype='FILE_PATH', default='',
                                    update=lambda s, c: __import__(__package__ + '.mm3d_import', fromlist=['x']).reset_scene_items(),
                                    description='Your own Majora\'s Mask 3D: a decrypted .3ds, or its extracted RomFS folder '
                                                '(for Import MM3D Scene; nothing from it is shipped)')

        def draw(self, context):
            draw_game_folder(self.layout, context, self)
            self.layout.prop(self, 'work_mod')
            self.layout.prop(self, 'mm3d_source')

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

    def get_game(context):
        """The game folder with the map mod as an overlay (new maps and earlier edits are read as they are now)."""
        from .core.game import Game
        f = game_folder(context)
        work = os.path.join(f, 'mods', prefs(context).work_mod)
        # (the game's files as the game sees them: the map mod first, then every other enabled mod, last-loaded first)
        mods_dir = os.path.join(f, 'mods')
        others = []
        if os.path.isdir(mods_dir):
            names = sorted((n for n in os.listdir(mods_dir) if n[:1] not in '_-' and os.path.isdir(os.path.join(mods_dir, n))),
                           key=str.lower, reverse=True)
            others = [os.path.join(mods_dir, n) for n in names if os.path.join(mods_dir, n) != work]
        return Game(f, overlays=[work] + others)

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
                g = get_game(context)
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
        lights: BoolProperty(name='Lighting', default=True, description='Load the runtime lights, the baked lights (reference) and the environment')

        def invoke(self, context, event):
            context.window_manager.invoke_search_popup(self)
            return {'RUNNING_MODAL'}

        def execute(self, context):
            if self.banc == 'NONE':
                self.report({'ERROR'}, 'Choose your game folder first (OoT sidebar tab)')
                return {'CANCELLED'}
            from . import scene
            from .core.game import Game
            game = get_game(context)
            stats = scene.import_map(context, game, self.banc, models=self.models, collision=self.collision,
                                     report=lambda m: self.report({'WARNING'}, m))
            try:
                from . import mapsettings_ui
                mapsettings_ui.load_for(context, game, self.banc)
            except Exception as ex:
                self.report({'WARNING'}, f'map settings not loaded: {ex}')
            if self.lights:
                from . import lighting
                root = next(r for r in reversed(scene.map_roots(context)) if r['ootr_banc'] == self.banc)
                try:
                    stats += '; ' + lighting.import_lighting(context, game, self.banc, root)
                except Exception as ex:
                    self.report({'WARNING'}, f'lighting not loaded: {ex}')
            self.report({'INFO'}, stats)
            return {'FINISHED'}

    class watchdog:
        """Writes every thread's Python stack to ~/.cache/ootr_map_tools/hang.log when the work inside takes longer
        than `after` seconds (Blender is frozen meanwhile: this says where)."""
        def __init__(self, what, after=60):
            self.what, self.after = what, after

        def __enter__(self):
            import faulthandler
            from .core.play import profile_dir
            path = os.path.join(os.path.dirname(profile_dir()), 'hang.log')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.f = open(path, 'w')
            self.f.write(f'{self.what}: still running after {self.after} s; Python stacks:\n'); self.f.flush()
            faulthandler.dump_traceback_later(self.after, repeat=True, file=self.f)
            return self

        def __exit__(self, *exc):
            import faulthandler
            faulthandler.cancel_dump_traceback_later()
            self.f.close()
            return False

    def export_root(context, game, root, mod_name):
        with watchdog(f'export of {root.get("ootr_banc")}'):
            return _export_root(context, game, root, mod_name)

    def _export_root(context, game, root, mod_name):
        """Placements, custom geometry and lighting of one imported map -> the mod folder. -> report lines."""
        from . import scene, lighting, custom_geo
        from .core.export import write_mod, update_size_requests, Edit
        from .core.lights import write_lighting
        from .core.custom import write_custom, actor_name
        banc = root['ootr_banc']
        mod = os.path.join(game.folder, 'mods', mod_name)
        lines = []
        edits = scene.gather_edits(root)
        from . import sound_ui
        edits += sound_ui.gather_sound(context, root)        # (music / ambience zones, emitters)
        meshes, images, collision, maps = custom_geo.gather_custom(context, root)
        if meshes or collision is not None:
            rep, files, placements = write_custom(game, banc, mod, meshes, images, collision, maps)
            lines += rep + update_size_requests(game, mod, {k: v for k, v in files.items() if k.startswith(('RSDB/', 'Phive/', 'Model/', 'Tex/', 'Pack/'))})
            if placements:                                     # (the previous custom model tiles are replaced)
                edits = [e for e in edits if not (e.actor or '').startswith(f'Land_Ootr_{banc}_')]
            for pl in placements:
                edits.append(Edit(0, 'Dynamic', pl['translate'], (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), pl['actor']))
            import numpy as np
            pts = [m['pos'] for m in meshes] + ([np.asarray(collision[0])] if collision is not None and len(collision[0]) else [])
            if pts:                                            # (big maps: the physics world must hold them)
                from .core.mapsettings import ensure_world_box
                allp = np.concatenate(pts)
                lines += ensure_world_box(game, banc, mod, allp.min(0), allp.max(0))
        lines = write_mod(game, banc, edits, mod, root.get('ootr_folder') or None) + lines
        lights = lighting.gather_lights(context, root)
        env = lighting.gather_env(context, banc)
        if lights is not None or env is not None:
            rep, files = write_lighting(game, banc, mod, lights=lights, env_edits=env)
            lines += rep + update_size_requests(game, mod, files)
        m = context.scene.ootr_map
        if m.banc == banc and m.changes():                    # (Map Settings: after lighting, both write GameBancParam)
            from .core.mapsettings import write_settings
            ch = m.changes()
            if m.soundscape == 'Custom' and any(k in ch for k in ('soundscape', 'custom_music', 'custom_ambience')):
                ch['soundscape'] = ('Custom', m.custom_music, m.custom_ambience)
            ch.pop('custom_music', None); ch.pop('custom_ambience', None)
            files, rep = write_settings(game, banc, mod, ch)      # (the game as the game sees it: every enabled mod)
            lines += rep + update_size_requests(game, mod, files)
            m.original = __import__('json').dumps(m.values())
            if env is not None:
                import glob
                other = [d for d in glob.glob(os.path.join(game.folder, 'mods', '*', 'romfs', 'Pack', 'EnvPalette.pack.zs'))
                         if os.path.basename(d.split(os.sep + 'romfs')[0])[:1] not in '_-'
                         and os.path.basename(d.split(os.sep + 'romfs')[0]).lower() > mod_name.lower()]   # (mods loading
                #  before ours are carried in our pack; only a mod loading after ours would replace it)
                for d in other:
                    lines.append(f'warning: {d.split(os.sep + "romfs")[0]} loads after this mod and replaces EnvPalette.pack: this map\'s environment edits are hidden while it is enabled')
        return lines

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
            self.mod_name = prefs(context).work_mod
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            if self.root == 'NONE':
                self.report({'ERROR'}, 'Import a map first'); return {'CANCELLED'}
            from . import scene
            from .core.game import Game
            from .core.export import write_mod
            root = bpy.data.collections[self.root]
            game = get_game(context)
            lines = export_root(context, game, root, self.mod_name)
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
            self.mod_name = prefs(context).work_mod
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
            game = get_game(context)
            banc = root['ootr_banc']
            lines = export_root(context, game, root, self.mod_name)
            for line in lines: print('[ootr play]', line)
            pos = tuple(scene.GAME_TO_BLENDER.inverted() @ context.scene.cursor.location)
            profile, n, _proc = play(game.folder, banc, pos)
            from .core.play import log_path
            self.report({'INFO'}, f'{banc}: starting the game at {tuple(round(v, 1) for v in pos)} '
                                  f'({n} saves of the test profile set; the title screen is skipped for you). Game log: {log_path(profile)}')
            return {'FINISHED'}

    def template_items(self, context):
        items = [it for it in map_items(self, context) if it[0] != 'NONE']
        return items or [('NONE', '(choose your game folder first)', '')]

    class OOTR_OT_new_map(bpy.types.Operator):
        """Make a new map next to the game's (nothing of the game is replaced): a copy of a template map's ground,
        collision and lighting under a new name, registered so Play can start in it"""
        bl_idname = 'ootr.new_map'
        bl_label = 'New Map'
        bl_options = {'REGISTER', 'UNDO'}

        name: StringProperty(name='Name', default='MyMap', description='Letters, digits and _ (3-41 characters)')
        kind: EnumProperty(name='Kind', items=[
            ('blank', 'Blank', 'An empty map (no ground, walls or kill plane; a small start platform until you add '
                               'collision of your own) with the sky, light and ambient sound of maps you choose'),
            ('copy', 'Copy of a Map', 'A copy of a game map under a new name (its ground pieces, or everything)')], default='blank')
        look_from: EnumProperty(name='Sky & Light Like', items=template_items,
                                description='Sky, clouds, weather effects, sun and time of day of this map')
        sound_from: EnumProperty(name='Ambient Sound Like', items=template_items,
                                 description='The ambient sound of this map')
        template: EnumProperty(name='Copy Of', items=template_items)
        keep: EnumProperty(name='Keep', items=[(k, n, d) for k, n, d in __import__(__package__ + '.core.newmap', fromlist=['KEEP_MODES']).KEEP_MODES],
                           default='land')

        def invoke(self, context, event):
            names = {it[0] for it in template_items(self, context)}
            for prop, want in (('template', 'Market_Child'), ('look_from', 'HyruleField_Child'), ('sound_from', 'HyruleField_Child')):
                if want in names: setattr(self, prop, want)
            return context.window_manager.invoke_props_dialog(self, width=440)

        def draw(self, context):
            lay = self.layout
            lay.prop(self, 'name')
            lay.prop(self, 'kind', expand=True)
            if self.kind == 'blank':
                lay.prop(self, 'look_from')
                lay.prop(self, 'sound_from')
                lay.label(text='Empty: only a start point and a small platform under it', icon='INFO')
            else:
                lay.prop(self, 'template')
                lay.prop(self, 'keep', expand=True)
            lay.label(text=f'Written into mods/{prefs(context).work_mod}', icon='FILE_FOLDER')

        def execute(self, context):
            global _map_items
            from . import scene, lighting
            from .core.newmap import create_map
            from .core.export import update_size_requests
            game = get_game(context)
            mod = os.path.join(game.folder, 'mods', prefs(context).work_mod)
            try:
                with watchdog(f'new map {self.name}'):
                    if self.kind == 'blank':
                        from .core.newmap import BLANK_TEMPLATE
                        rep, files = create_map(game, self.name, BLANK_TEMPLATE, 'start', mod,
                                                look_from=self.look_from, sound_from=self.sound_from)
                    else:
                        rep, files = create_map(game, self.name, self.template, self.keep, mod)
            except ValueError as ex:
                self.report({'ERROR'}, str(ex)); return {'CANCELLED'}
            rep += update_size_requests(game, mod, {k: v for k, v in files.items() if k.startswith(('RSDB/', 'Banc/BancBasedResource/'))})
            info = os.path.join(mod, 'info.json')
            if not os.path.exists(info):
                import json
                with open(info, 'w') as f:
                    json.dump({'name': 'Map Edits', 'author': '', 'version': '1.0.0',
                               'description': 'Maps made and edited with OoT Remake Map Tools for Blender'}, f, indent=2)
            for line in rep: print('[ootr new map]', line)
            _map_items = []
            game = get_game(context)                       # (now with the new map)
            stats = scene.import_map(context, game, self.name, models=True, collision=True)
            root = next(r for r in reversed(scene.map_roots(context)) if r['ootr_banc'] == self.name)
            try:
                lighting.import_lighting(context, game, self.name, root)
            except Exception as ex:
                print('[ootr new map] lighting:', ex)
            try:
                from . import mapsettings_ui
                mapsettings_ui.load_for(context, game, self.name)
            except Exception as ex:
                print('[ootr new map] map settings:', ex)
            from .core.newmap import STARTPOS
            sp = game.byml(STARTPOS).get(self.name, {})
            t = next(iter(sp.values()), {}).get('Trans') if sp else None
            if t: context.scene.cursor.location = scene.GAME_TO_BLENDER.to_3x3() @ __import__('mathutils').Vector(t)
            warn = [l for l in rep if l.startswith('warning')]
            self.report({'WARNING'} if warn else {'INFO'}, f'New map {self.name}: ' + (warn[0] if warn else stats))
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
            self.layout.operator('ootr.new_map', icon='ADD')
            col = self.layout.column(align=True)
            col.operator('ootr.import_map', icon='IMPORT')
            col.operator('ootr.import_mm3d', icon='IMPORT')
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

    classes = (OOTR_Preferences, OOTR_OT_use_game_folder, OOTR_OT_choose_game_folder, OOTR_OT_new_map, OOTR_OT_import_map, OOTR_OT_export_map, OOTR_OT_play, OOTR_OT_build_library, OOTR_PT_panel)

    @bpy.app.handlers.persistent
    def _upright(scene, depsgraph=None):
        from . import scene as sc
        sc.upright_library_assets(scene, depsgraph)

    def register():
        for c in classes: bpy.utils.register_class(c)
        from . import lighting, custom_geo, scene, mapsettings_ui, sound_ui, mm3d_import
        lighting.register()
        custom_geo.register()
        mapsettings_ui.register()
        sound_ui.register()
        mm3d_import.register()
        bpy.app.handlers.depsgraph_update_post.append(_upright)
        bpy.types.TOPBAR_MT_file_import.append(menu_import)
        bpy.types.TOPBAR_MT_file_export.append(menu_export)

    def unregister():
        from . import lighting, custom_geo, mapsettings_ui, sound_ui, mm3d_import
        mm3d_import.unregister()
        sound_ui.unregister()
        mapsettings_ui.unregister()
        if _upright in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_upright)
        custom_geo.unregister()
        lighting.unregister()
        bpy.types.TOPBAR_MT_file_import.remove(menu_import)
        bpy.types.TOPBAR_MT_file_export.remove(menu_export)
        for c in reversed(classes): bpy.utils.unregister_class(c)
