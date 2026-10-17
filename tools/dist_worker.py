"""Worker for distributed game evaluation (tools/dist.py, docs/DIST_WORKERS.md).

Claims tasks from a shared dist folder, rebuilds each bot from the task (genome blob patched into this
checkout's own bots/neat build, or a repo bot directory built here), checks that every wasm hash matches
what the coordinator expects, plays with arena.play and writes the results back.

  .venv/Scripts/python tools/dist_worker.py --root .arena/dist --jobs 12 [--until 11:50] [--name nightpc]
  .venv/Scripts/python tools/dist_worker.py status --root .arena/dist

--until HH:MM: stop claiming `--margin` seconds before the next HH:MM (local time), finish the tasks already
claimed, then exit cleanly. Ctrl+C gives unfinished claims back to the queue at once.
Tasks are refused (left in the queue for another worker, never claimed) when their cambc version differs
from this checkout's, or when a bot cannot be rebuilt to the coordinator's exact wasm hash: a bots/neat
base build that differs (unless --adopt-base copies the coordinator's build) or a repo bot that differs.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import os
import shutil
import socket
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import dist  # noqa: E402


def deadline(hhmm: str, now: datetime | None = None) -> float:
    """Epoch time of the next HH:MM (today if still ahead, else tomorrow)."""
    now = now or datetime.now()
    h, m = map(int, hhmm.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return t.timestamp()


def default_name() -> str:
    return f"{socket.gethostname().lower()}-{hashlib.sha256(str(arena.ROOT).encode()).hexdigest()[:4]}"


class Worker:
    def __init__(self, root, name=None, jobs=2, until=None, margin=120.0, adopt_base=False, poll=2.0,
                 idle_exit=0.0, lease=dist.LEASE, beat=10.0):
        self.root = dist.ensure(root)
        self.name = name or default_name()
        self.jobs, self.poll, self.idle_exit, self.beat_every = jobs, poll, idle_exit, beat
        self.stop_claim_at = (deadline(until) - margin) if until else None
        self.adopt_base = adopt_base
        self.reaper = dist.Reaper(self.root, lease=lease)
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.current = set()           # task ids claimed and not yet finished
        self.bases = {}                # base hash -> (bot.wasm, bot-injected.wasm) with genome zeroed
        self.dirs = {}                 # "bots/x" -> Bot, or None if its hash differs from the coordinator's
        self.refused = {}              # task id -> reason (logged once)
        self.last_work = time.time()
        self.version = None
        self.hb = None
        self.slot_root = arena.ARENA / "distslots" / self.name

    # ------------------------------------------------------------ start-up
    def setup(self):
        self.version = arena.cambc_version()
        if not self.version:
            raise SystemExit(f"cambc not found at {arena.CAMBC}")
        # Each slot is built in place: cambc reconfigures (and rebuilds, losing the patch) a copied build dir.
        dirs = [self.slot_root / f"t{i:02d}{side}" for i in range(self.jobs) for side in "ab"]
        for d in dirs:
            if not (d / "src").exists():
                shutil.copytree(arena.ROOT / "bots" / "neat" / "src", d / "src")
        with cf.ThreadPoolExecutor(min(len(dirs), max(2, os.cpu_count() or 2))) as ex:
            bots = list(ex.map(lambda d: arena.build(d, d.name), dirs))
        own = set()
        for d in dirs:
            data = [(d / ".cambc" / "build" / n).read_bytes() for n in dist.WASMS]
            own.add(dist.base_hash(data))
        if len(own) != 1:
            raise SystemExit(f"slot builds differ: {own}")
        own = own.pop()
        self.bases[own] = tuple(dist.zeroed(x, dist._region(x)) for x in data)
        self.slots = [dirs[2 * i:2 * i + 2] for i in range(self.jobs)]
        bot = bots[0]
        self.hb = dist.Heartbeat(self.root, self.name, role="worker", cambc=self.version, jobs=self.jobs,
                                 base=own,
                                 until=datetime.fromtimestamp(self.stop_claim_at).isoformat(timespec="minutes")
                                 if self.stop_claim_at else None)
        for p in (self.root / "claimed").glob(f"*@{self.name}.json"):   # left by an earlier crash of this worker
            dist.release(self.root, self.name, dist.parse_claim(p.name)[0])
        self.hb.beat(state="idle")
        self.last_work = time.time()
        print(f"{dist.now_iso()} worker {self.name}: {self.version}, bots/neat base {own} ({bot.hash}), "
              f"{self.jobs} jobs, root {self.root}", flush=True)

    # ------------------------------------------------------------ can this worker play the task?
    def _base(self, b):
        if b in self.bases:
            return self.bases[b]
        d = self.root / "bases" / b
        if self.adopt_base and all((d / n).exists() for n in dist.WASMS):
            data = tuple((d / n).read_bytes() for n in dist.WASMS)
            if dist.base_hash(data) == b:
                self.bases[b] = data
                print(f"{dist.now_iso()} adopted coordinator base {b}", flush=True)
                return data
        return None

    def _dir_bot(self, rel):
        with self.lock:
            if rel not in self.dirs:
                p = arena.ROOT / rel
                try:
                    self.dirs[rel] = arena.build(p) if (p / "src").is_dir() else None
                except RuntimeError:
                    self.dirs[rel] = None
            return self.dirs[rel]

    def why_not(self, task):
        if task.get("cambc") != self.version:
            return f"cambc {task.get('cambc')!r} != {self.version!r}"
        for h, s in task["bots"].items():
            if s["kind"] == "blob" and self._base(s["base"]) is None:
                return f"unknown bots/neat base {s['base']} (rebuild from the coordinator's commit, or --adopt-base)"
            if s["kind"] == "dir":
                bot = self._dir_bot(s["dir"])
                if bot is None or bot.hash != h:
                    return f"{s['dir']} builds to {bot and bot.hash}, coordinator has {h}"
        return None

    def accept(self, task):
        reason = self.why_not(task)
        if reason and task["id"] not in self.refused:
            self.refused[task["id"]] = reason
            print(f"{dist.now_iso()} skip {task['id']}: {reason}", flush=True)
        return reason is None

    # ------------------------------------------------------------ playing
    def materialise(self, h, spec, slot: Path) -> arena.Bot:
        if spec["kind"] == "dir":
            return self.dirs[spec["dir"]]
        data = dist.apply_blob(self._base(spec["base"]), dist._unb64z(spec["blob"]))
        if hashlib.sha256(data[1]).hexdigest()[:16] != h:
            raise ValueError(f"patched wasm hash differs from the coordinator's {h}")
        for n, d in zip(dist.WASMS, data):
            (slot / ".cambc" / "build" / n).write_bytes(d)
        return arena.Bot(h, slot, h)

    def map_file(self, mh, m) -> Path:
        p = arena.ARENA / "distmaps" / mh / m["name"]
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_bytes(dist._unb64z(m["data"]))
            tmp.replace(p)
        return p

    def play_game(self, task, g, slots):
        try:
            a = self.materialise(g["a"], task["bots"][g["a"]], slots[0])
            b = a if g["b"] == g["a"] else self.materialise(g["b"], task["bots"][g["b"]], slots[1])
            mp = self.map_file(g["map"], task["maps"][g["map"]])
            r = arena.play(a, b, mp)
            for bot in (a, b):        # a rebuild during `cambc run` would have replaced the patched genome
                if arena.sha(Path(bot.path) / ".cambc" / "build" / dist.WASMS[1]) != bot.hash:
                    return {"key": g["key"], "error": f"worker {self.name}: {bot.path} was rebuilt during play"}
        except (OSError, ValueError, KeyError) as e:
            return {"key": g["key"], "error": f"worker {self.name}: {e}"}
        if r["key"] != g["key"]:
            return {"key": g["key"], "error": f"worker {self.name}: game key {r['key']} != {g['key']}"}
        return dist.strip(r)

    def serve(self, task, slots):
        t0 = time.time()
        res = {}
        for g in task["games"]:
            res[g["key"]] = self.play_game(task, g, slots)
            self.hb.add(games=1)
        dist.complete(self.root, self.name, task, res, time.time() - t0)
        self.hb.add(tasks=1)

    def may_claim(self):
        return not self.stopping.is_set() and (self.stop_claim_at is None or time.time() < self.stop_claim_at)

    def loop(self, i):
        (arena.ARENA / "tmp").mkdir(parents=True, exist_ok=True)
        while self.may_claim():
            task = dist.claim(self.root, self.name, self.accept)
            if task is None:
                if self.idle_exit and time.time() - self.last_work > self.idle_exit:
                    return
                self.stopping.wait(self.poll)
                continue
            with self.lock:
                self.current.add(task["id"])
            self.serve(task, self.slots[i])
            with self.lock:
                self.current.discard(task["id"])
                self.last_work = time.time()

    def run(self):
        self.setup()
        threads = [threading.Thread(target=self.loop, args=(i,), daemon=True) for i in range(self.jobs)]
        for th in threads:
            th.start()
        try:
            while any(th.is_alive() for th in threads):
                self.reaper.reap()
                with self.lock:
                    busy = len(self.current)
                self.hb.beat(state="busy" if busy else ("draining" if not self.may_claim() else "idle"),
                             claims=busy)
                for th in threads:
                    th.join(self.beat_every / len(threads))
        except KeyboardInterrupt:
            self.stopping.set()
            with self.lock:
                for tid in list(self.current):
                    dist.release(self.root, self.name, tid)
            print(f"{dist.now_iso()} interrupted: claims returned to the queue", flush=True)
        self.hb.beat(state="stopped", claims=0)
        info = self.hb.info
        print(f"{dist.now_iso()} worker {self.name} stopped: {info['games']} games, {info['tasks']} tasks", flush=True)


# ---------------------------------------------------------------- status
def status(root):
    root = dist.ensure(root)
    now = time.time()
    claims = {}
    for p in (root / "claimed").glob("*.json"):
        claims.setdefault(dist.parse_claim(p.name)[1], []).append(p)
    print(f"{'worker':24} {'host':16} {'state':9} {'seen':>7} {'jobs':>4} {'claims':>6} {'games':>7} {'games/h':>8}")
    total = 0.0
    for p in sorted((root / "workers").glob("*.json")):
        hb = None if dist.is_conflict(p.name) else dist.read_json(p)
        if not hb:
            continue
        age = now - hb.get("at_epoch", 0)
        if age > 86400 and hb["name"] not in claims:
            continue
        up = max(1.0, hb.get("at_epoch", now) - hb.get("started_epoch", hb.get("at_epoch", now)))
        rate = hb.get("games", 0) * 3600 / up if hb.get("started_epoch") else 0.0
        alive = age <= dist.LEASE and hb.get("state") != "stopped"
        total += rate if alive else 0.0
        print(f"{hb['name'][:24]:24} {hb.get('host', '')[:16]:16} {hb.get('state', '?') if alive else 'dead' if hb.get('state') != 'stopped' else 'stopped':9}"
              f" {age:6.0f}s {hb.get('jobs', 0):4} {len(claims.get(hb['name'], [])):6} {hb.get('games', 0):7}"
              f" {rate:8.0f}")
    q = [dist.read_json(p) for p in dist.queued(root)]
    qg = sum(len(t["games"]) for t in q if t)
    print(f"queue: {len(q)} tasks / {qg} games   claimed: {sum(len(v) for v in claims.values())} tasks   "
          f"results waiting: {len(list((root / 'results').glob('*.json')))}   live worker throughput ~{total:.0f} games/h")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", choices=["run", "status"], default="run")
    ap.add_argument("--root", default=str(arena.ARENA / "dist"))
    ap.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    ap.add_argument("--name", default=None, help="worker id (default: host + checkout hash)")
    ap.add_argument("--until", default=None, help="HH:MM local: stop claiming before it, finish, exit")
    ap.add_argument("--margin", type=float, default=120.0, help="seconds before --until to stop claiming")
    ap.add_argument("--adopt-base", action="store_true",
                    help="play tasks for a bots/neat build that differs from ours, using the coordinator's wasm")
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--idle-exit", type=float, default=0.0, help="exit after this many idle seconds (0 = never)")
    a = ap.parse_args()
    if a.cmd == "status":
        return status(a.root)
    Worker(a.root, a.name, a.jobs, a.until, a.margin, a.adopt_base, a.poll, a.idle_exit).run()


if __name__ == "__main__":
    sys.exit(main())
