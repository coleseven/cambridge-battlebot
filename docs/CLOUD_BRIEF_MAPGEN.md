# Cloud brief: `.map26a` codec + procedural map generator

Paste this as the first message of a cloud Claude Code session on `coleseven/cambridge-battlebot` (branch from `main`).

---

Build a reader/writer for the CAMBC `.map26a` map format and a procedural map generator, so our CMA-ES tuning has held-out maps to test against. Work on a new branch off `main` and open a PR to `main` when done. Budget is tight (about $20 of credit total), so stay on this scope: no bot strategy work, and don't modify `bots/`, `tools/arena.py`, `tools/eval.py` or `tools/tune.py`.

**Setup (Linux):** `python -m venv .venv && .venv/bin/pip install cambc` (CLI 2.1.6, same as `cambc.toml`), then `.venv/bin/cambc init --yes .` in the repo root. It downloads the WASI toolchain (~600 MB), restores the SDK and pulls the 9 published maps into `maps/`. Test a map with `.venv/bin/cambc run starter-cpp starter-cpp --map <path> --statistics /tmp/s.json`.

**What's known about the format:**
- `maps/*.map26a` are binary, 273–4,693 bytes. They start with ASCII magic `w62pam` followed by a few small header bytes; the size suggests the body is compressed or run-length encoded.
- The `cambc` binary contains the strings "corrupt xz stream" and "map too large", and `cambc mapeditor --no-browser` serves a local web map editor that can load and save maps. Its JS bundle (served locally, or cached under the cambc data dir) is the best reference for the exact encoding. The engine WASM used by the visualiser is another source.
- Game rules (tile types, camp types, map size 30×30 to 100×100, spawn tiles, altars) are in `docs/GameRules.md` and `docs/GAME_NOTES.md`; `sdk/cpp/cambc.h` shows what a map exposes.

**Deliverables:**
1. `tools/mapformat.py`: `read(path) -> MapData` (dimensions, per-tile terrain, spawn tiles per team, altars, camps with monster type, header fields such as champions per team) and `write(MapData, path)`. It must round-trip all 9 pool maps byte-for-byte. If the encoder can't be byte-identical, re-encoded maps must decode identically and play an identical game (same `--statistics` JSON with starter-cpp vs starter-cpp).
2. `tools/mapstats.py`: per-map features for the pool (size, symmetry type, terrain counts, camps by type, champions per team, spawn→altar and spawn→camp path distances).
3. `tools/mapgen.py --n 40 --seed S --out maps_gen/`: generate valid, fair maps resembling the pool:
   - Use the same symmetry type(s) as the pool, so both teams get mirrored spawns, altars and camps.
   - Every spawn must reach every altar and camp.
   - Vary size, terrain density, camp mix and champion count across the pool's ranges.
4. `tools/validate_maps.py maps_gen/`: every map decodes, and a full starter-cpp vs starter-cpp game finishes on it with zero bot faults. Commit 40 generated maps in `maps_gen/`.
5. `docs/MAP_FORMAT.md` documenting the format.

**Done** = PR open with the five deliverables, validation passing on all 40 maps, and round-trip checks passing on all 9 pool maps. Report the PR link and anything about the format you couldn't pin down.
