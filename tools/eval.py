"""Evaluate one bot against an opponent pool on a map pool, both seats (cached; see tools/arena.py).

Usage:
  .venv/Scripts/python tools/eval.py main --vs starter-cpp sparring-tarot [--maps maps] [--jobs N]
  .venv/Scripts/python tools/eval.py main --set RECALL_HP_FRAC=0.5 --vs starter-cpp
--maps takes a directory of .map26a files or comma-separated map stems from maps/.
"""
import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402


def parse_maps(spec):
    p = arena.ROOT / spec
    if p.is_dir():
        return arena.all_maps(spec)
    return [arena.resolve_map(s) for s in spec.split(",")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bot")
    ap.add_argument("--vs", nargs="+", default=["starter-cpp"])
    ap.add_argument("--maps", default="maps")
    ap.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    ap.add_argument("--set", nargs="*", default=[], help="NAME=value params.h overrides")
    a = ap.parse_args()

    if a.set:
        vals = {}
        for kv in a.set:
            k, v = kv.split("=")
            vals[k] = float(v) if "." in v else int(v)
        ours = arena.variant(a.bot, vals, name=a.bot + "*")
    else:
        ours = arena.build(a.bot)
    opps = [arena.build(o) for o in a.vs]
    maps = parse_maps(a.maps)
    rows = arena.suite(ours, opps, maps, a.jobs)

    for r in sorted(rows, key=lambda r: (r["map"], r["opp"], r["seat"])):
        if "error" in r:
            print(f"{r['map']:12} vs {r['opp']:14} seat {'AB'[r['seat']]}  ERROR {r['error'][-300:]}")
            continue
        print(f"{r['map']:12} vs {r['opp']:14} seat {'AB'[r['seat']]}  {'WIN ' if r['won'] else 'loss'} "
              f"{r['ours']:5}-{r['theirs']:<5} r{r['round']:<5} D{r['deaths']}{'  FAULT' if r['fault'] else ''}")
    by_opp = collections.defaultdict(list)
    for r in rows:
        by_opp[r["opp"]].append(r)
    print()
    for o, rs in by_opp.items():
        w = sum(r.get("won", False) for r in rs)
        print(f"vs {o:14} {w}/{len(rs)} wins ({100 * w / len(rs):.0f}%)")
    f, per_map = arena.fitness(rows)
    print(f"fitness {f:+.3f}   worst map {min(per_map, key=per_map.get)} {min(per_map.values()):+.3f}")


if __name__ == "__main__":
    main()
