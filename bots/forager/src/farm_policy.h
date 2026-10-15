#pragma once
#include <algorithm>

// Engine-independent policy: distances and estimates are supplied by the adapter.
namespace farming {
struct Camp {
    float travel = 0, clear = 0, damage = 0, value = 0, bank_distance = 0;
    float claim_cost = 0, risk = 0;
    int ready_in = 0;
    bool reachable = true, covered = false;
};
inline float score(const Camp& c, float health_budget, float delivery_weight) {
    if (!c.reachable || c.covered || c.damage > health_budget || c.value <= 0) return -1;
    // Travel overlaps the respawn timer; only the remaining wait is charged.
    const float cost = std::max(c.travel, float(c.ready_in)) + c.clear + 5
        + delivery_weight * c.bank_distance + c.claim_cost + c.risk;
    return c.value / std::max(1.0f, cost);
}
inline bool bank(int gems, int distance, int threshold, int nearby,
                 bool winning, bool deadline, bool capturing) {
    if (gems <= 0 || distance >= 65535) return false;
    if (winning || deadline) return true;
    // Finish an existing capture, unless already next to an altar.
    if (capturing && distance > 1) return false;
    return gems >= threshold || distance <= nearby;
}
}
