"""Real-map fighter training: NEAT on the per-hero networks INSIDE the combined bot (tools/team_bot.py), on the
published 5v5 map pool, scored by the real game result.

The strategy layer (forager) plays everything except close fights (FIGHTER_ENGAGE_RANGE); the network decides
the fights. Each candidate network for hero p plays K real games as part of a team drafted around p (the
composition travels in the genome blob's draft block, tools/team_bot.py), against an opponent pool, both seats.
All candidates of a hero play the same games (common random numbers).

  .venv/Scripts/python tools/real_train.py run r1 --warm-run n3v3 [--pop 16] [--games 6] [--jobs 12]
        [--opps sparring-tarot v1 forager frozen] [--until 11:50] [--gens 0 (= until the deadline)]
  .venv/Scripts/python tools/real_train.py status r1

First run: copies the warm run's populations (read-only), freezes its elites as frozen_start.json (an opponent
and the revert point), and snapshots bots/team/src as the run's fixed base (team_src/), so regenerating bots/team
later doesn't change a running experiment. Checkpoints and exports elites (+ snapshots/) every generation.
Score per game (our side): +1 win / -1 loss, plus gem margin / 500; errored games -3.
"""
import argparse
import json
import random
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


def opponent_bots(names, frozen_progs, ref_slot):
    out = {}
    for name in names:
        if name == "frozen":                     # our full bot with the networks this run started from
            out[name] = dist.virtual_bot(ref_slot, nvn.team_blob(nt, frozen_progs, 0), "frozen")
        elif name == "forager":                  # strategy layer alone, as of the run's start (fixed copy)
            src = arena.ARENA / "realopp" / "forager"
            if not (src / "src").exists():
                shutil.copytree(arena.ROOT / "bots" / "forager" / "src", src / "src")
            out[name] = arena.build(src, "forager")
        else:
            out[name] = arena.build(arena.ROOT / "bots" / name, name)
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
        shutil.copytree(arena.ROOT / "bots" / "team" / "src", d / "team_src")
        nt.save_state(ck, st)
        print(f"real-map run {a.name}: warm-started {len(st['pops'])} populations from {st['warm']}; "
              f"frozen start + team source saved", flush=True)
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
        for p, pop in st["pops"].items():
            items_of[p] = list(pop.population.items())
            sched = schedule(gen, p, a.games, list(opps), maps)
            for gi, (gid, g) in enumerate(items_of[p]):
                prog = nt.program(g, pop.config)
                blob = nvn.team_blob(nt, team, 0, p, prog)
                for m, oname, seat, comp in sched:
                    cand = dist.virtual_bot(slots[0], tb.blob_with_draft(blob, comp), f"{PICKS[p]}{gi}")
                    ob = opps[oname]
                    games.append((cand, ob, m) if seat == 0 else (ob, cand, m))
                    meta.append((p, gid, oname, seat))
        res = dist.play_slots(games, a.jobs, slots, root=a.dist or None)
        fit, by_opp, errs = {}, {}, 0
        for (p, gid, oname, seat), r in zip(meta, res):
            s = score(r, seat)
            errs += "error" in r
            fit.setdefault((p, gid), []).append(s)
            by_opp.setdefault(oname, []).append(s)
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
                                 "best": summary, **({"dist": dist.take_stats(a.dist)} if a.dist else {})}) + "\n")
        print(f"gen {gen:4} real  {secs:6.0f}s  {len(games)} games  errors {errs}  mean score vs " +
              " ".join(f"{o}:{v:+.2f}" for o, v in vs.items()) + "  best " +
              " ".join(f"{k}:{v[0]:+.2f}" for k, v in summary.items()), flush=True)


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
    r.add_argument("--warm-arena", default="")
    r.add_argument("--pop", type=int, default=16)
    r.add_argument("--games", type=int, default=6, help="real games per candidate per generation")
    r.add_argument("--opps", nargs="+", default=["sparring-tarot", "v1", "forager", "frozen"])
    r.add_argument("--jobs", type=int, default=12)
    r.add_argument("--gens", type=int, default=0, help="0 = run until --until")
    r.add_argument("--until", default="", help="HH:MM local: don't start a generation that would end after it")
    dist.add_arg(r)
    s = sub.add_parser("status")
    s.add_argument("name")
    a = ap.parse_args()
    {"run": run, "status": status}[a.cmd](a)


if __name__ == "__main__":
    main()
