"""Per-turn fuel from the engine's own logs (team-a.fuel / team-b.fuel in every replay bundle), so it works
for --release (platform) builds and needs no probe code in the bot.

.fuel layout (cambc 2.2.2, decoded 18 Oct 2026): b"a62leuf\\0" magic, 5 header bytes, a budget table of
8 little-endian u64 (draft first turn, draft turn, champion first turn, champion turn, twice), then one u64
per bot turn in order: the draft turns (one per pick), each body's first turn (2x budget, init), then play.

  .venv/Scripts/python tools/fuel_engine.py BOT [BOT...] --vs sparring-tarot [--maps elkring rows ...]
      [--features-on]   (bots/forager only: also a copy with every FEATURE_* switch on)
Prints p50 / p90 / p99 / max per bot (first turns excluded) and the share of turns above 60k / 80k / 90k.
"""
import argparse
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402

MAGIC = b"a62leuf\0"


def fuel_values(data: bytes):
    if not data.startswith(MAGIC):
        raise ValueError("not a cambc .fuel file")
    i = data.find(struct.pack("<Q", 1_000_000))
    vals = [struct.unpack_from("<Q", data, j)[0] for j in range(i, len(data) - 7, 8)]
    return vals[:8], vals[8:]


def play(x: Path, y: Path, m: Path, release=True):
    out = Path(tempfile.mkdtemp(prefix="fe-", dir=arena.ARENA / "tmp"))
    try:
        sf = out / "s.json"
        cmd = [str(arena.CAMBC), "run", str(x), str(y), "--map", str(m), "-o", str(out),
               "--statistics", str(sf), "--json"] + (["--release"] if release else [])
        r = subprocess.run(cmd, cwd=arena.ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if not sf.exists():
            raise RuntimeError((r.stdout + r.stderr)[-600:])
        stats = json.loads(sf.read_text(encoding="utf-8"))
        bundle = next(out.glob("*.repl26a"))
        with zipfile.ZipFile(bundle) as z:
            fuel = {t: fuel_values(z.read(f"team-{t}.fuel"))[1] for t in "ab"}
        skip = {}
        for side, t in (("teamA", "a"), ("teamB", "b")):
            picks = sum(1 for d in stats["roster"]["draft"] if d["team"] == side)
            bodies = sum(1 for c in stats["roster"]["champions"] if c["team"] == side)
            skip[t] = picks + bodies
        return {t: fuel[t][skip[t]:] for t in "ab"}, arena._summarise(stats)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def summary(v):
    v = sorted(v)
    if not v:
        return {}
    q = lambda f: v[min(len(v) - 1, int(f * len(v)))]   # noqa: E731
    pct = lambda t: round(100 * sum(x > t for x in v) / len(v), 2)  # noqa: E731
    return {"turns": len(v), "p50": q(.5), "p90": q(.9), "p99": q(.99), "max": v[-1],
            ">60k%": pct(60_000), ">80k%": pct(80_000), ">90k%": pct(90_000), ">100k%": pct(100_000)}


def forager_all_on() -> Path:
    d = arena.ROOT / "bots" / "fuelstudy-forager-on"
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(arena.ROOT / "bots" / "forager" / "src", d / "src")
    p = d / "src" / "params.h"
    p.write_text(re.sub(r"(constexpr int FEATURE_\w+ = )0;", r"\g<1>1;", p.read_text(encoding="utf-8")),
                 encoding="utf-8")
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bots", nargs="+")
    ap.add_argument("--vs", default="sparring-tarot")
    ap.add_argument("--maps", nargs="+", default=["elkring", "rows", "corners", "lanes", "collision"])
    ap.add_argument("--features-on", action="store_true")
    ap.add_argument("--default-build", action="store_true", help="default instead of --release builds")
    a = ap.parse_args()
    (arena.ARENA / "tmp").mkdir(parents=True, exist_ok=True)
    maps = [sorted((arena.ROOT / "maps").glob(f"{m}*.map26a"))[0] for m in a.maps
            if sorted((arena.ROOT / "maps").glob(f"{m}*.map26a"))]
    bots = {b: arena.ROOT / "bots" / b for b in a.bots}
    if a.features_on:
        bots["forager (all features on)"] = forager_all_on()
    opp = arena.ROOT / "bots" / a.vs
    for path in list(bots.values()) + [opp]:
        arena.build(path)          # a fresh bot dir needs one default build before cambc's --release build works
    out = {}
    for name, path in bots.items():
        vals = []
        for m in maps:
            for us_first in (True, False):
                x, y = (path, opp) if us_first else (opp, path)
                try:
                    fuel, res = play(x, y, m, release=not a.default_build)
                except RuntimeError as e:
                    print(f"  {name} on {m.stem}: FAILED {str(e)[-200:]}", flush=True)
                    continue
                v = fuel["a" if us_first else "b"]
                vals += v
                print(f"  {name:28} {m.stem[:22]:22} {'A' if us_first else 'B'}: {len(v)} turns, max {max(v) if v else 0}, "
                      f"gems {res['gems']}", flush=True)
        out[name] = summary(vals)
    print(f"\nper-turn fuel ({'default' if a.default_build else 'release'} builds, engine logs, first turns excluded)"
          f" vs {a.vs}:")
    for name, s in out.items():
        print(f"  {name:28} {json.dumps(s)}")
    (arena.ARENA / "fuel").mkdir(parents=True, exist_ok=True)
    (arena.ARENA / "fuel" / "engine.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
