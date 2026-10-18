"""Per-turn fuel of the baked fighter (bots/fighter, tools/bake_fighter.py) in platform (--release) and default
builds, plus a check that release games give the same results.

  .venv/Scripts/python tools/fuel_study.py [--games 4] [--sizes 1 2 3] [--real elkring collision rows]

For each team size it plays fighter-vs-fighter games on the duel arenas (and 5v5 games on --real maps from the
published pool), logging every turn's fuel from a BOT_FUEL probe copy, and prints p50 / p90 / p99 / max, the
share of turns above 90k and above the 100k limit, and how often the network's re-ranking was cut short.
Turn 1 of each body is excluded (2x fuel, init). Writes .arena/fuel/study.json.
"""
import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import nvn  # noqa: E402

LIMIT = 100_000


def probe_dir(name: str, release: bool) -> Path:
    """Copy of bots/fighter/src that logs every turn's fuel (BOT_FUEL=2)."""
    d = arena.ROOT / "bots" / f"fuelstudy-{name}"   # under bots/: cambc --release fails for dirs in .arena
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(arena.ROOT / "bots" / "fighter" / "src", d / "src")
    b = d / "src" / "bot.cpp"
    t = b.read_text(encoding="utf-8")
    old = 'if (!first && used > FUEL_MAX) { FUEL_MAX = used; std::cout << "FUEL " << S.type << " " << used << std::endl; }'
    if old not in t:
        sys.exit("bot.cpp fuel probe line not found; update tools/fuel_study.py")
    t = t.replace(old, 'if (!first) std::cout << "FUEL " << S.type << " " << used << std::endl;')
    b.write_text("#define BOT_FUEL 1\n" + t, encoding="utf-8")
    return d


def run(x: Path, y: Path, m: Path, release: bool, stats=False):
    out = Path(tempfile.mkdtemp(prefix="fuel-", dir=arena.ARENA / "tmp"))
    try:
        cmd = [str(arena.CAMBC), "run", str(x), str(y), "--map", str(m), "-o", str(out)]
        if release:
            cmd.append("--release")
        sf = out / "stats.json"
        if stats:
            cmd += ["--statistics", str(sf), "--json"]
        r = subprocess.run(cmd, cwd=arena.ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        text = ""
        for f in out.glob("*.repl26a"):
            with zipfile.ZipFile(f) as z:
                for n in z.namelist():
                    if n.endswith(".stdout"):
                        text += z.read(n).decode("utf-8", "replace") + "\n"
        st = arena._summarise(json.loads(sf.read_text(encoding="utf-8"))) if stats and sf.exists() else None
        if stats and st is None:
            st = {"error": r.stdout[-500:] + r.stderr[-500:]}
        if not stats and not text.strip():
            print(f"  no bot output: {(r.stdout + r.stderr)[-300:]}", flush=True)
        return text, st
    finally:
        shutil.rmtree(out, ignore_errors=True)


def parse(text):
    fuel, nn_cut, nn_all = [], 0, 0
    for line in text.splitlines():
        m = re.match(r"FUEL (\d+) (\d+)", line)
        if m:
            fuel.append(int(m.group(2)))
        m = re.match(r"NN (\d+) (\d+) (\d) (\d+)", line)
        if m:
            nn_all += 1
            nn_cut += m.group(3) == "0"
    return fuel, nn_cut, nn_all


def summary(v):
    if not v:
        return {}
    v = sorted(v)
    q = lambda f: v[min(len(v) - 1, int(f * len(v)))]   # noqa: E731
    return {"turns": len(v), "p50": q(.5), "p90": q(.9), "p99": q(.99), "max": v[-1],
            "over90k%": round(100 * sum(x > 90_000 for x in v) / len(v), 2),
            "over100k%": round(100 * sum(x > LIMIT for x in v) / len(v), 3)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=4)
    ap.add_argument("--sizes", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--real", nargs="*", default=["elkring", "collision", "rows"])
    a = ap.parse_args()
    (arena.ARENA / "tmp").mkdir(parents=True, exist_ok=True)
    rng = random.Random(11)
    jobs = []
    for n in a.sizes:
        for g in range(a.games):
            ta = tuple(sorted(rng.sample(range(11), n)))
            tb = tuple(sorted(rng.sample(range(11), n)))
            jobs.append((f"{n}v{n}", nvn.map_path(n, nvn.LAYOUTS[g % 2], ta, tb)))
    for stem in a.real:
        jobs.append(("5v5 real map", arena.resolve_map(stem)))
    results = {}
    same = []
    plain = {False: arena.ROOT / "bots" / "fighter", True: arena.ROOT / "bots" / "fighter"}  # separate build dirs
    probe = probe_dir("probe", False)          # one source copy; release and default builds coexist
    for release in (True, False):
        for label, m in jobs:
            text, _ = run(probe, probe, m, release)
            fuel, cut, nn = parse(text)
            r = results.setdefault(f"{'release' if release else 'default'} {label}", {"fuel": [], "cut": 0, "nn": 0})
            r["fuel"] += fuel
            r["cut"] += cut
            r["nn"] += nn
            print(f"{'release' if release else 'default':8} {label:12} {m.name[:40]:40} turns {len(fuel):5} "
                  f"max {max(fuel) if fuel else 0}", flush=True)
    # Same results in release? (plain baked fighter, no probe output)
    for label, m in jobs:
        _, s_rel = run(plain[True], plain[True], m, True, stats=True)
        _, s_def = run(plain[False], plain[False], m, False, stats=True)
        ok = s_rel == s_def and "error" not in (s_rel or {})
        if "error" in (s_rel or {}) or "error" in (s_def or {}):
            print(f"  ERROR release={str(s_rel)[:200]} default={str(s_def)[:200]}", flush=True)
        same.append(ok)
        print(f"results {label:12} {m.name[:40]:40} release == default: {s_rel == s_def}", flush=True)
    print("\nper-turn fuel (turn 1 excluded):")
    out = {}
    for k, r in results.items():
        s = summary(r["fuel"])
        s["nn_cut_short%"] = round(100 * r["cut"] / r["nn"], 1) if r["nn"] else None
        out[k] = s
        print(f"  {k:24} {json.dumps(s)}")
    print(f"\nrelease vs default game results identical: {sum(same)}/{len(same)}")
    (arena.ARENA / "fuel").mkdir(parents=True, exist_ok=True)
    (arena.ARENA / "fuel" / "study.json").write_text(json.dumps({"summary": out, "identical": [sum(same), len(same)]},
                                                                indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
