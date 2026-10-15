"""Self-play training of the duel bot's per-champion action weights (bots/duel/src/weights.h).

One CMA-ES per draft pick (C&I = cassi+issac rows together). Candidate bot i bundles the i-th sample
of every pick's ES, so one build serves all picks. Each duel's result is credited to the ES of the
pick the candidate played. Opponents: the current mean policy plus a hall of fame of past means.

  .venv/Scripts/python tools/duel_train.py train r1 --gens 30 [--size 1] [--opps 5] [--pop 8]
  .venv/Scripts/python tools/duel_train.py matrix r1 [--size 1]       # learned matchup table
  .venv/Scripts/python tools/duel_train.py matrix prior               # table for the hand prior

Game score (candidate side): kill difference + 0.1 x champion-damage difference / 1000.
"""
import argparse
import hashlib
import json
import pickle
import random
import shutil
import sys
import time
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore", message="Could not import matplotlib")
import arena  # noqa: E402

PICKS = ["ryth", "vesper", "vendetta", "ci", "stellar", "prism", "marcella", "willow", "ren", "varg", "tavi"]
ROWS = {0: [0], 1: [1], 2: [2], 3: [3, 4], 4: [5], 5: [6], 6: [7], 7: [8], 8: [9], 9: [10], 10: [11]}
NF = int((Path(__file__).resolve().parent.parent / "bots" / "duel" / "src" / "weights.h").read_text()
         .split("constexpr int NF = ")[1].split(";")[0])
DUEL_SRC = arena.ROOT / "bots" / "duel" / "src"
OUT = arena.ARENA / "duel"


def prior_matrix():
    text = (DUEL_SRC / "weights.h").read_text()
    start = text.index("PRIOR[NF] = {") + len("PRIOR[NF] = {")
    vals = [float(v.strip().rstrip("f")) for v in text[start:text.index("};", start)].split(",") if v.strip()]
    return np.tile(np.array(vals), (12, 1))


def weights_header(m):
    rows = ",\n".join("    {" + ", ".join(f"{v:.5f}f" for v in r) + "}" for r in m)
    text = (DUEL_SRC / "weights.h").read_text()
    head = text[: text.index("constexpr float WEIGHTS")]
    return head + "constexpr float WEIGHTS[12][NF] = {\n" + rows + ",\n};\n"


def build_matrix(m, name):
    h = weights_header(m)
    key = hashlib.sha256(h.encode()).hexdigest()[:12]
    d = arena.ARENA / "duelbots" / key
    if not (d / ".cambc" / "build" / "bot-injected.wasm").exists():
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(DUEL_SRC, d / "src")
        (d / "src" / "weights.h").write_text(h)
    return arena.build(d, name)


def map_path(size, layout, a, b):
    return arena.ROOT / "maps_duel" / f"{size}v{size}" / f"{layout}__{PICKS[a]}-{PICKS[b]}.map26a"


def side_score(r, seat):
    """Score for the bot in `seat` (0 = A, 1 = B) of an arena result."""
    if "error" in r:
        return -5.0
    us, them = r["teams"][seat], r["teams"][1 - seat]
    return (us["kills"] - them["kills"]) + 0.1 * (us.get("dmg", 0) - them.get("dmg", 0)) / 1000


def train(a):
    import cma
    d = OUT / a.name
    d.mkdir(parents=True, exist_ok=True)
    ck = d / "state.pkl"
    if ck.exists():
        st = pickle.loads(ck.read_bytes())
        print(f"resuming at gen {st['gen']}")
    else:
        prior = prior_matrix()
        if a.init_from:
            # Curriculum warm start: previous run's learned weights; new feature columns start at 0.
            old = current_mean(pickle.loads((OUT / a.init_from / "state.pkl").read_bytes()))
            prior = np.zeros((12, NF))
            prior[:, :old.shape[1]] = old[:, :NF]
            print(f"warm start from {a.init_from} ({old.shape[1]} -> {NF} features)")
        st = {"gen": 0, "hof": [], "es": {}, "init_from": a.init_from}
        for p, rows in ROWS.items():
            x0 = np.concatenate([prior[r] for r in rows])
            st["es"][p] = cma.CMAEvolutionStrategy(x0, a.sigma, {"popsize": a.pop, "verbose": -9, "seed": 7 + p})
    log = d / "log.jsonl"
    for _ in range(a.gens):
        t0 = time.perf_counter()
        gen = st["gen"] + 1
        rng = random.Random(gen)
        asks = {p: es.ask() for p, es in st["es"].items()}
        mean = prior_matrix()
        for p, es in st["es"].items():
            for k, r in enumerate(ROWS[p]):
                mean[r] = es.mean[k * NF:(k + 1) * NF]
        cands = []
        for i in range(a.pop):
            m = mean.copy()
            for p, rows in ROWS.items():
                for k, r in enumerate(rows):
                    m[r] = asks[p][i][k * NF:(k + 1) * NF]
            cands.append(m)
        bots = [None] * a.pop
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(8) as ex:
            futs = {ex.submit(build_matrix, m, f"c{i}"): i for i, m in enumerate(cands)}
            for f in cf.as_completed(futs):
                bots[futs[f]] = f.result()
        opp_pool = [build_matrix(mean, "mean")] + [build_matrix(h, f"hof{j}") for j, h in enumerate(st["hof"][-3:])]
        layout = rng.choice(["open", "pillars"])
        games, meta = [], []
        for p in range(len(PICKS)):
            opp_picks = rng.sample(range(len(PICKS)), a.opps)
            for q in opp_picks:
                opp = rng.choice(opp_pool)
                for i, b in enumerate(bots):
                    games.append((b, opp, map_path(a.size, layout, p, q))); meta.append((i, p, 0))
                    games.append((opp, b, map_path(a.size, layout, q, p))); meta.append((i, p, 1))
        res = arena.play_many(games, a.jobs)
        fit = {p: [[] for _ in range(a.pop)] for p in ROWS}
        for (i, p, seat), r in zip(meta, res):
            fit[p][i].append(side_score(r, seat))
        summary = {}
        for p, es in st["es"].items():
            f = [float(np.mean(v)) for v in fit[p]]
            es.tell(asks[p], [-x for x in f])
            summary[PICKS[p]] = round(float(np.mean(f)), 2)
        st["gen"] = gen
        if gen % 5 == 0:
            st["hof"].append(mean)
        ck.write_bytes(pickle.dumps(st))
        with log.open("a") as fh:
            fh.write(json.dumps({"gen": gen, "layout": layout, "mean_fit": summary}) + "\n")
        print(f"gen {gen:3} {layout:7} {time.perf_counter() - t0:5.0f}s  " +
              " ".join(f"{k}:{v:+.1f}" for k, v in summary.items()), flush=True)
    np.save(d / "mean.npy", current_mean(st))


def current_mean(st):
    """Learned weights of a run as a 12 x nf matrix (nf = that run's feature count)."""
    nf = len(next(iter(st["es"].values())).mean) // len(ROWS[next(iter(st["es"]))])
    mean = np.zeros((12, nf))
    for p, es in st["es"].items():
        for k, r in enumerate(ROWS[p]):
            mean[r] = es.mean[k * nf:(k + 1) * nf]
    return mean


def matrix(a):
    if a.name == "prior":
        m = prior_matrix()
    else:
        st = pickle.loads((OUT / a.name / "state.pkl").read_bytes())
        m = current_mean(st)
    bot = build_matrix(m, a.name)
    vs = build_matrix(prior_matrix(), "prior") if a.vs_prior else bot
    n = len(PICKS)
    games, meta = [], []
    for lay in ("open", "pillars"):
        for p in range(n):
            for q in range(n):
                games.append((bot, vs, map_path(a.size, lay, p, q))); meta.append((p, q, 0))
    res = arena.play_many(games, a.jobs, progress=True)
    tab = np.zeros((n, n)); cnt = np.zeros((n, n))
    for (p, q, seat), r in zip(meta, res):
        s = side_score(r, 0)
        tab[p, q] += s; cnt[p, q] += 1
        if not a.vs_prior:
            tab[q, p] -= s; cnt[q, p] += 1
    tab /= np.maximum(cnt, 1)
    print("\nrow vs column: mean kill difference per 2000-round duel (+ = row wins)")
    print(" " * 9 + "".join(f"{x[:6]:>7}" for x in PICKS) + "   mean")
    for p in range(n):
        print(f"{PICKS[p]:9}" + "".join(f"{tab[p, q]:7.1f}" for q in range(n)) + f"{tab[p].mean():7.1f}")
    out = OUT / f"matrix_{a.name}{'_vsprior' if a.vs_prior else ''}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"picks": PICKS, "table": tab.tolist()}))
    print(f"-> {out}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train"); t.add_argument("name"); t.add_argument("--gens", type=int, default=10)
    t.add_argument("--size", type=int, default=1); t.add_argument("--opps", type=int, default=5)
    t.add_argument("--pop", type=int, default=8); t.add_argument("--sigma", type=float, default=0.5)
    t.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    t.add_argument("--init-from", default="", help="warm-start from this run's learned weights (curriculum)")
    m = sub.add_parser("matrix"); m.add_argument("name"); m.add_argument("--size", type=int, default=1)
    m.add_argument("--vs-prior", action="store_true"); m.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    a = ap.parse_args()
    {"train": train, "matrix": matrix}[a.cmd](a)


if __name__ == "__main__":
    main()
