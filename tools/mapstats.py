"""Per-map features for .map26a files (pool or generated).

    python tools/mapstats.py maps/                 # table
    python tools/mapstats.py maps/ --json out.json # full feature dicts

Distances are king-move BFS steps (8 directions, as champions move) from the nearest own
spawn tile to the nearest tile from which the target can be used: Chebyshev 1 of an altar
(tribute range) or of a camp tile. Walls, holes, altars, camp tiles and the enemy spawn block
movement (camp tiles are walkable when measuring camp distances, since camps can be killed). Dashing over holes is ignored, so distances are an upper bound.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mapformat as mf  # noqa: E402

NB8 = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]


def blocked_grid(m: mf.MapData, team: int, camps_block: bool = True) -> list:
    blk = [[bool(m.wall[y][x] or m.hole[y][x] or m.altar[y][x]) for x in range(m.width)] for y in range(m.height)]
    for _, x, y in m.all_camps() if camps_block else ():
        blk[y][x] = True
    for x, y in m.spawn_tiles(1 - team):
        blk[y][x] = True
    return blk


def bfs(m: mf.MapData, team: int, blk=None) -> list:
    """Distance grid from team's spawn tiles (None = unreachable)."""
    blk = blk or blocked_grid(m, team)
    dist = [[None] * m.width for _ in range(m.height)]
    q = deque()
    for x, y in m.spawn_tiles(team):
        dist[y][x] = 0
        q.append((x, y))
    while q:
        x, y = q.popleft()
        d = dist[y][x] + 1
        for dx, dy in NB8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < m.width and 0 <= ny < m.height and not blk[ny][nx] and dist[ny][nx] is None:
                dist[ny][nx] = d
                q.append((nx, ny))
    return dist


def reach_dist(m: mf.MapData, dist: list, x: int, y: int):
    best = None
    for dx, dy in NB8:
        nx, ny = x + dx, y + dy
        if 0 <= nx < m.width and 0 <= ny < m.height and dist[ny][nx] is not None:
            best = dist[ny][nx] if best is None else min(best, dist[ny][nx])
    return best


def altars(m: mf.MapData) -> list:
    return [(x, y) for y in range(m.height) for x in range(m.width) if m.altar[y][x]]


def detect_symmetry(m: mf.MapData) -> list:
    """Which reflections leave all terrain unchanged (the header names only one)."""
    out = []
    for s in (mf.HORIZONTAL, mf.VERTICAL, mf.ROTATIONAL180):
        t = mf.MapData(m.width, m.height, s)
        if all(getattr(m, k)[y][x] == getattr(m, k)[t.reflect(x, y)[1]][t.reflect(x, y)[0]]
               for k in ("bush", "wall", "hole", "altar") for y in range(m.height) for x in range(m.width)):
            out.append(mf.SYMMETRY_NAMES[s])
    return out


def features(m: mf.MapData) -> dict:
    area = m.width * m.height
    counts = {k: sum(map(sum, getattr(m, k))) for k in ("bush", "wall", "hole", "altar")}
    camps = m.all_camps()
    by_type = {n: sum(1 for t, _, _ in camps if t == k) for k, n in mf.CAMP_NAMES.items()}
    _, clusters = mf.bush_clusters(m.bush, m.width, m.height)
    f = {
        "width": m.width, "height": m.height, "area": area,
        "symmetry": mf.SYMMETRY_NAMES[m.symmetry], "terrain_symmetric_under": detect_symmetry(m),
        "champions": m.champions, "spawn_a": list(m.spawn_a), "spawn_tiles": len(m.spawn_tiles(0)),
        **{f"{k}s": v for k, v in counts.items()},
        **{f"{k}_density": round(v / area, 4) for k, v in counts.items() if k != "altar"},
        "bush_clusters": clusters, "camps_stored": len(m.camps), "camps_total": len(camps),
        "camps_by_type": by_type,
    }
    for team in (0, 1):
        dist = bfs(m, team)
        ad = [reach_dist(m, dist, x, y) for x, y in altars(m)]
        # Camps die, so a camp walled in by its own group still counts: walk through camp tiles.
        cdist = bfs(m, team, blocked_grid(m, team, camps_block=False))
        cd = [reach_dist(m, cdist, x, y) for _, x, y in camps]
        tag = "ab"[team]
        f[f"unreachable_{tag}"] = sum(d is None for d in ad + cd)
        ad, cd = sorted(d for d in ad if d is not None), sorted(d for d in cd if d is not None)
        f[f"altar_dists_{tag}"] = ad
        f[f"camp_dist_min_{tag}"] = cd[0] if cd else None
        f[f"camp_dist_mean_{tag}"] = round(sum(cd) / len(cd), 1) if cd else None
        f[f"walkable_reached_{tag}"] = sum(d is not None for row in dist for d in row)
    blk = blocked_grid(m, 0, camps_block=False)
    for x, y in m.spawn_tiles(1):
        blk[y][x] = False
    dist = bfs(m, 0, blk)
    f["spawn_to_enemy_spawn"] = min((dist[y][x] for x, y in m.spawn_tiles(1) if dist[y][x] is not None), default=None)
    return f


def map_paths(args: list) -> list:
    out = []
    for a in args:
        p = Path(a)
        out += sorted(p.glob("*.map26a")) if p.is_dir() else [p]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", help="write feature dicts to this file")
    a = ap.parse_args()
    rows = {}
    hdr = f"{'map':14} {'size':>7} {'sym':>6} {'ch':>2} {'bush':>5} {'wall':>5} {'hole':>5} {'alt':>3} " \
          f"{'clus':>4} {'camps':>5} {'W/E/G/D/M':>12} {'altar d (A)':>16} {'camp d':>6} {'unr':>3}"
    print(hdr)
    for p in map_paths(a.paths):
        f = features(mf.read(p))
        rows[p.stem] = f
        ct = "/".join(str(v) for v in f["camps_by_type"].values())
        print(f"{p.stem[:14]:14} {f['width']:>3}x{f['height']:<3} {f['symmetry'][:6]:>6} {f['champions']:>2} "
              f"{f['bushs']:>5} {f['walls']:>5} {f['holes']:>5} {f['altars']:>3} {f['bush_clusters']:>4} "
              f"{f['camps_total']:>5} {ct:>12} {','.join(map(str, f['altar_dists_a']))[:16]:>16} "
              f"{f['camp_dist_min_a']!s:>6} {f['unreachable_a'] + f['unreachable_b']:>3}")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
