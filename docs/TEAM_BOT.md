# Combined bot: strategy layer + trained fighters, and the strategy CMA-ES

`tools/team_bot.py` generates `bots/<out>/src` from Astra's strategy layer (`bots/forager/src`) and our fighter
(`bots/neat/src`) without editing either:
- the fighter's `bot.cpp` is wrapped in `namespace fighter` as `fighter_bot.cpp`, with `fighter::take_turn(range)`
  and `fighter::prepare()`;
- `genome.cpp` is baked from a champion set (`tools/bake_fighter.py`);
- `main.cpp` installs the combat hook: the fighter takes the turn only when an enemy is within
  `FIGHTER_ENGAGE_RANGE`, otherwise the strategy layer keeps it;
- `params.h` gets the integration knobs `FIGHTER_ENGAGE_RANGE` and `FIGHTER_WIN_MODEL` (the fight selector uses
  `win_model.h`), so CMA-ES can tune them with the strategy numbers.

```bash
.venv/Scripts/python tools/team_bot.py --source champ:3 --out team --build     # 3v3 champion networks
.venv/Scripts/python tools/team_bot.py --source prior   --out team-prior --build  # control: hand prior
.venv/Scripts/python tools/tune.py prepare <run> --base team --space tools/space-team.json --vs sparring-tarot v1 starter-cpp --popsize 8
.venv/Scripts/python tools/tune.py run <run> --gens N --jobs 12
```

`tools/space-team.json`: engage range plus the forager's main levers (recall HP, bank thresholds, shopping trigger,
fight HP/slack, flee bag, safe damage, claim penalty, enemy-side weight, squad DPS share, switch margin).
One generation of 8 candidates × 3 opponents × 17 maps × 2 seats takes ~3.5 min at 12 jobs on the main PC.

## First results (18 Oct 2026, all 17 published maps, both seats, vs sparring-tarot / v1 / starter-cpp)

| Variant | Fitness | vs Tarot | vs v1 | vs starter | Gem margin | Deaths/game |
|---|---:|---:|---:|---:|---:|---:|
| forager alone (no trained fighters) | **+0.327** | 25/34 | **19/34** | 32/34 | +328 | **11.9** |
| team, engage range 1 | −0.100 | 16 | 3 | 32 | +51 | 15.7 |
| team, range 2 | +0.088 | 18 | 7 | 33 | +164 | 14.7 |
| **team, range 3** | +0.245 | 21 | 15 | 33 | +260 | 13.2 |
| team, range 4 | +0.242 | 22 | 12 | 33 | +196 | 12.7 |
| team, range 6 | +0.123 | 18 | 12 | 33 | +164 | 13.0 |
| range 3 + fight selector | +0.031 | 15 | 6 | 34 | +126 | 15.2 |
| range 3 + selector + win model | −0.048 | 12 | 8 | 33 | +93 | 15.9 |
| range 3, **hand-prior** fighter (control) | **+0.331** | 24 | 15 | 34 | +344 | 14.5 |

The first version gave the fighter every turn with any enemy in view: 14/34 vs Tarot, 11/34 vs v1, 0 faults in
204 games.

## What this means

- **The integration works:** 0 faults, the hook hands turns back and forth, and the fuel split is in place
  (`docs/FUEL_BUDGET.md`).
- **The arena-trained networks don't transfer to real 5v5 maps yet.** On real maps they're no better than the
  hand prior (+0.245 vs +0.331 at range 3), and attaching them is slightly worse than the forager alone. This
  is a domain gap. They trained only in 30×30 arenas with harmless gem clusters: no monster camps, no large
  maps, no items, no strategy layer deciding when they fight.
- **Handoff matters:** range 3–4 is best. Fighting whenever an enemy is anywhere in view is clearly bad, and the
  current fight selector hurts.

## Next stage (proposed)

**Train the fighters inside the combined bot on real maps.**
- The strategy layer plays everything except close fights. Fitness is the real game result (arena fitness: win
  + gem margin) against an opponent pool: Tarot, v1, the forager alone, and frozen earlier team versions.
- The combined bot contains the same patchable genome block, so `dist.BlobBot` candidates and the distributed
  workers apply unchanged. The main change is the evaluation: real maps, 5v5 (later 3v3–5v5 per the plan),
  full-game score instead of duel score.
- **Freeze a fighter snapshot before this stage** (plan: revert instead of retrain if it degrades). CMA-ES on
  the strategy numbers and NN training stay separate stages until we decide how to interleave them.
- Then: CMA-ES the strategy layer around the adapted fighters; then comms and items (Astra's work, within the
  fuel budget).

## Real-map fighter training (`tools/real_train.py`, first run night of 18-19 Oct)

- NEAT on the per-hero networks inside the combined bot, on the 17-map pool, scored by real 5v5 games:
  +1 win / -1 loss + gem margin / 500.
- Each candidate for hero p plays `--games` games in a team drafted around p. The composition travels in the
  genome blob's draft block (word 16000, magic `DRFT`), which the generated `draft.cpp` reads, so every hero
  trains, including ones the forager never drafts (Vesper, Marcella, Willow). Verified: a blob drafted
  Marcella / Willow / Vesper / Prism / Tavi and won 1044-720 vs starter.
- Opponents: sparring-tarot, v1, a fixed copy of the forager alone, and `frozen` (our full bot with the
  networks the run started from).
- **Revert point:** `frozen_start.json` holds the warm run's elites, untouched by training. Every
  generation is also exported to `snapshots/`.
- The run snapshots `bots/team/src` as its fixed base (`team_src/`).
- Smoke test: 88 games per generation in ~33 s at 12 jobs, 0 errors, clean resume. Full settings (pop 16,
  6 games per candidate, ~1,056 games per generation) take ~6-7 min per generation on the main PC.
- **Night mode switch:** with `.arena/night_real.flag` present, `tools/night_worker.cmd` runs
  `real_train.py run real1 --warm-run n3v3 --pop 16 --games 6 --until 11:50` instead of the distributed
  worker. Delete the flag to go back to helping the training PC. Crashes restart and resume.
- It runs on the main PC only: the combined bot is generated from Astra's uncommitted forager, so the
  training PC can't build it. Meanwhile the training PC continues 3v3 alone.
