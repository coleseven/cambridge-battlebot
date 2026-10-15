#pragma once

// One instance per champion body; all state lives in bot.cpp statics (one WASM instance per body).
class Bot {
public:
    void play_turn();
};
