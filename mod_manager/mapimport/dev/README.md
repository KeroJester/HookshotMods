# Clock Town development scripts (RESEARCH_NOTES.md §80)

Research scripts that built the `070-ClockTownDev` test mod; not used by the mod manager yet. They read the game's
romfs and the developer's own extracted MM3D files (`mods/_MajoraAssets/extracted/mm3d_romfs`) and write into a game
folder's `mods/`. Nothing of either game is stored here.

- `build_ct_compound.py`: Clock Town's collision (MM3D scene collision) into the SageRoom compound template -> `ct.bphsc`
  (expects `sage.bphsc`, the decompressed SageRoom compound, next to the scripts' parent folder).
- `build_dev.py`: the test map (GameBancParam, placements, GameBancInfo row, StartPos, warp flow hijack, compound,
  bake copies); options as environment variables (DEV_GBP, DEV_START, DEV_START_ROTY, DEV_DROP, DEV_SAGE_BCETT,
  DEV_ONLY_BASE, DEV_BAKE, DEV_DIR, HOOKSHOT_GAME_DIR).
- `build_ct_dev.py`: Clock Town's model, the Clock Tower parts and the moon into the test mod (CT_* options).
- `rf.py`: read a romfs file decompressed.

Last good setup (Longshot, 2026-11-15):
`DEV_DIR=070-ClockTownDev DEV_START_ROTY=3.14159 DEV_DROP=HeightMapParam DEV_GBP=Market_Child DEV_START=-7,100.6,-6
DEV_SAGE_BCETT=1 DEV_ONLY_BASE=1 DEV_BAKE=1 python3 build_dev.py` then `python3 build_ct_dev.py 070-ClockTownDev`,
with MajoraMod's `majora/dev_flow.txt` = `15=11` and `majora/dev_rstb.txt` raising StartPos and GameBancInfo.
