# Cambridge Battlecode 2026a: team 777

> **Result: the neural-network approach failed.**
>
> Two weeks and about 2.7 million training games on two home PCs went into evolving neural networks to fight and to
> run strategy. The networks did not learn to play this game well enough to matter.
> - Our ladder rating fell from 1,500 to **1,284**, rank **620 of 1,092**. That is below the median (1,368) and far
>   from the top-10% cutoff (about 1,830).
> - In the bot we ship, a network controls 1 of our 5 heroes. Everything else is hand-written rules.
> - **With this compute, neuroevolution was the wrong tool for this game.** Overnight training stopped on 27 Oct 2026.

This repository is our bot, tools and training code for [Cambridge Battlecode 2026a](https://2026a.battlecode.co.uk/game/home),
a 5v5 hero game with a strict per-turn compute budget. It is kept as a record of what we tried and why it didn't work.

## What we tried

The plan: learn each hero's fighting with small evolved networks, then learn the strategy layer on top of them.
We wanted as little hand-tuning as possible.

| Stage | Method | Training | Outcome |
|---|---|---|---|
| 1v1 fighters | NEAT and CGPANN per-hero networks, in generated duel arenas | ~40 generations | Beat our hand-written fight prior in duel arenas, then plateaued. Did not carry over to real games. |
| 2v2 / 3v3 fighters | Same networks plus team features | 104 + 60 generations | No measurable gain on real maps |
| Real-map fighters (real1–real3) | NEAT inside the full bot, on the published maps | 684 generations, ~700k games | Flat. Only Varg's network beat the scripted fight. |
| Strategy parameters | CMA-ES shared with the fighters' games | 194 generations | Improved neither layer |
| Learned strategy ("director") | NEAT re-ranking the rule layer's orders | 4 runs, 1,965 generations | One small gain (d1, generation 100), never improved on |

Supporting work: distributed play over two PCs via Syncthing, fuel budgeting for the networks, and a map
generator for held-out testing. Details are in `docs/` (`NVN_TRAINING.md`, `DIRECTOR.md`, `ROADMAP.md`,
`DIST_WORKERS.md`).

## The evidence

**Ladder rating over the season** (public ladder, 27 Oct 2026). It started at the default 1,500 and drifted down
through every upload, including each version with trained networks:

    1500 1505 1483 1454 1441 1489 1462 1480 1473 1447 1434 1469 1386 1442 1388 1403
    1411 1354 1361 1374 1310 1344 1324 1329 1318 1306 1327 1346 1352 1315 1284

**The fighter training curves are flat.** The score is per game: ±1 for a win or loss, plus the gem margin / 500.
Each figure is the mean over a block of generations.

| Run | Generations | Games | vs its own starting networks: first block → last block |
|---|---:|---:|---|
| real2 | 194 | 205k | −1.04 → −0.97 |
| real3 (after the patch) | 461 | 487k | −0.31 → −0.30 |

Hundreds of generations moved the population nowhere against its own starting point. Mutation and selection were
following noise.

**Networks per hero, in the real bot** (19 Oct, 57 maps; fitness is the evaluation suite's score vs Tarot, v1
and the starter):

| Fight control | Fitness |
|---|---:|
| Scripted fight for every hero | +0.356 |
| Networks for every hero | +0.406 |
| Varg's network only, the rest scripted | +0.410 |
| C&I's network only | +0.295 |

Of the other heroes' networks, Stellar's and Tavi's were about even with the hand-written fight, and Ren's and
C&I's were worse. The shipped bot uses one network (`NN_HEROES = 1024`, Varg only).

**The learned strategy never got past its first lucky snapshot.**
- One snapshot (d1 generation 100) beat the rule layer: +0.472 vs +0.385 fitness, 27/42 head to head. It became
  ladder version 4.
- Three further runs (d2, c1, c2: about 1,400 generations between them, including stricter, champion-gated
  selection) never beat it head to head. The best try scored 65/122 games with a −7 gem margin.

**Final local comparison** (27 Oct, 21 maps):

| Bot | Fitness | Head to head vs version 4 |
|---|---:|---:|
| version 4 (live) | +0.533 | — |
| version 4 + latest Varg network | +0.489 | 20/42 |
| ladder-meta draft + latest networks | +0.363 | 16/42 |
| ladder-meta draft, scripted fight | +0.192 | 16/42 |

## Why it failed

1. **Too few games per candidate for the noise.** One game swings far more than a typical mutation changes
   strength. Reliable rankings needed hundreds of games per candidate per generation, so our ~16k games/hour bought
   only a few dozen trustworthy comparisons per night. Every selection scheme we tried either followed noise
   (NEAT's winner's curse in d2) or almost never accepted a change (c2: 4 promotions in 367 generations).
2. **The duel arenas didn't transfer.** Fighters that beat the prior in 1v1–3v3 arenas were mostly no better in
   full 5v5 games on real maps. In those games the fight is a small part of what decides the result.
3. **The compute budget caps the networks.** Each bot gets 100k fuel per turn. Listing and scoring options takes
   most of it, so the networks had to stay tiny.
4. **The game moved under us.** The 20 Oct balance patch nerfed all five of our heroes and cut camp rewards. Most
   pre-patch training was stale overnight.
5. **Local evaluation didn't predict the ladder.** Our sparring pool was old bots, while the ladder plays the
   current meta. Local gains of a few hundredths didn't show up in the rating.
6. **Infrastructure ate nights.** Several nights were lost to a hung distributed claim and to Syncthing being
   closed by Windows while it re-hashed caches. With the second PC, training ran only 1.65–1.9× as fast, not the 4–5× planned.

## What we'd do instead

With two consumer PCs and four weeks, the time is better spent on:
- **hand-written rules for the current meta**, built from studying top teams' replays (`tools/replay`, `tools/scout.py`);
- **CMA-ES on a few dozen of those rules' numbers**, a search that does work at this game budget (`tools/tune.py`);
- **evaluation against opponents that look like the ladder**, plus regular ladder checks after uploads.

Learning approaches need at least an order of magnitude more games, or a much stronger learning signal than win
or loss over a full game, before they are worth it here.

## Layout

| Path | What |
|---|---|
| `bots/director` | Shipped strategy layer (rules plus the version 4 learned re-ranker) |
| `bots/neat` | Network fighter, patchable genome blob |
| `bots/forager` | Teammate's farming module |
| `bots/main`, `bots/v1` | Early rule bot and its frozen tuned version |
| `tools/arena.py`, `eval.py`, `tune.py` | Cached parallel match engine, evaluation suite, CMA-ES tuner |
| `tools/neat_train.py`, `cgp_train.py`, `real_train.py`, `director_train.py`, `director_climb.py` | The trainers |
| `tools/dist.py`, `dist_worker.py` | Distributed play over a shared folder |
| `tools/mapgen.py`, `maps_gen/` | Map generator and 40 held-out maps |
| `docs/` | Rules notes, design docs and the full training history |

Commands are in `CLAUDE.md`. The engine CLI (`cambc`) lives in `.venv`.
