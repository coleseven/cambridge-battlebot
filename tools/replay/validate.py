"""Check decoded replays against official statistics.

Usage: python tools/replay/validate.py <dir or files>... [--timelines DIR]

For each bundle (.zip / .repl26a) it needs official statistics from either a platform sidecar
(<stem>.meta.json from fetch.py) or a local `cambc run --statistics` file named <stem>.stats.json.
The public platform API stopped publishing per-match statistics in mid-October 2026; for such
sidecars only the final gems are checked against the public scores ("scores only").
Compares per team: final submitted gems, kills (from kill events, C&I linked deaths count once),
body deaths (from death events); per champion: banked gems, gems collected from camps, and
champion damage dealt/taken. Prints one line per game and exits non-zero on any mismatch.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import events as E  # noqa: E402
import metrics as M  # noqa: E402


def stats_for(p):
    side = p.with_suffix(".meta.json")
    if side.exists():
        meta = json.loads(side.read_text(encoding="utf-8"))
        if meta.get("statistics"):
            return meta["statistics"], "platform"
        if all(isinstance(x, int) for x in meta.get("scores") or [None]):
            return {"scores_only": meta["scores"]}, "platform-scores"   # public API: final scores only
        return None, None
    st = p.with_suffix(".stats.json")
    if st.exists():
        return json.loads(st.read_text(encoding="utf-8")), "local"
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--timelines", help="also write decoded timelines here")
    a = ap.parse_args()
    files = []
    for i in a.inputs:
        p = Path(i)
        files += sorted(q for q in p.iterdir() if q.suffix.lower() in (".zip", ".repl26a")) if p.is_dir() else [p]
    bad = n = 0
    for f in files:
        stats, src = stats_for(f)
        if not stats:
            print(f"SKIP {f.name}: no statistics")
            continue
        out = subprocess.run(["node", str(HERE / "decode.mjs"), str(f)], capture_output=True, text=True)
        if out.returncode:
            print(f"FAIL {f.name}: decode error {out.stderr.strip()[-300:]}")
            bad += 1; n += 1
            continue
        tl = json.loads(out.stdout)
        if a.timelines:
            Path(a.timelines).mkdir(parents=True, exist_ok=True)
            (Path(a.timelines) / (f.stem + ".timeline.json")).write_text(out.stdout, encoding="utf-8")
        ev = E.derive(tl)
        if "scores_only" in stats:
            gems = E.team_totals(tl, ev)["gems"]
            ok = list(gems) == list(stats["scores_only"])
            n += 1
            bad += 0 if ok else 1
            teams = (tl["meta"].get("platform") or {}).get("teams") or ["A", "B"]
            print(f"{'OK  ' if ok else 'BAD '}{f.name} [scores only] {teams[0]} vs {teams[1]} r{tl['meta']['rounds']} "
                  f"gems decoded {gems[0]}/{gems[1]} official {stats['scores_only'][0]}/{stats['scores_only'][1]}")
            continue
        ok, res = E.check_stats(tl, ev, stats)
        m = M.compute(tl, ev)
        diffs = []
        for c in stats["champions"]:
            mc = m["champions"].get(c["champion"])
            if mc is None:
                diffs.append(f"champ {c['champion']} missing"); continue
            for name, mine, off in (("banked", mc["gems"]["banked"], c["submittedGems"]), ("collected", mc["gems"]["collected"], c["collectedGems"]),
                                    ("dmg out", mc["damage_to_champs"], c["championDamageDone"]), ("dmg in", mc["damage_from_champs"], c["championDamageTaken"])):
                if abs(mine - off) > 1:
                    diffs.append(f"{mc['name']}#{c['champion']} {name} {mine} vs {off}")
        n += 1
        status = "OK  " if ok else "BAD "
        if not ok:
            bad += 1
        meta = tl["meta"]
        teams = (meta.get("platform") or {}).get("teams") or ["A", "B"]
        print(f"{status}{f.name} [{src}] {teams[0]} vs {teams[1]} r{meta['rounds']} "
              f"gems {res['gems'][0]}/{res['gems'][1]} kills {res['kills'][0]}/{res['kills'][1]} deaths {res['deaths'][0]}/{res['deaths'][1]}"
              f" | champion stats: {'all match' if not diffs else '; '.join(diffs[:6])}")
    print(f"{n - bad}/{n} games match official gems, kills and deaths")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
