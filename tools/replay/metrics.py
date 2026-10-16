"""Per-champion and per-team metrics from a decoded timeline plus its derived events.

Usage:
  python tools/replay/metrics.py <timeline.json|replay.repl26a|bundle.zip> [-o metrics.json] [--early 150]

Metrics (schema in tools/replay/README.md):
  early (first N rounds, default 150): first camp killed / captured / deposited, first shop, unspent gold,
    carried and banked gems at round N;
  time budget (rounds): moving, clearing, capturing, channelling, fighting, dead, idle;
  gems collected / banked / lost; engagements won / lost / drawn; kills, deaths, assists;
  champion damage dealt and taken.
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

try:
    from . import events as E  # type: ignore
except ImportError:
    import events as E  # noqa: E402

BUDGET = ["moving", "clearing", "capturing", "channelling", "fighting", "dead", "idle"]


def activity(g, tl):
    """Classify every champion's every round into one BUDGET bucket. Returns {cid: [(round, bucket)]}."""
    clear_r, fight_r = defaultdict(set), defaultdict(set)
    for a in tl["acts"]:
        if a[1] in ("attack", "cast") and len(a) > 3 and isinstance(a[3], str) and a[3].startswith("m"):
            clear_r[a[2]].add(a[0])
    for r, src, dst, _amt, _k in tl["hits"]:
        if isinstance(src, int) and g.team_of.get(src) != g.team_of.get(dst):
            fight_r[src].add(r); fight_r[dst].add(r)
    out = defaultdict(list)
    prev = None
    for f in g.frames:
        groups = f.get("_g") or []
        claim_tiles = set()
        for gi, row in enumerate(groups):
            if row[0] == 2:  # WaitingForRewardClaim
                claim_tiles |= g.group_tiles.get(gi, set())
        for c in g.champs:
            cid = c["id"]
            row = f["c"][g._row(cid)]
            x, y, alive, ch = row[g.cols["x"]], row[g.cols["y"]], row[g.cols["alive"]], row[g.cols["channel"]]
            r = f["r"]
            if not alive:
                b = "dead"
            elif ch:
                b = "channelling"
            elif r in fight_r[cid]:
                b = "fighting"
            elif r in clear_r[cid]:
                b = "clearing"
            elif (x, y) in claim_tiles:
                b = "capturing"
            elif prev is not None and prev["c"][g._row(cid)][:2] != [x, y]:
                b = "moving"
            else:
                b = "idle"
            out[cid].append((r, b))
        prev = f
    return out


def compute(tl, ev, early=150):
    g = E.Game(tl)
    last = tl["frames"][-1]
    fe = g.frame_at(early) or last
    act = activity(g, tl)
    champs, teams = {}, [defaultdict(float) for _ in range(2)]
    dmg_done, dmg_taken = Counter(), Counter()
    for r, src, dst, amt, k in tl["hits"]:
        if k >= 0 and isinstance(src, int) and g.team_of.get(src) != g.team_of.get(dst):
            dmg_done[src] += amt; dmg_taken[dst] += amt

    def first(pred):
        for e in ev:
            if pred(e):
                return e["r"]
        return None

    for c in g.champs:
        cid, t = c["id"], c["team"]
        budget = Counter(b for _, b in act[cid])
        collected = sum(e["split"].get(cid, [0, 0])[1] for e in ev if e["type"] == "capture")
        stolen = sum(e["gems_carried"] for e in ev if e["type"] == "death" and e["killer"] == cid and e["by"] == "champion")
        banked = sum(e["size"] for e in ev if e["type"] == "deposit" and e["champ"] == cid and e["completed"])
        lost = sum(e["gems_lost"] for e in ev if e["type"] == "death" and e["victim"] == cid)
        eng = Counter()
        for e in ev:
            if e["type"] == "engagement" and cid in e["teams"][t]:
                eng["won" if e["winner"] == t else "drawn" if e["winner"] is None else "lost"] += 1
        m = {
            "name": c["name"], "team": t,
            "early": {
                "first_camp_kill": first(lambda e: e["type"] == "camp_kill" and e["killer"] == cid),
                "first_capture": first(lambda e: e["type"] == "capture" and cid in e["champs"]),
                "first_deposit": first(lambda e: e["type"] == "deposit" and e["champ"] == cid and e["completed"]),
                "first_shop": first(lambda e: e["type"] == "purchase" and e["champ"] == cid),
                f"gold_unspent_r{early}": g.val(fe, cid, "gold"),
                f"gems_carried_r{early}": g.val(fe, cid, "gems"),
                f"gems_banked_r{early}": sum(e["size"] for e in ev if e["type"] == "deposit" and e["champ"] == cid and e["completed"] and e["finish"] <= early),
            },
            "time": {b: budget.get(b, 0) for b in BUDGET},
            "gems": {"collected": collected + stolen, "from_camps": collected, "from_kills": stolen, "banked": banked, "lost": lost,
                     "carried_end": g.val(last, cid, "gems")},
            "engagements": {k: eng.get(k, 0) for k in ("won", "lost", "drawn")},
            "kills": g.val(last, cid, "kills"), "deaths": g.val(last, cid, "deaths"), "assists": g.val(last, cid, "assists"),
            "damage_to_champs": round(dmg_done[cid]), "damage_from_champs": round(dmg_taken[cid]),
            "gold_end": g.val(last, cid, "gold"), "earned_gold": g.val(last, cid, "earned_gold"),
            "deposits": {"completed": sum(1 for e in ev if e["type"] == "deposit" and e["champ"] == cid and e["completed"]),
                         "interrupted": sum(1 for e in ev if e["type"] == "deposit" and e["champ"] == cid and not e["completed"])},
            "recalls": {"completed": sum(1 for e in ev if e["type"] == "recall" and e["champ"] == cid and e["completed"]),
                        "cancelled": sum(1 for e in ev if e["type"] == "recall" and e["champ"] == cid and not e["completed"])},
        }
        champs[cid] = m
        T = teams[t]
        for k in ("kills", "deaths", "assists", "damage_to_champs", "damage_from_champs", "earned_gold"):
            T[k] += m[k]
        for k, v in m["gems"].items():
            T["gems_" + k] += v
        for k, v in m["time"].items():
            T["time_" + k] += v
    team_out = []
    for t in (0, 1):
        T = {k: int(v) for k, v in teams[t].items()}
        # Linked C&I deaths are one kill; team kills come from kill events, deaths are bodies.
        T["kills"] = sum(1 for e in ev if e["type"] == "kill" and e["team"] == t)
        T["banked"] = tl["meta"]["final_gems"][t]
        T["engagements"] = {k: sum(1 for e in ev if e["type"] == "engagement" and (
            (k == "won" and e["winner"] == t) or (k == "lost" and e["winner"] == 1 - t) or (k == "drawn" and e["winner"] is None)))
            for k in ("won", "lost", "drawn")}
        T["early"] = {
            "first_camp_kill": first(lambda e: e["type"] == "camp_kill" and e["team"] == t),
            "first_capture": first(lambda e: e["type"] == "capture" and e["team"] == t),
            "first_deposit": first(lambda e: e["type"] == "deposit" and e["team"] == t and e["completed"]),
            "first_shop": first(lambda e: e["type"] == "purchase" and e["team"] == t),
            f"gold_unspent_r{early}": sum(g.val(fe, c["id"], "gold") for c in g.champs if c["team"] == t),
            f"banked_r{early}": fe["sg"][t],
            "captures": sum(1 for e in ev if e["type"] == "capture" and e["team"] == t and e["r"] <= early),
        }
        T["captures"] = sum(1 for e in ev if e["type"] == "capture" and e["team"] == t)
        team_out.append(T)
    return {"early_rounds": early, "teams": team_out, "champions": champs}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--out")
    ap.add_argument("--early", type=int, default=150)
    a = ap.parse_args()
    tl = E.load_timeline(a.input)
    m = compute(tl, E.derive(tl), a.early)
    js = json.dumps(m, indent=1)
    if a.out:
        Path(a.out).write_text(js, encoding="utf-8")
    else:
        print(js)


if __name__ == "__main__":
    main()
