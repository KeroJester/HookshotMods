// Changed game files without files on disk: when the game opens a registered RomFS path, it reads a patched copy
// held in memory. The copy is made from the file it would have read (another mod's or the game's own), so mods that
// replace the same file still apply underneath. Hooks Hookshot's nn::fs functions (OpenFile, ReadFile, GetFileSize,
// CloseFile); every other file goes to Hookshot untouched.
#pragma once
#include "gamefiles.h"
#include <functional>
#include <memory>
#include <string>

namespace vfs {

// patch(file bytes as stored in the RomFS) -> true if it made the changed copy (in place). False: the game reads the
// file unchanged. Runs once, on a worker thread started by Start() (or at the first open, whichever comes first).
using Patch = std::function<bool(gamefiles::Bytes& file)>;
void Register(const std::string& romfsRel, Patch patch);

// A file whose content depends on the moment: live() runs at each open of that path (on the opening thread; keep it
// quick) and returns the bytes to serve for this open, or null for the file as it would be otherwise.
using Live = std::function<std::shared_ptr<const gamefiles::Bytes>()>;
void RegisterLive(const std::string& romfsRel, Live live);

// Installs the hooks and starts making the copies. False: no file is changed (logged).
bool Start();

}  // namespace vfs
