# Fight win-probability model

A calibrated estimate of "will I win this fight?" for one of our bodies against one visible enemy.
It is meant for `farming::estimate_win` (`bots/forager/src/combat_hook.h`), replacing the hand
placeholder that `fight_policy.h` uses when no estimator is installed. Later it can also replace the
macro proxy's fixed "two beat one" contest rule.

## Pipeline

```bash
.venv/Scripts/python tools/fight_data.py duels                       # champion-set round robin, 242 duel replays
.venv/Scripts/python tools/fight_data.py pool --bots main v1 --vs starter-cpp sparring-tarot   # 136 5v5 games
.venv/Scripts/python tools/fight_data.py extract                     # -> .arena/fights/fights.jsonl
.venv/Scripts/python tools/fight_model.py fit [--holdout-src pool]   # evaluation + .arena/fights/model.json
.venv/Scripts/python tools/fight_model.py export                     # -> bots/forager/src/win_model.h
```

- **Examples:** every champion-vs-champion engagement found by `tools/replay/events.py` gives one row
  per body in the fight, paired with the nearest enemy body in that engagement.
- **Features:** taken from the frame just before the first hit, using only what a bot can see when it
  spots an enemy:
  - both hero types (Cassi and Issac are separate bodies);
  - both health fractions and maximum health, plus the log health ratio;
  - Chebyshev distance;
  - allies and enemies within 4 tiles, the same radius as forager's selector;
  - whether each of my two abilities is ready;
  - a team-game flag (3+ bodies per side) and its interactions with every numeric feature, because
    1v1 duels and 5v5 skirmishes behave differently.
- **Label:** the engagement winner from our body's side: more kills, else at least 25% more damage.
  Draws count as 0.5.
- **Model:** L2 logistic regression with 56 weights.
- **Header:** `win_model::estimate` caches the self-side features per round and body.
- **Fuel:** about 2.5k for the first enemy in a turn and about 0.7k for each further enemy (measured in
  a forager copy with the fight selector on). The turn budget is 100k.

## Results (16 Oct 2026: 19,590 engagements, 16,323 from duels and 3,267 from 5v5 pool games)

`main` and its frozen copy `v1` are identical code, and matches are deterministic, so their pool games
duplicate each other. `fit` drops duplicate games (54 here) before splitting. The test set is 20% of
games, held out as whole games.

| estimator | log-loss | Brier | accuracy (decisive fights) |
|---|---:|---:|---:|
| **model** | **0.476** | **0.123** | **0.832** |
| matchup only (empirical win rate per hero pair) | 0.478 | 0.127 | 0.821 |
| forager placeholder | 0.715 | 0.230 | 0.506 |
| constant | 0.693 | 0.220 | 0.497 |

Split by source:

| source | estimator | log-loss | Brier | accuracy (decisive) |
|---|---|---:|---:|---:|
| duels (n = 3167) | model | 0.468 | 0.118 | 0.843 |
| duels | placeholder | 0.714 | 0.228 | 0.502 |
| 5v5 pool (n = 402) | model | 0.541 | 0.163 | 0.756 |
| 5v5 pool | placeholder | 0.720 | 0.244 | 0.538 |

Reliability on the test set (predicted, then observed win rate): 0.07→0.05, 0.14→0.11, 0.24→0.18, 0.35→0.33,
0.45→0.38, 0.56→0.71, 0.65→0.64, 0.76→0.79, 0.85→0.92, 0.94→0.93. It is close to calibrated; the
0.5–0.6 bucket is under-confident.

In duels, most of the signal is the hero pair: matchup-only is nearly as good as the full model.
The health, distance and count features matter more in 5v5. The 5v5 test set is small (402 rows from
about 14 games), so 5v5 needs more data, ideally from trained-fighter games.

**Duels alone do not transfer to team fights.** Trained on duels only and tested on all 5v5 games,
the model is no better than the placeholder (log-loss 0.695 vs 0.674) and badly over-confident: its
0.96 bucket wins only 76%. Team-game data in training, plus the team-context interactions, fix most of
this. Keep 5v5 games in the dataset.

## Caveats

- The model scores fights as the bots in the data fought them. The duel rows come from the current
  NEAT/CGPANN champion set; the 5v5 rows come from main, v1, starter-cpp and Tarot, not our trained
  fighters. Hero weights partly measure how well each policy plays a hero, not the hero alone.
  **Refit after each champion refresh, and once the trained fighters drive 5v5 games.**
- Selection bias: only fights that happened are in the data. Fights a bot avoided never appear, so
  the estimate is "given a fight starts here", not "if I choose to fight here".
- The label is engagement-level, so it ignores what happens to bags after the fight. `fight_policy.h`
  already prices bags separately.
- Items appear only through maximum health. Ladder 5v5 builds may need extra item features, and the
  data would need item-heavy games.

## Installing it (for whoever owns forager)

```cpp
#include "win_model.h"
// once, before Bot::play_turn:
farming::estimate_win = win_model::estimate;
```

`bots/forager/src/win_model.h` is generated. Nothing in forager includes it yet, and the
`FEATURE_FIGHT_SELECTOR` switch is unchanged.
