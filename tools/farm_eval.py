"""Compare economic throughput in both seats, with a passive opponent by default.

python tools/farm_eval.py --bots main forager --jobs 2
python tools/farm_eval.py --bots forager --vs main starter-cpp --jobs 2
"""
import argparse
import json
import os
from pathlib import Path
import arena
from eval import parse_maps

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bots', nargs='+', default=['main', 'forager'])
    ap.add_argument('--vs', nargs='+', default=['farm-idle'])
    ap.add_argument('--maps', default='maps')
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--out', default='.arena/farming/evaluation.json')
    a = ap.parse_args()
    os.environ.setdefault('CAMBC_OFFLINE', '1')
    os.environ.setdefault('CAMBC_NO_UPDATE_CHECK', '1')
    # Separate cache from concurrent combat training.
    arena.CACHE = arena.ARENA / 'farming-results.jsonl'
    bots = [arena.build(b) for b in a.bots]
    opponents = [arena.build(b) for b in a.vs]
    maps = parse_maps(a.maps)
    if not maps:
        raise SystemExit('No maps selected')
    batches = arena.suite_many(bots, opponents, maps, max(1, a.jobs), progress=True)
    result = {}
    for bot, rows in zip(bots, batches):
        valid = [r for r in rows if 'error' not in r]
        summary = dict(games=len(rows), errors=len(rows)-len(valid),
                       faults=sum(r['fault'] for r in valid),
                       wins=sum(r['won'] for r in valid),
                       mean_gems_per_round=sum(r['ours']/max(1,r['round']) for r in valid)/max(1,len(valid)),
                       mean_body_deaths=sum(r['deaths'] for r in valid)/max(1,len(valid)))
        result[bot.name] = dict(wasm_hash=bot.hash, summary=summary, games=rows)
        print(bot.name, json.dumps(summary), flush=True)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    if any(v['summary']['errors'] or v['summary']['faults'] for v in result.values()):
        raise SystemExit(1)

if __name__ == '__main__':
    main()
