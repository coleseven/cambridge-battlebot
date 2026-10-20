"""Learned director: NEAT over the director network that re-ranks candidate orders (bots/director, 'DIRN' block).

Each network scores candidate orders from NFD features (bots/director/src/bot.cpp, "learned director"); feature
0 flags the rule cascade's own choice. Generation 0 is seeded so every network weights that feature heavily and
the rest near zero: they all start playing exactly like the rule-based director (no imitation data needed),
and evolution keeps only what wins more. The fighter networks and director params stay fixed during a run
(alternating stages: fighters train in their own runs).

  .venv/Scripts/python tools/team_bot.py --macro-src bots/director/src --source neat:real1 --out team-dirlearn --build
  .venv/Scripts/python tools/director_train.py run d1 [--team team-dirlearn] [--fighters neat:real1] [--pop 20]
        [--games 6] [--opps rules sparring-tarot v1 forager starter-cpp] [--jobs 12] [--until 11:50] [--dist .arena/dist]
  .venv/Scripts/python tools/director_train.py status d1
Outputs in .arena/director/<name>/: state.pkl, log.jsonl, best.json (+ snapshots/gen####.json): the program
words of the generation's best network, for tools/team_bot.py --director-net.
Score per game (our side): +1 win / -1 loss + gem margin / 500 (errors -3); each schedule entry is played from
both seats; all networks of a generation play the same games (common random numbers).
"""
import argparse
import json
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
import dist  # noqa: E402
import league  # noqa: E402
import neat_train as nt  # noqa: E402
import real_train as rt  # noqa: E402

OUT = arena.ARENA / "director"
NFD = 25                      # bots/director/src/bot.cpp NFD
MAX_NODES = 64                # DN_MAX_NODES
DIRN_BLOCK = 15000
DIRN_MAGIC = 0x4E524944       # 'DIRN'
RULE_WEIGHT = 3.0             # warm start: weight on feature 0 ("the rule cascade chose this")


def program(genome, config):
    """Feed-forward program words (hidden slots from NFD), or None over the size cap."""
    net = neat.nn.FeedForwardNetwork.create(genome, config)
    slots = {k: -k - 1 for k in config.genome_config.input_keys}
    for node, *_ in net.node_evals:
        if node not in slots:
            slots[node] = NFD + len([s for s in slots.values() if s >= NFD])
    if len(net.node_evals) > MAX_NODES or max(slots.values()) >= NFD + MAX_NODES:
        return None
    out_key = config.genome_config.output_keys[0]
    if out_key not in slots:
        slots[out_key] = NFD
    words = [len(net.node_evals)]
    for node, _act, _agg, bias, resp, links in net.node_evals:
        words += [slots[node], nt.ACT.get(genome.nodes[node].activation, 0), nt._f(bias), nt._f(resp), len(links)]
        for src, w in links:
            words += [slots[src], nt._f(w)]
    words.append(slots[out_key])
    return words


def with_director(blob: bytes, words) -> bytes:
    """A genome blob plus the 'DIRN' block (draft and params blocks, if any, are added after this)."""
    base = list(struct.unpack(f"<{len(blob) // 4}I", blob))
    if any(base[DIRN_BLOCK:]):
        raise ValueError("blob already reaches the director block")
    base = base[:DIRN_BLOCK] + [0] * max(0, DIRN_BLOCK - len(base))
    base += [DIRN_MAGIC, NFD] + list(words)
    if len(base) >= 16000:
        raise ValueError("director program reaches the draft block")
    return struct.pack(f"<{len(base)}I", *base)


def seed(pop, config, rng):
    out_key = config.genome_config.output_keys[0]
    for g in pop.population.values():
        for (i, o), cg in g.connections.items():
            if o == out_key and i < 0:
                cg.weight = RULE_WEIGHT if i == -1 else rng.gauss(0, 0.05)
        node = g.nodes[out_key]
        node.bias, node.response, node.activation = 0.0, 1.0, "identity"


def schedule(gen, k, opps, maps):
    rng = random.Random(f"dir:{gen}")
    return [(rng.choice(maps), rng.choice(opps)) for _ in range(k)]


def run(a):
    d = OUT / a.name
    d.mkdir(parents=True, exist_ok=True)
    ck = d / "state.pkl"
    if ck.exists():
        st = nt.load_state(ck)
        print(f"resuming director run {a.name} at generation {st['gen']}", flush=True)
    else:
        config = nt.make_config(d, a.pop, "", nin=NFD)
        pop = neat.Population(config)
        seed(pop, config, random.Random(1))
        if (d / "team_src").exists():
            shutil.rmtree(d / "team_src")
        shutil.copytree(arena.ROOT / "bots" / a.team / "src", d / "team_src")
        st = {"gen": 0, "pops": {0: pop}, "phase": {0: {"mode": "complexify", "floor": nt.complexity(pop)}},
              "fighters": a.fighters, "best": None}
        nt.save_state(ck, st)
        print(f"director run {a.name}: {a.pop} networks seeded on the rule choice; team source frozen", flush=True)
    pop = st["pops"][0]
    slots = rt.build_slots(d, max(2, 2 * a.jobs))
    _, fw = bf.blob_words(league.programs(st["fighters"], 3), 0)
    fblob = struct.pack(f"<{len(fw)}I", *fw)
    opps = {}
    for o in a.opps:
        if o == "rules":
            opps[o] = dist.virtual_bot(slots[0], fblob, "rules")       # same build, no network: the rule director
        else:
            src = arena.ARENA / "realopp" / o
            if not (src / "src").exists():
                shutil.copytree(arena.ROOT / "bots" / o / "src", src / "src")
            opps[o] = arena.build(src, o)
    maps = arena.all_maps()
    stop_at = rt.deadline(a.until) if a.until else None
    log = d / "log.jsonl"
    last, done = 0.0, 0
    while a.gens == 0 or done < a.gens:
        if stop_at and time.time() + 1.1 * last > stop_at:
            print(f"stopping before the {a.until} deadline", flush=True)
            break
        t0 = time.perf_counter()
        gen = st["gen"] + 1
        items = list(pop.population.items())
        sched = schedule(gen, a.games, list(opps), maps)
        games, meta, progs = [], [], {}
        for gi, (gid, g) in enumerate(items):
            prog = program(g, pop.config)
            progs[gid] = prog
            if prog is None:
                continue
            cand = dist.virtual_bot(slots[0], with_director(fblob, prog), f"dn{gi}")
            for m, oname in sched:
                for seat in (0, 1):
                    ob = opps[oname]
                    games.append((cand, ob, m) if seat == 0 else (ob, cand, m))
                    meta.append((gid, oname, seat))
        res = dist.play_slots(games, a.jobs, slots, root=a.dist or None)
        fit, by_opp, errs = {}, {}, 0
        for (gid, oname, seat), r in zip(meta, res):
            s = rt.score(r, seat)
            errs += "error" in r
            fit.setdefault(gid, []).append(s)
            by_opp.setdefault(oname, []).append(s)
        pre = {gid: sum(v) / len(v) for gid, v in fit.items()}

        def assign(genomes, cfg):
            for gid, g in genomes:
                g.fitness = pre.get(gid, -3.0) - 0.001 * len(g.connections)
        best_id = max(pre, key=pre.get)
        st["best"] = {"gen": gen, "fitness": round(pre[best_id], 3), "nfd": NFD, "words": progs[best_id]}
        pop.run(assign, 1)
        nt.update_phase(st["phase"][0], pop)
        st["gen"] = gen
        nt.save_state(ck, st)
        (d / "best.json").write_text(json.dumps(st["best"]), encoding="utf-8")
        (d / "snapshots").mkdir(exist_ok=True)
        (d / "snapshots" / f"gen{gen:04d}.json").write_text(json.dumps(st["best"]), encoding="utf-8")
        secs = time.perf_counter() - t0
        last = secs
        done += 1
        vs = {o: round(sum(v) / len(v), 3) for o, v in by_opp.items()}
        vals = sorted(pre.values())
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"gen": gen, "secs": round(secs), "games": len(games), "errors": errs, "vs": vs,
                                 "best": round(vals[-1], 3), "median": round(vals[len(vals) // 2], 3),
                                 **({"dist": dist.take_stats(a.dist)} if a.dist else {})}) + "\n")
        print(f"gen {gen:4} director  {secs:5.0f}s  {len(games)} games  errors {errs}  best {vals[-1]:+.3f}  "
              f"median {vals[len(vals) // 2]:+.3f}  vs " + " ".join(f"{o}:{v:+.2f}" for o, v in vs.items()), flush=True)


def status(a):
    for line in (OUT / a.name / "log.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        print(f"gen {r['gen']:4} {r['secs']:4}s best {r['best']:+.3f} median {r['median']:+.3f} vs "
              + " ".join(f"{o}:{v:+.2f}" for o, v in r["vs"].items()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--team", default="team-dirlearn", help="generated director bot (team_bot.py) frozen as team_src")
    r.add_argument("--fighters", default="neat:real1", help="fighter networks for every game (league spec)")
    r.add_argument("--pop", type=int, default=20)
    r.add_argument("--games", type=int, default=6, help="schedule entries per generation (each played both seats)")
    r.add_argument("--opps", nargs="+", default=["rules", "sparring-tarot", "v1", "forager", "starter-cpp"])
    r.add_argument("--jobs", type=int, default=12)
    r.add_argument("--gens", type=int, default=0, help="0 = run until --until")
    r.add_argument("--until", default="")
    dist.add_arg(r)
    s = sub.add_parser("status")
    s.add_argument("name")
    a = ap.parse_args()
    {"run": run, "status": status}[a.cmd](a)


if __name__ == "__main__":
    main()
