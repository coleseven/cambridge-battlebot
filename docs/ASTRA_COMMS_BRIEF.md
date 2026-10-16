# Brief for Astra: team communication protocol + strategies built on it

Self-contained task brief (16 Oct 2026). Read CLAUDE.md, docs/GAME_NOTES.md, docs/GameRules.md,
docs/FARMING_MODULE.md, docs/STRATEGY_PROXY.md, docs/PROXY_CALIBRATION.md and docs/WIN_MODEL.md first.

## Goal

Get the most useful team information through the game's communication channels, then rework the
higher-level strategies in `bots/forager` around it. There are two halves:
- **transport:** how we move bits;
- **content:** what is worth sending and what decisions use it.

## The channels (from the rules)

- **Shared memory: 8 bytes (64 bits).** Read and write by every champion on our team, all match. The
  opponent can't read it (`cambc::shared_memory`). No write-rate limit is stated; each read or write
  goes straight to the host.
- **Draft memory: 1,024 bytes.** Written once by the draft step, which sees the whole map and gets 10×
  fuel. Read-only to champions (`cambc::draft_memory`).
- **Turn priority (`set_priority`):** orders our champions within the team (higher first, effective
  next round). It carries no information by design.
- **Vision is per champion** (radius 8, plus your own ward at radius 6), not shared.
- Up to **6 bodies per team** (5 picks; Cassi & Issac is one pick with two bodies). Design for 2–6
  bodies.
- **Dead champions still get a turn** each round, so they can still read and write shared memory.

## Part 1: measure the channel (do this first, with a small test bot)

Establish empirically and document:
- whether shared memory is zeroed at match start;
- whether a write is visible to a teammate acting later in the **same round**, for each turn order
  (teams alternate and the starting team swaps every round);
- whether dead champions can read and write;
- whether each bot instance's private memory persists across turns;
- the fuel cost per read and write call;
- **what is already free without comms**: round number and both teams' banked totals
  (`global_submitted_gems`) are global. Can an ally's position or alive status be read when it is
  out of vision? The answer changes the budget a lot.
- **Possible side channel (needs a rules check before use):** can `champion::priority()` /
  `prev_priority()` of an ally be read when it is out of vision, and can an enemy read ours when it
  sees us? If allies can read it and enemies can't, evaluate putting the order rank in the high bits
  and a payload in the low bits. Flag it; no submitted bot may rely on it until the organisers confirm
  it is allowed.

## Part 2: transport design

- **A message bus, not 64 bits of storage.** Each body keeps a full private copy of team state and
  uses the 8 bytes as a delta stream into everyone's copy.
- **A blackboard with priority eviction.** On its turn, each body reads the board into its private
  state, adds its new observations, and rewrites the board with the messages the team still needs
  most (urgency × age). Turns are sequential, so read-modify-write within a turn has no races.
- **Round-number time slots** give free timestamps and allow multiplexing, e.g. a field whose meaning
  rotates with `round mod k`.
- **Same-round relays:** give the body that most often has news (scout or carrier) a higher priority,
  so its writes reach the fighters before they act.
- **Codebook in draft memory:** message-type meanings, region/camp-group coordinates, precomputed
  routes and plans, so a few live bits can point at rich static content.
- **Send observations, not conclusions.** Every body runs the same deterministic estimators on the
  same events, so beliefs stay consistent for free.
- **Typed C++ API** in `bots/forager/src/team_comms.h`, includable by other bots unchanged
  (`write_intent`, `post_event`, `read_events`, ...). Unit tests for encode/decode at body counts 1–6,
  stale data and eviction.

## Part 3: content (candidates, roughly by value per bit; measure before committing bits)

1. **Enemy sightings with age (the backbone).** Enemy id, region or camp-group id, HP bucket, bag
   bucket. Builds one team-wide belief of where every enemy is and how fresh that is. Escort risk,
   counter-attack risk and intercepts all depend on it.
2. **High-loot disengage / retreat.** Enemy id, **bag carried when it disengaged** (deposit timing
   isn't needed), last region, likely altar. About 16 bits.
3. **Enemy deaths.** Enemy id; the round is implied. Gives respawn timing (60 ticks) and a "they're
   shopping now" signal for the gold ledger.
4. **Capture status.**
   - "I'm capturing group G": teammates stay **off** the region (extra allies only split the reward,
     they don't speed it up) and guard it from adjacent tiles.
   - "Enemy capturing group G": a steal/deny opportunity. Stepping onto the region resets their
     5-round timer, and whoever holds it uncontested at the end gets the full reward.
5. **Camp respawn timers.** Any sighting of a captured or empty group gives its respawn round
   (capture round + longest member cooldown). Share them to build a map-wide respawn table and route
   to camps that are about to spawn.
6. **Kill-blow assignment.** The champion that lands the killing blow gets **all** the victim's carried
   gems + 50 gold; assists get nothing. In a team fight, the kill should go to the body best placed to
   bank the stolen bag: nearest our altar, healthiest, smallest current bag. Reserve a "I'm taking
   this kill / you take it" bit pattern.
7. **Fighter field (reserved for the trained networks).** Each fighting body publishes its current
   fight target (enemy id) + 2 bits of intent (engaging / retreating / carrying / in danger). State
   exactly which bits; Claude's training pipeline will feed them to the networks as inputs.
8. **Our own availability.** Dead-until round, or recalling, so the team knows when we're down a
   body. Only needed if ally state isn't readable for free (Part 1).
9. **Our own power spikes.** "Completing an emblem at my next recall" (1–2 bits per body), so the team
   times fights after our spikes and before theirs.
10. **Enemy item snapshots when they change.** A visible enemy's `item_count()` and stats are
    readable. Rare, so cheap.
11. **Rally / retreat call:** "converge on X by round R". Rare, a few bits.
12. **Altar lock alerts** (rare, tiny) and **monolith state** on maps that have one (20k HP, 200
    gems, 150-round respawn).

**No hero-specific team information.** Hero identity is known from the first sighting of an enemy id,
and the per-hero networks learn matchups and combos themselves.

**Before committing bits, measure value from replays.** The replay toolkit (`tools/replay`) can count
how often teammate B could have used something only A saw: an enemy tribute seen by one body with
another in range to stop it, captures stealable within reach, respawns walked into blind, and so on.

## Part 4: estimators every body runs on the shared events

- **Enemy gold ledger:** per-hero expected gold-by-round curve (fit from ladder replays), +50 per kill
  the enemy gets on us, +20% for Vesper / Treasure, minus exact spending read from `item_count()` at
  each sighting (shards 30g, emblems 150g). Predicts power spikes; shopping happens only at spawn or
  while dead (Tavi: anywhere).
- **Enemy position belief:** last-seen region and age per enemy; enemies not accounted for count as
  partial risk everywhere they could have reached.
- Keep both as small modules separate from forager. The fighters' win-probability model will use them
  too (estimated enemy stats for enemies not currently visible).

## Part 5: decisions built on it (each behind its own params.h switch, default off)

- **Intercept / chase rule (expected value, not reflex).** Options: do nothing, best single body, or a
  pair. Score each in gems-per-round of the bodies involved:
  - **Gains:** P(kill within the channel) × P(getting the stolen bag home) × (bag + 50 gold), plus
    P(stop only) × denial value.
  - **Costs:** P(we lose) × (our bags + 50 gold each + respawn time), rounds away × our current
    gems/round, the exposure cost of leaving a teammate alone at a contested camp, and escort risk
    from the position belief.
  - **Rules that matter:** damage does NOT stop a tribute; only a stun, death or an altar lock does.
    So an intercept needs a stun ready on arrival, or enough damage to kill within ceil(gems/10)
    turns. A stolen bag is worth nothing until banked.
  - **Overrides:** if their banked + bag ≥ 1000, stop it almost at any cost (an instant win for them).
    If ours ≥ 1000, bank, don't chase.
  - Merely slowing a carrier is worth it only near their 1,000.
  - Use `bots/forager/src/win_model.h` for the probabilities.
- **Camp-kill denial.** Awakened camps attack the **nearest champion of either team**, range 3. A
  camp kill destroys the victim's carried gems and pays no bounty.
  - Offence: finish a low-HP enemy carrier near a camp without committing to a fight.
  - Defence: a doomed carrier of ours dies to a camp to deny its bag.
  - GameRules.md contradicts itself on when a camp goes back to sleep ("no damage for 2 turns" vs "no
    champion in range for 2 rounds"). Verify in-engine before relying on it.
- **Capture play:** single-body captures with an off-region guard; steal or deny enemy captures; use
  respawn timers for routing; holding a spawn tile delays the whole group's respawn (situational
  denial).
- **Opening plan from draft memory:** per-body opening routes (first 2–3 groups, then bank, squad
  split) computed by the draft with whole-map knowledge. Adaptive after the opening, with the plan as
  a bonus rather than a command.

## Constraints

- Don't edit `bots/main`, `bots/duel`, `bots/neat` or `tools/*train*.py`; they belong to the training
  pipeline.
- Forager switches default to off.
- **Fuel:** 100k per champion turn. The fighter network can take 35k plus its own feature work
  (2v2 team-path turns already reach about 80k before the network). Keep per-turn comms and estimator
  work small; heavy planning goes in the draft turn (10× fuel) or each body's first turn (2×).
- Never run `cambc init`. Commit only your own files. No uploads or submissions.

## Evaluation and deliverables

- Full-engine matched games on all 17 published maps, both seats: against the current forager with
  the same switches, and against main / sparring-tarot, with the trained fighters attached where
  possible. Report per-map banked-gem margins, deaths while carrying gems, gems stolen and lost,
  captures stolen and lost, and first capture/deposit timings (`tools/replay` measures these).
- The macro proxy is fine for screening only (it runs ~35 rounds slow in the opening; its contest
  rules are counterfactual).
- `docs/TEAM_COMMS.md`: channel measurements, the bit-layout table, value-per-bit reasoning, the
  2–6 body scaling plan, and evaluation results.
- `team_comms.h` with tests; the estimators; the reworked strategies behind switches.
- A summary of which switches earned a default-on recommendation, and the exact fighter-field
  interface.
