"""Distributed game evaluation: a shared-folder task queue between one coordinator and any number of workers.

The coordinator is the trainer process. dist.play_many(games, jobs) is a drop-in for arena.play_many. It
splits the games it can ship into batches (tasks) in <root>/queue/, plays everything with `jobs` local
threads (local threads also take queued tasks, like any other worker) and collects what remote workers
return in <root>/results/. Workers (tools/dist_worker.py) claim a task by renaming it into claimed/,
rebuild each bot from the task (a genome blob patched into their own pre-built bots/neat slot copy, or a
repo bot directory they build themselves), check its wasm hash, play with arena.play and write the result.

Without a root (no --dist flag and no BC_DIST env var), play_many is arena.play_many: defaults unchanged.

Layout of <root> (docs/DIST_WORKERS.md has the details):
  queue/<id>.json            task waiting for a worker
  claimed/<id>@<worker>.json task a worker is playing (its lease lasts while the worker's heartbeat moves)
  results/<id>.json          finished task, read and deleted by its coordinator
  workers/<worker>.json      heartbeat: seq counter, state, games played, ...
  bases/<base>/              coordinator's bots/neat wasm with the genome block zeroed (for --adopt-base)

Games are keyed exactly like the arena cache, (cambc version, wasm A, wasm B, map hash), and the engine is
deterministic, so a task played twice (a reaped lease whose worker comes back, a race through Syncthing,
a coordinator restart) gives the same result twice; duplicates are harmless.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import arena

ENV = "BC_DIST"                       # root folder; set (or pass --dist) to switch distribution on
BATCH = int(os.environ.get("BC_DIST_BATCH", "4"))
LEASE = float(os.environ.get("BC_DIST_LEASE", "120"))
# Idle local threads replay a remote claim only after this long (and 2x the median task time). It must exceed
# one Syncthing round trip (task out + result back, ~20-40 s), or remote results arrive after the replay.
MIN_STEAL = float(os.environ.get("BC_DIST_MIN_STEAL", "90"))    # s without a heartbeat change -> worker dead
MAX_CLAIM = float(os.environ.get("BC_DIST_MAX_CLAIM", "3600"))   # s a claim may last whatever the heartbeat
MAGIC = struct.pack("<II", 0x4E454154, 0x424C4F42)
REGION = 16384 * 4                    # bytes of the genome block (bots/neat/src/genome.h GENOME_WORDS)
WASMS = ("bot.wasm", "bot-injected.wasm")
SUBDIRS = ("queue", "claimed", "results", "workers", "bases")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def ensure(root) -> Path:
    root = Path(root)
    for s in SUBDIRS:
        (root / s).mkdir(parents=True, exist_ok=True)
    return root


def rm(path: Path) -> bool:
    """Delete a queue file if possible. On Windows a file another thread or process has open cannot be
    deleted (PermissionError); every caller tolerates a leftover, which a later pass removes."""
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def write_json(path: Path, obj):
    """Atomic write (tmp + rename), so readers never see a half-written file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:      # Windows: the target is open in another process (a reader, Syncthing)
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _b64z(b: bytes) -> str:
    return base64.b64encode(zlib.compress(b, 9)).decode()


def _unb64z(s: str) -> bytes:
    return zlib.decompress(base64.b64decode(s))


# ---------------------------------------------------------------- bots as portable specs
def _region(data: bytes) -> int:
    i = data.find(MAGIC)
    return i if i >= 0 and data.find(MAGIC, i + 1) < 0 else -1


def zeroed(data: bytes, i: int) -> bytes:
    """The genome block emptied except for its magic words (so it can still be found and patched)."""
    return data[:i] + MAGIC + bytes(REGION - len(MAGIC)) + data[i + REGION:]


def base_hash(wasms) -> str:
    """Identity of a network-bot build regardless of its genome: sha256 of both wasm files, genome zeroed."""
    h = hashlib.sha256()
    for d in wasms:
        h.update(zeroed(d, _region(d)))
    return h.hexdigest()[:16]


_spec_cache: dict = {}
_spec_lock = threading.Lock()


def bot_spec(bot: arena.Bot):
    """What a worker needs to rebuild `bot` exactly, or None if it can only be played here.
    {"kind": "blob", "base": .., "blob": ..} for a patched bots/neat slot, {"kind": "dir", "dir": "bots/x"}
    for a repo bot directory; both carry the wasm hash the worker must reproduce."""
    if isinstance(bot, BlobBot):
        return {"kind": "blob", "base": bot.base, "blob": _b64z(bot.blob), "hash": bot.hash}
    ck = (str(bot.path), bot.hash)
    with _spec_lock:
        if ck in _spec_cache:
            return _spec_cache[ck]
    spec = None
    try:
        build = Path(bot.path) / ".cambc" / "build"
        data = [(build / n).read_bytes() for n in WASMS]
        if hashlib.sha256(data[1]).hexdigest()[:16] == bot.hash:
            offs = [_region(d) for d in data]
            if min(offs) >= 0 and data[0][offs[0]:offs[0] + REGION] == data[1][offs[1]:offs[1] + REGION]:
                blob = data[1][offs[1]:offs[1] + REGION].rstrip(b"\0")
                blob += bytes(-len(blob) % 4)
                spec = {"kind": "blob", "base": base_hash(data), "blob": _b64z(blob), "hash": bot.hash}
            else:
                rel = Path(bot.path).resolve().relative_to(arena.ROOT.resolve())
                if rel.parts[0] == "bots" and len(rel.parts) == 2:
                    spec = {"kind": "dir", "dir": rel.as_posix(), "hash": bot.hash}
    except (OSError, ValueError):
        spec = None
    with _spec_lock:
        _spec_cache[ck] = spec
    return spec


def publish_base(root: Path, bot: arena.Bot, base: str):
    """Coordinator's zeroed base wasm under bases/<base>/, for workers whose own build differs (--adopt-base)."""
    d = Path(root) / "bases" / base
    if (d / WASMS[1]).exists():
        return
    d.mkdir(parents=True, exist_ok=True)
    if isinstance(bot, BlobBot):
        with _bases_lock:
            pair = _bases[base]
    else:
        pair = [(Path(bot.path) / ".cambc" / "build" / n).read_bytes() for n in WASMS]
        pair = [zeroed(x, _region(x)) for x in pair]
    for n, z in zip(WASMS, pair):
        tmp = d / f".{n}.{os.getpid()}.tmp"
        tmp.write_bytes(z)
        os.replace(tmp, d / n)


def apply_blob(base_wasms, blob: bytes):
    """Base wasm pair (genome zeroed) + blob -> the patched wasm pair (same bytes neat_train.patch writes)."""
    if not blob.startswith(MAGIC) or len(blob) > REGION:
        raise ValueError("not a genome blob")
    out = []
    for d in base_wasms:
        i = _region(d)
        if i < 0:
            raise ValueError("base wasm has no genome block")
        out.append(d[:i] + blob + bytes(REGION - len(blob)) + d[i + REGION:])
    return out


# ---------------------------------------------------------------- virtual network bots
_bases: dict = {}          # base hash -> (bot.wasm, bot-injected.wasm) with the genome block zeroed
_bases_lock = threading.Lock()


@dataclass(frozen=True)
class BlobBot:
    """A bots/neat build that exists only as base + genome blob (no directory of its own). Used where a
    generation has more candidates than slots: tasks carry the blob, and whoever plays the game (a remote
    worker or a coordinator thread) patches it into one of its own slots first. Same `name`/`hash` fields
    as arena.Bot, so game keys and the arena cache are unchanged."""
    name: str
    hash: str
    base: str
    blob: bytes
    path: Path | None = None


def base_of(slot: Path):
    """(base hash, zeroed wasm pair) of a built bots/neat slot; the pair is registered for BlobBots."""
    data = [(Path(slot) / ".cambc" / "build" / n).read_bytes() for n in WASMS]
    z = tuple(zeroed(d, _region(d)) for d in data)
    b = base_hash(data)
    with _bases_lock:
        _bases.setdefault(b, z)
    return b, z


def virtual_bot(ref_slot: Path, blob: bytes, name: str) -> BlobBot:
    """The bot neat_train.patch(ref_slot, blob) would produce, without touching ref_slot."""
    b, z = base_of(ref_slot)
    blob = blob.rstrip(bytes(1))
    blob += bytes(-len(blob) % 4)
    data = apply_blob(z, blob)
    return BlobBot(name, hashlib.sha256(data[1]).hexdigest()[:16], b, blob)


class SlotPlayer:
    """Plays games whose bots may be BlobBots on this machine, giving each thread its own pair of built
    bots/neat slots. A BlobBot is materialised by rewriting only the genome block in place (64 KB, not the
    3.3 MB files), and not at all when the slot already holds that bot."""

    def __init__(self, slots):
        slots = [Path(x) for x in slots]
        self.pairs = [slots[2 * i:2 * i + 2] for i in range(len(slots) // 2)]
        if not self.pairs:
            raise ValueError("SlotPlayer needs at least two slots")
        self.held = {}          # slot -> hash currently written there
        self.offs = {}          # slot -> (base hash, [genome offset per wasm])

    @property
    def jobs(self):
        return len(self.pairs)

    def materialise(self, bot, slot: Path):
        if not isinstance(bot, BlobBot):
            return bot
        if self.held.get(slot) != bot.hash:
            build = slot / ".cambc" / "build"
            if slot not in self.offs or self.offs[slot][0] != bot.base:
                b, _ = base_of(slot)
                if b != bot.base:      # a slot of another build: write the whole adopted base once
                    with _bases_lock:
                        z = _bases[bot.base]
                    for n, d in zip(WASMS, apply_blob(z, bot.blob)):
                        (build / n).write_bytes(d)
                self.offs[slot] = (bot.base, [_region((build / n).read_bytes()) for n in WASMS])
            block = bot.blob + bytes(REGION - len(bot.blob))
            for n, off in zip(WASMS, self.offs[slot][1]):
                with open(build / n, "r+b") as fh:
                    fh.seek(off)
                    fh.write(block)
            self.held[slot] = bot.hash
        return arena.Bot(bot.name, slot, bot.hash)

    def play(self, a, b, m, i):
        """Play one game on pair i (each thread must use its own i)."""
        pa, pb = self.pairs[i]
        ra = self.materialise(a, pa)
        rb = ra if (isinstance(b, BlobBot) and b.hash == a.hash) else self.materialise(b, pb)
        r = arena.play(ra, rb, m)
        if isinstance(a, BlobBot) or isinstance(b, BlobBot):
            for bot in (ra, rb):
                if arena.sha(Path(bot.path) / ".cambc" / "build" / WASMS[1]) != bot.hash:
                    self.held.pop(Path(bot.path), None)      # `cambc run` rebuilt it: the genome is gone
                    return {"key": r.get("key"), "map": Path(m).stem,
                            "error": f"{bot.path} was rebuilt during play (genome lost)"}
        return r


def play_slots(games, jobs, slots, root=None, progress=False):
    """Play (a, b, map) games where a/b may be BlobBots, using `slots` (built bots/neat dirs, two per local
    thread). Distributed through `root` (or env BC_DIST) when set. Results in the order of `games`, shaped
    like arena.play_many's."""
    player = SlotPlayer(slots)
    jobs = max(1, min(jobs, player.jobs))
    root = root or os.environ.get(ENV)
    if root:
        return coordinator(root).play_many(games, jobs, progress, player=player)
    import concurrent.futures as cf
    games = [(a, b, arena.resolve_map(m) if not isinstance(m, Path) else m) for a, b, m in games]
    out = [None] * len(games)
    order = iter(range(len(games)))
    lock = threading.Lock()

    def run(i):
        while True:
            with lock:
                k = next(order, None)
            if k is None:
                return
            a, b, m = games[k]
            hit = cached(game_key(a, b, m))
            r = {**hit, "cached": True} if hit else player.play(a, b, m, i)
            out[k] = {**r, "a": a.name, "b": b.name}
    with cf.ThreadPoolExecutor(jobs) as ex:
        list(ex.map(run, range(jobs)))
    return out


# ---------------------------------------------------------------- queue primitives
def task_id(coord: str, keys) -> str:
    return hashlib.sha256((coord + "\n" + "\n".join(sorted(keys))).encode()).hexdigest()[:20]


def claim_name(tid: str, worker: str) -> str:
    return f"{tid}@{worker}.json"


CONFLICT = ".sync-conflict-"   # Syncthing's name for the losing copy: <stem>.sync-conflict-<date>-<time>-<dev>.<ext>


def is_conflict(name: str) -> bool:
    return CONFLICT in name


def parse_claim(name: str):
    """claimed/<id>@<worker>.json -> (id, worker). A Syncthing conflict copy of a claim belongs to the same
    worker (otherwise it would look like a dead worker and be requeued after a lease)."""
    stem = name[:-5] if name.endswith(".json") else name
    stem = stem.split(CONFLICT)[0]
    tid, _, worker = stem.partition("@")
    return tid, worker


def exists_anywhere(root: Path, tid: str) -> bool:
    return ((root / "queue" / f"{tid}.json").exists() or (root / "results" / f"{tid}.json").exists()
            or any((root / "claimed").glob(f"{tid}@*.json")))


def submit(root: Path, task: dict) -> bool:
    """Queue a task unless the same id is already queued, claimed or finished. Returns True if written."""
    if exists_anywhere(root, task["id"]):
        return False
    write_json(root / "queue" / f"{task['id']}.json", task)
    return True


def queued(root: Path):
    """Queue files, oldest first."""
    out = []
    for p in (root / "queue").glob("*.json"):
        try:
            out.append((p.stat().st_mtime, p.name, p))
        except OSError:
            pass
    return [p for *_, p in sorted(out)]


LOCK_STALE_SECS = 30   # a lock with no claim file this old is a leftover of a crash between lock and rename


def _lock(root: Path, tid: str) -> Path:
    d = root / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{tid}.lock"


def unlock(root: Path, tid: str):
    rm((root / "locks" / f"{tid}.lock"))


def claim(root: Path, worker: str, accept=lambda task: True):
    """Atomically take the oldest acceptable queued task.

    Exclusivity comes from creating locks/<id>.lock with O_CREAT|O_EXCL. A plain rename is not enough:
    on Windows two concurrent renames of the same file can both report success (seen in about 45% of
    8-thread trials on NTFS), which handed one task to several workers. The winner then renames
    queue/<id>.json -> claimed/<id>@<worker>.json; the lock lives until the claim ends (complete,
    release or reap). Returns the task dict or None."""
    for p in queued(root):
        task = read_json(p)
        if task is None or not accept(task):
            continue
        lock = _lock(root, task["id"])
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:   # leftover lock from a crash between lock and rename: clear it, retry on a later pass
                if (not any((root / "claimed").glob(f"{task['id']}@*.json"))
                        and time.time() - lock.stat().st_mtime > LOCK_STALE_SECS):
                    rm(lock)
            except OSError:
                pass
            continue
        except OSError:
            continue
        os.write(fd, worker.encode()); os.close(fd)
        dest = root / "claimed" / claim_name(task["id"], worker)
        try:
            os.rename(p, dest)
        except OSError:            # tidied away or finished meanwhile
            rm(lock)
            continue
        return task
    return None


def complete(root: Path, worker: str, task: dict, results: dict, secs: float):
    """Write results/<id>.json (atomic), then drop the claim. Safe to call twice or after a reap."""
    write_json(root / "results" / f"{task['id']}.json",
               {"id": task["id"], "coord": task.get("coord"), "worker": worker, "secs": round(secs, 2),
                "finished": now_iso(), "results": results})
    rm((root / "claimed" / claim_name(task["id"], worker)))
    rm((root / "queue" / f"{task['id']}.json"))   # requeued meanwhile: no longer needed
    unlock(root, task["id"])


def release(root: Path, worker: str, tid: str):
    """Give an unfinished claim back to the queue (clean shutdown)."""
    src = root / "claimed" / claim_name(tid, worker)
    try:
        if (root / "results" / f"{tid}.json").exists() or (root / "queue" / f"{tid}.json").exists():
            rm(src)
        else:
            os.rename(src, root / "queue" / f"{tid}.json")
    except OSError:
        pass
    unlock(root, tid)


class Heartbeat:
    """workers/<name>.json. The lease of every claim by <name> lasts while `seq` keeps changing."""

    def __init__(self, root: Path, name: str, **info):
        self.path = Path(root) / "workers" / f"{name}.json"
        self.info = {"name": name, "host": socket.gethostname(), "pid": os.getpid(), "started": now_iso(),
                     "started_epoch": time.time(), "seq": 0, "games": 0, "tasks": 0, "state": "starting", **info}
        self.lock = threading.Lock()

    def beat(self, **kw):
        with self.lock:
            self.info.update(kw)
            self.info["seq"] += 1
            self.info["at"] = now_iso()
            self.info["at_epoch"] = time.time()
            write_json(self.path, self.info)

    def add(self, games=0, tasks=0):
        with self.lock:
            self.info["games"] += games
            self.info["tasks"] += tasks


class Reaper:
    """Returns stale claims to the queue. A claim is stale when its worker's heartbeat seq has not changed
    for `lease` seconds of *this process's* clock (no cross-machine clock comparison), when the worker
    says it stopped, or when the claim has been held longer than `max_claim`. Any process may reap; a
    duplicate play of a reaped task is harmless."""

    def __init__(self, root: Path, lease=LEASE, max_claim=MAX_CLAIM, clock=time.monotonic):
        self.root, self.lease, self.max_claim, self.clock = Path(root), lease, max_claim, clock
        self.seen = {}        # worker -> (seq, first time this seq was seen)
        self.claims = {}      # claim file name -> first time seen

    def alive(self, worker: str) -> bool:
        hb = read_json(self.root / "workers" / f"{worker}.json")
        t = self.clock()
        if hb is None:
            first = self.seen.setdefault(worker, (None, t))
            return t - first[1] <= self.lease
        if hb.get("state") == "stopped":
            return False
        seq = hb.get("seq")
        if self.seen.get(worker, (object(),))[0] != seq:
            self.seen[worker] = (seq, t)
        return t - self.seen[worker][1] <= self.lease

    def reap(self):
        t = self.clock()
        out, names = [], set()
        for p in (self.root / "claimed").glob("*.json"):
            names.add(p.name)
            first = self.claims.setdefault(p.name, t)
            tid, worker = parse_claim(p.name)
            if (self.root / "results" / f"{tid}.json").exists():
                rm(p)         # finished; the worker died before dropping its claim
                unlock(self.root, tid)
                continue
            if self.alive(worker) and t - first <= self.max_claim:
                continue
            try:
                if (self.root / "queue" / f"{tid}.json").exists():
                    p.unlink()
                else:
                    os.rename(p, self.root / "queue" / f"{tid}.json")
                unlock(self.root, tid)
                out.append(tid)
            except OSError:
                pass
        self.claims = {k: v for k, v in self.claims.items() if k in names}
        # Conflict copies of heartbeats and locks are never authoritative (queue/claimed/results copies
        # are handled above or by the coordinator): delete them so they do not pile up.
        for sub in ("workers", "locks"):
            for p in (self.root / sub).glob(f"*{CONFLICT}*"):
                rm(p)
        return out


def live_workers(root: Path, lease=LEASE):
    """Heartbeats younger than `lease` by wall clock (for status/fallback decisions only, never for reaping)."""
    out = []
    for p in sorted((Path(root) / "workers").glob("*.json")):
        hb = None if is_conflict(p.name) else read_json(p)
        if hb and hb.get("state") != "stopped" and time.time() - hb.get("at_epoch", 0) <= lease:
            out.append(hb)
    return out


# ---------------------------------------------------------------- arena cache access
def game_key(a: arena.Bot, b: arena.Bot, m: Path) -> str:
    return f"{arena.cambc_version()}|{a.hash}|{b.hash}|{arena._map_hash(m)}"


def cached(key: str):
    with arena._cache_lock:
        return arena._load_cache().get(key)


def cache_put(res: dict):
    """Store a result in this machine's arena cache (same format arena.play writes)."""
    if "error" in res:
        return
    with arena._cache_lock:
        c = arena._load_cache()
        if res["key"] in c:
            return
        c[res["key"]] = res
        arena.CACHE.parent.mkdir(parents=True, exist_ok=True)
        with arena.CACHE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(res) + "\n")


def strip(res: dict) -> dict:
    return {k: v for k, v in res.items() if k not in ("a", "b", "cached")}


# ---------------------------------------------------------------- coordinator
class Coordinator:
    """One per trainer process. `name` must be unique among coordinators sharing a root (default: host)."""

    def __init__(self, root, name=None, batch=BATCH, lease=LEASE, poll=0.5, min_steal=MIN_STEAL):
        self.root = ensure(root)
        self.name = name or os.environ.get("BC_DIST_NAME") or socket.gethostname().lower()
        self.worker = f"{self.name}-local"
        self.batch, self.poll, self.min_steal = batch, poll, min_steal
        self.reaper = Reaper(self.root, lease=lease)
        self.hb = Heartbeat(self.root, self.worker, role="coordinator", cambc=arena.cambc_version())
        self.task_secs = []     # durations of finished tasks (remote and local), for the steal threshold
        self.reset_stats()

    def reset_stats(self):
        """Counters since the last take_stats() (one trainer generation): distinct games by where their result
        came from, local replays of slow remote claims, duplicate remote results, games per remote worker,
        and the remote workers seen alive."""
        self.stats = {"games": 0, "cached": 0, "local": 0, "remote": 0, "stolen": 0, "dup": 0, "secs": 0.0,
                      "by_worker": {}, "workers": set()}

    # results/ -> arena cache. Returns {key: result} of every task of ours that came back.
    def ingest(self):
        got = {}
        for p in (self.root / "results").glob("*.json"):
            r = read_json(p)
            if r is None or r.get("coord") != self.name:
                continue
            if r.get("secs") is not None:
                self.task_secs = (self.task_secs + [float(r["secs"])])[-200:]
            remote = r.get("worker") != self.worker
            for key, res in r.get("results", {}).items():
                got[key] = res
                if remote:
                    known = "error" not in res and cached(key) is not None
                    self.stats["dup"] += known
                    w = r.get("worker", "?")
                    self.stats["by_worker"][w] = self.stats["by_worker"].get(w, 0) + 1
                cache_put(res)
            rm(p)
        return got

    def tidy(self):
        """Drop queued tasks of ours whose games are all cached (left over from a crash or an earlier call)."""
        for p in queued(self.root):
            t = read_json(p)
            if t and t.get("coord") == self.name and all(cached(g["key"]) for g in t["games"]):
                rm(p)

    def make_tasks(self, games, keys):
        """Batches of the uncached, shippable games (one entry per distinct key). Returns [task]."""
        version = arena.cambc_version()
        seen, todo = set(), []
        for (a, b, m), k in zip(games, keys):
            if k in seen or cached(k):
                continue
            sa, sb = bot_spec(a), bot_spec(b)
            if sa is None or sb is None:
                continue
            seen.add(k)
            todo.append((a, b, m, k, sa, sb))
        tasks = []
        for i in range(0, len(todo), self.batch):
            chunk = todo[i:i + self.batch]
            t = {"id": task_id(self.name, [c[3] for c in chunk]), "coord": self.name, "cambc": version,
                 "created": now_iso(), "bots": {}, "maps": {}, "games": []}
            for a, b, m, k, sa, sb in chunk:
                for bot, s in ((a, sa), (b, sb)):
                    t["bots"][bot.hash] = s
                    if s["kind"] == "blob":
                        publish_base(self.root, bot, s["base"])
                mh = arena._map_hash(m)
                t["maps"][mh] = {"name": m.name, "data": _b64z(m.read_bytes())}
                t["games"].append({"key": k, "a": a.hash, "b": b.hash, "map": mh})
            tasks.append(t)
        return tasks

    def play_many(self, games, jobs=arena.DEFAULT_JOBS, progress=False, player=None):
        """`player` (a SlotPlayer) plays the local share when games contain BlobBots: local thread i uses
        its pair i, so jobs is capped at player.jobs."""
        if player is not None:
            jobs = max(1, min(jobs, player.jobs))
        (arena.ARENA / "tmp").mkdir(parents=True, exist_ok=True)
        games = [(a, b, arena.resolve_map(m) if not isinstance(m, Path) else m) for a, b, m in games]
        keys = [game_key(*g) for g in games]
        by_key = {}
        for g, k in zip(games, keys):
            by_key.setdefault(k, g)
        self.ingest()
        self.tidy()
        tasks = self.make_tasks(games, keys)
        mine = {t["id"]: t for t in tasks}
        for t in tasks:
            submit(self.root, t)
        shipped = {g["key"] for t in tasks for g in t["games"]}
        lock = threading.Lock()
        done = {}                                    # key -> result (without a/b names)
        src = {}                                     # key -> "local" | "remote" | "cached"
        for k in by_key:
            if cached(k):
                done[k], src[k] = {**cached(k), "cached": True}, "cached"
        local = [k for k in by_key if k not in shipped and k not in done]
        busy, stop, failures = set(), threading.Event(), []

        def play_local(k, i, stolen=False):
            with lock:
                if k in done or k in busy:
                    return
                busy.add(k)
            try:
                r = player.play(*by_key[k], i) if player is not None else arena.play(*by_key[k])
            except BaseException as e:      # surface it in the caller, like arena.play_many would
                with lock:
                    busy.discard(k)
                    failures.append(e)
                stop.set()
                return
            with lock:
                busy.discard(k)
                if k not in done or "error" in done[k]:
                    done[k] = r
                    src[k] = "cached" if r.get("cached") else "local"
                    self.stats["stolen"] += stolen and not r.get("cached")
            if not r.get("cached"):
                self.hb.add(games=1)

        def accept(t):
            return t["id"] in mine

        def worker_loop(i):
            while not stop.is_set():
                with lock:
                    k = local.pop() if local else None
                if k is not None:
                    play_local(k, i)
                    continue
                t = claim(self.root, self.worker, accept)
                if t is None:
                    t = self._steal(mine, done, busy, lock)    # tail: replay a task a remote holds
                if t is None:
                    stop.wait(self.poll)
                    continue
                t0 = time.time()
                for g in t["games"]:
                    play_local(g["key"], i, stolen=bool(t.get("stolen")))
                with lock:
                    res = {g["key"]: strip(done[g["key"]]) for g in t["games"] if g["key"] in done}
                if len(res) == len(t["games"]) and (self.root / "claimed" / claim_name(t["id"], self.worker)).exists():
                    complete(self.root, self.worker, t, res, time.time() - t0)
                    with lock:
                        self.task_secs = (self.task_secs + [time.time() - t0])[-200:]
                self.hb.add(tasks=1)

        self.hb.beat(state="busy", jobs=jobs)
        threads = [threading.Thread(target=worker_loop, args=(i,), daemon=True) for i in range(max(1, jobs))]
        for th in threads:
            th.start()
        retried, last, t_start = set(), -1, time.time()
        try:
            while True:
                for k, r in self.ingest().items():
                    if k not in by_key:
                        continue
                    with lock:
                        if "error" in r and k not in retried:      # a worker-side failure: replay here once
                            retried.add(k)
                            local.append(k)
                        elif k not in done:
                            done[k], src[k] = r, "remote"
                self.reaper.reap()
                self.hb.beat()
                self.stats["workers"].update(h["name"] for h in live_workers(self.root)
                                             if h["name"] != self.worker and h.get("role") != "coordinator")
                with lock:
                    n = sum(k in done for k in by_key)
                    pending = any(k not in done for k in by_key) or busy
                if progress and n != last and (n % 25 == 0 or n == len(by_key)):
                    print(f"  {n}/{len(by_key)} distinct games", flush=True)
                    last = n
                if failures:
                    raise failures[0]
                if not pending:
                    break
                time.sleep(self.poll)
        finally:
            stop.set()
            for th in threads:      # local threads use the caller's slot bots: never leave one running
                th.join()
            for tid in mine:
                for p in (self.root / "claimed").glob(f"{tid}@{self.worker}.json"):
                    release(self.root, self.worker, tid)
            # Final sweep: results our own threads wrote after the loop's last ingest (seen on Windows,
            # where play_many returned with its local tasks' results/<id>.json still on disk).
            self.ingest()
            self.hb.beat(state="idle")
        self.last = {s: sum(v == s for v in src.values()) for s in ("cached", "local", "remote")}
        for s_, n_ in self.last.items():
            self.stats[s_] += n_
        self.stats["games"] += len(by_key)
        self.stats["secs"] += time.time() - t_start
        out = []
        for (a, b, m), k in zip(games, keys):
            r = done[k]
            out.append({**strip(r), "a": a.name, "b": b.name, "cached": r.get("cached", False)})
        return out

    def steal_after(self):
        """Seconds a remote claim must be held before idle local threads replay it: 2x the median task time
        seen so far (at least min_steal), 30 s before any task has finished."""
        v = sorted(self.task_secs)
        return max(self.min_steal, 2 * v[len(v) // 2] if v else 30.0)

    def _steal(self, mine, done, busy, lock):
        """When nothing is queued, replay a task of this call that a remote worker has held for longer than
        steal_after(): a slow or vanished worker cannot stretch the tail of a generation much beyond local
        speed. Every game is checked again before it is played; the first result in wins (they are equal)."""
        t_now, wait = self.reaper.clock(), self.steal_after()
        for p in sorted((self.root / "claimed").glob("*.json")):
            tid, worker = parse_claim(p.name)
            first = self.reaper.claims.get(p.name)
            if tid in mine and worker != self.worker and first is not None and t_now - first > wait:
                t = mine[tid]
                with lock:
                    if any(g["key"] not in done and g["key"] not in busy for g in t["games"]):
                        return {**t, "stolen": True}
        return None


_coords: dict = {}


def coordinator(root) -> Coordinator:
    root = str(Path(root).resolve())
    if root not in _coords:
        _coords[root] = Coordinator(root)
    return _coords[root]


def play_many(games, jobs=arena.DEFAULT_JOBS, progress=False, root=None):
    """arena.play_many, distributed when `root` (or env BC_DIST) names a dist folder."""
    root = root or os.environ.get(ENV)
    if not root:
        return arena.play_many(games, jobs, progress)
    return coordinator(root).play_many(games, jobs, progress)


def take_stats(root=None):
    """Distribution counters of every coordinator in this process since the last call (then reset), as a
    JSON-ready dict for a trainer's per-generation log line; None when distribution is off."""
    root = root or os.environ.get(ENV)
    if not root:
        return None
    c = coordinator(root)
    st = dict(c.stats, secs=round(c.stats["secs"], 1), workers=sorted(c.stats["workers"]), t=round(time.time()))
    c.reset_stats()
    return st


def add_arg(ap):
    ap.add_argument("--dist", default=os.environ.get(ENV, ""),
                    help="distribute games through this shared folder (tools/dist_worker.py; default: env BC_DIST, "
                         "else local play)")
