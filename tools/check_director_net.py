"""Checks the learned-director plumbing (tools/director_train.py):
  1. a warm-start network (seeded on the rule-choice feature) plays exactly the rule director's games;
  2. a scrambled network plays different games (the 'DIRN' block is really read);
  3. fuel of the warm-start network vs the rules (engine logs are not used: games/turn counts only).

  .venv/Scripts/python tools/check_director_net.py [--team team-dirlearn] [--maps 6] [--jobs 12]
Exit 0 if check 1 holds and check 2 shows a difference.
"""
import argparse
import random
import shutil
import struct
import sys
from pathlib import Path

import neat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import bake_fighter as bf  # noqa: E402
import director_train as dt  # noqa: E402
import dist  # noqa: E402
import league  # noqa: E402
import neat_train as nt  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default="team-dirlearn")
    ap.add_argument("--fighters", default="neat:real1")
    ap.add_argument("--maps", type=int, default=6)
    ap.add_argument("--jobs", type=int, default=12)
    a = ap.parse_args()
    d = arena.ARENA / "dncheck"
    d.mkdir(parents=True, exist_ok=True)
    slots = []
    for i in range(2 * a.jobs):
        s = d / f"s{i:02d}"
        if s.exists():
            shutil.rmtree(s)
        shutil.copytree(arena.ROOT / "bots" / a.team / "src", s / "src")
        slots.append(arena.build(s, f"dn{i}").path)
    config = nt.make_config(d, 4, "", nin=dt.NFD)
    pop = neat.Population(config)
    dt.seed(pop, config, random.Random(1))
    g_seed, g_scr = list(pop.population.values())[:2]
    rng = random.Random(5)
    for cg in g_scr.connections.values():
        cg.weight = rng.gauss(0, 2.0)          # scrambled: the rule feature no longer dominates
    _, fw = bf.blob_words(league.programs(a.fighters, 3), 0)
    fblob = struct.pack(f"<{len(fw)}I", *fw)
    rules = dist.virtual_bot(slots[0], fblob, "rules")
    seeded = dist.virtual_bot(slots[0], dt.with_director(fblob, dt.program(g_seed, config)), "seeded")
    scrambled = dist.virtual_bot(slots[0], dt.with_director(fblob, dt.program(g_scr, config)), "scrambled")
    opp = arena.build(arena.ROOT / "bots" / "starter-cpp", "starter-cpp")
    maps = random.Random(2).sample(arena.all_maps(), a.maps)
    games = lambda b: [g for m in maps for g in ((b, opp, m), (opp, b, m))]   # noqa: E731
    res = dist.play_slots(games(rules) + games(seeded) + games(scrambled), a.jobs, slots)
    n = 2 * len(maps)
    r0, r1, r2 = res[:n], res[n:2 * n], res[2 * n:]
    keys = ("outcome", "round", "gems", "teams")
    for x, y in zip(r0, r1): print(x.get("map"), x.get("gems"), y.get("gems"), x.get("a"), y.get("a"))
    same1 = sum(all(x.get(k) == y.get(k) for k in keys) for x, y in zip(r0, r1))
    same2 = sum(all(x.get(k) == y.get(k) for k in keys) for x, y in zip(r0, r2))
    errs = sum("error" in r for r in res)
    print(f"warm-start network == rules: {same1}/{n} games identical")
    print(f"scrambled network == rules: {same2}/{n} (should be fewer)   errors {errs}")
    sys.exit(0 if same1 == n and same2 < n and not errs else 1)


if __name__ == "__main__":
    main()
