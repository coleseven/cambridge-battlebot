#pragma once
// Tunable numbers. tools/tune.py rewrites the values in candidate copies, so keep each one as
// `constexpr <type> NAME = <number>;` on its own line.

// --- Draft ---
// Index into the composition table in draft.cpp.
constexpr int DRAFT_COMP = 0;

// --- Target choice ---
// Bodies are split into this many squads (body index mod SQUADS); a squad shares one target.
constexpr int SQUADS = 2;
// Each extra squad member adds this fraction of my dps to the kill-time estimate.
constexpr float SQUAD_DPS_SHARE = 0.8f;
// Value of one gold relative to one gem when scoring camp groups.
constexpr float GOLD_WEIGHT = 0.25f;
// Rounds added to a group's cost when an ally has claimed it (discourages doubling up).
constexpr float CLAIM_PENALTY = 25.0f;
// Expected damage taken must stay below this fraction of current health to farm a group.
constexpr float SAFE_DAMAGE_FRAC = 1.0f;
// Extra rounds of cost per tile of closeness to the enemy spawn relative to ours (risk of contest).
constexpr float ENEMY_SIDE_WEIGHT = 0.15f;
// Stay on the current target unless another scores this much better (hysteresis).
constexpr float SWITCH_MARGIN = 1.25f;

// --- Health / recall ---
// Recall when health falls below this fraction of max health away from spawn.
constexpr float RECALL_HP_FRAC = 0.3f;
// Walk instead of recalling when spawn is at most this many steps away.
constexpr int WALK_HOME_DIST = 8;

// --- Banking ---
// Walk to an altar once carrying at least this many gems.
constexpr int BANK_MIN_GEMS = 40;
// Also bank when carrying at least BANK_NEAR_MIN gems and an altar is within BANK_NEAR_DIST steps.
constexpr int BANK_NEAR_MIN = 10;
constexpr int BANK_NEAR_DIST = 6;

// --- Shopping ---
// Recall to shop once gold reaches this and the bag is empty.
constexpr int SHOP_RECALL_GOLD = 150;
// 0 = power shards only; 1 = one jungle emblem first; 2 = one monster emblem first.
constexpr int FIRST_EMBLEM = 1;

// --- Fighting champions ---
// Fight a visible enemy when my health fraction exceeds theirs by this margin (or they are killable).
constexpr float FIGHT_HP_EDGE = 0.15f;
// Flee from enemies when carrying at least this many gems and not clearly winning.
constexpr int FLEE_GEMS = 20;
