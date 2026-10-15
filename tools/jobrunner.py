"""Always-on job runner for the training PC.

Drop a JSON job file into <root>/queue/ and it runs, one job at a time (each job uses all cores):
    {"name": "duel-2v2-r1", "cmd": ".venv/Scripts/python -u tools/duel_train.py train r2 --size 2 --gens 40"}
Optional fields: "cwd" (default: repo root), "priority" (lower runs first, default 50).
The file moves queue/ -> running/ -> done/ or failed/, and output goes to logs/<name>.log.
status.json in <root> always shows the current job, so it can be checked over SSH.

With --on-commit "<cmd>", the runner also polls origin/main and queues that command whenever a new
commit lands (e.g. the regression suite), after fast-forwarding the local checkout.

Usage: .venv/Scripts/python tools/jobrunner.py [--root .arena/jobs] [--poll 15]
       [--on-commit ".venv/Scripts/python tools/eval.py main --vs starter-cpp sparring-tarot v1"]
"""
import argparse
import json
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def now():
    return datetime.now().isoformat(timespec="seconds")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def write_status(root, **kw):
    (root / "status.json").write_text(json.dumps({"host": socket.gethostname(), "at": now(), **kw}, indent=1))


def next_job(queue):
    jobs = []
    for f in queue.glob("*.json"):
        try:
            spec = json.loads(f.read_text(encoding="utf-8"))
            jobs.append((spec.get("priority", 50), f.stat().st_mtime, f, spec))
        except (OSError, json.JSONDecodeError):
            continue
    return min(jobs, key=lambda j: (j[0], j[1]))[2:] if jobs else (None, None)


def check_commit(a, root, last):
    if not a.on_commit:
        return last
    if git("fetch", "-q", "origin").returncode:
        return last
    head = git("rev-parse", "origin/main").stdout.strip()
    if head and head != last:
        if last is not None and git("merge", "--ff-only", "-q", "origin/main").returncode == 0:
            name = f"commit-{head[:8]}"
            (root / "queue" / f"{name}.json").write_text(json.dumps({"name": name, "cmd": a.on_commit, "priority": 10}))
            print(f"{now()} queued {name}", flush=True)
        return head
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".arena/jobs")
    ap.add_argument("--poll", type=int, default=15)
    ap.add_argument("--on-commit", default="")
    ap.add_argument("--commit-poll", type=int, default=300)
    a = ap.parse_args()
    root = (ROOT / a.root).resolve()
    for sub in ("queue", "running", "done", "failed", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    # Jobs left in running/ by a crash or reboot go back to the queue.
    for f in (root / "running").glob("*.json"):
        f.replace(root / "queue" / f.name)
    last_commit, last_check = None, 0.0
    print(f"{now()} job runner on {socket.gethostname()} watching {root}", flush=True)
    while True:
        if time.time() - last_check > a.commit_poll:
            last_commit, last_check = check_commit(a, root, last_commit), time.time()
        f, spec = next_job(root / "queue")
        if not f:
            write_status(root, state="idle")
            time.sleep(a.poll)
            continue
        name = spec.get("name", f.stem)
        run = root / "running" / f.name
        f.replace(run)
        log = root / "logs" / f"{name}.log"
        write_status(root, state="running", job=name, cmd=spec["cmd"], started=now())
        print(f"{now()} start {name}: {spec['cmd']}", flush=True)
        t0 = time.time()
        with log.open("a", encoding="utf-8") as out:
            out.write(f"# {now()} {spec['cmd']}\n")
            out.flush()
            rc = subprocess.run(spec["cmd"], shell=True, cwd=spec.get("cwd", str(ROOT)), stdout=out,
                                stderr=subprocess.STDOUT).returncode
        spec.update(returncode=rc, finished=now(), seconds=round(time.time() - t0))
        dest = root / ("done" if rc == 0 else "failed") / f.name
        dest.write_text(json.dumps(spec, indent=1))
        run.unlink(missing_ok=True)
        print(f"{now()} {'done' if rc == 0 else 'FAILED'} {name} rc={rc} ({spec['seconds']}s)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
