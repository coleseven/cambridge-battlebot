"""Game reports from replays: a self-contained HTML page, a two-game comparison, and a compact
LLM-ready summary.json.

Usage:
  python tools/replay/report.py game <replay|bundle|timeline.json> [-o report.html] [--summary summary.json]
  python tools/replay/report.py compare <game A> <game B> [-o compare.html]
  python tools/replay/report.py summary <replay|bundle|timeline.json>... [-o summary.json | --out-dir DIR]

Inputs may be .repl26a / .zip (decoded with node) or timeline JSON from decode.mjs.
"""
import argparse
import html
import json
import re
from pathlib import Path

try:
    from . import events as E, metrics as M  # type: ignore
except ImportError:
    import events as E  # noqa: E402
    import metrics as M  # noqa: E402

TEAM = "AB"
COLORS = {"moving": "#7aa6d6", "clearing": "#d9a441", "capturing": "#6fbf73", "channelling": "#a77bd1",
          "fighting": "#e0574f", "dead": "#3a3a3a", "idle": "#cfcfcf"}
TEAM_COL = ["#2f6fdb", "#d9452f"]


def analyse(path):
    tl = E.load_timeline(path)
    ev = E.derive(tl)
    m = M.compute(tl, ev)
    return tl, ev, m


def team_names(tl):
    p = tl["meta"].get("platform") or {}
    if p.get("teams"):
        return [p["teams"][0], p["teams"][1]]
    mm = re.match(r"(.+?)-vs-(.+?)-\d+\.", tl["meta"].get("file", ""))
    return [mm.group(1), mm.group(2)] if mm else ["Team A", "Team B"]


def who(g, cid):
    if isinstance(cid, int):
        return f"{TEAM[g.team_of[cid]]}:{g.name_of[cid]}"
    if isinstance(cid, str) and cid.startswith("m"):
        return "camp"
    return "?"


def describe(g, e):
    """One short line per event (also used in summary.json)."""
    t = e["type"]
    if t == "kill":
        v = "+".join(g.name_of[x] for x in e["victims"])
        return f"{who(g, e['killer'])} killed {TEAM[1 - e['team']]}:{v}" + (f" (+{e['gems']} gems)" if e["gems"] else "")
    if t == "death":
        return f"{who(g, e['victim'])} died to {who(g, e['killer']) if e['by'] != 'unknown' else '?'}" + (f", lost {e['gems_lost']} gems" if e["gems_lost"] else "")
    if t == "engagement":
        a = "+".join(g.name_of[x] for x in e["teams"][0]); b = "+".join(g.name_of[x] for x in e["teams"][1])
        res = "draw" if e["winner"] is None else f"{TEAM[e['winner']]} won"
        return f"fight {e['duration']}r A[{a}] vs B[{b}] dmg {e['damage'][0]}-{e['damage'][1]} kills {e['kills'][0]}-{e['kills'][1]} stuns {e['stuns'][0]}-{e['stuns'][1]}: {res}"
    if t == "capture":
        return f"{TEAM[e['team']]} captured group {e['group']} ({'/'.join(e['camps'] or [])}) +{e['reward']['gems']}g +{e['reward']['gold']}$"
    if t == "deposit":
        if e["completed"]:
            return f"{who(g, e['champ'])} banked {e['size']} gems ({e['finish'] - e['r'] + 1}r)"
        return f"{who(g, e['champ'])} deposit of {e['gems_at_start']} interrupted ({e.get('reason')})"
    if t == "recall":
        return f"{who(g, e['champ'])} recall {'done' if e['completed'] else 'cancelled (' + str(e.get('reason')) + ')'}"
    if t in ("purchase", "sale"):
        its = ", ".join(f"{n:+d} {i}" for i, n in e["items"] + e["consumables"])
        return f"{who(g, e['champ'])} {'sold' if t == 'sale' else 'bought'} {its} ({e['gold']:+d}$)"
    if t == "camp_kill":
        return f"{who(g, e['killer'])} killed camp {e['camp']} (group {e['group']})"
    if t == "ward":
        return f"{who(g, e['champ'])} warded {e['pos']}"
    if t == "altar_lock":
        return f"{who(g, e['champ'])} placed altar lock at {e['pos']}"
    if t == "first":
        return f"first {e['of']}: {TEAM[e['team']]}" + (f" ({g.name_of.get(e['champ'], '')})" if isinstance(e.get("champ"), int) else "")
    return t


# ---------------- summary.json ----------------
def summary(tl, ev, m):
    g = E.Game(tl)
    meta = tl["meta"]
    names = team_names(tl)
    sg = meta["final_gems"]
    end = "1000 gems" if max(sg) >= 1000 else "round limit" if meta["rounds"] >= 2000 else "resign/other"
    frames = tl["frames"]
    curve, lead, changes = [], None, []
    for f in frames:
        if f["r"] % 50 == 0 or f is frames[-1]:
            curve.append([f["r"], f["sg"][0], f["sg"][1]])
        l = 0 if f["sg"][0] > f["sg"][1] else 1 if f["sg"][1] > f["sg"][0] else None
        if l is not None and l != lead:
            if lead is not None:
                changes.append([f["r"], TEAM[l]])
            lead = l
    pct = lambda d: {k[:4]: round(100 * v / max(1, sum(d.values()))) for k, v in d.items() if v}
    teams = []
    for t in (0, 1):
        T = m["teams"][t]
        teams.append({"name": names[t], "draft": meta["drafts"][t], "banked": sg[t], "kills": T["kills"], "deaths": T["deaths"],
                      "gems": {"camps": T["gems_from_camps"], "kills": T["gems_from_kills"], "lost": T["gems_lost"], "end_carried": T["gems_carried_end"]},
                      "captures": T["captures"], "fights": [T["engagements"]["won"], T["engagements"]["lost"], T["engagements"]["drawn"]],
                      "dmg": [T["damage_to_champs"], T["damage_from_champs"]], "gold": T["earned_gold"],
                      "early": T["early"], "time%": pct({k[5:]: v for k, v in T.items() if k.startswith("time_")})})
    champs = []
    last = frames[-1]
    for cid, c in m["champions"].items():
        items = E.Game(tl).val(last, cid, "items")
        names_ = {}
        for i in items:
            n = E.ITEMS[i] if i < len(E.ITEMS) else str(i)
            names_[n] = names_.get(n, 0) + 1
        champs.append({"id": cid, "n": c["name"], "t": TEAM[c["team"]], "kda": [c["kills"], c["deaths"], c["assists"]],
                       "gems": [c["gems"]["from_camps"], c["gems"]["from_kills"], c["gems"]["banked"], c["gems"]["lost"]],
                       "dmg": [c["damage_to_champs"], c["damage_from_champs"]], "fights": [c["engagements"]["won"], c["engagements"]["lost"], c["engagements"]["drawn"]],
                       "dep": [c["deposits"]["completed"], c["deposits"]["interrupted"]], "rec": [c["recalls"]["completed"], c["recalls"]["cancelled"]],
                       "early": {k.split("_r")[0] if "_r" in k else k: v for k, v in c["early"].items()},
                       "time%": pct(c["time"]), "items": [f"{n} x{k}" if k > 1 else n for n, k in names_.items()]})
    # key events: kills, firsts, interrupted deposits with gems, big fights; capped
    key = []
    for e in ev:
        t = e["type"]
        if t == "kill" or (t == "first" and e["of"] not in ("recall", "ward")) or t == "altar_lock" \
                or (t == "deposit" and not e["completed"] and (e["gems_at_start"] or 0) >= 20) \
                or (t == "deposit" and e["completed"] and e["size"] >= 100) \
                or (t == "engagement" and (sum(e["kills"]) or sum(e["damage"]) >= 1500)) \
                or (t == "death" and e["gems_lost"] >= 30 and e["by"] != "champion"):
            key.append(f"r{e['r']} {describe(g, e)}")
    if len(key) > 60:
        key = key[:30] + [f"... {len(key) - 60} more ..."] + key[-30:]
    notable = []
    deaths = [e for e in ev if e["type"] == "death"]
    if deaths:
        d = max(deaths, key=lambda e: e["gems_lost"])
        if d["gems_lost"]:
            notable.append(f"biggest gem loss: r{d['r']} {describe(g, d)}")
    fights = [e for e in ev if e["type"] == "engagement"]
    if fights:
        f = max(fights, key=lambda e: sum(e["damage"]))
        notable.append(f"largest fight: r{f['r']} {describe(g, f)}")
    for t in (0, 1):
        idle = m["teams"][t].get("time_idle", 0); total = sum(v for k, v in m["teams"][t].items() if k.startswith("time_"))
        if total and idle / total > 0.15:
            notable.append(f"{TEAM[t]} idle {round(100 * idle / total)}% of champion-rounds")
        held = max((g.val(f, c["id"], "gems"), f["r"], c["id"]) for f in frames for c in g.champs if c["team"] == t)
        if held[0] >= 100:
            notable.append(f"{TEAM[t]}:{g.name_of[held[2]]} carried {held[0]} unbanked gems at r{held[1]}")
    p = meta.get("platform") or {}
    return {"v": 1, "match": {"id": p.get("matchId"), "map": p.get("map"), "file": meta["file"], "rounds": meta["rounds"],
                              "winner": TEAM[meta["winner"]] if meta["winner"] is not None else None, "score": sg, "end": end},
            "legend": {"gems": "[camps, kills, banked, lost]", "dmg": "[to champs, from champs]", "fights": "[won, lost, drawn]",
                       "dep/rec": "[completed, interrupted]", "time%": "share of rounds: movi clea capt chan figh dead idle", "$": "gold"},
            "teams": teams, "champs": champs, "curve": curve, "lead_changes": changes, "key_events": key, "notable": notable}


# ---------------- HTML ----------------
CSS = """body{font:13px/1.4 system-ui,sans-serif;margin:16px;background:#fafafa;color:#222}
h1{font-size:18px}h2{font-size:15px;margin-top:22px}table{border-collapse:collapse;background:#fff}
td,th{border:1px solid #ddd;padding:2px 6px;text-align:right}th{background:#f0f0f0}td.l,th.l{text-align:left}
.A{color:#2f6fdb}.B{color:#d9452f}.legend span{display:inline-block;margin-right:10px}.legend i{display:inline-block;width:10px;height:10px;margin-right:3px}
#ev{max-height:520px;overflow:auto;display:block}.flt label{margin-right:8px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (prefers-color-scheme: dark){body{background:#1b1b1b;color:#ddd}table{background:#222}th{background:#2c2c2c}td,th{border-color:#444}}"""


def svg_curve(tl, w=900, h=220):
    fr = tl["frames"]
    R = max(1, tl["meta"]["rounds"])
    top = max(1000, max(max(f["sg"]) for f in fr))
    g = E.Game(tl)
    carried = [[sum(g.val(f, c["id"], "gems") for c in g.champs if c["team"] == t) for f in fr] for t in (0, 1)]
    X = lambda r: 40 + (w - 50) * r / R
    Y = lambda v: h - 20 - (h - 30) * v / top
    parts = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
             f'<line x1="40" y1="{Y(1000):.1f}" x2="{w - 10}" y2="{Y(1000):.1f}" stroke="#999" stroke-dasharray="4 3"/>',
             f'<text x="2" y="{Y(1000) + 4:.1f}" font-size="10">1000</text><text x="{w - 40}" y="{h - 4}" font-size="10">r{R}</text>']
    for t in (0, 1):
        pts = " ".join(f"{X(f['r']):.1f},{Y(f['sg'][t]):.1f}" for f in fr)
        parts.append(f'<polyline fill="none" stroke="{TEAM_COL[t]}" stroke-width="2" points="{pts}"/>')
        pts = " ".join(f"{X(f['r']):.1f},{Y(f['sg'][t] + carried[t][i]):.1f}" for i, f in enumerate(fr))
        parts.append(f'<polyline fill="none" stroke="{TEAM_COL[t]}" stroke-width="1" stroke-dasharray="3 2" opacity=".6" points="{pts}"/>')
    parts.append("</svg>")
    return "".join(parts)


def svg_strips(tl, ev, w=900):
    g = E.Game(tl)
    act = M.activity(g, tl)
    R = max(1, tl["meta"]["rounds"])
    row = 16
    h = row * len(g.champs) + 20
    X = lambda r: 110 + (w - 120) * r / R
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">']
    for k, c in enumerate(g.champs):
        y = 4 + k * row
        out.append(f'<text x="2" y="{y + 11}" font-size="11" fill="{TEAM_COL[c["team"]]}">{TEAM[c["team"]]} {html.escape(c["name"])}#{c["id"]}</text>')
        runs = []
        for r, b in act[c["id"]]:
            if runs and runs[-1][2] == b and runs[-1][1] == r - 1:
                runs[-1][1] = r
            else:
                runs.append([r, r, b])
        for a, b, kind in runs:
            out.append(f'<rect x="{X(a - 1):.1f}" y="{y}" width="{max(0.6, X(b) - X(a - 1)):.1f}" height="{row - 4}" fill="{COLORS[kind]}"><title>r{a}-{b} {kind}</title></rect>')
        for e in ev:
            if e["type"] == "death" and e["victim"] == c["id"]:
                out.append(f'<text x="{X(e["r"]) - 3:.1f}" y="{y + 10}" font-size="11" fill="#fff">✕</text>')
    out.append(f'<line x1="110" y1="{h - 14}" x2="{w - 10}" y2="{h - 14}" stroke="#999"/>')
    for r in range(0, R + 1, 100 if R > 500 else 50):
        out.append(f'<text x="{X(r) - 6:.1f}" y="{h - 2}" font-size="9">{r}</text>')
    out.append("</svg>")
    legend = "".join(f'<span><i style="background:{c}"></i>{k}</span>' for k, c in COLORS.items())
    return "".join(out) + f'<div class="legend">{legend}<span>✕ death</span></div>'


def metrics_table(tl, m):
    g = E.Game(tl)
    cols = ["K/D/A", "dmg out", "dmg in", "gems camps", "gems kills", "banked", "lost", "fights W/L/D", "dep ok/int", "recall ok/cx",
            "1st camp", "1st cap", "1st dep", "1st shop", "gold@150"] + [k[:5] for k in M.BUDGET]
    rows = []
    for cid, c in m["champions"].items():
        e = c["early"]
        vals = [f"{c['kills']}/{c['deaths']}/{c['assists']}", c["damage_to_champs"], c["damage_from_champs"], c["gems"]["from_camps"], c["gems"]["from_kills"],
                c["gems"]["banked"], c["gems"]["lost"], "/".join(str(c["engagements"][k]) for k in ("won", "lost", "drawn")),
                f"{c['deposits']['completed']}/{c['deposits']['interrupted']}", f"{c['recalls']['completed']}/{c['recalls']['cancelled']}",
                e["first_camp_kill"], e["first_capture"], e["first_deposit"], e["first_shop"], list(e.values())[4]] + [c["time"][k] for k in M.BUDGET]
        rows.append(f'<tr><td class="l {TEAM[c["team"]]}">{TEAM[c["team"]]} {html.escape(c["name"])}#{cid}</td>' + "".join(f"<td>{'' if v is None else v}</td>" for v in vals) + "</tr>")
    for t in (0, 1):
        T = m["teams"][t]
        e = T["early"]
        vals = [f"{T['kills']}/{T['deaths']}/{T.get('assists', 0)}", T["damage_to_champs"], T["damage_from_champs"], T["gems_from_camps"], T["gems_from_kills"],
                T["banked"], T["gems_lost"], "/".join(str(T["engagements"][k]) for k in ("won", "lost", "drawn")), "", "",
                e["first_camp_kill"], e["first_capture"], e["first_deposit"], e["first_shop"], list(e.values())[4]] + [T.get("time_" + k, 0) for k in M.BUDGET]
        rows.append(f'<tr><th class="l {TEAM[t]}">Team {TEAM[t]}</th>' + "".join(f"<th>{'' if v is None else v}</th>" for v in vals) + "</tr>")
    return "<table><tr><th class=l>champion</th>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>" + "".join(rows) + "</table>"


def event_table(tl, ev):
    g = E.Game(tl)
    kinds = sorted({e["type"] for e in ev})
    default_off = {"camp_kill", "ward", "recall", "purchase"}
    flt = "".join(f'<label><input type="checkbox" data-k="{k}" {"" if k in default_off else "checked"}> {k}</label>' for k in kinds)
    rows = "".join(f'<tr data-k="{e["type"]}"><td>{e["r"]}</td><td class=l>{e["type"]}</td><td class="l {TEAM[e["team"]] if e.get("team") in (0, 1) else ""}">{html.escape(describe(g, e))}</td></tr>' for e in ev)
    js = """<script>const f=()=>{const on=new Set([...document.querySelectorAll('.flt input:checked')].map(i=>i.dataset.k));
document.querySelectorAll('#ev tr[data-k]').forEach(r=>r.style.display=on.has(r.dataset.k)?'':'none')};
document.querySelectorAll('.flt input').forEach(i=>i.onchange=f);f();</script>"""
    return f'<div class="flt">{flt}</div><table id="ev"><tr><th>r</th><th class=l>type</th><th class=l>event</th></tr>{rows}</table>{js}'


def page(title, body):
    return f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'><title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>"


def header(tl):
    n = team_names(tl)
    meta = tl["meta"]
    win = f"winner {TEAM[meta['winner']]}" if meta["winner"] is not None else "no winner"
    p = meta.get("platform") or {}
    return (f'<h1><span class=A>{html.escape(n[0])}</span> {meta["final_gems"][0]} – {meta["final_gems"][1]} <span class=B>{html.escape(n[1])}</span></h1>'
            f'<div>{html.escape(str(p.get("map") or meta["file"]))} · {meta["rounds"]} rounds · {win} · '
            f'draft A: {", ".join(meta["drafts"][0])} · draft B: {", ".join(meta["drafts"][1])}</div>')


def game_html(tl, ev, m):
    body = (header(tl) + "<h2>Score (solid: banked, dashed: banked + carried)</h2>" + svg_curve(tl) +
            "<h2>Champion timeline</h2>" + svg_strips(tl, ev) + "<h2>Metrics</h2>" + metrics_table(tl, m) +
            "<h2>Events</h2>" + event_table(tl, ev))
    return page(f"Replay {tl['meta']['file']}", body)


def compare_html(a, b):
    (ta, ea, ma), (tb, eb, mb) = a, b
    rows = []
    keys = [("banked", lambda m, t: m["teams"][t]["banked"]), ("kills", lambda m, t: m["teams"][t]["kills"]),
            ("deaths", lambda m, t: m["teams"][t]["deaths"]), ("gems from camps", lambda m, t: m["teams"][t]["gems_from_camps"]),
            ("gems from kills", lambda m, t: m["teams"][t]["gems_from_kills"]), ("gems lost", lambda m, t: m["teams"][t]["gems_lost"]),
            ("captures", lambda m, t: m["teams"][t]["captures"]), ("fights won", lambda m, t: m["teams"][t]["engagements"]["won"]),
            ("damage to champs", lambda m, t: m["teams"][t]["damage_to_champs"]), ("first capture", lambda m, t: m["teams"][t]["early"]["first_capture"]),
            ("first deposit", lambda m, t: m["teams"][t]["early"]["first_deposit"]), ("banked @150", lambda m, t: list(m["teams"][t]["early"].values())[5])] + \
        [(f"time {k}", (lambda k: lambda m, t: m["teams"][t].get("time_" + k, 0))(k)) for k in M.BUDGET]
    for name, f in keys:
        cells = []
        for t in (0, 1):
            va, vb = f(ma, t), f(mb, t)
            d = (vb - va) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else ""
            cells += [va, vb, f"{d:+}" if d != "" else ""]
        rows.append(f"<tr><td class=l>{name}</td>" + "".join(f"<td>{'' if v is None else v}</td>" for v in cells) + "</tr>")
    tbl = ("<table><tr><th class=l>metric</th><th>A in game 1</th><th>A in game 2</th><th>Δ</th><th>B in game 1</th><th>B in game 2</th><th>Δ</th></tr>"
           + "".join(rows) + "</table>")
    body = (f"<h1>Compare</h1>{tbl}<div class=grid><div><h2>Game 1</h2>{header(ta)}{svg_curve(ta, 640, 180)}{svg_strips(ta, ea, 640)}</div>"
            f"<div><h2>Game 2</h2>{header(tb)}{svg_curve(tb, 640, 180)}{svg_strips(tb, eb, 640)}</div></div>"
            f"<h2>Game 1 metrics</h2>{metrics_table(ta, ma)}<h2>Game 2 metrics</h2>{metrics_table(tb, mb)}")
    return page("Replay comparison", body)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("game"); p.add_argument("input"); p.add_argument("-o", "--out"); p.add_argument("--summary")
    p = sub.add_parser("compare"); p.add_argument("a"); p.add_argument("b"); p.add_argument("-o", "--out", default="compare.html")
    p = sub.add_parser("summary"); p.add_argument("inputs", nargs="+"); p.add_argument("-o", "--out"); p.add_argument("--out-dir")
    a = ap.parse_args()
    if a.cmd == "game":
        tl, ev, m = analyse(a.input)
        out = a.out or re.sub(r"(\.timeline)?\.(json|zip|repl26a)$", "", a.input) + ".report.html"
        Path(out).write_text(game_html(tl, ev, m), encoding="utf-8")
        print(out)
        if a.summary:
            Path(a.summary).write_text(json.dumps(summary(tl, ev, m), separators=(",", ":")), encoding="utf-8")
    elif a.cmd == "compare":
        Path(a.out).write_text(compare_html(analyse(a.a), analyse(a.b)), encoding="utf-8")
        print(a.out)
    else:
        for inp in a.inputs:
            s = json.dumps(summary(*analyse(inp)), separators=(",", ":"))
            if a.out_dir:
                Path(a.out_dir).mkdir(parents=True, exist_ok=True)
                dest = Path(a.out_dir) / (re.sub(r"(\.timeline)?\.(json|zip|repl26a)$", "", Path(inp).name) + ".summary.json")
                dest.write_text(s, encoding="utf-8")
                print(dest)
            elif a.out:
                Path(a.out).write_text(s, encoding="utf-8")
            else:
                print(s)


if __name__ == "__main__":
    main()
