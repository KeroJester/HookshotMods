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

        def invoke(self, context, event):
            context.window_manager.invoke_search_popup(self)
            return {'RUNNING_MODAL'}

        def execute(self, context):
            if self.banc == 'NONE':
                self.report({'ERROR'}, 'Set the game folder in the add-on preferences')
                return {'CANCELLED'}
            from . import scene
            from .core.game import Game
            stats = scene.import_map(context, Game(prefs(context).game_folder), self.banc, models=self.models,
                                     report=lambda m: self.report({'WARNING'}, m))
            self.report({'INFO'}, stats)
            return {'FINISHED'}

    def menu_import(self, context):
        self.layout.operator(OOTR_OT_import_map.bl_idname, text='OoT Remake Map')

    classes = (OOTR_Preferences, OOTR_OT_import_map)

    def register():
        for c in classes: bpy.utils.register_class(c)
        bpy.types.TOPBAR_MT_file_import.append(menu_import)

    def unregister():
        bpy.types.TOPBAR_MT_file_import.remove(menu_import)
        for c in reversed(classes): bpy.utils.unregister_class(c)
