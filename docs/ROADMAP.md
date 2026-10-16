# Roadmap: compute, curriculum, items and strategy (planned 16 Oct 2026)

Planning record from the 16 Oct session. Nothing below is built yet unless marked **(done)**. Read with
`docs/NVN_TRAINING.md` (2v2 trainer), `docs/ASTRA_COMMS_BRIEF.md` (comms; Astra is working on it),
`docs/WIN_MODEL.md`, `docs/PROXY_CALIBRATION.md` and `docs/GAME_NOTES.md`.

## Where things stand (16 Oct evening)

- **1v1 fighters (done):** NEAT `n2` (gen ~40) and CGPANN `c2`, plateaued at about +9.5 vs the hand prior.
- **2v2 stage running** on the training PC: job `fight-2v2-1`, runs `n2v2` / `c2v2`, warm-started from
  n2/c2, about 20 min per NEAT generation at `--jobs 4`. Team-size-generic code is in main (PR #3);
  3v3+ is a flag plus maps.
- **Varg:** worst 1v1 hero (10% win rate). The decision is deferred until the 2v2 results. Top ladder
  teams give Varg 20–25 Power Strength shards and it banks ~280 gems a game, so it may be an "items
  hero" or a support.
- **Astra:** building the 8-byte team comms protocol and the strategies that use it
  (`docs/ASTRA_COMMS_BRIEF.md`).
- **Tools available:** fight win-probability model, macro proxy (screening only), replay toolkit +
  replay coach (Claude Haiku batches, ~$0.02 per 23 games).

## 1. Compute plan

| Machine | Availability | Power |
|---|---|---|
| Main PC (i9-9900K, 16 threads) | **00:00–12:00 local, every night** | ~4–5× the training PC (~16k full games/h at 12 jobs) |
| Training PC (i5, 8 GB) | 24/7, `--jobs 4` | 1× |
| Laptop (maybe later) | any time | ≤ 1×, worker only |
| Paid compute | only after a **proven** scaling result in 3v3–5v5 | — |

Rules for the night window:
- Night jobs start after 00:00, **checkpoint and exit by 12:00**, and leave the PC idle for daytime
  development.
- Power and sleep settings belong to the user.
- Use the same `tools/jobrunner.py` mechanics as the training PC (status file, logs).
- Each machine writes its own result cache (`results-<hostname>.jsonl`); all caches merge.

### 1a. Islands (stopgap, low effort)
- The night PC runs extra populations (islands) of the same run.
- Elites migrate both ways through Syncthing: at start-up the night islands seed from the training PC's
  current elites; at shutdown they export theirs, and the training PC's trainer imports them as
  migrants.
- CGP already has islands. NEAT needs a "load external elites as migrants" step.
- **Limit:** this doesn't speed up the main run's generations; it only adds search breadth.

### 1b. Distributed game workers (the real fix; build before 3v3+)
- The training PC stays the 24/7 **coordinator**: it runs the trainer and splits each generation's
  games into batches.
- **Workers** on any machine (training PC, night PC, laptop, future cloud) play batches and return
  results.
- **Task:** genome/strategy blob bytes, opponent spec, map name, seat. Bots are pre-built on each
  worker from git, so the payload is small.
- **Transport:** a Syncthing-shared `queue/` → `results/` folder (already works between the PCs).
  Leases with timeouts, so a worker vanishing at noon returns its batches to the queue. Tasks must be
  idempotent (deterministic engine + result cache).
- **Requirements:** identical cambc version on all workers (2.2.2 now; never `cambc init` on the
  training PC); version-check each worker before it takes tasks; coordinator falls back to local play
  when no worker is alive.
- **Success test:** a 2v2 generation that takes ~20 min on the training PC alone takes ≈4–5 min with
  the night PC attached, with identical results for identical games.

### 1c. Scaling test (gate for buying compute)
- Same setup, same wall-clock time: 1× (training PC only) vs ~5× (both machines). Compare league
  score against fixed opponents over time.
- Second test: spend extra compute as (a) more games per candidate (less noisy fitness) vs (b) bigger
  populations. This shows how extra compute should be used.
- Buying compute is justified only if extra compute gives a clearly steeper curve.

## 2. Curriculum from here

1. 2v2 runs until it plateaus (league against fixed opponents + Varg's 1v1/2v2 split in reports).
2. 3v3 via `--team-size 3`.
3. **Zero-shot transfer test before 4v4/5v5 training:** play the 3v3 champions in 4v4/5v5 games and
   compare with a few generations of real training. The team features are size-independent (counts,
   nearest ally), so a short top-up per size may be enough.
4. **3v3+ moves to the real map pool** (17 published + 40 generated held-out maps). Real maps already
   have gold clusters, so **gold and items switch on here**, not in the duel arenas. No gold is added
   to training before then.

## 3. Items (switch on at 3v3+ on real maps)

Rules recap:
- Shards cost 30 gold; 5 matching shards become an emblem (150 gold) with its family passive.
- Consumables cost 10 gold: altar lock, teleport crystal.
- Shopping only at your spawn or while dead. Tavi can buy anywhere, and can sell at spawn for full value.
- Gold earned over the whole game is the round-2000 tiebreaker.

Top teams (scouting, 15 Oct) put 15–25 shards on key bodies. Openings depend on role (Bruiser or
Durability Health on tanks, Power Focus on casters, Jungle on junglers); there's no single universal
build.

Systems to prepare **now, switched off**:
- **Build blob + shopping module in `bots/neat`:** a patchable per-hero ordered list of emblem targets
  (e.g. Jungle-Str, Power-Str ×3, Durability-Health). Partial shards are bought as gold allows; no blob
  means no shopping. Must pass the 1v1 equivalence check (`tools/nvn.py equiv`).
- **Archetype library mined from scouted replays** (`tools/replay` purchase/deposit/fight events):
  per-team build orders per hero, plus behaviour stats (deposit size and frequency, fight rate,
  grouping, opening shape). Used as sparring opponents and as seeds for the build search.
- **Recall-to-shop timing** is a macro decision and belongs to the strategy layer (Astra / forager),
  not the fighters.

Training once gold is on:
- Fighters see itemized fights with builds **sampled** from the archetype library (varied builds, so
  they don't overfit to one).
- Build orders per hero are tuned by CMA-ES in full games, seeded from scouting builds.
- Later, builds adapt to map, enemy composition and game state.

Tricks to test: Tavi mid-fight shard buys (Strength/Focus apply at once; field-bought Health doesn't
heal); Tavi as a gold bank (sell at spawn); death-shopping windows; altar locks and teleport crystals
in the intercept and escape logic.

## 4. Strategy layer: blob, archive, league, learned macro

**Goal:** a very diverse set of strategies on top of the trained fighters, plus external bots, as the
training population. Then a **learned strategy** trained against all of it, robust and able to adapt
in-game.

### 4a. Strategy interface + strategy blob
- One **macro interface** that decides goals: camp target, bank/recall/shop, group/split, intercept,
  rally. The trained fighters execute fights through `combat_hook`; forager is already shaped this way.
- **Parameter strategies:** one general macro bot with many knobs (aggression threshold, bank size,
  grouping, intercept margin, build plan). "Very aggressive" or "money first" is a point in parameter
  space, cheap to generate by the hundred and searchable by CMA-ES.
- **Code strategies:** qualitatively different modules (all-in rush, bush ambush, monolith focus,
  split push, roam-and-steal, capture vulture) written by Astra, Gemini (free tier, sparingly) or
  Claude against the same interface.
- **Strategy blob:** strategy id + parameters patched into a pre-built bot, as with the genome blob.
  No recompile per strategy.
- Astra's comms protocol becomes part of the interface (team state, events, fighter field).

### 4b. Quality-diversity archive (MAP-Elites)
- Describe each strategy by **measured behaviour** from its games (replay toolkit):
  - fights per 100 rounds (aggression);
  - gold vs gems focus (economy);
  - average deposit size (batch vs trickle);
  - grouping (blob vs spread);
  - roaming distance from its own side.
- Keep the **best strategy per behaviour cell**, so diversity is guaranteed, not 40 copies of one idea.

### 4c. Opponent registry / league
- **Pool:**
  - current champions;
  - **past snapshots** (prevents forgetting and cycling);
  - archive strategies;
  - top-team archetypes;
  - external bots (public repos only, credited, sparring only; e.g. Tarot's snapshot, starters, our
    old versions; Python bots are slow, so use them sparingly);
  - later, **exploiters** trained only to beat the current best.
- **Sampling** weighted toward opponents we lose to (prioritised fictitious self-play).
- **Held-out set:** a few external bots and archetypes are never trained against; they are the honest
  robustness test.

### 4d. Learned macro policy
- **Macro network** scoring macro options, the same way the fighters score move options:
  - options: go to group G, bank at altar A, recall + shop, join ally, intercept, rally, hold;
  - inputs: game state (score race, bags, health, respawn timers, comms team state) plus
    **opponent-summary inputs** (enemy aggression, banking style, build, grouping, accumulated
    in-game). These give in-game adaptation, since the networks have no memory.
- Trained with NEAT / CGP / CMA against the league.
- **Fitness = mean + weighted worst case** (`arena.fitness` already has `worst_weight`). "Flexible"
  means no opponent in the pool beats it badly.
- **Alternate training:** macro with fighters frozen, then fighters with macro frozen. Never co-train
  both at once.
- Adaptation inputs only get used if the pool contains distinct styles where adapting pays off, so the
  diverse pool is a prerequisite.
- **Test:** per-archetype results, within-game improvement against each archetype, and whether
  counter-builds differ by opponent.

### 4e. Compute reality
Evaluation, not idea generation, is the bottleneck:
- sample opponents per generation instead of playing the whole archive;
- screen with the macro proxy only after it is recalibrated;
- make parameter strategies the bulk of the archive;
- depends on the distributed workers in 1b.

## 5. Development order (proposed for the first night sessions)

1. **Distributed game workers** (1b), proven on the live 2v2 setup, plus the night-window job wrapper
   (start after 00:00, checkpoint and stop by 12:00).
2. **Islands / migration** (1a) as the night fallback when workers aren't ready.
3. **Strategy interface + strategy blob design doc** (4a), aligned with Astra's comms work.
4. **Build blob + shopping module** in `bots/neat`, off by default, passing the equivalence check (3).
5. **Archetype mining** from scouted replays (3, 4c).
6. **Scaling test** (1c) once workers run.
7. Later, with 3v3+ on real maps: gold on, league + QD archive, learned macro policy.

## Open decisions

- When to move from 2v2 to 3v3 (needs a plateau check).
- Varg: revisit after 2v2 results.
- The priority-bits side channel needs a rules check with the organisers before any submitted bot
  relies on it (see the comms brief).
- External bots: which public repos to include, and license/credit for each.
