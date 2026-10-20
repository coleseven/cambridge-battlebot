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
// 0-3: pre-patch ladder meta. 4-9: candidates after the 20 Oct patch (meta comp nerfed; Vesper +33% gold and
// Marcella 2 gold per tributed gem matter now that many games reach round 2,000 and lifetime gold breaks ties;
// Willow buffed; Heart of Ruin new). On 3- and 4-champion maps only the first picks are used.
using D = champion_draft;
constexpr std::array<std::array<champion_draft, 5>, 10> COMPS{{
    {D::varg, D::cassi_and_issac, D::stellar, D::ren, D::tavi},
    {D::cassi_and_issac, D::varg, D::stellar, D::ren, D::ryth},
    {D::varg, D::stellar, D::ren, D::ryth, D::vendetta},
    {D::cassi_and_issac, D::stellar, D::ren, D::varg, D::prism},
    {D::varg, D::cassi_and_issac, D::marcella, D::vesper_vale, D::willow},
    {D::cassi_and_issac, D::marcella, D::ryth, D::vesper_vale, D::tavi},
    {D::heart_of_ruin, D::cassi_and_issac, D::varg, D::willow, D::vesper_vale},
    {D::marcella, D::vesper_vale, D::willow, D::ryth, D::varg},
    {D::cassi_and_issac, D::varg, D::ren, D::willow, D::marcella},
    {D::vesper_vale, D::marcella, D::cassi_and_issac, D::stellar, D::heart_of_ruin},
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
    case champion_draft::heart_of_ruin: out.push_back(t(champion_type::heart_of_ruin)); break;
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
