// Director + trained fighter (generated tree from tools/team_bot.py; fighter_bot.cpp is added there).
#include <cambc.h>
#include <iostream>
#include "bot.h"
#include "draft.h"
#include "fighter.h"
#include "params_runtime.h"

int main() {
    params_runtime_load();   // training: CMA-ES candidate values from the genome blob (no-op otherwise)
    Bot bot;
    bool prepared = false;
    while (true) {
        try {
            if (cambc::is_draft()) {
                play_draft();
            } else {
                // The fighter's one-off setup on each body's first turn (2x fuel), never on an ordinary turn.
                if (!prepared) {
                    prepared = true;
                    try { fighter::prepare(); } catch (const std::exception&) {}
                }
                bot.play_turn();
            }
        } catch (const std::exception& error) {
            std::cout << "bot error: " << error.what() << std::endl;
        } catch (...) {
            std::cout << "bot error: unknown exception" << std::endl;
        }
        cambc::end_turn();
    }
}
