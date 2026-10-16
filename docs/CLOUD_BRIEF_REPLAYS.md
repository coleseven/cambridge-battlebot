# Cloud brief: replay analytics toolkit

Build a toolkit that turns CAMBC 2026a replays into timelines, events, metrics, reports and compact
summaries. Work on a new branch off `main` in coleseven/cambridge-battlebot and open a PR to `main`.
Budget is tight (about $20 of credit), so stay on this scope. Don't modify `bots/`, `tools/arena.py`,
`tools/eval.py`, `tools/tune.py`, `tools/duel_train.py`, `tools/neat_train.py`, `tools/cgp_train.py`
or `tools/jobrunner.py`.

## Why

Three workstreams need to see what happened in a game, not just its final score:
1. Learned fighting networks (NEAT and CGPANN, trained in 1v1 duels). Do they fight well? Do they
   focus targets, chain stuns, disengage at low health?
2. Farming and banking (`bots/forager`, `docs/FARMING_MODULE.md`). Measure time to first capture and
   deposit, idle time, and gems lost.
3. Our ladder losses (team "777") and top teams' games (`docs/TOP_10_SCOUTING_2026-10-15.md`).

The summaries will later feed an LLM "replay coach" that diagnoses losses in bulk, so a compact,
well-structured per-game JSON summary is a first-class output.

## Setup (Linux)

`python -m venv .venv && .venv/bin/pip install cambc`, then `.venv/bin/cambc init --yes .` (pulls the
toolchain, SDK and the 9 maps). Make local replays with `.venv/bin/cambc run main starter-cpp --map
<stem>`; each run writes a `.repl26a` bundle under `replays/`. For a 1v1 duel replay,
`.venv/bin/python tools/duel_maps.py --size 1 --out maps_duel/1v1`, then
`.venv/bin/cambc run duel duel --map maps_duel/1v1/open__varg-ren.map26a`.

## What's known

- A local `.repl26a` bundle is a zip containing `replay.repl26a` plus each team's stdout and debug
  log. Platform bundles are zips too, from `GET https://2026a.battlecode.co.uk/api/v1/matches/<matchId>/bundle`
  (public, no auth). Match and team listings: `/api/v1/match-groups?scope=all&team=<teamId>`,
  `/api/v1/match-groups/<groupId>` (includes official per-match `statistics`), and `/api/v1/ladder`.
  See `tools/scout.py` for working calls. Our team id is `1158c8e5-3ab7-47da-9a7a-344e175243d0`.
- The replay holds only inputs; the state comes from re-simulating it with the engine. The visualiser's
  own engine WASM plus its snapshot decoders can replay a match headlessly in Node. Tarot Club's public
  decoder shows how (https://github.com/FHL-08/Tarot-Club-Cambc2026, `tools/replay/decode.mjs`,
  `assets.sh`, `extract.mjs`, `report.py`). It loads `v0_0_1-*.wasm` and the `PanelChrome-*.js` module
  from the visualiser assets and finds the minified decoders by behaviour. Our CLI serves the same
  assets (`cambc view` / `cambc mapeditor --no-browser`; they're also cached under the cambc data dir).
  Write our own implementation; don't vendor their repo.
- Rules: `docs/GameRules.md` and `docs/GAME_NOTES.md`. Item and champion enums: `sdk/cpp/cambc.h`
  (champion_type, item_type, camp_type).

## Deliverables (under `tools/replay/`)

1. `decode.mjs` (Node 20+): replay or bundle (local `.repl26a` or platform `.zip`) -> per-round JSON
   timeline. For every champion: position, health and max health, alive, gems carried, gold, items,
   ability cooldowns and buffs (stun or shield) where the snapshot exposes them, and the current
   channel (recall, tribute or teleport). Plus camp-group states, altar locks, wards and both teams'
   submitted gems. Keep it compact: no redundant per-tile data.
2. `events.py`: derive events from the timeline: deaths (victim, killer if inferable, gems carried and
   lost), damage exchanges between champions grouped into "engagements" (participants, duration, who
   won, stuns landed), captures (group, team, reward), deposits (start, finish, size, interrupted?),
   recalls (completed or cancelled), purchases and first-of-kind events.
3. `metrics.py`: per champion and team, the first-150-rounds metrics (first camp killed, captured and
   deposited; first shop; unspent gold), a time budget (moving, clearing, capturing, channelling,
   dead, idle), gems collected / banked / lost, engagements won/lost, kills and deaths.
4. `report.py`: a self-contained HTML report for one game (score curve, timeline strip per champion,
   event list, metrics table), and a compare mode for two games (e.g. same draft, different result).
   Also `summary.json`: a compact, token-efficient game summary designed for an LLM to read (key
   events, metrics, notable moments; aim for a few KB per game).
5. `fetch.py`: download platform bundles by match id, by match group, or the latest N for a team
   (with a polite delay between requests).
6. `tools/replay/README.md`: usage, the timeline/event/summary schemas, and known limits.

## Validation (required in the PR)

- Decoded final scores must equal the official statistics for at least 10 platform matches (mix our
  team's and top-10 teams'), plus at least 3 local games.
- Works on a 1-champion duel replay (`bots/duel` or `bots/neat` on `maps_duel/1v1/...`).
- Event sanity: deaths and kills per team match the official statistics on those matches.

Done = PR open with the six deliverables and the validation results in the PR description. Report the
PR link and anything you couldn't decode reliably.
