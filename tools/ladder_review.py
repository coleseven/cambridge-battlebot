"""Review our platform matches (downloaded with `cambc matches summary --json` + `cambc matches download`):
result, gems, kills/deaths, faults, bot stdout errors and engine fuel per game, plus totals per opponent and map.

  .venv/Scripts/cambc matches list --kind unranked --team <our team id> --limit 60   (ids of finished matches)
  .venv/Scripts/python tools/ladder_review.py matches/unranked-1019 [--team 777]
The folder holds <id>.json (summary) and <id> (zip with team-a/b .stdout/.fuel). Our side is found by team name
in the summary roster when present, else by our draft (the first picks of our composition).
"""
import argparse
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fuel_engine as fe  # noqa: E402

OUR_PICKS = [9, 3, 4, 8, 10]      # Varg, C&I, Stellar, Ren, Tavi (draft.cpp COMPS[0])


def sides(d):
    """-> {"teamA": [picks], "teamB": [picks]} from the summary roster."""
    out = defaultdict(list)
    for p in d.get("roster", {}).get("draft", []):
        out[p["team"]].append(int(p.get("pick", p.get("champion", -1))) if isinstance(p.get("pick", p.get("champion")), (int, str)) else -1)
    return out


def team_stats(d, side):
    k = de = 0
    for c in d.get("champions", []):
        if c.get("team") != side:
            continue
        m = c.get("metrics", {})
        k += int(m.get("kills", 0))
        de += int(m.get("deaths", 0))
    return k, de


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--names", default="", help="file with `cambc matches list` output (opponent names)")
    a = ap.parse_args()
    folder = Path(a.folder)
    names = {}
    if a.names:
        cur = None
        for line in Path(a.names).read_text(encoding="utf-8").splitlines():
            if not line.startswith(" "):
                cur = line.split()[0]
            elif "777" not in line.split()[1:2] and cur:
                names[cur] = " ".join(line.split()[1:-1])
    rows = []
    for js in sorted(folder.glob("*.json")):
        mid = js.stem
        d = json.loads(js.read_text(encoding="utf-8"))
        m = d["match"]
        draft = d.get("roster", {}).get("draft", [])
        if not draft:
            continue
        by = defaultdict(list)
        for p in draft:
            by[p["team"]].append(p)
        num = lambda v: int(re.search(r"(\d+)", str(v)).group(1))   # noqa: E731  ("Unknown draft champion (9)")
        picks = {t: [num(p["champion"]) for p in ps] for t, ps in by.items()}
        mine = [t for t, ps in picks.items() if ps == OUR_PICKS[:len(ps)]]
        if len(mine) != 1:
            continue                      # mirror or not our draft: skip rather than guess
        ours = mine[0]
        them = "teamB" if ours == "teamA" else "teamA"
        gems = {t["team"]: int(t["submittedGems"]) for t in d["teams"]}
        won = m["outcome"] == ours
        k, de = team_stats(d, ours)
        ek, ed = team_stats(d, them)
        faults = d["diagnostics"]["botFaults"].get(ours, 0)
        err_lines, fuel = [], []
        z = folder / mid
        if z.exists():
            try:
                with zipfile.ZipFile(z) as zz:
                    t = "a" if ours == "teamA" else "b"
                    out = zz.read(f"team-{t}.stdout").decode("utf-8", "replace").replace("\x00", "")
                    err_lines = [l.strip() for l in out.splitlines() if "error" in l.lower() or "ERR" in l]
                    fuel = fe.fuel_values(zz.read(f"team-{t}.fuel"))[1]
            except (KeyError, zipfile.BadZipFile, ValueError):
                pass
        n = len(picks[ours])
        play = fuel[n + 1 + n:] if fuel else []     # skip draft turns and first turns (approximate: C&I = 2 bodies)
        rows.append({"id": mid[:8], "map": d.get("map", m.get("mapHash", "")[:6]), "opp": names.get(mid, "?"),
                     "size": n, "won": won, "ours": gems[ours], "theirs": gems[them], "rounds": m["roundsStarted"],
                     "k": k, "d": de, "faults": faults, "errors": len(err_lines), "err": err_lines[:2],
                     "fuel_max": max(play) if play else 0, "fuel_90k": sum(x > 90000 for x in play),
                     "turns": len(play), "engine": m.get("engineVersion")})
    for r in rows:
        print(f"{r['id']} {r['opp'][:22]:22} size{r['size']} {'WIN ' if r['won'] else 'loss'} {r['ours']:5}-{r['theirs']:<5} "
              f"r{r['rounds']:5} K{r['k']:3} D{r['d']:3} faults {r['faults']} errs {r['errors']} "
              f"fuel max {r['fuel_max']:6} >90k {r['fuel_90k']}")
    print(f"\n{len(rows)} games, {sum(r['won'] for r in rows)} wins; engine {sorted(set(r['engine'] for r in rows))}")
    by_opp = defaultdict(list)
    for r in rows:
        by_opp[r["opp"]].append(r)
    for o, rs in sorted(by_opp.items()):
        print(f"  {o[:28]:28} {sum(r['won'] for r in rs):2}/{len(rs)}  gem margin {sum(r['ours'] - r['theirs'] for r in rs) / len(rs):+7.1f}"
              f"  deaths {sum(r['d'] for r in rs) / len(rs):5.1f} vs kills {sum(r['k'] for r in rs) / len(rs):5.1f}")
    by_size = defaultdict(list)
    for r in rows:
        by_size[r["size"]].append(r)
    for s, rs in sorted(by_size.items()):
        print(f"  size {s}: {sum(r['won'] for r in rs)}/{len(rs)}")
    errs = [e for r in rows for e in r["err"]]
    print(f"faults {sum(r['faults'] for r in rows)}, stdout error lines {sum(r['errors'] for r in rows)}: {errs[:6]}")
    print(f"turns over 90k fuel: {sum(r['fuel_90k'] for r in rows)} of {sum(r['turns'] for r in rows)}")
    (folder / "review.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
