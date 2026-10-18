"""Where the fighter's fuel goes, and how often more network budget changes the chosen option.

Builds bots/fuelstudy-decide from bots/fighter (baked champions; tools/bake_fighter.py) with a probe that,
every turn: records fuel per stage (observe = queries before option scoring, options = enumerate + score
every option, network = re-rank), the number of options, and re-ranks the top 12 with NO fuel cutoffs,
then reports for K = 0..12 whether the choice using only the top K (K = 0: the linear prior alone) equals
the full top-12 choice. Plays fighter-vs-fighter arenas and real maps vs an opponent (default builds:
release builds don't capture stdout; tools/fuel_study.py showed both builds play identical games).

  .venv/Scripts/python tools/fighter_probe.py [--sizes 2 3] [--games 4] [--real elkring rows corners lanes collision]
Writes .arena/fuel/fighter_probe.json.
"""
import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import fuel_study as fs  # noqa: E402
import nvn  # noqa: E402

K_MAX = 12


def build_probe(src: Path | None = None) -> Path:
    d = arena.ROOT / "bots" / "fuelstudy-decide"
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(src or (arena.ROOT / "bots" / "fighter" / "src"), d / "src")
    b = d / "src" / "bot.cpp"
    t = b.read_text(encoding="utf-8")

    def rep(old, new):
        nonlocal t
        if t.count(old) != 1:
            sys.exit(f"probe anchor not found once: {old[:60]!r}")
        t = t.replace(old, new)

    rep("    if (!me.is_alive()) return;\n", "    if (!me.is_alive()) return;\n    const auto PF0 = c::fuel_remaining();\n")
    rep("    // ---- enumerate and score options ----\n",
        "    const auto PF1 = c::fuel_remaining();\n    // ---- enumerate and score options ----\n")
    start = t.index("    std::vector<Opt*> order;")
    end = t.index("    if (!best) return;", start)
    t = t[:start] + f"""    const auto PF2 = c::fuel_remaining();
    std::vector<Opt*> order;
    for (auto& o : opts) order.push_back(&o);
    std::stable_sort(order.begin(), order.end(), [](const Opt* a, const Opt* b) {{ return a->score > b->score; }});
    if (!order.empty()) best = order[0];
    if (NET.ok && !order.empty()) {{
        const std::size_t kk = std::min<std::size_t>(order.size(), {K_MAX});
        std::vector<float> vals(kk);
        for (std::size_t i = 0; i < kk; ++i) vals[i] = run_net<NX>(order[i]->x.data());
        const auto PF3 = c::fuel_remaining();
        auto choice = [&](std::size_t k) -> std::size_t {{   // index into order chosen with only the top k re-ranked
            if (k == 0) return 0;
            std::size_t bi = 0;
            for (std::size_t i = 1; i < k && i < kk; ++i) if (vals[i] > vals[bi]) bi = i;
            return bi;
        }};
        const std::size_t full = choice(kk);
        std::cout << "DEC " << S.type << " " << opts.size() << " " << (PF0 - PF1) << " " << (PF1 - PF2) << " "
                  << (PF2 - PF3) << " ";
        for (std::size_t k = 0; k <= {K_MAX}; ++k) std::cout << (choice(std::min(k, kk)) == full ? '1' : '0');
        std::cout << std::endl;
        best = order[full];
    }}
""" + t[end:]
    b.write_text(t, encoding="utf-8")
    arena.build(d)
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", type=int, nargs="+", default=[2, 3])
    ap.add_argument("--games", type=int, default=4)
    ap.add_argument("--real", nargs="*", default=["elkring", "rows", "corners", "lanes", "collision"])
    ap.add_argument("--vs", default="sparring-tarot")
    ap.add_argument("--src", default="", help="fighter source to probe (default bots/fighter/src)")
    a = ap.parse_args()
    (arena.ARENA / "tmp").mkdir(parents=True, exist_ok=True)
    probe = build_probe(Path(a.src) if a.src else None)
    rng = random.Random(23)
    jobs = []
    for n in a.sizes:
        for g in range(a.games):
            ta = tuple(sorted(rng.sample(range(11), n)))
            tb = tuple(sorted(rng.sample(range(11), n)))
            jobs.append((f"{n}v{n} arena", probe, probe, nvn.map_path(n, nvn.LAYOUTS[g % 2], ta, tb)))
    opp = arena.ROOT / "bots" / a.vs
    for stem in a.real:
        hits = sorted((arena.ROOT / "maps").glob(f"{stem}*.map26a"))
        if hits:
            jobs.append(("5v5 real", probe, opp, hits[0]))
    stats = {}
    for label, x, y, m in jobs:
        text, _ = fs.run(x, y, m, release=False)
        st = stats.setdefault(label, {"obs": [], "opt": [], "nn": [], "n": [], "agree": [0] * (K_MAX + 1), "turns": 0})
        for line in text.splitlines():
            mm = re.search(r"DEC (\d+) (\d+) (\d+) (\d+) (\d+) ([01]+)", line)
            if not mm:
                continue
            st["n"].append(int(mm.group(2)))
            st["obs"].append(int(mm.group(3)))
            st["opt"].append(int(mm.group(4)))
            st["nn"].append(int(mm.group(5)))
            for k, ch in enumerate(mm.group(6)):
                st["agree"][k] += ch == "1"
            st["turns"] += 1
        print(f"{label:10} {m.name[:44]:44} turns so far {st['turns']}", flush=True)
    out = {}
    for label, st in stats.items():
        if not st["turns"]:
            continue
        pct = lambda v: fs.summary(v)   # noqa: E731
        agree = [round(100 * x / st["turns"], 1) for x in st["agree"]]
        out[label] = {"turns": st["turns"], "options": pct(st["n"]), "observe_fuel": pct(st["obs"]),
                      "options_fuel": pct(st["opt"]), "network_fuel_top12": pct(st["nn"]),
                      "agree_with_full_by_K%": dict(enumerate(agree))}
        print(f"\n{label}: {st['turns']} decisions")
        for k in ("options", "observe_fuel", "options_fuel", "network_fuel_top12"):
            s = out[label][k]
            print(f"  {k:20} p50 {s['p50']:>7} p90 {s['p90']:>7} p99 {s['p99']:>7} max {s['max']:>7}")
        print("  same choice as full top-12 re-rank, by network budget K:")
        print("   " + "  ".join(f"K={k}:{v:5.1f}%" for k, v in enumerate(agree)))
    (arena.ARENA / "fuel").mkdir(parents=True, exist_ok=True)
    (arena.ARENA / "fuel" / "fighter_probe.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
