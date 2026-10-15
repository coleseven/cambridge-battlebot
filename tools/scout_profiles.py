"""Reproducible, ranked-only top-ten profiles from a saved scout snapshot."""
import collections as C
import json
import statistics as S
from pathlib import Path

ROOT = Path('scouting/2026-10-15')
DRAFT = ['Ryth','Vesper','Vendetta','C&I','Stellar','Prism','Marcella','Willow','Ren','Varg','Tavi']
BODY = ['Ryth','Vesper','Vendetta','Cassi','Issac','Stellar','Prism','Marcella','Willow','Ren','Varg','Tavi']
ITEM = [f'{family} {stat}' for family in ['Monster','Power','Durability','Treasure','Jungle','Home','Bruiser'] for stat in ['Strength','Focus','Health']]
ladder = json.loads((ROOT/'ladder.json').read_text(encoding='utf-8'))['teams'][:10]
groups = [json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'groups').glob('*.json')]
profiles = []
for team in ladder:
    eligible = sorted([g for g in groups if g['group']['kind']=='ranked' and any(s['teamId']==team['teamId'] for s in g['group']['sides'])], key=lambda g:g['group']['createdAt'], reverse=True)[:20]
    rows, seen = [], set()
    for g in eligible:
        for m in g['matches']:
            if m['id'] in seen or m['status']!='finished' or not m.get('statistics'): continue
            seen.add(m['id'])
            i = next(i for i,s in enumerate(m['sides']) if s['teamId']==team['teamId'])
            cs = [c for c in m['statistics']['champions'] if c['team']==i]
            rows.append(dict(id=m['id'],date=m['createdAt'],map=m['map'],rounds=m['rounds'],win=m['sides'][i]['result']=='win',score=m['sides'][i]['score'],opp=m['sides'][1-i]['teamName'],opp_score=m['sides'][1-i]['score'],draft=[DRAFT[c] for c in m['statistics']['teams'][i]['draftPicks']],champs=cs))
    if not rows: continue
    n=len(rows)
    def avg(k): return round(sum(sum(c[k] for c in r['champs']) for r in rows)/n,2)
    picks=C.Counter(c for r in rows for c in r['draft'])
    builds={}
    for typ in sorted({c['type'] for r in rows for c in r['champs']}):
        cs=[c for r in rows for c in r['champs'] if c['type']==typ]
        # Odd entries count auto-combined emblems too; only even shard entries measure purchases.
        its=C.Counter({ITEM[j//2]:sum(c['itemsBought'][j] for c in cs) for j in range(0,42,2)})
        builds[BODY[typ]]=dict(n=len(cs),shards_per_appearance={k:round(v/len(cs),2) for k,v in its.most_common(4) if v},gems=round(S.mean(c['submittedGems'] for c in cs),1),kills=round(S.mean(c['kills'] for c in cs),2))
    drafts={str(size):C.Counter('/'.join(r['draft']) for r in rows if len(r['draft'])==size).most_common(2) for size in sorted({len(r['draft']) for r in rows})}
    p=dict(rank=team['rank'],name=team['name'],rating=round(team['rating'],1),teamId=team['teamId'],n=n,groups=len(eligible),start=min(r['date'] for r in rows),end=max(r['date'] for r in rows),win_pct=round(100*sum(r['win'] for r in rows)/n,1),win_rounds=S.median([r['rounds'] for r in rows if r['win']]) if any(r['win'] for r in rows) else None,kills=avg('kills'),body_deaths=avg('deaths'),collected=avg('collectedGems'),banked=round(S.mean(r['score'] for r in rows),1),monster_damage=avg('monsterDamageDone'),pvp_damage=avg('championDamageDone'),picks=dict(picks),drafts=drafts,builds=builds,consumables_per_game=[round(sum(c['consumablesBought'][j] for r in rows for c in r['champs'])/n,2) for j in range(4)],maps={mp:dict(n=len(rs),wins=sum(r['win'] for r in rs),margin=round(S.mean(r['score']-r['opp_score'] for r in rs),1)) for mp in sorted({r['map'] for r in rows}) if (rs:=[r for r in rows if r['map']==mp])},examples=[{k:v for k,v in r.items() if k!='champs'} for r in rows[:3]])
    profiles.append(p)
(ROOT/'profiles.json').write_text(json.dumps(profiles,indent=2,ensure_ascii=False),encoding='utf-8')
for p in profiles:
    print(json.dumps({k:v for k,v in p.items() if k not in ['builds','examples','teamId']},ensure_ascii=True))
    print('BUILDS',json.dumps(p['builds'],ensure_ascii=True))
