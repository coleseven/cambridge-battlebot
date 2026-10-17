"""End-to-end check of distributed evaluation on one machine (needs cambc; plays ~12 short duels twice).

Makes two worker checkouts (copies of tools/, bots/neat, sdk/, cmake/, cambc.toml sharing this .venv) in a
scratch folder, starts a worker process in each on a shared dist folder, and runs a coordinator here on
12 1v1 games between three bots/neat genome blobs (the hand prior and two random linear networks).
Worker w1 is killed (SIGKILL / TerminateProcess) as soon as it holds a claim, so its lease has to expire.
Then the same games are replayed with plain arena.play_many on an empty cache, and every distributed result
must equal the local one game for game.

  .venv/Scripts/python tools/dist_e2e.py [--scratch <dir>] [--games 12] [--team-size 1] [--no-kill] [--keep]
Exit code 0 = identical.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402

PY = Path(sys.executable)


def checkout(dest: Path):
    """Minimal repo copy a worker needs; .venv is linked so cambc and its toolchain are shared."""
    dest.mkdir(parents=True)
    for d in ("tools", "sdk", "cmake"):
        shutil.copytree(arena.ROOT / d, dest / d, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(arena.ROOT / "bots" / "neat" / "src", dest / "bots" / "neat" / "src")
    shutil.copy(arena.ROOT / "cambc.toml", dest / "cambc.toml")
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(dest / ".venv"), str(arena.ROOT / ".venv")], check=True,
                       capture_output=True)
    else:
        os.symlink(arena.ROOT / ".venv", dest / ".venv")


def linear_program(rng, nf, nf_used):
    import struct
    f = lambda x: struct.unpack("<I", struct.pack("<f", x))[0]   # noqa: E731
    words = [1, nf, 0, f(0.0), f(1.0), nf_used]
    for s in range(nf_used):
        words += [s, f(rng.gauss(0, 1))]
    return words + [nf]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", default="")
    ap.add_argument("--games", type=int, default=12)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--no-kill", action="store_true")
    ap.add_argument("--team-size", type=int, default=1, help="N-v-N games (maps_duel/<N>v<N>)")
    a = ap.parse_args()
    scratch = Path(a.scratch or tempfile.mkdtemp(prefix="dist-e2e-")).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    root = scratch / "dist"
    print(f"scratch {scratch}", flush=True)

    # The coordinator (this process) works in a scratch .arena so no real run's cache is read or written.
    arena.ARENA = scratch / "coord-arena"
    arena.CACHE = arena.ARENA / "results-coord.jsonl"
    arena._cache = None
    (arena.ARENA / "tmp").mkdir(parents=True, exist_ok=True)
    import dist
    import neat_train as nt
    import nvn

    slots = nt.build_slots(3, "e2eslots")
    rng = random.Random(11)
    progs = [{}, *({r: linear_program(rng, nt.NF, nt.NF_V1) for r in range(12)} for _ in range(2))]
    bots = [nt.patch(s, nt.blob(p, 800), n) for s, p, n in zip(slots, progs, ("prior", "lin1", "lin2"))]
    games = []
    for i in range(a.games):
        x, y = rng.sample(bots, 2)
        ta, tb = (tuple(rng.sample(range(11), a.team_size)) for _ in range(2))
        games.append((x, y, nvn.map_path(a.team_size, rng.choice(nvn.LAYOUTS), ta, tb)))

    procs = []
    for w in ("w1", "w2"):
        d = scratch / w
        if not d.exists():
            checkout(d)
        log = (scratch / f"{w}.log").open("w")
        procs.append(subprocess.Popen([str(PY), "-u", str(d / "tools" / "dist_worker.py"), "--root", str(root),
                                       "--jobs", "2", "--name", w, "--poll", "0.5", "--idle-exit", "20"],
                                      stdout=log, stderr=subprocess.STDOUT))
    dist.ensure(root)
    # wait until both workers have built their base and beat once
    t0 = time.time()
    while len(dist.live_workers(root)) < 2:
        if time.time() - t0 > 600 or any(p.poll() is not None for p in procs):
            raise SystemExit("workers did not start; see " + str(scratch / "w*.log"))
        time.sleep(1)

    killed = []
    if not a.no_kill:
        import threading

        def killer():
            while not list((root / "claimed").glob("*@w1.json")):
                time.sleep(0.05)
            procs[0].kill()
            killed.append(time.time())
        threading.Thread(target=killer, daemon=True).start()

    # min_steal 120 s: the local thread does not replay the workers' claims, so the results really come from
    # the worker processes, and w1's claim has to come back through lease expiry (10 s).
    coord = dist.Coordinator(root, name="e2e", batch=2, lease=10, poll=0.2, min_steal=120)
    t0 = time.time()
    got = coord.play_many(games, jobs=1)
    t_dist = time.time() - t0
    for p in procs:
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            p.kill()
    hbs = {w: dist.read_json(root / "workers" / f"{w}.json") or {} for w in ("w1", "w2", "e2e-local")}

    # Reference: plain local play, nothing cached.
    arena._cache = {}
    arena.CACHE = scratch / "reference.jsonl"
    t0 = time.time()
    want = arena.play_many(games, 2)
    t_local = time.time() - t0

    def norm(r):
        return json.dumps({k: v for k, v in r.items() if k not in ("cached",)}, sort_keys=True)
    same = sum(norm(x) == norm(y) for x, y in zip(got, want))
    errs = sum("error" in r for r in got + want)
    for x, (ga, gb, m) in zip(got, games):
        print(f"  {ga.name:6} vs {gb.name:6} {m.stem:28} {x.get('outcome', x.get('error', ''))[:20]:10} "
              f"round {x.get('round')}")
    print(f"games played: w1 {hbs['w1'].get('games', 0)} (killed: {bool(killed)}), w2 {hbs['w2'].get('games', 0)}, "
          f"coordinator-local {hbs['e2e-local'].get('games', 0)}; results used by source {coord.last}")
    print(f"generation stats (as logged by the trainers): {dict(coord.stats, workers=sorted(coord.stats['workers']))}")
    print(f"distributed {t_dist:.0f}s, local reference {t_local:.0f}s")
    print(f"{same}/{len(games)} games identical, errors {errs}")
    leftover = [p.name for s in ("queue", "claimed") for p in (root / s).glob("*.json")]
    if leftover:
        print("left in queue/claimed:", leftover)
    if not a.keep:
        shutil.rmtree(scratch, ignore_errors=True)
    ok = same == len(games) and not errs
    print("IDENTICAL" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
