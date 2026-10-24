"""Learned director, champion-gated hill climb (replaces NEAT population selection for this noisy problem).

Why: NEAT selection on few noisy games has a winner's curse. Newcomers are scored on one generation's luck and
displace proven elites whose averages regress to their true strength. d1/d2 drifted steadily worse against
both the rule director and their own seed (docs/DIRECTOR.md).

Each generation:
  1. The champion (a director network; first the seed) plays a schedule of S (map, opponent) entries, both seats.
  2. L challengers (NEAT mutations of the champion; sometimes of the runner-up) play the SAME games; each is scored
     by its mean per-game difference from the champion (paired, so the schedule's difficulty cancels out).
  3. The best challenger, if ahead by more than --margin, is re-tested on a FRESH schedule (--confirm entries) against
     the champion AND the anchor (--anchor, the network we ship). It replaces the champion only if its lead over the
     champion exceeds --z standard errors of the per-game differences, and it is not behind the anchor. (23 Oct: c1
     promoted on "ahead at all" over 48 games; 80 promotions mostly accumulated small losses and every champion
     ended below its seed, version 4.)

  .venv/Scripts/python tools/director_climb.py run c1 --seed-net .arena/director/hall/d1-gen0100.json
        [--team team-dirlearn] [--fighters neat:real1] [--lam 12] [--games 16] [--confirm 24] [--margin 0.05]
        [--opps rules net:<file> sparring-tarot v1 forager starter-cpp] [--jobs 12] [--until 11:50] [--dist .arena/dist]
  .venv/Scripts/python tools/director_climb.py status c1
Outputs in .arena/director/<name>/: state.pkl, log.jsonl, champion.json (the current champion's program words, for
team_bot.py --director-net), champions/c###.json (every promotion).
"""
import argparse
import copy
import json
import pickle
import random
import shutil
import struct
import sys
import time
from pathlib import Path

import neat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import bake_fighter as bf  # noqa: E402
import director_train as dt  # noqa: E402
import dist  # noqa: E402
import league  # noqa: E402
import neat_train as nt  # noqa: E402
import real_train as rt  # noqa: E402

OUT = dt.OUT


def opponents(names, slots, fblob):
    opps = {}
    for o in names:
        if o == "rules":
            opps[o] = dist.virtual_bot(slots[0], fblob, "rules")
        elif o.startswith("net:"):
            words = json.loads(Path(o[4:]).read_text(encoding="utf-8"))["words"]
            opps[o] = dist.virtual_bot(slots[0], dt.with_director(fblob, words), Path(o[4:]).stem)
        else:
            src = arena.ARENA / "realopp" / o
            if not (src / "src").exists():
                shutil.copytree(arena.ROOT / "bots" / o / "src", src / "src")
            opps[o] = arena.build(src, o)
    return opps


def sched(tag, k, opps, maps, rules_share=0.5):
    rng = random.Random(tag)
    others = [o for o in opps if o != "rules"] or list(opps)
    return [(rng.choice(maps), "rules" if "rules" in opps and j < round(rules_share * k) else rng.choice(others))
            for j in range(k)]


def play(nets, schedule, opps, slots, fblob, a):
    """nets: list of (name, words) -> {name: [score per game in schedule order]} (both seats per entry)."""
    games, meta = [], []
    for name, words in nets:
        bot = dist.virtual_bot(slots[0], dt.with_director(fblob, words), name)
        for m, oname in schedule:
            ob = opps[oname]
            games += [(bot, ob, m), (ob, bot, m)]
            meta += [(name, 0), (name, 1)]
    res = dist.play_slots(games, a.jobs, slots, root=a.dist or None)
    out, errs = {}, 0
    for (name, seat), r in zip(meta, res):
        out.setdefault(name, []).append(rt.score(r, seat))
        errs += "error" in r
    return out, errs


def run(a):
    d = OUT / a.name
    d.mkdir(parents=True, exist_ok=True)
    ck = d / "state.pkl"
    config = nt.make_config(d, 4, "climb", nin=dt.NFD)
    config.genome_config.weight_mutate_power = a.mut
    pop = neat.Population(config)                 # only for the innovation tracker and node indexer
    if ck.exists():
        st = pickle.loads(ck.read_bytes())
        config.genome_config.innovation_tracker = st["tracker"]
        pop.reproduction.innovation_tracker = st["tracker"]
        print(f"resuming climb {a.name} at generation {st['gen']}, {st['promotions']} promotions", flush=True)
    else:
        words = json.loads(Path(a.seed_net).read_text(encoding="utf-8"))["words"]
        champ = dt.genome_from_words(words, config, 1)
        if (d / "team_src").exists():
            shutil.rmtree(d / "team_src")
        shutil.copytree(arena.ROOT / "bots" / a.team / "src", d / "team_src")
        st = {"gen": 0, "champ": champ, "runner": None, "promotions": 0, "next_key": 2, "fighters": a.fighters,
              "seed": a.seed_net, "tracker": config.genome_config.innovation_tracker}
    top = max([k for g in (st["champ"], st["runner"]) if g for k in g.nodes] + [1])
    import itertools
    config.genome_config.node_indexer = itertools.count(top + 1)
    slots = rt.build_slots(d, max(2, 2 * a.jobs))
    _, fw = bf.blob_words(league.programs(st["fighters"], 3), 0)
    fblob = struct.pack(f"<{len(fw)}I", *fw)
    opps = opponents(a.opps, slots, fblob)
    anchor_words = json.loads(Path(a.anchor or st["seed"]).read_text(encoding="utf-8"))["words"]
    maps = arena.all_maps()
    stop_at = rt.deadline(a.until) if a.until else None
    log = d / "log.jsonl"
    last, done = 0.0, 0
    rng = random.Random(f"climb:{a.name}:{st['gen']}")
    while a.gens == 0 or done < a.gens:
        if stop_at and time.time() + 1.1 * last > stop_at:
            print(f"stopping before the {a.until} deadline", flush=True)
            break
        t0 = time.perf_counter()
        gen = st["gen"] + 1
        champ_words = dt.program(st["champ"], config)
        chall = []
        for j in range(a.lam):
            parent = st["runner"] if (st["runner"] is not None and j % 4 == 3) else st["champ"]
            g = copy.deepcopy(parent)
            g.key = st["next_key"]
            st["next_key"] += 1
            for _ in range(1 + (j % 3 == 2)):           # some challengers take two mutation steps
                g.mutate(config.genome_config)
            w = dt.program(g, config)
            if w is not None and w != champ_words:
                chall.append((g, w))
        schedule = sched(f"climb:{gen}", a.games, list(opps), maps)
        scores, errs = play([("champ", champ_words)] + [(f"c{i}", w) for i, (_, w) in enumerate(chall)],
                            schedule, opps, slots, fblob, a)
        base = scores["champ"]
        diffs = sorted(((sum(x - y for x, y in zip(scores[f"c{i}"], base)) / len(base), i)
                        for i in range(len(chall))), reverse=True)
        promoted, confirm_diff = False, None
        anchor_diff = None
        if diffs and diffs[0][0] > a.margin:
            bi = diffs[0][1]
            fresh = sched(f"confirm:{gen}", a.confirm, list(opps), maps)
            nets = [("champ", champ_words), ("cand", chall[bi][1])]
            if anchor_words != champ_words:
                nets.append(("anchor", anchor_words))
            cs, e2 = play(nets, fresh, opps, slots, fblob, a)
            errs += e2
            dl = [x - y for x, y in zip(cs["cand"], cs["champ"])]
            confirm_diff = sum(dl) / len(dl)
            var = sum((x - confirm_diff) ** 2 for x in dl) / max(1, len(dl) - 1)
            se = (var / len(dl)) ** 0.5
            ref = cs.get("anchor", cs["champ"])
            anchor_diff = sum(x - y for x, y in zip(cs["cand"], ref)) / len(ref)
            if confirm_diff > a.z * se and anchor_diff >= 0:
                st["runner"] = st["champ"]
                st["champ"] = chall[bi][0]
                st["promotions"] += 1
                promoted = True
                rec = {"gen": gen, "promotion": st["promotions"], "screen": round(diffs[0][0], 3),
                       "confirm": round(confirm_diff, 3), "se": round(se, 3), "vs_anchor": round(anchor_diff, 3),
                       "nfd": dt.NFD, "words": chall[bi][1]}
                (d / "champions").mkdir(exist_ok=True)
                (d / "champions" / f"c{st['promotions']:03d}.json").write_text(json.dumps(rec), encoding="utf-8")
        if not promoted and len(diffs) > 1 and diffs[0][0] > 0:
            st["runner"] = chall[diffs[0][1]][0]       # best of the rest: a second parent for diversity
        st["gen"] = gen
        cw = dt.program(st["champ"], config)
        (d / "champion.json").write_text(json.dumps({"gen": gen, "promotions": st["promotions"], "nfd": dt.NFD,
                                                     "words": cw}), encoding="utf-8")
        st["tracker"] = config.genome_config.innovation_tracker
        tmp = ck.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps(st))
        tmp.replace(ck)
        secs = time.perf_counter() - t0
        last = secs
        done += 1
        champ_mean = sum(base) / len(base)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"gen": gen, "secs": round(secs), "errors": errs, "champ_score": round(champ_mean, 3),
                                 "best_diff": round(diffs[0][0], 3) if diffs else None,
                                 "confirm": None if confirm_diff is None else round(confirm_diff, 3),
                                 "vs_anchor": None if anchor_diff is None else round(anchor_diff, 3),
                                 "promoted": promoted, "promotions": st["promotions"],
                                 **({"dist": dist.take_stats(a.dist)} if a.dist else {})}) + "\n")
        print(f"gen {gen:4} climb  {secs:5.0f}s  errors {errs}  champion {champ_mean:+.3f}  best challenger "
              f"{(diffs[0][0] if diffs else 0):+.3f}" + (f"  confirm {confirm_diff:+.3f}" if confirm_diff is not None else "")
              + (f" vs anchor {anchor_diff:+.3f}" if anchor_diff is not None else "")
              + (f"  PROMOTED (#{st['promotions']})" if promoted else ""), flush=True)


def status(a):
    for line in (OUT / a.name / "log.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        print(f"gen {r['gen']:4} {r['secs']:4}s champion {r['champ_score']:+.3f} best {r['best_diff']} "
              f"confirm {r['confirm']} {'PROMOTED' if r['promoted'] else ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--seed-net", required=False, default=".arena/director/hall/d1-gen0100.json")
    r.add_argument("--team", default="team-dirlearn")
    r.add_argument("--fighters", default="neat:real1")
    r.add_argument("--lam", type=int, default=12, help="challengers per generation")
    r.add_argument("--games", type=int, default=16, help="screening schedule entries (each both seats)")
    r.add_argument("--confirm", type=int, default=64, help="fresh confirmation entries (each both seats)")
    r.add_argument("--margin", type=float, default=0.15, help="screening lead needed to try a confirmation")
    r.add_argument("--z", type=float, default=2.0, help="confirmation lead needed, in standard errors")
    r.add_argument("--anchor", default="", help="network a promotion must not trail (default: the seed)")
    r.add_argument("--mut", type=float, default=0.15)
    r.add_argument("--opps", nargs="+", default=["rules", "net:.arena/director/hall/d1-gen0100.json", "sparring-tarot",
                                                  "v1", "forager", "starter-cpp"])
    r.add_argument("--jobs", type=int, default=12)
    r.add_argument("--gens", type=int, default=0)
    r.add_argument("--until", default="")
    dist.add_arg(r)
    s = sub.add_parser("status")
    s.add_argument("name")
    a = ap.parse_args()
    {"run": run, "status": status}[a.cmd](a)


if __name__ == "__main__":
    main()
