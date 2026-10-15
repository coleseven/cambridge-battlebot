# Cambridge Battlecode MOBA: top-ten scouting and Claude implementation brief

Leaderboard snapshot: **15 October 2026, 3:22 p.m. Eastern (19:22 UTC)**. This is a snapshot, not a permanent ranking.

## What was actually inspected

- Public [leaderboard API](https://2026a.battlecode.co.uk/api/v1/ladder?page=1&pageSize=10), two pages of public match groups per team, and official match statistics. The download contains 442 unique groups: 48 ranked and 394 unranked.
- The profiles below use **ranked games only: 240 unique matches, yielding 370 top-ten team appearances**. A match between two top-ten teams contributes two appearances. Each team has 25–80 matches in this sample. These are mostly from 16:50–19:20 UTC today; per-team windows differ. Unranked testing is deliberately excluded from the conclusions and win rates.
- **Six complete replay timelines covering all ten teams**, chosen as each team's newest available ranked match (four matches cover two top-ten teams). Decoded with the official viewer engine and an adapted public [Tarot Club decoder](https://github.com/FHL-08/Tarot-Club-Cambc2026/blob/main/tools/replay/decode.mjs). All six decoded final scores were checked against the API.
- Local rules and SDK, plus a read of our current draft, parameters, and relevant behavior code. No bot code was changed or submitted.

**Confidence:** drafts, purchases, scores, and quoted replay events are observations. Labels such as “farm-oriented” are interpretations. One replay per team cannot establish a universal route, exact threshold, or internal algorithm. Public records hide submission versions, so even this recent window can mix revisions. Match results and map records are small, opponent-dependent samples, not controlled comparisons.

## Main findings

1. **The common foundation is camp clearing and turning gems into score.** Across these profiles, teams average roughly 44k–57k monster damage versus 8k–13k champion damage per game. Damage totals alone do not prove priorities, but farming is substantial for every team, including melee-heavy Quantom.
2. **Cassi & Issac, Stellar, Ren and Varg are the main building blocks, but there is no universal draft.** Quantom skips Stellar completely in this ranked sample; several other teams switch compositions even at the same team size.
3. **Builds are champion- and team-specific.** Focus Ren and Strength Ren are both present. Some teams open Jungle; others open Power, Durability, or Bruiser. “Everyone buys Power first” is false.
4. **Banking is a policy, not one magic number.** Fish beats Mite with fewer, larger deposits in the inspected interlock replay. VeryGood wins rows with earlier, smaller deposits than Shingtonwash. Both batching and frequent deposits can work.
5. **Identical champions can produce radically different results.** hello beats Shawn 1,004–220 with identical C&I/Stellar/Ren drafts. Full Damage beats geese 1,050–110 with identical Ryth/Stellar/Prism/Ren/Tavi drafts. These are useful behavior comparisons, not proof of any single causal explanation.

## Top-ten profiles

Draft sequences below are observed examples, not recommended fixed orders. C&I is one draft pick but two bodies. Shard averages are per appearance of that body, across wins and losses; they are not literal build orders. Opening orders are stated only where a replay supports them.

### 1. Intercontinental Fish — rating 1918.3

**Interpretation: role-specialized farming with survivability and selective combat.**

- C&I appears in all 25 sampled ranked games. Common five-pick draft: **Ren / Stellar / C&I / Ryth / Varg** (12 games). A three-pick alternative is C&I/Vendetta/Varg. Ren and Stellar each appear in 21/25.
- Ren averages **19.1 Power Focus shards**, Issac **17.9 Power Strength**, and Varg **13.7 Power Strength** per appearance. Ryth and Ren also buy Bruiser Health and Durability Health. Cassi receives much less Power investment than Issac; this is consistent with different roles, though income differences also matter.
- Average kills 4.44 and body deaths 1.88 per game; sample record 16/25. This is the lowest body-death average of these ten profiles, without being the lowest-kill profile.
- **Replay:** beats Mite on interlock, **1066–972 at round 537**. Fish makes **17 deposits averaging 62.7 gems**; Mite makes 27 averaging 36. Fish's first deposit is 72 at round 55. Issac starts buying Power Strength at round 37; Ryth's first observed purchase is Bruiser Health, as is Ren's. At round 20, Stellar/Cassi/Issac are together while Ren and Ryth are elsewhere nearby: the opening is not one permanently packed blob.
- **What to test:** different jobs and shopping priorities for Cassi versus Issac; Focus Ren; durable frontline builds; banking batches chosen by route and danger. Do not turn 62.7 into a hard threshold.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/1f02041f-6e38-422e-9d18-71629ad11fb5)

### 2. Mite — rating 1869.2

**Interpretation: sustained farming with early Jungle protection and frequent scoring.**

- C&I 25/25, Ren 24/25. Common drafts: **Ren/Stellar/C&I** on three-pick maps, **Varg/Ryth/C&I/Ren** with four, **Varg/Stellar/C&I/Ryth/Ren** with five.
- Builds spread spending among Jungle, Durability, Power and Bruiser Health. This differs materially from Fish's heavier Power investment. Ren's sampled build leans Strength rather than Fish's Focus.
- **Replay:** against Fish, every Mite body starts with the relevant **Jungle shards**, then begins Durability purchases. Mite banks its first 22 gems at round 28 and makes 27 deposits. This confirms an early Jungle opening in this match; it does not prove the same order on every map.
- **What to test:** Jungle-first farming builds and small opportunistic deposits as a coherent alternative to Power-first/batching. Tune clear speed, survivability, and travel together.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/1f02041f-6e38-422e-9d18-71629ad11fb5)

### 3. Univardisty of Shingtonwash — rating 1856.9

**Interpretation: caster damage plus strong physical farmers, with a distinct Tavi economy build.**

- C&I and Stellar 25/25; Tavi 22/25; Varg 19/25. Common five-pick set: **Stellar/C&I/Tavi/Ren/Varg**, with different orders.
- Varg averages **22.8 Power Strength shards**; Issac 15.8. Stellar and Ren favor Power Focus. Tavi buys Treasure Focus as well as Power and Durability Focus.
- **Replay:** loses rows **792–1056** to VeryGood. Tavi's first observed shopping event at round 54 buys **five Treasure Focus shards and four Durability Focus shards**. Varg starts Power Strength at round 27. The team makes seven deposits averaging 113.1 gems; its first is round 151. VeryGood first scores at round 49.
- **What to test:** Treasure-first Focus Tavi as a separate build branch; evaluate whether its extra income repays its early cost. Watch delayed scoring and overlong banking batches.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/7743f796-0ca5-48f4-8ebd-4ad20ce51d5c)

### 4. VeryGood — rating 1831.4

**Interpretation: flexible compositions, including a support-assisted farming group.**

- C&I and Stellar 30/30. Willow appears 13 times; Tavi 16; Ren 14. Observed five-pick alternatives include **Stellar/Tavi/C&I/Willow/Prism** and **Ryth/C&I/Stellar/Ren/Varg**.
- Power Focus on Stellar/Cassi/Tavi, Power Strength on Issac/Varg. Willow's purchases lean Strength, not simply maximum healing Focus.
- **Replay:** wins rows **1056–792** using **Varg/Stellar/C&I/Willow**. All five bodies are within a three-tile-wide area at round 20. The team opens with Power purchases on each body, scores first at round 49, and completes **12 deposits averaging 88 gems**, all 66 or 132. This is observed grouped movement and a healer-containing composition; it does not by itself establish how much healing caused the win.
- **What to test:** an optional Willow-supported group on suitable camps, versus independent farmers. Account for overkill, capture reward splitting, and the opportunity cost of removing another damage dealer.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/7743f796-0ca5-48f4-8ebd-4ad20ce51d5c)

### 5. hello — rating 1805.8

**Interpretation: durable combat-capable farmers, with Ren and Issac doing substantial scoring.**

- Stellar 35/35, C&I 31/35. Common three-pick draft **C&I/Stellar/Ren**. Five-pick examples add Ryth/Prism or use Ryth/Stellar/Prism/Ren/Tavi.
- Ren averages **15.3 Bruiser Health shards**, plus Focus and Durability Health; Ryth favors Health. Issac averages 250 banked gems per appearance and Ren 245. Tavi leans Strength, unlike Shingtonwash's Focus Tavi.
- **Replay:** beats Shawn **1004–220 on collision**, both using C&I/Stellar/Ren. All hello bodies begin with **Durability Health** purchases before their later damage purchases. hello scores at round 33 versus Shawn's 93, and makes 28 deposits versus eight. hello's round-20 opening is tightly grouped.
- **What to test:** Durability-first on contested maps, sustained frontline Ren, and safe small deposits. The same-draft result makes this a good replay pair for comparing decisions, but it does not isolate items from routing or combat.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/b56c1cfe-6ba2-467a-92a9-2413675fa68c)

### 6. Shawn — rating 1801.0

**Interpretation: several composition/build modes with strong map-dependent results.**

- Ryth 37/45, Stellar 31, Ren 30; C&I only 25. Common five-pick draft **Ryth/Stellar/Prism/Ren/Tavi**. Four-pick alternatives include **Varg/Ryth/Vendetta/C&I** and Ryth/Stellar/Prism/Ren.
- Strength builds on Ren and Tavi distinguish this profile from several leaders. Jungle Health, Power Health and Monster shards also feature.
- **Replay:** against hello, all bodies begin with **Jungle Health**, with Monster Strength/Focus appearing afterward. The 220–1004 loss shows that this opening can fail badly on collision; it does not show that Jungle-first is generally inferior.
- Small sampled map records: rows **7/7 wins**, packs **4/4**, expanse **4/4**, but lanes **0/6** and collision **0/4**. These are leads for further testing, not reliable map win probabilities.
- **What to test:** map-conditioned draft and build selection; replay the failed collision opening and check travel, threat handling and time to first bank.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/b56c1cfe-6ba2-467a-92a9-2413675fa68c)

### 7. Sneaky Bush Campers — rating 1796.6

**Interpretation: mixed farming/caster composition with substantial Varg and Issac scoring. The name is not evidence of bush camping.**

- Stellar 45/45; C&I 37; Tavi 35; Varg 28. Three-pick C&I/Stellar/Varg is common. Other drafts use Ryth/Stellar/Ren/Tavi or include Willow/Prism.
- Varg averages **286 banked gems** and 19.8 Power Strength shards per appearance; Issac averages 241 banked gems. Ren favors Focus. Jungle and Durability are common additions.
- **Replay:** beats Quantom **1050–204 on interlock** using Ryth/Stellar/Ren/Tavi. First purchases differ by role: Bruiser Health on Ryth/Ren, Power Focus on Stellar, Jungle Focus on Tavi. Sixteen deposits average 65.6 gems; the first is round 55. The ranged pair are adjacent at round 20 with the melee bodies nearby.
- **What to test:** champion-specific opening builds and nearby melee/caster cooperation. Bush ambush logic is unverified and should not be attributed to this team.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/3f41ddac-5ef2-4c1d-ad5f-df68213758d5)

### 8. ❤️Quantom❤️ — rating 1790.8

**Interpretation: melee-heavy, Strength-oriented clearing rather than the mainstream Stellar composition.**

- Varg and Ren **25/25**, Ryth 20, Vendetta 19, C&I 15. **No Stellar, Tavi or Prism** in this sample. Common five-pick draft: **Varg/Ren/Vendetta/Ryth/C&I**.
- Power Strength dominates, including on Ren; Power/Durability Health and Monster Strength supplement it. Even Cassi's sampled purchases favor Strength. This is a strong outlier, not a reason to assume it is optimal.
- Only **2.24 kills/game**, the lowest among these profiles: melee-heavy does not mean successful kill hunting. Average monster damage is 50.8k/game.
- **Replay:** loses interlock 204–1050 to Sneaky Bush Campers. Each melee champion's first observed item family is Power Strength. Only four successful deposits, with long gaps after rounds 67 and 188.
- **What to test:** this as a distinct sparring archetype; measure farm throughput and ability to reach an altar under caster pressure. Avoid copying the entire build indiscriminately.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/3f41ddac-5ef2-4c1d-ad5f-df68213758d5)

### 9. procless — rating 1787.1

**Interpretation: durable camp-clearing specialists with distributed opening movement.**

- Varg and C&I 32/35, Stellar 31. **Varg/Stellar/C&I** appears in 11 three-pick games. Five-pick lineups commonly add Ren and Tavi.
- Varg averages **21.3 Power Strength shards**, five Jungle Strength, 4.8 Durability Strength, and 275 banked gems. Stellar also averages five Jungle Focus shards. Ren and Tavi lean Strength in this sample.
- **Replay:** beats test **1008–990 on expanse at round 363**. All four bodies start with Jungle shards. There are **22 deposits averaging 45.8 gems**, starting with 48 at round 38. At round 20, Cassi is at (21,0), while Stellar/Issac are around (9–11,16–18) and Varg at (4,11): clear evidence of spatially distributed opening movement, not proof of a specific assignment algorithm.
- **What to test:** separate camp assignments, Jungle-first durability, and opportunistic deposits during routes.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/25657330-8ca3-4b2f-9908-675d4e4a9c29)

### 10. geese geese go — rating 1786.8

**Interpretation: flexible drafts and high Power investment, with both farming and combat capability.**

- Ren 72/80, Stellar 64, C&I 63, Varg 53. Common five-pick drafts **Varg/Stellar/C&I/Tavi/Ren** and **Ryth/Stellar/Prism/Ren/Tavi**.
- Varg averages **24.8 Power Strength shards** and 281 banked gems; Ren 16.8 Power Focus and 227 banked gems. Durability Health complements these builds. Cassi gets much less investment than Issac.
- **Replay:** loses corners **110–1050** to Full Damage with identical Ryth/Stellar/Prism/Ren/Tavi drafts. geese opens Power purchases; Full Damage's first observed purchases are Monster shards. geese's first deposit is only at **round 261**, versus Full Damage's 44. At round 20, four geese bodies are around (31–35,28–35), while Tavi is at (10,38).
- This selected loss is not typical performance: geese wins **49/80** sampled ranked games. It is a useful failure case showing that draft and raw scaling do not ensure early scoring.
- **What to test:** Power versus Monster openings under matched conditions; instrument time to first capture and bank, especially when splitting a caster away from the group.
- [Replay](https://2026a.battlecode.co.uk/game/visualiser/37371e34-98c3-4e08-9118-6b2a8f9ee964)

## Concrete handoff to Claude

The current bot already has camp-value scoring, two-squad claims, abilities, retreat/recall, banking, and capture/tribute overlap handling. Do not rebuild those merely because they appear in this report. The meaningful gaps and experiments are:

1. **Make draft selection depend on map features and team size.** Current `draft.cpp` uses a fixed composition prefix. Add explicit 3/4/5-pick candidates from the observed profiles and compare them by camp density, distance, safety, and clear speed. Draft changes within a size are observed; the competitors' exact selection rules are unknown.
2. **Replace a universal first-emblem choice with champion-specific build tables.** Test Power-first, Jungle-first, Durability-first, and Bruiser-Health-first where appropriate. Compare Focus Ren versus Strength Ren and Focus Tavi versus Strength Tavi. Add Treasure Focus Tavi as a separate experiment. Our current `FIRST_EMBLEM = 1` is not enough to express these alternatives.
3. **Give Cassi and Issac distinct roles.** Strong teams often buy far more damage on Issac and obtain more banked gems from him. Test Cassi as ranged support near a productive group, while controlling the risk of their linked deaths. Do not interpret unequal purchases as proof that the team deliberately transfers income.
4. **Benchmark grouped clearing against distributed assignments.** Current `SQUADS = 2` and `CAPTURE_HANDOFF = 0` are adjustable starting points. Compare a durable farming group, independent farmers, and mixed pairs. Maintain an uncontested capture holder and avoid wasting additional bodies on a capture that needs only one.
5. **Make banking sensitive to route cost, enemy danger, and remaining score.** Current settings include `BANK_MIN_GEMS = 24` and nearby banking within seven tiles. Test batches around one or two camp rewards, but let nearby safe altars and the winning deposit override normal thresholds. The observed deposit sizes are outcomes, not recovered constants.
6. **Instrument the first 150 rounds.** Record first camp killed/captured, first deposit, time moving/clearing/capturing/channeling/dead, first shop, unspent gold, and gems lost. The replay comparisons show large early-score differences even with identical drafts. These measurements will distinguish routing problems from build or fighting problems.
7. **Validate combinations, not just isolated stat improvements.** Use the published map pool in both seats, several frozen bots and distinct sparring archetypes. Report win rate and score margin alongside first-bank time and death/lost-gem costs. Keep generated maps as held-out checks. A slower but safer build may only work with a different route or grouping policy.

Suggested order: **map-aware draft candidates → role-specific builds → opening routing/grouping → adaptive banking → selective combat tuning**. This is a recommendation derived from the evidence and our existing implementation, not a recovered competitor architecture.

## Important limits and data pitfalls

- **Do not claim a top-ten team uses A*, MCTS, a fixed route scheduler, a particular shared-memory protocol, or a specific retreat threshold.** We have not seen their source code.
- **Do not infer purchase order from match-end item totals.** The replay examples above supply actual first observed purchases. Purchases in the same action are not necessarily ordered within that action.
- Item statistics include odd-index emblem entries produced by combining shards. The profile script counts even-index shard entries to avoid treating automatic combinations as additional purchases.
- **Consumables remain unidentified in these statistics.** Four-element arrays show activity at index 2, while the installed SDK exposes two consumable enums at indices 0 and 1. Until the schema mapping is verified, do not call index 2 an altar lock or teleport crystal. The older `RESEARCH.md` guess is not evidence.
- Death averages count physical body rows; C&I can contribute two deaths for one linked death event. They are not exactly enemy kill counts.
- Map records and sample win percentages are not ladder win rates. Ratings use a broader history. Current comparisons have different opponents, maps and sample windows.
- No broad conclusion about wards, altar locks, monolith targeting, bush ambushes, stun chaining or exact camp cycles is established by this pass.

## Reproducibility

Raw snapshot: `scouting/2026-10-15/ladder.json` and `groups/*.json`.

Computed profiles: `scouting/2026-10-15/profiles.json`, generated by `tools/scout_profiles.py`.

Replay inputs and measurements: `scouting/2026-10-15/replays/*.repl26a`, `analyze_replays.mjs`, and `replay-analysis.json`. The six replay results match the official final scores exactly. Decoder assets are the local viewer's own engine and snapshot decoder.

The existing scout downloader completed saving all 442 groups, then hit a Windows console encoding error while printing Quantom's name. That display failure did not invalidate the saved JSON; the separate profile analysis completed successfully.
