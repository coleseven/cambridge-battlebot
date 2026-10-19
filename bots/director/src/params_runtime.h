#pragma once
// Runtime parameter overrides. This stub does nothing; tools/team_bot.py --runtime-params <space.json> replaces
// it in a generated tree with a loader that reads a 'PRMS' block from the genome blob into the named params
// (which it turns from constexpr into inline variables), so training can try CMA-ES candidates without
// rebuilding (tools/real_train.py --cma).
inline void params_runtime_load() {}
