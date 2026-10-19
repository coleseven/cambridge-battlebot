"""Deaths to camps vs champions for a bot on some maps (both seats), from the engine's statistics JSON events.
Ladder review on 19 Oct found bodies dying over and over to monoliths on `contest`; this measures it locally.

  .venv/Scripts/python tools/camp_deaths.py BOT [--vs sparring-tarot] [--maps contest stampede swirl]
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402


def one(x, y, m, our_side):
    out = Path(tempfile.mkdtemp(prefix="cd-", dir=arena.ARENA / "tmp"))
    try:
        sf = out / "s.json"
        for _ in range(5):      # parallel runs sharing a bot directory can collide on a rename (Windows)
            subprocess.run([str(arena.CAMBC), "run", str(x.path), str(y.path), "--map", str(m),
                            "-o", str(out), "--statistics", str(sf), "--json"], cwd=arena.ROOT, capture_output=True)
            if sf.exists():
                break
        d = json.loads(sf.read_text(encoding="utf-8"))
        c = Counter()
        for e in d["events"]:
            if e["kind"] == "death" and e["owner"].get("team") == our_side:
                for t in e["entries"]:
                    kind = str(t["target"].get("kind", ""))       # "camp" locally, "Unknown entity kind (2)" on the platform
                    c["camp" if kind == "camp" or kind.endswith("(2)") else "champion"] += 1
        gems = {t["team"]: int(t["submittedGems"]) for t in d["teams"]}
        return c, d["match"]["outcome"] == our_side, gems[our_side]
    finally:
        shutil.rmtree(out, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bots", nargs="+")
    ap.add_argument("--vs", default="sparring-tarot")
    ap.add_argument("--maps", nargs="+", default=["contest", "stampede", "swirl"])
    ap.add_argument("--jobs", type=int, default=12)
    a = ap.parse_args()
    (arena.ARENA / "tmp").mkdir(parents=True, exist_ok=True)
    opp = arena.build(arena.ROOT / "bots" / a.vs, a.vs)
    for name in a.bots:
        b = arena.build(arena.ROOT / "bots" / name, name)
        jobs = [(b, opp, arena.resolve_map(m), "teamA", m) for m in a.maps] + \
               [(opp, b, arena.resolve_map(m), "teamB", m) for m in a.maps]
        with ThreadPoolExecutor(a.jobs) as ex:
            res = list(ex.map(lambda j: one(j[0], j[1], j[2], j[3]), jobs))
        for (_, _, _, side, m), (c, won, g) in zip(jobs, res):
            print(f"{name:18} {m:12} {side}: camp deaths {c['camp']:3}  champion deaths {c['champion']:3}  "
                  f"{'WIN ' if won else 'loss'} gems {g}")


if __name__ == "__main__":
    main()
