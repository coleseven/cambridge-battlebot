#pragma once
namespace farming {
// Install once before Bot::play_turn. Called only for a live, non-channeling
// champion with a visible enemy. true = combat owns this turn; false = no actions
// were taken and farming may continue. A non-null hook replaces legacy combat.
// Shared bytes 0..5 belong to farming; bytes 6..7 are reserved for integration.
using CombatTurn = bool (*)();
inline CombatTurn combat_turn = nullptr;
}
