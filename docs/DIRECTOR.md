# Director: the strategy layer built around the trained fighters

19 Oct 2026. `bots/director/src` is the strategy layer rebuilt from what the fighter networks can and can't do
(`docs/STRATEGY_V2.md` has the design and the conflicts it fixes). Astra's forager (`bots/forager`) is
untouched; the director reuses its farming rules and tuned numbers, and replaces its combat with orders for the
fighter.

Build and play (the director never builds alone; the generator adds the fighter):

    .venv/Scripts/python tools/team_bot.py --macro-src bots/director/src --source neat:real1 --out team-director --build
    .venv/Scripts/python tools/director_eval.py team-director --pool maps_gen maps --refs forager
    .venv/Scripts/python tools/director_eval.py --var NAME=team-director:PARAM=V,PARAM=V ...   (params variants)

## How a turn works

1. **Observe** (cheap): own record, the enemy tiles bitboard (nearest enemy), states of nearby visible camp groups,
   the 8 comms bytes.
2. **Order** (one per body): heal, intercept, bank, join, shop, or farm / capture / wait on a group. The rules and
   numbers start as the forager's (+0.327 alone on the ladder maps); every threshold is in `params.h` and the
   CMA-ES space `tools/space-director.json`.
3. **Contact rule:** an enemy within `ENGAGE_RANGE`.
   - The **engage gate** (`CONTACT_GATE`, the forager's tuned rules) picks fight, flee or ignore.
   - **Fight:** the trained fighter takes the turn under a `FighterOrder` (`bots/neat/src/order.h`):
     - *goal* (the objective field; off by default, see below);
     - *leash* on the order's group field (farm `FARM_LEASH`, capture `CAPTURE_LEASH`);
     - the group's *awake monsters* as extra attack targets (`FIGHT_CAMPS`).
   - `NN_FIGHT` / `NN_HEROES` choose the network or the forager's scripted fight per hero.
4. **No contact:** the order's scripted executor (walk, clear, capture, tribute, recall, shop).
5. **Comms** (8 bytes):
   - bytes 0–5: each body's claimed group, plus a "fighting" bit;
   - bytes 6–7: the latest fight location, for `JOIN_DIST` and `SIGHT_COST`.

The fighter changes are behaviour-preserving without an order: `tools/same_games.py` gives 180/180 games
identical to the training fighter. That includes `refresh_objective()`, which the director calls in quiet turns
through `fighter::warm()`, so the fighter's field rebuild seldom lands on a fight turn.

## Results so far (19 Oct, real1 networks)

- 57 maps (17 ladder + 40 generated) × 2 seats vs Tarot, v1 and starter.
- Fitness as in `arena.fitness`.
- Head to head vs the forager, out of 114 games.

| Bot | Fitness | v1 wins | Deaths/game | vs forager |
|---|---:|---:|---:|---:|
| forager alone | +0.372 | 59 | 5.6 | — |
| director, scripted combat, engage 4 (control) | +0.356 | 56 | 5.8 | 60 |
| director, all networks, engage 4 | +0.406 | 50 | 6.5 | 49 |
| director, Varg network only | +0.410 | 67 | 5.5 | 62 |
| director, C&I network only | +0.295 | 54 | 6.1 | 59 |
| **director as shipped** (Varg network only, engage 4, fuel fixes) | **+0.414** | 67 | 5.6 | 60 |

The shipped director's head-to-head record (out of 114):
- 60 vs the forager alone (+31 gems a game);
- 71 vs the team bot with the real1 networks (+98);
- 73 vs the team bot with the hand-prior fighter (+87).

**Fuel** (release builds, engine logs, 17 maps vs Tarot):

| Build | p50 | p99 | Turns over 90k |
|---|---:|---:|---:|
| shipped | 11.8k | 60k | 0.05% |
| all-network training build | 12.0k | 78k | 0.19% |

Measured costs:
- A fighter turn costs about 10–14k to list its options, 15–22k to score them and 7–12k for the network; the
  director adds 8–11k.
- Two fixes, game-identical in training:
  - the fighter now reads the director's live-group set (`FighterOrder::live`) instead of scanning group
    states, which saved ~11k a fight turn on camp-dense maps;
  - the field builder budgets by its most expensive field.
- Probe: `--set DEBUG_FUEL=60000` (default build) prints heavy turns with their director / fighter split.

Findings:
- **The director port is sound.** With the scripted fight it plays like the forager (fitness, deaths,
  and an even head to head).
- **Order goal off.** Feeding the networks the order's goal as their objective costs a lot (+0.235 vs +0.396 on
  the ladder maps): they were trained on their own objective. It stays off (`ORDER_GOAL = 0`) until real-map
  training runs with it on.
- **The fighter's limits help.** The leash, awake-camp targets and intercepts help once the goal is off.
- **The networks are uneven per hero.** After one night of real-map training:
  - Varg's network beats the scripted fight;
  - Stellar's and Tavi's are about even;
  - Ren's and C&I's are worse.
- **Noise.** Single-parameter changes swing ±0.05–0.1 on 17 maps, so judge on all 57 (342 games per bot).

## Shared training: networks and director params on the same games

From 19 Oct the nightly real-map run trains both on the same games:

    .venv/Scripts/python tools/team_bot.py --macro-src bots/director/src --source neat:real1 --out team-director-train \
        --set NN_HEROES=4095 --runtime-params tools/space-director.json --build
    .venv/Scripts/python tools/real_train.py run real2 --warm-run real1 --team team-director-train \
        --cma tools/space-director.json [--cma-pop 8 --cma-sigma 0.08 --cma-cap 0.25]

**Params without rebuilding.** `--runtime-params` turns the space's params from `constexpr` into `inline`
variables. A generated `params_runtime.h` loads them at start-up from a `'PRMS'` block in the genome blob (word
16008, after the draft block), so each game patches a director candidate in like a network.
`tools/check_runtime_params.py` checks two things:
- no block plays exactly like the compiled defaults (16/16 games);
- a block with values V plays exactly like a build compiled with V (16/16 games).

**Balanced assignment.** Each generation CMA-ES asks 8 candidates.
- The 66 (hero, schedule slot) cells are shuffled and dealt to them round-robin.
- All 16 network candidates of a hero play a given cell with the same director candidate. The network ranking
  within a hero therefore keeps its common random numbers.
- Each director candidate gets ~130 games per generation, spread over heroes, maps and opponents. A
  separate CMA-ES run gives each candidate 102 games and needs its own compute.

**Fitness and limits.**
- Fitness is the same per-game score as the networks (±1 win/loss + gem margin / 500).
- The search stays within `--cma-cap` (0.25 of each range) of the starting values, so the networks never get
  extreme strategies to train under.
- The frozen and prior opponents keep the compiled starting params as a fixed yardstick.

**Outputs.**
- `params_mean.json` and `snapshots/params_gen####.json` every generation, next to the network snapshots.
  Revert either layer independently.
- To ship a mean: `team_bot.py --set NAME=V ...` with the values in `params_mean.json`.

## What gets learned

| Piece | How |
|---|---|
| Director thresholds (engage gate, leashes, bank / recall / shop, target scoring, join / intercept) | CMA-ES: `tools/tune.py` with `tools/space-director.json`, fighters frozen |
| Fighter networks inside the director | `tools/real_train.py run real2 --warm-run real1 --team team-director-train`, the nightly run from 19 Oct. The training build is `team_bot.py ... --out team-director-train --set NN_HEROES=4095`, so every hero's network plays. |
| Which heroes fight with a network (`NN_HEROES`) | measured per hero (table above); revisit after each training stage |
| Later: a learned order scorer (features per candidate order -> network, like the fighter) | the same NEAT machinery |
