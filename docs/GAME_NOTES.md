# CAMBC 2026a — game notes

Condensed from the official handbook (https://2026a.battlecode.co.uk/game/docs, exported 14 Oct 2026, engine 0.0.1 / CLI 2.1.6).
The full rules + C++ API markdown can be re-exported from the "Agents.md" docs page (Download GameRules.md / BotAPICpp.md).
The C++ API source of truth locally is `sdk/cpp/cambc.h`.

## Season
- 12 Oct launch → Sprint 1 locks **Sat 17 Oct 24:00 UK**, Sprint 2 locks 24 Oct, Sprint 3 locks 31 Oct.
- Int'l qualifiers 4 Nov, UK qualifiers 11 Nov, finals 18 Nov (Cambridge Union). Sprints are ladder-seeded tournaments.
- Languages: C++, Python, Rust, OCaml. All compile to WASM.

## Win condition
- First team to **tribute 1,000 gems** wins instantly. Otherwise at round 2,000: tributed gems → lifetime gold earned → champion kills → Team B.
- Carried gems count for nothing until tributed at an altar.

## Draft
- 3–5 champions per team depending on map; picked one at a time, simultaneously, picks revealed as they go.
- No duplicate within a team; mirror picks vs the opponent are allowed. Cassi & Issac = one pick, two bodies.
- Draft sees the **whole map**. Write up to **1,024 bytes of draft memory** → read-only to champions during the match.
- Draft gets 10× fuel on its first turn. Missing pick (yield/crash/out of fuel) → random.

## Turn structure
- Round start: camp respawns → spell/ward updates → camp captures → camp actions. Then champion turns, teams alternate, **starting team swaps every round**. Within a team: higher `set_priority` first (effective next round), then draft order. Dead champions still get a turn (to shop).
- Per turn: 1 move (8 dirs), 1 basic attack, 1 successful ability cast (2 slots, 8-turn cooldown each).
- **Ranged champions cannot move and basic attack in the same turn** (shared allowance); also applies to ranged damaging abilities marked "excludes walking". Melee can move+attack. Attack-reset abilities restore the attack allowance.
- Melee range 1, ranged range 3, Chebyshev distance. Most spells range 4.
- Channels (recall, tribute, teleport) tick at owner turn end (including the start turn), complete at next owner turn start once zero. While channelling, no other actions.

## Map
- 30×30 to 100×100. Tiles: wall (blocks LOS + movement, no dashing over), hole (see over, can dash over, can't stand), bush (blocks LOS, walkable; from inside you see the whole bush cluster + out), altar (impassable, tribute point), spawn (enemy can't enter; heals; shop).
- Dash = up to 2 steps on a legal path (can cross holes). Teleport crystal = up to 3 tiles ignoring terrain, 3-turn channel.
- Moving into a tile with an unseen enemy (in a bush) returns `false`, doesn't consume the move, reveals the tile. Moving into a *known* occupied tile throws.

## Camps (monsters)
| Monster | HP | Str | Range | Gold | Gems | Respawn |
|---|---|---|---|---|---|---|
| Wolf | 1,350 | 20 | 3 | 90 | 22 | 40 |
| Elk | 2,400 | 60 | 3 | 160 | 36 | 60 |
| Gem cluster | 500 | 0 | 0 | 0 | 24 | 50 |
| Gold cluster | 500 | 0 | 0 | 35 | 0 | 50 |
| Monolith | 20,000 | 200 | 3 | 300 | 200 | 150 |
- Camp group = cardinally connected monsters. Passive until hit; then attacks nearest champion each round until dead or 2 rounds with no in-range champion. Vision 8, sees through bushes, **does not chase**.
- **Capture**: after the whole group dies, stand on one of its spawn tiles uncontested for a 5-round timer. Leaving or enemy presence resets it. Extra allies don't speed it up; rewards split among allies present (lowest ID gets remainder). Respawn uses the longest member cooldown; an occupied spawn tile delays respawn.
- Gem clusters: 0 damage, 500 HP, 24 gems — free gems.

## Gems & altars
- Tribute: within Chebyshev 1 of an altar, ≥1 gem. Duration max(1, ceil(gems/10)) turn ends. Whole bag (incl. gems gained meanwhile) is tributed on completion. Damage doesn't interrupt; stun, death or altar lock does.
- **Altar lock** (10g consumable, place within 2 tiles): blocks enemy tributes at that altar, cancels their channels, removed by 3 basic attacks (tribute attempts against it auto-convert to attacks on it).

## Death & recall
- Death: killer gets **all carried gems + 50 gold**; assists get nothing; dying to a camp destroys the gems. Respawn after 60 turn-end ticks with full HP/abilities. Dead champs can still shop.
- Cassi & Issac die together (one kill reward, both bags).
- Recall: 5-turn channel (3 with Home emblem) to spawn, interrupted by damage/stun. Keeps gems. Stand on spawn through turn end to heal fully.

## Items (buy on spawn or while dead; Tavi buys shards anywhere)
- Shards: 30g, add the champion's growth value to Strength / Health / Focus. Health bought on spawn heals by the amount gained.
- 5 matching shards auto-combine into an emblem (150g) with a passive (one per family counts):
  - Monster: +10% damage to camps · Power: worth 6 shards, no passive · Durability: −10% damage taken · Treasure: +20% gold · Jungle: −30% damage from camps · Home: recall 3 turns · Bruiser: 5 + 1 of each other stat.
- Consumables 10g, carry one of each: altar lock, teleport crystal.

## Vision
- Radius 8, symmetric shadowcasting; walls and bushes occlude. **Vision is per champion — not shared with allies.** Each champion has one ward (vision 6, place within 2 tiles, re-placeable once per round, permanent).
- Team comms only via **8 bytes of shared memory** (read/write all match) + the read-only draft memory.

## Fuel (compute)
- **C++/Rust: 100k fuel per turn** (≈ instructions). OCaml 5×, Python 25×. Draft first turn 10×, first game turn 2×. Unused fuel doesn't carry over.
- Out of fuel → suspended, resumes from that point next round (no crash, but you lose the turn).
- 100k is tight: lean on the SDK's native bitboard / distance_field operations rather than hand-written loops over the grid.

## Champions (base HP / Str / Focus; growth per shard in brackets)
| Champion | Type | HP | Str | Focus | Slot 0 | Slot 1 | Passive |
|---|---|---|---|---|---|---|---|
| Cassi (C&I) | ranged | 700 (71.25) | 30 (3.5) | 70 (15) | 2×F dmg, r4, no-walk | 1-rd stun, r4 | dies with Issac |
| Issac (C&I) | melee | 900 (95) | 40 (4.75) | 100 (10) | attack reset | dash 2 | dies with Cassi |
| Ryth | melee | 1200 (100) | 50 (5) | 100 (10) | 1×F dmg + stun radius 1, r4, no-walk | attack reset | +1% bonus HP as Str |
| Vesper Vale | ranged | 900 (100) | 45 (5) | 100 (10) | attack reset | dash 2 | +20% gold |
| Prism | ranged | 700 (75) | 35 (3.5) | 80 (10) | 1.5×F + stun, r4, no-walk | 1×F radius 1, r4, no-walk | +0.2 Focus per carried gem |
| Stellar | ranged | 750 (75) | 35 (3.5) | 100 (10) | 2×F, r4, no-walk | 1.4×F radius 1, r4, no-walk | — |
| Vendetta | melee | 800 (100) | 70 (5) | 100 (10) | attack reset | dash 2 | kill (incl. monsters) resets cooldowns |
| Marcella | melee | 1200 (100) | 45 (5) | 50 (10) | attack reset; next attack stuns | 2×F, r4, no-walk | stun immune |
| Tavi | ranged | 750 (75) | 35 (5) | 100 (10) | attack reset | 2.2×F + stun, r4, no-walk | buy shards anywhere; sell at spawn |
| Willow | ranged | 700 (50) | 45 (3) | 105 (5) | heal ally 3×F, r4 | attack reset | — |
| Ren | melee | 900 (100) | 40 (5) | 100 (10) | 1.25×F + stun, r4, no-walk | dash 2 | retaliates 0.2×F when damaged |
| Varg | melee | 1000 (100) | 50 (5) | 100 (10) | attack reset | shield 2.5×F for 5 turns | basic attacks splash Str in radius 1 |
| #12 | — | — | — | — | classified / "under containment" | | not yet released (draft ids 0–10 only) |

`cambc::champion_draft` ids (also the `draftPicks` numbers in stats JSON): 0 Ryth, 1 Vesper Vale, 2 Vendetta, 3 Cassi & Issac, 4 Stellar, 5 Prism, 6 Marcella, 7 Willow, 8 Ren, 9 Varg, 10 Tavi.

## First observations (starter vs starter, 14 Oct)
- Matches are deterministic; identical bots split exactly 50/50 by seat across the 9 maps.
- Results swing hugely on draft (e.g. 1020–110 on `corners`), so the draft is a real lever.
- Full match ≈ 0.5–1 s of engine time; 18-game suite ≈ 9 s with 6 jobs.
