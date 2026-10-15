#include "../bots/forager/src/farm_policy.h"
#include <cassert>
#include <cmath>
int main() {
    farming::Camp camp;
    camp.travel = 10; camp.clear = 5; camp.value = 24;
    const float baseline = farming::score(camp, 100, 0.5f);
    camp.ready_in = 8;
    assert(farming::score(camp, 100, 0.5f) == baseline); // respawns before arrival
    camp.ready_in = 20;
    assert(farming::score(camp, 100, 0.5f) < baseline);
    camp.ready_in = 0; camp.bank_distance = 30;
    assert(farming::score(camp, 100, 0.5f) < baseline);
    camp.bank_distance = 0; camp.claim_cost = 25;
    assert(farming::score(camp, 100, 0.5f) < baseline);
    camp.claim_cost = 0; camp.damage = 101;
    assert(farming::score(camp, 100, 0.5f) < 0); // no unsafe fallback
    camp.damage = 0; camp.covered = true;
    assert(farming::score(camp, 100, 0.5f) < 0);
    camp.covered = false; camp.reachable = false;
    assert(farming::score(camp, 100, 0.5f) < 0);
    assert(!farming::bank(0, 1, 24, 3, true, true, false));
    assert(!farming::bank(24, 65535, 24, 3, false, false, false));
    assert(!farming::bank(24, 5, 24, 3, false, false, true));
    assert(farming::bank(24, 1, 24, 3, false, false, true));
    assert(farming::bank(10, 5, 24, 3, true, false, true));
    assert(farming::bank(10, 5, 24, 3, false, true, true));
    assert(farming::bank(1, 2, 24, 3, false, false, false));
    assert(!farming::bank(1, 5, 24, 3, false, false, false));
}
