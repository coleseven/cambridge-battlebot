"""Bake trained fighting networks into bot SOURCE, so a platform build (which compiles the submitted source)
plays the trained networks instead of the hand prior.

Training patches the genome blob into compiled .wasm files; `cambc bundle`/`submit` package source only, so
an unbaked bots/neat uploads the empty default blob. This writes bots/<out>/src (a copy of bots/neat/src)
with genome.cpp holding the blob as a static initializer. Resign is 0 (never) unless --resign is given:
training blobs make team A resign at round 800 to end duels early, which must not reach real games.

  .venv/Scripts/python tools/bake_fighter.py --source champ:2 --out fighter      # 2v2 champion set
  .venv/Scripts/python tools/bake_fighter.py --source neat:n2v2 --out fighter --verify
Sources: champ:<N> (N-v-N champion set), neat:<run>, cgp:<run>, prior.
--verify builds the baked bot and a slot patched with the same blob, and checks the wasm files are
byte-identical (then every game is identical too).
"""
import argparse
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena  # noqa: E402
import league  # noqa: E402
import neat_train as nt  # noqa: E402
import nvn  # noqa: E402


def blob_words(progs: dict, resign: int):
    data = nt.blob({r: progs[p] for p in progs for r in nt.ROWS[p]}, resign)
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    while words and words[-1] == 0:
        words.pop()
    return data, words


def genome_cpp(words, note: str) -> str:
    rows = [", ".join(f"0x{w:08X}u" for w in words[i:i + 8]) for i in range(0, len(words), 8)]
    body = ",\n    ".join(rows)
    return ('#include "genome.h"\n\n'
            f"// Baked by tools/bake_fighter.py: {note}\n"
            "// (do not hand-edit; regenerate from the champion set)\n"
            'extern "C" {\n'
            f"alignas(16) volatile std::uint32_t GENOME_BLOB[GENOME_WORDS] = {{\n    {body}\n}};\n"
            "}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="champ:2")
    ap.add_argument("--out", default="fighter")
    ap.add_argument("--resign", type=int, default=0)
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.source.startswith("champ:"):
        progs = nvn.champion_set(int(a.source.split(":")[1]))
    else:
        progs = league.programs(a.source, 2)
    data, words = blob_words(progs, a.resign)
    out = arena.ROOT / "bots" / a.out
    if out.exists() and not (out / "src" / "genome.cpp").exists():
        sys.exit(f"{out} exists and is not a baked fighter; refusing to overwrite")
    (out / "src").mkdir(parents=True, exist_ok=True)
    for f in (arena.ROOT / "bots" / "neat" / "src").iterdir():
        if f.is_file() and f.name != "genome.cpp":
            shutil.copy2(f, out / "src" / f.name)
    heroes = ", ".join(nt.PICKS[p] for p in sorted(progs)) or "none (hand prior)"
    (out / "src" / "genome.cpp").write_text(
        genome_cpp(words, f"source {a.source}, resign {a.resign}, {len(words)} words, networks for {heroes}"),
        encoding="utf-8")
    print(f"baked {a.source} ({len(words)} words, resign {a.resign}) into {out / 'src' / 'genome.cpp'}")
    if a.verify:
        baked = arena.build(out, a.out)
        slot = nt.build_slots(1, "bakeslots")[0]
        patched = nt.patch(slot, data, "patched")
        same = all((out / ".cambc" / "build" / n).read_bytes() == (slot / ".cambc" / "build" / n).read_bytes()
                   for n in ("bot.wasm", "bot-injected.wasm"))
        print(f"baked wasm {baked.hash}  patched slot {patched.hash}  ->  "
              f"{'IDENTICAL (same games guaranteed)' if same else 'DIFFERENT (compare games instead)'}")
        sys.exit(0 if same else 2)


if __name__ == "__main__":
    main()
