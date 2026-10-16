"""Round-robin league between fighting policies, all run in the same network bot (bots/neat) with
the same resign round, on every hero matchup x both duel layouts x both seats.

Contestants (pick any; default all):
  neat:<run>   NEAT elites exported to .arena/neat/<run>/elites.json
  cgp:<run>    CGPANN current elites from .arena/cgp/<run>/state.pkl
  lin:<run>    a CMA duel run's linear weights, encoded as an equivalent network
  champ        the per-hero champion set (.arena/champions/current.json, tools/champions.py)
  prior        the hand prior (empty genome block -> the bot's built-in linear weights)

  .venv/Scripts/python tools/league.py neat:n2 cgp:c2 lin:d2 prior [--jobs 12] [--resign 800]
Writes .arena/league/<timestamp>.json with pairwise and per-hero results.

  --team-size N (N > 1): N-v-N league instead. Per hero h and contestant pair, --pairings sampled team
  pairings (h in the row contestant's team, random allies and opponents, both layouts, both seats; the
  same sample every time, so results cache). The per-hero score is the row team's mean game score in
  games where it fields h. `champ` is then the N-v-N champion set (falls back to the 1v1 set).
"""
import argparse
import itertools
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import cgp_train as cg  # noqa: E402
import duel_train  # noqa: E402
import neat_train as nt  # noqa: E402
import nvn  # noqa: E402

PICKS, ROWS = nt.PICKS, nt.ROWS


def programs(spec, team_size=1):
    """Contestant spec -> {pick: program words} (empty dict = hand prior)."""
    if spec == "prior":
        return {}
    if spec == "champ":
        return nvn.champion_set(team_size)
    kind, run = spec.split(":")
    if kind == "neat":
        return nvn.load_programs(arena.ARENA / "neat" / run / "elites.json")
    if kind == "cgp":
        st = pickle.loads((arena.ARENA / "cgp" / run / "state.pkl").read_bytes())
        return {p: cg.program(g) for p, g in st["elite"].items()}
    if kind == "lin":
        m = duel_train.current_mean(pickle.loads((duel_train.OUT / run / "state.pkl").read_bytes()))
        rng = __import__("random").Random(0)
        out = {}
        for p, rows in ROWS.items():
            w = np.zeros(nt.NF_V1)
            k = min(m.shape[1], nt.NF_V1)
            w[:k] = np.mean([m[r][:k] for r in rows], axis=0)
            if m.shape[1] < nt.NF_V1:    # older runs lack the newer features: use the prior there
                w[k:] = duel_train.prior_matrix()[0][k:nt.NF_V1]
            out[p] = cg.program(cg.seeded_genome(w, rng))
        return out
    raise SystemExit(f"unknown contestant {spec}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("contestants", nargs="*", default=["neat:n2", "cgp:c2", "lin:d2", "prior"])
    ap.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    ap.add_argument("--resign", type=int, default=800)
    ap.add_argument("--team-size", type=int, default=1)
    ap.add_argument("--pairings", type=int, default=12, help="N > 1: sampled team pairings per hero")
    a = ap.parse_args()
    n = a.team_size
    slots = nt.build_slots(len(a.contestants), "leagueslots")
    bots = {}
    for spec, slot in zip(a.contestants, slots):
        progs = programs(spec, n)
        bots[spec] = nt.patch(slot, nt.blob({r: progs[p] for p in progs for r in ROWS[p]}, a.resign), spec)
    games, meta = [], []
    for x, y in itertools.combinations(a.contestants, 2):
        if n > 1:
            for p in range(len(PICKS)):
                for _, lay, own, opp, _ in nvn.schedule(n, 0, p, a.pairings, 0, seed="league"):
                    games.append((bots[x], bots[y], nvn.map_path(n, lay, own, opp))); meta.append((x, y, p, None, 0))
                    games.append((bots[y], bots[x], nvn.map_path(n, lay, opp, own))); meta.append((x, y, p, None, 1))
            continue
        for lay in ("open", "pillars"):
            for p in range(len(PICKS)):
                for q in range(len(PICKS)):
                    games.append((bots[x], bots[y], nt.map_path(lay, p, q))); meta.append((x, y, p, q, 0))
                    games.append((bots[y], bots[x], nt.map_path(lay, q, p))); meta.append((x, y, p, q, 1))
    t0 = time.perf_counter()
    res = arena.play_many(games, a.jobs, progress=True)
    pair, hero = {}, {}
    for (x, y, p, q, flip), r in zip(meta, res):
        s = duel_train.side_score(r, 1 if flip else 0)      # from x's point of view, x playing hero p
        pair.setdefault((x, y), []).append(s)
        hero.setdefault((x, y, p), []).append(s)
    print(f"\n{len(games)} games in {time.perf_counter() - t0:.0f}s. Score = row's mean duel score vs column "
          "(kills diff + gems diff/50 + small damage term; + = row better)")
    names = a.contestants
    print(" " * 12 + "".join(f"{n:>12}" for n in names))
    for x in names:
        row = ""
        for y in names:
            if x == y:
                row += f"{'-':>12}"
            elif (x, y) in pair:
                row += f"{np.mean(pair[(x, y)]):>+12.2f}"
            else:
                row += f"{-np.mean(pair[(y, x)]):>+12.2f}"
        print(f"{x:12}" + row)
    print("\nPer hero (row contestant playing that hero, mean over all opposing heroes):")
    print(f"{'hero':10}" + "".join(f"{x[:6]}>{y[:6]:>8}" for x, y in itertools.combinations(names, 2)))
    for p in range(len(PICKS)):
        print(f"{PICKS[p]:10}" + "".join(f"{np.mean(hero[(x, y, p)]):>+15.1f}" for x, y in itertools.combinations(names, 2)))
    out = arena.ARENA / "league" / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"contestants": names, "team_size": n,
                               "pairs": {f"{x}|{y}": float(np.mean(v)) for (x, y), v in pair.items()},
                               "heroes": {f"{x}|{y}|{PICKS[p]}": float(np.mean(v)) for (x, y, p), v in hero.items()}},
                              indent=1))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
