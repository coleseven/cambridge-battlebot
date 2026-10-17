"""Progress curve of an N-v-N NEAT run: every saved snapshot of its elites against fixed opponents.

The trainers' own fitness is measured against the evolving champion set, so a flat line there does not
mean "no progress". This plays each snapshot (.arena/neat/<run>/snapshots/gen####.json, written by every
`neat_train.py export`, plus the current elites.json) against fixed references:
  prior        the hand prior
  neat:<warm>  the 1v1 elites the run was warm-started from (generation 0 of the curve)
  cgp:<cgp>    the frozen CGP elites
on the same sampled team pairings every time (both layouts, both seats). The engine is deterministic and
results are cached, so a rerun only plays games for snapshots it has not seen.

  .venv/Scripts/python tools/progress.py n2v2 [--warm n2] [--cgp c2v2] [--team-size 2] [--pairings 4]
                                        [--max-snaps 8] [--jobs 12] [--dist .arena/dist]
Writes .arena/progress/<run>.json and prints score by generation (+ = snapshot better than the reference).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import dist  # noqa: E402
import duel_train  # noqa: E402
import league  # noqa: E402
import neat_train as nt  # noqa: E402
import nvn  # noqa: E402

PICKS, ROWS = nt.PICKS, nt.ROWS


def snapshots(run: str):
    """{gen: path}: saved snapshots plus the current elites.json."""
    d = arena.ARENA / "neat" / run
    out = {int(p.stem[3:]): p for p in sorted((d / "snapshots").glob("gen*.json"))}
    cur = d / "elites.json"
    if cur.exists():
        g = json.loads(cur.read_text(encoding="utf-8")).get("gen")
        if g is not None and g not in out:
            out[g] = cur
    return dict(sorted(out.items()))


def pick_spread(gens, k):
    """Up to k generations spread over the run, always including the first and the latest."""
    if len(gens) <= k:
        return list(gens)
    step = (len(gens) - 1) / (k - 1)
    return sorted({gens[round(i * step)] for i in range(k)})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--warm", default="n2", help="1v1 NEAT run the N-v-N run started from")
    ap.add_argument("--cgp", default="c2v2", help="frozen CGP run used as a reference ('' to skip)")
    ap.add_argument("--team-size", type=int, default=2)
    ap.add_argument("--pairings", type=int, default=4, help="sampled team pairings per hero and reference")
    ap.add_argument("--max-snaps", type=int, default=8)
    ap.add_argument("--resign", type=int, default=800)
    ap.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    dist.add_arg(ap)
    a = ap.parse_args()
    n = a.team_size

    snaps = snapshots(a.run)
    if not snaps:
        sys.exit(f"no snapshots or elites.json for neat:{a.run}")
    gens = pick_spread(list(snaps), a.max_snaps)
    refs = {"prior": {}, f"neat:{a.warm}": league.programs(f"neat:{a.warm}", n)}
    if a.cgp:
        refs[f"cgp:{a.cgp}"] = league.programs(f"cgp:{a.cgp}", n)

    slots = nt.build_slots(max(4, 2 * a.jobs), "progressslots")
    ref_slot = slots[0]

    def bot(progs, name):
        return dist.virtual_bot(ref_slot, nt.blob({r: progs[p] for p in progs for r in ROWS[p]}, a.resign), name)

    ref_bots = {name: bot(progs, name) for name, progs in refs.items()}
    games, meta = [], []
    for g in gens:
        sb = bot(nvn.load_programs(snaps[g]), f"gen{g}")
        for rname, rb in ref_bots.items():
            for p in range(len(PICKS)):
                for _, lay, own, opp, _ in nvn.schedule(n, 0, p, a.pairings, 0, seed="progress"):
                    games.append((sb, rb, nvn.map_path(n, lay, own, opp))); meta.append((g, rname, 0))
                    games.append((rb, sb, nvn.map_path(n, lay, opp, own))); meta.append((g, rname, 1))
    t0 = time.perf_counter()
    res = dist.play_slots(games, a.jobs, slots, root=a.dist or None)
    played = sum(1 for r in res if not r.get("cached"))
    scores, errors = {}, 0
    for (g, rname, seat), r in zip(meta, res):
        errors += "error" in r
        scores.setdefault(g, {}).setdefault(rname, []).append(duel_train.side_score(r, seat))
    table = {g: {r: round(sum(v) / len(v), 3) for r, v in by.items()} for g, by in scores.items()}
    for g, row in table.items():
        row["mean"] = round(sum(row.values()) / len(row), 3)

    names = list(refs)
    print(f"neat:{a.run} {n}v{n} progress: {len(games)} games ({played} played, the rest cached) in "
          f"{time.perf_counter() - t0:.0f}s, {errors} errored. Score = snapshot's mean game score vs the "
          f"reference (+ = snapshot better).")
    print(f"{'gen':>5}" + "".join(f"{x:>14}" for x in names) + f"{'mean':>10}")
    for g in gens:
        print(f"{g:>5}" + "".join(f"{table[g][x]:>+14.2f}" for x in names) + f"{table[g]['mean']:>+10.2f}")
    out = arena.ARENA / "progress" / f"{a.run}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    hist = prev.get("history", [])
    hist.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "table": {str(g): table[g] for g in gens}})
    out.write_text(json.dumps({"run": a.run, "team_size": n, "refs": names, "pairings": a.pairings,
                               "latest": {str(g): table[g] for g in gens}, "history": hist[-60:]}, indent=1),
                   encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
