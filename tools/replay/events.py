"""Derive game events from a decoded replay timeline (tools/replay/decode.mjs output).

Usage:
  python tools/replay/events.py <timeline.json|replay.repl26a|bundle.zip> [-o events.json]
  python tools/replay/events.py <...> --check-stats stats.json   # compare with official statistics

Events (schema in tools/replay/README.md): death, kill, engagement, camp_kill, capture, deposit,
recall, purchase, sale, ward, altar_lock, first. Replays are decoded on the fly when given a
.repl26a/.zip (needs node on PATH).
"""
import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAMILIES = ["Monster", "Power", "Durability", "Treasure", "Jungle", "Home", "Bruiser"]
ITEMS = [f"{fam} {kind} of {stat}" for fam in FAMILIES for stat in ("Strength", "Focus", "Health") for kind in ("Shard", "Emblem")]
CONSUMABLES = ["teleport_crystal", "altar_lock", "consumable2", "consumable3"]
ENGAGE_GAP = 3        # rounds without a champion-on-champion hit that closes an engagement
DEATH_GRACE = 2       # deaths this many rounds after the last hit still belong to the engagement


def load_timeline(path):
    p = Path(path)
    if p.suffix.lower() in (".zip", ".repl26a"):
        out = subprocess.run(["node", str(HERE / "decode.mjs"), str(p)], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)
    return json.loads(p.read_text(encoding="utf-8"))


class Game:
    """Convenience view over a timeline: column access and per-round lookups."""

    def __init__(self, tl):
        self.tl = tl
        self.cols = {c: i for i, c in enumerate(tl["champion_cols"])}
        self.champs = tl["champions"] or []
        self.team_of = {c["id"]: c["team"] for c in self.champs}
        self.name_of = {c["id"]: c["name"] for c in self.champs}
        self.frames = tl["frames"]
        self.by_round = {f["r"]: f for f in self.frames}
        # Carry forward delta-encoded group/ward rows.
        g = w = None
        for f in self.frames:
            g = f.get("g", g); w = f.get("w", w)
            f["_g"], f["_w"] = g, w
        self.rounds = tl["meta"]["rounds"]
        self.camp_group = {c["id"]: grp["id"] for grp in tl["groups"] or [] for c in grp["camps"]}
        self.group_tiles = {grp["id"]: {(c["x"], c["y"]) for c in grp["camps"]} for grp in tl["groups"] or []}
        self.group_types = {grp["id"]: sorted({c["type"] for c in grp["camps"]}) for grp in tl["groups"] or []}

    def val(self, frame, cid, col):
        return frame["c"][self._row(cid)][self.cols[col]]

    def _row(self, cid):
        if not hasattr(self, "_rows"):
            self._rows = {c["id"]: i for i, c in enumerate(self.champs)}
        return self._rows[cid]

    def frame_at(self, r):
        """Latest frame with round <= r (frames are end-of-round states)."""
        if r in self.by_round:
            return self.by_round[r]
        best = None
        for f in self.frames:
            if f["r"] <= r:
                best = f
            else:
                break
        return best

    def label(self, cid):
        if isinstance(cid, int):
            return f"{self.name_of.get(cid, cid)}#{cid}"
        return cid


def linked_deaths(g, deaths):
    """Group body deaths that are one kill (Cassi & Issac die together)."""
    groups, seen = [], set()
    for i, d in enumerate(deaths):
        if i in seen:
            continue
        grp = [d]
        for j in range(i + 1, len(deaths)):
            e = deaths[j]
            if e["r"] != d["r"]:
                break
            if e["team"] == d["team"] and e["killer"] == d["killer"] and {g.name_of[d["victim"]], g.name_of[e["victim"]]} == {"Cassi", "Issac"}:
                grp.append(e); seen.add(j)
        groups.append(grp)
    return groups


def engagements(g, tl, deaths):
    hits = [h for h in tl["hits"] if h[4] >= 0 and isinstance(h[1], int) and g.team_of.get(h[1]) is not None and g.team_of[h[1]] != g.team_of[h[2]]]
    open_, done = [], []
    for r, src, dst, amt, _kind in hits:
        for e in list(open_):
            if r - e["end"] > ENGAGE_GAP:
                open_.remove(e); done.append(e)
        hit_set = {src, dst}
        joins = [e for e in open_ if e["ids"] & hit_set]
        if not joins:
            e = {"start": r, "end": r, "ids": set(), "dmg": defaultdict(float), "taken": defaultdict(float), "first": g.team_of[src], "hits": 0}
            open_.append(e)
        else:
            e = joins[0]
            for o in joins[1:]:  # a hit bridging two fights merges them
                e["start"] = min(e["start"], o["start"]); e["ids"] |= o["ids"]; e["hits"] += o["hits"]
                for k, v in o["dmg"].items(): e["dmg"][k] += v
                for k, v in o["taken"].items(): e["taken"][k] += v
                open_.remove(o)
        e["end"] = r; e["ids"] |= hit_set; e["hits"] += 1
        e["dmg"][src] += amt; e["taken"][dst] += amt
    done += open_
    out = []
    for e in sorted(done, key=lambda e: e["start"]):
        ids = sorted(e["ids"])
        lo, hi = e["start"], e["end"] + DEATH_GRACE
        dd = [d for d in deaths if lo <= d["r"] <= hi and d["victim"] in e["ids"]]
        kills = [0, 0]
        for grp in linked_deaths(g, dd):
            kills[1 - grp[0]["team"]] += 1
        dmg = [0.0, 0.0]
        for cid, v in e["dmg"].items():
            dmg[g.team_of[cid]] += v
        stuns = [0, 0]
        for r, src, dst, _dur in tl["stuns"]:
            if lo <= r <= hi and isinstance(src, int) and src in e["ids"] and dst in e["ids"]:
                stuns[g.team_of[src]] += 1
        # Winner: more kills; otherwise clearly more damage (>25% more) wins; else a draw.
        if kills[0] != kills[1]:
            win = 0 if kills[0] > kills[1] else 1
        elif max(dmg) > 0 and min(dmg) < 0.8 * max(dmg):
            win = 0 if dmg[0] > dmg[1] else 1
        else:
            win = None
        # Focus: share of each team's damage that went to its single most-hit target.
        tgt = [defaultdict(float), defaultdict(float)]
        for h in hits:
            if lo <= h[0] <= e["end"] and h[1] in e["ids"] and h[2] in e["ids"]:
                tgt[g.team_of[h[1]]][h[2]] += h[3]
        focus = [round(max(t.values()) / sum(t.values()), 2) if t else None for t in tgt]
        end_hp = {}
        f = g.frame_at(e["end"])
        if f:
            for cid in ids:
                mh = g.val(f, cid, "max_hp")
                end_hp[cid] = round(g.val(f, cid, "hp") / mh, 2) if mh else 0
        out.append({"type": "engagement", "r": lo, "end": e["end"], "duration": e["end"] - lo + 1,
                    "teams": [[c for c in ids if g.team_of[c] == t] for t in (0, 1)], "first_hit": e["first"],
                    "damage": [round(x) for x in dmg], "kills": kills, "stuns": stuns, "focus": focus,
                    "hits": e["hits"], "winner": win, "end_hp": end_hp})
    return out


def channels(g, tl, deaths):
    """Deposits and recalls from start actions + per-round channel state."""
    acts = tl["acts"]
    death_rounds = defaultdict(list)
    for d in deaths:
        death_rounds[d["victim"]].append(d["r"])
    subm = defaultdict(list)
    for a in acts:
        if a[1] == "gems_submitted":
            subm[a[2]].append((a[0], a[3]))
    stunned = defaultdict(set)
    for r, _s, dst, _d in tl["stuns"]:
        stunned[dst].add(r)
    damaged = defaultdict(set)
    for h in tl["hits"]:
        damaged[h[2]].add(h[0])
    out = []
    starts = [(a[0], a[1], a[2]) for a in acts if a[1] in ("submit_gems", "recall")]
    by_champ = defaultdict(list)
    for r, kind, cid in starts:
        by_champ[cid].append((r, kind))
    cancels = defaultdict(list)
    for a in acts:
        if a[1] == "cancel_channel":
            cancels[a[2]].append(a[0])
    for cid, lst in by_champ.items():
        for i, (r, kind) in enumerate(lst):
            nxt = lst[i + 1][0] if i + 1 < len(lst) else g.rounds + 1
            f0 = g.frame_at(r)
            gems = g.val(f0, cid, "gems") if f0 else None
            if kind == "submit_gems":
                done = [s for s in subm[cid] if r <= s[0] <= nxt]
                ev = {"type": "deposit", "r": r, "champ": cid, "team": g.team_of[cid], "gems_at_start": gems}
                if done:
                    ev.update(finish=done[0][0], size=done[0][1], completed=True)
                else:
                    ev.update(completed=False, finish=None, size=0, reason=_interrupt(cid, r, nxt, death_rounds, stunned, cancels, damaged))
                out.append(ev)
            else:
                # Recall completes when the champion reappears far away (at spawn) while alive.
                end = r
                while end + 1 < nxt and (fr := g.by_round.get(end + 1)) and g.val(fr, cid, "channel") == "R":
                    end += 1
                fa, fb = g.by_round.get(end), g.by_round.get(end + 1)
                completed = False
                if fa and fb and g.val(fb, cid, "alive"):
                    dx = abs(g.val(fa, cid, "x") - g.val(fb, cid, "x")) + abs(g.val(fa, cid, "y") - g.val(fb, cid, "y"))
                    completed = dx > 2 or (g.val(fa, cid, "channel") == "R" and g.val(fb, cid, "channel") is None and dx > 0)
                ev = {"type": "recall", "r": r, "champ": cid, "team": g.team_of[cid], "completed": completed, "end": end + 1}
                if not completed:
                    ev["reason"] = _interrupt(cid, r, nxt, death_rounds, stunned, cancels, damaged)
                out.append(ev)
    return out


def _interrupt(cid, r, nxt, death_rounds, stunned, cancels, damaged):
    if any(r <= d <= nxt for d in death_rounds[cid]):
        return "death"
    if any(r <= s <= nxt for s in stunned[cid]):
        return "stun"
    if any(r <= c <= nxt for c in cancels[cid]):
        return "cancelled"
    if any(r <= c <= nxt for c in damaged[cid]):
        return "damage"
    return "other"


def derive(tl):
    g = Game(tl)
    ev = []
    deaths = []
    for d in tl["deaths"]:
        e = {"type": "death", "r": d["r"], "victim": d["victim"], "team": d["team"], "killer": d["killer"], "by": d["by"],
             "gems_carried": d["gems"], "gems_lost": d["gems"], "gems_to_killer": d.get("gems_to_killer", 0), "pos": d["pos"]}
        deaths.append(e)
    ev += deaths
    for grp in linked_deaths(g, deaths):
        d = grp[0]
        if d["by"] == "champion":
            ev.append({"type": "kill", "r": d["r"], "killer": d["killer"], "team": g.team_of[d["killer"]], "victims": [x["victim"] for x in grp],
                       "gems": sum(x["gems_carried"] for x in grp)})
    ev += engagements(g, tl, deaths)
    for r, camp, grp, killer in tl.get("camp_kills", []):
        ev.append({"type": "camp_kill", "r": r, "camp": camp, "group": grp, "killer": killer,
                   "team": g.team_of.get(killer) if isinstance(killer, int) else None})
    for c in tl["captures"]:
        ev.append({"type": "capture", "r": c["r"], "group": c["group"], "team": c["team"], "champs": c["champs"],
                   "reward": {"gold": c["gold"], "gems": c["gems"]}, "split": {int(k): v for k, v in (c.get("split") or {}).items()},
                   "camps": g.group_types.get(c["group"])})
    ev += channels(g, tl, deaths)
    for p in tl["purchases"]:
        items = [[ITEMS[i] if i < len(ITEMS) else i, n] for i, n in p["items"]]
        cons = [[CONSUMABLES[i], n] for i, n in p["consumables"]]
        ev.append({"type": "sale" if p["sell"] else "purchase", "r": p["r"], "champ": p["ch"], "team": g.team_of.get(p["ch"]),
                   "items": items, "consumables": cons, "gold": p["gold"]})
    for a in tl["acts"]:
        if a[1] == "place_ward":
            ev.append({"type": "ward", "r": a[0], "champ": a[2], "team": g.team_of[a[2]], "pos": a[3:5]})
    for l in tl["altar_locks"]:
        ev.append({"type": "altar_lock", "r": l["r"], "champ": l["ch"], "team": l["team"], "pos": [l["x"], l["y"]]})
    ev.sort(key=lambda e: (e["r"], e["type"]))
    # first-of-kind per team
    firsts, seen = [], set()
    for e in ev:
        t = e.get("team")
        kind = e["type"]
        if kind == "deposit" and not e.get("completed"):
            continue
        if kind in ("kill", "death", "camp_kill", "capture", "deposit", "purchase", "ward", "altar_lock", "recall") and t in (0, 1):
            if (kind, t) not in seen:
                seen.add((kind, t))
                champ = e.get("champ", e.get("killer", e.get("victim")))
                if champ is None and e.get("champs"):
                    champ = e["champs"][0]
                firsts.append({"type": "first", "r": e["r"], "of": kind, "team": t, "champ": champ})
    if any(e["type"] == "kill" for e in ev):
        fb = next(e for e in ev if e["type"] == "kill")
        firsts.append({"type": "first", "r": fb["r"], "of": "first_blood", "team": fb["team"], "champ": fb["killer"]})
    ev += firsts
    ev.sort(key=lambda e: (e["r"], e["type"]))
    return ev


def team_totals(tl, ev):
    g = Game(tl)
    last = tl["frames"][-1]
    kills, deaths = [0, 0], [0, 0]
    ev_k, ev_d = [0, 0], [0, 0]
    for c in g.champs:
        kills[c["team"]] += g.val(last, c["id"], "kills")
        deaths[c["team"]] += g.val(last, c["id"], "deaths")
    for e in ev:
        if e["type"] == "death":
            ev_d[e["team"]] += 1
        elif e["type"] == "kill":
            ev_k[e["team"]] += 1
    return {"gems": tl["meta"]["final_gems"], "kda_kills": kills, "kda_deaths": deaths, "event_kills": ev_k, "event_deaths": ev_d}


def check_stats(tl, ev, stats):
    """Compare decoded totals with an official statistics JSON (cambc run --statistics / platform)."""
    tot = team_totals(tl, ev)
    off_g = [t["submittedGems"] for t in sorted(stats["teams"], key=lambda t: t["team"])]
    off_k, off_d = [0, 0], [0, 0]
    for c in stats["champions"]:
        off_k[c["team"]] += c["kills"]; off_d[c["team"]] += c["deaths"]
    res = {"gems": (tot["gems"], off_g), "kills": (tot["event_kills"], off_k), "deaths": (tot["event_deaths"], off_d)}
    ok = all(list(a) == list(b) for a, b in res.values())
    return ok, res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--out")
    ap.add_argument("--check-stats", help="official statistics JSON to compare gems/kills/deaths against")
    a = ap.parse_args()
    tl = load_timeline(a.input)
    ev = derive(tl)
    rc = 0
    if a.check_stats or (tl["meta"].get("platform") or {}).get("statistics"):
        stats = json.loads(Path(a.check_stats).read_text()) if a.check_stats else tl["meta"]["platform"]["statistics"]
        ok, res = check_stats(tl, ev, stats)
        print(("OK  " if ok else "MISMATCH ") + " ".join(f"{k} decoded={v[0]} official={v[1]}" for k, v in res.items()), file=sys.stderr)
        if not ok:
            rc = 1
    js = json.dumps(ev, separators=(",", ":"))
    if a.out:
        Path(a.out).write_text(js, encoding="utf-8")
    elif not a.check_stats:
        print(js)
    sys.exit(rc)


if __name__ == "__main__":
    main()
