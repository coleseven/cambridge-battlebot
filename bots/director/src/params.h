#pragma once
// Tunable numbers. tools/tune.py rewrites the values in candidate copies, so keep each one as
// `constexpr <type> NAME = <number>;` on its own line. Search space: tools/space-director.json.
// Defaults: the forager's tuned values where the rule is the same (bots/forager/src/params.h), so the
// director starts from the farming that scored +0.327 alone.

// --- Draft ---
// Index into the composition table in draft.cpp.
constexpr int DRAFT_COMP = 0;

// --- Contact rule (who owns the turn) ---
// The trained fighter takes the turn, under the current order, when an enemy champion is this close.
constexpr int ENGAGE_RANGE = 4;
// While farming, the fighter may not drag the body farther than this (path steps) from the target group.
// -1 = no leash.
constexpr int FARM_LEASH = 5;
// While holding a capture (on a dead group's tile), the fighter stays within this many steps of the tiles.
// 0 = stays on the tiles, so the 5-round capture timer survives the fight. -1 = no leash.
constexpr int CAPTURE_LEASH = 0;
// 1 = the fighter may hit the ordered group's awake monsters during a fight (it clears what we woke).
constexpr int FIGHT_CAMPS = 1;
// 0 = scripted combat (the forager's legacy fight: stun interrupts, focus the weakest within 4) instead of
// the trained fighter; a control for the networks. 1 = the trained fighter.
constexpr int NN_FIGHT = 1;
// With NN_FIGHT = 1: bit t set = champion_type t uses the network, else scripted combat (0 ryth, 1 vesper,
// 2 vendetta, 3 cassi, 4 issac, 5 stellar, 6 prism, 7 marcella, 8 willow, 9 ren, 10 varg, 11 tavi).
constexpr int NN_HEROES = 1024;   // 19 Oct: Varg only (+0.390, 62/114 vs forager); see docs/DIRECTOR.md
// 1 = the fighter's objective is the order's goal; 0 = its own field to all live camp tiles (as trained);
// 2 = the order's goal only for heal / bank / shop orders (home or altar), its own field otherwise.
// 19 Oct, real1 networks: 0 scores +0.396, 1 scores +0.235 (they were trained on their own objective), so
// the goal stays off until real-map training runs with it on.
constexpr int ORDER_GOAL = 0;

// --- Fuel ---
// Per-group distance fields built per quiet turn (deterministic: never "while fuel lasts").
constexpr int FIELDS_PER_TURN = 8;   // 20 Oct, 21 maps: 3 -> +0.318, 8 -> +0.375, 20 -> +0.376

// --- Learned director ---
// 1 = a director network in the genome blob ('DIRN' block, tools/director_train.py) re-ranks the candidate
// orders; without the block the rule cascade below decides, exactly as before. 0 = rules only.
constexpr int LEARNED_DIRECTOR = 1;
// Turns the learned director reuses its group scores for (scoring ~20 groups costs ~13k fuel).
constexpr int SCORE_EVERY = 3;

// --- Engage gate (in contact: fight with the fighter, ignore and keep the order, or flee) ---
// 1 = decide per contact (the forager's tuned rules below); 0 = the fighter takes every contact turn.
// 19 Oct, held-out maps: the forager without a fighter (these rules) +0.487, every-contact fighter +0.395.
constexpr int CONTACT_GATE = 1;
// Fight when my health fraction is at least this and we are not outnumbered within 4 (or the kill is easy).
constexpr float FIGHT_MIN_HP = 0.43795f;
// Allowed excess of enemies over allies within 4 tiles before we count as outnumbered.
constexpr int FIGHT_SLACK = 1;
// Not fighting: flee when carrying at least this many gems (or below half health); else keep the order.
constexpr int FLEE_GEMS = 150;

// --- Target choice (forager baseline scorer) ---
// Bodies are split into this many squads (body index mod SQUADS); a squad shares one target.
constexpr int SQUADS = 2;
// Each extra squad member adds this fraction of my dps to the kill-time estimate.
constexpr float SQUAD_DPS_SHARE = 0.64195f;
// Value of one gold relative to one gem when scoring camp groups. Small but nonzero: on maps whose only gem
// camps are monoliths (contest) the harmless gold clusters are then worth farming; elsewhere gems dominate.
constexpr float GOLD_WEIGHT = 0.05f;
// 1 = when no group is safe to clear, take no target (heal / recall); 0 = the forager's best-value fallback.
constexpr int NO_SAFE_FALLBACK = 1;
// Rounds added to a group's cost when an ally has claimed it (discourages doubling up).
constexpr float CLAIM_PENALTY = 7.44221f;
// Expected damage taken must stay below this fraction of current health to farm a group.
constexpr float SAFE_DAMAGE_FRAC = 1.22705f;
// Extra rounds of cost per tile of closeness to the enemy spawn relative to ours (risk of contest).
constexpr float ENEMY_SIDE_WEIGHT = 0.39044f;
// Stay on the current target unless another scores this much better (hysteresis).
constexpr float SWITCH_MARGIN = 2.5f;
// Extra rounds of cost for a group within SIGHT_RADIUS of the latest fight an ally reported (comms).
constexpr float SIGHT_COST = 0.0f;
constexpr int SIGHT_RADIUS = 8;

// --- Health / recall ---
// Recall when health falls below this fraction of max health away from spawn.
constexpr float RECALL_HP_FRAC = 0.46146f;
// Walk instead of recalling when spawn is at most this many steps away.
constexpr int WALK_HOME_DIST = 0;

// --- Banking ---
// Walk to an altar once carrying at least this many gems.
constexpr int BANK_MIN_GEMS = 24;
// Also bank when an altar is within BANK_NEAR_DIST steps.
constexpr int BANK_NEAR_DIST = 7;

// --- Shopping ---
// Recall to shop once gold reaches this and the bag is empty.
constexpr int SHOP_RECALL_GOLD = 170;
// 0 = power shards only; 1 = one jungle emblem first; 2 = one monster emblem first.
constexpr int FIRST_EMBLEM = 1;

// --- Team play (comms) ---
// Join an ally's fight when it is at most this many path steps away (0 = never join).
constexpr int JOIN_DIST = 0;
// ... only with at least this health fraction and at most this many carried gems.
constexpr float JOIN_MIN_HP = 0.6f;
constexpr int JOIN_MAX_GEMS = 40;
// Intercept a visible enemy tribute within this many tiles (0 = never); the elected body is the closest.
constexpr int INTERCEPT_DIST = 8;
// ... only when the enemy's bag is at least this many gems.
constexpr int INTERCEPT_MIN_BAG = 20;

// > 0 = print turns above this much fuel with the stage they ended in (default builds only; diagnostics).
constexpr int DEBUG_FUEL = 0;
