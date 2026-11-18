# OoT remake map tools (Blender add-on)

A Blender 4.2+ extension to load the remake's maps into Blender and (later) export changes and new maps.
It reads the player's own game folder (Longshot / Hookshot, or an extracted romfs); nothing from the game is stored
here. Design and file formats: RESEARCH_NOTES.md §80-§81.

- `ootr_map_tools/core/`: readers with no Blender dependency (testable with plain Python):
  `game.py` (game folder, .zs files, actor packs, model index), `banc.py` (map list, placements),
  `bfres.py` (models), `collision.py` (Havok collision shapes).
- `scene.py`, `models.py`: the Blender side (objects, meshes, materials, collision collections).
- `tests/import_map.py`: headless import + preview renders (`blender -b --factory-startup --python ... -- <game> <map> <out>`).
- `ootr_map_tools/vendor/`: copies of mod_manager modules (byml, zstd_helper); edit the originals.

Try the core without Blender:

    cd blender && python3 -m ootr_map_tools.core.banc ~/Games/Longshot/nightly-80-7315957c            # maps
    cd blender && python3 -m ootr_map_tools.core.banc ~/Games/Longshot/nightly-80-7315957c KakarikoVillage_Child

Plan: v1 import (models + textures, placements as objects, collision), v2 export (placements, new maps).
