// Event flow files (.bfevfl, "BFEVFL", version 4): small in-place edits. Layout from the evfl Python library
// (evfl-1.3.0 in the game folder): header +0x1c file size, +0x28 -> pointer to the flowchart; flowchart +0x10 actor
// count, +0x16 event count, +0x28 actors (0x38 each: +0x00 name, +0x20 query names, +0x32 query count), +0x30 events
// (0x30 each: +0x00 name, +0x08 type, switch (type 1): +0x0a case count, +0x0c actor, +0x0e query (the actor's own
// index), +0x18 case table of {u32 value, u16 event, pad} 8 bytes each). Names are u16-length strings; pointers are
// file offsets turned into addresses at load through the relocation table.
#pragma once
#include "gamefiles.h"
#include <set>
#include <string>

namespace evfl {
// Gives every switch on actor.query that has a case `copyFrom` and none for `value` a case value -> the same event.
// The longer case tables are appended to the file and the switches' existing pointers moved to them (no new pointer,
// so the relocation table stays). Switches named in `skip` are left alone. Returns the switches changed, -1 if the
// file is not an event flow.
int AddCase(gamefiles::Bytes& file, const std::string& actor, const std::string& query, uint32_t value, uint32_t copyFrom,
            const std::set<std::string>& skip, std::string* changedNames = nullptr);
}
