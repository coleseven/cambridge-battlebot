#pragma once
// Director: the strategy layer that decides WHAT each body does (an order: farm a group, hold a capture,
// bank, heal, shop, join a fight, intercept a tribute) and WHERE; the trained fighter decides HOW whenever
// an enemy is close, steering by the order (order.h) instead of by its own objective. Design and evidence:
// docs/DIRECTOR.md, docs/STRATEGY_V2.md.
//
// This file also marks the source tree for tools/team_bot.py: a macro source with director.h keeps its own
// main.cpp and params (no forager combat hook is generated). Build the playable bot with
//   .venv/Scripts/python tools/team_bot.py --macro-src bots/director/src --source neat:real1 --out team-director --build
namespace director {
enum class Mode { none, farm, capture, wait, bank, heal, shop, join, intercept };
}
