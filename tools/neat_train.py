"""NEAT training of per-character fighting networks (bots/neat) in 1v1 objective duels.

Each draft pick has its own NEAT population (C&I: one network drives both bodies). A generation
evaluates every population against the current elite network of each sampled opponent pick, both
seats, on the gem-cluster duel arenas. Genomes are not compiled: a fixed set of pre-built "slot"
bots has its genome block patched in place (see bots/neat/src/genome.h), so a new genome costs a
file write. Populations are seeded from a CMA duel run's linear weights (curriculum warm start):
generation 0 plays exactly like that run, and NEAT adds structure from there.

  .venv/Scripts/python -u tools/neat_train.py run n1 --seed-from d2 [--gens 1000] [--pop 30]
      [--opps 4] [--slots 8] [--jobs 4] [--resign 800]
  .venv/Scripts/python tools/neat_train.py export n1        # elite networks -> .arena/neat/n1/elites.json

N-v-N stage (tools/nvn.py has the matchups, fitness credit and map lookup):
  .venv/Scripts/python -u tools/neat_train.py run n2v2 --team-size 2 --warm-run n2 [--pop 12]
      [--pairings 5] [--duels 2] [--slots 8] [--jobs 4] [--warm-arena <other .arena>]

Structure control (Green, "Phased Searching with NEAT"): each population alternates between a
complexifying phase (add/delete mutations) and a simplifying phase (deletions only). It switches to
simplifying when mean complexity (nodes + enabled links) rises PHASE_RISE above the floor set at the
last switch, and back once simplification has not lowered mean complexity for PHASE_STALL generations.
Activation functions mutate per node. A small per-link fitness cost breaks ties toward smaller nets.
Networks over MAX_NODES/MAX_LINKS are rejected (bot-side fuel cap: top-12 options, <=35k fuel).

Resumable: state is pickled every generation to .arena/neat/<run>/state.pkl.
"""
import argparse
import json
import pickle
import random
import struct
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import dist  # noqa: E402
import duel_train  # noqa: E402
import nvn  # noqa: E402

import neat  # noqa: E402

PICKS = duel_train.PICKS
ROWS = duel_train.ROWS          # pick -> champion_type rows (C&I -> [3, 4])
NF = nvn.NF            # bot features (31); hidden/output program slots start here
NF_V1 = nvn.NF_V1      # duel features (25): input count of 1v1 genomes
MAX_NODES, MAX_LINKS, WORDS = 64, 160, 16384
MAGIC = struct.pack("<II", 0x4E454154, 0x424C4F42)
ACT = {"identity": 0, "tanh": 1, "sigmoid": 2, "relu": 3}
OUT = arena.ARENA / "neat"
SRC = arena.ROOT / "bots" / "neat"

CONFIG = f"""
[NEAT]
fitness_criterion     = max
fitness_threshold     = 1000000
pop_size              = {{pop}}
reset_on_extinction   = True
no_fitness_termination = True

[DefaultGenome]
num_inputs              = {{nin}}
num_hidden              = 0
num_outputs             = 1
initial_connection      = full_direct
feed_forward            = True
compatibility_disjoint_coefficient = 1.0
compatibility_weight_coefficient   = 0.5
conn_add_prob           = 0.3
conn_delete_prob        = 0.15
node_add_prob           = 0.1
node_delete_prob        = 0.05
activation_default      = identity
activation_options      = identity tanh relu sigmoid
activation_mutate_rate  = 0.05
aggregation_default     = sum
aggregation_options     = sum
aggregation_mutate_rate = 0.0
bias_init_mean          = 0.0
bias_init_stdev         = 0.5
bias_init_type          = gaussian
bias_max_value          = 30.0
bias_min_value          = -30.0
bias_mutate_power       = 0.3
bias_mutate_rate        = 0.5
bias_replace_rate       = 0.05
response_init_mean      = 1.0
response_init_stdev     = 0.0
response_init_type      = gaussian
response_max_value      = 30.0
response_min_value      = -30.0
response_mutate_power   = 0.0
response_mutate_rate    = 0.0
response_replace_rate   = 0.0
weight_init_mean        = 0.0
weight_init_stdev       = 1.0
weight_init_type        = gaussian
weight_max_value        = 30.0
weight_min_value        = -30.0
weight_mutate_power     = 0.3
weight_mutate_rate      = 0.8
weight_replace_rate     = 0.05
enabled_default         = True
enabled_mutate_rate     = 0.01
enabled_rate_to_true_add  = 0.0
enabled_rate_to_false_add = 0.0
single_structural_mutation = False
structural_mutation_surer  = default
node_gene_type          = DefaultNodeGene
connection_gene_type    = DefaultConnectionGene

[DefaultSpeciesSet]
compatibility_threshold = 3.0

[DefaultStagnation]
species_fitness_func = max
max_stagnation       = 20
species_elitism      = 2

[DefaultReproduction]
elitism            = 2
survival_threshold = 0.2
min_species_size   = 2
"""


PHASE_RISE, PHASE_STALL = 20.0, 5
COMPLEXIFY = {"conn_add_prob": 0.3, "node_add_prob": 0.1, "conn_delete_prob": 0.15, "node_delete_prob": 0.05}
SIMPLIFY = {"conn_add_prob": 0.0, "node_add_prob": 0.0, "conn_delete_prob": 0.5, "node_delete_prob": 0.3}


def complexity(pop):
    gs = list(pop.population.values())
    return sum(len(g.nodes) + sum(1 for c in g.connections.values() if c.enabled) for g in gs) / max(1, len(gs))


def set_phase(pop, mode):
    for k, v in (COMPLEXIFY if mode == "complexify" else SIMPLIFY).items():
        setattr(pop.config.genome_config, k, v)


def update_phase(ph, pop):
    """Phased searching: returns the (possibly switched) phase record after a generation."""
    mc = complexity(pop)
    if ph["mode"] == "complexify":
        if mc > ph["floor"] + PHASE_RISE:
            ph.update(mode="simplify", best=mc, stall=0)
    else:
        if mc < ph["best"] - 0.5:
            ph.update(best=mc, stall=0)
        else:
            ph["stall"] += 1
            if ph["stall"] >= PHASE_STALL:
                ph.update(mode="complexify", floor=mc)
    set_phase(pop, ph["mode"])
    ph["mean_complexity"] = round(mc, 1)
    return ph


def make_config(run_dir: Path, pop: int, tag: str = "", nin: int = NF_V1):
    path = run_dir / f"neat{tag}.cfg"
    path.write_text(CONFIG.replace("{pop}", str(pop)).replace("{nin}", str(nin)))
    return neat.Config(neat.DefaultGenome, neat.DefaultReproduction, neat.DefaultSpeciesSet,
                       neat.DefaultStagnation, str(path))


# ---------------------------------------------------------------- genome -> program words
def program(genome, config):
    """Feed-forward program words for the bot, or None if over the size caps."""
    net = neat.nn.FeedForwardNetwork.create(genome, config)
    slots = {k: -k - 1 for k in config.genome_config.input_keys}
    for node, *_ in net.node_evals:
        if node not in slots:
            slots[node] = NF + len([s for s in slots.values() if s >= NF])
    if len(net.node_evals) > MAX_NODES or max(slots.values()) >= NF + MAX_NODES:
        return None
    if sum(len(ev[5]) for ev in net.node_evals) > MAX_LINKS:
        return None
    out_key = config.genome_config.output_keys[0]
    if out_key not in slots:          # output unreachable: constant score
        slots[out_key] = NF
    words = [len(net.node_evals)]
    for node, act, _agg, bias, resp, links in net.node_evals:
        name = genome.nodes[node].activation
        words += [slots[node], ACT.get(name, 0), _f(bias), _f(resp), len(links)]
        for src, w in links:
            words += [slots[src], _f(w)]
    words.append(slots[out_key])
    return words


def _f(x):
    return struct.unpack("<I", struct.pack("<f", float(x)))[0]


def blob(programs: dict, resign: int):
    """programs: champion_type -> program words."""
    words = [0x4E454154, 0x424C4F42, WORDS, 2, resign] + [0] * 12   # format 2: NF-feature slots
    for t, prog in programs.items():
        words[5 + t] = len(words)
        words += prog
    if len(words) > WORDS:
        raise ValueError("genome block overflow")
    return struct.pack(f"<{len(words)}I", *words)


# ---------------------------------------------------------------- slot bots
def build_slots(n, kind="neatslots"):
    """Pre-built network-bot copies whose genome block is patched per genome (one dir per trainer kind)."""
    slots = []
    for i in range(n):
        d = arena.ARENA / kind / f"s{i:02d}"
        if not (d / "src").exists():
            import shutil
            shutil.copytree(SRC / "src", d / "src")
        slots.append(arena.build(d, f"s{i:02d}").path)
    return slots


def patch(slot_dir: Path, data: bytes, name: str):
    for n in ("bot.wasm", "bot-injected.wasm"):
        p = slot_dir / ".cambc" / "build" / n
        b = bytearray(p.read_bytes())
        i = b.find(MAGIC)
        if i < 0:
            raise RuntimeError(f"no genome block in {p}")
        b[i:i + len(data)] = data
        b[i + len(data):i + WORDS * 4] = bytes(WORDS * 4 - len(data))
        p.write_bytes(bytes(b))
    return arena.Bot(name, slot_dir, arena.sha(slot_dir / ".cambc" / "build" / "bot-injected.wasm"))


def pack_pop(pop):
    """Picklable snapshot (neat Population itself holds an itertools.count)."""
    return {"generation": pop.generation, "config": pop.config, "population": pop.population,
            "species": pop.species, "tracker": getattr(pop.reproduction, "innovation_tracker", None)}


def unpack_pop(snap):
    import itertools
    pop = neat.Population(snap["config"], (snap["population"], snap["species"], snap["generation"]))
    if snap["tracker"] is not None:
        pop.reproduction.innovation_tracker = snap["tracker"]
        snap["config"].genome_config.innovation_tracker = snap["tracker"]
    pop.reproduction.genome_indexer = itertools.count(max(snap["population"]) + 1)
    return pop


def save_state(path, st):
    snap = dict(st, pops={p: pack_pop(pop) for p, pop in st["pops"].items()}, random=random.getstate())
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(snap))
    tmp.replace(path)    # atomic: a crash mid-write never corrupts the last good checkpoint


def load_state(path):
    st = pickle.loads(path.read_bytes())
    random.setstate(st.pop("random"))
    st["pops"] = {p: unpack_pop(snap) for p, snap in st["pops"].items()}
    for p, pop in st["pops"].items():
        set_phase(pop, st["phase"][p]["mode"])
    return st


def seed_rows(seed_from, min_vs_prior=0.0):
    """Per-hero seed weights: the CMA run's row where it beats the hand prior (row mean of the
    run's matrix vs prior >= min_vs_prior), otherwise the prior row. Returns {pick: weights}."""
    import numpy as np
    learned = duel_train.current_mean(pickle.loads((duel_train.OUT / seed_from / "state.pkl").read_bytes()))
    prior = duel_train.prior_matrix()
    mpath = duel_train.OUT / f"matrix_{seed_from}_vsprior.json"
    vs = None
    if mpath.exists():
        tab = json.loads(mpath.read_text(encoding="utf-8"))["table"]
        vs = {p: sum(tab[p]) / len(tab[p]) for p in range(len(PICKS))}
    out = {}
    for p, rows in ROWS.items():
        use_learned = vs is None or vs[p] >= min_vs_prior
        out[p] = np.mean([(learned if use_learned else prior)[r][:NF_V1] for r in rows], axis=0)
        print(f"  seed {PICKS[p]:9} <- {'learned' if use_learned else 'prior'}"
              + (f" (vs prior {vs[p]:+.1f})" if vs else ""), flush=True)
    return out


def anchor_bot(slot_dir, resign):
    """Fixed reference opponent: a network bot with no programs, i.e. the hand-prior linear scorer."""
    return patch(slot_dir, blob({}, resign), "anchor")


def champion_programs():
    """Current per-hero champion set (tools/champions.py), or None."""
    path = arena.ARENA / "champions" / "current.json"
    return nvn.load_programs(path) if path.exists() else None


def map_path(layout, a, b):
    return arena.ROOT / "maps_duel" / "1v1" / f"{layout}__{PICKS[a]}-{PICKS[b]}.map26a"


# ---------------------------------------------------------------- training
def seed_population(pop, config, row_weights, rng):
    for g in pop.population.values():
        for (i, o), cg in g.connections.items():
            if o == config.genome_config.output_keys[0] and i < 0:
                cg.weight = float(row_weights[-i - 1]) + rng.gauss(0, 0.05)
        out = g.nodes[config.genome_config.output_keys[0]]
        out.bias, out.response, out.activation = 0.0, 1.0, "identity"


def widen(pop, size):
    """Warm start for N-v-N: give a 1v1 population the NF team-feature inputs (unconnected, so every
    genome scores exactly as before) and cut it to `size` genomes, elites first."""
    gc = pop.config.genome_config
    gc.num_inputs = NF
    gc.input_keys = [-i - 1 for i in range(NF)]
    pop.config.pop_size = size
    if len(pop.population) > size:
        keep = sorted(pop.population.items(), key=lambda kv: -1e9 if kv[1].fitness is None else -kv[1].fitness)
        pop.population = dict(keep[:size])
    pop.species.speciate(pop.config, pop.population, pop.generation)


def run_nvn(a):
    """N-v-N stage (tools/nvn.py): the 1v1 populations of --warm-run, widened to the team features."""
    n = a.team_size
    d = OUT / a.name
    d.mkdir(parents=True, exist_ok=True)
    ck = d / "state.pkl"
    warm = Path(a.warm_arena) if a.warm_arena else arena.ARENA
    if ck.exists():
        st = load_state(ck)
        print(f"resuming {n}v{n} at generation {st['gen']}", flush=True)
    else:
        src = load_state(warm / "neat" / a.warm_run / "state.pkl")       # read-only
        st = {"gen": 0, "pops": src["pops"], "elites": src["elites"], "phase": src["phase"], "team_size": n,
              "warm": f"{warm / 'neat' / a.warm_run} gen {src['gen']}"}
        for pop in st["pops"].values():
            widen(pop, a.pop)
        print(f"{n}v{n}: warm-started {len(st['pops'])} populations from {st['warm']}", flush=True)
    assert st["team_size"] == n, f"run {a.name} is {st['team_size']}v{st['team_size']}"
    slots = build_slots(a.slots + 2)
    elite_slot, anchor = slots[0], anchor_bot(slots[1], a.resign)
    cand_slots = slots[2:]
    log = d / "log.jsonl"
    for _ in range(a.gens):
        t0 = time.perf_counter()
        gen = st["gen"] + 1
        champs = nvn.champion_set(n, warm)
        team_progs = {q: champs.get(q) or program(st["elites"][q], st["pops"][q].config) for q in ROWS}
        elite_bot = patch(elite_slot, nvn.team_blob(sys.modules[__name__], team_progs, a.resign), "elite")
        summary = {}
        scheds = {p: nvn.schedule(n, gen, p, a.pairings, a.duels) for p in st["pops"]}
        pre = {}
        if not a.per_hero:
            # Whole generation in one batch: every hero's genomes, in the order pop.run will pass them.
            # Evaluation uses no shared randomness, so this equals the per-hero path game for game.
            items_of = {p: list(pop.population.items()) for p, pop in st["pops"].items()}
            res = nvn.evaluate_many(sys.modules[__name__],
                                    [(p, [program(g, st["pops"][p].config) for _, g in items_of[p]], scheds[p])
                                     for p in st["pops"]], team_progs, elite_bot, anchor, cand_slots, a)
            pre = {p: {gid: r for (gid, _), r in zip(items_of[p], res[p])} for p in st["pops"]}
        for p, pop in st["pops"].items():
            sched = scheds[p]

            def evaluate(genomes, cfg, p=p, sched=sched):
                items = list(genomes)
                if pre:
                    res = [pre[p][gid] for gid, _ in items]
                else:
                    res = nvn.evaluate(sys.modules[__name__], [program(g, cfg) for _, g in items], p, sched,
                                       team_progs, elite_bot, anchor, cand_slots, a)
                for (_, g), (f, fn, f1) in zip(items, res):
                    g.fitness = f - 0.002 * len(g.connections)
                    g.f_split = (fn, f1)
                best = max((g for _, g in items), key=lambda g: g.fitness)
                st["elites"][p] = best
                fn, f1 = best.f_split
                summary[PICKS[p]] = [round(best.fitness, 2), fn and round(fn, 2), f1 and round(f1, 2),
                                     len(best.nodes), len(best.connections)]

            pop.run(evaluate, 1)
            ph = update_phase(st["phase"][p], pop)
            summary[PICKS[p]] += [ph["mode"][0], ph["mean_complexity"]]
        st["gen"] = gen
        save_state(ck, st)
        secs = time.perf_counter() - t0
        mean = lambda i: round(sum(v[i] for v in summary.values() if v[i] is not None) / len(summary), 3)  # noqa: E731
        with log.open("a") as fh:
            dst = dist.take_stats(a.dist) if a.dist else None
            fh.write(json.dumps({"gen": gen, "team_size": n, "secs": round(secs), **({"dist": dst} if dst else {}),
                                 "mean_fit": mean(0),
                                 f"mean_f{n}v{n}": mean(1), "mean_f1v1": mean(2), "best": summary}) + "\n")
        print(f"gen {gen:4} {n}v{n} {secs:6.0f}s  fit {mean(0):+.2f}  {n}v{n} {mean(1):+.2f}  1v1 {mean(2):+.2f}  " +
              " ".join(f"{k}:{v[1]:+.1f}/{v[2]:+.1f}/{v[3]}n{v[4]}c" for k, v in summary.items()), flush=True)


def run(a):
    if a.team_size > 1:
        return run_nvn(a)
    d = OUT / a.name
    d.mkdir(parents=True, exist_ok=True)
    config = make_config(d, a.pop)
    ck = d / "state.pkl"
    rng = random.Random(1)
    if ck.exists():
        st = load_state(ck)
        print(f"resuming at generation {st['gen']}", flush=True)
    else:
        seeds = seed_rows(a.seed_from)
        st = {"gen": 0, "pops": {}, "elites": {}, "phase": {}, "seed_from": a.seed_from}
        for p, rows in ROWS.items():
            pop = neat.Population(make_config(d, a.pop, f"_{PICKS[p]}"))   # own config: own phase
            seed_population(pop, config, seeds[p], rng)
            st["pops"][p] = pop
            st["phase"][p] = {"mode": "complexify", "floor": complexity(pop), "best": 0.0, "stall": 0}
            st["elites"][p] = next(iter(pop.population.values()))   # seed genome = linear policy
        print(f"seeded {len(ROWS)} populations from {a.seed_from}", flush=True)
    slots = build_slots(a.slots + 2)
    elite_slot, anchor = slots[0], anchor_bot(slots[1], a.resign)
    cand_slots = slots[2:]
    log = d / "log.jsonl"

    for _ in range(a.gens):
        t0 = time.perf_counter()
        gen = st["gen"] + 1
        grng = random.Random(gen)
        layout = grng.choice(["open", "pillars"])
        summary = {}
        for p, pop in st["pops"].items():
            elite_progs = {}
            champs = champion_programs() if a.opp == "champions" else None
            for q, eg in st["elites"].items():
                prog = (champs or {}).get(q) or program(eg, config)   # champion set first, own elite as fallback
                if prog:
                    for r in ROWS[q]:
                        elite_progs[r] = prog
            elite_bot = patch(elite_slot, blob(elite_progs, a.resign), "elite")
            opps = grng.sample(range(len(PICKS)), a.opps)

            def evaluate(genomes, _config, p=p, opps=opps, elite_bot=elite_bot):
                items = list(genomes)
                for k in range(0, len(items), len(cand_slots)):
                    batch = items[k:k + len(cand_slots)]
                    games, meta = [], []
                    for (gid, g), slot in zip(batch, cand_slots):
                        prog = program(g, config)
                        if prog is None:
                            g.fitness = -50.0
                            continue
                        bot = patch(slot, blob({r: prog for r in ROWS[p]}, a.resign), f"g{gid}")
                        for j, q in enumerate(opps):   # first half vs evolving elites, second half vs the fixed anchor
                            opp = elite_bot if j < (len(opps) + 1) // 2 else anchor
                            games.append((bot, opp, map_path(layout, p, q))); meta.append((g, 0))
                            games.append((opp, bot, map_path(layout, q, p))); meta.append((g, 1))
                    res = arena.play_many(games, a.jobs)
                    scores = {}
                    for (g, seat), r in zip(meta, res):
                        if "error" not in r and a.resign and r["round"] > a.resign + 2:
                            r = {"error": "slot rebuilt (genome lost)"}
                        scores.setdefault(id(g), [g, []])[1].append(duel_train.side_score(r, seat))
                    for g, sc in scores.values():
                        g.fitness = sum(sc) / len(sc) - 0.002 * len(g.connections)
                best = max((g for _, g in items), key=lambda g: g.fitness)
                st["elites"][p] = best
                summary[PICKS[p]] = (round(best.fitness, 2), len(best.nodes), len(best.connections))

            pop.run(evaluate, 1)
            ph = update_phase(st["phase"][p], pop)
            summary[PICKS[p]] += (ph["mode"][0], ph["mean_complexity"])
        st["gen"] = gen
        save_state(ck, st)
        with log.open("a") as fh:
            fh.write(json.dumps({"gen": gen, "layout": layout, "best": summary}) + "\n")
        print(f"gen {gen:4} {layout:7} {time.perf_counter() - t0:6.0f}s  " +
              " ".join(f"{k}:{v[0]:+.1f}/{v[1]}n{v[2]}c/{v[3]}{v[4]}" for k, v in summary.items()), flush=True)


def export(a):
    d = OUT / a.name
    st = load_state(d / "state.pkl")
    out = {PICKS[p]: program(g, st["pops"][p].config) for p, g in st["elites"].items()}
    data = json.dumps({"gen": st["gen"], "nf": NF, "team_size": st.get("team_size", 1), "programs": out})
    tmp = d / "elites.tmp"
    tmp.write_text(data)
    tmp.replace(d / "elites.json")
    # Keep every export: tools/progress.py scores these snapshots against fixed opponents (a progress curve).
    snap = d / "snapshots" / f"gen{st['gen']:04d}.json"
    snap.parent.mkdir(exist_ok=True)
    if not snap.exists():
        tmp.write_text(data)
        tmp.replace(snap)
    print(f"gen {st['gen']}: wrote {d / 'elites.json'} (+ snapshots/{snap.name})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("name"); r.add_argument("--seed-from", default="d2")
    r.add_argument("--gens", type=int, default=1000); r.add_argument("--pop", type=int, default=30)
    r.add_argument("--opps", type=int, default=4); r.add_argument("--slots", type=int, default=8)
    r.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS); r.add_argument("--resign", type=int, default=800)
    r.add_argument("--opp", choices=["champions", "own"], default="champions",
                   help="evolving opponents: the cross-method champion set (default) or this run's own elites")
    nvn.add_args(r)
    e = sub.add_parser("export"); e.add_argument("name")
    a = ap.parse_args()
    {"run": run, "export": export}[a.cmd](a)


if __name__ == "__main__":
    main()
