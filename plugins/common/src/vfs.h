// Changed game files without files on disk: when the game opens a registered RomFS path, it reads a patched copy
// held in memory. The copy is made from the file it would have read (another mod's or the game's own), so mods that
// replace the same file still apply underneath. Hooks Hookshot's nn::fs functions (OpenFile, ReadFile, GetFileSize,
// CloseFile); every other file goes to Hookshot untouched.
#pragma once
#include "gamefiles.h"
#include <functional>
#include <string>

namespace vfs {

// patch(file bytes as stored in the RomFS) -> true if it made the changed copy (in place). False: the game reads the
// file unchanged. Runs once, on a worker thread started by Start() (or at the first open, whichever comes first).
using Patch = std::function<bool(gamefiles::Bytes& file)>;
void Register(const std::string& romfsRel, Patch patch);

// Installs the hooks and starts making the copies. False: no file is changed (logged).
bool Start();

}  // namespace vfs
