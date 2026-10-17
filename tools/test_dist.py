"""Unit tests for tools/dist.py and tools/dist_worker.py with fake games (no engine needed).

  .venv/Scripts/python -m unittest tools/test_dist.py -v
"""
import hashlib
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import dist  # noqa: E402
import dist_worker  # noqa: E402


def fake_result(key):
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return {"key": key, "map": "m", "outcome": "teamA" if h % 2 else "teamB", "round": 100 + h % 700,
            "gems": [h % 97, h % 89], "faults": [0, 0], "drafts": [[], []], "teams": [{}, {}]}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def task(tid, coord="c", keys=("k1",)):
    return {"id": tid, "coord": coord, "cambc": "v", "bots": {}, "maps": {},
            "games": [{"key": k, "a": "x", "b": "y", "map": "m"} for k in keys]}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = dist.ensure(self.tmp / "dist")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def files(self, sub):
        return sorted(p.name for p in (self.root / sub).glob("*.json"))

    def test_claim_is_exclusive_under_races(self):
        for i in range(40):
            dist.submit(self.root, task(f"t{i:02d}"))
        got, lock = [], threading.Lock()

        def grab(w):
            while True:
                t = dist.claim(self.root, w)
                if t is None:
                    return
                with lock:
                    got.append(t["id"])
        ths = [threading.Thread(target=grab, args=(f"w{j}",)) for j in range(8)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        self.assertEqual(sorted(got), [f"t{i:02d}" for i in range(40)])
        self.assertEqual(self.files("queue"), [])
        self.assertEqual(len(self.files("claimed")), 40)

    def test_submit_is_idempotent(self):
        self.assertTrue(dist.submit(self.root, task("a")))
        self.assertFalse(dist.submit(self.root, task("a")))
        t = dist.claim(self.root, "w")
        self.assertFalse(dist.submit(self.root, task("a")))       # claimed
        dist.complete(self.root, "w", t, {"k1": fake_result("k1")}, 1.0)
        self.assertFalse(dist.submit(self.root, task("a")))       # finished
        self.assertEqual(self.files("claimed"), [])
        self.assertEqual(self.files("results"), ["a.json"])

    def test_accept_filter_leaves_task_queued(self):
        dist.submit(self.root, task("a"))
        self.assertIsNone(dist.claim(self.root, "w", accept=lambda t: False))
        self.assertEqual(self.files("queue"), ["a.json"])

    def test_worker_killed_mid_task_is_requeued_after_lease(self):
        clock = Clock()
        reaper = dist.Reaper(self.root, lease=60, max_claim=10_000, clock=clock)
        hb = dist.Heartbeat(self.root, "w1")
        hb.beat(state="busy")
        dist.submit(self.root, task("a"))
        self.assertEqual(dist.claim(self.root, "w1")["id"], "a")
        self.assertEqual(reaper.reap(), [])
        for t in (20, 40, 59):            # heartbeat moving: lease holds
            clock.t = t
            hb.beat()
            self.assertEqual(reaper.reap(), [])
        # w1 is killed: no more beats. Still leased until 60 s after the last change was seen.
        clock.t = 100
        self.assertEqual(reaper.reap(), [])
        clock.t = 120
        self.assertEqual(reaper.reap(), ["a"])
        self.assertEqual(self.files("queue"), ["a.json"])
        self.assertEqual(self.files("claimed"), [])
        t = dist.claim(self.root, "w2")
        dist.complete(self.root, "w2", t, {"k1": fake_result("k1")}, 1.0)
        self.assertEqual(self.files("results"), ["a.json"])

    def test_claim_without_any_heartbeat_expires(self):
        clock = Clock()
        reaper = dist.Reaper(self.root, lease=60, clock=clock)
        dist.submit(self.root, task("a"))
        dist.claim(self.root, "ghost")
        self.assertEqual(reaper.reap(), [])
        clock.t = 61
        self.assertEqual(reaper.reap(), ["a"])

    def test_stopped_worker_claims_reaped_at_once(self):
        reaper = dist.Reaper(self.root, lease=60, clock=Clock())
        hb = dist.Heartbeat(self.root, "w1")
        dist.submit(self.root, task("a"))
        dist.claim(self.root, "w1")
        hb.beat(state="stopped")
        self.assertEqual(reaper.reap(), ["a"])

    def test_max_claim_age_reaps_even_a_live_worker(self):
        clock = Clock()
        reaper = dist.Reaper(self.root, lease=60, max_claim=300, clock=clock)
        hb = dist.Heartbeat(self.root, "w1")
        dist.submit(self.root, task("a"))
        dist.claim(self.root, "w1")
        for t in range(0, 301, 30):
            clock.t = t
            hb.beat()
            self.assertEqual(reaper.reap(), [])
        clock.t = 330
        hb.beat()
        self.assertEqual(reaper.reap(), ["a"])

    def test_claim_with_result_is_cleaned_not_requeued(self):
        reaper = dist.Reaper(self.root, lease=60, clock=Clock())
        dist.submit(self.root, task("a"))
        t = dist.claim(self.root, "w1")
        dist.write_json(self.root / "results" / "a.json", {"id": "a", "coord": "c", "results": {}})
        # the worker died between writing the result and dropping its claim
        self.assertTrue((self.root / "claimed" / "a@w1.json").exists())
        self.assertEqual(reaper.reap(), [])
        self.assertEqual(self.files("claimed"), [])
        self.assertEqual(self.files("queue"), [])
        self.assertIsNotNone(t)

    def test_late_finish_after_reap_is_harmless(self):
        clock = Clock()
        reaper = dist.Reaper(self.root, lease=10, clock=clock)
        dist.submit(self.root, task("a"))
        t1 = dist.claim(self.root, "slow")
        clock.t = 11
        reaper.reap()                    # first sight of the claim: lease starts now
        clock.t = 30
        self.assertEqual(reaper.reap(), ["a"])
        t2 = dist.claim(self.root, "fast")
        dist.complete(self.root, "slow", t1, {"k1": fake_result("k1")}, 1.0)   # the "dead" worker comes back
        dist.complete(self.root, "fast", t2, {"k1": fake_result("k1")}, 1.0)
        self.assertEqual(self.files("claimed"), [])
        self.assertEqual(self.files("queue"), [])
        self.assertEqual(dist.read_json(self.root / "results" / "a.json")["results"]["k1"], fake_result("k1"))

    def test_release_returns_claim(self):
        dist.submit(self.root, task("a"))
        dist.claim(self.root, "w1")
        dist.release(self.root, "w1", "a")
        self.assertEqual(self.files("queue"), ["a.json"])
        self.assertEqual(self.files("claimed"), [])

    def test_deadline(self):
        now = datetime(2026, 10, 16, 23, 50)
        self.assertEqual(datetime.fromtimestamp(dist_worker.deadline("12:00", now)), datetime(2026, 10, 17, 12, 0))
        now = datetime(2026, 10, 17, 0, 5)
        self.assertEqual(datetime.fromtimestamp(dist_worker.deadline("12:00", now)), datetime(2026, 10, 17, 12, 0))


class FakeEngine(unittest.TestCase):
    """Coordinator with a fake engine: arena.play, the cache and the bot specs are replaced."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "dist"
        self.played = []
        arena_dir = self.tmp / "arena"
        (arena_dir / "tmp").mkdir(parents=True)
        self.patches = [
            mock.patch.object(arena, "ARENA", arena_dir),
            mock.patch.object(arena, "CACHE", arena_dir / "results-test.jsonl"),
            mock.patch.object(arena, "_cache", None),
            mock.patch.object(arena, "cambc_version", lambda: "cambc test"),
            mock.patch.object(arena, "play", self.fake_play),
            mock.patch.object(dist, "bot_spec", lambda b: None if b.name == "local-only" else
                              {"kind": "blob", "base": "B", "blob": "", "hash": b.hash}),
            mock.patch.object(dist, "publish_base", lambda *a: None),
        ]
        for p in self.patches:
            p.start()
        maps = []
        for i in range(3):
            m = self.tmp / f"map{i}.map26a"
            m.write_bytes(bytes([i]) * 50)
            maps.append(m)
        bots = [arena.Bot(f"b{i}", self.tmp / f"b{i}", f"{i:016x}") for i in range(4)]
        bots.append(arena.Bot("local-only", self.tmp / "lo", "f" * 16))
        self.games = [(x, y, m) for x in bots for y in bots[:3] if x is not y for m in maps]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        dist._coords.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_play(self, a, b, m, keep_replay=None):
        key = dist.game_key(a, b, m)
        hit = dist.cached(key)
        if hit:
            return {**hit, "a": a.name, "b": b.name, "cached": True}
        self.played.append(key)
        time.sleep(0.002)
        r = fake_result(key)
        dist.cache_put(r)
        return {**r, "a": a.name, "b": b.name, "cached": False}

    def local(self):
        # reference run with a throwaway cache (arena reads every results*.jsonl, so keep it out of ARENA)
        with mock.patch.object(arena, "_cache", {}), mock.patch.object(arena, "CACHE", self.tmp / "ref.jsonl"):
            out = arena.play_many(self.games, 4)
        self.played.clear()
        return out

    def remote_worker(self, name, stop, die_after=None, hb_every=0.05):
        """A fake remote worker: claims tasks, 'plays' them, beats; with die_after it vanishes mid-task."""
        hb = dist.Heartbeat(self.root, name)
        n = 0
        while not stop.is_set():
            hb.beat(state="busy")
            t = dist.claim(self.root, name)
            if t is None:
                time.sleep(hb_every)
                continue
            n += 1
            if die_after is not None and n > die_after:
                return                    # killed: claim stays, heartbeat stops
            time.sleep(0.02)
            dist.complete(self.root, name, t, {g["key"]: fake_result(g["key"]) for g in t["games"]}, 0.02)

    @staticmethod
    def norm(rs):
        return [{k: v for k, v in r.items() if k != "cached"} for r in rs]

    def test_zero_remote_workers_matches_local(self):
        want = self.local()
        got = dist.play_many(self.games, 4, root=self.root)
        self.assertEqual(self.norm(got), self.norm(want))
        self.assertEqual(list((self.root / "queue").glob("*.json")), [])
        self.assertEqual(list((self.root / "claimed").glob("*.json")), [])

    def test_unset_root_is_plain_arena(self):
        with mock.patch.object(arena, "play_many", lambda g, j, p=False: "plain"):
            self.assertEqual(dist.play_many(self.games, 4, root=""), "plain")

    def test_remote_workers_match_local(self):
        want = self.local()
        stop = threading.Event()
        ths = [threading.Thread(target=self.remote_worker, args=(f"r{i}", stop)) for i in range(2)]
        dist.ensure(self.root)
        for th in ths:
            th.start()
        try:
            got = dist.play_many(self.games, 1, root=self.root)
        finally:
            stop.set()
            for th in ths:
                th.join()
        self.assertEqual(self.norm(got), self.norm(want))
        self.assertLess(len(self.played), len(self.games))       # remote workers did some of the games

    def test_remote_worker_killed_mid_task(self):
        want = self.local()
        dist.ensure(self.root)
        stop = threading.Event()
        th = threading.Thread(target=self.remote_worker, args=("dies", stop, 1))
        th.start()
        c = dist.Coordinator(self.root, lease=0.3, poll=0.05)
        got = c.play_many(self.games, 2)
        stop.set()
        th.join()
        self.assertEqual(self.norm(got), self.norm(want))
        self.assertEqual(list((self.root / "claimed").glob("*.json")), [])

    def test_coordinator_restart_resumes(self):
        want = self.local()
        dist.ensure(self.root)
        # a first coordinator queued the tasks and "crashed"; one task was finished by a worker meanwhile
        c1 = dist.Coordinator(self.root)
        keys = [dist.game_key(*g) for g in self.games]
        tasks = c1.make_tasks(self.games, keys)
        for t in tasks:
            dist.submit(self.root, t)
        t = dist.claim(self.root, "r0")
        dist.complete(self.root, "r0", t, {g["key"]: fake_result(g["key"]) for g in t["games"]}, 0.0)
        c2 = dist.Coordinator(self.root)           # restart: same name, fresh process state
        got = c2.play_many(self.games, 3)
        self.assertEqual(self.norm(got), self.norm(want))
        for g in t["games"]:
            self.assertNotIn(g["key"], self.played)   # the finished task was used, not replayed
        self.assertEqual(list((self.root / "results").glob("*.json")), [])

    def test_local_exception_propagates(self):
        def boom(*a, **k):
            raise RuntimeError("engine missing")
        with mock.patch.object(arena, "play", boom):
            with self.assertRaises(RuntimeError):
                dist.play_many(self.games, 2, root=self.root)
        self.assertEqual(list((self.root / "claimed").glob("*.json")), [])

    def test_remote_error_is_replayed_locally(self):
        want = self.local()
        dist.ensure(self.root)
        c = dist.Coordinator(self.root, poll=0.02)
        keys = [dist.game_key(*g) for g in self.games]
        t = c.make_tasks(self.games, keys)[0]
        dist.submit(self.root, t)
        dist.claim(self.root, "bad")
        dist.complete(self.root, "bad", t, {g["key"]: {"key": g["key"], "error": "boom"} for g in t["games"]}, 0)
        got = c.play_many(self.games, 2)
        self.assertEqual(self.norm(got), self.norm(want))


class BlobTests(unittest.TestCase):
    """bot_spec -> worker rebuild reproduces neat_train.patch byte for byte (fake wasm, no compiler)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = dist.ensure(self.tmp / "dist")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_build(self, d, salt=b""):
        b = d / ".cambc" / "build"
        b.mkdir(parents=True)
        default = dist.MAGIC + struct.pack("<III", 16384, 1, 0)
        data = b"\x00asm" + salt + bytes(1000) + default + bytes(dist.REGION - len(default)) + b"tail" * 50
        for n in dist.WASMS:
            (b / n).write_bytes(data)
        return d

    def test_patch_spec_rebuild_roundtrip(self):
        import neat_train as nt
        slot = self.fake_build(self.tmp / "slot")
        worker_base = self.fake_build(self.tmp / "wbase")
        prog = [1, nt.NF, 0, 0x3F800000, 0x3F800000, 2, 0, 0x3F000000, 5, 0xBF000000, nt.NF]
        for progs in ({}, {r: prog for r in range(12)}):
            bot = nt.patch(slot, nt.blob(progs, 800), "x")
            spec = dist.bot_spec(bot)
            self.assertEqual(spec["kind"], "blob")
            wdata = [(worker_base / ".cambc" / "build" / n).read_bytes() for n in dist.WASMS]
            self.assertEqual(spec["base"], dist.base_hash(wdata))
            zeroed = [dist.zeroed(x, dist._region(x)) for x in wdata]
            out = dist.apply_blob(zeroed, dist._unb64z(spec["blob"]))
            self.assertEqual(hashlib.sha256(out[1]).hexdigest()[:16], bot.hash)
            dist._spec_cache.clear()

    def test_other_build_is_refused_or_adopted(self):
        import neat_train as nt
        bot = nt.patch(self.fake_build(self.tmp / "slot"), nt.blob({}, 800), "x")
        spec = dist.bot_spec(bot)
        dist.publish_base(self.root, bot, spec["base"])
        t = dict(task("t"), cambc="v", bots={bot.hash: spec})
        for adopt in (False, True):
            w = dist_worker.Worker(self.root, "w", jobs=1, adopt_base=adopt)
            w.version = "v"
            w.bases = {"other": (b"", b"")}          # this worker's own build differs
            self.assertEqual(w.why_not(t) is None, adopt)
        slot = self.fake_build(self.tmp / "wslot", salt=b"other compiler")
        got = w.materialise(bot.hash, spec, slot)
        self.assertEqual(arena.sha(slot / ".cambc" / "build" / dist.WASMS[1]), bot.hash)
        self.assertEqual(got.hash, bot.hash)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = dist.ensure(self.tmp / "dist")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def worker(self, **kw):
        w = dist_worker.Worker(self.root, "w", jobs=2, poll=0.01, **kw)
        w.version = "v"
        w.slots = [[None, None]] * 2
        w.hb = dist.Heartbeat(self.root, "w")
        w.bases = {"B": (b"", b"")}
        w.play_game = lambda t, g, s: fake_result(g["key"])
        return w

    def test_worker_refuses_other_cambc_and_unknown_base(self):
        w = self.worker()
        bad_v = dict(task("v1"), cambc="other")
        bad_b = dict(task("b1"), bots={"x": {"kind": "blob", "base": "NOPE", "blob": "", "hash": "x"}})
        good = dict(task("g1"), bots={"x": {"kind": "blob", "base": "B", "blob": "", "hash": "x"}})
        for t in (bad_v, bad_b, good):
            dist.submit(self.root, t)
        self.assertEqual(dist.claim(self.root, "w", w.accept)["id"], "g1")
        self.assertIsNone(dist.claim(self.root, "w", w.accept))
        self.assertEqual(sorted(p.name for p in (self.root / "queue").glob("*.json")), ["b1.json", "v1.json"])

    def test_until_stops_claiming_and_drains(self):
        w = self.worker(idle_exit=0.2)
        for i in range(6):
            dist.submit(self.root, task(f"t{i}", keys=(f"k{i}",)))
        w.stop_claim_at = time.time() - 1         # past the --until deadline: claim nothing
        w.loop(0)
        self.assertEqual(len(list((self.root / "queue").glob("*.json"))), 6)
        w.stop_claim_at = None
        w.loop(0)                                 # serves everything, then exits after idle_exit
        self.assertEqual(len(list((self.root / "results").glob("*.json"))), 6)
        self.assertEqual(w.hb.info["games"], 6)


if __name__ == "__main__":
    unittest.main()
