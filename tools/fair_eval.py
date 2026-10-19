"""Fair comparison of fighter variants inside the SAME combined bot (a real_train run's fixed team_src) with
the strategy layer's own meta draft (no draft block), so only the fighter differs.

  .venv/Scripts/python tools/fair_eval.py real1 [--vs sparring-tarot v1 starter-cpp] [--jobs 12]
Variants: team-<run> (the run's current elites), team-frozen (frozen_start.json), team-prior (hand-prior
fighter), forager (strategy layer alone, the run's fixed copy). Plays every variant vs the opponent pool and
the variants head-to-head, all 17 maps, both seats. Writes .arena/neat/<run>/fair_eval.json.
"""
import argparse
import itertools
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import bake_fighter as bf  # noqa: E402
import neat_train as nt  # noqa: E402
import nvn  # noqa: E402


def variant(run_dir: Path, name: str, progs: dict) -> arena.Bot:
    d = arena.ARENA / "faireval" / name
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(run_dir / "team_src", d / "src")
    _, words = bf.blob_words(progs, 0)
    (d / "src" / "genome.cpp").write_text(bf.genome_cpp(words, f"fair_eval {name}"), encoding="utf-8")
    return arena.build(d, name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--vs", nargs="+", default=["sparring-tarot", "v1", "starter-cpp"])
    ap.add_argument("--jobs", type=int, default=12)
    a = ap.parse_args()
    rd = nt.OUT / a.run
    variants = {
        f"team-{a.run}": variant(rd, f"team-{a.run}", nvn.load_programs(rd / "elites.json")),
        "team-frozen": variant(rd, "team-frozen", nvn.load_programs(rd / "frozen_start.json")),
        "team-prior": variant(rd, "team-prior", {}),
        "forager": arena.build(arena.ARENA / "realopp" / "forager", "forager"),
    }
    opps = [arena.build(arena.ROOT / "bots" / o, o) for o in a.vs]
    maps = arena.all_maps()
    names = list(variants)
    rows = arena.suite_many([variants[n] for n in names], opps, maps, jobs=a.jobs)
    out = {"vs_pool": {}, "head_to_head": {}}
    print(f"{'variant':16} {'fitness':>8}  " + "  ".join(f"{o.name[:12]:>12}" for o in opps) + "   gem margin  deaths")
    for n, rs in zip(names, rows):
        f = arena.fitness(rs)[0]
        errs = sum("error" in r for r in rs)
        rs = [r for r in rs if "error" not in r]
        if errs:
            print(f"  {n}: {errs} errored games left out of the counts")
        wins = {o.name: sum(r["won"] for r in rs if r["opp"] == o.name) for o in opps}
        gm = sum(r["ours"] - r["theirs"] for r in rs) / len(rs)
        de = sum(r["deaths"] for r in rs) / len(rs)
        out["vs_pool"][n] = {"fitness": round(f, 3), "wins": wins, "gem_margin": round(gm, 1), "deaths": round(de, 2)}
        print(f"{n:16} {f:+8.3f}  " + "  ".join(f"{wins[o.name]:>9}/{2 * len(maps)}" for o in opps) +
              f"   {gm:+9.1f}  {de:6.2f}")
    print("\nhead to head (row vs column: wins of 34, mean gem margin):")
    games, meta = [], []
    for x, y in itertools.combinations(names, 2):
        for m in maps:
            games += [(variants[x], variants[y], m), (variants[y], variants[x], m)]
            meta += [(x, y, 0), (x, y, 1)]
    res = arena.play_many(games, a.jobs)
    agg = {}
    for (x, y, seat), r in zip(meta, res):
        if "error" in r:
            continue
        us, them = seat, 1 - seat
        won = r["outcome"] == ("teamA" if seat == 0 else "teamB")
        t = agg.setdefault((x, y), [0, 0, 0.0])
        t[0] += won
        t[1] += 1
        t[2] += r["gems"][us] - r["gems"][them]
    for (x, y), (w, n, gm) in agg.items():
        out["head_to_head"][f"{x} vs {y}"] = {"wins": w, "games": n, "gem_margin": round(gm / n, 1)}
        print(f"  {x:16} vs {y:16}: {w:2}/{n} wins, gem margin {gm / n:+7.1f}")
    (rd / "fair_eval.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
