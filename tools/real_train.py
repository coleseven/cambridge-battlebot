"""Real-map fighter training: NEAT on the per-hero networks INSIDE the combined bot (tools/team_bot.py), on the
published 5v5 map pool, scored by the real game result.

The strategy layer (forager) plays everything except close fights (FIGHTER_ENGAGE_RANGE); the network decides
the fights. Each candidate network for hero p plays K real games as part of a team drafted around p (the
composition travels in the genome blob's draft block, tools/team_bot.py), against an opponent pool, both seats.
All candidates of a hero play the same games (common random numbers).

  .venv/Scripts/python tools/real_train.py run r1 --warm-run n3v3 [--pop 16] [--games 6] [--jobs 12]
        [--opps sparring-tarot v1 forager frozen] [--until 11:50] [--gens 0 (= until the deadline)]
  .venv/Scripts/python tools/real_train.py run r2 --warm-run real1 --team team-director   (inside the director)
  .venv/Scripts/python tools/real_train.py status r1

First run: copies the warm run's populations (read-only), freezes its elites as frozen_start.json (an opponent
and the revert point), and snapshots bots/team/src as the run's fixed base (team_src/), so regenerating bots/team
later doesn't change a running experiment. Checkpoints and exports elites (+ snapshots/) every generation.
Score per game (our side): +1 win / -1 loss, plus gem margin / 500; errored games -3.

--cma tools/space-director.json: shared CMA-ES of the director's params on the SAME games (docs/DIRECTOR.md).
Needs a team built with tools/team_bot.py --runtime-params <space> (the params travel in the genome blob). Each
generation asks --cma-pop candidates; every (hero, schedule slot) cell is given one candidate (balanced: the
cells are shuffled and dealt round-robin), and all network candidates of that hero play that cell with it, so
the network ranking within a hero keeps its common random numbers. A candidate's fitness is the mean score of
its ~games/pop share; the search stays within --cma-cap (normalised units) of the starting values. The frozen
and prior opponents keep the compiled (starting) params as a fixed yardstick.
"""
import argparse
import json
import pickle
import random
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import dist  # noqa: E402
import neat_train as nt  # noqa: E402
import nvn  # noqa: E402
import team_bot as tb  # noqa: E402
import tune  # noqa: E402

PICKS = nt.PICKS


def deadline(hhmm: str) -> float:
    h, m = map(int, hhmm.split(":"))
    now = datetime.now()
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return t.timestamp()


def build_slots(d: Path, n: int):
    """n built copies of the run's fixed team source (two per local thread)."""
    out = []
    for i in range(n):
        s = d / "slots" / f"s{i:02d}"
        if not (s / "src").exists():
            shutil.copytree(d / "team_src", s / "src")
        out.append(arena.build(s, f"real{i}").path)
    return out


DIRECTOR_WORDS = None    # learned director network baked into every game of the run (--director-net), or None


def with_dir(blob: bytes) -> bytes:
    """The run's learned director network appended to a genome blob (training then matches what ships)."""
    if not DIRECTOR_WORDS:
        return blob
    import director_train as dt          # local import: director_train imports this module
    return dt.with_director(blob, DIRECTOR_WORDS)


def opponent_bots(names, frozen_progs, ref_slot):
    """Opponents. Our own team-bot variants are BlobBots, so distributed workers can play them:
      frozen  our full bot with the networks this run started from
      prior   our full bot with the hand-prior fighter (no networks; plays about like the forager alone)
    Anything else (sparring-tarot, v1, forager) is a fixed copy under .arena/realopp/: those compile to different
    wasm on each machine (and the training PC lacks some sources), so they stay on this machine (not shippable)."""
    out = {}
    for name in names:
        if name == "frozen":
            out[name] = dist.virtual_bot(ref_slot, with_dir(nvn.team_blob(nt, frozen_progs, 0)), "frozen")
        elif name == "prior":
            out[name] = dist.virtual_bot(ref_slot, with_dir(nvn.team_blob(nt, {}, 0)), "prior")
        else:
            src = arena.ARENA / "realopp" / name
            if not (src / "src").exists():
                shutil.copytree(arena.ROOT / "bots" / name / "src", src / "src")
            out[name] = arena.build(src, name)
    return out


def score(r, seat):
    if "error" in r:
        return -3.0
    us, them = seat, 1 - seat
    won = r["outcome"] == ("teamA" if seat == 0 else "teamB")
    return (1.0 if won else -1.0) + (r["gems"][us] - r["gems"][them]) / 500.0


def schedule(gen, hero, k, opps, maps):
    rng = random.Random(f"real:{gen}:{hero}")
    others = [q for q in range(len(PICKS)) if q != hero]
    out = []
    for j in range(k):
        comp = [hero] + rng.sample(others, 4)
        out.append((rng.choice(maps), rng.choice(opps), j % 2, comp))
    return out


class SharedCMA:
    """CMA-ES over the director params, told once per generation from the real-map games."""

    def __init__(self, d: Path, space_path: str, popsize: int, sigma: float, cap: float):
        import cma
        self.d, self.space = d, json.loads(Path(space_path).read_text(encoding="utf-8"))
        self.path = d / "cma.pkl"
        lp = d / "team_src" / "params_runtime.h"
        loader = lp.read_text(encoding="utf-8") if lp.exists() else ""
        if "PRMS" not in loader:
            sys.exit("--cma needs a team built with tools/team_bot.py --runtime-params (team_src has no loader)")
        order = re.search(r"in this order: ([^)]*)\)", loader).group(1).split(", ")
        if order != list(self.space):
            sys.exit(f"--cma space order {list(self.space)} != the team's runtime params {order}")
        if self.path.exists():
            self.es, self.start = pickle.loads(self.path.read_bytes())
        else:
            text = (d / "team_src" / "params.h").read_text(encoding="utf-8")
            self.start = {}
            for k in self.space:
                v = re.search(rf"^(?:constexpr|inline) \w+ {k} = ([^;]+);", text, re.M).group(1).rstrip("f")
                self.start[k] = float(v)
            x0 = tune.encode(self.start, self.space)
            lo = [max(0.0, x - cap) for x in x0]
            hi = [min(1.0, x + cap) for x in x0]
            self.es = cma.CMAEvolutionStrategy(x0, sigma, {"bounds": [lo, hi], "popsize": popsize,
                                                           "verbose": -9, "seed": 1})
        self.xs, self.vals = [], []

    def ask(self):
        self.xs = self.es.ask()
        self.vals = [tune.decode(x, self.space) for x in self.xs]
        return self.vals

    def tell(self, fits, gen):
        self.es.tell(self.xs, [-f for f in fits])           # cma minimises
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps((self.es, self.start)))
        tmp.replace(self.path)
        mean = tune.decode(self.es.mean, self.space)
        out = {"gen": gen, "mean": mean, "sigma": round(self.es.sigma, 4), "start": self.start,
               "best": self.vals[max(range(len(fits)), key=lambda i: fits[i])]}
        (self.d / "params_mean.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        (self.d / "snapshots").mkdir(exist_ok=True)
        (self.d / "snapshots" / f"params_gen{gen:04d}.json").write_text(json.dumps(out), encoding="utf-8")
        return mean


def run(a):
    d = nt.OUT / a.name
    d.mkdir(parents=True, exist_ok=True)
    ck = d / "state.pkl"
    warm = Path(a.warm_arena) if a.warm_arena else arena.ARENA
    if ck.exists():
        st = nt.load_state(ck)
        print(f"resuming real-map run {a.name} at generation {st['gen']}", flush=True)
    else:
        src = nt.load_state(warm / "neat" / a.warm_run / "state.pkl")       # read-only
        st = {"gen": 0, "pops": src["pops"], "elites": src["elites"], "phase": src["phase"], "team_size": 5,
              "warm": f"{warm / 'neat' / a.warm_run} gen {src['gen']}", "real": True}
        for pop in st["pops"].values():
            nt.widen(pop, a.pop)
        frozen = {PICKS[p]: nt.program(g, st["pops"][p].config) for p, g in st["elites"].items()}
        (d / "frozen_start.json").write_text(json.dumps({"from": st["warm"], "nf": nt.NF, "programs": frozen}),
                                             encoding="utf-8")
        if (d / "team_src").exists():
            shutil.rmtree(d / "team_src")
        shutil.copytree(arena.ROOT / "bots" / a.team / "src", d / "team_src")
        nt.save_state(ck, st)
        print(f"real-map run {a.name}: warm-started {len(st['pops'])} populations from {st['warm']}; "
              f"frozen start + team source saved", flush=True)
    shared = SharedCMA(d, a.cma, a.cma_pop, a.cma_sigma, a.cma_cap) if a.cma else None
    global DIRECTOR_WORDS
    dnf = d / "director_net.json"                # frozen with the run, like team_src
    if a.director_net and not dnf.exists():
        shutil.copy(a.director_net, dnf)
    if dnf.exists():
        DIRECTOR_WORDS = json.loads(dnf.read_text(encoding="utf-8"))["words"]
        print(f"learned director network in every game ({len(DIRECTOR_WORDS)} words)", flush=True)
    frozen_progs = nvn.load_programs(d / "frozen_start.json")
    slots = build_slots(d, max(2, 2 * a.jobs))
    opps = opponent_bots(a.opps, frozen_progs, slots[0])
    maps = arena.all_maps()
    stop_at = deadline(a.until) if a.until else None
    log = d / "log.jsonl"
    last_secs = 0.0
    done = 0
    while a.gens == 0 or done < a.gens:
        if stop_at and time.time() + 1.1 * last_secs > stop_at:
            print(f"stopping before the {a.until} deadline", flush=True)
            break
        t0 = time.perf_counter()
        gen = st["gen"] + 1
        team = {q: nt.program(st["elites"][q], st["pops"][q].config) for q in st["pops"]}
        games, meta, items_of = [], [], {}
        cell_of = {}                      # (hero, slot) -> director-params candidate (shared CMA-ES)
        if shared:
            vals = shared.ask()
            cells = [(p, j) for p in st["pops"] for j in range(a.games)]
            random.Random(f"cma:{gen}").shuffle(cells)
            cell_of = {c: k % len(vals) for k, c in enumerate(cells)}
        for p, pop in st["pops"].items():
            items_of[p] = list(pop.population.items())
            sched = schedule(gen, p, a.games, list(opps), maps)
            for gi, (gid, g) in enumerate(items_of[p]):
                prog = nt.program(g, pop.config)
                blob = with_dir(nvn.team_blob(nt, team, 0, p, prog))
                for j, (m, oname, seat, comp) in enumerate(sched):
                    b = tb.blob_with_draft(blob, comp)
                    if shared:
                        b = tb.blob_with_params(b, list(vals[cell_of[(p, j)]].values()))
                    cand = dist.virtual_bot(slots[0], b, f"{PICKS[p]}{gi}")
                    ob = opps[oname]
                    games.append((cand, ob, m) if seat == 0 else (ob, cand, m))
                    meta.append((p, gid, oname, seat, cell_of.get((p, j), -1)))
        res = dist.play_slots(games, a.jobs, slots, root=a.dist or None)
        fit, by_opp, errs, by_param = {}, {}, 0, {}
        for (p, gid, oname, seat, c), r in zip(meta, res):
            s = score(r, seat)
            errs += "error" in r
            fit.setdefault((p, gid), []).append(s)
            by_opp.setdefault(oname, []).append(s)
            by_param.setdefault(c, []).append(s)
        cma_info = {}
        if shared:
            pfits = [sum(by_param[c]) / len(by_param[c]) if by_param.get(c) else -3.0 for c in range(len(vals))]
            mean = shared.tell(pfits, gen)
            cma_info = {"cma": {"fits": [round(f, 3) for f in pfits], "sigma": round(shared.es.sigma, 4),
                                "games": [len(by_param.get(c, [])) for c in range(len(vals))], "mean": mean}}
        summary = {}
        for p, pop in st["pops"].items():
            pre = {gid: sum(v) / len(v) for (q, gid), v in fit.items() if q == p}

            def assign(genomes, cfg, pre=pre):
                for gid, g in genomes:
                    g.fitness = pre.get(gid, -3.0) - 0.002 * len(g.connections)
            ids = dict(items_of[p])
            best = max(ids.values(), key=lambda g: pre.get(g.key, -9))
            pop.run(assign, 1)
            st["elites"][p] = best
            ph = nt.update_phase(st["phase"][p], pop)
            summary[PICKS[p]] = [round(pre.get(best.key, 0), 3), round(sum(pre.values()) / max(1, len(pre)), 3),
                                 len(best.nodes), len(best.connections), ph["mode"][0]]
        st["gen"] = gen
        nt.save_state(ck, st)
        nt.export(argparse.Namespace(name=a.name))
        secs = time.perf_counter() - t0
        last_secs = secs
        done += 1
        vs = {o: round(sum(v) / len(v), 3) for o, v in by_opp.items()}
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"gen": gen, "secs": round(secs), "games": len(games), "errors": errs, "vs": vs,
                                 "best": summary, **cma_info,
                                 **({"dist": dist.take_stats(a.dist)} if a.dist else {})}) + "\n")
        print(f"gen {gen:4} real  {secs:6.0f}s  {len(games)} games  errors {errs}  mean score vs " +
              " ".join(f"{o}:{v:+.2f}" for o, v in vs.items()) + "  best " +
              " ".join(f"{k}:{v[0]:+.2f}" for k, v in summary.items()) +
              (f"  cma best {max(cma_info['cma']['fits']):+.2f} median "
               f"{sorted(cma_info['cma']['fits'])[len(vals) // 2]:+.2f} sigma {shared.es.sigma:.3f}" if shared else ""),
              flush=True)


def status(a):
    d = nt.OUT / a.name
    rows = [json.loads(l) for l in (d / "log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        print(f"gen {r['gen']:4} {r['secs']:5}s errors {r['errors']} vs " + " ".join(f"{o}:{v:+.2f}" for o, v in r["vs"].items()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--warm-run", default="n3v3")
    r.add_argument("--director-net", default="", help="learned director network (best.json) for every game; "
                   "copied into the run on its first generation")
    r.add_argument("--team", default="team", help="generated combined bot (bots/<team>) whose source the run "
                   "freezes as team_src on its first generation, e.g. team-director (tools/team_bot.py)")
    r.add_argument("--warm-arena", default="")
    r.add_argument("--pop", type=int, default=16)
    r.add_argument("--games", type=int, default=6, help="real games per candidate per generation")
    r.add_argument("--opps", nargs="+", default=["sparring-tarot", "v1", "forager", "frozen", "prior"])
    r.add_argument("--jobs", type=int, default=12)
    r.add_argument("--gens", type=int, default=0, help="0 = run until --until")
    r.add_argument("--until", default="", help="HH:MM local: don't start a generation that would end after it")
    r.add_argument("--cma", default="", metavar="SPACE.json",
                   help="also CMA-ES the director params on the same games (needs a --runtime-params team)")
    r.add_argument("--cma-pop", type=int, default=8)
    r.add_argument("--cma-sigma", type=float, default=0.08, help="initial step (normalised units)")
    r.add_argument("--cma-cap", type=float, default=0.25, help="max distance from the start (normalised units)")
    dist.add_arg(r)
    s = sub.add_parser("status")
    s.add_argument("name")
    a = ap.parse_args()
    {"run": run, "status": status}[a.cmd](a)


if __name__ == "__main__":
    main()
