"""Did the extra workers speed training up? Reads a trainer's log.jsonl (N-v-N runs log a "dist" block per
generation when run with --dist) and compares generation times with and without remote workers.

  .venv/Scripts/python tools/dist_report.py n2v2 [--last 30] [--root .arena/dist]
  .venv/Scripts/python tools/dist_report.py .arena/cgp/c2v2/log.jsonl

Groups generations by the set of remote workers seen alive during them. "solo" = distribution on but no remote
worker; "no stats" = generations logged before the dist block existed (or run without --dist). Speed-up is
measured on seconds per distinct game played (cached games excluded), so a generation that hits the cache
more does not look faster; generations without stats fall back to seconds per generation.
Waste = local replays of slow remote claims (stolen) + duplicate remote results, as a share of games played.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402


def find_log(spec: str) -> Path:
    p = Path(spec)
    if p.is_file():
        return p
    for kind in ("neat", "cgp"):
        q = arena.ARENA / kind / spec / "log.jsonl"
        if q.exists():
            return q
    raise SystemExit(f"no log.jsonl for {spec} under {arena.ARENA}/neat or /cgp")


def read(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def group_of(r):
    d = r.get("dist")
    if not d:
        return "no stats"
    ws = d.get("workers") or sorted(d.get("by_worker", {}))
    return "+".join(ws) if ws else "solo"


def played(d):
    return d.get("local", 0) + d.get("remote", 0)


def per_game(r):
    d = r.get("dist")
    return r["secs"] / played(d) if d and played(d) else None


def summarise(rows):
    groups = {}
    for r in rows:
        groups.setdefault(group_of(r), []).append(r)
    out = []
    for g, rs in groups.items():
        ds = [r["dist"] for r in rs if r.get("dist")]
        pg = [x for x in map(per_game, rs) if x is not None]
        games = sum(played(d) for d in ds)
        out.append({
            "group": g, "gens": len(rs), "median_secs": statistics.median(r["secs"] for r in rs),
            "s_per_game": statistics.median(pg) if pg else None,
            "games_per_h": games * 3600 / sum(r["secs"] for r in rs if r.get("dist")) if ds and games else None,
            "remote_share": sum(d.get("remote", 0) for d in ds) / games if games else None,
            "waste": sum(d.get("stolen", 0) + d.get("dup", 0) for d in ds) / games if games else None,
            "by_worker": {w: sum(d.get("by_worker", {}).get(w, 0) for d in ds)
                          for w in sorted({w for d in ds for w in d.get("by_worker", {})})},
        })
    return out


def speedups(summary):
    base = next((s for s in summary if s["group"] == "solo"), None) or \
        next((s for s in summary if s["group"] == "no stats"), None)
    res = {}
    if not base:
        return None, res
    for s in summary:
        if s is base:
            continue
        if base["s_per_game"] and s["s_per_game"]:
            res[s["group"]] = base["s_per_game"] / s["s_per_game"]
        else:
            res[s["group"]] = base["median_secs"] / s["median_secs"]
    return base["group"], res


def fmt(x, spec, dash="-"):
    return dash if x is None else format(x, spec)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="run name (.arena/neat|cgp/<run>/log.jsonl) or a log.jsonl path")
    ap.add_argument("--last", type=int, default=30, help="generations listed (all are summarised)")
    ap.add_argument("--since-gen", type=int, default=0, help="ignore generations before this one")
    ap.add_argument("--root", default="", help="also print the worker status of this dist folder")
    a = ap.parse_args()
    path = find_log(a.run)
    rows = [r for r in read(path) if "secs" in r and r.get("gen", 0) >= a.since_gen]
    if not rows:
        raise SystemExit(f"{path}: no generations")
    print(f"{path}: {len(rows)} generations\n")
    print(f"{'gen':>5} {'end':>11} {'secs':>6} {'games':>6} {'local':>6} {'remote':>6} {'cached':>6} "
          f"{'stolen':>6} {'dup':>5}  workers")
    for r in rows[-a.last:]:
        d = r.get("dist") or {}
        end = time.strftime("%d %H:%M", time.localtime(d["t"])) if d.get("t") else "-"
        print(f"{r['gen']:5} {end:>11} {r['secs']:6} {fmt(d.get('games'), 'd'):>6} {fmt(d.get('local'), 'd'):>6} "
              f"{fmt(d.get('remote'), 'd'):>6} {fmt(d.get('cached'), 'd'):>6} {fmt(d.get('stolen'), 'd'):>6} "
              f"{fmt(d.get('dup'), 'd'):>5}  {group_of(r)}")
    summary = summarise(rows)
    print(f"\n{'group':24} {'gens':>5} {'med s/gen':>9} {'s/game':>7} {'games/h':>8} {'remote':>7} {'waste':>6}  remote games by worker")
    for s in summary:
        print(f"{s['group'][:24]:24} {s['gens']:5} {s['median_secs']:9.0f} {fmt(s['s_per_game'], '7.2f'):>7} "
              f"{fmt(s['games_per_h'], '8.0f'):>8} {fmt(s['remote_share'], '7.0%'):>7} {fmt(s['waste'], '6.0%'):>6}  "
              + " ".join(f"{w}:{n}" for w, n in s["by_worker"].items()))
    base, sp = speedups(summary)
    if base:
        for g, x in sp.items():
            print(f"speed-up of {g} vs {base}: {x:.2f}x")
    if a.root:
        import dist_worker
        print()
        dist_worker.status(a.root)


if __name__ == "__main__":
    sys.exit(main())
