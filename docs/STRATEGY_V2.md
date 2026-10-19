# Strategy layer v2: a director that gives orders, fighters that execute them

Draft, 18 Oct 2026. Astra's forager was built without knowing what the trained fighters can do. This
document maps what each side does today, where they overlap or conflict in the combined bot, and proposes a
redesign that delegates what the networks have learned and uses team comms to coordinate. Evidence:
`docs/TEAM_BOT.md` (combined-bot results), `docs/FUEL_BUDGET.md` (fuel).

## 1. What each side does today

**Forager (one fixed cascade per turn, `bots/forager/src/bot.cpp` `play_turn_inner`):**
1. dead → shop
2. channelling → escort request
3. at spawn → shop
4. **[combat hook]**
5. observe groups and post comms sightings
6. fight selector (EV engage / retreat)
7. camp denial
8. tribute intercept
9. **its own combat**: stun interrupts, fight or flee (`FIGHT_MIN_HP`, `FIGHT_SLACK`, `FLEE_GEMS`)
10. low health → bank-if-adjacent or recall
11. banking (threshold, altar choice, escort request)
12. escort
13. shopping trip
14. heal at spawn
15. opening plan
16. farm: camp scoring, clear, **capture hold**, wait for respawn

**Trained fighter (per-hero network over ~11–31 options per turn, `bots/neat/src/bot.cpp`):**
- **Options:** 9 moves; attack the weakest foe in range, or hold the attack; hit a harmless camp; bank when
  adjacent to an altar; offensive spells on foes within 4; attack reset and shield; dash 2 toward or away.
- **Learned from 1v1 → 2v2 → 3v3 duels**, in the input features:
  - damage and kill-this-turn;
  - threat at the destination and health-weighted threat;
  - closing distance;
  - retreat home when hurt (`(1−hp)·home progress`);
  - stun use, and **channel × stun (interrupt a tribute or recall)**;
  - exploit stunned targets;
  - spell readiness;
  - carry-aware play (`gems·threat`, `gems·altar progress`, bank option);
  - progress toward its objective;
  - team play (focus fire on an ally's target, distance to and health of the nearest ally, local numbers
    around the target, follow-up on an ally's stun).
- **Never trained on:**
  - monster camps (its camp options only include harmless clusters, so real camps are never hit);
  - capture timers;
  - items;
  - large maps;
  - anyone telling it where to go: its "objective" is its own field to camp tiles.

## 2. Overlaps and conflicts in the combined bot

Results (17 maps × both seats, vs Tarot / v1 / starter): forager alone **+0.327**; with fighters at the best
handoff (enemy within 3) **+0.245**; hand-prior fighter at the same handoff **+0.331**; deaths 11.9 → 13.2 per
game. Fight selector variants were worse (+0.03 / −0.05). Causes visible in the code:

| # | Conflict | Effect |
|---|---|---|
| 1 | **Capture abandoned.** An enemy comes within range while a body holds a capture → the fighter takes the turn and has no idea a capture is running → leaves the tile and the 5-round timer resets | lost captures, gifted camps |
| 2 | **Camp abandoned mid-clear.** The forager wakes a camp; an enemy arrives; the fighter takes over and ignores the awake camp (it never targets monsters) → the camp keeps hitting us | more deaths (11.9 → 13.2) |
| 3 | **Objective mismatch.** In contact, the fighter moves by its own objective field (all camp tiles), not the forager's target camp, altar or escort | bodies drift off the plan |
| 4 | **Duplicated combat logic.** Fight selector, flee, stun interrupts and kill-assign re-implement things the network has learned, with hand thresholds; the selector also takes the decision away from the network | worse fights (selector variants lose) |
| 5 | **Seam at the engage radius.** Outside `FIGHTER_ENGAGE_RANGE` the forager walks and recalls without the network's threat sense (recall next to enemies gets interrupted; walking into enemies); inside it, the network acts without the forager's plan | jerky handoffs both ways |
| 6 | **Double observation cost.** Both query enemies, allies and camps and build their own fields; the fighter's real-map objective field spikes to ~74k fuel | fuel (`docs/FUEL_BUDGET.md`) |

## 3. Proposal: director + executors

**Principle:** the strategy layer decides **what and where** (orders); the network decides **how** whenever
there's contact, steering by the order instead of by its own objective.

### 3.1 Orders (strategy layer → body, every turn)

```
struct Order {
  Mode mode;           // FARM(g) CAPTURE(g) BANK(altar) ESCORT(ally) INTERCEPT(enemy) RETREAT RECALL SHOP REGROUP(point) HOLD(point)
  bitboard goal;       // tiles the body should move toward or stay on (camp group, altar ring, escort target, point)
  const distance_field* goal_field;   // the director's field to `goal` (it already builds these)
  int leash;           // how far contact may pull the body from `goal` before the director takes back over
  bool locked;         // e.g. CAPTURE with <= 2 rounds left: stay on the tile unless lethal
};
```

The director keeps everything the network can't do or hasn't learned:
- camp scoring and assignment, clearing monster camps, the capture protocol;
- bank timing and altar choice;
- recall and shop timing, and the build order (items);
- respawn timers, the opening plan, team-level comms decisions: intercept election, escorts, regroups,
  kill assignment, camp denial.

### 3.2 Executors

- **Contact** (an enemy within `ENGAGE_RANGE`, or a champion hit us last turn): the **network acts**, with its
  "objective" input replaced by the order's `goal_field`. Its learned "progress toward objective" then means
  "progress toward the order".
  - BANK: the objective is the altar ring; the network's bank option and carry features already fit.
  - CAPTURE / FARM: the objective is the camp group, so it fights *around* the capture instead of abandoning it.
  - INTERCEPT: the objective is the carrier; its channel × stun feature does the rest.
  - `leash` and `locked` bound how far a fight can drag a body off its order.
- **No contact:** the scripted farm executor, as today (walk, clear, capture, tribute).
- **Delete when the fighter is attached** (keep for standalone testing): the forager's own combat (step 9),
  the fight selector (6), flee, and stun-interrupt logic. The network learned these; the duplicates either
  fight it or replace it with hand thresholds.
- **Recall:** decided by the director, but with enemies within `ENGAGE_RANGE` the network gets the turn first
  (it retreats home when hurt), and the director only starts a recall channel out of contact (no interrupted
  recalls).

### 3.3 Comms change how the bodies operate

The 8-byte bus (Astra's `team_comms.h`) turns the director from per-body into a **team planner**:
- **Roles:** the team belief (enemy positions with age, bags, respawns, item spikes) lets the director assign
  roles per body: farmer, carrier, escort, interceptor, regroup. Example: two bodies converge on a contested
  camp when an enemy group approaches, so the network's team features (focus fire, allies near target) get
  used in planned team fights instead of chance encounters.
- **Intercepts:** an INTERCEPT order is issued to the elected body, and the network executes it. Its channel ×
  stun skill is exactly the interrupt the intercept rule needs.
- **Later: comms as network inputs.** Zero-initialised inputs from the bus (an ally committed to my target, an
  ally in danger, enemy carrier out of sight), learned during real-map training. This adds coordination beyond
  vision without touching the duel-trained weights.

### 3.4 Fuel and observations

The director computes observations once per turn (visible champion records, live groups, its per-group
fields) and passes them to the fighter (`docs/ASTRA_FUEL_BRIEF.md` item 4). That removes the fighter's
duplicate queries and its real-map objective-field spike. Budget split stays in `bots/neat/src/budget.h`.

## 4. What gets learned (and how)

| Layer | How it's tuned |
|---|---|
| Fighter networks (contact execution, with order objectives) | NEAT, real-map training inside the combined bot (`tools/real_train.py`). They must re-adapt once the objective comes from orders. |
| Director thresholds (engage range, leash, lock rules, bank/recall/shop thresholds, role rules) | CMA-ES (`tools/tune.py` + `tools/space-team.json`), with the fighters frozen |
| Later: a learned director (scoring orders like the fighter scores moves) | the same NEAT machinery, against the league (`docs/ROADMAP.md` §4d) |

Training order follows the plan: frozen NN snapshots before each stage, revert instead of retrain, and NN
training and director CMA-ES as separate stages until we decide otherwise.

## 5. Interface changes needed

- **Fighter (Claude):**
  - `fighter::take_turn(const Order&, const Observation&)` uses `order.goal_field` as its objective field and
    the shared observations;
  - behaviour-preserving when the order's goal equals its old objective (checked with `tools/same_games.py`);
  - real-map training passes real orders.
- **Director (Astra):**
  - the Order struct and the mode logic above;
  - the Observation struct;
  - delete the duplicated combat paths when the fighter is attached;
  - `leash` and `lock` rules;
  - comms-based role assignment;
  - fuel within the `MACRO` + `COMMS` shares.

## 6. Order of work

1. **Now (tonight):** real-map training (`real1`) with today's hook, so the networks start unlearning
   pure-fight habits.
2. Fighter API with Order/Observation, with the objective from the order (no behaviour change when the goal
   equals the old objective).
3. Director v2 MVP:
   - FARM / CAPTURE / BANK / RECALL / SHOP orders;
   - the contact rule;
   - delete duplicated combat;
   - capture lock;
   - measure against the forager alone and the current team bot on the full pool.
4. Real-map training on v2 orders (warm start from `real1`).
5. CMA-ES of the director's thresholds.
6. Comms roles (ESCORT / INTERCEPT / REGROUP), then comms as network inputs.
7. Items (build blob), then mixed 3v3 / 4v4 / 5v5 (per the plan).
