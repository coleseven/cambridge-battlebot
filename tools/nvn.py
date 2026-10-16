"""Shared machinery for team-size-generic (N vs N) fight training of the bots/neat networks.

Both trainers (tools/neat_train.py, tools/cgp_train.py) use this module for everything except genome
code: map lookup, program migration, the champion sets, the per-generation matchup schedule, game
evaluation and fitness credit. Team size N is a single parameter; N = 1 is the original duel setup.

Maps: maps_duel/<N>v<N>/<layout>__<a1+a2..>-<b1+b2..>.map26a (tools/duel_maps.py). Teams are sorted
tuples of draft picks with no duplicate hero; mirrors across teams are allowed. A missing map is written
on demand, so 3v3+ (27k pairings per layout) never needs a full map set.

Programs: the bot reads genome blobs of format 2 (NF = 31 features: the 25 duel features plus 6 team
features). Programs written for the 25-feature bot (every 1v1 elites.json / champions file before this
change, marked by a missing or 25 "nf" field) are migrated with migrate(): their hidden/output slots move
up by 6 and the new inputs stay unconnected, so they play exactly as before.

Matchups (one generation, one hero h, common to every candidate of h):
  * k2 team pairings: the candidate's team is h plus N-1 allies drawn at random from the other heroes
    (allies play the champion set's network), against a random team of N heroes. Each pairing is played
    in both seats. The first half of the pairings face the evolving N-v-N champion set, the second half
    the fixed hand-prior anchor.
  * k1 1v1 duels (retention), both seats, same half/half opponent split, on the 1v1 maps.
  Layouts are drawn per pairing. The schedule depends only on (generation, hero, seed), so it is the same
  for every candidate and is reproduced on resume.

Fitness credit: each game's team score (duel_train.side_score: kill difference + gem difference/50 + a
small damage term) goes to the candidate genome. Only one member of a candidate's team is a learner
(the allies are fixed champion networks shared by every candidate), so crediting the whole team score
does not reward a passenger relative to its rivals: every candidate of h has the same allies on the same
maps, and the only thing that differs is h's play. Fitness = mean over all games (2v2 and 1v1 together),
and f2 / f1 are logged separately.

  .venv/Scripts/python tools/nvn.py equiv --old-src <dir with the 25-feature bots/neat/src> [--maps 24]
  .venv/Scripts/python tools/nvn.py fuel --team-size 2 [--games 4]
  .venv/Scripts/python tools/nvn.py play --team-size 3        # one N-v-N game, champion blob vs prior
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import dist  # noqa: E402
import duel_maps  # noqa: E402
import duel_train  # noqa: E402
import mapformat as mf  # noqa: E402

PICKS = duel_train.PICKS
ROWS = duel_train.ROWS
_W = (arena.ROOT / "bots" / "neat" / "src" / "weights.h").read_text(encoding="utf-8")
NF = int(re.search(r"constexpr int NF = (\d+);", _W).group(1))
NF_V1 = int(re.search(r"constexpr int NF_V1 = (\d+);", _W).group(1))
LAYOUTS = ("open", "pillars")


# ---------------------------------------------------------------- maps
def map_path(n: int, layout: str, ta, tb) -> Path:
    """Arena for team A = ta vs team B = tb (iterables of pick indices); written if missing."""
    ta, tb = tuple(ta), tuple(tb)
    if n > 1:
        ta, tb = tuple(sorted(ta)), tuple(sorted(tb))
    p = arena.ROOT / "maps_duel" / f"{n}v{n}" / duel_maps.map_name(layout, ta, tb)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{random.getrandbits(32):08x}.tmp")
        mf.write(duel_maps.make_map(layout, ta, tb), tmp)
        tmp.replace(p)
    return p


def teams(n: int):
    return list(itertools.combinations(range(len(PICKS)), n))


# ---------------------------------------------------------------- programs
def migrate(words, from_nf: int = NF_V1):
    """Program words for a from_nf-feature bot -> the current NF-feature bot (shift hidden slots)."""
    if words is None or from_nf == NF:
        return words
    shift = NF - from_nf
    w = list(words)
    out, i = [w[0]], 1
    sl = lambda s: s + shift if s >= from_nf else s   # noqa: E731
    for _ in range(w[0]):
        node, act, bias, resp, k = w[i:i + 5]
        out += [sl(node), act, bias, resp, k]
        i += 5
        for _ in range(k):
            out += [sl(w[i]), w[i + 1]]
            i += 2
    out.append(sl(w[i]))
    return out


def force_team(words):
    """Same scores, but with an extra zero-weight node reading all team features, so the bot takes the
    team path (computes the team features). Used to measure the worst-case fuel of a 2v2 network."""
    w = list(words)
    used, i = [w[-1]], 1
    for _ in range(w[0]):
        used.append(w[i]); k = w[i + 4]; i += 5 + 2 * k
    slot = max(max(used) + 1, NF)
    zero = struct.unpack("<I", struct.pack("<f", 0.0))[0]
    one = struct.unpack("<I", struct.pack("<f", 1.0))[0]
    node = [slot, 0, zero, one, NF - NF_V1] + [x for s in range(NF_V1, NF) for x in (s, zero)]
    return [w[0] + 1] + node + w[1:]


def load_programs(path: Path):
    """{pick: program words for this bot} from an elites/champions json (old 25-feature files migrate)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    nf = d.get("nf", NF_V1)
    return {PICKS.index(h): migrate(w, nf) for h, w in d["programs"].items() if w}


def champions_file(n: int) -> Path:
    return arena.ARENA / "champions" / ("current.json" if n == 1 else f"current_{n}v{n}.json")


def champion_set(n: int, warm_arena: Path | None = None):
    """Evolving N-v-N champion set: champions/current_<N>v<N>.json, else the 1v1 set (here, then in
    warm_arena). Returns {pick: words} or {} (hand prior)."""
    for p in [champions_file(n), champions_file(1)] + ([Path(warm_arena) / "champions" / "current.json"]
                                                       if warm_arena else []):
        if p.exists():
            return load_programs(p)
    return {}


def team_blob(nt, progs: dict, resign: int, hero=None, prog=None):
    """Genome blob holding `progs` (pick -> words) for every hero, with `prog` for `hero` if given."""
    rows = {r: w for q, w in progs.items() if w for r in ROWS[q]}
    if hero is not None and prog is not None:
        for r in ROWS[hero]:
            rows[r] = prog
    return nt.blob(rows, resign)


# ---------------------------------------------------------------- schedule and evaluation
def schedule(n: int, gen: int, hero: int, k2: int, k1: int, seed: int = 0):
    """Matchups for hero `hero` in generation `gen`: list of (size, layout, own_team, opp_team, opp_kind)
    with opp_kind 'elite' or 'anchor'. Each is played in both seats."""
    rng = random.Random(f"{seed}:{gen}:{hero}:{n}")
    out = []
    others = [q for q in range(len(PICKS)) if q != hero]
    if n > 1:
        for j in range(k2):
            own = tuple(sorted([hero] + rng.sample(others, n - 1)))
            opp = tuple(sorted(rng.sample(range(len(PICKS)), n)))
            out.append((n, rng.choice(LAYOUTS), own, opp, "elite" if j < (k2 + 1) // 2 else "anchor"))
    k1 = k1 if n > 1 else k2       # a 1v1 run is all duels
    if k1:
        layout = rng.choice(LAYOUTS)
        for j, q in enumerate(rng.sample(range(len(PICKS)), k1)):
            out.append((1, layout, (hero,), (q,), "elite" if j < (k1 + 1) // 2 else "anchor"))
    return out


def evaluate(nt, progs, hero, sched, team_progs, elite_bot, anchor, cand_slots, a):
    """Score each candidate program (as hero `hero`, allies from team_progs) on every matchup in
    `sched`, both seats. Returns a list of (fitness, f_nvn, f_1v1); fitness = mean over all games."""
    out = [(-50.0, None, None)] * len(progs)
    for k in range(0, len(progs), len(cand_slots)):
        idx = [i for i in range(k, min(k + len(cand_slots), len(progs))) if progs[i] is not None]
        games, meta = [], []
        for i, slot in zip(idx, cand_slots):
            bot = nt.patch(slot, team_blob(nt, team_progs, a.resign, hero, progs[i]), f"c{i}")
            for size, layout, own, opp, kind in sched:
                ob = elite_bot if kind == "elite" else anchor
                games.append((bot, ob, map_path(size, layout, own, opp))); meta.append((i, size, 0))
                games.append((ob, bot, map_path(size, layout, opp, own))); meta.append((i, size, 1))
        res = dist.play_many(games, a.jobs, root=getattr(a, "dist", None))
        acc = {}
        for (i, size, seat), r in zip(meta, res):
            if "error" not in r and a.resign and r["round"] > a.resign + 2:
                r = {"error": "slot rebuilt (genome lost)"}
            acc.setdefault(i, {}).setdefault(size, []).append(duel_train.side_score(r, seat))
        for i, by in acc.items():
            allg = [s for v in by.values() for s in v]
            m = lambda v: sum(v) / len(v) if v else None   # noqa: E731
            f1 = m(by.get(1, []))
            fn = m([s for sz, v in by.items() if sz != 1 for s in v])
            out[i] = (sum(allg) / len(allg), fn, f1)
    return out


def add_args(r):
    """Team-size options shared by both trainers' `run` command."""
    r.add_argument("--team-size", type=int, default=1, help="N for N-v-N training (1 = the duel trainer)")
    r.add_argument("--pairings", type=int, default=5, help="N-v-N team pairings per hero per generation")
    r.add_argument("--duels", type=int, default=2, help="1v1 retention duels per hero per generation (N>1)")
    r.add_argument("--warm-run", default="", help="N>1: warm-start from this 1v1 run (same trainer kind)")
    r.add_argument("--warm-arena", default="", help="N>1: .arena holding --warm-run and the 1v1 champion "
                   "set (read-only; default this checkout's .arena)")
    dist.add_arg(r)


# ---------------------------------------------------------------- tests
def _cmd_equiv(a):
    """1v1 bit-identity: old 25-feature bot + old blob vs new bot + old (format 1) and migrated blobs."""
    import shutil
    import neat_train as nt
    warm = Path(a.warm_arena) if a.warm_arena else arena.ARENA
    d = json.loads((warm / "champions" / "current.json").read_text(encoding="utf-8"))
    old_progs = {PICKS.index(h): w for h, w in d["programs"].items() if w}
    old_rows = {r: w for q, w in old_progs.items() for r in ROWS[q]}

    def blob_v(rows, version):
        words = [0x4E454154, 0x424C4F42, nt.WORDS, version, a.resign] + [0] * 12
        for t, prog in rows.items():
            words[5 + t] = len(words)
            words += prog
        import struct
        return struct.pack(f"<{len(words)}I", *words)

    def slot(kind, i, src):
        dd = arena.ARENA / kind / f"s{i:02d}"
        if not (dd / "src").exists():
            shutil.copytree(src, dd / "src")
        return arena.build(dd, f"{kind}{i}").path
    old = [slot("equivold", i, Path(a.old_src)) for i in range(2)]
    new = [slot("equivnew", i, arena.ROOT / "bots" / "neat" / "src") for i in range(4)]
    o_champ = nt.patch(old[0], blob_v(old_rows, 1), "old-champ")
    o_prior = nt.patch(old[1], blob_v({}, 1), "old-prior")
    n_champ1 = nt.patch(new[0], blob_v(old_rows, 1), "new-champ-v1")
    n_champ2 = nt.patch(new[1], team_blob(nt, {q: migrate(w) for q, w in old_progs.items()}, a.resign), "new-champ-v2")
    n_prior = nt.patch(new[2], nt.blob({}, a.resign), "new-prior")
    rng = random.Random(5)
    maps = [map_path(1, rng.choice(LAYOUTS), (rng.randrange(11),), (rng.randrange(11),)) for _ in range(a.maps)]
    pairs = {"old": (o_champ, o_prior), "new-v1": (n_champ1, n_prior), "new-v2": (n_champ2, n_prior)}
    games = []
    for k, (x, y) in pairs.items():
        for m in maps:
            games += [(x, y, m), (y, x, m)]
    res = arena.play_many(games, a.jobs)
    strip = lambda r: json.dumps({k: v for k, v in r.items() if k not in ("key", "a", "b", "cached")}, sort_keys=True)  # noqa: E731
    n = 2 * len(maps)
    base = [strip(r) for r in res[:n]]
    ok = True
    for j, k in enumerate(pairs):
        if j == 0:
            continue
        got = [strip(r) for r in res[j * n:(j + 1) * n]]
        same = sum(x == y for x, y in zip(base, got))
        print(f"{k:7}: {same}/{n} games identical to the old bot")
        ok &= same == n
    errs = sum("error" in r for r in res)
    kills = sum(r["teams"][0]["kills"] + r["teams"][1]["kills"] for r in res[:n] if "error" not in r)
    print(f"maps {len(maps)}, games per variant {n}, errors {errs}, total kills (old) {kills}")
    print("EQUIVALENT" if ok and not errs else "MISMATCH")
    return 0 if ok and not errs else 1


def _fuel_bot(nt):
    import shutil
    dd = arena.ARENA / "fuelprobe" / "s00"
    if dd.exists():
        shutil.rmtree(dd)
    shutil.copytree(arena.ROOT / "bots" / "neat" / "src", dd / "src")
    b = dd / "src" / "bot.cpp"
    b.write_text("#define BOT_FUEL 1\n" + b.read_text(encoding="utf-8"), encoding="utf-8")
    return arena.build(dd, "fuel").path


def _bot_stdout(x, y, m, rounds=0):
    """Both teams' bot stdout of one game (from the replay bundle, a zip with team-a/b.stdout)."""
    import shutil
    import subprocess
    import tempfile
    import zipfile
    (arena.ARENA / "tmp").mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="log-", dir=arena.ARENA / "tmp"))
    try:
        cmd = [str(arena.CAMBC), "run", str(x.path), str(y.path), "--map", str(m), "-o", str(out)]
        subprocess.run(cmd + (["--rounds", str(rounds)] if rounds else []), cwd=arena.ROOT, capture_output=True)
        text = ""
        for f in out.glob("*.repl26a"):
            with zipfile.ZipFile(f) as z:
                for n in z.namelist():
                    if n.endswith(".stdout"):
                        text += z.read(n).decode("utf-8", "replace") + "\n"
        return text
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _cmd_fuel(a):
    """Worst per-turn fuel of a network body in N-v-N games (champion blob vs champion blob)."""
    import neat_train as nt
    champs = {q: w if a.solo else force_team(w) for q, w in champion_set(a.team_size, a.warm_arena or None).items()}
    bot = nt.patch(_fuel_bot(nt), team_blob(nt, champs, 0), "fuel")
    rng = random.Random(7)
    worst = {}
    for g in range(a.games):
        ta = tuple(sorted(rng.sample(range(11), a.team_size)))
        tb = tuple(sorted(rng.sample(range(11), a.team_size)))
        m = map_path(a.team_size, LAYOUTS[g % 2], ta, tb)
        for line in _bot_stdout(bot, bot, m).splitlines():
            mm = re.search(r"FUEL (\d+) (\d+)", line)
            if mm:
                t, u = int(mm.group(1)), int(mm.group(2))
                worst[t] = max(worst.get(t, 0), u)
        print(f"game {g + 1}: {m.name}  worst so far {max(worst.values()) if worst else 'n/a'}", flush=True)
    print("worst fuel per turn by champion_type:", dict(sorted(worst.items())))
    print(f"overall worst: {max(worst.values()) if worst else 'n/a'} (limit 100000; NN share capped at 35000)")


def _cmd_play(a):
    """One N-v-N game: champion-set network team vs the hand prior."""
    import neat_train as nt
    slots = nt.build_slots(2, "nvnplay")
    champs = champion_set(a.team_size, a.warm_arena or None)
    x = nt.patch(slots[0], team_blob(nt, champs, a.resign), "champ")
    y = nt.patch(slots[1], nt.blob({}, a.resign), "prior")
    rng = random.Random(a.seed)
    ta = tuple(sorted(rng.sample(range(11), a.team_size)))
    tb = tuple(sorted(rng.sample(range(11), a.team_size)))
    m = map_path(a.team_size, "open", ta, tb)
    r = arena.play(x, y, m)
    print(m.name)
    print(json.dumps({k: v for k, v in r.items() if k != "key"}, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("equiv"); e.add_argument("--old-src", required=True); e.add_argument("--maps", type=int, default=24)
    f = sub.add_parser("fuel"); f.add_argument("--games", type=int, default=4)
    f.add_argument("--solo", action="store_true", help="measure the duel path (no team features) instead")
    p = sub.add_parser("play"); p.add_argument("--seed", type=int, default=0)
    for s in (e, f, p):
        s.add_argument("--team-size", type=int, default=2); s.add_argument("--jobs", type=int, default=2)
        s.add_argument("--resign", type=int, default=800); s.add_argument("--warm-arena", default="")
    a = ap.parse_args()
    sys.exit({"equiv": _cmd_equiv, "fuel": _cmd_fuel, "play": _cmd_play}[a.cmd](a) or 0)


if __name__ == "__main__":
    main()
