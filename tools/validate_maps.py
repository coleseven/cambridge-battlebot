"""Validate .map26a files: decode, symmetry/reachability, engine load, and a full game.

    python tools/validate_maps.py maps_gen/ [--jobs 4] [--no-game] [--no-engine]

Per map:
  1. mapformat decodes it and re-encodes it byte-for-byte.
  2. Terrain is symmetric under the header's symmetry; both spawns reach every altar and camp
     and each other (mapstats.py rules).
  3. Engine check (if Node and the cambc engine wasm are found): the engine's own map loader
     accepts it, passes MapBuilder_VerifyIntegrity, decodes the same tiles/camps/header as
     mapformat, and re-encodes it to identical bytes.
  4. `cambc run starter-cpp starter-cpp` plays to completion with zero bot faults.
Exit status is non-zero if any map fails.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import mapformat as mf  # noqa: E402
import mapstats as ms  # noqa: E402


def find_cambc() -> str:
    for c in (ROOT / ".venv/bin/cambc", ROOT / ".venv/Scripts/cambc.exe", ROOT / ".venv/Scripts/cambc"):
        if c.exists():
            return str(c)
    return shutil.which("cambc") or "cambc"


def find_wasm() -> str | None:
    roots = [Path.home() / ".cache/cambc", Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "cambc",
             Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "cambc"]
    for r in roots:
        hits = sorted(glob.glob(str(r / "**/mapeditor/assets/v*.wasm"), recursive=True), key=os.path.getmtime)
        if hits:
            return hits[-1]
    return None


def static_checks(path: Path) -> tuple:
    b = path.read_bytes()
    m = mf.decode(b)
    errs = []
    if mf.encode(m) != b:
        errs.append("re-encode differs")
    f = ms.features(m)
    if mf.SYMMETRY_NAMES[m.symmetry] not in f["terrain_symmetric_under"]:
        errs.append("terrain not symmetric")
    if f["unreachable_a"] or f["unreachable_b"]:
        errs.append(f"unreachable altars/camps: A={f['unreachable_a']} B={f['unreachable_b']}")
    if f["spawn_to_enemy_spawn"] is None:
        errs.append("spawns not connected")
    return m, errs


def engine_checks(results: dict, wasm: str, models: dict) -> None:
    paths = list(models)
    out = subprocess.run(["node", str(ROOT / "tools/map_engine_check.mjs"), wasm, *map(str, paths)],
                         capture_output=True, text=True)
    lines = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
    by = {line["map"]: line for line in lines}
    for p in paths:
        r, m = by.get(str(p)), models[p]
        if r is None:
            results[p].append(f"engine check produced no output: {out.stderr.strip()[:200]}")
            continue
        if not r["ok"]:
            results[p].append(r["error"])
            continue
        want = {"symmetry": m.symmetry, "champions": m.champions, "spawn_a": list(m.spawn_a),
                "width": m.width, "height": m.height, "camps": [list(c) for c in m.camps],
                **{k: ["".join(map(str, row)) for row in getattr(m, k)] for k in ("bush", "wall", "hole", "altar")}}
        bad = [k for k, v in want.items() if r[k] != v]
        if bad:
            results[p].append(f"engine decodes differently: {bad}")
        if not r["engine_reencode_identical"]:
            results[p].append("engine re-encode differs")


def play(cambc: str, path: Path) -> list:
    with tempfile.TemporaryDirectory() as td:
        stats = Path(td) / "s.json"
        proc = subprocess.run([cambc, "run", "starter-cpp", "starter-cpp", "--map", str(path.resolve()),
                               "--statistics", str(stats), "-o", td], cwd=ROOT, capture_output=True, text=True)
        if proc.returncode != 0 or not stats.exists():
            return [f"game failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[-300:]}"]
        s = json.loads(stats.read_text())
        faults = s.get("botFaults")
        log = (proc.stdout + "\n" + proc.stderr).replace("\r", "\n")
        summary = next((ln.strip() for ln in log.splitlines() if ln.strip().startswith("finished")), "?")
        if not summary.startswith("finished"):
            return [f"game did not finish: {log.strip()[-200:]}"]
        return [] if faults == [0, 0] else [f"bot faults {faults} ({summary})"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--no-game", action="store_true")
    ap.add_argument("--no-engine", action="store_true")
    ap.add_argument("--wasm", help="engine wasm (default: search the cambc cache)")
    a = ap.parse_args()
    paths = ms.map_paths(a.paths)
    results, models = {}, {}
    for p in paths:
        try:
            models[p], results[p] = static_checks(p)
        except Exception as e:  # noqa: BLE001
            results[p] = [f"decode failed: {e}"]
    if not a.no_engine:
        wasm = a.wasm or find_wasm()
        if wasm and shutil.which("node"):
            engine_checks(results, wasm, models)
        else:
            print("engine check skipped (need node and the cambc engine wasm; see --wasm)")
    if not a.no_game:
        cambc = find_cambc()
        subprocess.run([cambc, "build", "starter-cpp"], cwd=ROOT, capture_output=True)
        with ThreadPoolExecutor(a.jobs) as ex:
            for p, errs in zip(models, ex.map(lambda p: play(cambc, p), models)):
                results[p] += errs
    bad = 0
    for p in paths:
        errs = results[p]
        bad += bool(errs)
        print(f"{'FAIL' if errs else 'ok  '} {p}" + (": " + "; ".join(errs) if errs else ""))
    print(f"{len(paths) - bad}/{len(paths)} maps passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
