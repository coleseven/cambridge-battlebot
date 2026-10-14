# CAMBC 2026a — research: MOBA strategy, competitor intel, technical approaches

Compiled 14 Oct 2026 (day 3 of the season, Sprint 1 locks Sat 17 Oct 24:00 UK).
Companion files: `docs/GAME_NOTES.md` (rules digest), `docs/GameRules.md` + `docs/BotAPICpp.md` (official exports),
`tools/scout.py` (ladder scraper), `tools/eval.py` (local suite).

---

## 1. LoL / Dota concepts that transfer

CAMBC is basically "the jungle phase of a MOBA, with no lanes": the whole economy is neutral camps, the score is
banked gems, and fights only matter because they steal bags or deny farm. The MOBA ideas worth stealing:

| MOBA concept | What it means there | CAMBC equivalent |
|---|---|---|
| **Clear speed / jungle pathing** | Route camps so you're always hitting the next one as it spawns; AoE vs single-target clearers path differently. Faster clears = more time for objectives. | Camp routing is the core problem. Camps have fixed periods (wolf 40, elk 60, clusters 50, monolith 150). Varg (splash) and Stellar/Prism (radius-1 spells) are the AoE clearers; packed, cardinally-connected groups favour them. |
| **Leashing / "inching" a camp** | Tank the camp from the right spot so it does minimal damage to you. | Camps attack the **nearest** champion and never chase. A melee body in front absorbs all aggro while ranged carries free-hit from range 3–4. |
| **Stacking / camp blocking (Dota)** | Manipulate spawn timers; a ward in the box blocks the spawn. | An **occupied spawn tile delays the whole group's respawn**. Step off your own captured camp before its timer ends; park on an enemy's camp to starve it. |
| **Counter-jungling / invading** | Steal the enemy's camps when you know where their jungler is; leave immediately. | Capturing is contestable: an enemy on the region resets the 5-round timer. Taking camps near the enemy spawn or contesting their captures is direct denial. |
| **Tracking the enemy jungler** | Know their path from camp timers and sightings. | Camp state is partly observable; wards (radius 6, permanent, free) on high-value camps reveal respawns and enemy pathing. Vision isn't shared, so findings have to go through the 8-byte blackboard. |
| **Back timing / gold breakpoints** | Recall when you can afford the next power spike, never mid-objective; "cheater recall" after shoving a wave. | Recall is 5 turns, cancelled by damage, and shopping only happens on spawn. Back when gold buys a full emblem-worth of shards (150g), or when the bag is big enough that dying would be a disaster. Dead champions can shop, so a death is also a free back. |
| **Shutdown gold / bounties** | Killing a fed carrier pays out big. | **The killer takes the whole carried bag + 50g.** A champion carrying 150 gems is a piñata. Bank often (≤10 gems = a 1-tick tribute) unless the route is safe. |
| **Objectives (Baron / Roshan)** | High-risk, high-reward neutrals that swing games; contest them with vision and numbers. | **Monolith**: 20k HP, 200 strength, 200 gems + 300 gold, 150-round respawn. That's a team objective; only take it with enough DPS, ideally when the enemy is visibly elsewhere. |
| **Teamfights: focus fire + CC chaining** | Kill the carry first; chain stuns rather than overlapping them; hold burst until enemies commit. | Stuns are 1 round. Several stunners (C&I, Prism, Tavi, Ren, Ryth, Marcella's enhanced attack) can chain-lock a target. Burst maths (2×–2.2× Focus spells plus attack resets) decides whether a kill is on. Stun or kill a champion mid-tribute; damage alone doesn't stop the channel. |
| **Macro tempo / timing windows** | "What's the single most valuable thing I can do right now?" Convert advantages into the next objective. | The game is a race to 1,000 banked gems; the median top-team win ends around rounds 360–650. Every idle turn or wasted recall is lost tempo. |

Good primers (not deep analysis, just the vocabulary):
- Jungle pathing, clear speed and timers: [Dodge.gg jungle guide 2026](https://www.dodge.gg/en-US/lol/news/jungle-guide-2026), [Sportskeeda pathing guide](https://www.sportskeeda.com/esports/guide-jungle-pathing-league-legends-season-13), [Mobalytics low-elo jungler series](https://mobalytics.gg/blog/low-elo-to-diamond-jungler-part-2/)
- Dota farming patterns, stacking and blocking: [Dota 2 wiki – Farming](https://dota2.fandom.com/wiki/Farming), [ProSettings farming guide](https://prosettings.net/blog/dota-2-farming/), [bo3.gg stacking/pulling guide](https://bo3.gg/da/dota2/articles/mastering-dota-2-stacking-and-pulling-techniques-guide)
- Macro, tempo and vision: [Mobatrainer macro](https://mobatrainer.com/improve/macro-game), [Dignitas: lane priority → tempo & vision](https://dignitas.gg/articles/blogs/League-of-Legends/10280/transitioning-lane-priority-into-tempo-and-vision-control), [Red Bull: timing windows](https://www.redbull.com/us-en/the-importance-of-the-macro-game-timing-windows)
- Back timing: [Metabot: when to recall](https://metabot.gg/en/league/guides/when-to-recall-back-timing-economy), [Mobalytics recall timers](https://mobalytics.gg/blog/lol-5-best-recall-timers/)
- Counter-jungling: [Mobalytics: stop dying to invades](https://mobalytics.gg/?p=40358)
- Teamfighting, focus and CC chaining: [Mobatrainer teamfighting](https://www.mobatrainer.com/guides/teamfighting), [1v9 support teamfight guide](https://1v9.gg/blog/leagueof-legends-lol-how-to-teamfight-support-guide)
- MOBA AI research (macro/micro split): [Hierarchical Macro Strategy for MOBA AI (Tencent, AAAI 2019)](https://arxiv.org/pdf/1812.07887). Its useful idea: predict *where to go* (macro attention map) separately from *what to do there* (micro). [JueWu-SL](https://arxiv.org/pdf/2011.12582) is supervised learning from human replays.

---

## 2. Competitor intel

### 2a. Public repos for this game (2026a, created after the 12 Oct launch)

| Repo | What's there | Value |
|---|---|---|
| [FHL-08/Tarot-Club-Cambc2026](https://github.com/FHL-08/Tarot-Club-Cambc2026) | **The big one.** A C++ bot "foundry" (~200 KB source: atlas/planner/exec/ledger/econ/tactics), `docs/strategy.md`, a replay decoder, a platform scraper, and an AI-agent "factory" that turns issues into PRs gated by real matches. 30+ PRs merged on 13–14 Oct. | Strategy thesis, mechanics list, a sparring opponent we can build locally, and replay tooling. |
| [turtletiny/cambridge-battlecode](https://github.com/turtletiny/cambridge-battlecode) | Rust bot, mostly committed build artefacts. | Low. |
| [rafancoding/cambcode](https://github.com/rafancoding/cambcode) | Lightly modified starter. | Low. |

Tarot's thesis (`docs/strategy.md`), summarised:
- *"The game is a compile-time scheduling problem."* At draft you see every camp (`map::camp_groups()`: positions, gem/gold value, spawn tiles, respawn period) and get 10× fuel. So: build a cyclic harvest plan, pack it into the 1,024-byte draft memory, and have each champion run its route as a small state machine. Runtime fuel is spent on following the plan plus "tactics" (deny, gank, lock, peel, burst) that interrupt it.
- The mechanics they list are worth checking ourselves. The most interesting:
  - **Capture-overlap tribute.** A tribute's duration is set by the bag at the start, but it pays out the whole bag at the end, and captures resolve before channels. So start a 1-tick tribute on an altar-adjacent camp the round its capture lands.
  - **Captures are passive.** You can attack the next group while standing on the capture tile.
  - Camps tank the nearest champion; occupied tiles delay respawn; bags transfer on kill.
  - Shared memory as deterministic consensus: every champion runs the same pure function over the same inputs, so they agree without a protocol.
- Their draft order: Vendetta → C&I → Varg → Tavi → Vesper. Their own later PR moved C&I first and Stellar third, and dropped Vendetta from the early picks, which matches the ladder data below.
- One claim is wrong, and I measured it: they say bitboard/distance-field ops sit "outside the fuel meter". **They are metered, but cheaply** (see §3).

### 2b. Earlier Cambridge Battlecode (spring 2026, a *different* game: builders/conveyors/turrets)
Different rules, same organisers, same kind of WASM/fuel engine. The process lessons transfer:
- **Winner [HerbertTheBird/Cambridge-Battlecode](https://github.com/HerbertTheBird/Cambridge-Battlecode)** (Pantheon, 1st at the Grand Finals; [postmortem PDF](https://github.com/HerbertTheBird/Cambridge-Battlecode/tree/main/postmortem)):
  - The bot was deliberately **memoryless**: each turn it scored states by priority, with no long-lived scripts. They credit this with adaptability and resistance to overfitting.
  - Everything ran on bitmasks: incremental updates (only re-scan tiles that became visible), memoised derived masks, TTL caches for targets that failed recently, and a debug-log flag compiled out before submitting.
  - Their sprint finishes were Top 128 → 8 → 4 → 8 → 4, then 3rd at the international qualifiers and 1st at the finals. Sprint 1 is not decisive; steady improvement is.
- [lxorb/muteki](https://github.com/lxorb/muteki) (5th/6th at finals), [CoffeeCoder1015](https://github.com/CoffeeCoder1015/cambridge-battlecode) (bucketed A*, frontier exploration, adaptive spending): mostly game-specific.

### 2c. Ladder data (public API, scraped 14 Oct ~22:45 UTC)
The platform's public API (`/api/v1/ladder`, `/api/v1/match-groups?scope=all&team=<id>`, `/api/v1/match-groups/<id>`) returns official per-match statistics: drafts, items bought, damage, gems, kills and deaths per champion. Replays come from `/api/v1/matches/<id>/bundle`. Run `tools/scout.py` to refresh.

345 teams are on the ladder. Top 5: hello 1823, Univardisty of Shingtonwash 1821, Mite 1819, VeryGood 1767, procless 1764.
The sample is 6,440 top-15 team-games from 919 match groups.

**Draft meta among the top 15** (picks / first picks / win rate when picked):

| Champion | Picks | First | Win% | Notes |
|---|---|---|---|---|
| C&I | 5355 | 1594 | 62% | Near-universal: two bodies for one pick |
| Varg | 4373 | **2767** | 62% | Most common first pick; **top gem earner** (~269 tributed per game) |
| Stellar | 3946 | 888 | 64% | Most enemy-champion damage; AoE clear |
| Ren | 3506 | 596 | 66% | Second-best tributer (~242); dash + stun |
| Ryth | 2779 | 259 | 63% | |
| Vendetta | 2326 | 290 | 59% | |
| Tavi | 2257 | 2 | 63% | Late pick |
| Prism / Willow / Marcella | 858 / 629 / 421 | ~0 | 60–66% | Niche |
| Vesper Vale | 16 | 6 | 44% | **Unpicked.** +20% gold doesn't matter in a gem race |

Most common full drafts: Mite and procless use **Varg / Stellar / C&I / Tavi / Ren**; hello uses C&I / Stellar / Ren (3-pick maps); #2 uses Ryth / Stellar / Prism / Ren / Tavi.

**Builds:** Power shards dominate for every champion. The Power emblem is worth 6 shards with no passive, so raw stats beat the passives.
- Varg: ~16 power shards of Strength per game, plus some Jungle and Durability.
- Stellar, Cassi and Prism: power shards of Focus.
- Issac and Vendetta: power shards of Strength.
- Ren: a mix of Focus and Strength, plus Bruiser Health.
- Monster, Treasure and Home emblems are rare.
- One consumable index is bought ~14k times (which one is unconfirmed; probably the altar lock). The other is almost never bought.

**Pace:** top-team wins are usually *races*, finishing at a median of round **364 (expanse) to 655 (collision)**. Deaths are low (~0.5–1 per champion per game) and so are kills (~1 per champion). The best teams farm efficiently and fight only when it pays. Kill-heavy teams (PTN Hyperion: K 6.7, D 6.2) rank below the efficient farmers.

### 2d. Replays
Replays come as zip bundles with `replay.repl26a` and both teams' stdout. Tarot's [`tools/replay/decode.mjs`](https://github.com/FHL-08/Tarot-Club-Cambc2026/blob/main/tools/replay/decode.mjs) replays a bundle headlessly in Node: it loads the visualiser's own engine WASM and reads its state snapshots per round into objects (positions, health, gems). That's the route to "what route did Mite take on elkring?" at scale.

I couldn't watch replays visually this session: the visualiser doesn't draw while the app window is hidden. Worth watching: Mite vs #2 on elkring, 1008–504 at round 556 (`/game/visualiser/aaaa5d08-6d6e-44af-9b95-0c2305f52f6a`).

---

## 3. Technical approaches

### Facts that constrain the design (measured locally, 14 Oct)
- **Fuel:** 100k per turn for C++, ~178k on a champion's first turn, 1M on the first draft turn.
  - A whole-map `distance_field::compute_to` costs **~5–6k fuel**, three bitboard unions ~370, and a trivial 1,000-iteration C++ loop ~9k.
  - So SDK bitboard/field ops are about **100× cheaper per tile than hand-written loops**. Do spatial work with SDK ops and keep scalar logic small.
  - That's ~15 full fields per turn, or ~30 on turn 1 (cacheable: camps and altars don't move).
- **Speed:** matches are deterministic and fast. A full game takes ~0.5–1 s, and the 9-map, both-seat suite runs in ~9 s with 6 jobs. On 16 cores that's roughly **7–10k games/hour**, more than enough for CMA-ES.
- **Information:** the draft sees the full map and can hand a plan to the champions through draft memory (1,024 B). After that there are only 8 shared bytes, and vision isn't shared.

### A. Architecture: plan at draft, run a utility policy at runtime (recommended)
1. **Draft-time atlas.** Enumerate camp groups, altars and spawns. Build a distance matrix between points of interest from cached fields (spawn ↔ camp ↔ altar). Compute each group's **value rate** ≈ (gems + λ·gold) / (travel + clear time + 5-round capture), where clear time comes from a DPS model per champion kit.
2. **Partition and route.** Split camps between champions (Voronoi on travel time from spawn, with balanced load), then build each champion's cyclic route. Respawn periods turn this into an orienteering / vehicle-routing problem with time windows; with ≤~30 camp groups, greedy insertion plus 2-opt is plenty.
   - Pack the routes into draft memory: one byte per camp index, plus thresholds.
3. **Runtime: a priority/utility pass each turn, Pantheon-style, mostly memoryless:**
   - safety (retreat or recall),
   - interrupts (an enemy channelling a tribute or carrying a big bag in reach, a contested capture),
   - bank (bag ≥ threshold, or near an altar),
   - follow the route,
   - shop on spawn.
   - Each check is a cheap scoring function with tunable weights. This gives the robustness of a memoryless design and the throughput of a precomputed plan.
4. **Coordination:** deterministic assignment that every ally computes the same way, e.g. "the nearest live ally with a free slot takes a dead ally's camps". Use the 8 shared bytes for claim bits, the last enemy sighting and an alarm.

### B. Parameter tuning with CMA-ES (reuse the UNSW pipeline)
- Expose every magic number as a parameter: bank thresholds, retreat HP fractions, λ (gold vs gems), aggression thresholds (bag size worth hunting, burst margin), build order, monolith gate, ward placement weights. Compile them in via `-D` defines or a generated header, like `CPP_*` at UNSW.
- **Fitness:** win rate first, then mean gem margin as a tiebreak. Score across all 9 maps × both seats × an **opponent pool**:
  - the starters,
  - our frozen versions,
  - **Tarot's foundry, built locally from their public repo** (a realistic, evolving opponent),
  - variants built to copy the top ladder drafts.
- **Overfitting risk:** only 9 maps. Use `cambc mapeditor` or a script to make **procedurally generated maps** for a held-out set, and check that tuned parameters generalise before keeping them. The platform will add maps over the season.
- Population ~16, 18–54 games per candidate, which is a few minutes per generation. An overnight run is hundreds of generations.

### C. The draft as its own optimisation problem
- Picks are sequential, simultaneous and revealed as they go; the pick count (3–5) depends on the map.
- **Comp payoff matrix:** simulate candidate comps against each other per map (our runtime with different drafts forced through a draft-override parameter). Pick by **maximin**, i.e. the comp whose worst matchup is best, then react to revealed enemy picks with a lookup.
- **Map-feature model:** features visible at draft (camp counts by type, cardinal-pack sizes favouring splash, spawn→camp distances, altar count, monolith present) → best comp. A small decision table learned from simulations on generated maps.
- **Prior from the ladder:** C&I, Varg, Stellar and Ren are near-consensus at the top, and Vesper is dead. Starting point: Varg / Stellar / C&I / Ren (+ Tavi or Ryth on 5-pick maps).

### D. Combat micro: small exact calculations, not search
- **Burst calculator:** given visible enemy HP, our Strength/Focus, available resets and stuns, and range, can we kill within N turns before they escape or recall? Only commit when yes, or when the target carries a large bag.
- **Stun chaining:** one-round stuns from multiple champions, staggered so they don't overlap. Prioritise stuns on enemies channelling a tribute or recall.
- **Threat map:** a bitboard of tiles within enemy attack/spell range (Chebyshev expand of visible enemies, cheap with SDK ops). Route around it while carrying gems.
- Retreat and recall rules: recall only out of enemy reach (it's cancelled by damage), otherwise walk home.

### E. Information and denial tactics (tune and keep only if they win games)
- Wards on enemy-side high-value camps track respawns and the enemy route.
- Altar lock (10g) on the enemy's nearest altar when they're carrying big bags; it cancels channels in progress.
- Respawn denial: stand on an enemy camp's spawn tile after it's captured.
- Contest captures (resetting the enemy's 5-round timer) instead of fighting outright.
- Capture-overlap tribute timing (§2a). Verify locally first.

### F. Learning from top teams (later, if time allows)
- Decode top-team replays with the engine WASM (Tarot's approach). Extract per-champion routes, bank sizes and recall timings, and copy the patterns that work. This is behaviour cloning by hand, essentially the HMS "where to go" idea from §1.
- A learned policy *inside* the bot is not realistic: 100k fuel is ~10k multiply-adds. Learned things have to be compiled into tables or parameters.

### G. Infrastructure to build first
1. `tools/eval.py`: done. Extend it with an opponent pool, held-out maps and a `--param` override.
2. Freeze versions (`bots/v1`, `bots/v2`, …) as sparring partners; clone and build Tarot's foundry as an external opponent.
3. A CMA-ES driver: port `bcsim/tune.py` from the UNSW project, aimed at `tools/eval.py`.
4. Replay/statistics analysis: per-champion idle turns, deaths while carrying, gems lost, time to 1,000.
5. A fuel guard: check `fuel_remaining()` and degrade gracefully; log any turn that gets suspended.

### Suggested order up to the Sprint 1 lock (Sat 17 Oct)
1. **v1:** ladder-meta draft + a proper farm/route/bank loop + recall/shop discipline + power-shard builds. Target: beat the starter on 18/18 and hold up against Tarot's foundry.
2. Add the threat map and burst calculator (punish fat carriers, avoid becoming one).
3. Upload v1 for the sprint (with your go-ahead), then start CMA-ES and draft simulations for Sprint 2.
