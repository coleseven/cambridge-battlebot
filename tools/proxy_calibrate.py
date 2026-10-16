"""Calibrate tools/macro_proxy.py against real games and re-check its strategic conclusions.

Real side: full-engine replays (default: the pool games from tools/fight_data.py pool, i.e. main / v1 vs
starter-cpp / sparring-tarot on every published map, both seats), decoded with tools/replay. Per team
we take first capture, first completed deposit, captures by round 300, and banked gems at rounds
150/300/500.

Proxy side: the same map, seat and number of picks, under every combination of the proxy's free
assumptions (clear DPS, tie rule, carrier risk) and a policy per team. The fit minimises the mean
normalised error over those metrics. Reported: best settings, per-map residuals, the rank correlation
between proxy and real banked margins, and the STRATEGY_PROXY.md comparisons rerun under the
calibrated settings. The proxy module itself is only imported, never modified.

  .venv/Scripts/python tools/proxy_calibrate.py [--replays .arena/fights/replays/pool] [--out .arena/proxycal/report.md]
"""
import argparse
import itertools
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "replay"))
import macro_proxy as mp  # noqa: E402
import mapformat as mf  # noqa: E402

ROOT = HERE.parent
CACHE = ROOT / ".arena" / "proxycal"
CHECKS = (150, 300, 500)
METRICS = ["first_capture", "first_bank", "captures_r300"] + [f"banked_r{c}" for c in CHECKS]
SCALE = {"first_capture": 30, "first_bank": 50, "captures_r300": 5, "banked_r150": 60, "banked_r300": 120,
         "banked_r500": 200}            # error normalisers (roughly one "meaningful" difference each)
DPS = [40, 60, 80, 120, 160, 200, 280, 400]
TIES = ["stall", "incumbent"]
RISKS = ["none", "displace", "loot"]
POLICIES = ["spread:24", "pair:24", "selective:24", "reinforce:24", "spread:48", "pair:48"]


# ---------------------------------------------------------------- real games
def real_metrics(replay):
    import events as E
    tl = E.load_timeline(replay)
    ev = E.derive(tl)
    g = E.Game(tl)
    out = []
    for t in (0, 1):
        caps = sorted(e["r"] for e in ev if e["type"] == "capture" and e["team"] == t)
        deps = sorted(e["finish"] for e in ev if e["type"] == "deposit" and e["team"] == t and e["completed"] and e.get("finish"))
        m = {"first_capture": caps[0] if caps else None, "first_bank": deps[0] if deps else None,
             "captures_r300": sum(1 for r in caps if r <= 300)}
        for c in CHECKS:
            f = g.frame_at(c)
            m[f"banked_r{c}"] = f["sg"][t] if f else 0
        out.append(m)
    return {"map": tl["meta"].get("map") or None, "rounds": tl["meta"]["rounds"], "teams": out,
            "final": tl["meta"]["final_gems"], "picks": [len(d) for d in tl["meta"]["drafts"]]}


def load_real(replay_dir):
    CACHE.mkdir(parents=True, exist_ok=True)
    files = sorted(Path(replay_dir).rglob("*.repl26a"))

    def one(p):
        c = CACHE / (p.parent.name + ".json")
        if c.exists():
            return json.loads(c.read_text(encoding="utf-8"))
        r = real_metrics(p)
        name = p.parent.name                      # <map>__<botA>-<botB>
        r["map"], r["pair"] = name.split("__")[0], name.split("__")[1]
        c.write_text(json.dumps(r), encoding="utf-8")
        return r

    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(one, files))


# ---------------------------------------------------------------- proxy
class Recording(mp.Match):
    """Match that also records the metrics above (same rules: run() is copied, not changed)."""

    def run(self):
        snap = {c: [0, 0] for c in CHECKS}
        first_cap = [None, None]
        caps300 = [0, 0]
        for self.round in range(1, self.max_rounds + 1):
            before = [s.captures for s in self.stats]
            self._arrive()
            self._resolve_camps()
            blocked = self._resolve_altars()
            for body in self.bodies:
                if body.task and body.task[0] == "altar" and body.task[1] in blocked:
                    body.channel += 1
            self._bank()
            for t in (0, 1):
                if self.stats[t].captures > before[t]:
                    first_cap[t] = first_cap[t] or self.round
                    if self.round <= 300:
                        caps300[t] += self.stats[t].captures - before[t]
            if self.round in snap:
                snap[self.round] = [s.banked for s in self.stats]
            if max(s.banked for s in self.stats) >= 1000:
                for c in CHECKS:
                    if c > self.round:
                        snap[c] = [s.banked for s in self.stats]
                break
            self._assign()
        return [{"first_capture": first_cap[t], "first_bank": self.stats[t].first_bank, "captures_r300": caps300[t],
                 **{f"banked_r{c}": snap[c][t] for c in CHECKS}} for t in (0, 1)]


def err(real, sim):
    e = []
    for k in METRICS:
        r, s = real[k], sim[k]
        if r is None and s is None:
            continue
        if r is None or s is None:
            e.append(2.0)
            continue
        e.append(min(abs(r - s) / SCALE[k], 4.0))
    return sum(e) / max(len(e), 1)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replays", default=str(ROOT / ".arena" / "fights" / "replays" / "pool"))
    ap.add_argument("--out", default=str(CACHE / "report.md"))
    a = ap.parse_args()
    games = load_real(a.replays)
    bots = sorted({b for g in games for b in g["pair"].split("-", 1)}, key=len)
    # Bot names may contain '-', so split each pair using the known names.
    names = sorted({n for g in games for n in [g["pair"]]})
    known = ["sparring-tarot", "starter-cpp", "main", "v1"]
    for g in games:
        for k in known:
            if g["pair"].startswith(k + "-") and g["pair"][len(k) + 1:] in known:
                g["bots"] = [k, g["pair"][len(k) + 1:]]
    games = [g for g in games if "bots" in g]
    models = {}
    for g in games:
        if g["map"] not in models:
            path = next(p for p in (ROOT / "maps").glob("*.map26a") if p.stem == g["map"])
            models[g["map"]] = mp.MapModel(mf.decode(path.read_bytes()))
    botset = sorted({b for g in games for b in g["bots"]})
    print(f"{len(games)} real games on {len(models)} maps; bots {botset}")

    # Fit: one policy per bot + shared (dps, tie, risk). Coordinate search over policies per assumption set.
    sim_cache = {}

    def sim(mapname, pols, dps, tie, risk, rounds):
        key = (mapname, pols, dps, tie, risk, rounds)
        if key not in sim_cache:
            sim_cache[key] = Recording(models[mapname], (mp.Policy.parse(pols[0]), mp.Policy.parse(pols[1])),
                                       dps, tie, rounds, risk).run()
        return sim_cache[key]

    def total(assign, dps, tie, risk):
        s, n, per = 0.0, 0, {}
        for g in games:
            res = sim(g["map"], (assign[g["bots"][0]], assign[g["bots"][1]]), dps, tie, risk, 600)
            for t in (0, 1):
                e = err(g["teams"][t], res[t]); s += e; n += 1
                per.setdefault((g["map"], g["bots"][t]), []).append(e)
        return s / n, per

    best = None
    for dps, tie, risk in itertools.product(DPS, TIES, RISKS):
        assign = {b: "spread:24" for b in botset}
        cur, _ = total(assign, dps, tie, risk)
        for _ in range(2):                      # two sweeps of coordinate descent over bot policies
            for b in botset:
                for pol in POLICIES:
                    trial = {**assign, b: pol}
                    e, _ = total(trial, dps, tie, risk)
                    if e < cur:
                        cur, assign = e, trial
        print(f"  dps {dps:3} {tie:9} {risk:8} err {cur:.3f}  {assign}")
        if best is None or cur < best[0]:
            best = (cur, dps, tie, risk, assign)
    cur, dps, tie, risk, assign = best
    _, per = total(assign, dps, tie, risk)

    # Baseline: what does "predict the per-metric mean of the real games" score?
    means = {k: sum(g["teams"][t][k] or 0 for g in games for t in (0, 1)) / (2 * len(games)) for k in METRICS}
    base = sum(err(g["teams"][t], means) for g in games for t in (0, 1)) / (2 * len(games))

    # Margin rank correlation (does the proxy order games like the engine does?).
    pairs = []
    for g in games:
        res = sim(g["map"], (assign[g["bots"][0]], assign[g["bots"][1]]), dps, tie, risk, 600)
        pairs.append((res[0]["banked_r500"] - res[1]["banked_r500"], g["teams"][0]["banked_r500"] - g["teams"][1]["banked_r500"]))

    def ranks(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
        for k, i in enumerate(o):
            r[i] = k
        return r
    rs, rr = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    n = len(pairs)
    rho = 1 - 6 * sum((x - y) ** 2 for x, y in zip(rs, rr)) / (n * (n * n - 1)) if n > 2 else float("nan")

    # Per-metric bias under the fit.
    bias = {k: [] for k in METRICS}
    for g in games:
        res = sim(g["map"], (assign[g["bots"][0]], assign[g["bots"][1]]), dps, tie, risk, 600)
        for t in (0, 1):
            for k in METRICS:
                if g["teams"][t][k] is not None and res[t][k] is not None:
                    bias[k].append(res[t][k] - g["teams"][t][k])

    # Re-run the STRATEGY_PROXY.md comparisons under the calibrated assumptions (all pool maps, both seats).
    comps = [("pair:24", "spread:24"), ("selective:24", "pair:24"), ("pair:80", "pair:24"), ("pair:80:escort", "pair:24")]
    comp_rows = []
    for cand, opp in comps:
        margins, wins = [], 0
        per_map = {}
        for mname, model in sorted(models.items()):
            for seat in (0, 1):
                pols = (cand, opp) if seat == 0 else (opp, cand)
                r = mp.Match(model, (mp.Policy.parse(pols[0]), mp.Policy.parse(pols[1])), dps, tie, 600, risk).run()
                m = r["banked"][seat] - r["banked"][1 - seat]
                margins.append(m); wins += m > 0
                per_map.setdefault(mname, []).append(m)
        worse = sorted(k for k, v in per_map.items() if sum(v) < 0)
        comp_rows.append((cand, opp, sum(margins) / len(margins), wins, len(margins), worse))

    lines = ["# Macro proxy calibration", "",
             "Generated by `tools/proxy_calibrate.py`. Real games: "
             f"{len(games)} full-engine games ({', '.join(botset)}) on {len(models)} published maps, both seats,",
             "decoded with `tools/replay`. The proxy (`tools/macro_proxy.py`) was imported unchanged.", "",
             "## Best-fitting assumptions", "",
             f"- clear DPS **{dps}**, tie rule **{tie}**, carrier risk **{risk}**",
             "- policy per bot: " + ", ".join(f"`{b}` → `{p}`" for b, p in assign.items()),
             f"- mean normalised error **{cur:.3f}** (predicting the real-game mean for every team: {base:.3f};"
             " lower is better, 1.0 ≈ one meaningful difference per metric)",
             f"- Spearman rank correlation of proxy vs real banked-gem margin at round 500: **{rho:.2f}** (n = {n})", "",
             "Normalisers: " + ", ".join(f"{k} {v}" for k, v in SCALE.items()) + ".", "",
             "## Per-metric bias (proxy minus real, calibrated settings)", "",
             "| metric | mean bias | mean abs error | n |", "|---|---:|---:|---:|"]
    for k, v in bias.items():
        if v:
            lines.append(f"| {k} | {sum(v) / len(v):+.1f} | {sum(abs(x) for x in v) / len(v):.1f} | {len(v)} |")
    lines += ["", "## Per-map error (mean normalised error per team side)", "", "| map | " + " | ".join(botset) + " |",
              "|---|" + "---:|" * len(botset)]
    for mname in sorted(models):
        cells = []
        for b in botset:
            v = per.get((mname, b))
            cells.append(f"{sum(v) / len(v):.2f}" if v else "–")
        lines.append(f"| {mname} | " + " | ".join(cells) + " |")
    lines += ["", "## STRATEGY_PROXY.md comparisons under the calibrated assumptions", "",
              "All published maps, both seats, 600 rounds.", "",
              "| candidate | vs | mean banked margin | wins | maps where it loses on average |", "|---|---|---:|---:|---|"]
    for cand, opp, m, w, n_, worse in comp_rows:
        lines.append(f"| `{cand}` | `{opp}` | {m:+.1f} | {w}/{n_} | {', '.join(worse) or '–'} |")
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (CACHE / "fit.json").write_text(json.dumps({"err": cur, "baseline": base, "dps": dps, "tie": tie, "risk": risk,
                                                "assign": assign, "rho": rho, "comparisons": comp_rows}, indent=1), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
