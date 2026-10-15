"""Generate duel/skirmish arena maps with the matchup encoded in wall tiles.

Each map has N champions per team. Team A's picks and team B's picks are written as 4-bit values
(champion_draft id + 1, 0 = empty) in wall tiles on row y=0 (and mirrored to row y=H-1 by
symmetry): slot k of team A uses x = 1 + 5k .. 4 + 5k, team B uses x = 1 + 5k .. on row y=1.
The duel bot's draft decodes these and picks accordingly (see bots/duel/src/draft.cpp).

Usage: .venv/Scripts/python tools/duel_maps.py --size 1 --out maps_duel/1v1
  writes <layout>__<a1>-<b1>.map26a for every ordered pair of the 11 picks (121 maps per layout).
"""
import argparse
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mapformat as mf  # noqa: E402

PICKS = ["ryth", "vesper", "vendetta", "ci", "stellar", "prism", "marcella", "willow", "ren", "varg", "tavi"]
W = H = 30


def encode_picks(m: mf.MapData, row: int, picks):
    for k, p in enumerate(picks):
        v = p + 1
        for bit in range(4):
            x = 1 + 5 * k + bit
            m.wall[row][x] = (v >> bit) & 1


def layout(name: str, n: int) -> mf.MapData:
    m = mf.MapData(width=W, height=H, symmetry=mf.ROTATIONAL180, champions=n)
    m.spawn_a = (13, 7, 16, 10)          # 3x3; team B mirrored at (14..16, 20..22)
    m.altar[3][14] = 1                   # one altar per side (mirror pair) so banking code has a target
    m.altar[26][15] = 1
    if name == "pillars":
        # A few wall pillars and bush patches between the spawns (mirrored by hand below).
        for x, y in [(9, 14), (10, 14), (19, 13), (20, 13), (14, 15), (15, 14)]:
            m.wall[y][x] = 1
        for x, y in [(6, 12), (7, 12), (6, 13), (7, 13), (22, 16), (23, 16), (22, 17), (23, 17)]:
            m.bush[y][x] = 1
    # Mirror every terrain board (rotational symmetry) so hand-placed features are fair.
    for board in (m.wall, m.bush, m.hole, m.altar):
        for y in range(H):
            for x in range(W):
                if board[y][x]:
                    board[H - 1 - y][W - 1 - x] = 1
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=1)
    ap.add_argument("--out", default="maps_duel/1v1")
    ap.add_argument("--layouts", default="open,pillars")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    teams = list(itertools.combinations(range(len(PICKS)), a.size))
    for lay in a.layouts.split(","):
        for ta in teams:
            for tb in teams:
                m = layout(lay, a.size)
                encode_picks(m, 0, ta)
                encode_picks(m, 1, tb)
                for board in (m.wall,):
                    for y in (0, 1):
                        for x in range(W):
                            if board[y][x]:
                                board[H - 1 - y][W - 1 - x] = 1
                name = f"{lay}__{'+'.join(PICKS[p] for p in ta)}-{'+'.join(PICKS[p] for p in tb)}.map26a"
                mf.write(m, out / name)
                n += 1
    print(f"wrote {n} maps to {out}")


if __name__ == "__main__":
    main()
