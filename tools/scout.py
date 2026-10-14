"""Scout the public CAMBC platform: pull finished match groups for the top-N ladder teams
(official per-match statistics, no replays) and summarise what strong teams do.

Usage: .venv/Scripts/python tools/scout.py [--top 15] [--pages 4] [--out scouting]
Cached JSON lands in scouting/groups/; re-runs only fetch new groups.
"""
import argparse
import collections
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://2026a.battlecode.co.uk/api/v1"
CHAMPS = ["Ryth", "Vesper", "Vendetta", "C&I", "Stellar", "Prism", "Marcella", "Willow", "Ren", "Varg", "Tavi"]


def get(path):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(BASE + path, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))


def team_groups(team, pages):
    groups, before = [], None
    for _ in range(pages):
        q = {"scope": "all", "team": team, **({"before": before} if before else {})}
        d = get("/match-groups?" + urllib.parse.urlencode(q))
        page = d.get("groups", [])
        groups += page
        if not d.get("more") or not page:
            break
        before = f"{page[-1]['createdAt']}_{page[-1]['id']}"
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--pages", type=int, default=4)
    ap.add_argument("--out", default="scouting")
    a = ap.parse_args()
    out = Path(a.out)
    gdir = out / "groups"
    gdir.mkdir(parents=True, exist_ok=True)

    ladder = get(f"/ladder?page=1&pageSize={a.top}")
    (out / "ladder.json").write_text(json.dumps(ladder, ensure_ascii=False), encoding="utf-8")
    top = {r["teamId"]: r["name"] for r in ladder["teams"][: a.top]}

    for tid in top:
        for g in team_groups(tid, a.pages):
            p = gdir / f"{g['id']}.json"
            if g.get("status") == "finished" and not p.exists():
                p.write_text(json.dumps(get(f"/match-groups/{g['id']}"), ensure_ascii=False), encoding="utf-8")

    # One row per (top team, match).
    rows = []
    for p in gdir.glob("*.json"):
        for m in json.loads(p.read_text(encoding="utf-8"))["matches"]:
            s = m.get("statistics")
            if m.get("status") != "finished" or not s:
                continue
            for i, side in enumerate(m["sides"]):
                if side["teamId"] in top:
                    opp = m["sides"][1 - i]
                    rows.append({"team": top[side["teamId"]], "map": m["map"], "rounds": m["rounds"],
                                 "won": side["result"] == "win", "score": side["score"], "opp_score": opp["score"],
                                 "opp": opp["teamName"], "draft": s["teams"][i]["draftPicks"],
                                 "champs": [c for c in s["champions"] if c["team"] == i]})
    print(f"{len(rows)} top-team match rows from {len(list(gdir.glob('*.json')))} groups\n")

    picks, first, wins_by = collections.Counter(), collections.Counter(), collections.Counter()
    for r in rows:
        for c in r["draft"]:
            picks[c] += 1
            wins_by[c] += r["won"]
        if r["draft"]:
            first[r["draft"][0]] += 1
    print("Champion pick rate among top teams (picks, first-pick, win% when picked):")
    for c, n in picks.most_common():
        print(f"  {CHAMPS[c]:9} {n:4}  first {first[c]:3}  win {100 * wins_by[c] / n:4.0f}%")

    print("\nPer map (top-team games): n, median rounds of wins, mean score for/against")
    by_map = collections.defaultdict(list)
    for r in rows:
        by_map[r["map"]].append(r)
    for mp, rs in sorted(by_map.items()):
        wr = sorted(x["rounds"] for x in rs if x["won"])
        med = wr[len(wr) // 2] if wr else "-"
        print(f"  {mp:12} n={len(rs):3}  win-rounds median {med}  score {sum(x['score'] for x in rs) / len(rs):6.0f}"
              f" vs {sum(x['opp_score'] for x in rs) / len(rs):6.0f}")

    print("\nPer team: games, win%, mean kills, deaths, gold earned per champion, most common draft")
    by_team = collections.defaultdict(list)
    for r in rows:
        by_team[r["team"]].append(r)
    for t, rs in sorted(by_team.items(), key=lambda kv: -sum(x["won"] for x in kv[1]) / len(kv[1])):
        ch = [c for x in rs for c in x["champs"]]
        k = sum(c["kills"] for c in ch) / len(rs)
        d = sum(c["deaths"] for c in ch) / len(rs)
        gold = sum(c["earnedGold"] for c in ch) / max(len(ch), 1)
        dr = collections.Counter(tuple(CHAMPS[c] for c in x["draft"]) for x in rs).most_common(1)[0]
        print(f"  {t[:22]:22} n={len(rs):3} win {100 * sum(x['won'] for x in rs) / len(rs):3.0f}%  K {k:4.1f} D {d:4.1f}"
              f"  gold/champ {gold:5.0f}  draft {'/'.join(dr[0])} x{dr[1]}")

    # Item purchase profile: which item ids top teams buy most.
    items = collections.Counter()
    for r in rows:
        for c in r["champs"]:
            for idx, n in enumerate(c["itemsBought"]):
                items[idx] += n
    print("\nMost-bought item ids (see cambc::item_type order):", items.most_common(12))
    cons = collections.Counter()
    for r in rows:
        for c in r["champs"]:
            for idx, n in enumerate(c["consumablesBought"]):
                cons[idx] += n
    print("Consumables bought (index order as in stats):", dict(cons))


if __name__ == "__main__":
    main()
