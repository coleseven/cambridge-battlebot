"""Local match engine for evaluation and tuning.

- Bots are directories (anything with src/ that `cambc build` accepts). A bot can be a variant of
  another with its src/params.h numbers rewritten; variants live in .arena/bots/<key>/.
- Each bot is identified by the SHA-256 of its compiled WASM, so results are keyed by
  (cambc version, bot A wasm, bot B wasm, map bytes). The engine is deterministic, so every key
  is played at most once and cached in .arena/results.jsonl.
- Matches run as parallel `cambc run` processes; replays are discarded unless asked for.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMBC = ROOT / ".venv" / ("Scripts/cambc.exe" if os.name == "nt" else "bin/cambc")
ARENA = ROOT / ".arena"
CACHE = ARENA / "results.jsonl"
DEFAULT_JOBS = max(1, min(12, (os.cpu_count() or 2) * 3 // 4))

_cache_lock = threading.Lock()
_cache: dict | None = None
_version: str | None = None


def cambc_version() -> str:
    global _version
    if _version is None:
        _version = subprocess.run([str(CAMBC), "--version"], capture_output=True, text=True).stdout.strip()
    return _version


def sha(path: Path, n=16) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:n]


@dataclass(frozen=True)
class Bot:
    name: str      # label for tables
    path: Path     # bot directory
    hash: str      # compiled wasm hash


def _run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def build(path: Path, name: str | None = None) -> Bot:
    path = Path(path)
    if not path.is_absolute():
        path = (ROOT / "bots" / path) if (ROOT / "bots" / path).is_dir() else (ROOT / path)
    r = _run([CAMBC, "build", path])
    wasm = path / ".cambc" / "build" / "bot-injected.wasm"
    if r.returncode or not wasm.exists():
        raise RuntimeError(f"build failed for {path}:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return Bot(name or path.name, path, sha(wasm))


def write_params(text: str, values: dict) -> str:
    """Rewrite `constexpr <type> NAME = <number>;` lines (or `#define NAME number`)."""
    for name, value in values.items():
        lit = ("true" if value else "false") if isinstance(value, bool) else (
            f"{value:.6g}" if isinstance(value, float) else str(int(value)))
        new, n = re.subn(rf"^(\s*(?:(?:static|inline)\s+)*constexpr\s+([\w:<> ]+?)\s+{re.escape(name)}\s*=\s*)"
                         rf"(?:[-+0-9.eE]+f?|true|false)(\s*;)",
                         lambda m: m.group(1) + (lit + "f" if "float" in m.group(2) and not isinstance(value, bool)
                                                 and lit not in ("true", "false") else lit) + m.group(3),
                         text, count=1, flags=re.M)
        if not n:
            new, n = re.subn(rf"^(\s*#\s*define\s+{re.escape(name)}\s+)\S+", rf"\g<1>{lit}", text, count=1, flags=re.M)
        if not n:
            raise KeyError(f"parameter {name} not found")
        text = new
    return text


def read_params(text: str) -> dict:
    out = {}
    for m in re.finditer(r"^\s*(?:(?:static|inline)\s+)*constexpr\s+([\w:<> ]+?)\s+(\w+)\s*=\s*([-+0-9.eE]+)f?\s*;",
                         text, flags=re.M):
        typ, name, val = m.groups()
        out[name] = float(val) if ("float" in typ or "double" in typ) else int(float(val))
    return out


def variant(base: Path, values: dict, name: str | None = None) -> Bot:
    """Build a copy of `base` with params.h numbers replaced. Identical requests reuse the directory."""
    base = Path(base) if Path(base).is_absolute() else ROOT / "bots" / base
    params = base / "src" / "params.h"
    text = write_params(params.read_text(encoding="utf-8"), values)
    src_hash = hashlib.sha256()
    for f in sorted((base / "src").rglob("*")):
        if f.is_file() and f.name != "params.h":
            src_hash.update(f.relative_to(base).as_posix().encode() + f.read_bytes())
    src_hash.update(text.encode())
    key = f"{base.name}-{src_hash.hexdigest()[:12]}"
    d = ARENA / "bots" / key
    if not (d / ".cambc" / "build" / "bot-injected.wasm").exists():
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        shutil.copytree(base / "src", d / "src")
        for extra in (".clangd", ".gitignore"):
            if (base / extra).exists():
                shutil.copy(base / extra, d / extra)
        (d / "src" / "params.h").write_text(text, encoding="utf-8")
    return build(d, name or key)


def build_many(specs, jobs=DEFAULT_JOBS):
    """specs: list of (base, values, name) -> list[Bot] in order, built in parallel."""
    with cf.ThreadPoolExecutor(jobs) as ex:
        return list(ex.map(lambda s: variant(*s), specs))


def _load_cache():
    global _cache
    if _cache is None:
        _cache = {}
        if CACHE.exists():
            for line in CACHE.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    _cache[r["key"]] = r
    return _cache


def _map_hash(m: Path) -> str:
    return sha(m, 12)


def resolve_map(m) -> Path:
    p = Path(m)
    if p.suffix != ".map26a":
        p = ROOT / "maps" / f"{m}.map26a"
    return p if p.is_absolute() else ROOT / p


def all_maps(pool="maps"):
    return sorted((ROOT / pool).glob("*.map26a"))


def _summarise(stats: dict) -> dict:
    t = stats["teams"]
    per = [{"kills": 0, "deaths": 0, "gold": 0} for _ in range(2)]
    for c in stats["champions"]:
        p = per[c["team"]]
        p["kills"] += c["kills"]; p["deaths"] += c["deaths"]; p["gold"] += c["earnedGold"]
    return {"outcome": stats["outcome"], "round": stats["round"],
            "gems": [t[0]["submittedGems"], t[1]["submittedGems"]], "faults": stats["botFaults"],
            "drafts": [t[0]["draftPicks"], t[1]["draftPicks"]], "teams": per}


def play(a: Bot, b: Bot, map_path: Path, keep_replay: Path | None = None) -> dict:
    key = f"{cambc_version()}|{a.hash}|{b.hash}|{_map_hash(map_path)}"
    with _cache_lock:
        hit = _load_cache().get(key)
    if hit and not keep_replay:
        return {**hit, "a": a.name, "b": b.name, "cached": True}
    out = Path(tempfile.mkdtemp(prefix="m-", dir=ARENA / "tmp"))
    try:
        stats_file = out / "stats.json"
        for attempt in range(5):
            r = _run([CAMBC, "run", a.path, b.path, "--map", map_path, "-o", keep_replay or out,
                      "--statistics", stats_file])
            # Parallel runs sharing a bot directory occasionally collide on a file rename (Windows).
            if stats_file.exists() or "failed to persist" not in r.stdout + r.stderr:
                break
            time.sleep(0.2 * (attempt + 1))
        if not stats_file.exists():
            res = {"error": (r.stdout[-1500:] + r.stderr[-1500:])}
        else:
            res = _summarise(json.loads(stats_file.read_text()))
    finally:
        shutil.rmtree(out, ignore_errors=True)
    res = {"key": key, "map": map_path.stem, **res}
    if "error" not in res:
        with _cache_lock:
            _load_cache()[key] = res
            with CACHE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(res) + "\n")
    return {**res, "a": a.name, "b": b.name, "cached": False}


def play_many(games, jobs=DEFAULT_JOBS, progress=False):
    """games: list of (Bot a, Bot b, map Path). Returns results in the same order."""
    (ARENA / "tmp").mkdir(parents=True, exist_ok=True)
    results = [None] * len(games)
    done = 0
    with cf.ThreadPoolExecutor(jobs) as ex:
        futs = {ex.submit(play, *g): i for i, g in enumerate(games)}
        for f in cf.as_completed(futs):
            results[futs[f]] = f.result()
            done += 1
            if progress and (done % 25 == 0 or done == len(games)):
                print(f"  {done}/{len(games)} games", flush=True)
    return results


def suite_many(candidates, opponents, maps, jobs=DEFAULT_JOBS, progress=False):
    """Every candidate x map x opponent x both seats, played as one parallel batch.
    Returns one list of per-game rows (from that candidate's point of view) per candidate."""
    games, meta = [], []
    for ci, ours in enumerate(candidates):
        for m in maps:
            for o in opponents:
                games.append((ours, o, m)); meta.append((ci, m.stem, o.name, 0))
                games.append((o, ours, m)); meta.append((ci, m.stem, o.name, 1))
    out = [[] for _ in candidates]
    for (ci, mp, opp, seat), r in zip(meta, play_many(games, jobs, progress)):
        if "error" in r:
            out[ci].append({"map": mp, "opp": opp, "seat": seat, "error": r["error"]})
            continue
        us, them = (0, 1) if seat == 0 else (1, 0)
        won = r["outcome"] == ("teamA" if seat == 0 else "teamB")
        out[ci].append({"map": mp, "opp": opp, "seat": seat, "won": won, "round": r["round"],
                        "ours": r["gems"][us], "theirs": r["gems"][them], "fault": r["faults"][us] > 0,
                        "draft": r["drafts"][us], "deaths": r["teams"][us]["deaths"]})
    return out


def suite(ours: Bot, opponents, maps, jobs=DEFAULT_JOBS, progress=False):
    return suite_many([ours], opponents, maps, jobs, progress)[0]


def game_score(row) -> float:
    """[-1, 1]: win/loss plus half-weight gem margin; a fault or error scores -2."""
    if "error" in row or row["fault"]:
        return -2.0
    margin = max(-1.0, min(1.0, (row["ours"] - row["theirs"]) / 1000))
    return (0.5 if row["won"] else -0.5) + 0.5 * margin


def fitness(rows, worst_weight=0.25):
    """Mean over maps of per-map mean game score, plus worst_weight x worst map."""
    by_map = {}
    for r in rows:
        by_map.setdefault(r["map"], []).append(game_score(r))
    per_map = {m: sum(v) / len(v) for m, v in by_map.items()}
    vals = list(per_map.values())
    return sum(vals) / len(vals) + worst_weight * min(vals), per_map
