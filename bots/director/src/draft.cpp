// Draft: fixed compositions (mirror picks are allowed, so there is no denial to play around). The draft
// memory records our body types in pick order so each body knows its index (shared-memory slot).
// tools/team_bot.py patches the `comp` line so training can pick a composition per game (genome blob).
#include <cambc.h>
#include "draft.h"
#include "params.h"
#include <algorithm>
#include <array>
#include <cstddef>
#include <vector>

using cambc::champion_draft;

namespace {
// Ladder meta (14 Oct): Varg, C&I, Stellar, Ren, Tavi/Ryth dominate the top 15 (same table as the forager).
constexpr std::array<std::array<champion_draft, 5>, 4> COMPS{{
    {champion_draft::varg, champion_draft::cassi_and_issac, champion_draft::stellar, champion_draft::ren, champion_draft::tavi},
    {champion_draft::cassi_and_issac, champion_draft::varg, champion_draft::stellar, champion_draft::ren, champion_draft::ryth},
    {champion_draft::varg, champion_draft::stellar, champion_draft::ren, champion_draft::ryth, champion_draft::vendetta},
    {champion_draft::cassi_and_issac, champion_draft::stellar, champion_draft::ren, champion_draft::varg, champion_draft::prism},
}};

// Physical body types created by a draft pick (C&I creates two bodies).
void push_bodies(champion_draft pick, std::vector<std::byte>& out) {
    using cambc::champion_type;
    auto t = [](champion_type c) { return static_cast<std::byte>(static_cast<int>(c)); };
    switch (pick) {
    case champion_draft::ryth: out.push_back(t(champion_type::ryth)); break;
    case champion_draft::vesper_vale: out.push_back(t(champion_type::vesper_vale)); break;
    case champion_draft::vendetta: out.push_back(t(champion_type::vendetta)); break;
    case champion_draft::cassi_and_issac:
        out.push_back(t(champion_type::cassi));
        out.push_back(t(champion_type::issac));
        break;
    case champion_draft::stellar: out.push_back(t(champion_type::stellar)); break;
    case champion_draft::prism: out.push_back(t(champion_type::prism)); break;
    case champion_draft::marcella: out.push_back(t(champion_type::marcella)); break;
    case champion_draft::willow: out.push_back(t(champion_type::willow)); break;
    case champion_draft::ren: out.push_back(t(champion_type::ren)); break;
    case champion_draft::varg: out.push_back(t(champion_type::varg)); break;
    case champion_draft::tavi: out.push_back(t(champion_type::tavi)); break;
    }
}
} // namespace

void play_draft() {
    const auto mine = cambc::draft::current_draft(cambc::team::ally);
    const auto& comp = COMPS[static_cast<std::size_t>(DRAFT_COMP) % COMPS.size()];

    champion_draft pick = comp[0];
    for (auto c : comp)
        if (std::find(mine.begin(), mine.end(), c) == mine.end()) { pick = c; break; }

    // Byte 0 = magic, byte 1 = body count, then body champion_type ids in pick order.
    std::vector<std::byte> bodies;
    for (auto c : mine) push_bodies(c, bodies);
    push_bodies(pick, bodies);
    std::vector<std::byte> mem{std::byte{0xB7}, static_cast<std::byte>(bodies.size())};
    mem.insert(mem.end(), bodies.begin(), bodies.end());
    cambc::draft_memory = mem;
    cambc::draft::pick(pick);
}
