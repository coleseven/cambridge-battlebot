"""Do two network-bot sources play the same games? Same genome blob (the N-v-N champion set) patched into
slots built from each source, on the same sampled maps (1v1/2v2/3v3 arenas, both seats; champion team vs
champion team and vs the hand prior), compared result by result (arena summary stats).

  .venv/Scripts/python tools/same_games.py --a bots/neat/src --b <other src> [--maps 20] [--sizes 1 2 3]
      [--warm-arena <.arena with champions>] [--jobs 12]
Exit 0 if every game is identical.
"""
import argparse
import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import neat_train as nt  # noqa: E402
import nvn  # noqa: E402


def slots_from(src: Path, kind: str, n=2):
    out = []
    for i in range(n):
        d = arena.ARENA / kind / f"s{i:02d}"
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(src, d / "src")
        out.append(arena.build(d, f"{kind}{i}").path)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--maps", type=int, default=20)
    ap.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--warm-arena", default="")
    ap.add_argument("--resign", type=int, default=800)
    ap.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    a = ap.parse_args()
    champs = nvn.champion_set(max(a.sizes), Path(a.warm_arena) if a.warm_arena else None)
    champ_blob = nvn.team_blob(nt, champs, a.resign)
    prior_blob = nt.blob({}, a.resign)
    sides = {}
    for tag, src in (("A", Path(a.a)), ("B", Path(a.b))):
        s0, s1 = slots_from(src.resolve(), f"same{tag}")
        sides[tag] = (nt.patch(s0, champ_blob, f"{tag}-champ"), nt.patch(s1, prior_blob, f"{tag}-prior"))
    rng = random.Random(31)
    maps = []
    for n in a.sizes:
        for _ in range(a.maps):
            ta = tuple(sorted(rng.sample(range(11), n)))
            tb = tuple(sorted(rng.sample(range(11), n)))
            maps.append(nvn.map_path(n, rng.choice(nvn.LAYOUTS), ta, tb))
    games, meta = [], []
    for tag in ("A", "B"):
        c, p = sides[tag]
        for m in maps:
            for x, y, kind in ((c, c, "champ-champ"), (c, p, "champ-prior"), (p, c, "prior-champ")):
                games.append((x, y, m)); meta.append((tag, m.name, kind))
    res = arena.play_many(games, a.jobs)
    strip = lambda r: json.dumps({k: v for k, v in r.items() if k not in ("key", "a", "b", "cached", "map")},  # noqa: E731
                                 sort_keys=True)
    half = len(games) // 2
    ra, rb = res[:half], res[half:]
    diff = [(meta[i][1], meta[i][2]) for i in range(half) if strip(ra[i]) != strip(rb[i])]
    errs = sum("error" in r for r in res)
    by_size = {}
    for i in range(half):
        size = meta[i][1].split("__")[1].count("+") // 2 + 1 if "+" in meta[i][1] else 1
        t = by_size.setdefault(size, [0, 0])
        t[0] += strip(ra[i]) == strip(rb[i])
        t[1] += 1
    for size, (same, tot) in sorted(by_size.items()):
        print(f"{size}v{size}: {same}/{tot} games identical")
    print(f"total: {half - len(diff)}/{half} identical, errors {errs}")
    for m, kind in diff[:10]:
        print(f"  differs: {kind:12} {m}")
    print("IDENTICAL" if not diff and not errs else "DIFFERENT")
    sys.exit(0 if not diff and not errs else 1)


if __name__ == "__main__":
    main()
