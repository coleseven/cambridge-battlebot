# Replay analytics toolkit (`tools/replay/`)

Turns CAMBC 2026a replays into a per-round timeline, derived events, metrics, an HTML report and a
compact LLM-ready `summary.json`.

```
replay (.repl26a / platform .zip)
  └─ decode.mjs ──> timeline.json ──> events.py ──> metrics.py ──> report.py (HTML, compare, summary.json)
fetch.py downloads platform bundles + official statistics; validate.py checks decoding against them.
```

## Setup

- Node 20+ (for `decode.mjs`) and Python 3.10+ (stdlib only).
- The engine WASM and the visualiser's snapshot decoder come from the cambc asset cache. Running
  `cambc view <replay>` (or `cambc mapeditor --no-browser`) once downloads them to e.g.
  `~/.cache/cambc/assets/<hash>/visualiser/assets/` (Linux) or `%LOCALAPPDATA%\cambc\...` (Windows).
  `decode.mjs` searches the usual cache locations; override with `--assets <dir>` or `CAMBC_ASSETS`.

## Usage

```bash
# decode (one file -> stdout or -o file; a directory -> <out>/<stem>.timeline.json)
node tools/replay/decode.mjs replays/main-vs-starter-cpp-123.repl26a -o game.timeline.json
node tools/replay/decode.mjs replays/platform -o timelines/

# events / metrics (accept timeline JSON or a replay/bundle, which they decode via node)
python tools/replay/events.py game.timeline.json -o game.events.json
python tools/replay/events.py game.repl26a --check-stats stats.json      # vs `cambc run --statistics`
python tools/replay/metrics.py game.timeline.json -o game.metrics.json [--early 150]

# reports
python tools/replay/report.py game game.timeline.json -o game.html --summary game.summary.json
python tools/replay/report.py compare gameA.timeline.json gameB.timeline.json -o compare.html
python tools/replay/report.py summary replays/platform/*.zip --out-dir summaries/

# platform bundles (public API, 1 s between requests by default)
python tools/replay/fetch.py team us --latest 10            # our team's latest 10 finished matches
python tools/replay/fetch.py ladder --top 10 --latest 2      # 2 matches from each top-10 team
python tools/replay/fetch.py group <groupId>   |   fetch.py match <matchId>...
# -> replays/platform/<matchId>.zip + <matchId>.meta.json (team names, official statistics)

# validation against official statistics (<stem>.meta.json from fetch.py, or <stem>.stats.json
# written by `cambc run ... --statistics <stem>.stats.json`)
python tools/replay/validate.py replays/platform matches/val
```

Decoding re-simulates every engine step (~60-75k steps for a 5v5 game): ≈2-4 s per game, ≈0.7 s for a 1v1 duel.

## How decoding works

A replay holds only inputs. `decode.mjs` loads the visualiser's engine (`v0_0_1-*.wasm`) under
Node's WASI, calls `Game_FromReplayFileBytes`, then steps the main timeline with
`Game_Visualiser_Timeline_ReplayNext` and reads each step with `Game_Visualiser_ReadState` from the
spectator perspective (team 2, so everything is known). The binary snapshot is decoded with the
visualiser's own decoder, imported from `PanelChrome-*.js` and found by behaviour (an export whose
source mentions `roundFlags`, `channelType` and `kda`), since export names are minified per build.
Per-round frames are the state at the end of each round; events are derived by diffing consecutive
engine steps (each step has a kind, an actor and a target tile).

## Timeline schema (`format: "cambc-timeline/1"`)

```jsonc
{
  "format": "cambc-timeline/1",
  "meta": { "file", "rounds", "outcome": 1|2,            // 1 = team A won, 2 = team B won
            "winner": 0|1|null, "final_gems": [A, B], "steps", "decode_ms", "engine",
            "drafts": [["Varg", ...], [...]], "bundle_files": [...],
            "platform": { ...fetch.py sidecar, only for platform bundles... } },
  "champions": [{ "id", "team": 0|1, "type", "name", "slot" }],   // bodies: Cassi and Issac are separate
  "groups": [{ "id", "camps": [{ "id", "type": "wolf|elk|gem_cluster|gold_cluster|monolith", "x", "y" }] }],
  "champion_cols": ["x","y","hp","max_hp","alive","gems","gold","cd0","cd1","stun","shield","channel",
                    "channel_left","respawn","kills","deaths","assists","items","consumables","ward_charges","earned_gold"],
  "frames": [{
      "r": 57,                       // round; state at the end of that round
      "sg": [A, B],                  // submitted (banked) gems
      "c": [[...one row per champion, in `champions` order, columns as champion_cols...]],
      "g": [[state, capture_team, capture_timer, alive_camps, alive_hp, respawn_rounds], ...],  // per group
      "w": [[team, x, y, owner, disabled, is_control_ward], ...]
  }],  // "g" and "w" appear only when changed since the previous frame: carry the last value forward
  "acts":  [[r, kind, champ, ...]],  // champion actions, see below
  "hits":  [[r, src, dst, amount, step_kind]],  // damage to champions (health + shield absorbed)
  "stuns": [[r, src, dst, rounds]],
  "deaths":[{ "r", "victim", "team", "killer", "by": "champion|camp|unknown", "gems", "pos", "credited", "gems_to_killer"? }],
  "camp_kills": [[r, camp_id, group, killer_champ|null]],
  "captures":   [{ "r", "group", "team", "gold", "gems", "champs", "split": { champ: [gold, gems] } }],
  "purchases":  [{ "r", "ch", "sell", "items": [[item_idx, delta]], "consumables": [[idx, delta]], "gold": delta }],
  "altar_locks":[{ "r", "ch", "team", "x", "y" }]
}
```

- Champion row: `x,y = -1` and `hp = 0` while dead. `cd0/cd1` ability cooldowns; `stun` remaining stun
  rounds; `shield` remaining shield amount; `channel` is `"R"` recall, `"T"` tribute (submit gems),
  `"P"` teleport crystal or `null`, with `channel_left` turn-ends remaining; `respawn` rounds until
  respawn; `kills/deaths/assists` cumulative (engine KDA); `items` a list of item indices (one entry
  per copy, see `events.ITEMS`: 7 families × Strength/Focus/Health × Shard/Emblem, even = shard);
  `consumables` counts `[teleport_crystal, altar_lock, ?, ?]`.
- Group `state`: 0 unknown, 1 has camps, 2 waiting for reward claim (dead, capturable), 3 waiting to respawn.
  `capture_team` 2 = nobody.
- `acts` kinds: `attack` / `cast` (`[r, kind, champ, target, x, y(, slot)]`; target is a champion id,
  `"m<camp_id>"`, or null), `recall`, `submit_gems` (tribute start), `gems_submitted` (`[.., amount]`),
  `shop`, `sell_item`, `place_ward`, `consumable`, `place_altar_lock`, `teleport_channel`,
  `teleport_complete` (`[.., x, y]`), `cancel_channel`, `resign`.
- `hits.src`: champion id, `"m<camp_id>"` for camps, or null. `step_kind = -1` marks the partner
  body's health drop when Cassi & Issac die together; it is not damage and is ignored downstream.
  Damage that Ren's passive reflects is attributed to Ren.

## Events schema (`events.py`)

A time-sorted list of objects, each with `type` and `r` (round):

| type | fields |
|---|---|
| `death` | `victim, team, killer, by, gems_carried, gems_lost, gems_to_killer, pos` (one per body) |
| `kill` | `killer, team, victims, gems` (one per credited kill; linked C&I deaths are one kill) |
| `engagement` | `end, duration, teams: [[ids A], [ids B]], first_hit, damage: [A, B], kills: [A, B], stuns: [A, B], focus: [A, B], hits, winner: 0/1/null, end_hp: {id: hp fraction}` |
| `camp_kill` | `camp, group, killer, team` |
| `capture` | `group, team, champs, reward: {gold, gems}, split, camps` |
| `deposit` | `champ, team, gems_at_start, completed, finish, size, reason` (`death/stun/cancelled/damage/other` when interrupted) |
| `recall` | `champ, team, completed, end, reason` |
| `purchase` / `sale` | `champ, team, items: [[name, delta]], consumables, gold` |
| `ward`, `altar_lock` | `champ, team, pos` |
| `first` | `of` (kill, death, camp_kill, capture, deposit, purchase, ward, altar_lock, recall, first_blood), `team, champ` |

Engagements: champion-vs-champion hits are clustered; a hit joins an open engagement that shares a
participant, and an engagement closes after 3 rounds without hits. Deaths up to 2 rounds after the
last hit count. Winner = more kills, else >25% more damage, else draw. `focus` = share of a team's
damage that went to its most-hit target.

## Metrics schema (`metrics.py`)

`{early_rounds, teams: [A, B], champions: {id: {...}}}`. Per champion:
`early {first_camp_kill, first_capture, first_deposit, first_shop, gold_unspent_r150, gems_carried_r150, gems_banked_r150}`,
`time {moving, clearing, capturing, channelling, fighting, dead, idle}` (rounds; each round goes to the
first matching bucket in the order dead, channelling, fighting, clearing, capturing, moving, idle),
`gems {collected, from_camps, from_kills, banked, lost, carried_end}`, `engagements {won, lost, drawn}`,
`kills, deaths, assists, damage_to_champs, damage_from_champs, gold_end, earned_gold, deposits, recalls`.
Teams aggregate the same (prefixed `gems_`, `time_`) plus `banked`, `captures`, `engagements` and team `early`.
`gems.collected` equals the official `collectedGems` (camps + gems taken from kills).

## summary.json (LLM input, ~5-7 KB per 5v5 game)

```jsonc
{ "v": 1,
  "match": { "id", "map", "file", "rounds", "winner": "A"|"B", "score": [A, B], "end": "1000 gems|round limit|resign/other" },
  "legend": { ...meaning of the compact arrays... },
  "teams": [{ "name", "draft", "banked", "kills", "deaths", "gems": {camps, kills, lost, end_carried},
              "captures", "fights": [won, lost, drawn], "dmg": [out, in], "gold", "early": {...}, "time%": {...} }],
  "champs": [{ "id", "n", "t": "A"|"B", "kda", "gems": [camps, kills, banked, lost], "dmg", "fights",
               "dep": [ok, interrupted], "rec": [ok, cancelled], "early", "time%", "items" }],
  "curve": [[round, A, B], ...],          // banked gems every 50 rounds
  "lead_changes": [[round, "A"|"B"]],
  "key_events": ["r212 A:Tavi killed B:Cassi+Issac (+48 gems)", ...],   // capped at 60 lines
  "notable": ["biggest gem loss: ...", "largest fight: ...", ...] }
```

## Validation

`validate.py` compares, per game, final submitted gems, team kills (kill events) and team body deaths
(death events) with the official statistics, and per champion banked gems, collected gems and
champion damage dealt/taken. Results on 12 local games (5v5, 3v3 and 1v1 duels incl. a Cassi & Issac
duel): all gems/kills/deaths match; per-champion banked and collected gems match; damage matches
exactly in 9 of 12 games.

## Known limits

- Damage attribution is from engine steps: in a step where Ren's passive reflects damage or Varg's
  splash lands, a few hits go to the wrong champion, and overkill on a killing blow can't be seen, so
  per-champion damage can be off by a few percent (team totals and kills/deaths are exact).
- Altar locks: placements are recorded; their destruction is not (the snapshot decoder doesn't expose locks).
- `channel = "P"` (teleport) is inferred from the channel type code 3; no test replay contained a teleport.
- Recall completion is inferred from the champion reappearing elsewhere after the channel; deposit
  completion from `gems_submitted` steps. Interruption reasons are best-effort (death > stun > cancel > damage).
- Camp kill credit goes to the champion acting in the step; camps that die to retaliation/splash may show `null`.
- The decoder is tied to the visualiser snapshot ABI (`Unsupported game snapshot ABI` means cambc
  updated: re-run `cambc view` so the matching assets are cached).
- Platform bundles: team A/B names and statistics come from the match group (`sides[0]` = team A).
