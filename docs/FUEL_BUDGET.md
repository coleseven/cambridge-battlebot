# Per-turn fuel budget

Each champion gets **100,000 fuel per turn** on the platform (2× on its first turn). Running out suspends the
bot mid-turn. The trained fighter, the strategy layer (farming, banking, recall, shopping: `bots/forager`)
and team comms all share that turn, so each gets a share. **All numbers live in
`bots/neat/src/budget.h`**; change them there.

| Share | Default | What it covers |
|---|---:|---|
| `SAFETY` | 10,000 | Never planned for: engine overhead, rare spikes |
| `MACRO` | 25,000 | Strategy layer: camp choice, banking, recall, shopping |
| `COMMS` | 8,000 | Team comms read/write and shared-belief update |
| `FIGHTER` | 57,000 | `TURN − SAFETY − MACRO − COMMS` |

## Rule: the fighter's decisions never depend on fuel left

Before 18 Oct the network re-ranked options until a fuel cap or floor was hit. When cut short, the bot fell
back entirely to the linear prior's choice. In 2v2/3v3 training that fired on heavy turns (vs the new code:
9/90 2v2 and 22/90 3v3 games differ; 1v1 never). In a full bot, the strategy layer's spending would have
switched the network off far more often, so it would play differently from how it was trained.

Now the network always re-ranks exactly `NET_TOP_K` (12) options. Fuel is checked once, as an emergency
brake (`NET_EMERGENCY_FLOOR`, 30k left before the network runs). Checked with `tools/same_games.py`: the
brake at 30k vs disabled gives **270/270 identical games** in 1v1, 2v2 and 3v3 arenas, so it never fires in
training. It only fires in a full bot when the other layers overspend.

Any fuel optimisation of the fighter must keep games identical: run `tools/same_games.py` with the brake
disabled in both sources.

## Measurements (18 Oct 2026, release builds, engine fuel logs, first turns excluded)

`tools/fuel_engine.py` reads `team-a.fuel` / `team-b.fuel` from the replay bundle, the engine's own per-turn
fuel, so it works for `--release` builds. Release and default builds play identical games (15/15,
`tools/fuel_study.py`).

**Whole bots, real 5v5 maps vs sparring-tarot (5 maps, both seats):**

| Bot | p50 | p90 | p99 | max | > 90k |
|---|---:|---:|---:|---:|---:|
| forager as shipped (farm, bank, shop) | 11k | 23k | 35k | 85k | 0% |
| v1 (macro + shopping) | 11k | 23k | 34k | 84k | 0% |
| forager, every `FEATURE_*` on (Astra's comms + strategies) | 30k | 83k | **100k** | **100k** | 6.5% |
| fighter, before 18 Oct | 52k | 77k | 97k | **100k** | 2.0% |
| **fighter, now** | **46k** | 71k | 85k | 100k | 0.5% |

**Fighter in training arenas (fighter vs fighter):**

| | 2v2 p50 / p99 / max | 3v3 p50 / p99 / max |
|---|---|---|
| before | 56k / 81k / 88k | 57k / 83k / 88k |
| **now** | **46k / 65k / 73k** | **46k / 66k / 77k** |

**Fighter stages now** (`tools/fighter_probe.py`, p50 / p99):

| Stage | 2v2 arena | 3v3 arena | 5v5 real |
|---|---|---|---|
| observe (queries, objective field) | 11k / 15k | 13k / 17k | 16k / **74k** |
| score every option (11–31 options) | 16k / 31k | 20k / 31k | 16k / 27k |
| network, top 12 | 14k / 21k | 13k / 20k | 13k / 18k (p90) |

**Fuel-saving changes, all checked game-identical (180/180):**
- the objective distance field is cached until the set of live camp groups changes;
- destination- and target-only feature terms are computed once per turn (several options share a
  destination);
- per-turn host calls are hoisted out of the option loop (`can_move`, `can_attack`, camp positions);
- one record query per champion instead of one per getter.

## How much does network budget matter? (decision vs budget)

Share of turns where re-ranking only the top K options picks the same option as the full top 12 (K = 0:
the linear prior alone):

| K | 0 | 4 | 6 | 8 | 10 | 12 |
|---|---:|---:|---:|---:|---:|---:|
| 2v2 arena | 57% | 67% | 76% | 90% | 96% | 100% |
| 3v3 arena | 45% | 51% | 68% | 91% | 94% | 100% |
| 5v5 real | 40% | 46% | 69% | 96% | 98% | 100% |

The network changes about half of all decisions, and the full top 12 costs only ~14k. K = 8–10 would save
~3–5k for ~90–98% agreement if fuel ever gets tight.

## Open items

1. **Real-map observe spikes (p90 66k, max 78k):** the fighter rebuilds its own distance field to the
   objective over the whole map when camp groups change state. In the combined bot it should use the
   forager's per-group fields (the strategy layer already builds them) instead of its own. Integration work.
2. **Astra's features with everything on reach 100k:** comms and comms-driven strategies must fit
   `COMMS` + `MACRO` (≤ 33k together). See the brief in `docs/ASTRA_FUEL_BRIEF.md`.
3. When the fighter is attached to the forager: measure the combined bot with `tools/fuel_engine.py`. The
   fighter keeps its training behaviour as long as the brake doesn't fire; count how often it does.
4. cambc has a newer CLI out (seen 18 Oct). Upgrade both PCs together and re-check release/default parity
   and the fuel figures.
