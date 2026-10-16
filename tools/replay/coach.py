"""Replay coach: turn our ladder losses and notable training duels into structured diagnoses with Claude.

Pipeline:
  collect-ladder  download our team's latest platform matches (fetch.py) and keep the losses
  collect-duels   play a champion-set round robin, then record replays of the most lopsided matchups
  submit          summarise every collected replay (report.py summary) and send one Message Batches
                  request per game: cached system prompt (rules digest + our bots + rubric), the game
                  summary as the user turn, JSON-schema structured output
  results         wait for the batch, save per-game diagnoses, and write a markdown report that ranks
                  recurring root causes and recommendations across games

  .venv/Scripts/python tools/replay/coach.py collect-ladder --latest 30
  .venv/Scripts/python tools/replay/coach.py collect-duels --top 8
  .venv/Scripts/python tools/replay/coach.py submit --run r1 [--limit 3] [--model claude-haiku-5-5] [--effort medium]
  .venv/Scripts/python tools/replay/coach.py results --run r1

Credentials come from the environment (ANTHROPIC_API_KEY or an `ant auth login` profile); this script
never reads or stores a key. Batches cost half the normal per-token price.
"""
import argparse
import collections
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

OUR_TEAM_ID = "1158c8e5-3ab7-47da-9a7a-344e175243d0"
OUR_TEAM_NAME = "777"
COACH = ROOT / ".arena" / "coach"
LADDER_DIR = COACH / "ladder"
DUEL_DIR = COACH / "duels"
CATEGORIES = ["fighting", "farming_route", "banking", "draft", "builds", "macro_timing", "map_control",
              "survival", "objectives", "other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "result_summary": {"type": "string", "description": "Two or three sentences: how the game was decided."},
        "turning_points": {"type": "array", "items": {
            "type": "object",
            "properties": {"round": {"type": "integer"}, "what": {"type": "string"}},
            "required": ["round", "what"], "additionalProperties": False}},
        "root_causes": {"type": "array", "items": {
            "type": "object",
            "properties": {"category": {"type": "string", "enum": CATEGORIES},
                           "description": {"type": "string"},
                           "evidence": {"type": "string", "description": "Numbers or events from the summary."},
                           "severity": {"type": "string", "enum": ["minor", "major", "decisive"]}},
            "required": ["category", "description", "evidence", "severity"], "additionalProperties": False}},
        "recommendations": {"type": "array", "items": {
            "type": "object",
            "properties": {"category": {"type": "string", "enum": CATEGORIES},
                           "change": {"type": "string", "description": "A concrete, testable change to our bot."},
                           "expected_effect": {"type": "string"}},
            "required": ["category", "change", "expected_effect"], "additionalProperties": False}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["result_summary", "turning_points", "root_causes", "recommendations", "confidence"],
    "additionalProperties": False,
}

BOTS = """\
## Our bots (what a diagnosis can change)

Ladder bot (team "777", v1): five-pick draft Varg, Cassi & Issac, Stellar, Ren, Tavi (prefix for 3-4 picks).
Bodies form two squads (body index mod 2) that share one camp target, chosen by value / (travel + kill
time + respawn wait + 5 capture rounds + penalties), with a damage model that skips camps the squad's
tank cannot survive. It banks when carrying >= 24 gems or any gems within 7 tiles of an altar, recalls
below ~46% health, recalls to shop at 170 gold with an empty bag, buys a Jungle emblem then Power
shards of its main stat. It fights the weakest visible enemy in range unless outnumbered by more than
one or below ~44% health, and stuns enemies that are tributing or recalling. No wards, altar locks,
denial or teleport crystals. Parameters were CMA-ES tuned against two bots that rarely fight.

Duel fighters (in training, not yet on the ladder): per-champion neural networks (NEAT and CGPANN)
that score each turn's legal options (move, attack, spell, dash, camp hit, bank) from 25 features
(damage, kill, threat, health, distance, stun state, spells ready, objective distance, gems carried).
They train in 1v1 arenas with contested gem clusters, scored on kills, damage and banked gems.
"""

INSTRUCTIONS = """\
You are a game analyst for a team's bot in Cambridge Battlecode 2026a, a turn-based MOBA-like game
between programs. You receive a compact JSON summary of one finished game, decoded from its replay.
Explain why the focus team lost (or for a duel, why the losing champion lost), using only evidence in
the summary: numbers, event lines, time shares, and early-game timings. Rank root causes by how much
they decided the result. Recommendations must be concrete and testable changes to the focus team's
bot (for example "bank at 12+ gems when an enemy champion is within 6 tiles"), not generic advice.
Say when the evidence is thin, and prefer "low" confidence over guessing. Do not invent events that
the summary does not contain.

Summary legend: teams[].gems = {camps, kills, lost, end_carried}; champs[].gems = [camps, kills,
banked, lost]; dmg = [to champions, from champions]; fights = [won, lost, drawn]; dep/rec =
[completed, interrupted]; time% = share of rounds moving, clearing, capturing, channelling, fighting,
dead, idle; early = first-150-round timings; curve = [round, banked A, banked B].
"""


def system_blocks():
    rules = (ROOT / "docs" / "GAME_NOTES.md").read_text(encoding="utf-8")
    text = INSTRUCTIONS + "\n" + BOTS + "\n## Game rules digest\n\n" + rules
    # One stable system block, cached across every request in the batch.
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


# ---------------------------------------------------------------- collection
def run(cmd):
    return subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def collect_ladder(a):
    LADDER_DIR.mkdir(parents=True, exist_ok=True)
    r = run([sys.executable, HERE / "fetch.py", "team", "us", "--latest", a.latest, "--out", LADDER_DIR])
    print(r.stdout[-1500:] + r.stderr[-800:])
    losses = 0
    for meta in LADDER_DIR.glob("*.meta.json"):
        m = json.loads(meta.read_text(encoding="utf-8"))
        us = m["teamIds"].index(OUR_TEAM_ID) if OUR_TEAM_ID in m.get("teamIds", []) else None
        lost = us is not None and m["results"][us] == "loss"
        losses += lost
        tag = meta.with_suffix("").with_suffix(".focus.json")   # <id>.focus.json
        tag.write_text(json.dumps({"kind": "ladder", "focus_team": "A" if us == 0 else "B",
                                   "focus_name": OUR_TEAM_NAME, "lost": lost}), encoding="utf-8")
    print(f"{losses} losses among {len(list(LADDER_DIR.glob('*.meta.json')))} downloaded matches")


def collect_duels(a):
    import arena
    import duel_train
    import league
    import neat_train as nt
    DUEL_DIR.mkdir(parents=True, exist_ok=True)
    progs = league.programs("champ")
    slot = nt.build_slots(1, "coachslots")[0]
    bot = nt.patch(slot, nt.blob({r: progs[p] for p in progs for r in nt.ROWS[p]}, a.resign), "champ")
    n = len(nt.PICKS)
    pairs = [(p, q) for p in range(n) for q in range(n) if p != q]
    res = arena.play_many([(bot, bot, nt.map_path("open", p, q)) for p, q in pairs], arena.DEFAULT_JOBS)
    scored = sorted(((abs(duel_train.side_score(r, 0)), duel_train.side_score(r, 0), p, q)
                     for (p, q), r in zip(pairs, res) if "error" not in r), reverse=True)
    picked, seen = [], set()
    for _, s, p, q in scored:
        if frozenset((p, q)) in seen:
            continue
        seen.add(frozenset((p, q)))
        picked.append((p, q, s))
        if len(picked) >= a.top:
            break
    for p, q, s in picked:
        out = DUEL_DIR / f"{nt.PICKS[p]}-vs-{nt.PICKS[q]}"
        out.mkdir(exist_ok=True)
        arena.play(bot, bot, nt.map_path("open", p, q), keep_replay=out)
        for f in out.glob("*.repl26a"):
            loser = "B" if s > 0 else "A"
            f.with_suffix(".focus.json").write_text(json.dumps(
                {"kind": "duel", "focus_team": loser,
                 "focus_name": f"{nt.PICKS[q] if s > 0 else nt.PICKS[p]} (champion network)",
                 "matchup": f"{nt.PICKS[p]} (A) vs {nt.PICKS[q]} (B)", "lost": True}), encoding="utf-8")
        print(f"{nt.PICKS[p]:9} vs {nt.PICKS[q]:9} score {s:+.1f} -> {out}")


# ---------------------------------------------------------------- batch
def games(limit=None):
    """(custom_id, replay path, focus info) for every collected loss, ladder first."""
    out = []
    for d in (LADDER_DIR, DUEL_DIR):
        for focus in sorted(d.rglob("*.focus.json")):
            info = json.loads(focus.read_text(encoding="utf-8"))
            if not info.get("lost"):
                continue
            stem = focus.name[: -len(".focus.json")]
            replay = next((p for p in (focus.with_name(stem + ".zip"), focus.with_name(stem + ".repl26a")) if p.exists()), None)
            if replay:
                cid = (info["kind"] + "-" + stem)[:64].replace(".", "_")
                out.append((cid, replay, info))
    return out[:limit] if limit else out


def submit(a):
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    import report
    d = COACH / "runs" / a.run
    d.mkdir(parents=True, exist_ok=True)
    system = system_blocks()
    requests, index = [], {}
    for cid, replay, info in games(a.limit):
        s = report.summary(*report.analyse(str(replay)))
        (d / f"{cid}.summary.json").write_text(json.dumps(s, separators=(",", ":")), encoding="utf-8")
        context = (f"Kind: {info['kind']}. Focus team: {info['focus_team']} ({info['focus_name']}), which lost."
                   + (f" Matchup: {info['matchup']}." if info.get("matchup") else "")
                   + (" This is a 1v1 training duel: team A always resigns at the round cap by design, so ignore"
                      " match.winner/end and judge the result by kills, deaths, damage and banked gems."
                      if info["kind"] == "duel" else ""))
        requests.append(Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
            model=a.model, max_tokens=16000, system=system,
            output_config={"effort": a.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": context + "\n\nGame summary:\n" + json.dumps(s, separators=(",", ":"))}])))
        index[cid] = {"replay": str(replay), **info}
    if not requests:
        sys.exit("nothing collected: run collect-ladder / collect-duels first")
    if a.dry_run:
        # ~4 chars/token; system prompt is cached after the first request; batch = 50% of list price.
        price = {"opus": (4, 20), "sonnet": (2, 10), "haiku": (0.10, 0.50)}
        pin, pout = next(v for k, v in price.items() if k in a.model)
        sys_tok = sum(len(b["text"]) for b in system) / 4
        msg_tok = sum(len(r["params"]["messages"][0]["content"]) for r in requests) / 4
        in_cost = (sys_tok * (1.25 + 0.1 * (len(requests) - 1)) + msg_tok) * pin / 1e6
        out_cost = len(requests) * 3000 * pout / 1e6                 # rough: ~3k output tokens per diagnosis
        print(f"dry run: {len(requests)} games, system ~{sys_tok:.0f} tok, summaries ~{msg_tok / len(requests):.0f} tok each; "
              f"est. batch cost ~${(in_cost + out_cost) / 2:.2f} ({a.model}); summaries in {d}")
        return
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=requests)
    (d / "batch.json").write_text(json.dumps({"batch_id": batch.id, "model": a.model, "effort": a.effort,
                                              "games": index, "submitted": time.strftime("%Y-%m-%d %H:%M:%S")},
                                             indent=1), encoding="utf-8")
    print(f"submitted {len(requests)} games as batch {batch.id} ({a.model}, effort {a.effort}) -> {d}")


def results(a):
    import anthropic
    d = COACH / "runs" / a.run
    meta = json.loads((d / "batch.json").read_text(encoding="utf-8"))
    client = anthropic.Anthropic()
    while True:
        batch = client.messages.batches.retrieve(meta["batch_id"])
        if batch.processing_status == "ended":
            break
        print(f"{batch.processing_status}: {batch.request_counts.processing} processing", flush=True)
        time.sleep(30)
    diagnoses, usage = {}, collections.Counter()
    for r in client.messages.batches.results(meta["batch_id"]):
        if r.result.type != "succeeded":
            print(f"[{r.custom_id}] {r.result.type}")
            continue
        msg = r.result.message
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            usage[k] += getattr(msg.usage, k, 0) or 0
        if msg.stop_reason == "refusal":
            print(f"[{r.custom_id}] refused")
            continue
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            diagnoses[r.custom_id] = json.loads(text)
        except json.JSONDecodeError:
            print(f"[{r.custom_id}] unparseable output ({msg.stop_reason})")
    (d / "diagnoses.json").write_text(json.dumps(diagnoses, indent=1), encoding="utf-8")
    write_report(d, meta, diagnoses, usage)


def write_report(d, meta, diagnoses, usage):
    weight = {"minor": 1, "major": 2, "decisive": 3}
    cat = collections.Counter()
    for dg in diagnoses.values():
        for rc in dg["root_causes"]:
            cat[rc["category"]] += weight[rc["severity"]]
    lines = [f"# Replay coach report: run `{d.name}`", "",
             f"{len(diagnoses)} games diagnosed with {meta['model']} (effort {meta['effort']}), batch {meta['batch_id']}.",
             f"Tokens: {usage['input_tokens']} input, {usage['cache_read_input_tokens']} cache read, "
             f"{usage['cache_creation_input_tokens']} cache write, {usage['output_tokens']} output.", "",
             "## Recurring root causes (severity-weighted: decisive 3, major 2, minor 1)", ""]
    lines += [f"- **{c}**: {w}" for c, w in cat.most_common()]
    lines += ["", "## Recommendations by category", ""]
    by_cat = collections.defaultdict(list)
    for cid, dg in diagnoses.items():
        for rec in dg["recommendations"]:
            by_cat[rec["category"]].append(f"{rec['change']} *(expected: {rec['expected_effect']}; from {cid})*")
    for c, _ in cat.most_common():
        if by_cat.get(c):
            lines += [f"### {c}", ""] + [f"- {x}" for x in by_cat[c]] + [""]
    lines += ["## Per game", ""]
    for cid, dg in diagnoses.items():
        g = meta["games"][cid]
        lines += [f"### {cid}", "", f"*{g.get('matchup') or g['kind']}, focus {g['focus_name']}, confidence {dg['confidence']}*", "",
                  dg["result_summary"], ""]
        lines += [f"- r{t['round']}: {t['what']}" for t in dg["turning_points"]]
        lines += [""] + [f"- **{rc['severity']} / {rc['category']}**: {rc['description']} (evidence: {rc['evidence']})"
                         for rc in dg["root_causes"]] + [""]
    out = d / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect-ladder"); c.add_argument("--latest", type=int, default=30)
    c = sub.add_parser("collect-duels"); c.add_argument("--top", type=int, default=8); c.add_argument("--resign", type=int, default=800)
    s = sub.add_parser("submit"); s.add_argument("--run", required=True); s.add_argument("--limit", type=int)
    s.add_argument("--dry-run", action="store_true", help="build summaries and estimate cost; no API call")
    s.add_argument("--model", default="claude-haiku-5-5"); s.add_argument("--effort", default="medium",
                   choices=["low", "medium", "high", "xhigh", "max"])
    r = sub.add_parser("results"); r.add_argument("--run", required=True)
    a = ap.parse_args()
    {"collect-ladder": collect_ladder, "collect-duels": collect_duels, "submit": submit, "results": results}[a.cmd](a)


if __name__ == "__main__":
    main()
