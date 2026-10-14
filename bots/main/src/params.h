#pragma once
// Tunable numbers. tools/tune.py rewrites the values in candidate copies, so keep each one as
// `constexpr <type> NAME = <number>;` on its own line.

// Recall when health falls below this fraction of max health away from spawn.
constexpr float RECALL_HP_FRAC = 0.35f;
// Walk to an altar once carrying at least this many gems.
constexpr int BANK_MIN_GEMS = 1;
