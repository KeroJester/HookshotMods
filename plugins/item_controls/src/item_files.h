#pragma once
namespace itemctl {
// Registers the HUD layout and button prompt changes with vfs (call before vfs::Start)
void RegisterFilePatches();
// The vanilla RT box pane's (Pa_SelectItem_00) scale before the HUD patch set it to 0 (0: not patched yet)
float OriginalBoxScale();
}
