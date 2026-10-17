"""Equivalence check for whole-generation batching (17 Oct 2026): one N-v-N generation of NEAT and CGP from
the same starting state, once hero by hero (--per-hero, the old path) and once batched (the new default).
Each run starts from an empty result cache so every game is really played. Pass = every candidate's fitness
identical in all three variants (batch, batch + coordinator, per-hero) AND the trainers' real path
(batch + coordinator) reproducing the per-hero log and next-generation state exactly.

Result 17 Oct 2026 (2v2, this PC, 12 jobs): CGP fully identical in all three variants; NEAT 264/264
candidate fitnesses identical, batch + coordinator next-generation state identical to per-hero.

  .venv/Scripts/python tools/batch_equiv.py --from-arena <live .arena> --neat n2v2 --cgp c2v2 [--jobs 12]
Copies the runs' state.pkl and the N-v-N champion set into THIS checkout's .arena (eq_* runs), so the live
runs are only read. Exit 0 = identical.
"""
import argparse
import json
import pickle
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402

PY = sys.executable


def fresh_cache():
    for p in arena.ARENA.glob("results*.jsonl"):
        p.unlink()


def run(tool, name, extra, a):
    # Seed the global `random` (neat-python's reproduction uses it and the trainers never seed it, so two
    # processes differ there whatever the evaluation path) and fix string hashing, so states are comparable.
    boot = ("import os, random, runpy, sys; random.seed(12345); sys.argv = sys.argv[1:]; "
            "runpy.run_path(sys.argv[0], run_name='__main__')")
    cmd = [PY, "-u", "-c", boot, f"tools/{tool}", "run", name, "--team-size", str(a.team_size), "--gens", "1",
           "--jobs", str(a.jobs), "--slots", str(a.slots), "--pairings", "5", "--duels", "2", *extra]
    t0 = time.time()
    import os
    dump = arena.ARENA / f"evaldump_{name}.jsonl"
    dump.unlink(missing_ok=True)
    r = subprocess.run(cmd, cwd=arena.ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONHASHSEED": "0", "BC_EVAL_DUMP": str(dump)})
    for line in r.stdout.splitlines():
        if "errored" in line:
            print(f"    {name}: {line.strip()[:300]}", flush=True)
    if r.returncode:
        sys.exit(f"{' '.join(cmd)} failed:\n{r.stdout[-2000:]}\n{r.stderr[-3000:]}")
    return time.time() - t0, [l for l in r.stdout.splitlines() if l.startswith("gen")]


def last_log(d):
    rec = json.loads((d / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    return {k: v for k, v in rec.items() if k not in ("secs", "dist")}


def neat_sig(path):
    import neat_train as nt
    st = nt.load_state(path)
    out = {"gen": st["gen"], "phase": st["phase"]}
    for p, pop in st["pops"].items():
        out[p] = sorted((k, sorted((ck, round(c.weight, 12), c.enabled) for ck, c in g.connections.items()),
                         sorted((nk, n.activation, round(n.bias, 12)) for nk, n in g.nodes.items()))
                        for k, g in pop.population.items())
        e = st["elites"][p]
        out[f"elite{p}"] = (e.key, e.fitness)
    return out


def cgp_sig(path):
    st = pickle.loads(Path(path).read_bytes())
    return {"gen": st["gen"], "rng": st["rng"],
            "islands": {p: [(repr(i["g"]), i["fit"]) for i in isl] for p, isl in st["islands"].items()},
            "elite": {p: repr(g) for p, g in st["elite"].items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-arena", required=True)
    ap.add_argument("--neat", default="n2v2")
    ap.add_argument("--cgp", default="c2v2")
    ap.add_argument("--team-size", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--slots", type=int, default=24)
    ap.add_argument("--lam", type=int, default=4)
    ap.add_argument("--only", choices=["neat", "cgp"], help="check one trainer kind")
    a = ap.parse_args()
    src = Path(a.from_arena)
    n = a.team_size
    champ = f"current_{n}v{n}.json"
    (arena.ARENA / "champions").mkdir(parents=True, exist_ok=True)
    if (src / "champions" / champ).exists():
        shutil.copy2(src / "champions" / champ, arena.ARENA / "champions" / champ)
    ok = True
    for kind, tool, live, extra, sig in (("neat", "neat_train.py", a.neat, [], neat_sig),
                                         ("cgp", "cgp_train.py", a.cgp, ["--lam", str(a.lam)], cgp_sig)):
        if a.only and kind != a.only:
            continue
        state = (src / kind / live / "state.pkl").read_bytes()
        res = {}
        droot = arena.ARENA / "eqdist"
        shutil.rmtree(droot, ignore_errors=True)
        for variant, flags in (("batch", []), ("batchdist", ["--dist", str(droot)]), ("perhero", ["--per-hero"])):
            name = f"eq_{live}_{variant}"
            d = arena.ARENA / kind / name
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
            (d / "state.pkl").write_bytes(state)
            fresh_cache()
            secs, lines = run(tool, name, extra + flags, a)
            res[variant] = (secs, last_log(d), sig(d / "state.pkl"))
            print(f"{kind} {variant:8} {secs:6.0f}s  {lines[-1][:150] if lines else '(no gen line)'}", flush=True)
        fits = {}
        for v in ("batch", "batchdist", "perhero"):
            fp = arena.ARENA / f"evaldump_eq_{live}_{v}.jsonl"
            fits[v] = sorted(fp.read_text(encoding="utf-8").splitlines()) if fp.exists() else []
        same_fit = fits["batch"] == fits["batchdist"] == fits["perhero"] and fits["batch"]
        print(f"{kind}: every candidate's fitness {'IDENTICAL' if same_fit else 'DIFFERS'} "
              f"({len(fits['batch'])} candidates)", flush=True)
        if not same_fit:
            for v in ("batch", "batchdist"):
                d = [(x, y) for x, y in zip(fits[v], fits["perhero"]) if x != y][:5]
                print(f"  {v} vs perhero, first differences: {d}")
        ok &= bool(same_fit)
        # The trainers' real path (batch + coordinator) must also reproduce the whole next generation. The
        # plain local batch variant is only held to identical fitness: neat-python's reproduction is not
        # reproducible across processes even with the same fitness (it was never seeded in real runs).
        same_log = res["batchdist"][1] == res["perhero"][1]
        same_state = res["batchdist"][2] == res["perhero"][2]
        ok &= same_log and same_state
        print(f"{kind}: batch+dist vs per-hero: log {'IDENTICAL' if same_log else 'DIFFERS'}, "
              f"next-generation state {'IDENTICAL' if same_state else 'DIFFERS'}, "
              f"time batch {res['batch'][0]:.0f}s, batch+dist {res['batchdist'][0]:.0f}s, "
              f"per-hero {res['perhero'][0]:.0f}s", flush=True)
        if not same_log:
            print("  batch+dist:", json.dumps(res["batchdist"][1])[:1500])
            print("  perhero   :", json.dumps(res["perhero"][1])[:1500])
    print("EQUIVALENT" if ok else "NOT EQUIVALENT")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
