#pragma once
namespace nativehud {
// The six item boxes drawn by the game's own HUD (RESEARCH_NOTES.md sections 69-73). Installs its hooks; false when
// the game functions it needs are missing (the overlay HUD is used then). Off with ITEMCTL_NATIVE=0.
bool Start();
// Whether the game-drawn boxes are up and kept current (the overlay HUD is not needed during play then)
bool Active();
// Whether the game-drawn boxes are set up for the current HUD (also while the HUD is not drawn, as in the pause menu)
bool Ready();
}
