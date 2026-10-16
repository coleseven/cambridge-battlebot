# Brief: team-size-generic (NvN) fight trainer, starting with 2v2

For the Claude Code session on the training PC (16 Oct 2026). Read CLAUDE.md, docs/GAME_NOTES.md
and the module docstrings of the files named below before designing anything.

## Goal

Stage two of the fighting curriculum. Today, per-hero networks are trained in 1v1 duels:
- NEAT run `n2`: `tools/neat_train.py`;
- CGPANN run `c2`: `tools/cgp_train.py`;
- `tools/champions.py` combines the per-hero best of the two into the champion set at
  `.arena/champions/current.json`, which both trainers use as opponents;
- `tools/train_cycle.py` alternates the steps, and `tools/league.py` measures progress.

1v1 is near a plateau (league at NEAT gen 36 / CGP gen 34: about +9.5 vs the hand prior, flat since gen
30). Build the **2v2 stage**, warm-started from the 1v1 networks, so that 3v3, 4v4 and 5v5 later need
only a flag and new maps, not a rewrite. Build both NEAT and CGPANN 2v2 trainers so the
champion/league machinery keeps working. Do not build 3v3+ training now; just make it a parameter
that is exercised by a smoke test.

## Hard rules

1. **Do not touch the live checkout `C:\cambridge battlebot`.** The 24/7 job (`fight-cycle-4`, run by
   `tools/jobrunner.py`) relaunches Python from that checkout every step, so editing its files changes
   the running training. Work in a separate worktree:
   `git -C "C:\cambridge battlebot" fetch origin` then
   `git -C "C:\cambridge battlebot" worktree add "C:\cambridge-nvn" -b feat/nvn-trainer origin/main`.
   Use the main checkout's interpreter (`"C:\cambridge battlebot\.venv\Scripts\python"`). `tools/arena.py`
   finds cambc as ROOT/.venv, so either create a junction `C:\cambridge-nvn\.venv` →
   `C:\cambridge battlebot\.venv` (`mklink /J`) or pass the path; don't pip-install a second venv.
   The worktree gets its own `.arena/`, so test runs can't clobber the live runs.
2. **Never run `cambc init`** on this PC (it corrupts the SDK with the CAMBC_DATA workaround). The
   SDK and maps come from git.
3. **Share the machine.** It has 8 GB RAM and a weak i5, and training uses `--jobs 4`. Use at most
   `--jobs 2` for your tests and keep smoke runs small. Don't stop or edit the running job, its
   queue or `.arena/jobs`.
4. **Never retrain from scratch.** 2v2 populations start from the current 1v1 elites (NEAT
   `.arena/neat/n2/elites.json`, CGP `.arena/cgp/c2/state.pkl` / `elites.json`, champion set). Those
   live in the main checkout's `.arena`; read them from there, read-only.
5. Commit to `feat/nvn-trainer` and push the branch; don't merge into main, don't push main, and don't
   switch the live job. Cole's main-PC session reviews and switches. Open a PR if `gh` is
   authenticated; otherwise just push the branch.

## What exists that you build on

- `tools/duel_maps.py` already takes `--size N`. It encodes N picks per team as 4-bit values in wall
  tiles on rows 0/1, and `bots/neat/src/draft.cpp` decodes them. Maps are 30×30 rot180 arenas with
  contested gem clusters, layouts `open` and `pillars`, at `maps_duel/1v1/<layout>__<a>-<b>.map26a`.
  Check whether a 2v2 map needs more room or more gem clusters; it may need a size or layout parameter.
- `bots/neat` is the network bot. Each body scores its turn's legal options (move, attack, spell,
  dash, camp hit, bank) with NF = 25 features per option:
  - a linear prior (`weights.h`), plus a NN over a top-12 shortlist, with an NN fuel cap of 35k per
    turn (C++ gets 100k);
  - the genome blob (magic `NEAT BLOB`, 16384 words, one program per champion_type row; Cassi and
    Issac are rows 3 and 4) is patched into pre-built `bot.wasm` / `bot-injected.wasm` slot copies, so
    there is no recompile per genome (`neat_train.blob/patch/build_slots`).
  - **If you change bot features, the blob format or NF, every slot must be rebuilt**, and old blobs
    must still load (see rule on equivalence below).
- `tools/arena.py` `play/play_many` caches results by (cambc version, wasm hashes, map hash); the
  engine is deterministic. Results carry per-team kills, deaths, gold, dmg, taken and gems.
  `duel_train.side_score` = kill difference + 0.1·dmg difference/1000 + gem difference/50, already per
  team, so it works for any N.
- The draft rule is no duplicate hero within a team; mirrors across teams are allowed. Cassi & Issac
  is one pick with two bodies.

## Design requirements

- **Team size is one parameter, N,** threaded through maps, matchup sampling, evaluation and file
  layout (e.g. `maps_duel/<N>v<N>/`, run dirs that record N, `--team-size`). Put the shared parts in
  one module used by both trainers, e.g. `tools/nvn.py`: matchup sampling, the evaluation schedule,
  fitness credit and map lookup. The NEAT and CGP trainers should differ only in genome code. Adding
  3v3 later should mean generating maps and passing `--team-size 3`.
- **Ally-aware features, warm-start safe.** Add the team features a fighter needs, for example:
  - whether an ally is already hitting this option's target (focus fire);
  - nearest ally distance and health;
  - allies and enemies near the target;
  - whether a stun or other crowd control is landing on the target from an ally.

  Use the 8-byte shared memory only if a feature truly needs it. New feature columns start at
  **zero** in the linear prior and as unconnected inputs (NEAT) or unused inputs (CGP), so a warm-started
  network behaves exactly as in 1v1.
- **Equivalence test, before any training:** with the new bot and a migrated 1v1 champion blob, a
  sample of 1v1 duel maps must give bit-identical arena results (same stats) to the current
  `bots/neat` with the old blob. The engine is deterministic, so any difference is a bug. Also keep the
  fuel cap: measure worst-case fuel per turn for a 2v2 body with the new features (the
  `cambc::fuel_remaining()` before/after pattern) and keep it well under 100k.
- **Matchups.** There are about 4,400 2v2 team pairings per layout, too many to play exhaustively.
  - Each generation, sample a fixed set of team pairings and use it for every candidate (common random
    numbers), so candidates are compared on the same games.
  - Each genome of hero h plays as h in several teams with varied allies, taken from the champion set.
  - Opponents: half the evolving 2v2 champion set (which starts as the 1v1 champions playing as a
    team), half the fixed anchor (the prior).
  - Fitness credit: the team score goes to each member's genome. Consider a small per-body term so a
    passenger isn't credited for a carry's work; document whatever you choose.
- **Retention.** Mix in 1v1 duels (e.g. 25–35% of each genome's games) so 1v1 skill doesn't decay,
  and report 1v1 and 2v2 fitness separately per generation.
- **Budget.** A 1v1 generation takes about 14 minutes now; 2v2 games are slower. Target about 20–25
  minutes per generation at `--jobs 4`. Choose the population, sample count and resign round to fit,
  and say what you chose.
- **Integration.** `champions.py` and `league.py` take a team size (league: per-hero score
  over sampled 2v2 games, both layouts, both seats). `train_cycle.py` can run the 2v2 stage. Keep
  all 1v1 behaviour and defaults unchanged.
- **Checkpoints and resume.** Same atomic save/load pattern as now (`neat_train.save_state/load_state`,
  `pack_pop/unpack_pop`); the PC has crashed before.

## Done means

1. `feat/nvn-trainer` pushed with the code and `docs/NVN_TRAINING.md`, covering the design, features,
   matchup sampling, fitness credit, budget numbers, and how to start 2v2 and later 3v3+.
2. The equivalence test passes; include its output in the doc.
3. A smoke run of 2v2 NEAT and 2v2 CGP: 2 generations each, tiny populations, `--jobs 2`, warm-started
   from the live elites, in the worktree's own `.arena`. Report per-generation 2v2 and 1v1 fitness and
   seconds per generation.
4. A 3v3 smoke: maps generate and one 3v3 game runs with the network bot and the champion blob (no
   training), proving the parameter works end to end.
5. A short report back covering: what changed, test results, the measured cost per 2v2 generation,
   the proposed job JSON to start the 2v2 stage for real, and anything you weren't sure about.
