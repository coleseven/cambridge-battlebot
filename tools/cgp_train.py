"""CGPANN training of per-character fighting networks (bots/neat runs the phenotypes).

Cartesian Genetic Programming of ANNs (Khan, Khan & Miller): a genome is a fixed row of N nodes, each
with an activation function, a bias and A weighted inputs taken from any earlier node or any of the NF
option features; an output gene picks the scoring node. Only nodes reachable from the output are
expressed, so the network grows and shrinks freely, and the worst case (N x A links) fits the bot's
fuel cap by construction (N=40, A=4 -> 160 links).

Search: per hero, K islands each running a (1+lambda) evolution strategy. Every generation the parent
is re-scored alongside its children on the same opponents (they change as elites evolve); a child that
scores >= the parent replaces it (neutral drift). Mutation follows Goldman & Punch's "single active
mutation": genes are mutated until one that the phenotype uses has changed, so no evaluation is spent
on an identical network.

Warm start: the first nodes encode a weighted-sum tree equal to a CMA duel run's linear scorer, so
generation 0 plays exactly like it.

  .venv/Scripts/python -u tools/cgp_train.py run c1 --seed-from d2 [--gens 1000] [--islands 6] [--lam 4]
      [--opps 4] [--slots 8] [--jobs 4] [--resign 800]
  .venv/Scripts/python tools/cgp_train.py export c1

N-v-N stage (tools/nvn.py has the matchups, fitness credit and map lookup):
  .venv/Scripts/python -u tools/cgp_train.py run c2v2 --team-size 2 --warm-run c2 [--islands 3] [--lam 3]
      [--pairings 5] [--duels 2] [--slots 8] [--jobs 4] [--warm-arena <other .arena>]
"""
import argparse
import json
import math
import pickle
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import dist  # noqa: E402
import duel_train  # noqa: E402
import neat_train as nt  # noqa: E402
import nvn  # noqa: E402

PICKS, ROWS, NF, NF_V1 = nt.PICKS, nt.ROWS, nt.NF, nt.NF_V1
N_NODES, ARITY, N_FUNCS = 40, 4, 4          # functions: 0 identity, 1 tanh, 2 sigmoid, 3 relu
OUT = arena.ARENA / "cgp"


# ---------------------------------------------------------------- genome
# A genome's "nf" is its feature count: sources < nf are option features, nf + j is node j. 1v1 genomes
# (no "nf" key) have the 25 duel features; N-v-N runs widen them to NF (widen()), adding the team
# features as unused inputs. Programs always use the bot's NF slot layout.
def nf_of(g):
    return g.get("nf", NF_V1)


def random_node(j, rng, nf=NF_V1):
    return {"f": rng.randrange(N_FUNCS), "b": rng.gauss(0, 0.5),
            "in": [[rng.randrange(nf + j), rng.gauss(0, 1.0)] for _ in range(ARITY)]}


def widen(g, nf=NF):
    """Same network with nf features (the extra ones unused): node sources shift up."""
    old = nf_of(g)
    sh = lambda s: s + nf - old if s >= old else s   # noqa: E731
    return {"nodes": [{"f": n["f"], "b": n["b"], "in": [[sh(s), w] for s, w in n["in"]]} for n in g["nodes"]],
            "out": sh(g["out"]), "nf": nf}


def seeded_genome(w, rng):
    """Weighted-sum tree reproducing the linear scorer sum(w_i x_i); remaining nodes random/inactive."""
    nf = len(w)
    nodes, level = [], [(i, float(w[i])) for i in range(nf)]   # (source index, weight)
    while len(level) > 1:
        nxt = []
        for k in range(0, len(level), ARITY):
            grp = level[k:k + ARITY]
            ins = [[src, wt] for src, wt in grp] + [[0, 0.0]] * (ARITY - len(grp))
            nodes.append({"f": 0, "b": 0.0, "in": ins})
            nxt.append((nf + len(nodes) - 1, 1.0))
        level = nxt
    out = level[0][0]
    while len(nodes) < N_NODES:
        nodes.append(random_node(len(nodes), rng, nf))
    return {"nodes": nodes, "out": out} if nf == NF_V1 else {"nodes": nodes, "out": out, "nf": nf}


def active(g):
    """Indices of expressed nodes, ascending (sources always precede their node)."""
    nf = nf_of(g)
    need, act = [g["out"]], set()
    while need:
        s = need.pop()
        if s >= nf and (s - nf) not in act:
            act.add(s - nf)
            need += [src for src, _ in g["nodes"][s - nf]["in"]]
    return sorted(act)


def mutate(g, rng):
    """Single active mutation: change random genes until one used by the phenotype has changed."""
    nf = nf_of(g)
    g = {"nodes": [{"f": n["f"], "b": n["b"], "in": [list(c) for c in n["in"]]} for n in g["nodes"]], "out": g["out"],
         **({"nf": g["nf"]} if "nf" in g else {})}
    act = set(active(g))
    while True:
        j = rng.randrange(N_NODES + 1)
        if j == N_NODES:                      # output gene
            g["out"] = nf + rng.randrange(N_NODES)
            return g
        n = g["nodes"][j]
        kind = rng.randrange(2 + 2 * ARITY)
        if kind == 0:
            n["f"] = rng.choice([f for f in range(N_FUNCS) if f != n["f"]])
        elif kind == 1:
            n["b"] = n["b"] + rng.gauss(0, 0.3) if rng.random() < 0.9 else rng.gauss(0, 0.5)
        else:
            c = n["in"][(kind - 2) // 2]
            if (kind - 2) % 2 == 0:
                c[0] = rng.randrange(nf + j)
            else:
                c[1] = c[1] + rng.gauss(0, 0.3) if rng.random() < 0.9 else rng.gauss(0, 1.0)
        if j in act:
            return g


def program(g):
    """Bot program words (same format as NEAT phenotypes, see bots/neat/src/genome.h)."""
    act, nf = active(g), nf_of(g)
    slot = {nf + j: NF + r for r, j in enumerate(act)}
    sl = lambda s: s if s < nf else slot[s]   # noqa: E731
    words = [len(act)]
    for j in act:
        n = g["nodes"][j]
        words += [sl(nf + j), n["f"], nt._f(n["b"]), nt._f(1.0), ARITY]
        for src, w in n["in"]:
            words += [sl(src), nt._f(w)]
    words.append(sl(g["out"]))
    return words


def n_links(g):
    return len(active(g)) * ARITY


# ---------------------------------------------------------------- evaluation
def evaluate(progs, p, opps, layout, cand_slots, elite_bot, a, anchor=None):
    """Mean duel score of each program (as hero p) against the elite bot over opps x both seats."""
    fits = [0.0] * len(progs)
    for k in range(0, len(progs), len(cand_slots)):
        idx = list(range(k, min(k + len(cand_slots), len(progs))))
        games, meta = [], []
        for i, slot in zip(idx, cand_slots):
            bot = nt.patch(slot, nt.blob({r: progs[i] for r in ROWS[p]}, a.resign), f"c{i}")
            for j, q in enumerate(opps):       # first half vs evolving elites, second half vs the fixed anchor
                opp = elite_bot if anchor is None or j < (len(opps) + 1) // 2 else anchor
                games.append((bot, opp, nt.map_path(layout, p, q))); meta.append((i, 0))
                games.append((opp, bot, nt.map_path(layout, q, p))); meta.append((i, 1))
        res = arena.play_many(games, a.jobs)
        acc = {}
        for (i, seat), r in zip(meta, res):
            if "error" not in r and a.resign and r["round"] > a.resign + 2:
                r = {"error": "slot rebuilt (genome lost)"}
            acc.setdefault(i, []).append(duel_train.side_score(r, seat))
        for i, sc in acc.items():
            fits[i] = sum(sc) / len(sc)
    return fits


def save(path, st):
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(st))
    tmp.replace(path)


def run_nvn(a):
    """N-v-N stage (tools/nvn.py): the 1v1 islands of --warm-run, widened to the team features."""
    n = a.team_size
    d = OUT / a.name
    d.mkdir(parents=True, exist_ok=True)
    ck = d / "state.pkl"
    warm = Path(a.warm_arena) if a.warm_arena else arena.ARENA
    if ck.exists():
        st = pickle.loads(ck.read_bytes())
        print(f"resuming {n}v{n} at generation {st['gen']}", flush=True)
    else:
        src = pickle.loads((warm / "cgp" / a.warm_run / "state.pkl").read_bytes())     # read-only
        st = {"gen": 0, "team_size": n, "warm": f"{warm / 'cgp' / a.warm_run} gen {src['gen']}",
              "islands": {p: [{"g": widen(isl["g"]), "fit": None} for isl in src["islands"][p][:a.islands]]
                          for p in ROWS},
              "elite": {p: widen(g) for p, g in src["elite"].items()}, "rng": random.Random(12).getstate()}
        print(f"{n}v{n}: warm-started {len(ROWS)} heroes x {a.islands} islands from {st['warm']}", flush=True)
    assert st["team_size"] == n, f"run {a.name} is {st['team_size']}v{st['team_size']}"
    rng = random.Random()
    rng.setstate(st["rng"])
    slots = nt.build_slots(a.slots + 2, "cgpslots")
    elite_slot, anchor = slots[0], nt.anchor_bot(slots[1], a.resign)
    cand_slots = slots[2:]
    log = d / "log.jsonl"
    for _ in range(a.gens):
        t0 = time.perf_counter()
        gen = st["gen"] + 1
        champs = nvn.champion_set(n, warm)
        team_progs = {q: champs.get(q) or program(st["elite"][q]) for q in ROWS}
        elite_bot = nt.patch(elite_slot, nvn.team_blob(nt, team_progs, a.resign), "elite")
        summary = {}
        scheds, cands_of = {}, {}
        for p, islands in st["islands"].items():         # mutate every hero first: same rng order as before
            scheds[p] = nvn.schedule(n, gen, p, a.pairings, a.duels, seed=1)
            cands = []                                   # (island, genome); parent first per island
            for i, isl in enumerate(islands):
                cands.append((i, isl["g"]))
                cands += [(i, mutate(isl["g"], rng)) for _ in range(a.lam)]
            cands_of[p] = cands
        if not a.per_hero:                               # whole generation in one batch
            batch = nvn.evaluate_many(nt, [(p, [program(g) for _, g in cands_of[p]], scheds[p])
                                           for p in st["islands"]], team_progs, elite_bot, anchor, cand_slots, a)
        for p, islands in st["islands"].items():
            sched, cands = scheds[p], cands_of[p]
            if a.per_hero:
                res = nvn.evaluate(nt, [program(g) for _, g in cands], p, sched, team_progs, elite_bot, anchor,
                                   cand_slots, a)
            else:
                res = batch[p]
            fits = [f - 0.002 * n_links(g) for (f, _, _), (_, g) in zip(res, cands)]
            split = {}
            for i, isl in enumerate(islands):
                group = [(f, k) for k, (f, (ii, _)) in enumerate(zip(fits, cands)) if ii == i]
                parent_f, parent_k = group[0]
                best_f, best_k = max(group[1:], key=lambda t: t[0])
                k = best_k if best_f >= parent_f else parent_k      # neutral drift: ties go to the child
                isl["g"], isl["fit"] = cands[k][1], fits[k]
                split[i] = res[k][1:]
            ti = max(range(len(islands)), key=lambda i: islands[i]["fit"])
            st["elite"][p] = islands[ti]["g"]
            fn, f1 = split[ti]
            summary[PICKS[p]] = [round(islands[ti]["fit"], 2), fn and round(fn, 2), f1 and round(f1, 2),
                                 len(active(islands[ti]["g"]))]
        st["gen"] = gen
        st["rng"] = rng.getstate()
        save(ck, st)
        secs = time.perf_counter() - t0
        mean = lambda i: round(sum(v[i] for v in summary.values() if v[i] is not None) / len(summary), 3)  # noqa: E731
        with log.open("a") as fh:
            dst = dist.take_stats(a.dist) if a.dist else None
            fh.write(json.dumps({"gen": gen, "team_size": n, "secs": round(secs), **({"dist": dst} if dst else {}),
                                 "mean_fit": mean(0),
                                 f"mean_f{n}v{n}": mean(1), "mean_f1v1": mean(2), "best": summary}) + "\n")
        print(f"gen {gen:4} {n}v{n} {secs:6.0f}s  fit {mean(0):+.2f}  {n}v{n} {mean(1):+.2f}  1v1 {mean(2):+.2f}  " +
              " ".join(f"{k}:{v[1]:+.1f}/{v[2]:+.1f}/{v[3]}n" for k, v in summary.items()), flush=True)


def run(a):
    if a.team_size > 1:
        return run_nvn(a)
    d = OUT / a.name
    d.mkdir(parents=True, exist_ok=True)
    ck = d / "state.pkl"
    if ck.exists():
        st = pickle.loads(ck.read_bytes())
        print(f"resuming at generation {st['gen']}", flush=True)
    else:
        rng = random.Random(11)
        seeds = nt.seed_rows(a.seed_from)
        st = {"gen": 0, "islands": {}, "elite": {}, "seed_from": a.seed_from, "rng": rng.getstate()}
        for p, rows in ROWS.items():
            base = seeded_genome(seeds[p], rng)
            st["islands"][p] = [{"g": base if i == 0 else mutate(base, rng), "fit": None} for i in range(a.islands)]
            st["elite"][p] = base
        print(f"seeded {len(ROWS)} heroes x {a.islands} islands from {a.seed_from}", flush=True)
    rng = random.Random()
    rng.setstate(st["rng"])
    slots = nt.build_slots(a.slots + 2, "cgpslots")
    elite_slot, anchor = slots[0], nt.anchor_bot(slots[1], a.resign)
    cand_slots = slots[2:]
    log = d / "log.jsonl"
    for _ in range(a.gens):
        t0 = time.perf_counter()
        gen = st["gen"] + 1
        grng = random.Random(10_000 + gen)
        layout = grng.choice(["open", "pillars"])
        summary = {}
        for p, islands in st["islands"].items():
            champs = (nt.champion_programs() if a.opp == "champions" else None) or {}
            elite_bot = nt.patch(elite_slot, nt.blob({r: champs.get(q) or program(st["elite"][q])
                                                      for q in ROWS for r in ROWS[q]}, a.resign), "elite")
            opps = grng.sample(range(len(PICKS)), a.opps)
            cands = []                                   # (island, genome); parent first per island
            for i, isl in enumerate(islands):
                cands.append((i, isl["g"]))
                cands += [(i, mutate(isl["g"], rng)) for _ in range(a.lam)]
            fits = evaluate([program(g) for _, g in cands], p, opps, layout, cand_slots, elite_bot, a, anchor)
            fits = [f - 0.002 * n_links(g) for f, (_, g) in zip(fits, cands)]
            for i, isl in enumerate(islands):
                group = [(f, k) for k, (f, (ii, _)) in enumerate(zip(fits, cands)) if ii == i]
                parent_f = group[0][0]
                best_f, best_k = max(group[1:], key=lambda t: t[0])
                if best_f >= parent_f:                   # neutral drift: ties go to the child
                    isl["g"], isl["fit"] = cands[best_k][1], best_f
                else:
                    isl["fit"] = parent_f
            top = max(islands, key=lambda isl: isl["fit"])
            st["elite"][p] = top["g"]
            summary[PICKS[p]] = (round(top["fit"], 2), len(active(top["g"])),
                                 round(sum(len(active(isl["g"])) for isl in islands) / len(islands), 1))
        st["gen"] = gen
        st["rng"] = rng.getstate()
        save(ck, st)
        with log.open("a") as fh:
            fh.write(json.dumps({"gen": gen, "layout": layout, "best": summary}) + "\n")
        print(f"gen {gen:4} {layout:7} {time.perf_counter() - t0:6.0f}s  " +
              " ".join(f"{k}:{v[0]:+.1f}/{v[1]}n/avg{v[2]}" for k, v in summary.items()), flush=True)


def export(a):
    d = OUT / a.name
    st = pickle.loads((d / "state.pkl").read_bytes())
    out = {PICKS[p]: program(g) for p, g in st["elite"].items()}
    tmp = d / "elites.tmp"
    tmp.write_text(json.dumps({"gen": st["gen"], "nf": NF, "team_size": st.get("team_size", 1), "programs": out}))
    tmp.replace(d / "elites.json")
    print(f"gen {st['gen']}: wrote {d / 'elites.json'}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("name"); r.add_argument("--seed-from", default="d2")
    r.add_argument("--gens", type=int, default=1000); r.add_argument("--islands", type=int, default=6)
    r.add_argument("--lam", type=int, default=4); r.add_argument("--opps", type=int, default=4)
    r.add_argument("--slots", type=int, default=8); r.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    r.add_argument("--resign", type=int, default=800)
    r.add_argument("--opp", choices=["champions", "own"], default="champions",
                   help="evolving opponents: the cross-method champion set (default) or this run's own elites")
    nvn.add_args(r)
    e = sub.add_parser("export"); e.add_argument("name")
    a = ap.parse_args()
    {"run": run, "export": export}[a.cmd](a)


if __name__ == "__main__":
    main()
