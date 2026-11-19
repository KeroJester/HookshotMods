# OoT Remake Map Tools (Blender add-on) — experimental

A Blender add-on that turns Blender into a level editor for the Ocarina of Time remake running under Longshot or
Hookshot: open the game's maps, change them, build new maps from your own models, and press **Play in Game**.

> **Early, experimental release.** Many parts work; some are rough or unfinished (see *Known problems*). Expect
> bugs, back up your `mods/` folder, and please report what breaks.

**No game files are included.** The add-on reads your own copy of the game.

## Requirements

- **Blender 4.2 or newer** (tested on Blender 5.3, Linux).
- The game set up with **Longshot** or **Hookshot** (the game folder with `romfs/` and `mods/`).
- Linux or Windows (64-bit). Windows is less tested.

## Install

1. Download `ootr_map_tools-<version>.zip` from the Releases page.
2. Blender: **Edit → Preferences → Get Extensions → (menu) Install from Disk…**, pick the zip.
3. In the add-on's preferences, set **Game folder** (it lists folders it finds). Optional: **Majora's Mask 3D copy**
   (your own decrypted `.3ds` or extracted RomFS) for *Import MM3D Scene*.

The add-on installs its small runtime helper (**BlenderMod**, into `mods/065-BlenderMod`) when needed. If you use
**MajoraMod 0.4.2 or newer**, that is used instead.

## Using it

Open the **OoT** tab in the 3D view's sidebar (N).

- **New Map**: an empty map (choose its sky & light look and soundscape), or a copy of a game map.
- **Import Map** (also File → Import): any game map with its models, placements and collision.
- **Your own geometry**: any mesh you add becomes part of the map. Per object: *Enable Collision*,
  *Visible in Game*. Materials: Principled BSDF Base Color (texture or color), Normal (via a Normal Map node),
  Roughness, Metallic, Emission, Alpha (cut-out).
- **Import MM3D Scene**: Majora's Mask 3D rooms with textures and collision, from your own copy.
- **Map Settings**: sky & light preset, clouds, weather effects, soundscape (or a custom music + ambience),
  Link's age, Epona, pause-map location.
- **OoT Lighting**: point / spot lights, the environment per time of day, and *Whole map* adjusters
  (*Sun & Sky ×*, *Shadow Fill ×*, *Brightness*). The Eevee preview is calibrated against the game for sunlight,
  shade and exposure (see below).
- **Sound**: sound emitters (positioned sounds).
- **Export Map**: writes your changes into a mod folder (default `mods/075-MapEdits`). Game maps are never
  replaced: new maps get new names, edits go into the mod.
- **Play in Game**: exports, then starts the game in the map at the 3D cursor (it presses through the title
  screen for you; a separate test profile, your saves are not touched).

## Known problems

- **Lighting preview is not 1:1 yet.** Matched (within a few %): sunlight, shade under cover, exposure, noon sky
  colour. Not yet: shadows of small objects in open light (darker in Blender), sky at dawn / dusk / night, fog,
  bloom, point / spot light brightness, the game's baked bounce light (custom maps have none yet).
- **Play cannot set the in-game time**: the game shows its own clock, Blender the time you picked.
- **Music / ambience zones** do not play yet (the buttons are disabled). Map-wide soundscapes and emitters work.
- **No kill floors / void-outs** yet: put a floor under your map. **No navmesh** for custom geometry yet
  (enemies / NPCs may not walk on it). No doors or loading zones to your maps yet.
- **Laptop GPUs**: Blender and the game share video memory. Play switches lit views to Solid to free some; if the
  game is slow, close Blender while playing.
- Large maps are split into 128 m tiles; very large single triangles lose precision far from their centre.

Please report problems with: the map, what you did, what you expected, and `~/.cache/ootr_map_tools/play.log`
(Windows: `%LOCALAPPDATA%\ootr_map_tools\play.log`) if the game was involved.

## For developers

- `ootr_map_tools/core/`: readers and writers with no Blender dependency (testable with plain Python).
- The Blender side: `scene.py`, `models.py`, `lighting.py`, `custom_geo.py`, `mapsettings_ui.py`, `sound_ui.py`,
  `mm3d_import.py`.
- `ootr_map_tools/vendor/`: copies of `mod_manager` modules; edit the originals.
- Build the package (BlenderMod + Blender's extension build): `blender/build_addon.sh <blender>`.
- File formats and every finding behind the tools: `RESEARCH_NOTES.md` §80 onward (lighting calibration: §101).
