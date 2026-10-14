# Cambridge Battlecode 2026a (CAMBC)

Site: https://2026a.battlecode.co.uk/game/home · Rules summary: `docs/GAME_NOTES.md` · C++ API: `sdk/cpp/cambc.h`.

## Layout
- `bots/main/` — our bot (C++). `bots/starter-*` — official starters (sparring baseline, don't edit).
- `sdk/`, `cmake/`, `cambc.toml` — managed by `cambc init`; don't hand-edit.
- `maps/` — published map pool (`cambc maps pull` to refresh). `replays/`, `matches/` — git-ignored outputs.
- `tools/arena.py` — match engine: builds bots/variants, runs `cambc run` in parallel, caches every result in
  `.arena/results.jsonl` keyed by (cambc version, wasm hash A, wasm hash B, map hash). Deterministic engine → never replays.
- `tools/eval.py` — one bot vs an opponent pool, all maps, both seats, plus fitness.
- `tools/tune.py` — CMA-ES over `src/params.h` numbers (`prepare` / `run` / `best`; runs live in `.arena/runs/<name>/`).
- `tools/scout.py` — public ladder/match statistics scraper (`scouting/`).
- `bots/sparring-*` — git-ignored external opponents (e.g. `sparring-tarot` = Tarot Club's public foundry snapshot).

## Commands (run from repo root; the CLI lives in `.venv`)
- Build: `.venv/Scripts/cambc build main`
- One match: `.venv/Scripts/cambc run main starter-cpp --map <stem> --view`
- Full suite: `.venv/Scripts/python tools/eval.py main --vs starter-cpp sparring-tarot` (add `--set NAME=val` to try params)
- Tune: `.venv/Scripts/python tools/tune.py prepare r1 --space tools/space-main.json --vs starter-cpp sparring-tarot`
  then `tools/tune.py run r1 --gens 20` (resumable) and `tools/tune.py best r1`.
- Replay: `.venv/Scripts/cambc view replays/<file>.repl26a`
- Submit (needs user go-ahead every time): `.venv/Scripts/cambc bundle main -o submissions` or `cambc submit main`.

## Constraints to remember
- C++ gets 100k fuel per turn; running out suspends the bot mid-turn. Prefer SDK bitboard/distance_field ops.
- WASI randomness is always zero; seed RNG from map state (see starter `random.h`). Matches are deterministic.
- Tunable numbers go in `bots/<bot>/src/params.h` as `constexpr <type> NAME = <number>;` lines.
- Throughput on this PC (i9-9900K): ~16k full games/hour at 12 jobs; 16 jobs is slower. One CMA-ES generation of
  8 candidates × 2 opponents × 9 maps × 2 seats ≈ 75 s.
- Before creating a new frozen version, copy `bots/main` to `bots/vN` so it can be used as a sparring opponent.
