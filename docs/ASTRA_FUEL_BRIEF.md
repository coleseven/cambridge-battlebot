# Brief for Astra: fit the strategy layer and team comms into a fixed fuel budget

Paste-ready (18 Oct 2026). Read `docs/FUEL_BUDGET.md` first: it has the measurements and the split.

## Context
- Every champion turn has 100,000 fuel on the platform; running out suspends the bot mid-turn.
- The trained fighter plugs into `bots/forager` through `combat_hook.h` and needs its share every turn it
  fights.
- The split is in `bots/neat/src/budget.h` (`MACRO` 25k, `COMMS` 8k, `FIGHTER` 57k, `SAFETY` 10k). Mirror the
  same constants in forager's `params.h` (or include `budget.h`), so they're one edit away.

## Measured (release builds, engine fuel logs via `tools/fuel_engine.py`, real 5v5 maps vs sparring-tarot)

| Forager variant | p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|
| as shipped (switches off) | 11k | 23k | 35k | 85k |
| every `FEATURE_*` switch on | 30k | 83k | **100k** | **100k** |

With everything on, the strategy layer alone sometimes runs out of fuel. Combined with the fighter (~46k
typical) that cannot fit.

## Task
1. **Measure per feature.** For each `FEATURE_*` switch, measure p50 / p99 / max per-turn fuel on the real
   map pool (`tools/fuel_engine.py forager ...`; add a variant per switch). Find what costs the most.
   Suspects:
   - recomputing team belief / estimators every turn instead of incrementally;
   - full-map scans;
   - `cambc::champions()` (~43k), `vision::camp_groups()` (~28k), `map::camp_groups()` (~20k);
   - distance fields built every turn.
2. **Fit the budget:** strategy layer ≤ `MACRO` (25k) and comms ≤ `COMMS` (8k) at **p99**, with the
   one-off map setup allowed to spill over the first few turns only.
   - Spread expensive work across turns: round-robin bodies or every-N-rounds updates.
   - Cache static map facts once.
   - Never call `cambc::champions()` per turn.
3. **Work caps by count, not by fuel left, wherever a decision depends on them,** so a strategy behaves the
   same whatever the fighter spent. A fuel check is fine only as an emergency brake.
4. **Expose observations for the fighter.** The fighter currently queries visible enemies and allies,
   visible camps, and builds its own objective distance field. Its real-map observe step spikes to ~74k
   because of that field. Publish what the strategy layer already computes (visible champion records,
   live camp groups, per-group distance fields) in a small read-only struct the combat hook can pass to
   the fighter. Claude will adapt the fighter to use it, keeping its features identical.

## Deliverables
- Per-feature fuel table in `docs/TEAM_COMMS.md` or `docs/FARMING_MODULE.md`.
- Changes so that every switch you recommend turning on keeps the strategy layer ≤ 25k and comms ≤ 8k at
  p99 on the real pool.
- The observation struct for the combat hook (interface plus a short doc).
- Don't edit `bots/neat`, `bots/main`, `bots/duel` or `tools/*train*.py`.
