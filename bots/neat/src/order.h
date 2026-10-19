#pragma once
// Order from the strategy layer (bots/director) to the fighter for one contact turn. The default order is
// "none": the fighter then plays exactly as in training (its own camp-tiles objective, harmless camps only,
// no leash), so standalone builds and the training arenas are unchanged.
#include <cambc.h>

struct FighterOrder {
    // Objective field override: the network's "progress toward objective" (feature 21) is measured on this
    // field instead of the fighter's own field to all live camp tiles. Null = the fighter's own field.
    const cambc::distance_field* goal = nullptr;
    // Monster camps the fighter may attack while awake (e.g. the group the body was clearing when the enemy
    // arrived), in addition to harmless clusters. They use the network's camp-attack option.
    bool has_camps = false;
    cambc::bitboard camps;
    // Leash: a move or dash must keep leash_field(dest) <= max(leash, leash_field(here)). leash 0 on a
    // capture group's field keeps the body on its spawn tiles (the capture timer survives the fight).
    const cambc::distance_field* leash_field = nullptr;
    int leash = 0;
    // Query only camps within attack range + 1 (the only ones an option can hit). Real maps can show a
    // dozen camps, each costing several host calls; the training arenas show a few, so training leaves it off.
    bool near_camps_only = false;
    // The strategy layer's set of live camp tiles (groups alive or capturable, as it tracks them). With it the
    // fighter skips its own per-turn group-state scan (~11k fuel on camp-dense maps) and rebuilds its
    // objective field only when this set changes.
    const cambc::bitboard* live = nullptr;
};
