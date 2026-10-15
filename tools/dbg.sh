#!/usr/bin/env bash
# Usage: tools/dbg.sh <map> [opponent] [team-a|team-b]  -- run bots/main with BOT_DEBUG=1 vs opponent, print our log.
set -e
cd "$(dirname "$0")/.."
MAP=${1:-elkring}; OPP=${2:-starter-cpp}
rm -rf .arena/dbg && mkdir -p .arena/dbg && cp -r bots/main/src .arena/dbg/src
sed -i 's/#define BOT_DEBUG 0/#define BOT_DEBUG 1/' .arena/dbg/src/bot.cpp
.venv/Scripts/cambc.exe run .arena/dbg "$OPP" --map "$MAP" -o .arena/dbg/out 2>&1 | grep -E ' error|wins|  [AB] |Unfinished|rror' || true
f=$(ls -t .arena/dbg/out/*.repl26a | head -1)
unzip -p "$f" team-a.stdout | strings | grep -E "^T|bot error|^ERR" > .arena/dbg/log.txt || true
echo "log: .arena/dbg/log.txt ($(wc -l < .arena/dbg/log.txt) lines)"
