"""Fight-outcome dataset for the win-probability model (tools/fight_model.py).

Every champion-vs-champion engagement found by tools/replay/events.py becomes one example per
(our body, nearest enemy body) pair, with features taken from the frame just before the first hit
and only from what a bot can observe at that moment (see fight_model.FEATURES, mirrored in
bots/forager's fight selector: Chebyshev radius 4 for local counts). Label = the engagement's
winner from our body's side (1 win, 0 loss, 0.5 draw).

Sources:
  duels   champion-set round robin on every duel map (both layouts, all 11x11 matchups)
  pool    full games on the published map pool, both seats, e.g. main vs starter-cpp / sparring-tarot

  .venv/Scripts/python tools/fight_data.py duels [--jobs 12]
  .venv/Scripts/python tools/fight_data.py pool --bots main v1 --vs starter-cpp sparring-tarot
  .venv/Scripts/python tools/fight_data.py extract          # replays -> .arena/fights/fights.jsonl
Replays go to .arena/fights/replays/<source>/<game>/; extract decodes each once (cached per replay).
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "replay"))
import arena  # noqa: E402

OUT = arena.ARENA / "fights"
REPLAYS = OUT / "replays"
RADIUS = 4            # local ally/enemy count radius (forager: draw_chebyshev(pos, 4))
# Body types in champion_type order (sdk/cpp/cambc.h); Cassi and Issac are separate bodies.
TYPES = ["Ryth", "Vesper Vale", "Vendetta", "Cassi", "Issac", "Stellar", "Prism", "Marcella", "Willow",
         "Ren", "Varg", "Tavi"]


def _keep(games, source, jobs):
    """Play (a, b, map, name) games keeping each replay in its own directory (skips finished ones)."""
    todo = []
    for a, b, m, name in games:
        d = REPLAYS / source / name
        if not any(d.glob("*.repl26a")):
            d.mkdir(parents=True, exist_ok=True)
            todo.append((a, b, m, d))
    print(f"{source}: {len(games) - len(todo)} cached, {len(todo)} to play")
    with ThreadPoolExecutor(jobs) as ex:
        res = list(ex.map(lambda g: arena.play(g[0], g[1], g[2], keep_replay=g[3]), todo))
    bad = [r for r in res if "error" in r]
    if bad:
        print(f"{len(bad)} games failed, e.g. {bad[0]['error'][-300:]}")


def cmd_duels(a):
    import league
    import neat_train as nt
    progs = league.programs("champ")
    slot = nt.build_slots(1, "fightslots")[0]
    bot = nt.patch(slot, nt.blob({r: progs[p] for p in progs for r in nt.ROWS[p]}, a.resign), "champ")
    n = len(nt.PICKS)
    games = [(bot, bot, nt.map_path(lay, p, q), f"{lay}__{nt.PICKS[p]}-{nt.PICKS[q]}")
             for lay in ("open", "pillars") for p in range(n) for q in range(n)]
    _keep(games, "duels", a.jobs)


def cmd_pool(a):
    bots = {name: arena.build(arena.ROOT / "bots" / name) for name in a.bots + a.vs}
    games = []
    for m in arena.all_maps():
        for us in a.bots:
            for them in a.vs:
                games.append((bots[us], bots[them], m, f"{m.stem}__{us}-{them}"))
                games.append((bots[them], bots[us], m, f"{m.stem}__{them}-{us}"))
    _keep(games, "pool", a.jobs)


# ---------------------------------------------------------------- extraction
def examples(tl, source, game):
    import events as E
    g = E.Game(tl)
    ev = E.derive(tl)
    out = []
    for e in (x for x in ev if x["type"] == "engagement"):
        f = g.frame_at(e["r"] - 1)
        if f is None:
            continue
        rows = {}
        for c in g.champs:
            v = lambda col: g.val(f, c["id"], col)  # noqa: E731
            if not v("alive") or v("x") < 0:
                continue
            rows[c["id"]] = {"team": c["team"], "type": TYPES.index(c["name"]) if c["name"] in TYPES else -1,
                             "x": v("x"), "y": v("y"), "hp": v("hp"), "max_hp": v("max_hp"),
                             "cd0": v("cd0"), "cd1": v("cd1"), "items": len(v("items") or []), "gems": v("gems")}
        for side in (0, 1):
            for me_id in e["teams"][side]:
                me = rows.get(me_id)
                if me is None or me["type"] < 0:
                    continue
                foes = [(max(abs(r["x"] - me["x"]), abs(r["y"] - me["y"])), cid) for cid, r in rows.items()
                        if r["team"] != me["team"] and cid in e["teams"][1 - side]]
                if not foes:
                    continue
                dist, foe_id = min(foes)
                foe = rows[foe_id]
                if foe["type"] < 0:
                    continue
                near = lambda team: sum(1 for cid, r in rows.items() if cid != me_id and r["team"] == team  # noqa: E731
                                        and max(abs(r["x"] - me["x"]), abs(r["y"] - me["y"])) <= RADIUS)
                win = e["winner"]
                out.append({"src": source, "game": game, "r": e["r"],
                            "bodies": sum(1 for c in g.champs if c["team"] == me["team"]),
                            "me": me["type"], "foe": foe["type"],
                            "my_hp": me["hp"], "my_max": me["max_hp"], "foe_hp": foe["hp"], "foe_max": foe["max_hp"],
                            "dist": dist, "allies": near(me["team"]), "enemies": near(1 - me["team"]),
                            "cd0": me["cd0"], "cd1": me["cd1"], "my_items": me["items"], "foe_items": foe["items"],
                            "my_gems": me["gems"], "foe_gems": foe["gems"],
                            "first_hit": int(e["first_hit"] == side),
                            "y": 0.5 if win is None else float(win == side)})
    return out


def cmd_extract(a):
    import events as E
    files = sorted(REPLAYS.rglob("*.repl26a"))
    cache = OUT / "cache"
    cache.mkdir(parents=True, exist_ok=True)

    def one(p):
        c = cache / (p.parent.parent.name + "__" + p.parent.name + ".v2.json")
        if c.exists():
            return json.loads(c.read_text(encoding="utf-8"))
        try:
            rows = examples(E.load_timeline(p), p.parent.parent.name, p.parent.name)
        except Exception as ex:  # noqa: BLE001
            print(f"skip {p}: {ex}")
            return []
        c.write_text(json.dumps(rows), encoding="utf-8")
        return rows

    with ThreadPoolExecutor(a.jobs) as ex:
        allrows = [r for rows in ex.map(one, files) for r in rows]
    path = OUT / "fights.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in allrows:
            f.write(json.dumps(r) + "\n")
    by = {}
    for r in allrows:
        by[r["src"]] = by.get(r["src"], 0) + 1
    print(f"{len(allrows)} examples from {len(files)} replays {by} -> {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("duels"); d.add_argument("--resign", type=int, default=800)
    p = sub.add_parser("pool"); p.add_argument("--bots", nargs="+", default=["main"])
    p.add_argument("--vs", nargs="+", default=["starter-cpp", "sparring-tarot"])
    sub.add_parser("extract")
    for s in (d, p, sub.choices["extract"]):
        s.add_argument("--jobs", type=int, default=arena.DEFAULT_JOBS)
    a = ap.parse_args()
    {"duels": cmd_duels, "pool": cmd_pool, "extract": cmd_extract}[a.cmd](a)


if __name__ == "__main__":
    main()
