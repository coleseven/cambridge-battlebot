# N-vs-N fight training (2v2 first; 3v3 to 5v5 by flag)

Stage two of the fighting curriculum: the per-hero networks of the 1v1 runs (NEAT `n2`, CGPANN `c2`)
keep evolving in N-v-N team fights. Team size is one parameter, `--team-size N`. Brief:
`docs/NVN_TRAINER_BRIEF.md`.

## What changed

| File | Change |
|---|---|
| `bots/neat/src/weights.h` | `NF` 25 → 31: six team features (below). `NF_V1 = 25` names the duel feature count. New prior columns are 0. |
| `bots/neat/src/bot.cpp` | Team features; blob format 2 (hidden slots from 31) and format 1 still read (slots shifted on load); duel path is unchanged code (see *Equivalence*); team path caches the objective field; `BOT_FUEL` probe build. |
| `bots/neat/src/genome.h` | Documents formats 1 and 2. |
| `tools/nvn.py` | **New.** Shared NvN logic: map lookup (and on-demand map writing), program migration, champion sets, matchup schedule, evaluation and fitness credit; `equiv`, `fuel`, `play` test commands. |
| `tools/neat_train.py` | `--team-size N` → `run_nvn` (warm start = the 1v1 populations widened to 31 inputs); blobs are format 2; exports carry `nf` and `team_size`. 1v1 path unchanged. |
| `tools/cgp_train.py` | Same, genome code only (`widen`, per-genome `nf`). 1v1 genomes keep `nf` 25, so 1v1 mutation draws are unchanged. |
| `tools/champions.py`, `tools/league.py` | `--team-size N` (sampled team pairings, both layouts, both seats); read old and new program files. |
| `tools/train_cycle.py` | `--team-size N`, `--warm-neat/--warm-cgp`, population and schedule flags; 1v1 commands unchanged. |
| `tools/duel_maps.py` | `make_map`/`map_name` helpers; default `--out maps_duel/<N>v<N>`; `--sample K` for 3v3+. 1v1 maps are byte-identical to before. |

1v1 defaults and behaviour are unchanged. The 1v1 trainers write the same games (same wasm logic,
same maps). Their exported programs now use the format-2 slot layout and say `"nf": 31`; files without
`nf` (all files written before this change) are treated as 25-feature files and migrated on read.

## Team features (option columns 25–30)

All are per scored option (move/attack/spell/dash/camp/bank), against the option's target foe (or the
nearest visible foe):

| # | Feature |
|---|---|
| 25 | focus fire: number of allies whose attack range covers the target, if this option damages it |
| 26 | distance from the option's destination to the nearest ally, /10 |
| 27 | that ally's health fraction |
| 28 | allies within 3 tiles of the target |
| 29 | other enemies within 3 tiles of the target |
| 30 | ally crowd control: the target is stunned while an ally is on it, and this option damages or closes in |

No shared memory is used. The game reports only stuns (`buffs().has_stun()`), so "crowd control from an
ally" is inferred as a stunned target with an ally in range.

Warm-start safety: the prior's new columns are 0, NEAT genomes get 6 unconnected inputs (added to the
pickled config's `input_keys`), and CGP genomes get 6 unused source indices. A warm-started network
therefore scores every option exactly as in 1v1. Mutation can connect the new inputs from the first
2v2 generation on.

## Blob formats and the two code paths

Format 1 (all existing blobs) puts hidden slots at 25; format 2 puts them at 31. The bot reads both.
After loading, a net that reads **no** team input (every 1v1 net) is mapped back to the 25-slot layout,
and the turn runs `play<false>()`. That instantiation contains no team-feature code, so it is the old
duel code instruction for instruction. A net that reads a team input runs `play<true>()`.

Fuel is the reason this matters. The bot cuts the NN shortlist short when `fuel_remaining() < 15000`
(absolute, besides the 35k NN cap). Varg turns, for example, often reach the network with only about
10k fuel left, so the number of re-ranked options depends on the exact fuel spent earlier in the turn.
A first version that always computed the team features changed 6 of 48 equivalence games, all in Varg
games, because of this. Splitting the paths fixed it.

## Equivalence test

`tools/nvn.py equiv` plays the live 1v1 champion set against the hand prior on random 1v1 duel maps,
both seats, in three ways: the old `bots/neat` (origin/main) with the old blob, the new bot with the
same format-1 blob, and the new bot with the migrated format-2 blob. It compares the full result
records (outcome, round, per-team kills, deaths, gold, damage dealt and taken, gems, faults, drafts).

```
new-v1 : 120/120 games identical to the old bot
new-v2 : 120/120 games identical to the old bot
maps 60, games per variant 120, errors 0, total kills (old) 578
EQUIVALENT
(earlier 24-map run: 48/48 and 48/48, EQUIVALENT)
```

## Fuel

`tools/nvn.py fuel --team-size 2` builds a `BOT_FUEL` copy that logs each body's worst turn (turn 1
excluded, since it gets 2x fuel and runs init). It forces the team path by adding a zero-weight node
that reads all six team features to each champion network.

Over 3 full 2v2 games (all bodies, about 22k network turns per path):

| path | NN cut short by the 15k floor | pre-NN fuel p50 / p99 / max |
|---|---|---|
| duel path (current bot) | 1.1% | 45k / 67k / 91k |
| team path (team features on, objective field cached) | 0.2% | 44k / 60k / 81k |

The worst whole turn over 4 games is 94,882 fuel (team path) against 96,328 (duel path). That's close to
100k by design: the network keeps re-ranking until about 15k fuel is left, so the whole turn tops out
near 85k plus one network evaluation plus executing the move. The team features cost less than the
objective-field cache saves. On the team path the NN cap stays at 35k, and the floor remains the real
guard. If more headroom is wanted, raise the floor to 20k (it applies only to the team path if done
inside `play<true>`).

## Matchups, credit and retention (tools/nvn.py)

The schedule is per generation and per hero `h`, seeded by (generation, hero), so it is identical for
every candidate of `h` (common random numbers) and reproducible on resume:

* `--pairings K` team pairings: the candidate's team is `h` plus N−1 random allies (no duplicate hero),
  against a random team of N heroes (mirrors allowed). The layout is drawn per pairing, and each pairing
  is played in both seats. The first ⌈K/2⌉ pairings face the evolving N-v-N champion set and the rest
  the fixed hand-prior anchor.
* `--duels D` 1v1 duels for retention, both seats, with the same elite/anchor split.
* Allies play the champion set's networks. The champion set is `champions/current_<N>v<N>.json`, or
  until `champions.py --team-size N` has run once, the 1v1 set (here, then in `--warm-arena`); any
  hero still missing falls back to the run's own elite.

There are 55 two-hero teams, so 3,025 ordered team pairings per layout. The brief's ~4,400 counted
differently; it doesn't change the design.

**Fitness credit.** Each game's team score (`duel_train.side_score`: kill difference + gem
difference/50 + 0.1 × damage difference/1000) goes to the candidate genome, minus the usual small
complexity cost. Only one member of a candidate's team is a learner. The allies are fixed champion
networks, the same for every candidate of `h` on the same maps, so the only thing that differs between
candidates is `h`'s own play. A passenger can't gain relative to its rivals, and no per-body term was
needed (none is applied). Fitness is the mean over all games; the 2v2 mean (`f2v2`) and the 1v1 mean
(`f1v1`) are logged separately per hero and as generation means in `log.jsonl`.

With the defaults K = 5 and D = 2, each genome plays 10 2v2 games and 4 duels, so 29% of its games are 1v1.

## Smoke runs (2 generations, warm-started from the live n2 / c2, --jobs 2)

Tiny settings: NEAT `--pop 4`, CGP `--islands 2 --lam 1`, `--pairings 2 --duels 1` (4 2v2 games and
2 duels per genome, so 33% are 1v1), `--slots 4 --jobs 2`, while the live job ran at `--jobs 4`. That is
264 games per generation for NEAT and 264 for CGP. Columns: mean fitness, mean 2v2 score, mean 1v1 score
(per hero best). Per hero: 2v2 / 1v1 / size.

```
NEAT smoke_n2v2 (warm from n2 gen 39)
gen 1 2v2 122s  fit +8.86  2v2 +12.74  1v1 +1.27  ryth:+15.1/-1.2/2n27c vesper:+9.9/+4.8/5n33c ...
gen 2 2v2 114s  fit +8.71  2v2 +12.45  1v1 +1.39  ryth:+14.1/+1.0/2n27c vesper:+13.1/+4.6/3n31c ...
CGP smoke_c2v2 (warm from c2 gen 34)
gen 1 2v2 120s  fit +7.18  2v2 +11.13  1v1 -0.57  ryth:+11.6/+2.4/3n vesper:+5.2/-1.5/3n ...
gen 2 2v2 114s  fit +6.40  2v2 +9.12   1v1 +1.07  ryth:+12.3/-0.7/3n vesper:+6.9/+0.2/3n ...
```

Two generations with 4 genomes measure only cost, not learning. The 2v2 scores are high because half
the pairings are against the prior anchor. Throughput is about 2.2 games/s at `--jobs 2` on this PC
with the live job running. `champions.py --team-size 2` (22 games, 10 s) and
`league.py --team-size 2` (66 games, 27 s) also ran on the smoke elites. In the league, the NEAT smoke
elites scored +18.8 against the prior and the 1v1 champions playing as a team scored +16.4.

## 3v3 smoke

`duel_maps.py --size 3 --sample 50` wrote 100 maps. `nvn.py play --team-size 3` (live 1v1 champion blob
vs prior) played `open__ryth+marcella+tavi-stellar+willow+ren`: drafts decoded as
[Ryth, Marcella, Tavi] vs [Stellar, Willow, Ren], no bot faults, 800 rounds (resign), kills 26–4, gems 600–0.

## Budget and the proposed 2v2 job

Per genome per generation: 2K + 2D games (K = 5, D = 2 → 14). Per generation: 11 heroes × population × 14.

The measured rate at `--jobs 2` was about 2.2 games/s. At `--jobs 4` with the machine otherwise free
(the 2v2 job replaces fight-cycle-4), expect about 3.5–4 games/s; this is an estimate, not measured.
For 20–25 minutes that allows about 4,500 games:

* NEAT `--pop 24`: 11 × 24 × 14 = 3,696 games, about 16–18 min per generation.
* CGP `--islands 4 --lam 4` (20 candidates per hero): 3,080 games, about 13–15 min.

Proposed job (replaces fight-cycle-4; `--block 3` keeps champion refreshes frequent):

```json
{"name":"fight-2v2-1","priority":50,"cmd":".venv/Scripts/python -u tools/train_cycle.py --team-size 2 --block 3 --neat n2v2 --cgp c2v2 --warm-neat n2 --warm-cgp c2 --pop 24 --islands 4 --lam 4 --pairings 5 --duels 2 --jobs 4 --slots 8"}
```

Check the first generation's `secs` in `.arena/neat/n2v2/log.jsonl` and trim `--pop` or `--pairings` if it
runs over. Note that the NEAT 2v2 run keeps the 1v1 populations' size unless `--pop` is set (`widen` cuts
to `--pop`; the trainer default is 30).

## How to run

```
# maps (gitignored; nvn.py also writes any missing map on demand)
.venv/Scripts/python tools/duel_maps.py --size 2
# tests
.venv/Scripts/python tools/nvn.py equiv --old-src <checkout of the 25-feature bots/neat/src> --warm-arena <live .arena>
.venv/Scripts/python tools/nvn.py fuel --team-size 2
# 2v2 stage (first launch warm-starts from n2 / c2 in the same .arena)
.venv/Scripts/python -u tools/train_cycle.py --team-size 2 --neat n2v2 --cgp c2v2 --warm-neat n2 --warm-cgp c2 \
    --block 3 --pop 12 --islands 3 --lam 3 --pairings 5 --duels 2 --jobs 4 --slots 8
# league over sampled 2v2 games
.venv/Scripts/python tools/league.py neat:n2v2 cgp:c2v2 champ prior --team-size 2 --pairings 12
```

**3v3 and up:** `--team-size 3` (maps are written on demand, or pre-generate a sample with
`duel_maps.py --size 3 --sample 500`), warm-started from the 2v2 runs (`--warm-neat n2v2 --warm-cgp c2v2`;
those are already 31-feature genomes, so widening is a no-op). The 30×30 arena with a 3×3 spawn holds up to
nine bodies, so 5v5 with Cassi & Issac (6 bodies) fits. Larger teams may want more gem clusters or a bigger
map, which would be a new layout in `duel_maps.layout()`.
