"""Fit, evaluate and export the fight win-probability model.

Input: .arena/fights/fights.jsonl from tools/fight_data.py. Model: L2 logistic regression on features a
bot can observe when it sees an enemy (hero types, health, max health, distance, local counts, own
ability readiness). Evaluation holds out whole games, reports log-loss / Brier / accuracy against the
forager's hand placeholder and a matchup-only baseline, plus a reliability table.

  .venv/Scripts/python tools/fight_model.py fit [--l2 1.0] [--holdout-src pool]
  .venv/Scripts/python tools/fight_model.py export        # -> bots/forager/src/win_model.h
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fight_data import OUT, TYPES  # noqa: E402

ROOT = HERE.parent
MODEL = OUT / "model.json"
HEADER = ROOT / "bots" / "forager" / "src" / "win_model.h"
NUMERIC = ["my_hp_frac", "foe_hp_frac", "my_max_k", "foe_max_k", "log_hp_ratio", "dist", "allies", "enemies",
           "cd0_ready", "cd1_ready"]
# team = 1 in games with 3+ bodies per side (fights there are not 1v1 duels); team_* = team x numeric.
FEATURES = [f"me_{t}" for t in TYPES] + [f"foe_{t}" for t in TYPES] + NUMERIC + ["team"] + [f"team_{f}" for f in NUMERIC]


def featurise(r):
    x = np.zeros(len(FEATURES))
    x[r["me"]] = 1.0
    x[len(TYPES) + r["foe"]] = 1.0
    n = 2 * len(TYPES)
    my_hp, foe_hp = max(r["my_hp"], 1.0), max(r["foe_hp"], 1.0)
    x[n:] = [r["my_hp"] / max(r["my_max"], 1), r["foe_hp"] / max(r["foe_max"], 1),
             r["my_max"] / 1000, r["foe_max"] / 1000, np.log(my_hp / foe_hp),
             r["dist"] / 4, r["allies"], r["enemies"], float(r["cd0"] == 0), float(r["cd1"] == 0), 0.0] + [0.0] * len(NUMERIC)
    if r["bodies"] >= 3:
        x[n + len(NUMERIC)] = 1.0
        x[n + len(NUMERIC) + 1:] = x[n:n + len(NUMERIC)]
    return x


def placeholder(r):
    """bots/forager's hand estimate when no estimator is installed."""
    p = 0.5 + 0.12 * (r["allies"] - r["enemies"]) + 0.25 * (r["my_hp"] / max(r["my_max"], 1) - r["foe_hp"] / max(r["foe_max"], 1))
    return min(max(p, 0.05), 0.95)


def load():
    rows = [json.loads(l) for l in (OUT / "fights.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    # Matches are deterministic, so identical bots (e.g. main and its frozen copy v1) produce identical
    # games; keep one copy so duplicates cannot sit on both sides of the train/test split.
    games = {}
    for r in rows:
        games.setdefault(r["game"], []).append(r)
    seen, kept = set(), []
    for name in sorted(games):
        sig = tuple(tuple(sorted((k, v) for k, v in r.items() if k not in ("game", "src"))) for r in games[name])
        if sig not in seen:
            seen.add(sig); kept += games[name]
    if len(kept) < len(rows):
        print(f"dropped {len(rows) - len(kept)} rows from {len(games) - len(seen)} duplicate games")
    rows = kept
    return rows, np.array([featurise(r) for r in rows]), np.array([r["y"] for r in rows])


def fit_logistic(X, y, l2, iters=3000, lr=0.5):
    """Full-batch gradient descent with standardisation of the numeric columns (one-hots unscaled)."""
    mu, sd = X.mean(0), X.std(0) + 1e-9
    k = 2 * len(TYPES)
    mu[:k], sd[:k] = 0.0, 1.0
    Z = (X - mu) / sd
    w, b = np.zeros(X.shape[1]), 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        g = p - y
        w -= lr * (Z.T @ g / len(y) + l2 * w / len(y))
        b -= lr * g.mean()
    # Fold standardisation back into raw-feature weights.
    return w / sd, b - (w / sd) @ mu


def predict(w, b, X):
    return 1 / (1 + np.exp(-np.clip(X @ w + b, -30, 30)))


def scores(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    dec = y != 0.5
    return {"n": int(len(y)), "logloss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
            "brier": float(((p - y) ** 2).mean()),
            "acc_decisive": float(((p[dec] > 0.5) == (y[dec] == 1)).mean()) if dec.any() else None}


def reliability(p, y, bins=10):
    out = []
    for i in range(bins):
        m = (p >= i / bins) & (p < (i + 1) / bins if i < bins - 1 else p <= 1)
        if m.sum():
            out.append((f"{i / bins:.1f}-{(i + 1) / bins:.1f}", int(m.sum()), float(p[m].mean()), float(y[m].mean())))
    return out


def split(rows, holdout_src=None):
    if holdout_src:
        return np.array([r["src"] != holdout_src for r in rows])
    return np.array([int(hashlib.md5(r["game"].encode()).hexdigest(), 16) % 5 != 0 for r in rows])


def matchup_baseline(rows_tr, y_tr, rows_te):
    """Empirical win rate per (me, foe) type pair with a +1/+1 prior."""
    s, n = {}, {}
    for r, y in zip(rows_tr, y_tr):
        k = (r["me"], r["foe"]); s[k] = s.get(k, 0) + y; n[k] = n.get(k, 0) + 1
    return np.array([(s.get((r["me"], r["foe"]), 0) + 1) / (n.get((r["me"], r["foe"]), 0) + 2) for r in rows_te])


def cmd_fit(a):
    rows, X, y = load()
    tr = split(rows, a.holdout_src)
    te = ~tr
    rtr = [r for r, t in zip(rows, tr) if t]; rte = [r for r, t in zip(rows, te) if t]
    w, b = fit_logistic(X[tr], y[tr], a.l2)
    p = predict(w, b, X[te])
    print(f"train {tr.sum()}  test {te.sum()}  (holdout: {a.holdout_src or '20% of games'})  "
          f"draws {np.mean(y == 0.5):.0%}  sources {sorted({r['src'] for r in rows})}")
    rep = {"model": scores(p, y[te]),
           "matchup_only": scores(matchup_baseline(rtr, y[tr], rte), y[te]),
           "placeholder": scores(np.array([placeholder(r) for r in rte]), y[te]),
           "constant": scores(np.full(te.sum(), y[tr].mean()), y[te])}
    for k, v in rep.items():
        print(f"  {k:13} logloss {v['logloss']:.3f}  brier {v['brier']:.3f}  acc(decisive) {v['acc_decisive']:.3f}")
    rel = reliability(p, y[te])
    print("  reliability (bin, n, mean predicted, observed):")
    for row in rel:
        print(f"    {row[0]}  {row[1]:5}  {row[2]:.2f}  {row[3]:.2f}")
    for src in sorted({r["src"] for r in rte}):
        m = np.array([r["src"] == src for r in rte])
        ph = np.array([placeholder(r) for r in rte])
        fmt = lambda d: json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in d.items()})  # noqa: E731
        print(f"  test {src:6} model       {fmt(scores(p[m], y[te][m]))}")
        print(f"  test {src:6} placeholder {fmt(scores(ph[m], y[te][m]))}")
    # Final model on everything.
    w, b = fit_logistic(X, y, a.l2)
    MODEL.write_text(json.dumps({"features": FEATURES, "w": w.tolist(), "b": float(b), "l2": a.l2, "n": len(y),
                                 "eval": rep, "reliability": rel}, indent=1), encoding="utf-8")
    print("weights:", ", ".join(f"{f}={v:+.2f}" for f, v in zip(FEATURES, w)), f"bias={b:+.2f}")
    print(f"-> {MODEL}")


def cmd_export(a):
    m = json.loads(MODEL.read_text(encoding="utf-8"))
    assert m["features"] == FEATURES, "model.json was fitted with a different feature list; refit"
    w = m["w"]
    k = len(TYPES)
    f = lambda v: f"{v:+.6f}f"  # noqa: E731
    num = dict(zip(NUMERIC, w[2 * k:2 * k + len(NUMERIC)]))
    tm = dict(zip(FEATURES, w))
    text = f"""#pragma once
// Fight win-probability estimator, generated by tools/fight_model.py export (do not hand-edit).
// Logistic regression fitted on {m['n']} engagements decoded from replays (tools/fight_data.py).
// Held-out log-loss {m['eval']['model']['logloss']:.3f} vs {m['eval']['placeholder']['logloss']:.3f} for the hand placeholder.
// Matches farming::WinEstimate: install with `farming::estimate_win = win_model::estimate;`.
#include <algorithm>
#include <cmath>
#include <cambc.h>

namespace win_model {{
inline constexpr float W_ME[{k}] = {{{", ".join(f(v) for v in w[:k])}}};
inline constexpr float W_FOE[{k}] = {{{", ".join(f(v) for v in w[k:2 * k])}}};
inline constexpr float BIAS = {f(m['b'])};

// Same feature definitions as tools/fight_model.featurise.
inline float probability(int me_type, int foe_type, float my_hp, float my_max, float foe_hp, float foe_max,
                         int dist, int allies, int enemies, bool cd0_ready, bool cd1_ready, bool team_game) {{
    float z = BIAS;
    if (me_type >= 0 && me_type < {k}) z += W_ME[me_type];
    if (foe_type >= 0 && foe_type < {k}) z += W_FOE[foe_type];
    z += {f(num['my_hp_frac'])} * (my_hp / std::max(my_max, 1.0f));
    z += {f(num['foe_hp_frac'])} * (foe_hp / std::max(foe_max, 1.0f));
    z += {f(num['my_max_k'])} * (my_max / 1000.0f);
    z += {f(num['foe_max_k'])} * (foe_max / 1000.0f);
    z += {f(num['log_hp_ratio'])} * std::log(std::max(my_hp, 1.0f) / std::max(foe_hp, 1.0f));
    z += {f(num['dist'])} * (dist / 4.0f);
    z += {f(num['allies'])} * allies;
    z += {f(num['enemies'])} * enemies;
    z += {f(num['cd0_ready'])} * (cd0_ready ? 1.0f : 0.0f);
    z += {f(num['cd1_ready'])} * (cd1_ready ? 1.0f : 0.0f);
    if (team_game) {{
        z += {f(tm['team'])};
        z += {f(tm['team_my_hp_frac'])} * (my_hp / std::max(my_max, 1.0f));
        z += {f(tm['team_foe_hp_frac'])} * (foe_hp / std::max(foe_max, 1.0f));
        z += {f(tm['team_my_max_k'])} * (my_max / 1000.0f);
        z += {f(tm['team_foe_max_k'])} * (foe_max / 1000.0f);
        z += {f(tm['team_log_hp_ratio'])} * std::log(std::max(my_hp, 1.0f) / std::max(foe_hp, 1.0f));
        z += {f(tm['team_dist'])} * (dist / 4.0f);
        z += {f(tm['team_allies'])} * allies;
        z += {f(tm['team_enemies'])} * enemies;
        z += {f(tm['team_cd0_ready'])} * (cd0_ready ? 1.0f : 0.0f);
        z += {f(tm['team_cd1_ready'])} * (cd1_ready ? 1.0f : 0.0f);
    }}
    z = std::clamp(z, -30.0f, 30.0f);
    return 1.0f / (1.0f + std::exp(-z));
}}

// farming::WinEstimate adapter: our current body vs one visible enemy, local counts within Chebyshev 4.
// The self-side features (position, counts, abilities) are cached per round and body, so extra enemies
// in the same turn cost only the enemy queries.
inline float estimate(const cambc::champion& enemy) {{
    struct Self {{ std::uint32_t round = ~0u; int type = -1; cambc::vec2 pos{{}}; float hp = 0, max = 1;
                   int allies = 0, enemies = 0; bool r0 = false, r1 = false; }};
    static Self s;
    static const bool team_game = cambc::vision::champions(cambc::team::ally).size() >= 3;
    auto& me = cambc::self();
    const auto round = cambc::round_number();
    const int type = static_cast<int>(me.champion_name());
    if (s.round != round || s.type != type) {{
        s.round = round; s.type = type; s.pos = me.position(); s.hp = me.health(); s.max = me.max_health();
        cambc::bitboard local; local.clear(); local.draw_chebyshev(s.pos, {4});
        s.allies = std::max(static_cast<int>((cambc::vision::champion_tiles(cambc::team::ally) & local).count()) - 1, 0);
        s.enemies = static_cast<int>((cambc::vision::champion_tiles(cambc::team::enemy) & local).count());
        const auto ab = me.abilities();
        s.r0 = ab[0].info() && ab[0].is_ready(); s.r1 = ab[1].info() && ab[1].is_ready();
    }}
    const auto q = enemy.position();
    const int dist = std::max(std::abs(s.pos.x - q.x), std::abs(s.pos.y - q.y));
    return probability(type, static_cast<int>(enemy.champion_name()), s.hp, s.max, enemy.health(),
                       enemy.max_health(), dist, s.allies, s.enemies, s.r0, s.r1, team_game);
}}
}}  // namespace win_model
"""
    HEADER.write_text(text, encoding="utf-8")
    print(f"-> {HEADER}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit"); f.add_argument("--l2", type=float, default=1.0)
    f.add_argument("--holdout-src", help="hold out a whole source (e.g. pool) instead of 20%% of games")
    sub.add_parser("export")
    a = ap.parse_args()
    {"fit": cmd_fit, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
