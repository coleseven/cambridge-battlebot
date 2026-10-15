"""Reader/writer for CAMBC 2026a `.map26a` map files.

See docs/MAP_FORMAT.md for the byte layout. Summary:

    magic "w62pam" | 5 version bytes | 12 ground colours | flowers flag | bush colour + tint range
    | symmetry u8 | champions/team u8 | width u16 | height u16 | num_occluder_boards u16
    | bush, wall, hole, altar bitboards (run-length, 8x8 chunk order)
    | bush-cluster id grid (value,count) runs | occluder bitboards (derived)
    | team A spawn rect 4 x u16 | camp count u8 | camps (type, x, y) u8 each

Bush clusters and occluder boards are derived from bushes and walls; `write` recomputes
them, so a MapData only needs the editable fields. Camps are stored for one side only:
the engine resolves the reflected tile to the same camp.

    python tools/mapformat.py maps/*.map26a     # round-trip check (byte-for-byte)
    python tools/mapformat.py --show maps/packs.map26a
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field

MAGIC = b"w62pam"
HORIZONTAL, VERTICAL, ROTATIONAL180 = 0, 1, 2  # x reversed / y reversed / both
SYMMETRY_NAMES = {HORIZONTAL: "horizontal", VERTICAL: "vertical", ROTATIONAL180: "rotational180"}
WOLF, ELK, GEM_CLUSTER, GOLD_CLUSTER, MONOLITH = 0, 1, 2, 3, 4
CAMP_NAMES = {WOLF: "wolf", ELK: "elk", GEM_CLUSTER: "gem_cluster", GOLD_CLUSTER: "gold_cluster", MONOLITH: "monolith"}
COLOR_NAMES = (
    "grass", "dirt", "grass_highlight", "grass_shade", "grass_wall", "dirt_dark",
    "dirt_dusty", "dirt_moss", "pebble", "slab", "flower_a", "flower_b",
)
# Values found in every pool map; used as defaults for generated maps.
DEFAULT_VERSION = b"\x00\x00\x00\x00\x01"
DEFAULT_COLORS = (0x5E6F25, 0x504E26, 0x8D9E25, 0x395125, 0x4B6927, 0x454521,
                  0x58562C, 0x444C23, 0x545447, 0x545242, 0xD9CC8C, 0xB89ED9)
DEFAULT_BUSH_COLOR = 0x05332A
DEFAULT_BUSH_TINT = (0x789065, 0xC0D6A0)


@dataclass
class MapData:
    width: int
    height: int
    symmetry: int = ROTATIONAL180
    champions: int = 3
    # Grids are indexed grid[y][x] with values 0/1; y=0 is the bottom row in the visualiser.
    bush: list = None
    wall: list = None
    hole: list = None
    altar: list = None
    spawn_a: tuple = (0, 0, 3, 3)  # team A spawn rectangle (min_x, min_y, max_x, max_y), max exclusive
    camps: list = field(default_factory=list)  # (type, x, y) for one side only
    version: bytes = DEFAULT_VERSION
    colors: tuple = DEFAULT_COLORS
    flowers: int = 1
    bush_color: int = DEFAULT_BUSH_COLOR
    bush_tint: tuple = DEFAULT_BUSH_TINT

    def __post_init__(self):
        for k in ("bush", "wall", "hole", "altar"):
            if getattr(self, k) is None:
                setattr(self, k, [[0] * self.width for _ in range(self.height)])

    # --- geometry helpers -------------------------------------------------
    def reflect(self, x: int, y: int) -> tuple:
        if self.symmetry == HORIZONTAL:
            return self.width - 1 - x, y
        if self.symmetry == VERTICAL:
            return x, self.height - 1 - y
        return self.width - 1 - x, self.height - 1 - y

    def spawn_tiles(self, team: int) -> list:
        """Spawn tiles for team 0 (A) or 1 (B, the reflection of A)."""
        x0, y0, x1, y1 = self.spawn_a
        tiles = [(x, y) for y in range(y0, y1) for x in range(x0, x1)]
        return tiles if team == 0 else [self.reflect(x, y) for x, y in tiles]

    def all_camps(self) -> list:
        """Stored camps plus their reflections (a camp on its own mirror tile appears once)."""
        out = list(self.camps)
        for t, x, y in self.camps:
            rx, ry = self.reflect(x, y)
            if (rx, ry) != (x, y):
                out.append((t, rx, ry))
        return out

    def tile(self, x: int, y: int) -> str:
        for k, c in (("wall", "#"), ("hole", "o"), ("altar", "A"), ("bush", "*")):
            if getattr(self, k)[y][x]:
                return c
        return "."

    def ascii(self) -> str:
        g = [[self.tile(x, y) for x in range(self.width)] for y in range(self.height)]
        for team, ch in ((0, "a"), (1, "b")):
            for x, y in self.spawn_tiles(team):
                g[y][x] = ch
        for t, x, y in self.all_camps():
            g[y][x] = "WEGDM"[t]
        return "\n".join("".join(r) for r in reversed(g))


# --- bitboards --------------------------------------------------------------
def _chunks(w: int, h: int) -> tuple:
    return (w + 7) // 8, (h + 7) // 8


def _bit_index(x: int, y: int, cw: int) -> int:
    return ((y >> 3) * cw + (x >> 3)) * 64 + (y & 7) * 8 + (x & 7)


def _decode_board(runs: list, w: int, h: int) -> list:
    cw, ch = _chunks(w, h)
    bits = bytearray(cw * ch * 64)
    pos, val = 0, 0
    for r in runs:
        if val:
            bits[pos:pos + r] = b"\x01" * r
        pos += r
        val ^= 1
    return [[bits[_bit_index(x, y, cw)] for x in range(w)] for y in range(h)]


def _encode_board(grid: list, w: int, h: int) -> list:
    cw, ch = _chunks(w, h)
    bits = bytearray(cw * ch * 64)
    for y in range(h):
        for x in range(w):
            if grid[y][x]:
                bits[_bit_index(x, y, cw)] = 1
    runs, val, n = [], 0, 0
    for b in bits:
        if b == val:
            n += 1
        else:
            runs.append(n)
            val, n = b, 1
    runs.append(n)
    out = []
    for r in runs:  # split runs that overflow u16 with zero-length opposite runs
        while r > 0xFFFF:
            out += [0xFFFF, 0]
            r -= 0xFFFF
        out.append(r)
    return out


def bush_clusters(bush: list, w: int, h: int) -> tuple:
    """4-connected components labelled 1.. in row-major scan order (y, then x)."""
    lab = [[0] * w for _ in range(h)]
    n = 0
    for y in range(h):
        for x in range(w):
            if bush[y][x] and not lab[y][x]:
                n += 1
                lab[y][x] = n
                stack = [(x, y)]
                while stack:
                    cx, cy = stack.pop()
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < w and 0 <= ny < h and bush[ny][nx] and not lab[ny][nx]:
                            lab[ny][nx] = n
                            stack.append((nx, ny))
    return lab, n


# --- read / write -----------------------------------------------------------
class _Reader:
    def __init__(self, b: bytes):
        self.b, self.p = b, 0

    def take(self, fmt: str):
        v = struct.unpack_from("<" + fmt, self.b, self.p)
        self.p += struct.calcsize("<" + fmt)
        return v if len(v) > 1 else v[0]

    def runs(self, total: int) -> list:
        out, s = [], 0
        while s < total:
            r = self.take("H")
            out.append(r)
            s += r
        if s != total:
            raise ValueError("bitboard runs overflow")
        return out


def decode(b: bytes) -> MapData:
    if b[:6] != MAGIC:
        raise ValueError("not a .map26a file (bad magic)")
    r = _Reader(b)
    r.p = 6
    version = b[6:11]
    r.p = 11
    colors = tuple(r.take("I") for _ in range(12))
    flowers = r.take("B")
    bush_color = r.take("I")
    tint = (r.take("I"), r.take("I"))
    symmetry, champions = r.take("B"), r.take("B")
    w, h = r.take("H"), r.take("H")
    n_occ = r.take("H")
    cw, ch = _chunks(w, h)
    total = cw * ch * 64
    boards = [_decode_board(r.runs(total), w, h) for _ in range(4)]
    s = 0
    while s < w * h:  # cluster id grid; derived, skipped
        r.take("H")
        s += r.take("H")
    for _ in range(n_occ):  # occluder boards; derived, skipped
        r.runs(total)
    spawn = tuple(r.take("H") for _ in range(4))
    n_camps = r.take("B")
    camps = [tuple(r.take("B") for _ in range(3)) for _ in range(n_camps)]
    if r.p != len(b):
        raise ValueError(f"{len(b) - r.p} trailing bytes")
    return MapData(w, h, symmetry, champions, *boards, spawn, camps, version, colors, flowers, bush_color, tint)


def encode(m: MapData) -> bytes:
    w, h = m.width, m.height
    if not (0 < w <= 0xFFFF and 0 < h <= 0xFFFF) or len(m.camps) > 255:
        raise ValueError("map too large")
    out = bytearray(MAGIC + bytes(m.version))
    out += struct.pack("<12I", *m.colors)
    out += struct.pack("<BIII", m.flowers, m.bush_color, *m.bush_tint)
    lab, n = bush_clusters(m.bush, w, h)
    out += struct.pack("<BBHHH", m.symmetry, m.champions, w, h, n + 1)

    def board(grid):
        runs = _encode_board(grid, w, h)
        out.extend(struct.pack(f"<{len(runs)}H", *runs))

    for g in (m.bush, m.wall, m.hole, m.altar):
        board(g)
    flat = [v for row in lab for v in row]
    i = 0
    while i < len(flat):
        j = i
        while j < len(flat) and flat[j] == flat[i] and j - i < 0xFFFF:
            j += 1
        out += struct.pack("<HH", flat[i], j - i)
        i = j
    # Occluders seen from inside cluster k: walls plus every bush not in cluster k (k=0: all bushes).
    for k in range(n + 1):
        board([[1 if m.wall[y][x] or (m.bush[y][x] and lab[y][x] != k) else 0 for x in range(w)] for y in range(h)])
    out += struct.pack("<4H", *m.spawn_a)
    out.append(len(m.camps))
    for c in m.camps:
        out += bytes(c)
    return bytes(out)


def read(path) -> MapData:
    with open(path, "rb") as f:
        return decode(f.read())


def write(m: MapData, path) -> None:
    with open(path, "wb") as f:
        f.write(encode(m))


def main(argv: list) -> int:
    show = "--show" in argv
    paths = [a for a in argv if a != "--show"]
    bad = 0
    for p in paths:
        b = open(p, "rb").read()
        m = decode(b)
        same = encode(m) == b
        bad += not same
        print(f"{p}: {m.width}x{m.height} {SYMMETRY_NAMES[m.symmetry]} champs={m.champions} "
              f"camps={len(m.camps)} round-trip={'OK' if same else 'MISMATCH'}")
        if show:
            print(m.ascii())
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
