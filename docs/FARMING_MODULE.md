# Farming module and combat integration

`bots/forager` is an isolated, buildable farming prototype based on the existing
`main` bot. It does not modify `main`, `duel`, combat weights, or the combat trainer.
It is an implemented heuristic policy, not a trained economic policy.

## Behavior

- Score camps by gems plus a configurable gold value, divided by estimated travel,
  remaining respawn wait, clear time, five capture turns, delivery distance,
  competing claims and visible enemy risk. Travel and respawn waiting overlap.
- Every body chooses independently. One shared claim byte per body discourages
  redundant assignments. Only claimed allies actually visible near a camp count
  toward estimated clearing assistance. This is a soft assignment, not a global
  optimal matching or a guarantee that two champions never choose the same camp.
- Keep an existing capture; leave another ally's capture alone. Do not choose an
  unsafe camp merely because every safe candidate has a worse score.
- Retain the baseline's camp attacks, spells, movement, recall, shopping, and
  respawn-tile clearing. These execution routines are inherited, not newly trained.
- Bank at the configured bag threshold or near an altar; winning and end-of-game
  deposits override a capture. Finish a capture before making an ordinary trip.
- Release claims during banking travel, healing/shopping trips, death, channels,
  and combat. A claim may remain until the next turn when a channel starts.
- If no safe camp is available, try to bank a carried bag or recover health.

`farm_policy.h` contains the engine-independent camp score and bank decision;
`bot.cpp` adapts game observations and executes actions. `params.h` holds controls.
The fixed draft is deliberately inherited to keep initial comparisons meaningful.

## Connecting Claude's fighter

Include `combat_hook.h` and assign `farming::combat_turn` once before the gameplay
loop, for example to a small wrapper around the trained fighter's turn function.
The wrapper returns `true` when the fighter owns the turn. It must return `false`
only if it took **no actions**, so farming can safely continue.

The hook runs for a live, non-channeling body when an enemy is visible, before
normal camp observation/scoring. It replaces legacy enemy-combat behavior when
installed; the trained fighter is not called for neutral camp combat. It must not
call `end_turn()` itself: the outer loop owns yielding.

The adapter currently initializes its world before the first hook and can shop at
spawn before calling it. It does not grant the fighter a fresh fuel budget; the
fighter must inspect remaining fuel. Initial map setup can itself span turns if
the engine suspends execution. Budget and profile the combined bot before use.

Shared memory contract:

| Bytes | Owner | Encoding |
|---|---|---|
| 0–5 | Farming | One body claim: zero means none; otherwise camp index + 1 |
| 6–7 | Reserved | Available for an agreed combat integration protocol |

Do not let the fighter overwrite farming claim bytes. Bodies are mapped using
the inherited draft memory `[0xB7, body_count, champion_type...]`; preserve this
layout or replace both draft and farming initialization together. More than 255
camp groups remain locally selectable but cannot be advertised in a claim byte.

`FARM_LEGACY_COMBAT = 1` provides the existing main bot's combat for standalone
evaluation. Set it to zero for a strictly non-attacking economic experiment.
`FARM_SHOPPING = 0` disables item purchases and shopping recalls when connecting
the no-item fighter for initial tests. Combat trained without items still needs
validation against stat-scaled enemies before enabling shopping in competition.

## Evaluation

Run from the workspace root:

```powershell
.venv/Scripts/python tools/farm_eval.py --bots main forager --jobs 2
.venv/Scripts/python tools/farm_eval.py --bots forager --vs main starter-cpp --jobs 2 --out .arena/farming/contested.json
g++ -std=c++17 tools/farm_policy_test.cpp -o .arena/farming/policy-test.exe
.arena/farming/policy-test.exe
```

The first command compares the two bots against `farm-idle`, which stays in its
protected spawn. Every selected map is run in both seats. The metric is banked
gems divided by match rounds, plus deaths, wins, and runtime faults. A passive
opponent win is only a completion check, not evidence of competitive strength.
The evaluator records per-map/per-seat scores and bot hashes in JSON and fails
if a match errors or reports a bot fault. It uses a separate farming result cache
and defaults to only two workers, so it does not contend for the combat cache.

The evaluator requests offline operation, so install the CLI's compiler and
engine assets first (`cambc build forager` and a normal local match if needed).
Do not silently fall back to network downloads during a large local suite.

Policy assertions cover arrival versus respawn timing, delivery/claim cost,
unsafe and unreachable camps, capture handoff, winning deposits, and deadlines.

## Known limits

Camp damage/clear-time estimates use full camp health and approximate DPS; they
do not model every ability, area damage arrangement, partially cleared camp,
or exact tank assignment. Claimed nearby allies are only an estimate of help.
Enemy risk uses current local vision; it is not a learned fight-value model.
Altar routing uses the nearest terrain path, not escorts or lock-aware routing.
These are explicit next experiments, not features to assume already solved.
No submission is made by any command above.
