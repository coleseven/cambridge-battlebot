"""Head-to-head evaluation: bot A vs bot B on every map in maps/, both seatings.

Usage:  .venv/Scripts/python tools/eval.py main starter-cpp [--jobs 6] [--maps a,b] [--rounds N]
Writes replays + stats under matches/<a>-vs-<b>-<timestamp>/ and prints a per-map table.
"""
import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMBC = ROOT / ".venv" / "Scripts" / "cambc.exe"


def run_one(a, b, map_path, out_dir, rounds):
    tag = f"{map_path.stem}__{a}_vs_{b}"
    stats = out_dir / f"{tag}.json"
    cmd = [str(CAMBC), "run", a, b, "--map", str(map_path), "-o", str(out_dir / tag),
           "--statistics", str(stats)]
    if rounds:
        cmd += ["--rounds", str(rounds)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not stats.exists():
        return {"map": map_path.stem, "a": a, "b": b, "error": proc.stdout[-800:] + proc.stderr[-800:]}
    d = json.loads(stats.read_text())
    t = d["teams"]
    return {"map": map_path.stem, "a": a, "b": b, "outcome": d["outcome"], "round": d["round"],
            "gems": (t[0]["submittedGems"], t[1]["submittedGems"]), "faults": d["botFaults"],
            "drafts": (t[0]["draftPicks"], t[1]["draftPicks"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--maps", default="", help="comma-separated map stems (default: all)")
    ap.add_argument("--rounds", type=int, default=0)
    args = ap.parse_args()

    # Build both bots once up front so parallel runs don't race on the build dir.
    for bot in {args.a, args.b}:
        r = subprocess.run([str(CAMBC), "build", bot], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            print(r.stdout, r.stderr)
            sys.exit(f"build failed: {bot}")

    maps = sorted((ROOT / "maps").glob("*.map26a"))
    if args.maps:
        want = set(args.maps.split(","))
        maps = [m for m in maps if m.stem in want]
    out_dir = ROOT / "matches" / f"{args.a}-vs-{args.b}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True)

    jobs = [(args.a, args.b, m) for m in maps] + [(args.b, args.a, m) for m in maps]
    results = []
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        futs = [ex.submit(run_one, x, y, m, out_dir, args.rounds) for x, y, m in jobs]
        for f in cf.as_completed(futs):
            results.append(f.result())

    wins = 0
    per_map = {}
    for r in sorted(results, key=lambda r: (r["map"], r["a"] != args.a)):
        if "error" in r:
            print(f"{r['map']:12} {r['a']} vs {r['b']}: ERROR\n{r['error']}")
            continue
        a_is_ours = r["a"] == args.a
        won = (r["outcome"] == "teamA") == a_is_ours
        wins += won
        ours, theirs = r["gems"] if a_is_ours else r["gems"][::-1]
        per_map.setdefault(r["map"], []).append(won)
        seat = "A" if a_is_ours else "B"
        flag = " FAULTS" + str(r["faults"]) if any(r["faults"]) else ""
        print(f"{r['map']:12} seat {seat}  {'WIN ' if won else 'loss'}  {ours:5} - {theirs:<5} r{r['round']}{flag}")
    n = sum(len(v) for v in per_map.values())
    print(f"\n{args.a} vs {args.b}: {wins}/{n} wins ({100 * wins / max(n, 1):.0f}%)   -> {out_dir}")
    (out_dir / "summary.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
