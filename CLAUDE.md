# Cambridge Battlecode 2026a (CAMBC)

Site: https://2026a.battlecode.co.uk/game/home · Rules summary: `docs/GAME_NOTES.md` · C++ API: `sdk/cpp/cambc.h`.

## Layout
- `bots/main/` — our bot (C++). `bots/starter-*` — official starters (sparring baseline, don't edit).
- `sdk/`, `cmake/`, `cambc.toml` — managed by `cambc init`; don't hand-edit.
- `maps/` — published map pool (`cambc maps pull` to refresh). `replays/`, `matches/` — git-ignored outputs.
- `tools/eval.py` — A vs B on every map, both seats, parallel.

## Commands (run from repo root; the CLI lives in `.venv`)
- Build: `.venv/Scripts/cambc build main`
- One match: `.venv/Scripts/cambc run main starter-cpp --map <stem> --view`
- Full suite: `.venv/Scripts/python tools/eval.py main starter-cpp --jobs 6`
- Replay: `.venv/Scripts/cambc view replays/<file>.repl26a`
- Submit (needs user go-ahead every time): `.venv/Scripts/cambc bundle main -o submissions` or `cambc submit main`.

## Constraints to remember
- C++ gets 100k fuel per turn; running out suspends the bot mid-turn. Prefer SDK bitboard/distance_field ops.
- WASI randomness is always zero; seed RNG from map state (see starter `random.h`). Matches are deterministic.
- Before creating a new frozen version, copy `bots/main` to `bots/vN` so it can be used as a sparring opponent.
