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
// when: checked at each open; while it is false the game reads the file unchanged (e.g. rstb::Applied for files that
// grow beyond their resource size table entry).
void Register(const std::string& romfsRel, Patch patch, std::function<bool()> when = nullptr);

// A file whose content depends on the moment: live() runs at each read of that path (on the reading thread; keep it
// quick) and returns the bytes to serve for that read, or null for the file as it is. Asked per read, not per open,
// because the game keeps some files open (the sound streams of single notes). The bytes must keep the file's size.
using Live = std::function<std::shared_ptr<const gamefiles::Bytes>()>;
void RegisterLive(const std::string& romfsRel, Live live);

// Installs the hooks and starts making the copies. False: no file is changed (logged).
bool Start();

}  // namespace vfs
