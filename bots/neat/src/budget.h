#pragma once
// Per-turn fuel budget: one place to change the split. See docs/FUEL_BUDGET.md for the measurements behind
// these numbers and how to re-measure (tools/fuel_engine.py, tools/fighter_probe.py).
//
// The platform gives each champion 100,000 fuel per turn (2x on its first turn); running out suspends the
// bot mid-turn. The fighter, the strategy layer (farming, banking, shopping) and team comms share a turn,
// so each gets a share. The fighter's DECISIONS must not depend on how much fuel is left (otherwise it
// plays differently in a full bot than it did in training), so its work is capped by counts below, not by
// fuel checks; the fuel shares are targets to measure against.
#include <cstddef>
#include <cstdint>

namespace budget {
constexpr std::uint64_t TURN = 100000;      // platform limit per champion turn
constexpr std::uint64_t SAFETY = 10000;     // never planned for: engine overhead, rare spikes
constexpr std::uint64_t MACRO = 25000;      // strategy layer: camps, banking, recall, shopping
constexpr std::uint64_t COMMS = 8000;       // team comms read/write + shared belief update
constexpr std::uint64_t FIGHTER = TURN - SAFETY - MACRO - COMMS;   // 57,000

// Fighter work caps (deterministic).
constexpr std::size_t NET_TOP_K = 12;       // options the network re-ranks (top K of the linear prior)
// Emergency brake only: skip the network when less than this is left before it runs (its top-12 cost is
// ~14k typical, ~25k worst measured). In training the fighter has the whole turn, so this never triggers
// there; in a full bot it only fires if the other layers overspend their shares.
constexpr std::uint64_t NET_EMERGENCY_FLOOR = 30000;
}  // namespace budget
