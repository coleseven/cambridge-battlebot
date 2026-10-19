"""Checks the director's runtime params (tools/team_bot.py --runtime-params) against compiled values:

  1. the runtime build with no 'PRMS' block plays the same games as the plain compiled build;
  2. the runtime build with a 'PRMS' block of values V plays the same games as a build compiled with V (--set).

  .venv/Scripts/python tools/check_runtime_params.py [--source neat:real1] [--maps 8] [--jobs 12]
Exit 0 if every game is identical.
"""
import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import bake_fighter as bf  # noqa: E402
import dist  # noqa: E402
import league  # noqa: E402
import team_bot as tb  # noqa: E402
import tune  # noqa: E402

SPACE = arena.ROOT / "tools" / "space-director.json"


def gen(out, source, extra):
    sets = ["NN_HEROES=4095", *extra.pop("set", [])]      # one --set (a second one would replace the first)
    cmd = [sys.executable, str(arena.ROOT / "tools" / "team_bot.py"), "--macro-src", "bots/director/src",
           "--source", source, "--out", out, *extra.get("args", []), "--set", *sets]
    subprocess.run(cmd, check=True, cwd=arena.ROOT, capture_output=True)
    return arena.build(arena.ROOT / "bots" / out, out)


def same(xs, ys, label):
    keys = ("outcome", "round", "gems", "teams")
    n = sum(all(x.get(k) == y.get(k) for k in keys) and "error" not in x for x, y in zip(xs, ys))
    print(f"{label}: {n}/{len(xs)} games identical")
    return n == len(xs)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="neat:real1")
    ap.add_argument("--maps", type=int, default=8)
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--only", nargs="*", default=None, help="perturb only these params (others at their defaults)")
    a = ap.parse_args()
    space = json.loads(SPACE.read_text(encoding="utf-8"))
    rng = random.Random(7)
    vals = tune.decode([rng.random() for _ in space], space)       # a random candidate
    if a.only is not None:
        cur = arena.read_params((arena.ROOT / "bots" / "director" / "src" / "params.h").read_text(encoding="utf-8"))
        vals = {k: (v if k in a.only else cur[k]) for k, v in vals.items()}
    plain = gen("check-rp-plain", a.source, {})
    runtime = gen("check-rp-runtime", a.source, {"args": ["--runtime-params", str(SPACE)]})
    fixed = gen("check-rp-fixed", a.source, {"set": [f"{k}={v}" for k, v in vals.items()]})
    _, words = bf.blob_words(league.programs(a.source, 3) if not a.source.startswith("champ") else {}, 0)
    import struct
    base_blob = struct.pack(f"<{len(words)}I", *words)
    opp = arena.build(arena.ROOT / "bots" / "sparring-tarot", "sparring-tarot")
    maps = random.Random(1).sample(arena.all_maps(), a.maps)
    games = lambda b: [g for m in maps for g in ((b, opp, m), (opp, b, m))]   # noqa: E731
    slots = []                    # BlobBots play in rewritable copies of the runtime build (as in real_train)
    for i in range(2 * a.jobs):
        d = arena.ARENA / "rpcheck" / f"s{i:02d}"
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(arena.ROOT / "bots" / "check-rp-runtime" / "src", d / "src")
        slots.append(arena.build(d, f"rp{i}").path)
    cand = dist.virtual_bot(slots[0], tb.blob_with_params(base_blob, list(vals.values())), "check-rp-cand")
    res = dist.play_slots(games(plain) + games(runtime) + games(fixed) + games(cand), a.jobs, slots)
    n = 2 * len(maps)
    r_plain, r_runtime, r_fixed, r_cand = (res[i * n:(i + 1) * n] for i in range(4))
    print(f"candidate {vals}")
    ok = same(r_plain, r_runtime, "no block == compiled defaults")
    ok &= same(r_fixed, r_cand, "PRMS block == compiled candidate")
    differs = not all(x.get("gems") == y.get("gems") for x, y in zip(r_plain, r_fixed))
    print(f"(the candidate plays differently from the defaults: {differs})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
