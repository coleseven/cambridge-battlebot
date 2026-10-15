# `.map26a` map format (CAMBC 2026a, cambc 2.1.6)

Reverse-engineered from the 9 pool maps and checked against the engine itself: the map editor's
engine WASM (`<cambc cache>/assets/<hash>/mapeditor/assets/v0_0_1-*.wasm`) exports
`MapBuilder_FromFileBytes` / `MapBuilder_Get*` / `MapBuilder_Encode`. `tools/map_engine_check.mjs`
drives them from Node. `tools/mapformat.py` re-encodes all 9 pool maps byte-for-byte, and the
engine's own encoder also reproduces each pool and generated file exactly.

The file is **not compressed** (the "xz" strings in the cambc binary are for something else).
All integers are little-endian.

## Layout

| Offset | Size | Field |
|---|---|---|
| 0 | 6 | magic `w62pam` |
| 6 | 5 | `00 00 00 00 01` in every map: version/flags, see *Unknowns* |
| 11 | 12 × u32 | ground colours `0x00RRGGBB`: grass, dirt, grass highlight, grass shade, grass wall, dirt dark, dirt dusty, dirt moss, pebble, slab, flower A, flower B |
| 59 | u8 | flowers enabled (1) |
| 60 | u32 | bush base colour |
| 64 | 2 × u32 | bush tint range (lo, hi colours) |
| 72 | u8 | symmetry: 0 horizontal (x reversed), 1 vertical (y reversed), 2 rotational180 |
| 73 | u8 | champions per team (editor allows 3–5) |
| 74 | u16, u16 | width, height (30–100 in practice) |
| 78 | u16 | `K` = number of bush clusters + 1 (count of occluder boards below) |
| 80 | bitboard | bush |
| … | bitboard | wall |
| … | bitboard | hole |
| … | bitboard | altar (all altars, both sides) |
| … | (u16 id, u16 count)* | bush-cluster id grid, row-major `y*W+x`, runs until W·H cells |
| … | K × bitboard | occluder boards, board k = walls ∪ (bushes not in cluster k); board 0 = walls ∪ all bushes |
| … | 4 × u16 | team A spawn rectangle: min_x, min_y, max_x, max_y (**max exclusive**, 9–16 tiles) |
| … | u8 | camp count `n` (engine limit 250) |
| … | n × (u8 type, u8 x, u8 y) | camps, for **one side only** |

Coordinates: `(x, y)` with y = 0 the bottom row in the visualiser; the grid is `grid[y][x]`.

### Bitboards

Tiles are stored in 8×8 chunks. With `cw = ceil(W/8)`, `ch = ceil(H/8)`, bit index of `(x, y)`
is `((y>>3)*cw + (x>>3))*64 + (y&7)*8 + (x&7)`, over `cw*ch*64` bits. (In memory the engine pads
`cw` up to even, the `chunkStride`, but the file uses `cw`.) The bits are stored as alternating
run lengths, u16 each, starting with a run of zeros (which may be 0). The board ends when the runs
sum to `cw*ch*64`. Padding bits outside the map are 0.

### Derived sections

The cluster grid and occluder boards are fully determined by bushes and walls. `mapformat.write`
recomputes them:
- clusters = 4-connected bush components, numbered 1.. in row-major scan order (y, then x);
  non-bush cells are 0. Runs are maximal (value, count) pairs.
- occluder board k is what blocks line of sight for a viewer standing in cluster k (your own
  cluster is transparent, as the vision rules say).

The engine validates these on load ("Cardinally connected bush tiles have different cluster IDs",
"Bush cluster ID is reused by disconnected bush regions", …).

### Camps

Types: 0 wolf, 1 elk, 2 gem cluster, 3 gold cluster, 4 monolith (the editor's `NONE` = −1 is
never stored). Only one side's camps are written. The engine maps the reflected tile to the same
camp: `MapBuilder_GetCampHandleAt` on a reflected tile returns the stored camp's handle, and the
editor refuses a camp that overlaps its mirror. Camp groups are not stored. They are the
cardinally connected components of camps, and each group's gem/gold value and cooldown come from
its members' types (e.g. two wolves: 44 gems, 180 gold, cooldown 40).

### Engine validity rules seen in the WASM strings

Map must be symmetrical; bushes, walls, holes and altars cannot overlap; spawn regions must be in
bounds and clear of obstructions; the spawn area must be 9–16 tiles; camp positions must fit u8 and
be in the map; at most 250 camps before mirroring.

## Unknowns / not pinned down

- **Bytes 6–10** (`00 00 00 00 01`) are identical in all maps. Most likely a u32 format version
  (the engine has an `invalid_map_version` error) plus one byte, but which byte is the version and
  what the trailing `01` means is unconfirmed. `mapformat` keeps them as opaque bytes (`version`).
- The **side** that camps are stored for isn't fixed. Pool maps store camps on either side of the
  axis (e.g. `packs` stores both (8,2) and (12,18)), so it seems to be whichever tile the author
  clicked. Either choice decodes to the same game.
- The flower and bush colour fields only affect rendering. Generated maps use the pool's palette,
  which every pool map shares.

## Tools

- `python tools/mapformat.py maps/*.map26a` checks the byte-for-byte round trip; add `--show` for
  an ASCII render (`#` wall, `o` hole, `*` bush, `A` altar, `a`/`b` spawns, camps
  `W`olf `E`lk `G`em `D` gold `M`onolith).
- `python tools/mapstats.py maps/ [--json f]` prints per-map features.
- `python tools/mapgen.py --n 40 --seed 1 --out maps_gen/` generates maps.
- `python tools/validate_maps.py maps_gen/` runs the full validation (decode, symmetry and
  reachability, engine load, starter-cpp game). On `maps/` it flags `collision`, where one altar
  per side is walled in (reachable only by teleport crystal). That is deliberate in the pool map,
  but the generator avoids it.
