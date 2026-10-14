"""CMA-ES tuning of the numbers in a bot's src/params.h (ported from the UNSW bcsim/tune.py design).

Prepare a run (freezes the base source, search space, opponents and maps):
  .venv/Scripts/python tools/tune.py prepare r1 --base main --space tools/space-main.json \
      --vs starter-cpp sparring-tarot [--maps maps] [--popsize 12] [--sigma 0.2]
Run or resume (checkpoints every generation; Ctrl-C safe):
  .venv/Scripts/python tools/tune.py run r1 [--gens 20] [--jobs 12]
Show the best candidate and its params.h lines:
  .venv/Scripts/python tools/tune.py best r1

Space file: {"NAME": [low, high]} or [low, high, "int"]. CMA-ES works in [0, 1] per parameter.
Objective: arena.fitness = mean over maps of mean game score (+-0.5 win/loss + 0.5 x gem margin/1000)
plus 0.25 x worst map; any fault or runner error scores -2 for that game.
Results are cached by compiled-wasm hash, so re-evaluating a repeated candidate is free.
"""
import argparse
import json
import pickle
import shutil
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402

warnings.filterwarnings("ignore", message="Could not import matplotlib")
RUNS = arena.ARENA / "runs"


def bounds(spec):
    return spec[0], spec[1], len(spec) > 2 and spec[2] == "int"


def decode(x, space):
    vals = {}
    for xi, (name, spec) in zip(x, space.items()):
        lo, hi, is_int = bounds(spec)
        v = lo + min(1.0, max(0.0, float(xi))) * (hi - lo)
        vals[name] = int(round(v)) if is_int else round(v, 5)
    return vals


def encode(vals, space):
    out = []
    for name, spec in space.items():
        lo, hi, _ = bounds(spec)
        out.append(min(1.0, max(0.0, (vals[name] - lo) / (hi - lo))))
    return out


def prepare(a):
    d = RUNS / a.name
    if d.exists():
        sys.exit(f"{d} exists; pick another name")
    base_dir = arena.ROOT / "bots" / a.base
    space = json.loads(Path(a.space).read_text())
    current = arena.read_params((base_dir / "src" / "params.h").read_text(encoding="utf-8"))
    missing = [k for k in space if k not in current]
    if missing:
        sys.exit(f"not in params.h: {missing}")
    d.mkdir(parents=True)
    shutil.copytree(base_dir / "src", d / "base" / "src")       # frozen base source
    maps_dir = d / "maps"
    maps_dir.mkdir()
    src_maps = arena.all_maps(a.maps) if (arena.ROOT / a.maps).is_dir() else [arena.resolve_map(s) for s in a.maps.split(",")]
    for m in src_maps:
        shutil.copy(m, maps_dir / m.name)                        # frozen map pool
    plan = {"base": a.base, "space": space, "vs": a.vs, "popsize": a.popsize, "sigma": a.sigma,
            "x0": encode(current, space), "start": {k: current[k] for k in space},
            "cambc": arena.cambc_version(), "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    (d / "plan.json").write_text(json.dumps(plan, indent=1))
    print(f"prepared {d}: {len(space)} params, {len(src_maps)} maps, vs {a.vs}")


def run(a):
    import cma
    d = RUNS / a.name
    plan = json.loads((d / "plan.json").read_text())
    space = plan["space"]
    base = d / "base"
    maps = arena.all_maps(str(d.relative_to(arena.ROOT) / "maps"))
    opps = [arena.build(o) for o in plan["vs"]]
    ckpt = d / "cma.pkl"
    log = d / "log.jsonl"

    if ckpt.exists():
        es = pickle.loads(ckpt.read_bytes())
        print(f"resuming at generation {es.countiter}")
    else:
        es = cma.CMAEvolutionStrategy(plan["x0"], plan["sigma"],
                                      {"bounds": [0, 1], "popsize": plan["popsize"], "verbose": -9, "seed": 1})
        # Generation 0 baseline: the starting parameters.
        start = arena.variant(base, plan["start"], "start")
        f, per_map = arena.fitness(arena.suite(start, opps, maps, a.jobs))
        with log.open("a") as fh:
            fh.write(json.dumps({"gen": 0, "fitness": f, "values": plan["start"], "per_map": per_map}) + "\n")
        print(f"gen 0 (start) fitness {f:+.4f}")

    for _ in range(a.gens):
        t0 = time.perf_counter()
        xs = es.ask()
        vals = [decode(x, space) for x in xs]
        bots = arena.build_many([(base, v, f"c{i}") for i, v in enumerate(vals)], a.jobs)
        rows = arena.suite_many(bots, opps, maps, a.jobs)
        fits = [arena.fitness(r) for r in rows]
        es.tell(xs, [-f for f, _ in fits])                       # cma minimises
        gen = es.countiter
        with log.open("a") as fh:
            for v, (f, per_map), r in zip(vals, fits, rows):
                wins = sum(x.get("won", False) for x in r)
                fh.write(json.dumps({"gen": gen, "fitness": f, "values": v, "per_map": per_map,
                                     "wins": wins, "games": len(r)}) + "\n")
        ckpt.write_bytes(pickle.dumps(es))
        best_i = max(range(len(fits)), key=lambda i: fits[i][0])
        mean_vals = decode(es.mean, space)
        print(f"gen {gen:3}  best {fits[best_i][0]:+.4f}  median {sorted(f for f, _ in fits)[len(fits) // 2]:+.4f}"
              f"  sigma {es.sigma:.3f}  {time.perf_counter() - t0:5.1f}s  mean {mean_vals}", flush=True)


def best(a):
    d = RUNS / a.name
    entries = [json.loads(l) for l in (d / "log.jsonl").read_text().splitlines() if l.strip()]
    start = next(e for e in entries if e["gen"] == 0)
    top = sorted(entries, key=lambda e: -e["fitness"])[: a.top]
    print(f"start fitness {start['fitness']:+.4f}  {start['values']}")
    for e in top:
        print(f"gen {e['gen']:3} fitness {e['fitness']:+.4f}  {e['values']}")
    print("\nparams.h values for the best candidate:")
    for k, v in top[0]["values"].items():
        print(f"  {k} = {v}")
    print("Re-check it on a wider pool before adopting: tools/eval.py <bot> --set ... --vs ...")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("name"); p.add_argument("--base", default="main")
    p.add_argument("--space", required=True); p.add_argument("--vs", nargs="+", required=True)
    p.add_argument("--maps", default="maps"); p.add_argument("--popsize", type=int, default=12)
    p.add_argument("--sigma", type=float, default=0.2)
    r = sub.add_parser("run"); r.add_argument("name"); r.add_argument("--gens", type=int, default=10)
    r.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    b = sub.add_parser("best"); b.add_argument("name"); b.add_argument("--top", type=int, default=5)
    a = ap.parse_args()
    {"prepare": prepare, "run": run, "best": best}[a.cmd](a)


if __name__ == "__main__":
    main()
