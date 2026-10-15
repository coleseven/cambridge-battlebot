// Duel draft: the matchup is encoded in wall tiles by tools/duel_maps.py.
// Team A reads row 0, team B row 1; slot k uses x = 1+5k .. 4+5k as a 4-bit (champion_draft id + 1).
#include <cambc.h>
#include "draft.h"
#include <vector>

void play_draft() {
    const auto walls = cambc::map::walls();
    const auto spawn = cambc::map::spawn_region(cambc::team::ally).positions();
    const bool team_a = !spawn.empty() && spawn[0].y < cambc::map::height() / 2;
    const int row = team_a ? 0 : 1;
    const int slot = static_cast<int>(cambc::draft::current_draft(cambc::team::ally).size());
    int v = 0;
    for (int bit = 0; bit < 4; ++bit)
        if (walls.is_set(cambc::vec2{1 + 5 * slot + bit, row})) v |= 1 << bit;
    const int pick = (v >= 1 && v <= 11) ? v - 1 : static_cast<int>(cambc::champion_draft::varg);
    cambc::draft::pick(static_cast<cambc::champion_draft>(pick));
}
