"""Per-turn fuel of the macro layer (bots/forager: farming, banking, shopping, team comms) in real 5v5 games.

Copies bots/forager/src (read-only use) into bots/fuelstudy-forager-<variant>, adds a probe that prints each
turn's fuel split into the one-off map setup (lazy distance fields built with spare fuel) and everything else,
and plays real published maps against an opponent. Variants:
  off   forager as configured in params.h
  on    every FEATURE_* switch in params.h set to 1 (worst case: comms, comms strategies, fight selector,...)

  .venv/Scripts/python tools/fuel_macro.py [--maps elkring rows corners] [--vs sparring-tarot] [--variants off on]
Turn 1 of each body is excluded (2x fuel, init). Default builds (release builds don't capture bot stdout;
tools/fuel_study.py showed release and default play identical games).
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import fuel_study as fs  # noqa: E402


def make_variant(name: str, all_on: bool) -> Path:
    d = arena.ROOT / "bots" / f"fuelstudy-forager-{name}"
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(arena.ROOT / "bots" / "forager" / "src", d / "src")
    if all_on:
        p = d / "src" / "params.h"
        p.write_text(re.sub(r"(constexpr int FEATURE_\w+ = )0;", r"\g<1>1;", p.read_text(encoding="utf-8")),
                     encoding="utf-8")
    b = d / "src" / "bot.cpp"
    t = b.read_text(encoding="utf-8")
    # accumulate the fuel spent inside build_fields (one-off map setup) per turn
    t = t.replace("void build_fields(std::uint64_t reserve) {",
                  "std::uint64_t g_probe_fields = 0;\nvoid build_fields(std::uint64_t reserve) {\n"
                  "    const auto probe_f0 = c::fuel_remaining();\n    struct ProbeEnd { std::uint64_t f0; "
                  "~ProbeEnd() { g_probe_fields += f0 - c::fuel_remaining(); } } probe_end{probe_f0};", 1)
    old = "void Bot::play_turn() {\n    const auto f0 = c::fuel_remaining();"
    if old not in t:
        sys.exit("forager play_turn signature changed; update tools/fuel_macro.py")
    t = t.replace(old, "void Bot::play_turn() {\n    static bool probe_first = true;\n"
                       "    g_probe_fields = 0;\n    const auto f0 = c::fuel_remaining();", 1)
    t = t.replace("        comms_finish();\n    } catch (const std::exception& e) {",
                  "        comms_finish();\n        if (!probe_first) std::cout << \"FUELM \" << (f0 - c::fuel_remaining())"
                  " << \" \" << g_probe_fields << \" \" << c::round_number() << std::endl;\n        probe_first = false;\n"
                  "    } catch (const std::exception& e) {", 1)
    b.write_text(t, encoding="utf-8")
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--maps", nargs="+", default=["elkring", "rows", "corners", "lanes", "collision"])
    ap.add_argument("--vs", default="sparring-tarot")
    ap.add_argument("--variants", nargs="+", default=["off", "on"])
    a = ap.parse_args()
    (arena.ARENA / "tmp").mkdir(parents=True, exist_ok=True)
    maps = []
    for m in a.maps:
        hits = sorted((arena.ROOT / "maps").glob(f"{m}*.map26a"))
        if hits:
            maps.append(hits[0])
    opp = arena.ROOT / "bots" / a.vs
    out = {}
    for v in a.variants:
        d = make_variant(v, v == "on")
        tot, steady, fields = [], [], []
        for m in maps:
            for x, y in ((d, opp), (opp, d)):
                text, _ = fs.run(x, y, m, release=False)
                n = 0
                for line in text.splitlines():
                    mm = re.match(r"FUELM (\d+) (\d+) (\d+)", line)
                    if mm:
                        t_, f_, _r = int(mm.group(1)), int(mm.group(2)), int(mm.group(3))
                        tot.append(t_)
                        fields.append(f_)
                        steady.append(t_ - f_)
                        n += 1
                print(f"{v:4} {m.stem[:24]:24} {'A' if x == d else 'B'}: {n} turns, max total {max(tot) if tot else 0}",
                      flush=True)
        out[v] = {"total": fs.summary(tot), "excluding_field_setup": fs.summary(steady),
                  "field_setup_turns%": round(100 * sum(f > 0 for f in fields) / max(1, len(fields)), 2)}
    print("\nforager per-turn fuel (turn 1 excluded):")
    for v, r in out.items():
        print(f"  {v}: total {json.dumps(r['total'])}")
        print(f"  {v}: excluding one-off field setup {json.dumps(r['excluding_field_setup'])}; "
              f"turns with field setup {r['field_setup_turns%']}%")
    (arena.ARENA / "fuel").mkdir(parents=True, exist_ok=True)
    (arena.ARENA / "fuel" / "macro.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
