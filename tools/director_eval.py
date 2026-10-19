"""Director vs the pool and head-to-head against the reference team bots (same table as tools/fair_eval.py).

  .venv/Scripts/python tools/director_eval.py team-director [more bots/<name> ...] [--refs forager team-real1]
      [--vs sparring-tarot v1 starter-cpp] [--jobs 12]
Candidates are built from bots/<name>; refs are bots/<name> or a fair_eval build (.arena/faireval/<name>) or the
real_train opponent copy (.arena/realopp/<name>). All 17 maps, both seats.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402


def find(name):
    for d in (arena.ARENA / "faireval" / name, arena.ARENA / "realopp" / name, arena.ROOT / "bots" / name):
        if (d / "src").exists():
            return arena.build(d, name)
    sys.exit(f"no source for {name}")


def variant(spec):
    """name=base:K=V,K=V -> a copy of bots/<base> with those params.h values, built under .arena/dirvar/<name>."""
    name, rest = spec.split("=", 1)
    base, _, sets = rest.partition(":")
    d = arena.ARENA / "dirvar" / name
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(arena.ROOT / "bots" / base / "src", d / "src")
    pp = d / "src" / "params.h"
    s = pp.read_text(encoding="utf-8")
    for kv in filter(None, sets.split(",")):
        k, v = kv.split("=")
        s, n = re.subn(rf"(constexpr \w+ {k} = )[^;]+;", rf"\g<1>{v};", s)
        if n != 1:
            sys.exit(f"param {k} not found")
    pp.write_text(s, encoding="utf-8")
    return arena.build(d, name)


def table(name, rs, opps, n_maps):
    errs = sum("error" in r for r in rs)
    f = arena.fitness(rs)[0]
    rs = [r for r in rs if "error" not in r]
    wins = {o: sum(r["won"] for r in rs if r["opp"] == o) for o in opps}
    gm = sum(r["ours"] - r["theirs"] for r in rs) / max(1, len(rs))
    de = sum(r["deaths"] for r in rs) / max(1, len(rs))
    print(f"{name:18} {f:+8.3f}  " + "  ".join(f"{wins[o]:>9}/{2 * n_maps}" for o in opps) +
          f"   {gm:+9.1f}  {de:6.2f}" + (f"  ({errs} errors)" if errs else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bots", nargs="*")
    ap.add_argument("--var", nargs="*", default=[], help="name=base:K=V,K=V params variants")
    ap.add_argument("--refs", nargs="*", default=["forager", "team-real1"])
    ap.add_argument("--vs", nargs="+", default=["sparring-tarot", "v1", "starter-cpp"])
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--pool", nargs="+", default=["maps"], help="map folders (maps, maps_gen)")
    ap.add_argument("--no-h2h", action="store_true")
    a = ap.parse_args()
    cands = [arena.build(arena.ROOT / "bots" / b, b) for b in a.bots] + [variant(v) for v in a.var]
    refs = [find(r) for r in a.refs]
    opps = [find(o) for o in a.vs]
    maps = [m for pool in a.pool for m in arena.all_maps(pool)]
    names = [o.name for o in opps]
    print(f"{'bot':18} {'fitness':>8}  " + "  ".join(f"{o[:12]:>12}" for o in names) + "   gem margin  deaths")
    for b, rs in zip(cands + refs, arena.suite_many(cands + refs, opps, maps, jobs=a.jobs)):
        table(b.name, rs, names, len(maps))
    if refs and not a.no_h2h:
        print("\nhead to head (wins of 2 x maps, gem margin, our deaths):")
        for b, rs in zip(cands, arena.suite_many(cands, refs, maps, jobs=a.jobs)):
            for ref in refs:
                rr = [r for r in rs if r["opp"] == ref.name and "error" not in r]
                w = sum(r["won"] for r in rr)
                gm = sum(r["ours"] - r["theirs"] for r in rr) / max(1, len(rr))
                de = sum(r["deaths"] for r in rr) / max(1, len(rr))
                print(f"  {b.name:18} vs {ref.name:14}: {w:2}/{len(rr)}  {gm:+7.1f}  deaths {de:5.2f}")


if __name__ == "__main__":
    main()
