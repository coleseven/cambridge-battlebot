# Distributed game workers

Roadmap item 1b. The trainer on the 24/7 training PC is the **coordinator**. It splits each batch of games
into tasks in a shared folder. **Workers** on any machine (training PC, night PC, laptop, cloud) claim
tasks, play them and write the results back. Code: `tools/dist.py` (queue, leases, coordinator),
`tools/dist_worker.py` (worker CLI + `status`). Tests: `tools/test_dist.py` (no engine) and
`tools/dist_e2e.py` (real engine, one machine).

Distribution is **off unless asked for**. Without `--dist` (or the env var `BC_DIST`), every tool calls
`arena.play_many` exactly as before.

## Design

- **Drop-in.** `dist.play_many(games, jobs, progress, root)` takes and returns the same things as
  `arena.play_many`. `nvn.evaluate` (both trainers' N-v-N stage), `tools/league.py` and
  `tools/champions.py` call it and take `--dist <folder>`. The 1v1 trainer loops and `duel_train` still
  call `arena.play_many` directly.
- **What a task carries.** The game keys plus everything needed to rebuild each bot exactly:
  - `blob` bots: a bots/neat build patched with a genome blob (every slot bot of the trainers, league and
    champions tools). The task carries the base id and the blob bytes.
  - `dir` bots: a repo bot directory such as `bots/starter-cpp`, which the worker builds itself.
  - Maps travel as bytes (a duel map is ~150 B). Generated `maps_duel/` files don't need to exist on the
    worker.
  - Anything else (params variants in `.arena/bots/`, …) is played by the coordinator itself.
- **Exact reproduction is checked, not assumed.** Every bot is identified by its wasm hash, like the
  arena cache. A worker patches the blob into its own build and recomputes the hash. If it differs from the
  coordinator's, the game is not played. After each game the worker checks that `cambc run` didn't rebuild
  the slot (a rebuild would silently drop the genome). Results are keyed by
  `(cambc version, wasm A, wasm B, map hash)`, the same key as `.arena/results-*.jsonl`.
- **Base id** = sha256 of `bot.wasm` + `bot-injected.wasm` with the genome block emptied (magic words
  kept). Two machines that compile the same `bots/neat/src` to the same bytes have the same base id. The
  coordinator also publishes its emptied base wasm under `bases/<id>/`. A worker started with
  `--adopt-base` uses that copy when its own build differs (other OS or compiler). The engine only reads
  the wasm, so the worker then plays exactly the coordinator's bot.
- **The coordinator is also a worker.** `play_many` starts `jobs` local threads. They play the games that
  can't be shipped and claim queued tasks like any worker, using the caller's own Bot objects, so they
  play exactly the games `arena.play_many` would. With no remote worker the local threads play
  everything: same results, plus a few small JSON files.
- **Tail.** When the queue is empty, idle local threads replay a task a remote worker has held for longer
  than 2× the median task time (at least 5 s; 30 s before any task has finished). A slow or vanished
  worker therefore can't hold a generation hostage. The first result to arrive wins; both are identical.
- **Idempotent.** The engine is deterministic and tasks are content-addressed. A task played twice (reaped
  lease, restart, sync race) gives identical results, and the duplicate is ignored. Results are also
  appended to the coordinator's arena cache, so a rerun of the same games is free.

## File layout (`--root`, default `.arena/dist`)

```
queue/<id>.json              waiting task
claimed/<id>@<worker>.json   task being played (renamed from queue/ = the claim)
results/<id>.json            finished task; the coordinator ingests it into its arena cache and deletes it
workers/<name>.json          heartbeat of every worker (and of each coordinator's local worker <coord>-local)
bases/<base id>/bot.wasm, bot-injected.wasm    coordinator's base build, genome emptied (for --adopt-base)
```

All writes are tmp-file + rename, so nobody reads half a file.

## Task format

```json
{"id": "9c1e…(20 hex)", "coord": "trainpc", "cambc": "cambc 2.2.2", "created": "2026-10-16T20:41:59",
 "bots": {"<wasm hash>": {"kind": "blob", "base": "<base id>", "blob": "<b64 zlib>", "hash": "<wasm hash>"},
          "<wasm hash>": {"kind": "dir", "dir": "bots/starter-cpp", "hash": "<wasm hash>"}},
 "maps": {"<map hash>": {"name": "open__ryth-ci.map26a", "data": "<b64 zlib>"}},
 "games": [{"key": "cambc 2.2.2|<hash A>|<hash B>|<map hash>", "a": "<hash A>", "b": "<hash B>", "map": "<map hash>"}]}
```

- `id` = sha256 of the coordinator name and the sorted game keys, so the same batch always gets the same id.
- A task holds `BC_DIST_BATCH` games (default 4); bots and maps are deduplicated within a task.
- A result file holds `{"id", "coord", "worker", "secs", "finished", "results": {key: arena result}}`.
- A result with `"error"` (e.g. a cambc failure on the worker) is replayed once by the coordinator
  locally. Error results never enter the cache.

## Claims and leases

- **Claim:** `os.rename(queue/<id>.json → claimed/<id>@<worker>.json)`. On one filesystem only one rename
  wins; losers move on to the next file. A worker claims only tasks it can play (same cambc version, every
  bot reproducible). Other tasks stay queued for another worker.
- **Heartbeat:** each worker rewrites `workers/<name>.json` with an increasing `seq`:
  - remote workers every 10 s;
  - a coordinator's local worker on every poll during `play_many`.
- **Lease:** a claim is valid while its worker's `seq` keeps changing. The reaper (run by the coordinator
  and by every worker) returns `claimed/<id>@w.json` to `queue/` when any of these holds:
  - `w`'s seq has not changed for `BC_DIST_LEASE` seconds (default 120);
  - `w` has no heartbeat file for that long;
  - `w` wrote `state: stopped`;
  - the claim is older than `BC_DIST_MAX_CLAIM` (default 3600 s).
- **No clock comparison between machines:** each reaper measures "unchanged for 120 s" on its own clock.
- **Cleanup:** a claim whose result already exists is just deleted (the worker died between writing the
  result and dropping the claim).
- **A reaped worker that comes back** and finishes anyway writes a result for a task that is already
  requeued or done. That's harmless: `complete()` also removes the requeued copy, and the coordinator
  takes the first result.

## Running it

Start every machine from the same git commit of `bots/neat/src`, with the same cambc (2.2.2; never run
`cambc init`).

Coordinator (training PC), e.g. the 2v2 run:

```
.venv/Scripts/python -u tools/neat_train.py run n2v2 --team-size 2 --jobs 4 --dist .arena/dist
# or: set BC_DIST=.arena/dist   (then every tool that supports it distributes)
```

Workers (any machine, sharing the dist folder):

```
.venv/Scripts/python tools/dist_worker.py --root <shared>/dist --jobs 12 --name nightpc [--adopt-base]
.venv/Scripts/python tools/dist_worker.py status --root <shared>/dist
```

- On start-up a worker builds `2 × jobs` slot copies of `bots/neat` in `.arena/distslots/<name>/`. Each
  slot is built in place, because cambc reconfigures a copied build directory. Later starts reuse them.
- `--name` must be unique per worker. The default is host + a hash of the checkout path.
- On start, a worker returns any claims its name still holds from a crash.
- Optional knobs: `--poll` (queue poll, 2 s) and `--idle-exit S` (exit after S idle seconds).
- `status` prints one row per worker (state, seconds since the last heartbeat, claims, games, games/h) and
  the queue depth.

Night window (night PC, 00:00–12:00): start the worker at 00:00 from Task Scheduler (or a jobrunner job)
with

```
.venv/Scripts/python tools/dist_worker.py --root <shared>/dist --jobs 12 --name nightpc --until 12:00
```

- It stops claiming `--margin` (120 s) before 12:00 and finishes the tasks it holds.
- It then writes `state: stopped`, which releases its leases at once, and exits.
- Pick `--margin` larger than one task's play time if 12:00 is a hard stop.
- Ctrl+C returns unfinished claims to the queue immediately.

Coordinator restart: just rerun the trainer. It resumes from `state.pkl` and regenerates the same games,
so tasks get the same ids. It then:
- ingests any results that arrived meanwhile into the arena cache (those games are not replayed);
- reuses tasks still queued or claimed;
- deletes queued tasks of its own whose games are all cached.

Several coordinators can share one root as long as their names differ (`BC_DIST_NAME`; default host
name). Each one ingests only its own results.

## Failure handling

| Failure | What happens |
|---|---|
| Worker killed mid-task | Its heartbeat stops; after the lease its claims go back to the queue. The coordinator's idle threads may replay them sooner (tail rule). |
| Worker comes back after its lease was reaped | Its late result is a duplicate; ignored. |
| Worker can't reproduce a bot (different build) | It never claims the task; another worker or the coordinator plays it. `--adopt-base` fixes it for bots/neat. |
| Worker's cambc fails on a game | Error result; the coordinator replays that game locally once. |
| Coordinator killed | Workers finish what they hold; results wait in `results/`. Restart resumes (above). |
| No remote worker at all | Local threads play everything; same results as `arena.play_many`. |
| Torn heartbeat/task read | Ignored until the next poll (all writes are atomic renames). |

## Tests

- `.venv/Scripts/python -m unittest tools/test_dist.py -v`: 22 tests with fake games. They cover:
  - claim races (8 threads, 40 tasks, each claimed once);
  - idempotent submit;
  - lease expiry for a killed worker (lease holds while the heartbeat moves, then requeues), for a worker
    with no heartbeat, for a stopped worker, and the max claim age;
  - a claim with a result already written; a late finish after a reap;
  - release; `--until` deadline maths and draining;
  - worker refusal (other cambc, unknown base);
  - blob → worker rebuild reproducing `neat_train.patch` byte for byte, and `--adopt-base`;
  - the coordinator with a fake engine: zero workers = local results, two fake remote workers = local
    results, a remote worker killed mid-task, coordinator restart with a finished task (not replayed), and
    remote error → local replay, local engine exception re-raised.
- `.venv/Scripts/python tools/dist_e2e.py [--team-size 2] [--games 12] [--no-kill]`: the real engine on
  one machine. It:
  1. makes two worker checkouts (copies sharing this `.venv`);
  2. starts a worker in each (2 jobs each) and a coordinator with 1 local job, all in a scratch `.arena`
     (no real run is touched);
  3. kills worker w1 as soon as it holds a claim;
  4. replays the same games with plain `arena.play_many` on an empty cache and compares them game for game.

  Results in the cloud container (Linux, 4 cores, cambc 2.2.2):
  - 12 1v1 games: w1 killed holding a claim, which came back after the 10 s test lease. w2 played 8, the
    coordinator 4. **12/12 identical**.
  - 6 2v2 games: **6/6 identical**, 4 of them from the worker.

## Not tested yet

- **Two real machines over Syncthing.** Everything above assumed one filesystem, where rename is an atomic
  claim. Under Syncthing two machines can both "claim" the same task before the renames sync. Both then
  play it, which is harmless but wasted.
  - To try it, add `!/dist` to `tools/stignore.txt` (not done here: the default sync set is unchanged).
  - Sync latency adds to every task, so use bigger batches (`BC_DIST_BATCH=8`+).
  - Syncthing may create `*.sync-conflict-*` files in `workers/`. They are ignored (not `*.json`).
- **Windows ↔ Linux base ids.** Whether a Windows and a Linux build of `bots/neat` give the same wasm bytes.
  If not, start the Linux/cloud workers with `--adopt-base`.
- **The 1b success test.** A real 2v2 generation (~20 min on the training PC) with the night PC attached;
  we expect ≈4–5 min.
- **Long runs.** Heartbeat files and `.arena/distmaps/` grow slowly. Nothing prunes `workers/` (status
  hides entries older than a day).
- **Wasted plays in the tail.** At the end of a generation, the coordinator's idle threads may duplicate a
  slow worker's last tasks (by design). A remote worker's claim may also outlive the coordinator's
  `play_many` call. The coordinator ingests that result next time and tidies leftover queued tasks.
