"""Validate a tuning run's best candidates (plus the CMA mean and the start point) on a wider pool.

Usage: .venv/Scripts/python tools/validate_run.py v1 [--top 5] [--vs starter-cpp sparring-tarot v1b]
       [--maps maps .arena/maps_holdout]
Prints per-candidate fitness and win rates per map set, so held-out generalisation is visible.
"""
import argparse
import json
import pickle
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore", message="Could not import matplotlib")
import arena  # noqa: E402
import tune  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--vs", nargs="+", default=["starter-cpp", "sparring-tarot", "v1b"])
    ap.add_argument("--maps", nargs="+", default=["maps", ".arena/maps_holdout"])
    ap.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    a = ap.parse_args()

    d = tune.RUNS / a.run
    plan = json.loads((d / "plan.json").read_text())
    entries = [json.loads(l) for l in (d / "log.jsonl").read_text().splitlines() if l.strip()]
    cands = [("start", plan["start"])]
    seen = set()
    for e in sorted(entries, key=lambda e: -e["fitness"]):
        key = json.dumps(e["values"], sort_keys=True)
        if key in seen or e["gen"] == 0:
            continue
        seen.add(key)
        cands.append((f"g{e['gen']}{e['fitness']:+.2f}", e["values"]))
        if len(cands) > a.top:
            break
    es = pickle.loads((d / "cma.pkl").read_bytes())
    cands.append(("cma-mean", tune.decode(es.mean, plan["space"])))

    base = d / "base"
    bots = arena.build_many([(base, v, name) for name, v in cands], a.jobs)
    opps = [arena.build(o) for o in a.vs]
    pools = {m: arena.all_maps(m) for m in a.maps}
    all_maps = [m for ms in pools.values() for m in ms]
    rows = arena.suite_many(bots, opps, all_maps, a.jobs, progress=True)

    print(f"\n{'candidate':14}" + "".join(f"{Path(m).name[:12]:>26}" for m in a.maps) + f"{'overall':>10}")
    for (name, vals), r in zip(cands, rows):
        line = f"{name:14}"
        for m, ms in pools.items():
            stems = {x.stem for x in ms}
            sub = [x for x in r if x["map"] in stems]
            f, _ = arena.fitness(sub)
            w = {o: sum(x.get("won", False) for x in sub if x["opp"] == o) for o in a.vs}
            n = len(ms) * 2
            line += f"{f:+7.3f} " + "/".join(f"{w[o]:>2}" for o in a.vs) + f" of {n:<3}"
        f, _ = arena.fitness(r)
        line += f"{f:+10.3f}"
        print(line)
    print("\nwins listed per opponent in order:", ", ".join(a.vs))
    for name, vals in cands:
        print(name, json.dumps(vals))


if __name__ == "__main__":
    main()
