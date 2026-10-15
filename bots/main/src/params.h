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
constexpr float SQUAD_DPS_SHARE = 0.64195f;
// Value of one gold relative to one gem when scoring camp groups.
constexpr float GOLD_WEIGHT = 0.0f;
// Rounds added to a group's cost when an ally has claimed it (discourages doubling up).
constexpr float CLAIM_PENALTY = 7.44221f;
// Expected damage taken must stay below this fraction of current health to farm a group.
constexpr float SAFE_DAMAGE_FRAC = 1.22705f;
// Extra rounds of cost per tile of closeness to the enemy spawn relative to ours (risk of contest).
constexpr float ENEMY_SIDE_WEIGHT = 0.39044f;
// Stay on the current target unless another scores this much better (hysteresis).
constexpr float SWITCH_MARGIN = 2.5f;

// --- Health / recall ---
// Recall when health falls below this fraction of max health away from spawn.
constexpr float RECALL_HP_FRAC = 0.46146f;
// Walk instead of recalling when spawn is at most this many steps away.
constexpr int WALK_HOME_DIST = 0;

// --- Banking ---
// Walk to an altar once carrying at least this many gems.
constexpr int BANK_MIN_GEMS = 24;
// Also bank when carrying at least BANK_NEAR_MIN gems and an altar is within BANK_NEAR_DIST steps.
constexpr int BANK_NEAR_MIN = 1;
constexpr int BANK_NEAR_DIST = 7;

// --- Shopping ---
// Recall to shop once gold reaches this and the bag is empty.
constexpr int SHOP_RECALL_GOLD = 170;
// 0 = power shards only; 1 = one jungle emblem first; 2 = one monster emblem first.
constexpr int FIRST_EMBLEM = 1;

// --- Fighting champions ---
// Engage visible enemies when my health fraction is at least this and we are not outnumbered nearby.
constexpr float FIGHT_MIN_HP = 0.43795f;
// Allowed excess of enemies over allies within 4 tiles before we count as outnumbered.
constexpr int FIGHT_SLACK = 1;
// Flee from enemies when carrying at least this many gems and not clearly winning.
constexpr int FLEE_GEMS = 150;

// --- Pathing / coordination switches ---
// 1 = route around every camp tile (except the target's); 0 = terrain only.
constexpr int CAMP_WALLS = 0;
// 1 = keep my squad claim while recalling, shopping or dead; 0 = clear it.
constexpr int KEEP_CLAIM = 0;
// 1 = leave a dead group to the ally already standing on it and move to the next target.
constexpr int CAPTURE_HANDOFF = 0;
