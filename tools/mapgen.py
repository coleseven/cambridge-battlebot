"""Procedural .map26a generator: symmetric, fair, connected maps resembling the pool.

    python tools/mapgen.py --n 40 --seed 1 --out maps_gen/

Every feature is stamped together with its reflection under the map's symmetry (the pool's:
rotational180 most often, plus horizontal and vertical), so spawns, altars, camps and terrain
mirror exactly. A candidate is kept only if both spawns reach every altar and camp (see
mapstats.py for the distance rules) and the enemy spawn; otherwise it is regenerated.
Ranges for size, densities, altar/camp counts and champions follow the pool (mapstats.py maps/).
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mapformat as mf  # noqa: E402
import mapstats as ms  # noqa: E402

W_, E_, G_, D_, M_ = mf.WOLF, mf.ELK, mf.GEM_CLUSTER, mf.GOLD_CLUSTER, mf.MONOLITH
# Camp group recipes seen in the pool: (weight, [type per member]).
RECIPES = [
    (6, [W_]), (5, [W_, W_]), (2, [W_, W_, W_]), (3, [E_]), (3, [E_, D_]), (3, [G_]), (2, [G_, G_]),
    (3, [D_]), (1, [D_, D_]), (2, [G_, D_, D_, G_]), (1, [W_, E_]),
]
# Member offsets for a group of n camps (cardinally connected).
SHAPES = {
    1: [[(0, 0)]],
    2: [[(0, 0), (1, 0)], [(0, 0), (0, 1)]],
    3: [[(0, 0), (1, 0), (2, 0)], [(0, 0), (0, 1), (0, 2)], [(0, 0), (1, 0), (0, 1)]],
    4: [[(0, 0), (1, 0), (0, 1), (1, 1)]],
}


class Builder:
    def __init__(self, rng: random.Random, w: int, h: int, sym: int):
        self.rng, self.w, self.h = rng, w, h
        self.m = mf.MapData(w, h, sym)
        self.protect = [[False] * w for _ in range(h)]  # kept as plain ground

    def free(self, x, y) -> bool:
        m = self.m
        return (0 <= x < self.w and 0 <= y < self.h and not self.protect[y][x]
                and not (m.bush[y][x] or m.wall[y][x] or m.hole[y][x] or m.altar[y][x]))

    def stamp(self, kind: str, tiles) -> int:
        n = 0
        grid = getattr(self.m, kind)
        for x, y in tiles:
            for tx, ty in ((x, y), self.m.reflect(x, y)):
                if self.free(tx, ty):
                    grid[ty][tx] = 1
                    n += 1
        return n

    def density(self, kind: str) -> float:
        return sum(map(sum, getattr(self.m, kind))) / (self.w * self.h)

    # --- shapes -----------------------------------------------------------
    def rect(self, x, y, w, h):
        return [(x + i, y + j) for i in range(w) for j in range(h)]

    def line(self, x, y, dx, dy, length, thick):
        out = []
        for i in range(length):
            cx, cy = x + dx * i, y + dy * i
            for t in range(thick):
                # thicken perpendicular-ish; diagonals get an extra x step so they can't be slipped through
                out.append((cx + (t if dy else 0), cy + (0 if dy else t)))
                if dx and dy:
                    out.append((cx + 1, cy))
        return out

    def rnd_pt(self, margin=0):
        return self.rng.randrange(margin, self.w - margin), self.rng.randrange(margin, self.h - margin)

    def shape(self, kind: str):
        r = self.rng
        x, y = self.rnd_pt()
        if kind == "wall":
            if r.random() < 0.65:
                d = r.choice([(1, 0), (0, 1), (1, 1), (1, -1)])
                return self.line(x, y, *d, r.randint(4, max(5, min(self.w, self.h) // 2)), r.choice([1, 2, 2]))
            return self.rect(x, y, r.randint(2, 5), r.randint(2, 5))
        if kind == "hole":
            if r.random() < 0.7:
                return self.rect(x, y, r.randint(2, 6), r.randint(2, 6))
            return self.line(x, y, *r.choice([(1, 0), (0, 1)]), r.randint(4, 12), r.choice([1, 2, 3]))
        # bush
        roll = r.random()
        if roll < 0.45:
            return self.rect(x, y, r.randint(2, 5), r.randint(2, 5))
        if roll < 0.85:
            return self.line(x, y, *r.choice([(1, 0), (0, 1), (1, 1)]), r.randint(3, 12), r.choice([1, 1, 2]))
        return self.line(x, y, 1, 0, r.randint(3, 7), 1) + self.line(x, y, 0, 1, r.randint(3, 7), 1)


def pick_size(r: random.Random, sym: int) -> tuple:
    if r.random() < 0.15 and sym != mf.ROTATIONAL180:  # long lane map like `lanes`
        long_, short = r.randint(60, 100), r.randint(30, 40)
        return (long_, short) if sym == mf.HORIZONTAL else (short, long_)
    s = int(r.triangular(30, 85, 40))
    return s, max(30, min(100, s + r.choice([0, 0, 0, 1, -1])))


def pick_spawn(r: random.Random, m: mf.MapData) -> tuple:
    sw, sh = r.choice([(3, 3), (3, 3), (3, 3), (4, 4), (3, 4), (4, 3)])
    edge = r.choice([0, 0, 1, 2, 3])
    if m.symmetry == mf.HORIZONTAL:  # team A on the left (small x)
        x0, y0 = r.randint(0, min(6, m.width // 6)), r.randint(0, m.height - sh)
    elif m.symmetry == mf.VERTICAL:  # team A at the bottom (small y)
        x0, y0 = r.randint(0, m.width - sw), r.randint(0, min(6, m.height // 6))
    else:  # corner or the low end of an edge
        x0 = r.randint(0, edge + 1)
        y0 = r.randint(0, edge + 1) if r.random() < 0.6 else r.randint(0, m.height // 2 - sh)
    return x0, y0, x0 + sw, y0 + sh


def generate(r: random.Random) -> mf.MapData:
    sym = r.choices([mf.ROTATIONAL180, mf.HORIZONTAL, mf.VERTICAL], [6, 2, 1])[0]
    w, h = pick_size(r, sym)
    b = Builder(r, w, h, sym)
    m = b.m
    m.champions = r.choice([3, 4, 5])
    m.spawn_a = pick_spawn(r, m)
    sp = m.spawn_tiles(0) + m.spawn_tiles(1)
    for sx, sy in sp:  # keep a ring of ground around both spawns
        for y in range(max(0, sy - 2), min(h, sy + 3)):
            for x in range(max(0, sx - 2), min(w, sx + 3)):
                b.protect[y][x] = True

    # terrain: densities drawn within the pool's ranges
    targets = {"wall": r.choice([0, r.uniform(0.01, 0.09)]) if r.random() < 0.3 else r.uniform(0.01, 0.09),
               "hole": 0 if r.random() < 0.3 else r.uniform(0.003, 0.04),
               "bush": 0 if r.random() < 0.1 else r.uniform(0.01, 0.10)}
    for kind in ("wall", "hole", "bush"):
        tries = 0
        while b.density(kind) < targets[kind] and tries < 400:
            b.stamp(kind, b.shape(kind))
            tries += 1

    area = w * h
    # altars: pairs (plus a self-mirrored centre altar sometimes)
    pairs = max(1, min(9, round(area * r.uniform(0.0005, 0.003))))
    placed, tries = 0, 0
    while placed < pairs and tries < 500:
        tries += 1
        x, y = b.rnd_pt(1)
        rx, ry = m.reflect(x, y)
        if (rx, ry) == (x, y) or max(abs(rx - x), abs(ry - y)) < 4 or not (b.free(x, y) and b.free(rx, ry)):
            continue
        b.stamp("altar", [(x, y)])
        placed += 1
    if r.random() < 0.25:
        c = [(x, y) for y in range(h) for x in range(w) if m.reflect(x, y) == (x, y) and b.free(x, y)]
        if c:
            b.stamp("altar", [r.choice(c)])

    # camps (stored once; the engine mirrors them)
    occupied = set()
    near_spawn = lambda x, y: any(max(abs(x - sx), abs(y - sy)) < 5 for sx, sy in sp)
    n_total = max(8, round(area * r.uniform(0.007, 0.018)))
    monolith = r.random() < 0.25
    tries = 0
    while len(m.camps) * 2 < n_total and tries < 2000:
        tries += 1
        if monolith:
            types, monolith = [M_], False
        else:
            types = r.choices([t for _, t in RECIPES], [wt for wt, _ in RECIPES])[0]
        offs = r.choice(SHAPES[len(types)])
        x0, y0 = b.rnd_pt(1)
        tiles = [(x0 + dx, y0 + dy) for dx, dy in offs]
        refl = {m.reflect(x, y) for x, y in tiles}
        ok = all(b.free(x, y) and not near_spawn(x, y) for x, y in tiles)
        # no camp within Chebyshev 1 of another group, its own reflection, or another camp's reflection
        halo = {(x + dx, y + dy) for x, y in tiles for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        ok = ok and not (halo & occupied) and not (halo & refl)
        if not ok:
            continue
        for (x, y), t in zip(tiles, types):
            m.camps.append((t, x, y))
        occupied |= set(tiles) | refl
    return m


def acceptable(m: mf.MapData) -> bool:
    f = ms.features(m)
    if f["unreachable_a"] or f["unreachable_b"] or f["spawn_to_enemy_spawn"] is None:
        return False
    if f["altars"] < 2 or f["camps_total"] < 6 or len(m.camps) > 250:
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="maps_gen")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i in range(a.n):
        attempt = 0
        while True:
            r = random.Random(f"{a.seed}:{i}:{attempt}")
            m = generate(r)
            if acceptable(m):
                break
            attempt += 1
        path = out / f"gen{a.seed}_{i:02d}.map26a"
        mf.write(m, path)
        assert mf.encode(mf.read(path)) == path.read_bytes()
        print(f"{path}: {m.width}x{m.height} {mf.SYMMETRY_NAMES[m.symmetry]} champs={m.champions} "
              f"camps={len(m.all_camps())} attempts={attempt + 1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
