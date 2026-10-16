"""Per-hero champion set: for each hero, whichever method's current best network (NEAT or CGPANN)
wins the head-to-head on that hero, over every opposing hero x both duel layouts x both seats.

Both trainers use the set as their evolving opponent pool (the other half of each genome's duels
stays against the fixed hand-prior anchor), so each method trains against the strongest current
fighters rather than only its own elites.

  .venv/Scripts/python tools/champions.py --neat n2 --cgp c2 [--jobs 4] [--resign 800]
Writes .arena/champions/current.json: {"programs": {hero: words}, "source": {hero: method}, ...}.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import duel_train  # noqa: E402
import league  # noqa: E402
import neat_train as nt  # noqa: E402

PICKS, ROWS = nt.PICKS, nt.ROWS
OUT = arena.ARENA / "champions" / "current.json"


def load():
    """{pick: program words} of the current champion set, or None if there is none yet."""
    if not OUT.exists():
        return None
    d = json.loads(OUT.read_text())
    return {PICKS.index(h): w for h, w in d["programs"].items() if w}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--neat", default="n2")
    ap.add_argument("--cgp", default="c2")
    ap.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    ap.add_argument("--resign", type=int, default=800)
    a = ap.parse_args()
    specs = {"neat": f"neat:{a.neat}", "cgp": f"cgp:{a.cgp}"}
    progs = {k: league.programs(s) for k, s in specs.items()}
    slots = nt.build_slots(2, "champslots")
    bots = {k: nt.patch(slot, nt.blob({r: progs[k][p] for p in progs[k] for r in ROWS[p]}, a.resign), k)
            for k, slot in zip(("neat", "cgp"), slots)}
    games, meta = [], []
    for lay in ("open", "pillars"):
        for p in range(len(PICKS)):
            for q in range(len(PICKS)):
                games.append((bots["neat"], bots["cgp"], nt.map_path(lay, p, q))); meta.append((p, 0))
                games.append((bots["cgp"], bots["neat"], nt.map_path(lay, q, p))); meta.append((p, 1))
    t0 = time.perf_counter()
    res = arena.play_many(games, a.jobs)
    score = {p: [] for p in range(len(PICKS))}
    for (p, seat), r in zip(meta, res):
        score[p].append(duel_train.side_score(r, seat))       # NEAT's score when NEAT plays hero p
    chosen, source, margin = {}, {}, {}
    for p in range(len(PICKS)):
        m = sum(score[p]) / len(score[p])
        k = "neat" if m >= 0 else "cgp"
        chosen[PICKS[p]], source[PICKS[p]], margin[PICKS[p]] = progs[k].get(p), specs[k], round(m, 2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "neat": a.neat, "cgp": a.cgp,
                               "source": source, "neat_margin": margin, "programs": chosen}, indent=1))
    tmp.replace(OUT)
    print(f"{len(games)} games in {time.perf_counter() - t0:.0f}s; champion per hero (NEAT margin, + = NEAT better):")
    for h in PICKS:
        print(f"  {h:9} {source[h]:9} {margin[h]:+.2f}")


if __name__ == "__main__":
    main()
