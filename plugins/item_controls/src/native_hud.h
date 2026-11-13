#pragma once
namespace nativehud {
// The six item boxes drawn by the game's own HUD and on the pause menu's Items tab (RESEARCH_NOTES.md sections 69-75).
// Installs its hooks; false when the game functions it needs are missing (no boxes then). Off with ITEMCTL_NATIVE=0.
bool Start();
}
