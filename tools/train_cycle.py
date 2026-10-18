"""Run several resumable trainers in turn, forever (the training PC's standing job).

Each round runs every trainer for --block generations (each resumes from its own checkpoint), then
exports its elites and refreshes the per-hero champion set (tools/champions.py) both trainers fight, so best networks for every method keep landing in the synced .arena folder.
Stop it by deleting the job's file from .arena/jobs/running (or killing the process); restarting resumes.

  .venv/Scripts/python -u tools/train_cycle.py --block 5 --seed-from d2 --neat n1 --cgp c1 --jobs 4

N-v-N stage (tools/nvn.py), warm-started from the 1v1 runs on first launch:
  .venv/Scripts/python -u tools/train_cycle.py --team-size 2 --neat n2v2 --cgp c2v2 --warm-neat n2 --warm-cgp c2
      [--block 3] [--pop 12] [--islands 3] [--lam 3] [--pairings 5] [--duels 2] [--jobs 4]
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def step(args):
    print(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(args)}", flush=True)
    return subprocess.run([PY, "-u", *args], cwd=ROOT).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", type=int, default=5)
    ap.add_argument("--seed-from", default="d2")
    ap.add_argument("--neat", default="n1")
    ap.add_argument("--cgp", default="c1")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--slots", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=0, help="stop after this many cycles (0 = forever)")
    ap.add_argument("--team-size", type=int, default=1)
    ap.add_argument("--warm-neat", default="n2", help="N > 1: 1v1 NEAT run to warm-start from")
    ap.add_argument("--warm-cgp", default="c2", help="N > 1: 1v1 CGP run to warm-start from")
    ap.add_argument("--warm-arena", default="")
    ap.add_argument("--pop", type=int, default=0, help="NEAT population (0 = trainer default)")
    ap.add_argument("--islands", type=int, default=0, help="CGP islands (0 = trainer default)")
    ap.add_argument("--lam", type=int, default=0, help="CGP children per island (0 = trainer default)")
    ap.add_argument("--pairings", type=int, default=5)
    ap.add_argument("--duels", type=int, default=2)
    ap.add_argument("--retain", type=int, default=0, help="N>2: retention pairings at each smaller team size")
    ap.add_argument("--dist", default="", help="shared dist folder for tools/dist_worker.py workers (default off)")
    ap.add_argument("--freeze-cgp", action="store_true",
                    help="don't train CGP (its elites stay frozen); the champion refresh still compares against them")
    a = ap.parse_args()
    common = ["--gens", str(a.block), "--seed-from", a.seed_from, "--jobs", str(a.jobs), "--slots", str(a.slots)]
    common += ["--dist", a.dist] if a.dist else []
    extra = {"tools/neat_train.py": [], "tools/cgp_train.py": []}
    if a.team_size > 1:
        nv = ["--team-size", str(a.team_size), "--pairings", str(a.pairings), "--duels", str(a.duels),
              "--retain", str(a.retain)]
        nv += ["--warm-arena", a.warm_arena] if a.warm_arena else []
        extra["tools/neat_train.py"] = nv + ["--warm-run", a.warm_neat] + (["--pop", str(a.pop)] if a.pop else [])
        extra["tools/cgp_train.py"] = (nv + ["--warm-run", a.warm_cgp] + (["--islands", str(a.islands)] if a.islands else [])
                                       + (["--lam", str(a.lam)] if a.lam else []))
    n = 0
    while a.rounds == 0 or n < a.rounds:
        n += 1
        for tool, name in (("tools/neat_train.py", a.neat), ("tools/cgp_train.py", a.cgp)):
            if not name or (tool == "tools/cgp_train.py" and a.freeze_cgp):
                continue
            rc = step([tool, "run", name, *common, *extra[tool]])
            if rc:
                print(f"{tool} exited {rc}; continuing with the next trainer", flush=True)
                time.sleep(30)
                continue
            step([tool, "export", name])
        if a.neat and a.cgp:
            # Refresh the per-hero champion set both trainers use as their evolving opponents.
            step(["tools/champions.py", "--neat", a.neat, "--cgp", a.cgp, "--jobs", str(a.jobs),
                  *(["--team-size", str(a.team_size)] if a.team_size > 1 else []),
                  *(["--dist", a.dist] if a.dist else [])])


if __name__ == "__main__":
    main()
