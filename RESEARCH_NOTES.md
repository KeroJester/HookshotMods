# Goron Link Port: Research Notes

Claude's working notes. Started 2026-11-09. Update as findings come in.

---

## 0. Premise correction (important)

The original brief assumes an N64 static recomp (`Zelda64Recomp` / `ooat-recomp`) with
decomp-style C source (`z_player.c`, skelanime, actor overlays). **This folder is not that.**

- The game folder is a **Hookshot v2.0.3** build (`hookshot_version.txt`). Hookshot
  is a Switch static recompiler (AArch64 guest → x86_64 host). Logs show `[a64]`, `[hos ...]`,
  `nnMain`, NVN graphics.
- The game is a Switch "Ocarina of Time" title (`.hookshot_romfs`: unpacked from
  `The Legend of Zelda Ocarina of TIme [04004860224DEC000].nup`).
- The RomFS uses a **TotK-style engine layout**: `Pack/Actor/*.pack.zs` (zstd + SARC, with
  dictionaries in `Pack/ZsDic.pack.zs`), `Model/*.bfres.zs`, `AI/*.ainb`, `Banc/`, `Mals/`,
  `GameData/`, `RSDB/`, `Phive/` (physics).
- **There is no game source code.** The player logic is compiled guest code inside the
  recompiled binary (`bin/hookshot`), plus data-driven AI graphs (`.ainb`) and params (`.bgyml`).
- Takeaway: MM decomp/recomp is still useful as a *design reference* (how Goron moves, action
  list, tuning values, animation set). Its code and asset formats (F3DEX display lists, N64
  skeletons) **cannot be reused directly**. Assets must be converted to bfres, and behavior must
  be built from AINB/params and the overlay hooks.

## 1. Modding surfaces available

1. **RomFS replacement mods**: `mods/<NNN-Name>/romfs/...` mirrors `romfs/`. A leading `_`
   disables a mod (log: "is off (its name starts with _)").
2. **LD_PRELOAD host overlay**: `overlay/hookshot_overlay.cpp` → `libhookshot_overlay.so`,
   auto-preloaded by the `hookshot` launcher script. It has ImGui (SDL3 + Vulkan), hooks
   `nvnQueuePresentTexture` through Hookshot's host-call table, and calls guest functions
   directly with `SafeCallGuest(ctx, guest_addr, ...)`.
3. **Python tooling**: `mod_manager/` (`zstd_helper.py` handles ZsDic plus SARC pack/unpack,
   `byml.py`, `build_goron_mod.py`, `build_playground_mod.py`, CLI/GUI).

## 2. Existing work (already in the folder)

> **USER, 2026-11-09: all existing mods and overlay features are early and broken. Ignore them.**
> The adult age warp (`SwitchAgeAdult`, i.e. setting `Sequence_ReservedNextBanc` to
> `KokiriForest_Adult`) does NOT work. TransformGoron and SetGoronSpeedBuff are also unreliable;
> the speed buff logged "patched=0". Treat everything in this section as unverified hints only,
> and re-derive from vanilla.

- `mods/010-GoronMaskTransformation` (enabled): replaces
  `Model/Armor_Child_Mask_Goron.Armor_Child_Mask_Goron.bfres.zs`,
  `Pack/Actor/Armor_Child_Mask_Goron.pack.zs`, `Pack/Actor/Armor_Adult_Goron.pack.zs`, and
  `Mals/USen.Product.110.sarc.zs` (text). So the current approach treats the Goron Mask as an
  **armor/equipment item**, not as a separate player form.
- `mods/_020-ModPlayground` (disabled): title-menu option, test map (Banc DreamWorld), and
  Sequence AINBs that give all items.
- `mods/_MajoraAssets` (disabled, used as a source only): MM USA `.z64`,
  `extracted/object_link_goron.bin` (raw object segment, 117 KB), and
  `extracted/goron_link_standing.obj` (a static mesh export with no skeleton or animations).
- The overlay already has these pieces:
  - `TransformGoron` menu action and the `g_IsGoronTransformed` flag
  - `PushItemToPouch` (guest `0x8112d370`)
  - Equip helpers (guest `0x810cceb0`, `0x81033f20`, refresh `0x803971a0`)
  - `SetGoronSpeedBuff`, which patches the static param table at guest `0x83f21114` and scans
    the player-actor heap blocks for param blocks
  - Calls to GameData/flag functions (`0x809d39e0`, `0x83779370`)

### Known guest addresses (from the overlay; Hookshot v2.0.3, verify after updates)
| Guest addr | Use (as named in the overlay) |
|---|---|
| `0x84e98e28` / `0x84c9e0d0` | PlayerMgr global / holder |
| `0x84ea42a8` / `0x84c9e180` | GameDataMgr-ish BSS var / holder |
| `0x8112d370` | push item to pouch |
| `0x810cceb0`, `0x81033f20` | equip slot / equip item |
| `0x803971a0` | equipment refresh |
| `0x833cc0d0` | PlayerMgr call (see `hookshot_overlay.cpp:407`) |
| `0x83f21114` | static param table patched for Goron speed |

Host-side offsets (relative to the `bin/hookshot` load base): `hos_call_guest +0x1bf70910`,
`hos_ctx +0x1bf76800`, `hos_sys_alloc +0x1bf72960`, host-call table `+0x24bce0f0`
(count `+0x24bce0e0`). **These change with each Hookshot release.** The folder is v2.0.3 and
v2.0.9 is available, so do not update without re-deriving them.

## 3. Child vs Adult Link (in progress; first answer in 3a)

### 3a. Answer: separate actors, both derived from one base (confirmed)
- `Player_Child` and `Player_Adult` are **separate actors**. Both ActorParams declare
  `"$parent": "Work/Actor/Player_Base.engine__actor__ActorParam.gyml"`. They are not one actor
  toggling state.
- **Adult is close to Base.** `Player_Adult` overrides only 6 components: ActorHolder, ELink,
  EquipmentUser, SLink, Snapshot, SoundFootStep. Its model, animation, and physics refs are the
  generic `Player.*` ones.
- **Child overrides most of the player-specific components.** This is the template for a
  `Player_Goron`. The overrides are:
  - `ModelInfo` → `FmdbName: Player_Child` (the bfres)
  - `ASRef` → `AS/Player_Child.root.asb` (animation state machine), plus `ASInfo` and `AnimationParam`
  - `PhysicsRef` → `ControllerSetPath: Work/Phive/ControllerSetParam/Player_Child...` (CCT capsule;
    the Child pack has 292 Phive files vs 211 in Adult)
  - `PlayerGameParam` (`IsChild: true`, movement and size tuning)
  - `GameParameterTable`, `AttackParam`, Cct climb/ragdoll params
  - `EquipmentUserParam` (bone binds and offsets; arrow actor = DekuSeed, i.e. slingshot)
  - `PlayerCameraTarget`, `ActiveCameraAlpha`, `ConditionParam`, `PathFinding`,
    `PerimeterAnalyser`, `PlayerLifter`, `Shooter`, `Blackboard` table, `SLink`, `SoundFootStep`
- All three packs carry 208 AI files. The AI module names are identical between Child and Adult,
  so per-age behavior differences live in the params and AS, not in different action graphs.
- Child `PlayerGameParam` sample values (meters, decoded): CctRadius 0.23, PlayerHeight 1.17,
  JumpMaxInitSpeed 4.5, RollingMaxSpeed 5.625, FrontStepSpeed 6.4, PlayerStepHeight 0.35.
- **Tooling bug:** `mod_manager/byml.py` returns BYML floats (type 0xD2) as raw u32 ints. Decode
  them with `struct.unpack('<f', struct.pack('<I', v))`. Writing them back as ints keeps the
  0xD2 type, so round-trips are safe, but edits must write the float bits.
- **Still unknown:** how the engine picks which Player actor to spawn on age swap (likely a
  GameData flag read at scene/player creation). Need to find that flag. The
  `PlayerAbilityChangeArea` actor is worth a look.

### 3c. How the player actor gets created (vanilla, in progress)
- The player is **not** placed in the Banc scene files (`*.bcett.byml`); I grepped the
  KokiriForest scenes and found only `PlayerPos`.
- The literal strings `Player_Child` (guest `0x83dad4ef`) and `Player_Adult` (`0x83cd5a40`) each
  appear once in the binary. Their only code refs are inside a name-lookup/scan function around
  guest `0x80964800`–`0x80964b20`. That function scans a table for an actor whose name contains
  "Player_Child" (it compares bytes P-l-a-y-e-r-_-C-h-i-l-d). This is *finding* the player, not
  creating it.
- Age logic lives in the **Sequence AINBs**. `romfs/Sequence/LoadBancImpl.module.ainb` uses:
  - GameData bool `Sequence_IsChildhood` (string at `0x83d6adc5`)
  - `GameBancParam.IsChildWhenChangeBanc`
  - node param `IsCreateChildPlayer` (`0x83d46918`) and `IsForceSwitchChildOrAdult` (`0x83d14422`)
  - nodes `SeqOneShotCreatePouchPlayer`, `SeqOneShotDeletePouchPlayer`,
    `SeqQueryIsCreatedPouchPlayerChild`, `SeqSyncPlayerEquipmentAndGameData`
  - `Work/Actor/PouchPlayer_{Child,Adult}.engine__actor__ActorParam.gyml` (these are the pouch
    menu preview actors, not the field player)
- `IsChildWhenChangeBanc` / `IsChildWhenInitializingScene` strings are referenced at guest
  `0x8083cc50`/`0x8083cbe0` and `0x81228a28`/`0x812289c8`. The next RE target is the field
  player creation.
- RE tooling (scratchpad):
  - `img.elf` is `image.bin` wrapped as an ELF at VMA `0x80000000`; disassemble with
    `llvm-objdump -D --triple=aarch64 --start-address=… --stop-address=…`.
  - `xref.py image.bin <hexaddr>…` finds ADRP+ADD references to an address.

### 3b. Raw observations

Actor packs (`romfs/Pack/Actor/`):
- `Player_Base.pack.zs`: 498 files
- `Player_Child.pack.zs`: 598 files
- `Player_Adult.pack.zs`: 502 files
- Also `Player_Event`, `PlayerCamera`, `PlayerShockWave`, `PlayerAbilityChangeArea`.

Early observations:
- `Player_Child` contains both `Actor/Player_Base.engine__actor__ActorParam.bgyml` and
  `Actor/Player_Child.engine__actor__ActorParam.bgyml`. This points to an **ActorParam
  inheritance** model: Child and Adult are separate actors that both derive from Player_Base.
  They are not one actor toggling state. (TODO: confirm by diffing the bgyml parent refs.)
- Each pack carries its own copy of the action AI graphs: `AI/Player.action.<name>.module.ainb`
  (jump, crawl, climb, basicattack, chargeattack, hookshotshoot, playmusic, lockonmove, ...).
- Models are separate: `Model/Player_Child.Player_Child.bfres.zs` and
  `Model/Player_Adult.Player_Adult.bfres.zs`. Each has its own animation archives
  (`Player_{Child,Adult}{,_01.._04,_Cba,_Demo}_Animation.anim.bfres.zs`, and the Demo set has a
  `.Skeleton.bfres`). Cutscene anims are suffixed `_Player_Child` (73) or `_Player_Adult` (96).
- Unpacked copies for inspection are in
  `/tmp/claude-1000/-home-justin-Games-Hookshot/7462094f-f521-4c10-8e4e-c515f6e62042/scratchpad/packs/`
  (temporary). The unpack script is in the same scratchpad, `unpack.py`.

TODO:
- [x] Diff the Child and Adult ActorParam bgyml files (see 3a).
- [x] Which AINB modules differ: none by name (see 3a). Contents may still differ; not diffed yet.
- [ ] Find the guest function and flag that pick Player_Child or Player_Adult on age swap.
- [ ] Where inventory and equipment live (GameData flags vs. a per-actor component). How the
      engine picks Player_Child or Player_Adult on age swap (scene load? actor respawn?).
- [ ] What the vanilla `Armor_Child_Mask_Goron` pack contains vs. the modded one.

## 4. MM reference (Zelda64Recomp + mm decomp)

Source: https://github.com/Zelda64Recomp/Zelda64Recomp. The game logic is not in that repo. It
comes from the submodule `lib/mm-decomp` = zeldaret/mm @ `5607eec18bae68e4cd38ef6d1fa69d7f1d84bfc8`.
The recomp's own `patches/` only touch Goron for input remaps (`input.c:1438`), camera
(`camera_patches.c:689`), and cheats (magic). **All transformation logic is vanilla decomp code.**

Local copies (temporary scratchpad, re-clone if missing):
`/tmp/claude-1000/-home-justin-Games-Hookshot/7462094f-f521-4c10-8e4e-c515f6e62042/scratchpad/ref/`
(`Zelda64Recomp/`, plus a sparse `mm-decomp/` holding the player files).

### 4a. How MM structures forms (opposite of this game)
- MM uses **one actor**, `ovl_player_actor` (`z_player.c`, about 20.7k lines), with
  `this->transformation` (enum `PlayerTransformation`: FD=0, GORON=1, ZORA=2, DEKU=3, HUMAN=4,
  in `include/z64player.h:41`).
- Each form has its own row in several tables, all indexed by `transformation`:
  - `sPlayerAgeProperties[PLAYER_FORM_MAX]` (`z_player.c:752`): ceiling height, wall radius,
    ledge/water heights, voice/surface SFX offsets, and door/chest anims
  - `gPlayerSkeletons[]` (`z_player_lib.c:413`)
  - `gPlayerFormObjectIds[]`: the object file to load
  - boots-like movement table `D_801BFE14[PLAYER_BOOTS_GORON]`
  - per-form anim tables `D_8085BE84[animgroup][modelAnimType]`
- **Transforming:** `func_8012301C` (`z_player_lib.c:387`) swaps the player object file, then
  re-runs `Player_Init`. `Player_Init` (`z_player.c:~10680`) reads the form, sets
  `ageProperties`, `Player_InitCommon(skeleton)`, and actor flags (Goron gets
  `CAN_PRESS_HEAVY_SWITCH`), and resets B-button items and mask state. So MM also does a
  **re-init of the player**. That maps cleanly onto "despawn Player_X, spawn Player_Goron" here.
- **All forms share one limb layout** (`PLAYER_LIMB_MAX` = 0x16, i.e. 21 limbs). Animations are
  form-agnostic. Goron-specific anims are named `gPlayerAnim_pg_*` and live in the shared player
  animation data (link_animetion / gameplay_keep), **not** in `object_link_goron`.
  - → `mods/_MajoraAssets/extracted/object_link_goron.bin` holds only the mesh, textures, and
    skeleton. The animations must be extracted separately from the MM ROM.

### 4b. Goron mechanics (where to read them, and the key numbers)
MM runs at 20 fps logic. Speeds are units/frame (about 1 unit = 1 cm, an assumption to verify).
- **Curl into ball:** `func_80836AD8` (`z_player.c:6610`) → `Player_Action_96` (roll state
  machine, `z_player.c:18861`). SFX `NA_SE_PL_GORON_TO_BALL`. The curled mesh is a separate DL,
  `gLinkGoronCurledDL`, drawn instead of the skeleton (`z_player.c:12651`).
- **Roll physics (`Player_Action_96`):**
  - Speed `unk_B08`, capped at 18.0.
  - Wall bounce when speed ≥ 12.0 (yaw is reflected).
  - Slope term: `sin(floorPitch)*8 + 0.6`.
  - Visual spin: `av2.actionVar2`, a pitch rate stepped toward `speedTarget*900`.
- **Spike roll:** hold A while rolling; `unk_B86[1]` ramps 1 to 7.
  - Costs `Magic_Consume(play, 2, MAGIC_CONSUME_GORON_ZORA)`, then a continuous drain.
  - Speed is set to 18.0. Requires magic ≥ 2 and spin ≥ 0x36B0.
  - Breaks if pitch + floor pitch > 0x3A98. Releasing A unwinds it.
  - In the air: gravity -1.0, with a float phase (-0.2) then a slam (-10.0).
- **Roll jump:** B while rolling → `func_80857640(this, 14.0f, 0x1F40)` (jump vel 14,
  `NA_SE_PL_GORON_BALLJUMP`).
- **Ground pound:** `av1.actionVar1 == 2` inside `Player_Action_96` →
  `Player_SetCylinderForAttack(DMG_GORON_POUND, 4, 60)` +
  `Actor_SetPlayerImpact(PLAYER_IMPACT_GORON_GROUND_POUND, 2, 100.0f)` (`z_player.c:19217`).
- **Roll hitboxes:** spikes `DMG_GORON_SPIKES, 1, 25`; normal roll `DMG_NORMAL_ROLL, 1, 25`.
- **Punch combo:** melee anims `PLAYER_MWA_GORON_PUNCH_LEFT/RIGHT/BUTT` (`z_player.c:3446`).
  - Anims: `pg_punchA/B/C`, each with `…end` and `…endR`.
  - Active frame windows: A 6–8, B 12–18, C (butt) 8–14.
  - Damage: `{DMG_GORON_PUNCH, 2, 2, 0, 0}` (`z_player.c:5321`).
  - The combo table is at `z_player.c:5261`. Goron punches are set to 5.0 lunge (`:17760`).
- **Other Goron hooks:**
  - Camera modes `CAM_MODE_GORONDASH` and `CAM_MODE_GORONJUMP` (`z_player.c:11515`).
  - Goron can't swim: sinks, with checks like `transformation != PLAYER_FORM_GORON` around the
    water code.
  - No sword or items: `Player_IsGoronOrDeku`.
  - Hot-spring and lava tolerance handled elsewhere.
  - Drums `pg_gakki*` replace the ocarina.
- **Full `pg_*` animation set referenced by the player:** climb (start/up/end), doorA/B_open,
  gakki* (drums), maru_change (curl/uncurl), maskoffstart, punchA–C (+end/endR), Tbox_open, wait.
  The rest (walk/run/idle) reuse generic tables per `modelAnimType`. Check `D_8085BE84`.

## 4c. Execution plan (draft)
1. **Actor:** create a `Player_Goron` pack by cloning `Player_Child` (it has the full override
   set). Point ModelInfo/AS/Physics/PlayerGameParam/EquipmentUser at new `Player_Goron*` files.
   Register it in RSDB/ResourceSizeTable if the engine validates sizes (check how mod 010 handled
   RSTB).
2. **Swap:** in the overlay, on "Transform Goron", despawn the current player and spawn
   `Player_Goron` at the same transform. This is the analogue of MM's `func_8012301C` re-init.
   Step 0 is finding the guest function the age swap uses. Fallback: a reskin plus param patch on
   Child, as mod 010 does today.
3. **Assets:**
   - Mesh/skeleton from `object_link_goron.bin`, via an N64 extractor (e.g. Z64Utils, or
     Fast64 in Blender).
   - `pg_*` animations from the MM ROM's player animation file.
   - Retarget onto this game's Player_Child bone names (`Spine_2`, `Ph_Bangle`, …) so Child's
     animation archives can drive the body where no Goron anim exists.
   - Export to bfres (Switch Toolbox).
4. **Tuning:**
   - Translate the MM numbers above into `PlayerGameParam` (meters/second at this game's tick
     rate): taller and wider CCT, heavier, no swim, no climb on some surfaces.
   - Write floats correctly (see the byml bug).
5. **Moves:**
   - Map the punch combo onto the existing `basicattack` AINB with Goron AttackParam and anims.
   - Roll, spike roll, and pound have no OoT analogue. They need new AINB action modules or
     overlay-driven physics (the overlay can set velocity on the player each frame). Prototype
     in the overlay first.

## 5. Open questions for the user

- Which approach to take: (a) Goron as a third **player actor** (`Player_Goron` deriving from
  Player_Base, swapped like age), or (b) keep the current approach of Goron as an
  **armor/mask** that reskins and retunes Child Link?
- Asset pipeline: is there a tool that can write bfres (e.g. Switch Toolbox), and will the Goron
  skeleton be rigged to Link's bone names so the existing animations can drive it?

## 6. Key finding: player spawning is data-driven
`romfs/Scene/Component/ResidentActorMgrParam/Default.game__scene__ResidentActorMgrParam.bgyml`
has `Child: [{Actor: Work/Actor/Player_Child...}]`, `Adult: [{Actor: Work/Actor/Player_Adult...}]`,
and `Common: [PlayerCamera, EventCamera, ...]`. Age (`Sequence_IsChildhood`, a GameData bool)
picks the list. A new actor could be put in the `Child` slot with no code changes. However:
- New actors need RSDB rows in `ActorInfo` and `GameActorInfo`; both are sorted by `__RowId`.
  ActorInfo's `ActorName: "Player"` is the C++ class.
- 82 EventSetting rows (cutscenes) reference `Player_Child`. The engine's player lookup
  (`0x80964800`) does a substring search for "Player_Child"/"Player_Adult", so a new actor
  should be named `Player_Child_<X>` to stay matchable.
- So milestone 1 modifies **Player_Child in place**. A separate `Player_Child_Goron` actor
  (for real mask transformation) is milestone 3.

GameData hashes are **CRC32 of the name** (`Sequence_ReservedNextBanc` = 0xa77aecee matches).
The overlay's `IsRequestedResetGimmick` = 0x3fc35576 matches neither CRC32 nor murmur3, which
may be one reason the old warp failed. The lookup helper `0x806a9e60(name, 0, 0)` hashes a
string; the GameDataMgr pointer is at `[0x84c9e180]`, with the bool table at `gmd+0x20`.

## 7. Tooling fixes (2026-11-09). Likely root causes of the old "black screen" packs
1. `zstd_helper.create_sarc` padded the data offset to 0x80 and aligned files to only 4 bytes.
   Vanilla uses 8-byte alignment. Fixed; repacking now reproduces vanilla **byte-for-byte**
   (tested on 63 packs). The builder asserts this on every run.
2. `byml.py` had the scalar type table shifted: 0xD2 float was read as int, and 0xD3/0xD4 were
   swapped. Floats were written back as int32, silently corrupting every float param. 64-bit
   types, binary, null, and hash dicts were unsupported. **Rewritten** (same API:
   `parse_byml` / `write_byml`; types `U32`/`I64`/`U64`/`F64`/`HashDict32/64`). Verified
   lossless with typed deep-compare on 994 files (all player packs plus every RSDB table).
3. New `mod_manager/rstb.py`: a RESTBL reader/writer (byte-identical round-trip) plus
   `estimate_size()`. Measured rule: **pack = round_up(size, 32) + 312** (exact on 157 packs);
   ainb +376; bntx/extm/byml +264. bgyml and bfres overheads vary, so estimate generously. Any
   modded file that grows must get a bigger RSTB entry.
   - Caveat: only one mod can ship the RSTB file. If more mods need it, the mod manager should
     merge RSTB at launch.
Backups of the old helpers are in the scratchpad (`zstd_helper.py.bak`, `byml.py.bak`).

## 8. Progress log
- **v0.1.0 (2026-11-09)**: `mods/030-GoronLink`, built by
  `python3 -m mod_manager.build_goron_link` from vanilla (idempotent; writes `BUILD_LOG.txt`).
  - Player_Child takes on Goron body size: adult-sized height and radius (1.68 m / 0.35 m), 20
    child collision shapes scaled (H x1.52, V x1.44), and dodge roll at 9.0 m/s (MM
    spike-roll equivalent).
  - The model is still Child Link's.
  - RSTB entry raised.
  - Old `010` mod disabled (renamed `_010-...`).
  - **Needs an in-game test**: does it boot into a save, and is Link taller/wider (can't fit
    crawlspaces) and rolling faster?

## 9. Next milestones
1. Confirm v0.1 loads. If it black-screens, bisect: (a) the repacked-but-unchanged pack,
   (b) +RSTB, (c) +params.
2. **Goron model.** Needs a real bfres writer/editor, or Switch Toolbox (Windows; try Wine) or
   Blender plus a bfres plugin. Neither is installed.
   - Pipeline: MM `object_link_goron` limbs (27 limbs, skeleton table at `0x1C848` in the
     decompressed object) → mesh per limb → skin each limb rigidly to the matching Player_Child
     bone → replace `Model/Player_Child.Player_Child.bfres`.
   - Child's animations will then drive the Goron body.
3. Separate `Player_Child_Goron` actor + RSDB rows + a runtime swap. This gives a real Goron
   Mask transformation instead of a replacement.
4. Goron moves (curl/roll, spike roll, pound, punch combo) as AINB actions or overlay physics.

## 10. F2 "Param Tuner" (overlay), added 2026-11-09
- `overlay/param_tuner.inc` (included by `hookshot_overlay.cpp`), plus
  `overlay/player_param_keys.h`, generated by `overlay/tools/gen_param_table.py`. Rerun the
  generator after rebuilding the mod, then `make` in `overlay/`.
- **Scan:** a background thread scans the guest heap (`0x1000000000`, 6894 MB; uses `mincore`
  to skip non-resident pages) for the anchor value 5.625 (vanilla) or 9.0 (mod)
  RollingMaxSpeed. Each anchor is scored by how many distinct known PlayerGameParam values sit
  within ±0x2000.
- **Results:** a "vanilla N / mod M" score shows which values the game actually loaded. If the
  byte before the anchor is 0xD2, the block is raw BYML file data, not the parsed struct.
- **Output:** "Save changed" appends to `overlay/param_tweaks.txt`; "Dump block" writes
  `overlay/param_dump_<addr>.bin`, for mapping exact field offsets offline.
- User report on v0.1: the save loads; no obvious roll change. Unknown whether the values aren't
  loaded or aren't used. The tuner should tell.

## 11. Hookshot source (https://github.com/NotRexed/Hookshot), read 2026-11-09
Local clone: scratchpad `ref/hookshot-src` (main is around v2.0.10; this folder runs v2.0.3,
source 917a0ccb). It's a fork of mkw-recomp: the game is lifted to C via NotRexed's a64-kit,
with Horizon OS and NVN2 reimplemented in `host/` and Vulkan in `gpu/vk`.
- **Mods** (`host/mods.c`): emulator layout, `<mods>/<name>/romfs/...`; `-` or `_` prefix =
  off. **Whole-file replacement**; when two mods have the same file, the path that sorts last
  wins. No merging and **no RSTB handling**, so the mod manager must merge RSTB itself if
  several mods need it. exefs patches are ignored (the code is compiled ahead of time).
  `HOS_MODS=<dir>` overrides the folder.
- **`bin/hookshot` is not stripped.** Every lifted guest function is a host symbol
  `f_<guestaddr>` (295k of them), e.g. `f_80964800` at host +0x10e868c0. So:
  - Any host RIP maps to a guest function (`nm`/`addr2line`), and traces become readable.
  - The overlay can resolve `hos_call_guest`, `hos_ctx` and `hos_sys_alloc` by name from the
    ELF `.symtab` instead of hardcoded v2.0.3 offsets, so it survives Hookshot updates.
  - Guest functions are real native functions, so the overlay could inline-hook them (e.g. a
    param loader or player init). Needs care: check that callers call `f_*` directly.
- **Debug env vars** worth using:
  - `HOS_FS_TRACE=1`: logs every file opened or not found. This shows whether
    `Pack/Actor/Player_Child.pack.zs` comes from the mod.
  - `HOS_WATCH=<file>`: once `<file>` exists (up to 4 guest addresses in hex), sets CPU data
    breakpoints on every guest thread (4 s reads+writes, then 4 s writes). `<file>.out` lists
    accesses by host place, which maps to `f_<addr>`. Use it on a tuner-found param address to
    see who reads RollingMaxSpeed.
  - `HOS_INPUT_SCRIPT="<sec>:<btn>[+btn][/<ms>],..."` (buttons A B X Y L R ZL ZR PLUS MINUS
    LS RS dpad, sticks LUP/LDOWN/...), and `HOS_INPUT_LIVE=<file>` (lines appended while
    running): automated test runs. `HOS_AUDIO=0` mutes.
  - Others: `HOS_TRACE`, `HOS_SAMPLE` (profiler), `HOS_MEM_MAP`, `HOS_MAP_TRACE`,
    `HOS_FS_OVERLAY`, `HOS_OOT_TELEPORT_*`.
- **Also useful:** `nx2/xref.py`, `sasym.py`, `samap.py` (their xref/symbol tooling), and
  `nx2/oot_patches.py` (OoT-specific patches already applied by the builder).

## 12. Headless test sessions (2026-11-09): what is actually loaded and used
Runner: `overlay/tools/run_headless.sh <out> [secs] [shot_every]`. It uses `HOS_HEADLESS` +
`RX_VK_SHOTS` (PNGs, no window), `HOS_INPUT_LIVE` (input), `HOOKSHOT_TUNER_CMD` (tuner commands
`scan` / `select n` / `at <hex>` / `set <Name> <v>` / `find <Name..>` / `dump`), `HOS_WATCH`
(data breakpoints), `HOS_FS_TRACE`, and a COPY of `user/` (real saves untouched). Title →
Continue = `A,A,A` about 35 s after boot; you spawn in Hyrule Field facing a lake (LUP walks
into the water, LDOWN goes onto land).

**Finding 1: shared files across packs (the reason v0.1 did nothing).** `HOS_FS_TRACE`
showed our `Player_Child.pack` loading. But `Player_Child.game__player__PlayerGameParam.bgyml`
is also in `PouchPlayer_Child.pack`; the engine caches by path and the pouch player loads
first, so the vanilla copy won. Half the child shape files are also in `Dm_Player_Child_Op.pack`.
The builder now indexes every pack (`mod_manager/cache/pack_index.json`) and patches **every
pack holding an identical copy** of a changed file.

**Finding 2: memory layout.** The parsed PlayerGameParam struct lives in the guest region
`0x7f........` (not the 0x10_0000_0000 heap). Fields are in **alphabetical order**, e.g.
CctRadius +0x074, GroundSpeedScale +0x1b8, JumpMaxInitSpeed +0x250, LockOnRollingMaxSpeed
+0x268, PlayerHeight +0x2e8, RollingMaxSpeed +0x300, RollingMaxSpeedHeavy +0x304. 82 of 217 are
mapped so far (`overlay/struct_map.txt`, `tools/map_struct.py`); other field types are
interleaved. Child overrides arrive tagged (from our pack); base `Player` values arrive UNtagged,
so the base layer comes from somewhere else (compiled-in defaults?). Unresolved.

**Finding 3: what is read (HOS_WATCH while running and rolling).**
- PlayerHeight: read every frame by f_80c225d0, f_806211a0, f_80622b80, f_804caa80 (x2), f_8061d6c0.
- GroundSpeedScale: read ~10 times/s by f_80953c30.
- **RollingMaxSpeed / RollingMaxSpeedHeavy: never read, even mid-roll** (screenshot confirms
  the roll). The dodge roll's speed is not this param; it is likely animation root motion.
- **GroundSpeedScale = 3.0 live: no change in run distance** (an out-and-back A/B ended at the
  start point).
- So run and roll speed most likely come from the animation system (AS files / root motion).
  Next: look at `AS/Player_Child.root.asb`, `AnimationParam`, and the GameParameterTable for
  speed or rate fields, then test with the tuner and HOS_WATCH.

**Tooling added:** `--probe` builds (unique low-byte tag on every PlayerGameParam float; ~0.003%
change); `tools/analyze_dump.py`; `tools/map_struct.py`; the tuner scans all guest memory, ranks
by mod hits, and `set` only writes the exact struct slot. The mod is currently a **probe build
(v0.1.0-probe)**; rebuild without `--probe` for a clean build.

## 13. Cut / unused content survey (2026-11-09)
- **No Majora's Mask content.** No file name, actor or text mentions Majora, Termina, Clock
  Town, Fierce Deity, Kafei, Tatl, or playable transformations. Skull Kid, the Moon object
  (`Obj_Moon_A_01`), the Happy Mask Shop, and Goron/Zora/Keaton masks are all normal OoT
  content. The masks are cosmetic `Armor_Child_Mask_*` items.
- **Reserved third amiibo mask.** The game has `Armor_Child_Mask_Amiibo0` (Fairy-Kid Mask) and
  `Amiibo1` (Princess Mask). Poe dialogue (`EventFlowMsg/Enemy_Event.msbt`,
  `PoeMask{0,1,2,4,5,6}_Amiibo03`) reacts to a THIRD mask: "mysterious mask" / "I don't really
  understand that mask" (JP: 不可思議なお面). There is no actor, model, item text or pouch entry for
  it. Its identity is unknown.
- `Banc/DreamWorld/*` = "Hall of Fallen Foes", the official boss rematch hub, with 25 boss and
  mini-boss stages, `BossRushIntervalRoom` and `EnemyMuseum`. Not cut.
- Leftovers: `Dummy` actor, `Drop_Test_Pot_Normal`, `Test_Foliage*`, 10
  `engine__debug__ClickMenu` files (dev click menus for bosses, Big Poe, TreasureBox,
  MovableBox), `*_tmp` / `*Temp` palettes.
- 484 of 8373 actor packs are never placed in a scene nor named in loose AI/event files. That
  does NOT mean unused: most are templates (`*_Base`) or code-spawned items (masks, songs,
  bottles). Curious ones worth a look: `Armor_{Child,Adult}_AgonyStone`,
  `Armor_{Child,Adult}_GlassTruth`, `Npc_Kokiri_Saria_Adult`, `Npc_Hylia_Zelda_Adult_Scalable`,
  `Enemy_FlyingPot`, `Item_Hat_FishingPondOwner`, `Npc_Equipment_001..028`.
- **Maps:** 80 scene sets in `romfs/Banc`, and all 80 are registered (GameBancParam/GameBancInfo)
  and referenced elsewhere. No orphaned, test or beta maps. OoT areas like Lake Hylia, Lon Lon
  Ranch and Temple of Time have no map of their own (presumably part of seamless Hyrule Field
  or other scenes). Special sets:
  - `Demo_ShootingLocation_Tapestry_ED`: a cutscene-only set for the ending tapestry.
  - `KakarikoVillage_Adult_Alternative`, `ZoraDomain_Adult_Alternative`: story-state variants.
  - `CrumblingGanonCastle`, `FinalBattleField`: escape and finale.
  - `DreamWorld/*`: Hall of Fallen Foes.
- **Master Quest is DLC, and the base game has its hooks.** `romfs/AoC1/` ("add-on content 1")
  overrides `Banc/BancBasedResource/*` with entries for 12 `_MQ` dungeons (StartPos, MapParam,
  LocationArea, GmdReset, NoEscapeRangeArea). Base scene params carry `HasMQ: true`, and
  `Foliage/ResFoliageScene/*_MQ` exist. The MQ scene files themselves (`Banc/*/..._MQ_*.bcett`)
  are NOT in this dump: they ship with the DLC.

## 14. Goron model v1: IN GAME (2026-11-09)
Screenshots are in `screenshots/` (v0.2_goron_idle.png, v0.2_goron_running.png, plus previews).
- **Correct MM source.** The old `_MajoraAssets/extracted/object_link_goron.bin` was NOT
  object_link_goron. The real one is MM USA ROM **file #655** (dmadata @0x1A500), with
  gLinkGoronSkel @0x17A84 (21 LOD limbs). Saved as
  `extracted/object_link_goron.mm_usa_file655.bin`. Player animations: link_animetion = file #7
  (uncompressed), gameplay_keep = file #649 (pg_wait header @0xE260; frame = 22 Vec3s + s16 =
  134 bytes, [0] = root position, [1..21] = limb rotations, ZYX).
- `mod_manager/mm_goron.py`: ROM reader, Yaz0, F3DEX2 DL decoder (VTX/TRI1/TRI2/QUAD,
  0x0D-segment weld matrices, textures and UV scale), skeleton, posing, flat-shaded preview
  renderer. Goron = 994 verts / 637 tris.
- `mod_manager/goron_model.py`: bfres v10.4 reader plus in-place writer.
  - Child Link bones: stride 0x58, euler XYZ (R = Rz·Ry·Rx), matches the inverse bind matrices
    to 1e-7.
  - Skin_Body: FVTX stride 40 {_p0 f32x3 @0, _n0 10_10_10_2 @12, _t0 @16, _u0 16_16 UNorm @20,
    _i0/_i1 u8x4 @24/28 (= smooth matrix index), _w0/_w1 u8x4 @32/36}.
  - Its 3 LODs share one vertex buffer (first vertex 0 / 3014 / 4705); all are pointed at the
    Goron range. The other 24 shapes get index count 0.
  - Placement: each MM limb is attached to a Link bone. Limb segments (thighs, shins, upper
    arms, forearms) are rotated onto Link's bone direction and length-fitted; hands and feet
    follow them; the torso is anchored at Spine_1 and squashed to 0.71x so the neck meets Link's
    Head; head and necklace are anchored at Head; torso vertices above Spine_2 skin to Spine_2.
    HAT and SHEATH limbs are dropped. Each vertex has 1 bone, weight 255.
- Built into the mod by `build_goron_link` (`--no-model` to skip). File size unchanged, so the
  RSTB is unchanged. Mod version 0.2.0.
- **Known gaps:**
  1. Textures: it renders with Link's skin material (reddish).
  2. Child-Link size (1.17 m).
  3. Arms are squashed lengthwise to Link's arm length.
  4. No eyes or face animation.
  5. The PouchPlayer (inventory) uses the same model, so it shows Goron too (fine).
- **Next: textures.** MM Goron textures (CI4/CI8 with TLUTs at the offsets in
  object_link_goron.xml) → one atlas → UV remap → replace the BNTX texture(s) Mt_Skin
  samples (find their names in the FMAT texture list). This needs a BNTX writer, or an in-place
  rewrite at the same size and format.

## 15. Goron colours v1: IN GAME (2026-11-09), mod v0.3.0
Screenshots: `screenshots/v0.3_goron_colored_front.png`, `..._back.png`.
- Mt_Skin textures: Player_Child_Skin_Alb (ASTC 12x12 sRGB, 768², 10 mips), _Nrm (ASTC 5x5),
  _AO (BC4 256²), _Thc (ASTC 6x6), _Gn2 (BC1), plus CmnTex_Chara_Skin_LUT_A_Slt.
- `mod_manager/bntx.py`: BRTI parsing, Tegra block-linear swizzle (GOB 64B x 8 rows; per-mip
  block height shrinks while ceil(rows/8) <= bh/2), BC4 decode, ASTC void-extent blocks,
  `paint_astc_cells`.
  - Swizzle verified: a decoded BC4 mip 1/2 correlates 0.90/0.81 with the downsampled mip 0.
  - Mip data offsets are absolute file offsets (BRTI +0x70 table).
- Colours: `mm_goron.triangle_colors` decodes the MM textures (rgba16/ci8/ci4/i8/i4 via the
  TLUT loaded in the DL; segment 0x08 = eyes, using the open-eye texture) and averages each
  triangle at its corners and centroid. I4/I8 textures are multiplied by the prim colour.
- `goron_model.flat_colour_mesh`: k-means to <= 64 colours. Every triangle gets its own 3
  vertices (1908 total, capacity 3014) with UVs at the centre of its palette cell. The albedo is
  an 8x8 grid of solid cells (8x8 ASTC blocks each, so pure down to mip 3).
  AO is flattened to white and Nrm to flat (128,128,255), so Link's skin detail stays off.
  Thc/Gn2 are untouched.
- Result: flat-shaded, N64-faithful colours. No texture detail (cracks, eye/face detail) yet.
- **Ideas next:**
  1. Real texture detail: an ASTC 12x12 encoder (hard), or switch Skin_Body's material to one
     whose albedo is BC1/BC7 or RGBA (check other materials' formats) and atlas the MM textures.
  2. Scale Goron up (MM Goron Link is larger than child Link): bigger bones via a skeleton
     scale, or scale the model relative to the hip.
  3. Eyes: the MM eye texture states could map onto Link's face/eye shapes.
  4. Gameplay moves (roll, pound, punch).

## 16. Goron textures v1: IN GAME (2026-11-09), mod v0.4.0
Screenshots: `screenshots/v0.4_goron_textured_front.png`, `_back.png`, `v0.4_goron_texture_atlas.png`.
- **Re-typed the skin albedo in place.** Player_Child_Skin_Alb goes from ASTC 12x12 768² to
  **BC1 256²**. The BRTI fields (format, size, mip count, image size, mip offset table) are
  copied from a real BC1 256² texture (Mt_PantsParts_Player_Child_Alb: same block height 3,
  same mip offsets starting at 0x1000). The new data (0xb400) fits inside the old region
  (0x16000), so the file size is unchanged. `bntx.retype()`, `bntx.encode_bc1()` (PCA
  endpoints, 4-colour mode), `bntx.mip_chain()`.
- **Atlas.** `goron_model.atlas_mesh`: each distinct (texture, TLUT, tint) is decoded and
  packed into a 256² atlas at the largest integer upscale that fits (currently 2x), with
  edge-extended padding. Each triangle's UVs are shifted into the first tile, clamped, and
  mapped into its slot. I4/I8 textures are pre-multiplied by the prim colour.
- **Sphere-mapped parts.** DLs that enable G_TEXTURE_GEN (geometry mode bit 0x40000; e.g. the
  COLLAR limb with the 8x8 I8 highlight at 0x4340, combiner ≈ texel x shade) have meaningless
  UVs. They now get a solid colour (the texture's mean). Before this fix they sampled the eye
  slot, which showed as a blue blob on the back.
- Untextured triangles get small solid patches.
- **Remaining look issues:**
  - Skin shader SSS/thickness (Thc/Gn2 maps still Link's) may tint slightly.
  - Texture wrap is clamped (triangles that tiled a texture show it stretched).
  - Eyes use the open-eye texture only.

## 17. v0.5.0: hat restored, texture distortion fixed (2026-11-09)
Screenshots: `screenshots/v0.5_goron_front.png`, `_back.png`, `v0.5_goron_textured_preview.png`.
- **The HAT limb is Link's green cap** (DL @0x9898: head-space verts via 0x0D slot 7, cap verts
  in slot 8). With pg_wait's own rotation it drapes down the back; it looked like a "slab" only
  in flat shading. It's restored and skinned to Head_2. Only SHEATH is dropped now.
- **Distortion cause: UVs.** (1) UVs were normalised by the tile size active when vertices were
  LOADED (DLs load verts before switching texture). (2) Every triangle was shifted into the first
  tile, but most Goron textures use **clamp** (G_SETTILE cms/cmt bit 2), where UVs far outside
  0..1 intentionally show the edge texels (UVs up to 11.9).
- **Fix:** `mm_goron` records per triangle the draw-time UVs (s/32 * G_TEXTURE scale / repeat
  size, where repeat = 2^mask or the tile size) and wrap modes. In `goron_model.atlas_mesh`:
  clamp axes are clipped in place; wrap/mirror axes are shifted into the first repeat, and the
  atlas entry is stored tiled or mirrored (up to 3x) when triangles span repeats.
- The atlas packer is now a skyline packer (2x scale fits again).
- `mm_goron.render_textured()`: a z-buffered textured software renderer (front/side/back) for
  checking mapping without booting the game.

## 18. v0.5.1: texture swizzle fixed (Switch 2 GOB layout) (2026-11-09)
- User's pause-menu close-up showed blocky noise across the textures. Cause: `bntx.py` used the
  **Switch 1** GOB layout. Switch 2 orders the row bits differently (from Hookshot
  `gpu/vk/vk_texture.cpp` texture_read_texels):
  `((x&63)>>5)<<8 | ((y&7)>>2)<<7 | ((x&31)>>4)<<6 | ((y>>1)&1)<<5 | (y&1)<<4 | (x&15)`.
  The block arrangement and the per-mip block-height rule (`host/nvn_layout.c` adj_tile) were
  already right.
- The earlier BC4 "validation" (0.90) was misleading. A real check on BC1/BC4 textures now gives
  mip-to-mip correlation 0.97-1.00 (it was 0.20 on BC1 with the old layout). Added
  `bntx.decode_bc1`.
- **Lesson: for anything GPU-side, read Hookshot's own decoder first.**
- Pause-menu tip for test runs: PLUS opens Items; L/R switch tabs (Equipment = one L from
  Items); the right stick rotates the model. Controls: A = action/dash, B = jump, Y = attack.
- Screenshots: `screenshots/v0.5.1_pause_menu_side.png`, `_back.png`, `v0.5.1_field.png`.

## 19. Animations v1: Goron idle (pg_wait) replaces Link's Wait (2026-11-09), mod v0.6.0
Screenshots: `screenshots/v0.6_goron_idle_sequence.png` (in game),
`v0.6_pg_wait_retarget_preview.png` (CPU-skinned preview).
- **Player animation archives:** `Model/Player_Child_{01,02,03,04,Cba,Demo}_Animation.anim.bfres`,
  about 1660 FSKAs. Key names: Wait (04), Move_Jog/Move_Run/Move_Dash (+ curves/brakes),
  Jump/Land (02), Rolling_Long (03), Sword_Attack_*, Damage_*, Evt_*. Names list:
  scratchpad anim_names.json (regenerate via bfres_anim.AnimArchive.fskas()).
- **FSKA v10 decoded** (see the `mod_manager/bfres_anim.py` docstring).
  - Bone-result layout: scale @4, translate @16, rotate @32 (euler, R = Rz·Ry·Rx).
  - Vanilla curves use new compressed encodings (flags 0x4805/0x4809, bit-packed keys); not
    decoded. My curves use classic Linear/float (flags 0x0010), and the game accepts them.
  - Baked size per curve = 4·align(frames+1, 16).
- **Relocation table:** a single real section; entries (pos, structCount, ptrCount, padCount).
  Nintendo lists a bone's curve pointer only when non-null, but always lists base and user-data
  pointers. `AnimArchive` appends new data before _RLT, redirects the FSKA (+0x20 bind array,
  +0x28 bone-anim array, frame/curve counts, baked size), and rebuilds _RLT. The untouched
  round-trip is byte-identical.
- **`mod_manager/goron_anim.py`:** retargeting via the model's limb<->bone correspondence
  (`goron_model.LIMB_FRAMES`):
  - `W_B(t) = C·[R_mm(L,t)·R_mm(L,0)ᵀ]·Cᵀ·W_B(bind)`, then converted to local; unmapped bones
    keep their bind local rotation.
  - Skl_Root gets MM's root translation delta.
  - Resampled 20 -> 30 fps.
  - 15 driven bones: Waist, legs, ankles, Spine_1, Head, arms, wrists. Spine_2 follows Spine_1.
  - `skin_pose()` for CPU previews.
- **Build:** the `ANIM_SWAPS` table in build_goron_link (archive -> {Link anim: (gameplay_keep
  header offset, looping)}). The RSTB is updated (the archive grows by about 70 KB).
- **Next candidates** (MM header offsets in gameplay_keep.xml):
  - pg_punchA/B/C (+end) -> unarmed/sword combo
  - pg_maru_change (curl) -> roll start
  - pg_gakki* (drums) -> ocarina
  - pg_doorA/B_open -> doors
  - pg_Tbox_open -> chest
  - pg_climb_* -> ledge climb
  - pg_maskoffstart
  MM Goron walk/run use the human link_normal_* anims (so Link's are already equivalent).

## 20. v0.6.1: Goron model scale 1.35x (2026-11-09)
Screenshot: `screenshots/v0.6.1_scale_1.0_vs_1.35.png` (same spawn and camera; left 1.0, right 1.35).
- **ModelInfo `DebugModelScale` {X,Y,Z} is a real, shipped model scale** (Animal_Dog 0.85,
  boss bullets 1.5, ending water plane 30x). It scales the skeleton, mesh and all animations
  together. Editing bone translations would not stick, because every animation's base data
  carries translations.
- **The pause menu needs its own ModelInfo.** Player_Child's ModelInfo file is shared with
  PouchPlayer_Child (the pause-menu preview). Scaling it cropped the preview's head. So the
  builder now adds `Component/ModelInfo/Player_Child_Goron...bgyml` (scaled), only in
  Player_Child.pack, and points `Actor/Player_Child...ActorParam.bgyml` Components.ModelInfoRef
  at it. The shared ModelInfo stays vanilla.
- The builder supports **new files** in Player_Child.pack (an `added` set: written to
  PLAYER_PACK only).
- `GORON_MODEL_SCALE = 1.35` in build_goron_link.py: about 1.25 m mesh -> about 1.68 m,
  matching the Goron collision height from v0.1. MM's ratio (ceiling 70 vs 40) would be
  about 1.75x; tweak if desired.

## 21. v0.6.2: grainy surface fixed (Link's detail normal maps) (2026-11-09)
- User close-up showed fine grain over the whole Goron. Cause: Mt_Skin's other textures, sampled
  at the Goron atlas UVs:
  - `Player_Child_Skin_Gn2` (BC1 576²) is a second, two-channel (RG) normal map full of Link's
    skin/cloth wrinkles (yellow/green image).
  - `Skin_Detail_A_Nrm` (ASTC 5x5 128², tiling pores) is a shared detail normal.
  - `Player_Child_Skin_Thc` is thickness, for skin translucency.
- **Fix** (`goron_model.FLATTEN` / `REDIRECT_TEXTURES`):
  - Gn2 -> constant (128,128,0) flat RG normal (BC1).
  - Thc -> black (no translucency).
  - Mt_Skin's texture-name slot 2 (FMAT +0x20 name array; stride 0xB0, count at FMDL +0x6C) is
    repointed from Skin_Detail_A_Nrm to Player_Child_Skin_Nrm (already flat). The shared
    texture itself is untouched. FMAT +0x98 is the runtime texture-pointer table (0xFFFF...),
    not names.
- Result: smooth N64-style surfaces (`screenshots/v0.6.2_smooth_material_pause_menu.png`).
- **Headless caveat:** the save now spawns at the Kakariko/Death Mountain gate, where headless
  frames show ALL characters white (incl. the gate soldier), a noisy minimap and a red ribbon
  (`screenshots/v0.6.2_headless_white_render_glitch_kakariko_gate.png`). This looks like a
  Hookshot headless texture-upload issue, not the mod. Ask the user to confirm in normal play.

## 22. v0.7.0: Goron animation batch (2026-11-09)
Screenshots: `screenshots/v0.7_punch_combo_ingame.png`, `v0.7_punch_combo_preview.png`.
- **References moved** to `reference/` in the game folder (mm-decomp, Zelda64Recomp,
  hookshot-src; see its README). The decomp sparse checkout now also has assets/xml/scenes,
  z64scene.h, z64bgcheck.h, z_bgcheck.c.
- `goron_anim.MM_ANIMS`: all 31 pg_* header offsets.
  - `retarget(..., n_frames, looping)`: "loop" keeps MM's speed; "fit" time-stretches to the
    Link animation's own length (keeps hit frames, door/chest sync and camera cuts aligned).
  - A list of headers = played back to back (punch + its "end" recovery, so the spin of punch C
    returns to facing forward).
- `build_goron_link.ANIM_SWAPS` (Link anim -> (MM anim(s), mode)); the builder finds each Link
  anim in Player_Child_{01,02,03,04,Demo}_Animation. Current swaps (24):
  - Wait <- pg_wait (loop)
  - all 12 Sword_Attack_{Horizontal,Vertical}{1..6} <- pg_punchA/B/C + end (cycled 1-2-3)
  - Mask_Change_Off <- pg_maskoffstart
  - Cliff_Climb_Ed <- pg_climb_endBL; Jump_Clamber <- pg_climb_startA
  - Evt_Door_Key / Evt_Door_Failure <- pg_doorA/B_open
  - 6 Demo_Front_OpenTreasureBox_Child* <- pg_Tbox_open
  The RSTB grows for each touched archive.
- **Not done yet:**
  - Ocarina -> drums (pg_gakki*) maps onto ~40 Ocarina_Child_* anims (per-note *_lp, Rest,
    Blow, Close...) in the Demo archive.
  - Roll/curl (pg_maru_change + the separate curled-ball mesh): needs gameplay work.
  - MM Goron also uses human link_normal_* anims for locomotion (Link's own already match).

## 23. Termina Field port: stage A (extract + preview) done (2026-11-09)
Screenshots: `screenshots/termina_field_topdown_preview.png`, `termina_field_view_south.png`,
`termina_field_view_north.png`.
- **ROM files:** MM USA scene #1310 = Z2_00KEIKOKU (matched by the cutscene list 0x9D8, 0x11F8,
  0x1A48); room #1311 (VROM 0x25c5000); scene_texture_08 = #1121 (0x8000 bytes, segment 6).
  - Room header: mesh cmd 0x0A -> type 2 (cullable), 55 entries {s16 xyz, radius, opa DL,
    xlu DL}.
  - 221 room actors (cmd 0x01) are not drawn yet: trees, rocks, etc. come from actor objects.
  - Collision header = scene cmd 0x03.
- **`mod_manager/mm_scene.py`:** generic MM scene/room decoder.
  - F3DEX2 with TMEM tracking. Two-texture materials load tex A/B to different TMEM offsets;
    tile 0 resolves via its tmem. Pairing "last SETTIMG" with tile 0 was the garbage-texture bug.
  - Texture format/size/TLUT/wrap come from the DL. All N64 formats are decoded.
  - G_BRANCH_Z near branch is followed. Lighting bit 0x20000: vertex rgb are normals (3129 of
    3185 field tris are lit).
  - `render_top()` and `render_perspective()` software previews.
  - Result: 3185 triangles, 71 textures, world bounds about X -8.8k..8.9k, Y -0.5k..3.6k,
    Z -7.5k..7.9k (MM units; Link about 50 units tall -> about 0.023 m/unit at child scale).
- **Stage B needs (in game):**
  1. **A from-scratch bfres model writer** (static, multi-material): terrain textures tile
     heavily, so it needs separate textures/materials with wrap samplers, not one atlas. Clone
     a simple Land_* material (shader assign) as a template per material.
  2. **A BNTX writer** for new textures (BC1 encoder exists; swizzle verified).
  3. **Collision:** Phive (.bphsh / Phive/ShapeParam) format unknown so far. Options: write
     Phive mesh shapes, or reuse existing primitive collision actors as a rough floor.
  4. **A new Banc map** (+ GameBancParam, RSDB GameBancInfo row, start position) and a way
     to get there (warp).
  5. Room actors (trees, rocks, signs) as models; sky.

## 24. v0.7.1: Goron skeleton proportions + no horizontal root motion (2026-11-09)
Screenshots: `screenshots/v0.7.1_goron_skeleton_combo.png`, `v0.7.1_goron_skeleton_bind_preview.png`.
- User report: arms buried in the torso, and he teleported during the combo.
- **Skeleton** (`goron_model.apply_goron_skeleton`, runs before the mesh build):
  - Arm_1_L/R (shoulder joints) move to where MM's shoulders sit on the placed torso:
    lateral about 0.09 -> 0.19 m.
  - Leg_1_L/R get MM's hip spacing (lateral 0.068 -> 0.139) but keep Link's hip height.
  - Arm_2 and Wrist get MM segment lengths (0.148 -> 0.352, 0.14 -> 0.28).
  - Legs keep Link's thigh/shin lengths. MM's shorter legs floated the feet about 8 cm under
    Link's animations, which plant the feet for Link's leg length.
  - The inverse bind matrices are recomputed (FSKL +0x20, 3x4 per smooth index).
- **Animations:** every FSKA's base translation for those bones is patched in place
  (`patch_animation_base_translations`) across Player_Child_{01,02,03,04,Demo,Cba} (14,377
  bone tracks). 23 tracks with translation curves (compressed) are left as-is. Without this,
  every animation would restore Link's proportions.
- **Retarget:** Skl_Root keeps only MM's vertical bob. MM's horizontal root motion (punch
  lunges) fought the game's own movement, so the model drifted and then snapped back.
- **Seen in the test:** the Kokiri Sword appears in his hand during punches (the game still
  treats the attack as a sword swing). Goron should hide weapons and shield. TODO.

## 25. v0.7.2: idle arms stuck out + floating fixed (2026-11-09)
- **Arms out:** the retarget layered MM's *delta from its reference pose* onto Link's bind pose,
  and Link's bind is an A-pose (arms about 45° out). Now `W_B(t) = D(t)·Cᵀ·W_B(bind)`. Cᵀ undoes
  the mesh-placement correction, so MM's **absolute** limb orientation shows (arms hang like
  MM's). This applies to all retargeted anims.
- **Floating:** MM's bent knees on Link-length legs lifted the feet (lowest vertex +3 cm, +4 cm
  after scale). The retarget now runs FK each frame and lowers Skl_Root so the lower ankle stays
  at its bind height. Lowest vertex in pg_wait is about -1 cm; punches -1..-4 cm.
- Preview: `screenshots/v0.7.2_idle_preview_arms_down.png`. The in-game headless shot at the
  current spawn is the white-render glitch again (corrupted cliffs too, so environmental), but
  the silhouette shows the arms down.

## 26. v0.7.3: feet planted in idle, Goron carries no sword/shield/sheath (2026-11-09)
- **Foot sliding:** with horizontal root motion dropped, MM's hip sway made the feet slide.
  The retarget now shifts Skl_Root so the ankles' midpoint keeps its frame-0 position (MM keeps
  feet still by moving its root). Foot centroid drift over pg_wait is about 1.4 mm.
- **Weapons:** `HIDDEN_EQUIPMENT` = Weapon_Sword_Kokiri, Weapon_Shield_Deku,
  Weapon_Sheath_Kokiri. Each gets its own ModelInfo (only in its own pack) with
  DebugModelScale 0.0001; the ActorParam ModelInfoRef points at it. Shop/chest/NPC actors share
  the model files (e.g. Item_Sword_Kokiri, Shop/TBox/Obj_Shield_Deku, Npc_Equipment_021) and
  are unaffected. Verified in game (`screenshots/v0.7.3_no_weapons_idle_punch.png`).
- **Leftovers:**
  - The sword-swing trail effect still shows during punches (sword attack logic is still the
    attack).
  - The Hylian Shield (shared with adult) and child items are not hidden.
  - 1:1 MM would also block item use as Goron.

## 27. MM Goron roll spec (zeldaret/mm z_player.c), for a 1:1 port
- **Entry** (Player_ActionChange_10 -> func_80836B3C): press A while moving -> func_80836AD8
  (Player_Action_96, B28=0, B86[1]=0, AF0=0, B08=B0C=0, SFX GORON_TO_BALL) +
  landing_roll anim at 1.5x. Press A standing still (func_80839F98) -> curl with pg_maru_change
  frames 0..7.
- **Player_Action_96 state:**
  - B08 = roll speed
  - B0C = wall-bounce damping (+= speed*0.05 per bounce, clamped 0..0.9, decays by
    |cos(yaw delta)|*20)
  - B86[1] = spike level 0..7
  - av1 = phase (0 curl, 1 jump, 2 pound, 3/4 ...)
  - av2 = spin rate (binang/frame)
- **Speed:** speedTarget = stick speed (linear) x 2.6, or 18.0 while spiked. Spin target =
  speedTarget*900; spin is stepped toward it asymmetrically (+0x7D0 / -0x3E8, cap 0x4B0).
- **Wall:** if touching a wall with speed >= 12, reflect yaw about the wall normal (rounded to
  0x200 steps), B8C=4 frames of locked steering, SFX GORON_ROLLING_REFLECTION.
- **Spikes:**
  - Once av1 counts to >= 0x36 (54) frames with magic >= 2 and spin >= 0x36B0: Magic_Consume 2,
    then continuous drain (MAGIC_CONSUME_GORON_ZORA); speed = 18; B86[1] ramps to 7; SFX
    BALL_CHARGE_DASH.
  - Spikes end on: A released, magic 0, or (av1==4 && speed < 12), or
    |pitch + floorPitch| > 0x3A98. SFX BALL_CHARGE_FAILED; Magic_Reset.
- **Jump:** B (not spiked) -> func_80857640(14.0, 0x1F40): jump vel 14, spin >= 0x1F40, av1=1,
  SFX GORON_BALLJUMP.
- **Air while spiked:** gravity -1.0; float -0.2 near the apex, then slam -10.0.
- **Pound (av1==2):** cylinder DMG_GORON_POUND (4, 60) + PLAYER_IMPACT_GORON_GROUND_POUND
  radius 100.
- **Rolling hit:** DMG_GORON_SPIKES (1, 25) when spiked, else DMG_NORMAL_ROLL (1, 25).
- **Slopes:** speed responds to floorPitch (sin*8 + 0.6 term), downhill acceleration.
- **Visual:** gLinkGoronCurledDL (separate ball mesh) drawn instead of the skeleton while
  curled; shape.rot.x += spin rate.
- **Units:** MM world unit -> metres: hip 33.48 MM units = 0.79 m here (0.584 x 1.35 model
  scale) -> about 0.0236 m/unit. 18 u/frame @20fps = 360 u/s, about 8.5 m/s. (WRONG by 1.5x: see §43, MM moves velocity * 1.5 per frame.)

## 28. v0.7.4: matte material (no plastic sheen) (2026-11-09)
- Mt_Skin samplers: _a0 albedo, _n0 normal, _n1 detail normal, _ao0, _slt0 skin LUT,
  _th0 thickness, **_gn0 = the Gn2 texture**.
- **Gn2 channels:** comparing NPC textures, R = roughness (metal about 100, skin about 160,
  cloth 200-220) and G about 200 (cavity/spec mask); B unused.
  `DummyWhite_Gn2` = (255,255,255) is the shipped default. My earlier flat (128,128,0) =
  roughness 0.5 = shiny.
- **Fix:** Gn2 flattened to (255,255,0), fully rough. Verified matte
  (`screenshots/v0.7.4_matte_material.png`).

## 29. Combat 1:1: findings so far (2026-11-09)
- **MM Goron combat** (z_player.c):
  - The B button starts a melee attack (Player_Action_30). `func_808335F4` picks the step from a
    3-entry table D_8085D094[1] = {GORON_PUNCH_LEFT, GORON_PUNCH_RIGHT, GORON_PUNCH_BUTT},
    index unk_ADD 0->1->2->reset.
  - Anim table (z_player.c:3446) {start, end, endR, activeStart, activeEnd}: punchA 6..8,
    punchB 12..18, punchC 8..14.
  - Damage D_8085D09C[NONE] = {DMG_GORON_PUNCH, 2, 2, 0, 0}.
  - Lunge func_8083FCF0(5.0f). Chaining: B pressed during the window -> next step, else the
    step's "end" anim. Punch hit SFX NA_SE_IT_GORON_PUNCH_SWING; impact quake
    NA_SE_PL_GORON_PUNCH (z_player.c:18844).
- **This game:** `AI/Player.action.basicattack.module.ainb` just calls the native node
  `ExecutePlayerBasicAttack` (scheduler: Player.scheduler.basicattack). All combo logic
  (selection, timing, hit windows) is compiled code.
- **Conclusion:** 1:1 combat (like 1:1 rolling) needs native hooks. Find
  ExecutePlayerBasicAttack's implementation (string -> node factory -> class vtable ->
  f_XXXXXXXX), the player's animation-play API and attack collision, then implement MM's
  Player_Action_30 logic in the overlay. The current anim-swap combo is "janky" because the
  game's own combo timing and blending still drive a punch+end animation stretched to 37/43
  frames.

## 30. MM3D assets ripped (2026-11-09)
- **ROM:** `mods/_MajoraAssets/Legend of Zelda, The - Majora's Mask 3D (USA) (En,Fr,Es).3ds`
  (NCSD, product CTR-P-AJRE, **decrypted / NoCrypto**). RomFS extracted with
  `python3 -m mod_manager.ctr_romfs <rom> <out>` -> `mods/_MajoraAssets/extracted/mm3d_romfs/`
  (1851 files, 651 MB).
- **`mod_manager/mm3d.py`:** LzS decompression (Grezzo LZSS: header "LzS\x01", u32 ?, u32 dec
  size, u32 comp size; 4 KB ring @0xFEE, LSB-first flags, 1 = literal) + GAR v2 reader.
  `actor_archive(name)` -> {path: bytes}.
- **`actors/zelda2_link_goron_new.gar.lzs`:**
  - `goron/model/link_goron.cmb` (main model)
  - `link_goron_iwa.cmb` (**the curled rock ball**)
  - `lg_spike_model.cmb` (**spikes**)
  - `grt_01/02_model.cmb` (+ cmab; roll effects?)
  - `link_goron_gakki.cmb` (**drums**)
  - `link_goron_guard.cmb` (+ `anim/pg_gurdmotion.csab`)
  - `misc/link_goron_eye.cmab` (**eye blink** material animation)
- **`actors/zelda2_link_new.gar.lzs`:** 847 player CSAB animations + 847 `.linkb` side files,
  including **all 31 pg_*** (Goron) anims.
- **CMB (link_goron):** header +0x20 index count 7108; chunk offsets: skl 0x4c, qtrs 0x47c,
  mats 0x4b4, tex 0x11fc, sklm 0x1370 (mshs + shp: 16 sepd shapes, each prms/prm), luts 0x283c,
  vatr 0x28f4, index data 0xf244, texture data 0x12a00.
- **Skeleton** (`skl `: u32 count 24, u32 0, bones 0x2C each: i16 id (0x1000 flag = skinned?),
  i16 parent, f32 scale[3], f32 euler rot[3], f32 trans[3], u32 ?): a real standing bind pose,
  mirroring MM's player limb hierarchy (root/waist 2720 high/lower/legs/upper/chest/head chain/
  shoulders/forearms/hands/sheath).
- **Next:** parse mats/tex (3DS ETC1/ETC1A4 tiled textures), vatr/sepd/prms/prm (vertex streams,
  bone palettes, indices) -> mesh; CSAB animation reader; then retarget the MM3D rig onto
  Link's bones like the N64 path (most tooling reusable).

## 31. MM3D Goron in the mod (v0.8.0, 2026-11-09)
- **Formats done** (`mod_manager/cmb.py`):
  - CMB: materials stride 0x16C; prm index offset is in 16-bit units; vertices of skin mode 0/1
    prims are in bone space (transform by the bone's bind world matrix).
  - No V flip once texture rows are flipped to top-first.
  - CSAB v5: per-frame u16 tracks, value = bias + raw*scale.
  - Root translation tracks carry an absolute offset, so only rotations are used.
- **`mod_manager/goron3d.py`** replaces the N64 source (`USE_MM3D = True` in build_goron_link):
  - MM3D bone -> Link bone map (`BONE_MAP`); the character's left = +X for both rigs.
    - 3-5 = left leg, 15-17 = left arm.
    - 10 chest -> Spine_1 (vertices above Spine_2 height -> Spine_2).
    - 14/18 clavicles -> Spine_2; 11-13 head/hat -> Head_2.
  - Placement per bone about anchor joints: chain segments are rotated onto Link's bone direction
    with a lengthwise fit; the torso gets a vertical squash (s_up).
  - Smooth skin: <= 3 MM3D weights -> up to 4 Link smooth indices (`_i0/_w0`; Skin_Body's
    skin count is 7).
  - Mesh: 1539 verts / 2682 tris (Skin_Body capacity 3014 verts / 15282 idx). Bottle materials
    2/3/4 are skipped.
  - Atlas (BC1 256²): link_goron_00 256x128 at native size on top; _01 64² and e00 eyes 32²
    below, with edge padding.
  - Skeleton fit now uses MM3D bind joints (same proportions as N64: shoulder rel. upper root
    (1200, 2385, -476)).
  - Retarget: same formula with reference = MM3D bind: W_B(t) = R(t)·R(bind)ᵀ·Cᵀ·W_B(bind).
    Drivers in `ANIM_DRIVERS`.
- **MM3D Goron animations** (`zelda2_link_new.gar` `goron/anim/`, 22 nodes each):
  - all pg_* plus Goron-specific locomotion: link_normal_walk_free (28), link_normal_run_free (19),
    link_fighter_* (lock-on walk/side-walk/wait), landing, jump_climb, carry/throw/put, push/pull,
    damage (front/back down + wake), give_other, bottle, death (link_derth_rebirth), heat waits.
  - pg_wait = 79 frames (= N64). **pg_punchA/B/C are 5/6/12 frames vs N64 12/19/20.** MM3D
    shortened the punch clips (game code holds timing); stretched to Link's anim length anyway.
- **In-game result (headless, run25):**
  - The model, idle and punch combo all render correctly; the pause-menu preview looks right.
  - In direct sunlight the body blows out (waxy/bright), so the Mt_Skin shading needs a fix
    (Gn2 channel test next; also suspect the skin SSS LUT `_slt0`).

## 32. Gn2 channel test in game (2026-11-09)
- Same sunlit spot (Kakariko gate road), constant Gn2 values:
  - (255,0,0): body blown out white
  - (0,0,0): **clean matte**, correct albedo colours
  - (255,255,255): blown out, same as (255,0,0)
- So **R acts as gloss/sheen, not roughness: 0 = fully rough**. (Old-runtime rendering bug; see §46.) My earlier "R = roughness"
  reading (§28) was backwards, which is why (255,255,0) looked worse than (128,128,0).
- `FLAT_COLOURS["matte"]` = (0,0,0). Image: `screenshots/v0.8.0_gn2_variants_*.png`.

## 33. Goron roll, phase 1: visual (v0.8.1, 2026-11-09)
- **Ball mesh:** MM3D `link_goron_iwa.cmb` (570 tris; 1 texture 128², at atlas (128,128)). It is
  the curled Goron itself (hat, beads, gauntlets on the rock). It is rigid on the spare bone
  **Ph_Leg_Support_Waist_L** (Waist child, smooth index, animations only curve its rot Y),
  laid out in that bone's local frame so its animated matrix places it directly.
- **Hiding is done by translation:**
  - The ball is parked 1000 m below the Waist in the skeleton and in every animation's base pose.
  - While curled, Spine_1 / Leg_1_L / Leg_1_R are parked 1000 m down (children follow).
  - **Scale did not work for this:**
    - Bone records with `0x03000000` (ScaleUniform|ScaleVolumeOne) ignore scale.
    - Even with those bits cleared, the ball never showed in my tests.
    - Diagnostics in the castle-gate spot were inconclusive: the ball curves didn't show there
      even without scale, so that spot probably doesn't play `Wait`.
    - Vanilla scale curves exist (flags 0x8002b8 / 0x808eb8), so scale is possible; open question.
  - `bfres_anim` can now write scale curves (targets 4/8/12, flags 0x1C0, clears ScaleOne).
- **Rolling_\* (96 anims):**
  - Frames 0-2: the body curls (pg_maru_change compressed); then the ball pops in.
  - The ball spins about X at v/r = 9 m/s / 0.58 m = 0.52 rad/frame (30 fps).
  - While curled the Waist is world-aligned, so the spin is a pure local X rotation.
- **Rolling_\*_To_Run_\* (12):** 2 frames of ball, then pg_maru_change reversed (uncurl).
- **Switching inside the animation keeps the engine's blends body->body / ball->ball.**
  Translation hiding is still visible during blends (parts pass underground). One frame in
  three test runs showed nothing at all.
- **In game:** the ball appears and rolls (screenshots/v0.8.1_roll_ball_ingame.png). The test
  spot (castle gate, low health = red flashing, tight camera) makes it hard to judge.
- **Limits of phase 1:** the motion is still Link's dodge roll: fixed short duration, roll speed
  param 9 m/s. MM's roll (Player_Action_96, §27) is a sustained state: hold to keep rolling,
  stick-steered speed x2.6, wall reflect >= 12, spikes (magic) at 18, B jump 14, ground pound.
  **That needs native hooks (phase 2).**

## 34. Native hooks: framework + player movement internals (2026-11-09)
- **Hook framework:** `overlay/guest_hooks.inc`, included by hookshot_overlay.cpp; mod hooks in
  `overlay/goron_hooks.inc`, `InstallModHooks()`.
  - Lifted functions are `void f_<guest>(GuestCtx*)`. Registers are in `c->x[]` and `c->vreg[][]`.
    Guest memory is at host 0x100000000000 + addr.
  - `f_` symbols are only in bin/hookshot's static .symtab (493k); resolved from /proc/self/exe.
  - Inline hook = `jmp [rip+0]` (14 B); the trampoline copies whole prologue instructions (a small
    x86 decoder that refuses anything it doesn't understand). All 33 test hooks installed and the
    game ran normally.
  - `HOOKSHOT_GUEST_TRACE=a,b,..` counts calls every 250 ms. `HOOKSHOT_GUEST_TRACE_RET=1` also
    logs return w0 changes.
- **RE workspace** `re/` (project-local):
  - `img.elf` = image.bin as an ELF at 0x80000000 (`llvm-objcopy -I binary -O elf64-littleaarch64
    --change-section-address .data=0x80000000`)
  - `syms.txt` (nm of bin/hookshot)
  - `hostsym.py` (host offset -> f_ name)
  - `callers.py` (BL/B call sites)
  - `node_vtable.py` (AINB node name -> getName -> vtable)
  - test runs in `re/runs/`
  - `overlay/tools/drive_test.sh <dir> -- steps...` (inputs, sleep:, mark:)
- **Data breakpoints:** Hookshot's `HOS_WATCH=<file>` (run_headless always sets it):
  - Up to 4 guest addresses; 4 s read+write, then 4 s write-only.
  - `<file>.out` lists `addr rw|w hookshot+<hostoff> count`.
  - The roll hook can arm it with the live actor's / node's address (`HOOKSHOT_ROLL_VEL=0`
    + `HOOKSHOT_WATCH_OFFS=..` [+ `HOOKSHOT_WATCH_NODE=1`]).
- **ExecutePlayerRolling** (AINB node; graph `Player.action.rolling`: Root -> SelectorPlayerRolling ->
  {Rolling: ExecutePlayerRolling, WallHit: ExecutePlayerWallHit}):
  - Per-frame methods: f_80953e80 (returns float), f_80953a70, f_80953de0, f_80953980, f_80953950,
    f_80953470, f_80953d60, f_80953930 (x2).
  - Enter: f_806d9a30, f_80950670, f_809509f0, f_809509b0. Leave: f_809590b0.
  - None of the per-frame returns changes at the roll's end. Node status (+0x18, 0x80000000 =
    done) is written only by the generic AI executor (f_800e98c0 / f_807691d0 / f_82b51500).
  - Timers in the player-state struct x21 (= actor->[0x270]->[0x358]) count down by dt
    (~1.818 at 33 fps, i.e. 60 fps frame units): +0x13b4 (10), +0x155c (10), +0x162c (15), ...
- **ExecutePlayerBasicAttack:** per frame f_827b6fd0 (x2), f_8094ece0; leave f_807c0760.
- **Player actor** (= node->[+8]->[+8]->[+0x1f0]; vtable 0x84b45e18 per Hookshot's cheats):
  - +0x31c position (x, y, z), live.
  - +0x328 3x3 rotation (rows of 4 floats with the translation column: a 3x4 matrix at +0x310?).
    The forward vector is (+0x330, +0x33c, +0x348).
  - +0x388 velocity vec3 (copies at +0x3a0, +0x394, +0x3ac). This is derived output.
  - **Writing velocity has no effect.**
- **f_80156ae0(actor, newWorld3x4*, dt):** the only writer of position and velocity. It installs the
  transform its caller computed (translation at +0xc/+0x1c/+0x2c of the matrix) and derives
  velocity = delta * 60 / dt.
  - Callers: 0x82c4173c (in f_82c415e0, which Hookshot patches for its fly cheat) and 0x83af36e0.
  - +0x358 / +0x364 = optional target position requests (NaN when unused; never used in play).
- **Character controller (CCT)**, from Hookshot's fly cheat (host/hos_main.c) and the AINB CCT nodes:
  - `cct = actor->[0x270]->[0x50]->[0x20]->[0x10]->[0x8]`, vtable 0x84c54f10.
  - vtable +0x78 = setLinearVelocity(cct, Vec3*) f_80559cd0 (node ExecutePhysicsCctSetLinearVelocityMps)
  - +0x120 = getLinearVelocity(cct) -> Vec3* f_838b3f40 (QueryPhysicsCctGetLinearVelocity)
  - +0xe8 = changePosition(cct, Vec3*) f_806d2530 (OneShotPhysicsCctChangePosition; Hookshot's
    fly mode calls it every frame)
  - On ground (QueryPhysicsCctIsOnGround): s = cct->[0x48]; i = s->[0x58]; slot = s + 0x40 +
    8*(i<3 ? i : 0); grounded = (slot->[0x10] == 0).
  - Calling guest functions from host code: Hookshot's oot_pouch_change pattern (save Ctx, carve
    a stack frame below x[31], set x0.., call the f_, restore Ctx).
- **Roll root motion:** this engine moves the dodge roll by **animation root motion**. My ball
  animations keep Skl_Root fixed, so the v0.8.1 ball roll does not travel: velocity 4.5 for one
  frame, then 0. (The earlier screenshots showed walking after the roll.)

## 35. Roll travel fixed; how player motion really flows (v0.8.2, 2026-11-09)
- **Root motion lives on the `Root` bone.** Vanilla Rolling_Long has a Root translation-Z curve (5 keys).
  `bfres_anim.replace_animation` used to drop curves of every bone it wasn't given, which wiped root
  motion out of every swapped animation.
  - It now keeps the original curves of `KEEP_BONES = Root, Ph_Pred_Ankle_L/R` when the frame count
    is unchanged.
  - The ball roll travels again: about 2.3 m in 9 updates (headless log, `[roll] pos`).
  - The same fix also restores any root motion in the door/chest/climb swaps.
- **CCT probing results:**
  - `getLinearVelocity` = cct->[0x20] + 0xe0 is a read-back from the physics world each step
    (f_80049f10 calls the world's vtable +0x1d0). setLinearVelocity (f_80559cd0) is never called
    for the player during walk/roll, and a set from the roll update is overwritten the next step.
  - CCT +0x58 is a getter (cct->[0x28] + 0x1cc). The player's motion request enters the physics
    character upstream, from root motion. Not found yet.
- **Still open for the 1:1 Player_Action_96 port:**
  1. **Keep the roll action alive while A is held.** No roll node method returns "done"; the
     action's end comes from the action system / SelectorPlayerRolling, probably via the x21 timers.
  2. **Inject MM's speed and steering.** Either find the root-motion -> CCT step and replace its
     displacement, or keep calling setLinearVelocity after that step.
  - The hook framework, player actor, CCT access, guest calls and ground test are all in place
    (§34).

## 36. Roll speed control solved; roll duration is animation-sequence driven (2026-11-09)
- **CCT forward motion block:** `p28 = cct->[0x28]`.
  - +0x1c0 move direction (vec3), +0x1d0 up, **+0x1d8 forward speed (m/s)**, +0x1dc rate (1.0),
    +0x1f8 resulting velocity.
  - This matches the engine nodes Set/GetForwardSpeedMps. The CCT moves along the direction at that
    speed, with collision.
- **Writer: f_809552f0, the shared player "apply movement to CCT" step** (the roll update calls it;
  ~52 Hz, walking too). A post-hook that overwrites +0x1d8 controls the roll speed:
  - 9 m/s -> 4 m in 16 updates; 3 m/s -> 4.7 m in 82 updates.
  - Overlay test mode: `HOOKSHOT_ROLL_SPEED=<m/s>`.
  - **This is the injection point for MM's linearVelocity**, and for MM yaw via +0x1c0.
- **Why the roll ends:**
  - The native roll covers about 4.5 m whatever the speed or clip length (50- vs 200-frame
    animation). Standing still with no root motion, it lasts the animation's length (200 updates).
  - So it's the animation-sequence (AS) system's Rolling state, which plays in sync with distance
    moved. Names in the image: `QueryASGetHoldEventRolling`, `RollingEnd`, `RollingAnimeRate`,
    `RollingToRunType`.
  - Not decided by the roll node: its tick (vtable +0x80 = f_806ca120) always returns 0.
    SelectorPlayerRolling (f_82b51500) / SelectorPlayerSchedulerRolling (f_804c8880) are generic
    "tick the selected child" selectors; the child index (node+0x1e) is chosen by state elsewhere.
  - No distance/progress counter in the node, actor or x21 structs (probe 4); it must be in the
    AS / animation player object.
  - Data: AS/Player_Child.root.asb (2.2 MB, references Rolling_Long) and
    AnimationEvent/AsNode/Player_Child.root.baev.
- **Next for hold-to-roll:**
  - Locate the AS player's current frame/progress for the Rolling state (data-watch the anim frame
    value, or map QueryASGetHoldEventRolling's native implementation: getName 0x829b2d30, vtable
    0x84a7b8c0, node methods 0x80c3a8a0...).
  - Then hold the progress (or rewind it) while A is held, and release on A up -> RollingToRun
    (uncurl), as in MM func_80857950.
  - Fallback: chain rolls by re-pressing A on the roll's leave (f_809590b0).
- **Animation-sequence (AS) access (2026-11-09):**
  - AS player = `actor->[0x270]->[0x40]->[0x18]->[0x18]`: active slots array at +0x20 (stride
    0x48, count +0x18).
  - slot->[0]->[0x58] = node state. Flags at +0x44: bit 0x400000 = hold event active. Events at
    +0x28 (stride 0x48, count +0xa0); the Rolling hold event has type (+0x28->+0x2c) == 0x36,
    value byte +0x41 (QueryASGetHoldEventRolling, f_829b29a0).
  - Layer = AS player->[0x40] (vtable 0x84c49818). Its vtable +0x80 (f_837d9cc0) is
    **setFloatVariable(index, value)**: value array ->[0x28], change counters ->[0x58]. The roll
    update writes a direction value into variable x21+0x119c every frame. It's not a playback rate.
  - A looping ball clip did not extend the roll (7 updates). The Rolling state's end is defined in
    AS/Player_Child.root.asb (states / transitions / events), which isn't parsed yet.
- **Release safety:** every experiment in goron_hooks.inc is behind an env var. With none set,
  InstallModHooks installs nothing, so normal play is unchanged.

## 37. MM Goron roll running natively (overlay, on by default) (2026-11-09)
- **`overlay/goron_roll.inc`:** a verbatim port of MM Player_Action_96's grounded path.
  - Covers func_80836AD8 / func_80836B3C entry, func_80857A44 after the curl (spin = v*500,
    B08 = v), speedTarget = stick*0.8*0.14 (cap 6.0) *2.6, the B0C bounce terms, func_8083A4A4
    braking, AsymStepToF(linearVelocity, target, spin*0.0003, 0.6), turn rate |v|*20+300 via
    ScaledStepToS (gFramerateDivisorHalf 1.5), the spA4/spA0 carry-over with the 0.3 friction,
    B08 = |...| capped at 18, and spin AsymStepToS(av2, v_target*900, 0x7D0/0x3E8, 0x4B0).
  - z_lib.c helpers copied from git objects (src/code/z_lib.c isn't in the sparse checkout).
  - Ticked at a fixed 20 Hz from real time.
- **Wiring** (`goron_hooks.inc`, default on; `HOOKSHOT_GORON_ROLL=0` disables):
  - **Roll start:** the roll update (f_80953e80) identifies Link and starts the MM state with the
    current speed and facing.
  - **Each movement step:** the post-hook on f_809552f0 ticks MM and writes the CCT forward speed
    (p28+0x1d8, 1 u/f = 0.472 m/s) and the direction (p28+0x1c0) from MM's currentYaw.
  - **Input:** stick from the npad_states hook. The camera right axis is read the way Hookshot's
    oot_gameplay_view does (singleton 0x84ca6bc0 ... view+8/+16). Stick yaw = atan2 of
    (rx*sx + rz*sy, rz*sx - rx*sy).
  - **Hold:** the native roll ends after ~4.5 m. When it ends with A held (leave hook f_809590b0),
    A is reported released for ~34 ms, and the game starts the next roll itself.
    - Controller reads (host `npad_states`, behind the GetNpadStates jmp stubs) return a 16-sample
      history keyed by sampling number. A sample once reported released must stay released in
      later reads, or no press edge is seen.
  - **Release:** A up -> the controller stops (MM func_80857950). The native roll's tail and
    roll-to-run (uncurl) follow.
- **Measured (headless, Hyrule Field arena via `HOOKSHOT_TELEPORT=0,153,80` + `HOOKSHOT_TELEPORT_FILE`):**
  - Entry 9.5 u/f, then 15.6 u/f (= MM cap 6*2.6, 7.36 m/s).
  - Steering follows the stick.
  - Stick released: 15.6 -> 10.2 -> 4.2 -> 0 (MM's 0.6/frame).
  - A released: ends.
- **Test tools:**
  - Teleport: CCT changePosition f_806d2530, called *before* f_80156ae0 on the roll-identified
    actor. The vtable test alone matched another actor.
  - `drive_test.sh` gained `touch:<file>`.
- **Body hiding in the roll animations is now by scale** (`goron3d.BODY_HIDE = "scale"`, all body
  bones 1e-3); the ball is still hidden by translation. Engine blends at chained joins now show a
  shrinking body instead of nothing.
- **Not ported yet:** spikes (magic), B jump + ground pound, wall reflection, slope terms,
  immediate uncurl on A release (pg_maru_change reversed), ball spin driven by av2 (the spin is
  baked in the animation), sounds/dust.

## 38. Hold-A = sprint in this game -> sprint plays the ball (v0.8.4, 2026-11-10)
- **User report:** in the OoT remake, holding the roll button (A) makes Link sprint ("Dash"). After
  a roll, a held A puts the player into the sprint, which played a normal running animation, so
  the roll animation looked broken.
- **Fix (MM-faithful):** Goron Link has no sprint (holding A keeps him rolling), so all 15
  `Move_Dash*` animations (incl. Brake/Curve 180, slopes, grass arm variants) are now the curled
  ball: `roll_animation` without curl, looping, spin/frame rounded to whole turns per loop.
  Swim_Move_Dash is untouched.
- **Overlay:** the roll -> sprint path now carries the hold by itself, and the MM controller keeps
  driving speed and direction through it (f_809552f0 also runs in the sprint). Measured: one native
  roll (9 updates), then the sprint as a ball at 15.6 u/f, steering, release -> stand.
  - The A re-press chaining is now opt-in (`HOOKSHOT_ROLL_CHAIN=1`).

## 39. The roll actually rolls now: MM's ball driver baked in (v0.8.5, 2026-11-10)
- **Why it looked like a slide:**
  1. The ball's bone (Ph_Leg_Support_Waist_L) gets its rotation overridden by the engine; its
     translation follows the clip. The built curve was correct: rot X 0 -> 4pi over Move_Dash.
  2. With the spin moved to Waist, the roll clips spun, but the sprint still didn't. A layer in
     the sprint overrides Waist's rotation.
  3. The spin now rides on **Skl_Root** (`goron3d.SPIN_BONE`). Waist and the ball keep identity, so
     the ball tumbles about the model's lateral X. Verified with 2-frame screenshots: hat ->
     upside-down curled face -> rock -> hat.
  - **Test pitfalls:**
    - MM's spin (0.897 rad/frame) at 7-frame screenshot spacing is ~2pi per shot: it looks frozen.
    - The 3DS ball model is a rock shell on one side and the curled Goron (hat, face, gauntlets) on
      the other. A spin about the wrong axis keeps the hat facing the camera. The texture and UVs
      are correct (checked by rendering the ball alone).
  - Hiding Waist by scale stops the spin reaching the ball (reverted).
- **MM's driver baked into the clips:** `goron3d.mm_roll_driver` simulates the 20 Hz loop:
  - av2 steps toward speedTarget*900 by 0x7D0 (0x4B0 down); shape.rot.x += av2.
  - func_808577E0 spring: unk_ABC toward |av2|*0.00004, B48 +0.08/-0.07, clamps.
  - Player_Draw scale: (1-ABC/2, 1+ABC, max)*1.15 on Skl_Root; centre raised by the Y scale.
  - At ABC >= 0, Y and Z scale are equal, so the scale commutes with the X spin and its order
    relative to the spin doesn't matter.
  - Rolling_*: from the end of the curl, entry v 9.53 -> 15.6.
  - Move_Dash* (sprint loop): steady state 14040 binang/frame (rounded to whole turns per loop),
    ABC 0.3.
  - Rolling_To_Run: steady, then uncurl.
- **Still approximated:** the spin rate doesn't follow the live speed (baked at MM's curve; the
  playback rate is the engine's), no lean (shape.rot.z) / B86[0] wobble / bounce-landing squash.
  A fully code-driven pose needs the skeleton pose buffer (not found yet; candidate scan in
  `HOOKSHOT_MTX_PROBE` was noise).
- **Tools:**
  - `run_headless.sh` guard limits are configurable: `HOS_SHOT_GUARD_FILES` / `_KB` (runs live on
    the project disk now).
  - `drive_test.sh` has `clean:` (drop screenshots so far) to keep dense captures to the moment of
    interest.

## 40. Code-driven pose (MM Player_Draw in code) + standing curl (2026-11-10)
- **Animation curves are converted at load:**
  - The engine never reads FSKA key data again after loading. A data watch on driver keys showed
    only the overlay's own writes.
  - So editing curves in guest memory can't animate anything (the "driver block" experiment,
    `bfres_anim.Driver` / GORONDRV, is kept but unused: `GORON_DRIVEN=0`).
  - The apparent driver success in the sprint was the spin still baked on Waist.
- **Link's per-frame local pose** (found via the ball bone's unique hidden translation; a data
  watch shows it written once per frame by f_814f9460 / f_801d7ac0 and read by f_81501400 /
  f_801d7040):
  - 96-byte record per skeleton bone: scale vec4, 3 rotation rows (vec4 each, row-major),
    translation vec4, pad.
  - **f_81501400(inst, first bone, end bone, ..) = local -> world.** [inst] = model resource
    (->[0x10] bone array, 0x58 stride, name ptr at +0, parent u16 at +0x22), [inst+0x18] =
    local records, [inst+0x20] = world matrices (64-byte). It is called in bone ranges.
  - Overlay `HookLocalToWorld` (pre-hook) recognizes Link's skeleton (bones 3 = Skl_Root, 253 =
    Ph_Leg_Support_Waist_L; cached by resource pointer) and writes the frame's pose override first.
    This is code-driven posing, as MM's Player_Draw does.
  - Pointer guards matter: other skeletons have fewer bones, and reading entry 253 of a short
    array crashed the game (ModuleSystemWorker5, read of 0xfffff00000000000).
- **Overlay MM animation player** (`GoronFrame`, once per frame for Link from f_80156ae0):
  - Link is identified with Hookshot's check (components+800 movement -> Player resource).
  - Idle: no override (the baked pg_wait now covers every standard idle variant `Wait_*`; Link's
    equipment picks the variant, which is why the old single "Wait" swap didn't always show).
  - **Curl (standing, MM func_80839F98):** A pressed with no stick and no speed ->
    func_80836AD8 + pg_maru_change 0->7 at 2/3 frame per 20 Hz tick; then func_80857A44 -> ball.
  - **Curl (moving):** the roll start -> 4-tick curl (MM plays landing_roll at 1.5x; approximated
    with pg_maru_change), then ball.
  - **Ball:** goron_roll::Tick (now with func_808577E0 squash, shape.rot.z lean, B86[0]/B28 wobble)
    -> Player_Draw transform on Skl_Root: RotY(B28) RotX(B86) RotY(-B28) RotZ(lean) RotX(spin),
    scale (1-ABC/2, 1+ABC, max)*1.15, centre raised. Waist identity, ball at the origin, body bones
    scale 1e-3.
  - **Uncurl (func_80857950):** A not held once curled -> pg_maru_change 7->0 at -2/3, speed 0
    (Player_Action_4), then idle.
  - Pose tables: `mods/030-GoronLink/goron_driver.bin` (v2), written by
    `goron3d.export_driver_tables` (retargeted pg_wait / pg_maru_change at MM native frames +
    bone indices).
- **Pose record layout corrected** (from f_81501400's code): +0x00 flags, +0x10 scale, +0x20/+0x30/+0x40
  matrix columns (R*S), +0x50 translation. The first version wrote rows from +0x10 and translation at
  +0x40, so Skl_Root's height never applied. The writer also clears the 0x0F000000 shortcut flags.
- **Verified in game (v0.9.0, re/runs/curl5):**
  - Standing: A -> curl (pg_maru_change) -> ball on the ground -> release -> uncurl -> idle.
  - Moving: roll -> curl -> code-driven ball (spin, MM squash 0.3 = wheel-like 1.3/0.85 scale) ->
    release -> uncurl.
  - No crash.

## 41. Uncurl vertex explosion fixed (v0.9.1)
- Cause: while curled, the body bones were hidden with scale 1e-3. Bones with segment scale
  compensation (SSC; e.g. Face_Root and the eye/brow bones under Head_2 = 104) divide by their
  parent's stored scale. On the first uncurl frame a parent still held 1e-3, so those children got a
  world scale of 1.35 * 1000 for a frame.
- Fix: no scale tricks on the body. In Ball mode the chain roots (`BODY_ROOTS` = Spine_1, Leg_1_L,
  Leg_1_R) are moved 1000 units down; body poses put them back at their bind translation
  (goron_driver.bin v3 stores root index + bind translation). The ball bone (no SSC children) still
  hides with scale 1e-4, written in full (scale, rotation, translation).
- Verified in re/runs/fix1 (shots every 2 frames, HOOKSHOT_POSE_DEBUG): standing curl -> ball ->
  uncurl and moving roll -> ball -> uncurl. No abnormal body bone matrices; no explosion in the frames.
- Smoothness: the drawn pose is interpolated between the last two 20 Hz MM ticks (InterpState with
  alpha = time into the next tick), for the ball (spin, lean, wobble, squash) and for the curl/uncurl
  frames, as Zelda64Recomp's frame interpolation does.
- Dense headless runs need `HOS_SHOT_GUARD_FILES=4000 HOS_SHOT_GUARD_KB=8000000` and a 1200 s timeout
  (`drive_test.sh <dir> 1200 2 -- ...`); with 300 s the game is killed before the steps run.

## 42. Spiked roll (v0.10.0, in progress)
- **Ported 1:1 (overlay/goron_roll.inc)** from Player_Action_96: charge (av1 counts up while magic idle,
  magic >= 2 and spin >= 0x36B0; at av1 >= 0x36: Magic_Consume(2, GORON_ZORA), unk_B08 = 18, B86[1] = 1),
  spiked state (speedTarget 18, av1 -> 4, B86[1] grows to 7; retracts 1/tick on A release, magic 0 or
  av1 == 4 && speed < 12; B0C clamped 0..0.9), sharp turn (|sp8E| > 0x3A98) drops spikes + 20 ticks of no
  input + spin 0, braking only resets av1 without spikes, uncurl blocked while B86[1] != 0 (func_80857950).
  MM magic: SETUP -2, then -1 every 10 game frames (z_parameter.c Magic_Update), Magic_Reset on end.
- **Spikes model:** MM3D `goron/model/lg_spike_model.cmb` (58 tris, uses the rock texture; UVs remapped
  into the ball atlas slot) rigid on spare leaf `Ph_Leg_Support_Waist_R` (bone 254, smooth 224) in the
  ball's frame; parked like the ball in the skeleton and all animations; goron_driver.bin v4 holds its
  index. Overlay scale = func_80124618(D_8085D540, B86[1]) -> 0.5, 1.0, then full.
  Not done: the charge glow (grt_01/02 translucent animated meshes), sounds, wall bonk losing spikes.
- **Magic meter:** GameData Int `PlayerStatus.MP` (murmur3 0x76b18200), `PlayerStatus.MPMax` (0x12d63a97),
  `PlayerStatus.Life` (0x31ab5580, quarter hearts), `PlayerStatus.MaxLife` (0xfbe01da1),
  `PlayerStatus.CurrentRupee`. Names found by hashing guesses (re/gdhash.py; GameDataList decoded to
  re/gamedatalist.pkl). Runtime table of {u32 hash, s32 value} pairs, e.g. MP value at guest
  0x105a4f01b4 = 240 (same address in 3 runs); MPMax 240 -> 240 per magic level (MM 48): scale 5.
  - BUT: writing it (tuner `poke`) does not change the HUD, even after the pause menu, and HOS_WATCH saw
    no reads in 8 s. It is a save mirror; the live meter is elsewhere (or the HUD needs the setter's
    change notice). Next: fall damage (teleport to 0,185,80) with `tuner:watch` on Life 0x105a4ee82c to
    catch the game's GameData writer/setter, then write MP through it (or find the live copy).
  - The overlay already locates the table at runtime (MagicScanThread: MP/MPMax/Life pairs within 64 KB,
    Life <= 80 rules out an index table) and uses scale 5; HOOKSHOT_MAGIC_FAKE=<n> gives a private meter.
- Tools added: tuner `snap <path>` (all rw guest pages; ~7.5 GB each), `findctx`, `peek`, `poke`,
  `watch <hex..>` (arms HOS_WATCH now); drive_test `tuner:<cmd>` and `write:<file>=<text>` steps; the
  teleport trigger file may hold "x,y,z" and is repeatable. re/memdiff.py diffs snapshots.
- Neither the Y spin attack nor B used magic in a test (Y = attack, X = Deku Stick, R = item wheel).
- Don't edit drive_test.sh or rebuild the overlay while a run is going (sh reads the script lazily).

## 43. Roll speed: MM's actor movement scale (v0.10.1)
- z_actor.c Actor_UpdatePos: pos += velocity * sActorMovementScale; Actor_SetMovementScale(framerateDivisor)
  makes it 3 * 0.5 = 1.5 at 20 fps. The port had left it out, so every roll speed was 1.5x too slow.
- Now goron_roll::kSpeedToMps = 0.0236 m/unit * 20 Hz * 1.5: normal top speed 15.6 -> 11.04 m/s, spiked
  18 -> 12.74 m/s (moving-roll entry speed converted back the same way).
- Measured in re/runs/spd1 (actor +0x31c position, logged once per second in ball mode): normal 11.3 m/s,
  spiked 12.9-13.3 m/s on a slight downhill. The character controller follows the commanded speed.
- The spiked speed path itself (speedTarget 18, accel spin*0.0003, turn rate |speed|*20+300, braking off while
  charging, cap 18) is MM's; outside Player_Action_96 MM applies no other speed change for spikes (only the
  camera: CAM_MODE_GORONDASH / GORONJUMP, not ported).
- The magic meter read 48 (240 / 5) and spikes charged after 50 ticks at full speed, as in MM.

## 44. Speed calibration on Link's run speed (v0.10.2)
- MM speed units are now mapped through Link himself: MM human Link runs at 5.5 (R_RUN_SPEED_LIMIT 550,
  z_player_lib.c D_801BFE14[PLAYER_BOOTS_HYLIAN]); this game's child Link runs at 4.5 m/s (re/runs/run1:
  CCT speed exactly 4.500, measured 4.3-4.6 m/s from position). kSpeedToMps = 4.5 / 5.5 = 0.818 m/s per unit
  (replaces the model-size estimate 0.0236 m * 20 Hz * 1.5 = 0.708 of §43).
  Cross-check: MM roll 6.583 / run 5.5 = 1.20; vanilla dodge roll (RollingMaxSpeed 5.625) / run 4.5 = 1.25.
- Goron Link's own limits: D_801BFE14[PLAYER_BOOTS_GORON] R_RUN_SPEED_LIMIT 600 (6.0; 500 indoors), so the
  normal roll's target is 6.0 * 2.6 = 15.6 (13.0 indoors); spiked 18.
- Verified (re/runs/spd3): normal roll 12.76 m/s commanded, 12.7-13.1 measured; spiked 14.73 commanded,
  14.2-14.75 measured. Ratio to the run: normal 2.84x, spiked 3.27x (MM: 2.84x, 3.27x).
- overlay/Makefile now lists the *.inc files as dependencies (a change to goron_roll.inc alone used to
  leave a stale .so).

## 45. Hookshot v2.0.14 update fix
- Symptom: the game crashed in the overlay's vkQueuePresentKHR hook (read of a bad g_HosPosixKeys).
- Cause: InitHookshotBridge used fixed v2.0.3 offsets into the Hookshot binary (hos_call_guest, hos_ctx,
  hos_sys_alloc, hos_posix_keys, the host-call table). The guest hooks were fine (resolved by name).
- Fix: all resolved at runtime: the four functions/keys by name (ResolveHostSymbol, .symtab); the host-call
  table (local `ncalls`/`calls`, entries 0x58 bytes, name at +0x10) decoded from hos_hostcall_target's
  `movslq ncalls(%rip)` (48 63 3d) and `lea calls(%rip)` (48 8d 3d). No fixed host offsets remain.
  The game's own guest addresses (0x84......) are unchanged by Hookshot updates.
- Verified headless on v2.0.14 (re/runs/upd1): hooks, bridge, standing curl, moving roll, spikes, model.
- PlayerStatus.MP's table moved (0x10673f01b4 now); the runtime scan found it.

## 46. Material shading after Hookshot v2.0.14 (v0.10.3)
- Hookshot v2.0.14 fixed the game's material shading. With it, our flat Gn2 (0,0,0) (§32's "matte") became
  a mirror: streaky environment reflections over the whole body (user screenshot, Lon Lon Ranch).
- So §32's "R = gloss" was a rendering bug of the old runtime; §28's reading holds: R = roughness,
  G ~ spec/cavity mask. Gn2 is now flat (210, 200, 0): rough, NPC-like (skin ~160, cloth 200-220).
- Verified headless (re/runs/shine1/close.png): matte rock skin, no streaks.

## 47. Goron drawn with a non-skin material (v0.11.0)
- Even fully rough with a low spec mask, the Goron looked waxy / plastic (pale, glowing, low contrast; user
  screenshots in sun and shade). Cause: Mt_Skin is Link's skin shader: samplers _a0 _n0 _gn0 _n1 _th0
  _slt0 _ao0 with a subsurface lookup (CmnTex_Chara_Skin_LUT_A_Slt) and thickness.
- Fix: the Skin_Body shape (all other shapes are hidden) is drawn with **Mt_PantsParts** (material 20):
  plain PBR samplers _a0 _n0 _rgh0 _ao0, same vertex attributes and the same skin weight count
  (FSHP +0x5a = 7). Its texture-name slots point at Player_Child_Skin_Alb (atlas) / _Nrm (flat) /
  _AO (white) (string pointers copied from Mt_Skin's list); Mt_PantsParts_Player_Child_Rgh is flattened to
  235 (BC4). goron_model.use_material; FSHP +0x52 = material index.
- Mt_Leather (same samplers, skin count 5) rendered flat unlit orange: no shader variant for 7 weights.
  So a material swap must match the shape's skin weight count.
- Verified headless (re/runs/mat2/close.png): saturated MM colours, matte, no waxy glow.

## 48. Goron roll sounds + camera (v0.12.0)
- **MM3D SFX are sequences:** JokerSound.bcsar sound info type 0x2203 = sequence sound (N64-style): start label
  in a shared CSEQ (file 0) + 3 banks (CBNK) -> wave archives (CWAR/CWAV DSP-ADPCM). New offline renderer:
  `mod_manager/csar.py` (CSAR/STRG/INFO/FILE, CWAV + DSP-ADPCM) and `mod_manager/cseq.py` (sequencer:
  notes/wait/prg/bank select b6/open track/jump/call/loop/vars/_if/_r/_v/ADSR/tempo; sampler with
  direct / ranged / indexed regions; ranged + indexed region refs are relative to the table start).
  The builder renders cseq.GORON_SFX into mods/030-GoronLink/sfx/*.wav (continuous ones looped).
- **Overlay mixer** `overlay/goron_audio.inc`: own SDL3 stream (float 48 kHz), MM's one-shot and "- SFX_FLAG"
  (refreshed every 20 Hz tick, fades 50 ms after the requests stop), Audio_SetSyncedSfxFreqAndVolume
  (param > 6: freq 1.1 vol 1; else t = p/6: vol 0.775 + 0.225 t, freq 0.9 + 0.2 t). HOOKSHOT_GORON_SFX=0 off,
  =1 forces on (also with HOS_AUDIO=0), HOOKSHOT_GORON_SFX_VOL, HOOKSHOT_GORON_SFX_DUMP=<raw f32> (tests;
  use SDL_AUDIO_DRIVER=dummy headless).
- **Triggers (1:1 with z_player.c):** func_80836AD8 GORON_TO_BALL (both curls); func_80857950 BALL_TO_GORON;
  shape.rot.x zero crossing ((av2 + old) * old <= 0) GORON_ROLL / GORON_CHG_ROLL synced to unk_B08; B86[0]
  zero crossing (spin 0) GORON_ROLL synced to sp54; BALL_CHARGE flagged while charging; CHARGE_DASH;
  CHARGE_FAILED; func_808576BC (|v_prev * 800 - av2| > 0x7D0: ROLL_DUST flagged, > 0x1770 also SLIP).
  Verified headless (re/runs/sfx1): curl/uncurl, ~4 roll sounds/s at full speed, charge -> dash -> CHG_ROLL
  -> failed, audio in the dump, no clipping. Not wired: ROLLING_REFLECTION (no wall bounce yet), floor
  variants (ice/snow/sand), positional panning.
- **Camera:** post-hook on Hookshot's `hos_oot_free_view(ctx, view)` (host function called once per frame from
  f_804e48c0 with the gameplay view; trampoline re-does its 3 pushes; its `test rsi; je rel32` is done in the
  hook). View object (vtable 0x84c18a88): +0x08 3x4 view matrix rows (right, up, back; t = -row.eye), +0x38 /
  +0x44 eye / target floats, +0x50 up, +0x60 eye doubles, +0x78 target doubles, +0x9c/+0xb0 and +0xdc/+0xf0
  projection [0][0]/[1][1], +0x128 near 0.1, +0x12c far 25000, +0x130 FOV 0.8727 rad (50 deg), +0x134/+0x138/
  +0x13c sin/cos/tan(FOV/2), +0x140 aspect. Writes mark `a64_dirty` pages like Hookshot.
- **MM camera data** (z_camera_data.inc.c, NORMAL0 / CAM_FUNC_NORMAL1): normal (-20, 280, 320, 10 deg, fov 60),
  GORONDASH (50, 160, 255, -2, 92), GORONJUMP (-10, 160, 400, 0, 80). Applied as MM's mode change on the game's
  camera: look-at raise keeps MM's angle (dy / ~300 units scaled to the game's ~3.8 m distance), distance
  clamped like ClampDist2 (300 -> 255: x0.85), pitch -12 deg, FOV x92/60; blend at Normal1's 0.05/frame (x1.5).
  Dash while spiked in ball mode (GORONJUMP needs the airborne roll, not ported). Verified re/runs/cam3.
  No camera collision is added (the game's own collision ran before our offset).

## 49. Sound stacking + camera FOV fixes (v0.12.1)
- **Sounds stacked:** the mixer added a new voice per request (roll sound ~4/s, 0.7 s long). MM's
  AudioSfx_ProcessRequest keeps one entry per sound per source: a repeated one-shot (id carries SFX_FLAG)
  interrupts and restarts it; a "- SFX_FLAG" request only refreshes it; the player's sounds share a few
  channels. Mixer now: one voice per sound (restart with a 5 ms declick fade, 2 ms fade-in), continuous sounds
  refreshed, at most 4 live voices. The ROLL sequence's three hits are inside its sample, not a loop.
  HOOKSHOT_GORON_SFX_LOG=1 logs requests.
- **FOV had no effect:** the view embeds a **sead::PerspectiveProjection at +0x90** (+0x98 mDirty / +0x99
  mDeviceDirty, +0x9c matrix, +0xdc device matrix, +0x128 near, +0x12c far, +0x130 fovy, +0x134 sin, +0x138 cos,
  +0x13c tan of fovy/2, +0x140 aspect); the matrix is rebuilt lazily from the cached tan when dirty. The camera
  update f_804e48c0 sets the FOV (setFovy) at +0x13958, after Hookshot's free-view call (+0xd570), so writes in
  the free-view hook were overwritten. f_8035a8c0 (projection rebuild) is generic (~4 calls / frame; the view is
  not an argument). Fix: post-hook on f_804e48c0: fovy *= MM ratio, sin / cos / tan, dirty flags.
  The eye / target / view-matrix change stays in the free-view hook (it is not overwritten).
- Verified: re/runs/cam9/ab.png (forced dash camera: clearly wider, zoomed out) and re/runs/cam10 (spiked
  roll: pull-back + wide FOV when the spikes come out, eases back after; sound level steady).
- Test switch: HOOKSHOT_CAM_FORCE=<file> forces the dash camera while the file exists; HOOKSHOT_CAM_DEBUG=1.

## 50. Seamless rolling sound (v0.12.2)
- NA_SE_PL_GORON_ROLL (player sfx 0x990, carries SFX_FLAG = restart on re-request) is re-triggered every revolution
  in MM. Its sequence: track 0 a 0.2 s click (bank 0 prg 0), track 1 a **looping** 1.2 s rumble (bank 2 prg 28,
  loop 7168..38279), track 2 a 2 s one-time texture (bank 2 prg 1). The retrigger comes before the held rumble note
  ends, so in MM it sounds like one continuous rumble. Rendering the sequence once with released notes and replaying
  that clip per revolution gave a grating machine-gun stack.
- Builder: cseq.GORON_SFX mode "sustain" renders with every note held (Sequencer(sustain=True)), finds where the
  level settles (only looping layers left) and writes <name>.loop with that sample index (ROLL 2.25 s, CHG_ROLL
  0.60 s); the seam is crossfaded. Loop regions measured steady (100 ms RMS within +-12 %, no pulses).
- Overlay: clips read <name>.loop; the roll sound is held (refreshed each tick) while |av2| >= 0x1555 (MM's
  retriggers overlap), CHG_ROLL when spiked (the other fades in 100 ms), pitch / volume = Audio_SetSyncedSfxFreqAndVolume
  (unk_B08). Slower than that: one 0.4 s hit per revolution (shape.rot.x zero crossing), as MM's triggers stop
  overlapping. Verified re/runs/sfx2: one start, steady level through the roll, swap at spikes.

## 51. Sequence synth: tie, portamento, vibrato (v0.12.3)
- The grating sound "above a certain speed" was NA_SE_PL_GORON_BALL_CHARGE, which starts once av2 >= 0x36B0 (full
  speed, A held, magic >= 2). Its tracks: transpose +48, tie on (c8 01), vibrato (ca depth / cb speed / cd range),
  portamento from key 36 (c9 24, cf time), then an endless loop (d4 00 ... fc) of one 200-tick note. Without tie
  support every loop pass started a new never-released voice (a new flat layer every ~2 s).
- cseq.py now implements: tie (later notes continue the held voice, gliding; tie off releases it), portamento (from
  the porta key, transposed like notes, or from the previous note; over porta_time ticks, 0 = the whole note),
  pitch LFO (depth/128 * range semitones, speed * 0.390625 Hz, delay e0 ticks). Used by TO_BALL, BALL_TO_GORON,
  CHG_ROLL, CHARGE, CHARGE_DASH, CHARGE_FAILED, SLIP, ROLLING_REFLECTION.
- Check: BALL_CHARGE creates 2 voices in 6 s (one per track), level flat (0.090 RMS each second).

## 52. Spike-charge fire (v0.13.1)
- **MM:** Player_Draw (curled branch, z_player.c ~12666): while unk_B86[1] < 3 and av1 >= 5, after the ball (and
  spikes), Matrix_Scale(1, k, k) with k = (av1 - 4) * 0.02 while charging, 0.65 once the spikes appear; alpha
  200 * k, then 255 - 85 * B86[1]; env colour (155, 0, 0); object_link_goron_DL_0127B0 and DL_0134D0 with animated
  materials (scrolling textures). MM3D: goron/model/grt_01_model.cmb (224 tris) + grt_02_model.cmb (128 tris),
  grayscale masks grt_tex02/03 (32x64), goron/misc/grt_0*_model.cmab (texture scroll, one wrap per 30 frames;
  grt_02 also an intensity flicker 1 / 0.5 / 0.8 / 0.6 / 1 / 0.4).
- **Port:** the shells are rigid on Ph_WaistBelt_1 (bone 255, smooth 225; root of the skirt physics, unused by the
  Goron mesh), laid out in the ball's frame, in the hidden shape Pants_Main_low__Mt_Cloth_Pants (skin weight count
  7, 236 shared vertices of 568) drawn with Mt_PantsParts (the Goron body's alpha-masked material). The masks are
  stamped into the Goron atlas at (0,202) / (36,202) as 32x54 tiles, tinted (155 + 100 m, 25 + 140 m^2, 8 + 40 m^3),
  alpha = mask > 0.33 (BC1 punch-through; bntx.encode_bc1 now supports 3-colour + transparent blocks; the rest of the
  atlas is forced opaque, MM3D's ball texture carries alpha). The scroll is a turn about X (1 / s).
  Overlay: driver v5 = fire bone; shown in ball mode under MM's condition with MM's k (Y/Z scale); parked in the
  body poses and also in Idle (WriteIdlePose), because the skirt physics otherwise puts the bone on the body.
- **Dead ends (kept for reference):**
  - Mt_EyeShadow_test (the only translucent material, render mode "seal") draws as a darkening decal; its parameter
    block (names hashed) did not give it colour.
  - C_Eyelid_U__Mt_Face never drew (blink-visibility shape) until... the real bug: fire_mesh() ran after
    hide_ball_in_skeleton() had parked the fire bone, baking the vertices 1000 m away. It must run before.
  - Skinned shapes are culled by their skin-bone list (FSHP +0x20 u16 [+0x58 count]) and bounds (+0x38 boxes,
    +0x40 spheres): goron_model._set_shape_bounds points the list at the fire bone. Vertex matrix indices are global.
  - Dithered 1-bit alpha at 32x54 texels reads as scattered diamonds; a threshold gives solid flame tongues.
- Not reproduced: MM's partial transparency / fade (1-bit alpha) and grt_02's flicker; the size ramp is MM's.
- Test switch: HOOKSHOT_FIRE_FORCE=1 (fire always shown at full size in ball mode).
- Also: the magic-meter scan now retries every 3 s until it finds PlayerStatus.MP (it can run before the save).

## 53. Ground pound (v0.14.0) + damage research start
- **Ported (goron_roll.inc), z_player.c Player_Action_96:** grounded branch order func_80857AEC (landing) ->
  av1 == 2 recovery (unk_B8A-- each frame, unk_ABC = 0, unk_B48 = 0.14; av1 = 4 at 0) -> attack press with no spikes
  -> func_80857640(14.0, 0x1F40) (velocity.y 14, linearVelocity 0, spin >= 0x1F40, av1 = 1, unk_B48 = 1) -> normal
  roll. Air branch: shape.rot.z -> 0, B86[0] = 0, unk_B48 += vel.y * 0.005; av1 == 1: rising, if vel.y + g < 0 then
  vel.y = -g (apex at exactly 0); falling: unk_B8A = 10, g = -0.2 while vel.y > -1 (hang), else -10 (slam);
  unk_B08 = linearVelocity. Gravity reset to REG(68)/100 = -1.4 every update (func_8083BF54, Goron boots row),
  terminal -20; Actor_MoveWithGravity after the action (pos += vel * 1.5). func_80857AEC: av1 1 -> 2,
  Player_RequestQuakeAndRumble(NA_SE_PL_GORON_PUNCH), spin 0, unk_B08 0. unk_B10[0] (glow) stepped (not drawn).
  Uncurl also on (av1 == 3 && vel.y < 0).
- **Height:** the CCT ignores a vertical move direction (+0x1c4: tested, re/runs/up1). The jump height is set with
  the CCT's changePosition (f_806d2530, as Hookshot's fly cheat) before each step: take-off point + MM height
  (MM units * 0.818 / 30 m), interpolated per frame. MM stops horizontal motion for the whole jump, so he lands on the
  take-off point (landing = height back to 0). Apex ~ 115 units = 3.1 m.
- **Input:** MM's attack button B = this game's Y (Y = attack here, B = jump). Y edges go to the next MM tick; Y is
  hidden from the game while rolling (npad filter, bit 3).
- **Sounds:** NA_SE_PL_GORON_BALLJUMP (jump), NA_SE_PL_GORON_PUNCH (landing) added to the rendered set.
- **Quake:** Player_RequestQuake(27767, 7, 20) as a decaying vertical camera perturbation in the camera hook.
- Verified (re/runs/pound1): roll -> Y -> jump (BALLJUMP) -> hang/slam -> "lands (phase 1)" -> PUNCH -> phase 2
  recovery -> phase 4 roll; the frames show the ball high in the air.
- Not done: white shockwave effect (EffectSsBlast), EN_TEST spawn, rumble, the glow colour (unk_B10[0]).
- **Damage (not done yet):** MM: av1 == 2 -> Player_SetCylinderForAttack(DMG_GORON_POUND, 4, 60) +
  Actor_SetPlayerImpact(PLAYER_IMPACT_GORON_GROUND_POUND, 2, 100); spikes -> (DMG_GORON_SPIKES, 1, 25);
  rolling -> (DMG_NORMAL_ROLL, 1, 25). This game: attacks are native AINB nodes (ExecutePlayerBasicAttack vtable
  0x849dcd08, ChargeAttack 0x849e1af0, DodgeAttack 0x849e3280: methods 0x827f8210 / 89f0 / 8280 / 8530 / 8990);
  no attack-sensor switches in the ASB or the .baev (those are sound cues, incl. RollAttack_01..04,
  RollAttackDamage_*). The hit sensor is enabled in native code: next, trace a sword swing's hit to the
  function that registers an attack against enemies, then call it from the roll / pound.

## 54. Damage research with a live enemy (Kokiri Forest Deku Baba save, 2026-11-10)
- Test save: in front of an Enemy_Dekubaba_Withered (Kokiri Forest path; Link ~(40.4, 113.6, -4.8), Baba 4 m ahead).
  The Goron punch (Y) kills it: the game still swings the hidden sword ('Weapon' at-sensor), hit spark + death fx.
- **Actor names:** actor->[8]->[0x20] = resource name string (e.g. "Enemy_Dekubaba_Withered", "Npc_Kokiri_Mido_Child",
  "Obj_TBox_A_01" at +0x260 for chests). Overlay probes: HOOKSHOT_ACTOR_LIST=1 (name + position of every actor seen
  by f_80156ae0); HOOKSHOT_ACTOR_DUMP=<name> + HOOKSHOT_ACTOR_DUMP_FILE=<trigger> (trigger contents: an output path
  = dump actor + component table + objects; "watch" = HOS_WATCH its life; "watchc=<slot>,<field>"; "life=<n>").
- **Life:** comps (= actor->[0x270]) +0x408 -> object +0x60 = current life (s32; this Baba 2, from the
  Enemy_Dekubaba LifeParameters MaxLife 2 in its pack). Read every frame by f_80a599b0 (3 sites; likely the life
  component's damage / death processing). **Writing 0 does not kill it** (no death): death comes from the damage
  event, so a real hit has to be injected.
- **Player attack data:** Player_Child AttackParam has AttackType "ChargeAttack" on at-sensor "Body"
  (DamageType Body, Weak) = the spin attack's whole-body hit: the natural one to drive the roll / pound damage.
  Sensors: Phive/RigidBodySensorParam/Player_Atk_AtkPlayerBody (layer PlayerAttack). Attack-type hash table at
  0x83f14f88 (ChargeAttack 0x29f1f485 next to the player's sword attack types).
- Generic node ExecuteSensorHitSetupAttack (params IsEnableAttack / AttackType / AttackDirection / ExtraAttributes;
  vtable 0x849f4170; enter/update -> f_808f28c0 -> f_808f29c0, a 20 KB lifted function) arms an attack on an actor's
  SensorHit component: the API to reach, with the player's SensorHit and "ChargeAttack".
- On a hit the Baba's component at comps+0xf80 gains a list entry (+0x758 count 0 -> 1, two heap pointers at +0x7d8 /
  +0x7f0); written by f_804fe5d0 (+0x7d, +0xe8), read by f_80ebba50, f_8111de60, f_800c6940 (per frame). A hook on
  f_804fe5d0's entry saw no call at the punch (the write is reached another way).
- Next: hook f_808f29c0 / find the SensorHit "setup attack" core and call it for the player with ChargeAttack while
  rolling (spiked) and for the pound's recovery frames; or trace f_80a599b0's damage input back to its producer.

## 55. Wall bonk / reflection + MM attack states (v0.15.0)
- **func_80840A30 (rolling branch) + Player_Action_96:** wall touched, unk_B08 >= 12 and
  |currentYaw - (wallYaw + 0x8000)| < 0x1C00 (head-on) -> linearVelocity = -linearVelocity * 0.1, velocity.y 10
  (func_80834CD0), Player_RequestQuake(33267, 3, 12), NA_SE_PL_BODY_HIT; spikes lost with av1 = 3 (uncurl once
  falling). Otherwise (glancing, unk_B08 >= 12): unk_B0C += unk_B08 * 0.05, yaw mirrored off the wall (difference
  snapped to 0x200), home / shape yaw = it, unk_B8C (steer lock) = 4, NA_SE_IT_GORON_ROLLING_REFLECTION.
- **Wall detection (no BGCHECKFLAG_WALL from the engine):** each MM tick, the commanded displacement (last tick's
  linearVelocity along last yaw) vs the actual one; wall if the blocked part > 35 % and the forward progress < 90 %;
  the blocked direction points into the wall, its reverse is MM's wallYaw. 4-tick cooldown, ground and av1 >= 4 only.
  Verified in the Kokiri Forest path (re/runs/wall3): reflections and bonks with their sounds.
- **Quakes:** Player_RequestQuake parameters per request (pound 27767/7/20, bonk 33267/3/12).
- **Attack states:** Player_SetCylinderForAttack at the end of each tick: pound recovery (av1 2) DMG_GORON_POUND r 60,
  spikes DMG_GORON_SPIKES r 25, else DMG_NORMAL_ROLL r 25 (MM units; 25 = 0.6 m, 60 = 1.44 m). Logged as [attack]
  on change: the hook point for the damage call. Plan (user): Megaton Hammer damage (the Player_Child AttackParam
  has DamageType Hammer entries, e.g. on at-sensor "Punch_L").
- Leaving the roll while spiked resets the magic state (MM's Action_96 exit: Magic_Reset).
- The airborne height override keeps the CCT's own X/Z (the bonk's reverse drift).

## 56. Wall detection fixed (v0.15.1)
- The position-based check (§55) fired on flat ground: positions sampled at the 20 Hz MM ticks don't line up with
  frame timing, and bumps / step-ups shave a tick's progress.
- Now: the character controller's resulting (post-collision) velocity, p28 +0x1f8 (x) / +0x200 (z), against the
  velocity commanded last tick; wall when the blocked part > 35 % of the command and forward < 90 %, on two
  consecutive ticks (blocked vectors summed for the normal), command > 4 m/s, ground, av1 >= 4, 4-tick cooldown.
- Measured: open ground (re/runs/wall4) blocked 0-0.3 m/s (one 1.3 bump) vs threshold 4.5 at full speed: no
  detections. Kokiri path walls (re/runs/wall5): blocked 2.8-4.2+, detected; bonks at >= 12.
  HOOKSHOT_WALL_DEBUG=1 logs command / result per tick.

## 57. Walls from the character controller's own contact normal (v0.15.2)
- Velocity-based detection (§56) missed real walls (the CCT slides along / climbs some without losing speed).
- **The CCT stores the wall contact normal:** p28 (= cct->[0x28]) +0x150 (x, y, z), zero when nothing is touched
  (found by dumping the CCT blocks standing free vs walking into the Kokiri path wall: (0.881, 0, -0.473), opposite
  to the push direction; +0x15c holds a copy, +0xe4 is the current contact normal, (0, 1, 0) on flat ground).
  HOOKSHOT_CCT_DUMP_FILE=<trigger> dumps the CCT blocks (contents = output path).
- Detection = MM's BGCHECKFLAG_WALL + actor.wallYaw: |normal| > 0.5, steep (|n.y| / |n| < 0.6),
  wallYaw = Math_Atan2S_XY(n.z, n.x), and only while travelling into it (the CCT keeps the contact for a tick
  after a reflection, which double-reflected). Then func_80840A30 / the reflection decide as in MM (speed >= 12).
- Verified: Kokiri path (re/runs/wall9) reflections and bonks with exact normals; village roll (re/runs/wall8)
  one house hit.

## 58. Stale wall fix (v0.15.3)
- User: after one bonk, rolling the same way again bonked on nothing, until another wall replaced it.
  p28 +0x150 is the LAST wall normal and is never cleared.
- Now p28 +0xe4 = the CURRENT contact normal ((0, 1, 0) on open ground, the touched surface's normal while touching;
  verified in re/runs/wall10: walls for a few frames, then back to (0, 1, 0)). Sampled every frame (WallSample) and
  latched until the next MM tick (a graze lasts only a few frames). Same steepness / travelling-into tests.
- Verified re/runs/wall11: distinct contacts on the Kokiri path, reflections and a bonk, no repeats.

## 59. Bonk hop drawn only (v0.15.4)
- User: after a bonk he stayed stuck to the wall (colliding even when rolling away) until uncurled and walked off.
- Cause (most likely; not reproduced exactly): the bonk's hop (func_80834CD0 10.0) moved the CCT up with
  changePosition every frame, which skips collision; beside uneven / overhanging rock (contact normals with
  n.y < 0 seen on the Kokiri rocks) that can leave the controller embedded in the geometry.
- Now the bonk hop sets State.visualHop: MM's arc is simulated as before but only added to the ball's drawn height
  (Skl_Root translation y); the CCT stays on the floor and resolves collisions. The ground pound keeps the real
  height (open space; the camera follows it).
- Verified (re/runs/stuck4, the boulder near the new Hyrule Field spawn): bonk at 15.6, hop drawn, land, turn and
  roll away at full speed (then a spike charge). HOOKSHOT_STUCK_DEBUG=1 logs position / state per MM tick.
- Note: the newest save now spawns in Hyrule Field near the castle (yaw f774), not the Kokiri path.

## 60. Airborne roll hops with real height (v0.15.5)

User: "I want it 1:1 with MM. In MM, you can actually use wall bonks to gain height." The visual-only hop from §59 (section 59) is reverted:
bonk and ground-pound hops move the CCT to MM's real arc height again.
- Each airborne frame (actor-update hook): cct+0x2d4 (the CCT's own airborne fall speed) is zeroed, then
  changePosition(x, g_GroundY + airY + g_AirCorr, z).
- The CCT's step settles a forced height low, and the gap grows in the air (0 at take-off, ~0.035 m per MM tick, even
  at a still apex), so something else keeps pulling the ball down. g_AirCorr is an integral correction, reset to 0 at
  take-off. In free flight, each MM tick does corr −= 0.6·(actual − target two frames back), clamped to [0, 2].
  (The actor+0x320 read at a tick reflects the target handed over two frames earlier.)
- Collision (MM bgcheck stand-in), checked before each MM tick, with tRise = target change and aRise = actual change
  since the previous tick:
  - floor (a ledge too): falling and (actual > target + 0.15, or tRise < −0.1 and aRise > 0.25·tRise). Land there,
    g_GroundY = y.
  - ceiling: rising and tRise > 0.1 and aRise < 0.25·tRise → velY = 0.
  - Testing "below the target" for a ceiling gave false hits at every take-off. Small thresholds (0.03) gave a false
    floor during the ground pound's hang.
- Verified (re/runs/stuck11, stuck12a/b): bonk peak 1.20 m above take-off (arc target 1.25; MM ~1.4). Ground pound
  arc peak 2.6 m. Two identical runs with no false floor/ceiling; slam lands natively.
- HOOKSHOT_AIR_DEBUG=1 logs per frame (actual, target, airY) and per tick (actual, target history, velY, corr).
- Open: the source of the growing sag (not 0x2d4, not p28+0x1fc). Real ceilings and ledge landings are untested
  in-game.

## 61. Song of Healing mod (040-SongOfHealing, v0.2.0): presented natively through a host song

Play it with Y A R Y A R (B A F B A F; D-pad left takes the ocarina out). The game shows its own presentation:
camera, staff panel with the song's notes, "You played the Song of Healing!", Skip, replay.

**How songs work in this game**
- MelodyType is a fixed native enum: ZeldaLullaby 0 .. ScarecrowSong 12, 13 = none. A value outside it (14) gets no
  presentation at all; 13 is turned into 0 by the matcher's caller.
- Everything is looked up by type:
  - staff notes: MelodyScoreBook (GameParameter/MelodyScoreBook, note names D F A B HighD as murmur3 hashes);
  - title: LayoutMsg/Ocarina_00.msbt Title_%02d, inserted into EventFlowMsg/Dm_SY_Ocarina Talk_00 by tag 201.1;
  - replay: f_8337b730(sound, type, kind) starts "Melody_" / "ExampleMelody_" / "ScoreExampleMelody_" + type name
    -> Stream/Ocarina_<Name>.bwav (defined in slink2 .bslnk and Ocarina.bars);
  - event: Event/EventFlow/Dm_SY_Ocarina.bfevfl.
- OcarinaMgr (vtable 0x84bc1978, instance 0x1066df3210 every boot; f_832b4df0 = per-frame update, x0 = this,
  x1 = pad state):
  - +0x38 notes played; +0x138 u32[8] note ring (1 + pitch-table index: D 4, F 7, A 11, B 13, HighD 16), write
    index +0x258; +0x158 / +0x25c a second ring;
  - +0x220 matched type (13 none), read live by the staff / title UI and by flow queries;
  - +0x230 mode (1 free play uses f_832b7400, 3 prompted uses f_832b7600); +0x264 u8[13] learned flags.
- Matcher f_832b7400(mgr, MelodyType* out) -> bool (the symbol starts at the stub 0x832b7400, not 0x832b7418).
- Score book: singleton *0x84e8c7a8, +0x20 -> f_832b3f20 -> f_832b3f70 = list (f_80021a20 count); a score's type via
  f_82fe3df0, notes via f_832b3fd0 (-> +0x18 u32[]).
- QueryMelodyTypeForNormal = f_82a7c980 (x0 node): reads mgr+0x220, 13 -> -1.
- Controller: A = A, X = HighD, Y = B, L = D, R = F.

**Streams**
- A stream sound plays from the BARS's prefetch BWAV (header + first 14336 samples), then reads the stream file at
  the offsets in that prefetch header, with its codec, coefficients and sample count. The stream file's own header
  is not read (a PCM16 file played as noise for the host's length).
- The stream file is opened by nn::fs::OpenFile each time it plays
  (Hookshot: imp__ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci, path in x1).
- BWAV channel record +0x30 = offset in the stream file, +0x34 = offset in this file. bfotf fonts: 8-byte header,
  XOR key a6 01 85 02.

**The mod** (host = Zelda's Lullaby, type 0; overlay/song_of_healing.inc, mod_manager/build_song_of_healing.py)
- Matcher post-hook: no native match and a ring ends with the song -> report type 0. Until the presentation is over:
  - the host score's six notes are swapped for ours;
  - patches.bin swaps are applied: the host's prefetch BWAV in the loaded Ocarina.bars (our header + first samples)
    and the Title_00 text (tag + "Zelda's Lullaby" -> article byte 1 + "Song of Healing", same length). They are
    found by a 2.8 s background scan of resident guest memory when the ocarina first comes out, and re-checked
    before use;
  - OpenFile gets Ocarina_HealingSong.bwav in place of Ocarina_ZeldaLullaby.bwav (path rewritten in place for the
    call). The file is the mod's own romfs stream, DSP-ADPCM with the host's coefficient table
    (mod_manager/bwav.py: decoder, encoder, replace_stream).
- Replay = MM3D NA_BGM_OCA_CURE_SEQ notes (B A F, B A F, B A E D E, tempo 110, one ocarina track) played with the
  game's Ocarina_<pitch>.bwav; 7.08 s; level 1.35x live notes (the game's own: 1.33x).
- Presentation end: replay opened + length + 1 s, or the next matcher call, or 40 s.
- Flow queries: nodes that first ask before the replay opens (two: Dm_SY_Ocarina's opening switch and one polled
  every frame) get the host type; a node first asking later gets "none". This is for ocarina-point events that run
  Dm_SY_Ocarina as a sub-flow and check the song afterwards (e.g. TriggerSongOfTimeBlock). Prompted spots (mode 3)
  don't see the song at all.
- Verified headless (re/runs/soh17, soh18; audio via HOS_AUDIO_DUMP with HOS_AUDIO_RUN=1 in run_headless):
  presentation, title, notes, replay pitches and length; a real Zelda's Lullaby afterwards is back to its own
  title, notes and replay.

**Dead ends**
- Changing mgr+0x220 during the presentation: at 0.05 s it never starts; at 1.3 s the staff is empty and the flow
  hangs; at 4 s the type-specific effect has already latched (tested with Sun's Song as host).
- Hiding the type from every flow query cuts the presentation short.

**Open**
- Not tested at a real ocarina point (Song of Time block, gossip stone, Triforce mark).
- Link's "last melody" state reads Lullaby during and after the presentation.
- The visual effect is the Lullaby's pink wave (MM: OCEFF_WIPE7, also pink-white).
- No songbook entry. The title swap is English only (USen / EUen); other languages keep the host's title.
- Always known (no learned flag). Not tested on a save that has not learned Zelda's Lullaby.
- A third-party evfl parser run was blocked; flowcharts were read by strings only.

## 62. Goron overlay code follows the mod folder

- The user disabled the mod (folder `_030-GoronLink`) and the roll logic was still active. InstallModHooks enabled it
  unless HOOKSHOT_GORON_ROLL=0, and the driver/sfx lookups globbed `mods/*/` (which matches disabled folders).
- Fix: GoronModDir() = the folder of `mods/[!_-]*/goron_driver.bin`. Without it no Goron hook is installed (log:
  "[goron] Goron Link mod not enabled: roll code off"). The driver and sfx lookups use the same glob.
- Verified with the mod disabled (re/runs/gate1): vanilla Link, only the Song of Healing hooks installed.

## 63. Item Controls mod (050-ItemControls): spec + the RT item slot (2026-11-11)
Independent mod (not tied to Goron): `overlay/item_controls.inc`, gated on `mods/[!_-]*/item_controls`.
- **Spec (Xbox labels, as the game's prompts):** pause menu Items tab: hover + hold LB/RB + X/Y/B assigns one of
  6 slots; LB/RB no longer switch tabs (LT/RT already do, vanilla). In game the same combo uses the item; LB/RB alone
  do nothing (Quick Select wheel removed); RT = roll/dash, B unchanged. Overlay icons for the HUD first.
- **Vanilla layout** (Controls screen): LT target, LB/RB quick select (hold = wheel), RT use item, B actions /
  dash (hold), A jump, X attack, Y put away, RS camera. nn::hid bits: Xbox B = A (bit 0), A = B (1), Y = X (2),
  X = Y (3), LB = L (6), RB = R (7), LT = ZL (8), RT = ZR (9). In the pause menu LB/RB *and* LT/RT switch tabs.
- **HookHost chaining:** guest_hooks.inc's HookHost now chains onto an existing `jmp [rip+0]` hook (the new hook's
  "original" is the previous hook), so several mods can each hook npad_states independently.
- **RT item = GameData Enum `PlayerStatus.AssignableItem.Child` (0xa0029837) / `.Adult` (0xbc32ce24).** Values are
  murmur3 hashes of the names (Empty be69de49, DekuStick f9e54315, Boomerang d1b7e25f, DekuNut 83d2e406, ...).
  - Live store: key table of 8-byte {u32 hash, u32 entry index} (Child 65, Adult 59), and an entry array of 0x38-byte
    entries (header u32 0x84c40898; +0x18 default, +0x1C current, +0x20 copy, +0x28 value count = 55, +0x30 u64
    pointer to the value hash list). Run seen: key Child at 0x108be28318, array at 0x108be28638, Child entry 0x108be29470.
  - The writer during Quick Select is f_80bb9980 (sets +0x1C and +0x20); f_8019a580 copies it each frame into the
    player's copy (0x107ee740fc in that run).
  - **Writing +0x1C and +0x20 is enough:** the HUD switches and RT uses the new item (poked Boomerang -> RT threw it).
  - The `{hash, value}` hits with values 0x41/0x3b are the key table (indices), not values.
- Research switches: HOOKSHOT_ITEMCTL_DEBUG=1 (button + value log), HOOKSHOT_ITEMCTL_FIND=<file> (hex value search,
  narrowed on each change), HOOKSHOT_ITEMCTL_DUMP="addr:len,..." (dump after 60 s).
- **Pause menu (UIPlusMenuScreen):** screen object vtable 0x84c0baa8 (the 0x84c0be40/0x84c0bea0 tables next to the
  name getter 0x83417d20 are other objects). f_802f1380 (per-frame base UI screen method) gets it in x0 at 60 Hz
  only while the menu is open (byte +0x1a6 = 2 open, 0 closed). Tab = u32 at [[screen+0x198]+0x19c]: 0 Collection,
  3 Equipment, 4 Items, 5 System. f_83417190(screen, oldTab, newTab) = tab change. **The menu reopens on the last
  tab**, and System's cursor rests on Save, so a blind Select would save: always check the tab.
- **Select in the Items tab** writes the hovered item to the RT entry (that's how assignment works). The preview
  model load (f_80b17e20, "Item_<Name>", via the actor-spawn queue) also follows the cursor, but not on empty slots.
- **Gameplay signal:** f_809552f0 (movement step) runs each frame in play, stops while paused and while talking.
- Not useful: AssignableItem {hash,value} pairs (key table), f_80bb9980 (per-frame logic, every context).
- Tools: re/adrp_xref.py (ADRP+ADD refs to a guest address), re/profdiff.py (HOS_SAMPLE_ALL A/B diff).

### v0.1.0 working (re/runs/ic1, ic2)
Builder `python3 -m mod_manager.build_item_controls` (info.json + item_controls/items.txt from the enum names).
Overlay: item_controls.inc (mod logic) + item_controls_research.inc (debug switches only).
- Input: each new npad sample is transformed once and cached by sampling number (the game re-reads the history).
- Gameplay: LB/RB + X/Y/B -> slot item written to both RT entries (child + adult), RT reported held after 34 ms
  while the face button is held. LB/RB hidden; RT -> action bit (roll/dash).
- Pause, Items tab: LB/RB + X/Y/B -> RT entries set to Empty, Select pressed ~50 ms, whatever lands in the entry is
  stored (400 ms timeout restores the old value: empty slot or unassignable item). LB/RB hidden in the pause menu.
- Verified headless: assign Deku Stick / Boomerang / Bomb; Fire Arrow (adult only) refused; use throws the boomerang,
  draws the stick, pulls a bomb; holding LB opens no wheel; RT rolls. Slots persist in item_controls/slots.txt.
- Open: HUD panel not visible in headless shots (RX_VK_SHOTS capture is before the overlay), needs a live check;
  one slot set shared by both ages; game prompts still say "RT" (e.g. "Throw RT", the post-assign tip card);
  RT also does B's context actions; dialogue/ocarina/shop paths untested.

### v0.2.0: quirks from the user's first play session (2026-11-12)
- **Mid-air / during item animations the movement step f_809552f0 stops**, so v0.1 treated jumps as "not gameplay"
  and passed RT (item) and the bumpers (Quick Select wheel) through. Gameplay is now decided from UI screens updated
  by f_802f1380: HUD 84c0afe0 / 84c0b710 present (they stay up mid-air and while throwing), and no pause 84c0baa8,
  map/menu 84c04fb0 / 84c08aa0, dialogue 84c091f8 / 84c09a10, or ocarina (f_832b4df0 runs while it is out).
  Steady play screens: 84c042e8 84c05a50 84c08300 84c0afe0 84c0b710 84c0ffb0.
- Ocarina notes use the bumpers (L = D, R = F), so the ocarina must stay pass-through. Not yet tested live (the
  shop save could not take the ocarina out).
- **Aimed items** (item_controls/aim.txt: Boomerang, Slingshot, Bow, Fire/Ice/Light Arrow, Hookshot, Longshot): the
  combo latches RT; it is released when the bumper is let go or the same face button is pressed again (that press is
  then swallowed), and RT is held for at least 50 ms after it goes down, so a quick tap still throws.
- Verified headless (re/runs/q3, q4): jump + RB: no wheel; jump + RT: no item; boomerang aims while LB is held and
  throws on LB release or on a second Y; a quick tap throws; holding RB after the throw opens nothing.
- Test runner fix: run_headless.sh / drive_test.sh no longer use `pgrep/pkill -x hookshot` (a zombie hookshot under
  the user's mod-manager GUI kept runs from finishing, and pkill could hit the user's own game): they match the
  run's `--user <dir>` instead.

### v0.3.0: HUD with the game's item icons (2026-11-12)
- Icons: romfs/UI/Tex/Icon/<name>.bntx.zs (pouch icons; some files end _PouchIcon), ASTC 4x4 sRGB (format 0x2d06),
  341 or 683 px. Decoded by mod_manager/tools/astc_decode (suyu's astc.cpp from reference/hookshot-src, plus a tiny
  boost static_vector shim in tools/shim; the builder compiles it if missing), shrunk to 64x64 RGBA in
  item_controls/icons/. Bottles show their contents: GameData Enum
  PlayerStatus.AssignableItem.BottleInfo.Bottle<A-E>.BottleItem (Empty, RedPotion, ..., MaxMilk, HalfMilk), found
  with the same key/entry scan as the RT item.
- Overlay: ImTextureData + ImGui::RegisterUserTexture (ImGui 1.93; the overlay's render loop already uploads
  requested textures), drawn on the foreground draw list: rows LB / RB, 50 px boxes at 1080p (scaled by height),
  right-aligned under the game's own item box (y 232 px at 1080p), face badges X blue / Y yellow / B red, the slot in
  use highlighted. Shown in play and in the pause menu (plus an assign hint on the Items tab).
- Headless screenshots don't contain the overlay (no swapchain), so layout was checked with a PIL mock-up of the
  same geometry on a real frame; needs a live look.
- **Vanilla RT item box hidden (romfs):** HUD layout = UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.blarc.zs
  (SARC) -> PlayerStatus_00.buil, format BFFH "Nintendo.Ui.ResModels.LayoutResData" (Layout2, not BFLYT). Pane
  records: u32 count at 0x318, u64 pointers at 0x320, 0x44 bytes each (+0x10/+0x14 scale, +0x1c/+0x20 position,
  +0x28 parent index, +0x2c/+0x30 size), in the order of the pane names in the string pool (RootPane first).
  N_ScaleR_00 (11) = top-right anchor; N_Shortcut_00 (12) = "RB" + page dots; Pa_SelectItem_00 (18) = the RT item
  box (count, "RT"). Both scaled to 0 by the builder. The archive holds the layout's bytes twice; both copies are
  patched in place (same size, no RSTB change). Verified headless: box gone, also after using an item.
- HUD now 58 px boxes at y 48 px (1080p), right edge 52 px in (in line with the minimap): it takes the old box's spot.

### v0.3.x: RT prompts removed, HUD follows the game's HUD option (2026-11-12)
- **Button prompts:** romfs UI/CtrlGuideSetTable/CtrlGuideSetTable.game__ui__CtrlGuideSetTable.bgyml (BYML v7):
  GuideSetArray[] {GuideType = item/context name hash, GuideArray[] {Command = label hash, GuideKey = button}}.
  GuideKey 3432537025 = RT (item button); others: B_OptionX, X_OptionB, LS, RS, ZL, Plus ... The builder drops RT
  entries from item sets (13 entries: DekuStick, DekuNut, Bomb, Bombchu, SlingShot, Bow, Boomerang, Hookshot, Hammer,
  bottle Use/TakeOut/Drink/Read) and keeps fishing (ThrowLure, RollUp, Brake, Hook) and carried objects (Throw,
  Throw+Put). Empty GuideArrays are fine (no prompt). RSTB entry 16288 >> file size, no change needed.
  Verified: stick shows only "Put Away Y"; boomerang aim shows nothing.
- **HUD option:** GameData Enum 0x4e767f10 in option.sav (SaveFileIndex 1) {Normal, Pro, ProMaster}; ProMaster =
  the menu's Hero. Writing its entry (+0x1C/+0x20) applies immediately (research switch HOOKSHOT_ITEMCTL_HUDMODE).
- **Screen shown flag:** UI screen object +0x1a8 (u32) = 1 while shown. Player-status screen 84c0afe0 (hearts, old
  item box) gets 0 in Hero, in Pro outside combat, and while paused; 84c0b710 stays 1 in Pro. The screens keep
  updating while hidden (so gameplay detection is unaffected). The item boxes follow 84c0afe0's flag in play.
- Pause menu: boxes only on the Items tab.

## 64. Item Controls as a Vulkan layer (cross-platform loading, 2026-11-12)
The user wants the mod distributable, drag-and-drop on Linux and Windows. Hookshot only loads RomFS mods (readme
"Mods": exefs patches and plugins are not loaded), and the LD_PRELOAD overlay is Linux-only and launcher-bound.
- **Loading route:** Hookshot applies `hookshot_env.txt` (game folder; HOS_ENV_FILE=<path> overrides) before it starts
  Vulkan, on both platforms. With `VK_ADD_LAYER_PATH=<mod>/layer/<linux|windows>` and
  `VK_INSTANCE_LAYERS=VK_LAYER_HOOKSHOT_item_controls` the Vulkan loader (1.4.363 here) loads our layer from the mod
  folder (verified with VK_LOADER_DEBUG=layer). No game file is replaced; the mod manager will write these lines.
  Rejected: an SDL proxy in bin/ (players without SDL3 would break), a libvulkan proxy (no RUNPATH on Linux).
- **Source:** plugins/item_controls/ (src/layer.cpp, item_controls.cpp, hook.cpp, platform.cpp; build.sh builds
  libVkLayer_hookshot_item_controls.so with g++ and VkLayer_hookshot_item_controls.dll with mingw-w64, static, imports
  only KERNEL32/USER32/UCRT). The old overlay no longer contains the item controls (moved to plugins/item_controls/research).
- **Platform facts:** guest memory at 0x100000000000 on both (a64-kit A64_HOST_BASE). Symbols: Linux ELF .symtab;
  Windows: the builder's lld-link map beside the module (hookshot.map or hookshot_game.map; nx2/build.py), parsed
  for "Preferred load address" + the symbol's VA. npad_states is static: hook the five
  imp__ZN2nn3hid13GetNpadStates* entries, following jmp stubs and hooking each distinct target once (Linux: all five
  -> npad_states). The hooker relocates RIP-relative prologue operands and allocates trampolines within +-2 GB.
- **Layer details:** the plugin starts at the game's vkCreateInstance; the library path is normalized with realpath
  (the loader opens it as .../linux/./lib...so). The overlay pass submits waiting on the present's wait semaphores
  and the present waits on ours (the old overlay did not wait). Log: <mod>/item_controls/itemctl.log.
- Verified on Linux (re/runs/lay4): layer loaded via an env file, hooks in, boomerang aim/throw works. The HUD
  drawing can only be checked live (headless has no swapchain).
- Tests now use a fixed profile: HOS_TEST_USER=re/testuser_shop (run_headless.sh copies it instead of user/); the
  user's newest real save moved to a dungeon.

## 65. Mod manager: Windows + Linux, code mods (2026-11-12)
- **Code mods** declare a Vulkan layer in info.json: `"code": {"vulkan_layer": {"name": ..., "linux": "layer/linux",
  "windows": "layer/windows"}}` (folders with the manifest + library), and player files to keep on update:
  `"keep": ["item_controls/slots.txt"]`. mod_manager/codemods.py validates the layer for this platform and keeps
  one marked block in the game folder's hookshot_env.txt (VK_ADD_LAYER_PATH / VK_INSTANCE_LAYERS, ':' on Linux,
  ';' on Windows, load order) for the enabled code mods. Lines outside the block stay; a player's own values of
  those two variables are merged in front. No code mods enabled -> block removed (file removed if it held only that).
- The block is re-synced on every manager start (paths follow a moved game folder) and after enable / disable /
  reorder / install / delete / launch.
- **Game folder:** --game-dir, HOOKSHOT_GAME_DIR, the saved one (config.json in %APPDATA%\hookshot-mods or
  $XDG_CONFIG_HOME/hookshot-mods), the current folder, or folders around the manager; a game folder has hookshot.exe
  (Windows) or bin/hookshot (Linux). The GUI asks for it when none is found. No more hardcoded home-folder path.
- **Install:** a packaged mod folder (info.json with "code" or data folders besides romfs) is copied whole; a mod with
  the same name is updated in place (folder, order, enabled state and "keep" files survive). Plain RomFS archives
  still go through the old normalization.
- **Launch:** hookshot.exe on Windows; bin/hookshot (or the launcher) on Linux; the LD_PRELOAD dev overlay only on
  Linux and only when overlay/libhookshot_overlay.so exists (GUI checkbox hidden otherwise).
- Windows file locks (the running game holds the layer DLL) give "Close the game, then try again".
- CLI: `hookshot-mods code` lists code mods with their state and re-syncs. Tested in a fake game folder (install
  from zip, disable/enable, update keeps slots.txt, user env lines kept) and the GUI offscreen.
- Running the manager wrote the block into the real hookshot_env.txt (it did not exist before): the item controls
  layer now loads whenever the game starts normally.

## 66. Source repo moved out of the game folder (2026-11-12)

- All project source is now a git repo at `~/Projects/hookshot-mods`: `mod_manager/`, `plugins/`, `overlay/`,
  `re/*.py`, these notes. Submodules: `imgui_src`, `vulkan_headers_src`, `reference/hookshot-src`.
- The game folder keeps only game data and research data: `romfs/`, `mods/` (built mods), `re/` dumps, `user/`,
  `re/testuser_shop`, `reference/mm-decomp`, `reference/Zelda64Recomp`.
- Tools find the game folder through `core.find_game_dir()` (HOOKSHOT_GAME_DIR, then the saved manager config);
  shell scripts use `GAME=${HOOKSHOT_GAME_DIR:-$(python3 <repo>/hookshot-mods gamedir)}`.
- The dev overlay is preloaded from `<repo>/overlay/libhookshot_overlay.so`. It writes `overlay.log`,
  `param_tweaks.txt` and `param_dump_*.bin` beside itself (dladdr), not in the game's cwd. The manager's
  "F2 Dev Overlay" box uses `core.dev_overlay_lib()`.
- Headless tests: `HOS_TEST_USER=<game>/re/testuser_shop <repo>/overlay/tools/run_headless.sh <out> ...`.

## 67. hookshot_env.txt does NOT load Vulkan layers (v2.0.14+); code mods load at launch (2026-11-12)

- Correction to §64: since v2.0.14 Hookshot filters `hookshot_env.txt` (`env_line_allowed`, host/hos_main.c):
  only names starting `RX_ HOS_ HOOKSHOT_ EDEN_ NVN_ TOTK_ SDL_`, never `HOS_ENV_FILE`/SDL library overrides,
  and no value that is a path. Rejected lines print `env: hookshot_env.txt: X left out (only the game's switches,
  and no paths, are taken from it)` on stderr. Reason given: the file is passed around between players and must not
  load code. `HOS_ENV_FILE` (set in the real environment) is read unfiltered, for test runs.
- So the manager no longer writes a block there (it removes the old one). `launch_game` sets `VK_ADD_LAYER_PATH`
  and `VK_INSTANCE_LAYERS` for the game process only (`codemods.apply_layers`). Code mods load only when the game is
  started from Hookshot Mods (or with `eval "$(hookshot-mods env)"` first). We deliberately do not work around
  Hookshot's filter (e.g. an implicit layer toggled by an allowed `HOS_` switch).
- Earlier "layer loads" checks after §64 read stale itemctl.log lines: check the log's mtime, not its tail.
- `run_headless.sh` evals `hookshot-mods env`, so test runs load the enabled code mods like a Launch does.
- The local reference/hookshot-src submodule is pinned at v2.0.10+55; `git fetch --tags` there has v2.0.14..v2.2.0-rc.1.
  Hookshot v2.1.0 is out; the game folder's own launcher (`./hookshot`) starts the Builder, which offers the update.
- Launched games are reaped by a daemon thread (an old manager window left `[hookshot] <defunct>` processes).

## 68. Item Controls 0.5.0: a code-only mod, game files changed in memory, bundled with the app (2026-11-12)

- The mod ships no game data: `info.json`, `layer/{linux,windows}`, `item_controls/{items,aim,icons}.txt` (names and
  murmur3 hashes only). Template in `plugins/item_controls/mod/`; `build.sh` builds into a mod folder, `package.sh`
  makes `dist/bundled_mods/ItemControls.zip`.
- Hookshot indexes mod RomFS files once at start (host/mods.c `hos_mods_init`), so a mod cannot create RomFS files
  while the game runs. Instead the layer hooks Hookshot's nn::fs HLE functions (`vfs.cpp`): `OpenFile` (after the
  original succeeds, a registered path's FileHandle is remembered), the three `ReadFile`s, `GetFileSize` and
  `CloseFile` serve a patched in-memory copy for those handles; reads call `hos_mark_written` like Hookshot's own.
  The copy is made from `hos_mods_romfs(rel)` (another mod's file, or Hookshot's own prompts/ copy) else
  `*hos_romfs_dir + rel`, on a worker thread started at layer init (before the game opens them).
- HUD archive: `prompts/Common.blarc.zs` (Hookshot's button-prompt version) is what the game reads; 4 copies of
  PlayerStatus_00.buil in it. zstd dictionary ID 1 (from Pack/ZsDic.pack.zs); the served copy is a raw-block zstd
  frame (no dictionary, no compressor needed). Byte-identical to the old Python patch.
- Prompt table: BYML stores equal nodes once — GuideSetArray[6], [14], [18] share one GuideArray, and [14] keeps RT
  (fishing/carrying). So changed arrays are appended as new nodes and only that set's entry is repointed; never
  edit a shared node in place. Result identical to the Python patch (13 RT entries).
- Icons: `UI/Tex/Icon/<name>.bntx.zs` decoded in the layer (BNTX Switch 2 block-linear deswizzle, suyu ASTC
  `DecompressSerial`, alpha-weighted box filter to 64x64), 60 of 60.
- The ELF symbol reader now accepts STT_OBJECT too (`hos_romfs_dir` is a variable).
- Verified headless (re/testuser_shop): vanilla RT box gone, LB+Y raises the Boomerang with no RT prompt.
- The app carries the mod (`bundled_mods/` in the PyInstaller bundle) and offers it on first start, once per version
  ("included_declined" in the manager config); **Included Mods...** button; `hookshot-mods included [--install]`.
  Names match ignoring case/spaces ("ItemControls" = "Item Controls"); an update keeps `slots.txt`.
- `mod_manager/build_item_controls.py` is now only a developer tool regenerating the three lists.

## 69. Native HUD boxes (option 1) — research spike (2026-11-12)

Goal: the six item boxes drawn by the game's own UI (its art, fades, Pro/Hero handling, screenshots) instead of the
layer's ImGui overlay. Tools: `re/bffh.py` (list BFFH layouts and panes in the Layout2 archive), `re/gdis.py` (disassemble
the guest function around an address; FSTARTS = sorted f_ starts from `nm bin/hookshot`, image `re/img.elf`, needs
llvm-objdump `-D --triple=aarch64`).

**Layout format (BFFH, Nintendo.Ui.ResModels.LayoutResData)** — Hookshot's nx2/prompt_assets.py documents some of it:
layout header +0x118 u64 pane-name table, +0x120 u64 pane-record table, +0x130 u32 pane count. Pane record 0x44 bytes:
rotation, +0x10 scale (2f), +0x1c translation (3f), +0x28 parent index (u32), +0x2c size (2f). Panes form a flat list
with parent indices. PlayerStatus_00.buil (27152 bytes, 33 panes): the RT box is `Pa_SelectItem_00` (pane 18, under
N_ScaleR_00 at (1920,1078); box at (-294,-258), scale 0.85, size 380x492).
Part panes: a table (PlayerStatus at 0x1e80) of 7 pointers to 0x58-byte part records {u64 part-layout name ("PaItemBtn_00"),
zeros, u64 -> array, u32 pane index}, then the 7 part panes' names, then 7 u64s that look like hashes (lookup index).
The archive holds each layout 4 times (Hookshot's prompts copy). Growing the archive would also need its entry in the
resource size table (Hookshot already serves its own enlarged `rsizetable` from prompts/).

**The item button part `PaItemBtn_00`** (23 panes): N_HUD background (P_HUDBg_00/Deco/Sh), N_ShortCut (the Quick
Select look), T_Assign_00 (button label, font icon), N_Item_00 > N_ItemChange_00 > Pa_ReplaceIcon_00 (the icon, a
part itself: PaReplaceIcon_00), P_AmiiboIcon_00, N_Num_00 > T_ItemNum_00 (ammo count).

**Runtime creation exists**: PaShortCutSlideList_00 (the Quick Select list) has no item panes; its N_SlideList_00 is
a custom pane (factory f_80ebed30: pane name "N_SlideList_00" in layout "PaShortCutSlideList_00" -> 0x78-byte object,
vtable 0x84c024d8, ctor f_80ebee30) configured by a record naming "PaItemBtn_00" with the entry size (380, 492) and
spacing: the game instantiates PaItemBtn_00 parts at run time from the part's name.

**How the HUD drives the RT box**: HUD setup f_83415d20 (884 instructions) finds panes by name with
f_8045c4a0(name, root [screen+0x28], &out); for Pa_SelectItem_00 it takes the part's layout ([...+0xa0] via
f_8045c040/f_8045b1e0), allocates a 0x110-byte item-button controller (vtable from [0x84cac000+0xb78]+0x10), links it
into the layout's list and stores it at HUD screen +0x328 (another controller at +0x320), then f_8045efe0(ctrl, x20, 1)
binds it: ten states via f_8045c7a0 ("StockNum", bag/quiver sizes "..._Large", "...TypeForAnim"), then finds
Pa_ReplaceIcon_00 and makes its icon sub-controller (code around f_80a56d80..f_80a5715c also uses Pa_ReplaceIcon_00/_01).
Sibling init variants: f_8045e500, f_8045e9d0, f_8045ece0.

**Hookshot's prompt archive** is built once per run on first request (host/prompts.c hos_prompts_romfs), so the
layer's in-memory copy (made from it) is never stale.

Two ways to six native boxes:
- A (preferred): runtime creation — call the game's create-part code (found through the slide list's vtable) after the
  HUD setup, five PaItemBtn_00 parts under N_ScaleR_00, controllers made like the HUD's (alloc 0x110 + f_8045efe0), and
  their item set through the controller's set-item method (still to find: what the HUD update calls with the RT item).
- B: layout surgery — add five part panes to PlayerStatus_00 (pane records, names, part records, the hash index) and the
  size table entry. More format work, but the game then builds and owns them.
Open questions for the next step: (1) the create-part function and its arguments; (2) the controller's set-item method
(follow writes to/calls on [HUD screen+0x328] in the HUD's per-frame update); (3) calling lifted guest functions from a
hook (GuestCtx registers + guest stack) safely.

## 70. Native HUD boxes: six game-drawn PaItemBtn_00 clones on screen (2026-11-12)

Research probe `ITEMCTL_RESEARCH=clone[,keepbox]` (plugins/item_controls/src/research.cpp; keepbox = no file patches).
Calling game functions from a hook: save the GuestCtx, set x0.., x30 = 0, call the lifted f_ function, restore
(Hookshot's own host/oot_menu.c guest_call does the same).

Working recipe, after the HUD's setup (f_83415d20, a vtable method of HUD screen 0x84c0b710; x0 screen, x1 heap):
- template part layout = [[screen+0x328]+0x20] (the RT box controller keeps it; vtable 0x84c41970 = layout)
- template node T = f_80229650(tmpl+0x40); holder H = f_80229690(T) = the HUD's Pa_SelectItem_00 pane. Tree nodes (T, H)
  carry the transform: scale +0x60/+0x64, translation +0x6c/+0x70 (H: (-294,-258) 0.85, scale 0 under our HUD patch).
- per clone: name = f_8005ebe0(32, heap, 8) (operator new(size, heap, align)); layout = f_8005ebe0(0x98, heap, 8);
  f_80791f70(layout, tmpl, name, 0, 0) builds a fresh copy of the part; f_80235dc0([H+0x18], [layout+0x80], H) attaches
  its root under the holder; its node f_80229650(layout+0x40) then takes scale/translation in the holder's units.
- hide the template's content: T scale 0 (the holder must stay visible: its children inherit its scale).
- Pitfalls that crashed the UI worker (ModuleSystemWorker2, f_8155xxxx): attaching a clone under a pane of another
  container (N_ScaleR_00), and writing "transform" floats into a layout's root object [layout+0x80] (pointers live at
  +0x60..+0x70 there; transforms are only on tree nodes). A layout's root object: list links at +0/+8, owner +0xa0,
  parent +0x108, name pointer +0x148.
- The screens' "register layout" virtual (slot 0x1e8) is an empty default for the HUD (f_8379bdf0 returns 0): not needed.
  The dungeon map calls it on its own screen object; [HUD screen+0x20] (vt 0x84c43670) slot 0x1e8 is a DESTRUCTOR —
  never call it.
Result: six empty item frames in a 2x3 grid (scale 0.55, step 230, from (-260,-230) in N_ScaleR_00 units), drawn by
the game, no crash over a full headless run. Next: each box's item (icon, ammo count) and its button label.

## 71. Native HUD boxes: the item-button controller and the icon (in progress, 2026-11-12)

Probes (research.cpp): `trace` (call counts over ITEMCTL_TRACE ranges; skip functions < 16 guest instructions or
< 32 host bytes: a 14-byte hook spills out of tiny functions and crashed the game), `watch` (diff the RT box
controller every 100 ms), `copy` (callers of f_8575cdc0, the copy that writes it), `builder` (f_80459f90 calls),
`items` (set the boxes' items), `shareanims` (experiment). HOS_WATCH data breakpoints (run_headless watch.txt:
4 s reads+writes, then 4 s writes, then watch.txt.out "addr w hookshot+off count") found the writer.

- RT box controller (0x110, vt 0x84c01400, [HUD screen+0x328], same address every run 0x107ef01528):
  +0x20 its part layout, +0x30..+0x78 ten animation states (f_8045c7a0(layout, name); default object 0x84df2270
  when missing), +0x80 icon sub-controller (0x40, vt 0x84c022a8, built by f_80a57320 around P_ReplaceIcon_00 and
  P_ReplaceIconSh_00), +0x88 -> +0x94 icon name buffer (cap +0x90 = 0x40, "Item_DekuStick"), +0xd8 item kind,
  +0xe0 the layout system's entry for this layout (searched in [sys+0x38]), +0xf0 -> +0x100 count text ("27").
- The item setter: f_80a95ef0(ctrl, &name{char*, s32 cap}, kind, 1, 0, w5, w6) — the Quick Select list calls it with
  w5 = -1, w6 = 0; the HUD's RT update f_80a95770 (x0 = HUD screen; runs only on changes) with [screen+0x384],
  [screen+0x394]. It copies the name, calls the icon sub-controller f_80a93480(icon, name, kind, ...) and the count
  f_80a96170(ctrl, kind, ...). Kind = f_802efb20(**0x84c9e0d0 item database, item hash): DekuStick 0, DekuNut 1,
  Bomb 2, Boomerang 3, Bombchu 9. Name from kind: f_80a945f0(db, &str, kind, 1).
- Controllers for clones made like the HUD's (MakeItemButton in research.cpp) take items without crashing, but the
  icons do not show yet. Clones from f_80791f70 have NO animation table (layout +0x88 count / +0x90 array zeroed;
  the builder attaches animations in a later pass: f_804597f0 -> f_8045bac0 "ResetAnimAtOpen"/"OpenWithScreen"),
  and no +0xe0 entry. Sharing the template's table fixes the state lookups but not the icon; the nested icon part
  (Pa_ReplaceIcon_00 inside each clone) has no animations either — next suspect.
- The layout builder's part creation: f_80459f90 (virtual slot 0x10 of builder vt 0x84c419b8; x0 builder context
  [vt, 0, heap?, sys?, parent layout, resource ptr, 0], x1 description [part name, len, instance name, len, archive,
  ?, previous, 2]) -> f_8045a0e0 (builds from the resource). Generic builder f_8154f990 calls it for every part; the
  Quick Select list (f_8151eb00) builds its 16 PaItemBtn_00 entries with it at run time.
Options next: (a) give clones (and their nested icon part) animations through f_804597f0/f_8045bac0; (b) build the
boxes with the builder path (f_80459f90 + description) as the Quick Select list does, which may bring everything.

## 72. Native HUD boxes: icons drawn at vanilla size; ammo count text still corrupts (2026-11-13)

`ITEMCTL_RESEARCH=clone,items,keepbox,anims,alpha` (research.cpp; add `build` for builder-made boxes) shows the six
boxes with their items' icons, drawn by the game at the vanilla RT box's size (screenshot in the session scratchpad,
native_icons_first.png / r10).
- Icon opacity: the icon sub-controller [ctrl+0x80] holds two replace-icon objects (+0x30 icon, +0x38 shadow; vt
  0x84c02cb8, ctor f_80a574d0 -> f_80a57520 zeroes +0x30). Their float +0x30 is the icon's opacity: 1.0 on the RT box
  right after the HUD's setup, 0 on boxes made later (data breakpoints: nothing touches it during play). Setting it to
  1 shows the icons (0.5 = half transparent). Who writes it at the HUD's setup is not found yet.
- Icon size: without animations the icons were about twice too large (the node chains are identical, all scale 1, so
  the size comes from animations). BuildAnims (research.cpp) replicates the screen open pass f_8045bac0 for one
  layout: instances [root+0x48] (0x20 each, root = [layout+0x80], count [[root+0x10]+0x158]); table +0x88/+0x90 of
  0x28-byte entries {links, u32 0, u16 0x20 (|0x400 for "ResetAnimAtOpen", 0x83e3f1b0, and layout +0x38 |= 8), u8
  f_8045bf30(inst)&1, layout, instance}, f_802f2b80(inst, 0); then the layout's virtual +0x28 (f_8045cb50). Run on
  each box before its controller is bound (13 animations: Select UnSelect Decide Assign State StockNum NumMax NumMin
  IsInvalid ItemPattern Type MinigamePattern ItemChange) and on its nested icon layout [[ctrl+0x80]+0x20] (3: State
  Invalid IsRight). The frames then look like vanilla's and the icons have the right size.
- Builder path works: the HUD builds its RT box with f_80459f90(ctx, desc), ctx {0x84c419b8, 0, HUD heap, HUD screen,
  PlayerStatus layout [screen+0x28], resource 0x100a1fa7b8, ..}, desc {"PaItemBtn_00", 0xc, "Pa_SelectItem_00", 0x10,
  PlayerStatus's layout list 0x107ee743f0, 0, 0, 1}. Replaying it (ctx+8 = 0: the builder allocates) gives a fresh
  layout (root not attached, no animations: attach + BuildAnims as for clones). Generic builder f_8154f990 then
  registers it: f_8156aa50(list, layout+0x40) (list = desc+0x20; nested Pa_ReplaceIcon_00 layouts are in it too).
- Ammo count (as the RT update f_80a95770 at 0x80a958d4): rec = f_805aed10(&name); if [rec+0xfa] == 1: count
  f_805b63e0(db, &name, 1, 0), max f_805b5cf0(db, &name, 0, 0), flag f_80a94910(db, &name), f_80a95df0(ctrl, count,
  max, flag) (formats "%d" into ctrl+0xf0, sets the text of T_ItemNum_00 (0x83dfc148) through f_8045b970 ->
  f_80459520, plays NumMax/NumMin [ctrl+0x40]/[ctrl+0x48]). Values are right (Deku Stick 27/30, Bomb 40/40,
  Bombchu 2/40, Deku Nut 40/40). f_80a96170 is not the count: it sets the ItemPattern frame per kind.
- PROBLEM: setting the count text on our boxes (cloned or builder-made) corrupts rendering: the HUD disappears and
  3D meshes stretch into huge triangles (Link's held stick, black wedges). Bisection: the animations alone are fine
  (dfanim); the same f_80a95df0 call on the game's RT box from our hook is fine (rtdf), so the calling method is not
  the cause; registering our layouts (f_8156aa50, boxes and nested icons) plus copying the RT root's +0x90/+0x98
  (owner) does not fix it. The text pane (index 14) and its text box match the RT box's apart from own pointers. Root
  objects differ at +0x90/+0x98 and are shifted by 8 bytes from +0x158 on (the RT box's root has one more qword
  there) — unexplained; next suspect. Until solved: no count text on the native boxes (overlay could draw counts).

## 73. Native HUD boxes in the mod: built with the HUD, counts and labels work (2026-11-13)

The count-text corruption (§72) came from building the boxes too late. The HUD screen's open pass f_8045bac0 runs
BEFORE the HUD setup f_83415d20 and does two things per layout of the screen: (1) f_80c3de80 -> f_81546800(root, ..)
-> f_81515f20 sizes every pane's draw data (text included) and sets root+0x90 (its draw module struct, via f_81514300)
and root+0x98; f_815468c0 then allocates ONE draw list of exactly the total size; (2) the animation tables (what
BuildAnims in research.cpp replicated). Layouts made after it have no draw memory: setting their text writes past the
list (stretched meshes, HUD gone). Found with HOS_WATCH on the RT root's +0x90/+0x98, re-armed through boot
(scratchpad watchloop.sh: rewrite watch.txt whenever it becomes watch.txt.done); writers mapped from host offsets to
f_ symbols with `nm bin/hookshot`. (The root object is 0x158 bytes, made by f_8045a6f0: past it is another object,
which explained the "8-byte shift" of §72.)

Fix: build the six boxes during the screen's build. Right after the builder virtual f_80459f90 returns the RT box
(part "PaItemBtn_00", instance "Pa_SelectItem_00"), call it six more times with copies of the same ctx (+8 = 0) and
description, and add each new layout and its nested part layouts to the screen's layout list = description+0x20
(f_8156aa50(list, layout+0x40); nested: part panes, flag 0x1800000 at pane+0xa8, nested root [[pane+8]+8], its layout
at root+0xb0). The open pass then treats them like the game's own: own draw modules, 13 + 3 animations, and the icon
opacity is right too (no +0x30 workaround needed).

Labels: T_Assign_00 is text pane 14 of the box (22 is T_ItemNum_00). Its source text is not an "@name" tag, so the
by-name setters (f_80a87d30 / f_8045b970) set nothing: write it with f_80459520(pane, &str, 0). The text box keeps the
POINTER to the string (+0xf0, length +0x124; f_80459680), so the string must stay alive (per-box buffer from the HUD
heap; a stack buffer hung the game). Glyphs: f_80a86f70(&str) then f_80a8ae50(&str, button) with the Switch button
index (table at [0x84cac668]+0x128: None A B X Y L ZL R ZR Plus Minus Up Down ...); the game draws them with Xbox
labels, so Xbox X/Y/B = 4/3/1, LB/RB = 5/7; two glyph strings (UTF-8 private-use characters) can be joined.
Counts without a count: the RT update plays StockNum [ctrl+0x30] back (f_802f0fe0) to hide the number.
Per-frame HUD update: f_802ef010 (x0 = HUD screen, once per frame).

In the mod: src/native_hud.cpp (hooks f_80459f90, f_83415d20, f_802ef010). The HUD file patch still hides the RT box
pane (scale 0, its original scale recorded by item_files.cpp); the native setup gives it back and hides the RT box's
own content (its node at scale 0), so when anything is missing the old overlay HUD takes over unchanged.
ITEMCTL_NATIVE=0 forces the overlay.

### Later in 0.6.0: smaller boxes, empty slots, no overlay on the pause menu
- Boxes at scale 0.42 (from 0.55), the grid's top-right corner kept where the RT box's was.
- An emptied slot kept its old picture: the item setter with kind -1 leaves it, and f_80a93920 with an empty name does
  not clear it; plain scale writes to a drawn pane are ignored (no dirty flag). Fix: pane SetVisible f_8022aea0(pane,
  bool) (bit 0 of +0xa8, marks the pane dirty) on [[icon sub +0x30/+0x38] + 0x18].
- Opening and closing the pause menu re-runs neither the HUD setup nor the box build.

## 74. Item boxes on the pause menu's Items tab (0.7.0, 2026-11-13)
Goal: show where an item is going while assigning, in the game's own style, without covering the rupee counter.
- **Screens:** pause menu UIPlusMenuScreen vt 0x84c0baa8 (layout PlusMenu_00); its header with hearts and rupees is
  a separate screen, vt 0x84c08aa0 (MenuOverlay_00: A_Rupee_00 at 1652, 986 in 3840 x 2160 screen units, centre
  origin). The HUD screen with the RT box is vt 0x84c0b710 (not 0x84c0afe0).
- **The Items tab** is the part PaItemMenu_00 as Pa_Item_00 (built through f_80459f90 with the pause screen as
  ctx[3]); it sits at x 8560 under N_MenuChange_01, which slides by whole tabs, so while the tab is shown its layout
  is laid out as the screen. Anything attached under its layout node slides in and out with the tab by itself.
- **Building:** the same build call as the Items tab's with the description's names swapped (part "PaItemBtn_00",
  len 12; instance "Pa_SelectItem_00", len 16; desc[5..7] = 0, 0, 1) builds PaItemBtn_00 parts: every layout comes
  from the same resource (ctx[5] the same object for the HUD, the pause menu and Quick Select). Registered in the
  description's layout list as for the HUD, they get draw data and animations in the open pass.
- **Setup:** the screen setup virtual, f_83415d20 for the HUD, is at vt + 0x2e8 relative to the HUD screen's vt
  0x84c0b710; the same slot of the pause vt holds f_80f99400 (x0 screen, x1 heap). (f_80dc4fd0, in the slot found by
  matching table runs, belongs to another class, vt 0x84c0c238.) The build's heap (ctx[2]) cannot allocate there
  (the controllers failed): use the setup's x1.
- **Per frame:** f_802f1380 (generic screen frame, the pause screen only while open) updates items, counts, labels.
- **Feedback:** while LB/RB is held, the other row plays IsInvalid (dimmed, rewound on release); a newly assigned
  item plays Decide. "Select"/"UnSelect" show nothing visible on these boxes. Strings used from the image: IsInvalid
  0x83f342b8, Select 0x83f34e3a, UnSelect 0x83f34e42.
- **Placement:** scale 0.48, top-right corner at (1840, 700) in the tab's units: the dark corner of the preview panel
  under the header's ornament. Large preview models (the bow) still turn behind the left column at times.
- Verified headless: built and set up in the pause menu; assignment updates live (Deku Stick LB+X -> RB+X); row
  dimming; hidden on Equipment, back on Items; HUD boxes after closing; boxes again on reopening; no crash.
- Research switch: ITEMCTL_RESEARCH=allbuild logs every part build (part, instance, screen and its vt, parent).

## 75. Pane alpha: in-game boxes fade (0.8.0, 2026-11-13)
User's request: in play the boxes sit at about 40% and come to 100% while their bumper is held; the button labels
are hidden (0%) unless their bumper is held. The pause menu boxes stay opaque.
- **Pane alpha:** +0x74 float = the pane's own alpha, used while bit 0x8 of +0xa8 is set; +0x78 = its global alpha.
  The pane update f_8154c890 runs when pending bit 0x20 is set at +0. It computes global = (bit 0x8 ? own : 1)
  times the parent's global (unless bit 0x10), passes the change to the children (f_8154d720(pane, 0x20)), and hides
  the pane below ~0.001.
- **Setting it:** write +0x74, set bit 0x8, then mark the pane as SetVisible does: [pane] |= [pane+4] & 0x20, then
  f_815457d0(pane) if that bit was not already pending. The box's root pane (f_80229650(layout + 0x40)) fades the
  whole box, count included. The label is text pane 14, T_Assign_00.
- Not alpha: f_8154b720 writes +0x7c (0 by default) and bit 0x200, so it is something else. No setter for +0x74
  was found among the small functions.
- Eased by 0.2 a frame. Verified headless: rest 40% without labels; LB held: LB row full with labels; same for RB.

## 76. RT for movement only, latched sprint (0.9.0, 2026-11-13)
The user's request: RT only rolls, sprints and side hops, with no interactions; a sprint started with RT keeps going
without the trigger (as in NieR: Automata) until the player stops moving. The boxes' resting opacity goes to 60%
(40% faded in too abruptly).
- **The interaction signal:** the action prompt is its own screen, DoCommand_00 (vt 0x84c05a50; ActionGuide_00 is
  0x84c02d48, CtrlGuide_00 is 0x84c05318). u32 at +0x1a4: byte +0x1a6 = 1 appearing, 2 shown, 3 gone (with +0x1a5
  = 0xff while leaving); 0x10000 or 0x20000 means a prompt is offered. +0x2e8 = the command (0 Talk, 3 Open), with
  hashes at +0x2d0/+0x2d4. Seen in the shop: Talk at the counter, Open at the door, nothing in between (stays 0x3ff00).
- The interaction-or-roll choice is made in code (action verbs), not in the player AINBs (Player.scheduler.rolling
  only sets ActionVerb). The input masks (ControllerTrigMask...) are not in the player modules.
- **Mod:** an RT press counts for movement only if no prompt is offered when it starts. Then RT reports the action
  bit, as before, which gives roll, dash and side hop. Near an interactable, RT does nothing: the game would take
  the press as Talk or Open.
- **Latch:** RT held 0.3 s with the left stick at 45% or more (and no Z-target) latches the action bit held. Released
  when the stick stays under 45% for 0.15 s, on Z-target, on item use, when play stops, or on a B press (that sample
  reports B up, so B gives a fresh press). Sticks come from the NpadState (+0x10 lx, +0x14 ly).
- Verified headless: no Talk on RT at the counter; latch on, then off when the stick was released.
- Untested: open-field sprint length, side hop, walls (the latch holds while the stick pushes into a wall).

### 0.9.1: RT presses taken into interactions by the input history
The user's report: spamming RT during a roll still talked or opened when the prompt appeared mid-roll.
- **Reproduced headless:** roll toward the shopkeeper with RT every 120 ms. The last RT press, 37 ms before the
  prompt appeared, opened the dialogue.
- **No earlier signal:** the game's own command (GameData struct 0xca62c947, string member 0xd7e9ac3a naming a String
  entry: "Empty", "Talk", ...; read by DoCommand's update f_80a8b620) changes in the same frame as the prompt.
- **Input history:** the game reads 16 samples a frame (one new sample per frame, about 16.7 ms, so about 0.27 s)
  and finds presses inside them. A press up to a few frames old is spent on an interaction that turns up later.
- **Failed fixes:** waiting 70 ms before passing RT on still talked 2 of 4 times (the delayed press is just as
  fresh). Taking back RT's samples when a prompt appears still talked 1 of 4 times.
- **Fix:** an RT press is a press for one frame only. From the next poll on, the 16 samples before it read as held
  (bit set in the cache the game re-reads), so the history shows a hold and no press, and dash goes on. The take-back
  on a prompt stays.
- **Verified:** 4 of 4 timing offsets with no dialogue; rolls still happen; the latched sprint still works.
- **Cost:** an RT press made during a roll is no longer kept for a second roll; press again after it.
- Research switches: ITEMCTL_RTLOG=1 (RT presses with the prompt state, the game's command as it changes),
  ITEMCTL_POLLLOG=1 (npad poll timing).

### 0.9.2: rolling out of a latched sprint
The user's report: RT could not roll during a latched sprint. The latch reports the action bit held, so a new RT press
made no new press for the game. Fix: an RT press during a latched sprint (with no prompt offered) reports the bit up
for one sample, then the latch's hold resumes. That makes a fresh press, which is seen for one frame as every RT press
is, and the game rolls. The latch stays on. Verified headless: sprint latched, a second RT press rolled, the latch
ended only when the stick was released.

### 0.9.3: rolls out of a running dash, presses during a roll held back
The user's report: in a latched sprint, RT rolled only if the stick moved a little; with the stick fixed in the same
direction it did not.
- **Roll events:** the rolling node (AINB ExecutePlayerRolling, vtable 0x849e9500, name getter f_828258f0) has enter
  f_806d9a30 and exit f_809590b0 (each only in that vtable); f_80953a70 and f_80953e80 run every frame of the roll.
  Found with trace on the vtable's own methods. Research switch: ITEMCTL_RESEARCH=rolllog.
- **Vanilla:** in the market (out of the shop door), B held 1.5 s to dash, then B again with the stick fixed: a roll;
  a second one 0.8 s later rolled too.
- **Failed fix:** rewriting the sample before an RT press as up, to make the press in one sample, gave no roll. The
  game does not read samples it has already taken again.
- **The bit is up 50 ms** before the new press, for RT during a latched sprint and for B ending the latch (was one
  frame). 17 ms and 50 ms both rolled headless; 50 ms covers slower logic.
- **Facing:** headless, the mid-sprint RT press was blocked twice because the game offered Talk for an NPC Link ran
  toward. Prompts depend on facing, so nudging the stick turns Link away and RT rolls again. This is probably the
  user's case. To roll even then, RT presses would have to be kept out of the game's interaction choice (an action verb
  chosen in code; "Rolling" is referenced from about 15 functions near 0x80d60000, not researched yet).
- **Presses during a roll:** the 4-offset spam test was noisy (one-frame press: 0/4 once, 1/4 and 4/4 later). The game
  keeps an action press made during a roll and spends it at the roll's end, so rewriting the history cannot cover it.
  Now RT presses made during a roll (between enter and exit) are held. Once the roll is over plus 50 ms (time for a
  prompt to show), the press goes through if nothing is offered, else it is dropped (after 1 s at most). Verified:
  4 of 4 offsets with no dialogue, chained rolls still happen (1 or 2 per run).
- **RT log line:** an RT press blocked by an offer now logs "RT blocked: the game offers '<command>' on B".
- Two-frame press visibility (a test) let interactions in again (3 of 4), so presses stay one frame.

### 0.9.4: RT used an item right after loading a save
The user's report: sometimes after booting and loading, RT used the game's own RT item (a Deku Nut).
- **Cause:** play is recognised from UI screen updates (HUD screen seen, no menu screen seen). Headless, RT was spammed
  from the title through the load. One boot showed "menu" for 20 s of play with the HUD shown and its screen not
  updating, so RT went to the game unchanged.
- **The "menu" screen** was vt 0x84c04fb0, the class of TitleMenuBG_00, MenuBG_00 and SystemBG_00: the title's
  background screen kept updating after the load. Normally its +0x1a8 (shown) is 0 after the load (two later boots).
- **Fix:** the background class counts as a menu only while shown (+0x1a8 != 0). Play is also recognised from the
  player's movement step f_809552f0 (every frame in play; not while paused or talking): play = no pause, no ocarina,
  and (movement step seen, or no menu and HUD seen).
- Verified headless (two boots, intermittent bug not hit): play recognised from the movement step as soon as the
  player can move (before the HUD screen's first update); pause clears it.
