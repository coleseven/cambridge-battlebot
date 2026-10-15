// v1 champion logic: farm the best camp group (value per round, with a damage model), capture it,
// bank at altars, shop on spawn, use abilities, and pick fights only when they are favourable.
//
// Fuel notes (measured): cambc::champions() ~43k, map::camp_groups() ~20k, vision::camp_groups() ~28k,
// a full distance field ~6k, most scalar queries < 1k. So the map model is cached once and
// per-group distance fields are built lazily with leftover fuel.
#include <cambc.h>
#include "bot.h"
#include "params.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <variant>
#include <vector>

namespace c = cambc;
using c::team;
using c::vec2;

#ifndef BOT_DEBUG
#define BOT_DEBUG 0
#endif

namespace {

// One line per champion turn when compiled with -DBOT_DEBUG=1 (stdout lands in the replay bundle).
std::string g_why;
const char* g_stage = "";
void why(const char* s) { if (BOT_DEBUG) g_why = s; }

constexpr std::uint16_t UNREACH = c::distance_field::unreachable;
constexpr int CAPTURE_ROUNDS = 5;

struct MonsterStats { float hp, str; };
MonsterStats monster_stats(c::camp_type t) {
    switch (t) {
    case c::camp_type::wolf: return {1350, 20};
    case c::camp_type::elk: return {2400, 60};
    case c::camp_type::gem_cluster: return {500, 0};
    case c::camp_type::gold_cluster: return {500, 0};
    case c::camp_type::monolith: return {20000, 200};
    }
    return {1000, 20};
}

struct Group {
    std::vector<vec2> tiles;       // camp spawn tiles
    std::vector<MonsterStats> mons;
    float hp_total = 0, dmg_full = 0;
    int gems = 0, gold = 0, respawn = 0;
    vec2 anchor{0, 0};
    c::bitboard tile_bb;
    std::unique_ptr<c::distance_field> field;  // static-terrain distance to the group's tiles
    int ready_round = 0;           // round from which the group is expected alive again
    bool reward_known = false;     // seen dead with reward available
    int home_d = 0, enemy_d = 0;   // path distance from our / enemy spawn
};

struct World {
    bool ready = false;
    int w = 0, h = 0;
    c::bitboard terrain;           // static impassable for walking
    c::bitboard own_spawn, enemy_spawn, altars_bb, camp_tiles;
    std::vector<vec2> altars;
    std::vector<Group> groups;
    std::unique_ptr<c::distance_field> altar_field, home_field, enemy_field;
    std::size_t next_field = 0;
    int body = 0, bodies = 1;
    c::champion_type type = c::champion_type::ryth;
    bool ranged = false;
    // Per-body runtime state.
    int target = -1;
    vec2 seen_enemy{-100, -100};
    int seen_round = -1000;
    vec2 last_pos{-1, -1};
    int stuck = 0;  // consecutive turns a step_along made no progress
    int shards_bought = 0, emblem_shards = 0;
};
World W;
// Camp-group handles from the one-off map::camp_groups() call (~20k fuel), same order as W.groups.
std::vector<c::map::camp_group> HANDLES;

std::unique_ptr<c::distance_field> make_field(const c::bitboard& roots) {
    auto f = std::make_unique<c::distance_field>();
    c::bitboard visited;
    // Camp tiles are almost always occupied, so route around every group except the target's own.
    f->compute((CAMP_WALLS ? (W.terrain | W.camp_tiles) : W.terrain) & ~roots, roots, visited);
    return f;
}

bool in_bounds(vec2 p);
// Field distance at p; on a tile the field treats as a wall (e.g. standing on a camp tile), use the
// best neighbour + 1.
int fget(const c::distance_field* f, vec2 p) {
    if (!f) return UNREACH;
    const int v = f->get(p);
    if (v != UNREACH) return v;
    int best = UNREACH;
    for (int dx = -1; dx <= 1; ++dx)
        for (int dy = -1; dy <= 1; ++dy) {
            vec2 q{p.x + dx, p.y + dy};
            if ((dx || dy) && in_bounds(q)) best = std::min(best, static_cast<int>(f->get(q)));
        }
    return best == UNREACH ? UNREACH : best + 1;
}

int chebyshev(vec2 a, vec2 b) { return static_cast<int>(a.chebyshev_distance(b)); }

bool is_caster(c::champion_type t) {
    using T = c::champion_type;
    return t == T::cassi || t == T::stellar || t == T::prism || t == T::tavi || t == T::ren;
}

// Slot of an ability that stuns an enemy champion (interrupts tributes and recalls), or -1.
int stun_slot(c::champion_type t) {
    using T = c::champion_type;
    switch (t) {
    case T::cassi: return 1;
    case T::ryth: return 0;
    case T::prism: return 0;
    case T::tavi: return 1;
    case T::ren: return 0;
    default: return -1;
    }
}

void init_world() {
    auto& me = c::self();
    W.w = c::map::width();
    W.h = c::map::height();
    W.type = me.champion_name();
    W.ranged = me.attack_range() > 1;
    W.own_spawn = c::map::spawn_region(team::ally);
    W.enemy_spawn = c::map::spawn_region(team::enemy);
    W.altars_bb = c::map::altars();
    W.altars = W.altars_bb.positions();
    W.terrain = c::map::walls() | c::map::holes() | W.altars_bb | W.enemy_spawn;

    // Body index from draft memory: [0xB7, n, type...].
    const auto& dm = c::draft_memory;
    if (dm.size() >= 2 && dm[0] == std::byte{0xB7}) {
        W.bodies = std::to_integer<int>(dm[1]);
        for (int i = 0; i < W.bodies && 2 + i < static_cast<int>(dm.size()); ++i)
            if (std::to_integer<int>(dm[2 + i]) == static_cast<int>(W.type)) W.body = i;
    } else {
        W.body = static_cast<int>(W.type) % 6;
        W.bodies = 6;
    }
    W.body = std::min(W.body, 5);


    HANDLES = c::map::camp_groups();
    W.camp_tiles.clear();
    for (const auto& g : HANDLES)
        for (const auto& m : g.camps()) W.camp_tiles.set(m.spawn_position(), true);
    W.home_field = make_field(W.own_spawn);
    W.enemy_field = make_field(W.enemy_spawn);
    W.altar_field = make_field(W.altars_bb);
    for (const auto& g : HANDLES) {
        Group G;
        G.gems = static_cast<int>(g.gem_value());
        G.gold = static_cast<int>(g.gold_value());
        G.respawn = g.max_respawn_timer();
        G.tile_bb.clear();
        for (const auto& m : g.camps()) {
            G.tiles.push_back(m.spawn_position());
            G.tile_bb.set(m.spawn_position(), true);
            G.mons.push_back(monster_stats(m.kind()));
        }
        // Damage taken while killing members one at a time (weakest-first), at 1 dps unit:
        // dmg_full = sum_i str_i * (cumulative hp up to i). Scaled by 1/dps later.
        auto mons = G.mons;
        std::sort(mons.begin(), mons.end(), [](auto a, auto b) { return a.hp < b.hp; });
        float cum = 0;
        for (auto m : mons) { cum += m.hp; G.hp_total += m.hp; }
        cum = 0;
        for (std::size_t i = 0; i < mons.size(); ++i) {
            // Every monster still alive hits for its strength while monster i is being killed.
            float alive_str = 0;
            for (std::size_t j = i; j < mons.size(); ++j) alive_str += mons[j].str;
            G.dmg_full += alive_str * mons[i].hp;
        }
        G.anchor = G.tiles.empty() ? vec2{0, 0} : G.tiles[0];
        G.home_d = fget(W.home_field.get(), G.anchor);
        G.enemy_d = fget(W.enemy_field.get(), G.anchor);
        W.groups.push_back(std::move(G));
    }
    W.ready = true;
}

// Build per-group fields with spare fuel, nearest groups (from our spawn) first.
void build_fields(std::uint64_t reserve) {
    static std::vector<int> order;
    if (order.empty()) {
        for (int i = 0; i < static_cast<int>(W.groups.size()); ++i) order.push_back(i);
        std::sort(order.begin(), order.end(), [](int a, int b) { return W.groups[a].home_d < W.groups[b].home_d; });
    }
    while (W.next_field < order.size() && c::fuel_remaining() > reserve) {
        auto& g = W.groups[order[W.next_field++]];
        g.field = make_field(g.tile_bb);
    }
}

int group_dist(const Group& g, vec2 p) {
    if (g.field) return fget(g.field.get(), p);
    return chebyshev(p, g.anchor) * 13 / 10 + 2;  // estimate until the field exists
}

// ---------- shared memory: one claim byte per body (group index + 1) ----------
std::array<std::uint8_t, 8> read_shared() {
    std::array<std::uint8_t, 8> b{};
    c::shared_memory.read(0, std::span{b});
    return b;
}
void write_claim(int group) {
    if (group == -2) group = KEEP_CLAIM ? W.target : -1;
    std::uint8_t v = group < 0 ? 0 : static_cast<std::uint8_t>(std::min(group + 1, 255));
    c::shared_memory.write(static_cast<std::size_t>(W.body), std::span{&v, 1});
}

// ---------- movement ----------
c::bitboard dynamic_blocked(vec2 me) {
    auto b = W.terrain | c::vision::champion_tiles() | c::vision::camp_tiles() | c::vision::teleport_tiles();
    b.set(me, false);
    return b;
}

constexpr std::array<vec2, 8> DIRS{vec2{0, 1}, vec2{1, 1}, vec2{1, 0}, vec2{1, -1},
                                   vec2{0, -1}, vec2{-1, -1}, vec2{-1, 0}, vec2{-1, 1}};

bool in_bounds(vec2 p) { return p.x >= 0 && p.y >= 0 && p.x < W.w && p.y < W.h; }

// Try a dash (2 tiles) that makes good progress along `f`.
bool try_dash(const c::distance_field& f, vec2 me, const c::bitboard& blocked) {
    auto& self = c::self();
    for (const auto& ab : self.abilities()) {
        if (!ab.info() || !ab.is_dash()) continue;
        if (!ab.is_ready() || !self.can_cast()) return false;
        const int here = fget(&f, me);
        vec2 best = me;
        int best_d = here;
        for (int dx = -2; dx <= 2; ++dx)
            for (int dy = -2; dy <= 2; ++dy) {
                if (std::max(std::abs(dx), std::abs(dy)) != 2) continue;
                vec2 p{me.x + dx, me.y + dy};
                if (!in_bounds(p) || blocked.is_set(p)) continue;
                int d = f.get(p);
                if (d < best_d) { best_d = d; best = p; }
            }
        if (best_d > here - 2) return false;
        try { return ab.use(best); } catch (const std::exception&) { return false; }
    }
    return false;
}

// Step to reduce field distance; returns true if moved. Allows sideways steps when stuck.
bool step_along(const c::distance_field& f, bool allow_dash = true) {
    auto& self = c::self();
    if (!self.can_move()) return false;
    const vec2 me = self.position();
    const int here = fget(&f, me);
    if (here == 0 || here == UNREACH) return false;
    auto blocked = dynamic_blocked(me);
    if (allow_dash && here >= 4) try_dash(f, me, blocked);
    const vec2 cur = self.position();
    const int cur_d = fget(&f, cur);
    std::array<std::pair<int, vec2>, 8> cand{};
    int n = 0;
    for (auto d : DIRS) {
        vec2 p{cur.x + d.x, cur.y + d.y};
        if (!in_bounds(p) || blocked.is_set(p)) continue;
        int v = f.get(p);
        if (v == UNREACH || v > cur_d) continue;
        cand[n++] = {v, p};
    }
    std::sort(cand.begin(), cand.begin() + n, [](auto a, auto b) { return a.first < b.first; });
    for (int i = 0; i < n; ++i) {
        if (cand[i].first == cur_d && i > 0) break;  // only one sideways attempt
        try {
            if (self.move_absolute(cand[i].second)) { W.stuck = 0; return true; }
        } catch (const std::exception&) {}
    }
    if (cur != me) { W.stuck = 0; return true; }
    // Jammed (usually by allies): after two blocked turns take any free tile, rotating by body index.
    if (++W.stuck >= 2) {
        for (int k = 0; k < 8; ++k) {
            const auto d = DIRS[(k + W.body * 3 + W.stuck) % 8];
            vec2 p{cur.x + d.x, cur.y + d.y};
            if (!in_bounds(p) || blocked.is_set(p) || f.get(p) == UNREACH) continue;
            try { if (self.move_absolute(p)) { W.stuck = 0; return true; } } catch (const std::exception&) {}
        }
    }
    return false;
}

// Step to increase distance from a point (flee), preferring tiles closer to home.
bool step_away(vec2 threat) {
    auto& self = c::self();
    if (!self.can_move()) return false;
    const vec2 me = self.position();
    auto blocked = dynamic_blocked(me);
    vec2 best = me;
    int best_score = chebyshev(me, threat) * 10 - fget(W.home_field.get(), me);
    for (auto d : DIRS) {
        vec2 p{me.x + d.x, me.y + d.y};
        if (!in_bounds(p) || blocked.is_set(p)) continue;
        int s = chebyshev(p, threat) * 10 - fget(W.home_field.get(), p);
        if (s > best_score) { best_score = s; best = p; }
    }
    if (best == me) return false;
    try { return self.move_absolute(best); } catch (const std::exception&) { return false; }
}

// ---------- shopping ----------
c::item_type main_shard() {
    using I = c::item_type;
    return is_caster(W.type) ? I::power_shard_of_focus : I::power_shard_of_strength;
}
c::item_type emblem_shard() {
    using I = c::item_type;
    const bool foc = is_caster(W.type);
    if (FIRST_EMBLEM == 1) return foc ? I::jungle_shard_of_focus : I::jungle_shard_of_strength;
    if (FIRST_EMBLEM == 2) return foc ? I::monster_shard_of_focus : I::monster_shard_of_strength;
    return main_shard();
}

void shop() {
    auto& me = c::self();
    for (int guard = 0; guard < 20; ++guard) {
        const auto item = (FIRST_EMBLEM != 0 && W.emblem_shards < 5) ? emblem_shard() : main_shard();
        if (me.gold_count() < item.cost()) return;
        try {
            if (!me.can_buy(item, 1)) return;
            me.buy(item, 1);
        } catch (const std::exception&) { return; }
        if (FIRST_EMBLEM != 0 && W.emblem_shards < 5) ++W.emblem_shards; else ++W.shards_bought;
    }
}

// ---------- abilities ----------
// Cast a damage/aoe/cc spell on a camp in range (range 4). Returns true if cast.
bool cast_on_camps(const std::vector<c::camp>& camps, vec2 me, bool walked) {
    auto& self = c::self();
    if (!self.can_cast() || camps.empty()) return false;
    for (const auto& ab : self.abilities()) {
        if (!ab.info()) continue;
        const bool dmg = ab.is_damage(), aoe = ab.is_aoe_damage(), cc = ab.is_cc();
        if (!(dmg || aoe || cc)) continue;
        if (walked) continue;  // offensive spells exclude walking for every kit we draft
        if (!ab.is_ready()) continue;
        // Choose target: aoe -> camp with most neighbours; dmg -> lowest hp; cc -> strongest.
        const c::camp* best = nullptr;
        float best_v = -1e9f;
        for (const auto& m : camps) {
            const int d = chebyshev(me, m.spawn_position());
            if (d > 4) continue;
            try { if (m.health() <= 0) continue; } catch (const std::exception&) { continue; }
            float v;
            if (aoe) {
                v = 0;
                for (const auto& o : camps) if (chebyshev(o.spawn_position(), m.spawn_position()) <= 1) v += 1;
            } else if (cc) {
                v = m.strength();
                if (v <= 0) continue;
            } else {
                v = -m.health();
            }
            if (v > best_v) { best_v = v; best = &m; }
        }
        if (!best) continue;
        try {
            if (aoe && ab.can_target_ground()) { if (ab.use(best->spawn_position())) return true; }
            else if (ab.use(*best)) return true;
        } catch (const std::exception&) {}
    }
    return false;
}

bool use_slot_kind(bool want_reset, bool want_shield) {
    auto& self = c::self();
    if (!self.can_cast()) return false;
    for (const auto& ab : self.abilities()) {
        if (!ab.info()) continue;
        if ((want_reset && ab.is_attack_reset()) || (want_shield && ab.is_shield())) {
            if (!ab.is_ready()) return false;
            try { return ab.use(); } catch (const std::exception&) { return false; }
        }
    }
    return false;
}

// ---------- combat vs camps ----------
// Attack the best camp in attack range; prefer members of `prefer` group, lowest health first.
bool attack_camps(const std::vector<c::camp>& camps, vec2 me, int prefer) {
    auto& self = c::self();
    if (!self.can_attack()) return false;
    const int range = self.attack_range();
    const c::camp* best = nullptr;
    float best_v = 1e18f;
    for (const auto& m : camps) {
        if (chebyshev(me, m.spawn_position()) > range) continue;
        float v;
        try { v = m.health(); } catch (const std::exception&) { continue; }  // killed earlier this turn
        if (v <= 0) continue;
        if (prefer >= 0 && W.groups[prefer].tile_bb.is_set(m.spawn_position())) v -= 1e9f;
        if (v < best_v) { best_v = v; best = &m; }
    }
    if (!best) return false;
    try { self.attack(*best); } catch (const std::exception&) { return false; }
    // Attack reset: hit again if the target is still standing.
    try {
        if (best->is_visible() && best->health() > 0 && use_slot_kind(true, false) && self.can_attack()) self.attack(*best);
    } catch (const std::exception&) {}
    return true;
}

// ---------- target choice ----------
float my_dps() {
    auto& me = c::self();
    float d = me.strength() * (1.0f + 1.0f / 8);  // basic + attack reset every 8
    if (is_caster(W.type)) d += me.focus() * 2.0f / 8;
    if (W.type == c::champion_type::varg) d *= 1.3f;  // splash on packed groups
    return std::max(d, 10.0f);
}

int squad_of(int b) { return b % std::max(1, SQUADS); }

int choose_target(vec2 pos, int round, float hp, const std::array<std::uint8_t, 8>& claims) {
    const int my_squad = squad_of(W.body);
    int k = 0;
    for (int b = 0; b < W.bodies; ++b) if (squad_of(b) == my_squad) ++k;
    // Follow the lowest-index squad-mate that already has a target.
    for (int b = 0; b < W.body && b < 8; ++b)
        if (squad_of(b) == my_squad && claims[b] > 0 && claims[b] <= W.groups.size()) {
            const int t = claims[b] - 1;
            if (W.groups[t].ready_round <= round + 3) return t;  // ignore a lead target I know is respawning
        }

    const float dps = my_dps();
    const float dmg_mult = (FIRST_EMBLEM == 1 && W.emblem_shards >= 5) ? 0.7f : 1.0f;
    const float squad_dps = dps * (1.0f + SQUAD_DPS_SHARE * (k - 1));
    const float tank_budget = SAFE_DAMAGE_FRAC * hp * (1.0f + 0.5f * (k - 1));
    int best = -1, best_any = -1;
    float best_s = 0, best_any_s = 0, cur_s = -1;
    for (int i = 0; i < static_cast<int>(W.groups.size()); ++i) {
        auto& g = W.groups[i];
        const int d = group_dist(g, pos);
        if (d >= UNREACH) continue;
        int n_other = 0;
        for (int b = 0; b < W.bodies && b < 8; ++b)
            if (squad_of(b) != my_squad && claims[b] == i + 1) ++n_other;
        const float kill = g.reward_known ? 0.0f : g.hp_total / squad_dps;
        const float dmg = g.reward_known ? 0.0f : g.dmg_full / squad_dps * dmg_mult;
        const float wait = std::max(0, g.ready_round - (round + d));
        const float side = std::max(0, g.home_d - g.enemy_d) * ENEMY_SIDE_WEIGHT;
        const float cost = d + wait + kill + CAPTURE_ROUNDS + CLAIM_PENALTY * n_other + side;
        const float value = g.gems + GOLD_WEIGHT * g.gold;
        const float s = value / std::max(cost, 1.0f);
        if (s > best_any_s) { best_any_s = s; best_any = i; }
        if (dmg > tank_budget) continue;
        if (i == W.target) cur_s = s;
        if (s > best_s) { best_s = s; best = i; }
    }
    if (best < 0) return best_any;
    const bool cur_respawning = W.target >= 0 && W.groups[W.target].ready_round > round + 3;
    if (W.target >= 0 && !cur_respawning && cur_s > 0 && best_s < cur_s * SWITCH_MARGIN) return W.target;
    return best;
}

void refresh_group_state(int i, int round) {
    auto& g = W.groups[i];
    try {
        auto st = HANDLES[i].state();
        if (std::holds_alternative<c::map::camp_group_alive>(st)) {
            g.reward_known = false;
            g.ready_round = std::min(g.ready_round, round);
        } else if (std::holds_alternative<c::map::camp_group_reward_available>(st)) {
            g.reward_known = true;
            g.ready_round = round;
        } else {
            g.reward_known = false;
            g.ready_round = round + std::get<c::map::camp_group_respawning>(st).timer;
        }
    } catch (const std::exception&) {}
}
} // namespace

void play_turn_inner() {
    auto& me = c::self();
    const int round = static_cast<int>(c::round_number());
    if (!W.ready) init_world();
    const auto start_fuel = c::fuel_remaining();
    (void)start_fuel;

    if (!me.is_alive()) {
        shop();
        write_claim(-2);
        why("dead");
        build_fields(15000);
        return;
    }
    if (me.is_recalling() || me.is_submitting_gems() || me.is_teleporting()) {
        why("channel");
        build_fields(15000);
        return;
    }

    const vec2 pos = me.position();
    const float hp = me.health(), maxhp = me.max_health();
    const int gems = static_cast<int>(me.gem_count());
    const bool at_spawn = W.own_spawn.is_set(pos);
    if (at_spawn) shop();

    g_stage = "observe";
    auto claims = read_shared();
    const auto visible = c::vision::visible_tiles();

    // Exact state for nearby visible groups (cheap enough for a handful).
    for (int i = 0; i < static_cast<int>(W.groups.size()); ++i) {
        auto& g = W.groups[i];
        if (chebyshev(pos, g.anchor) > 8) continue;
        if ((g.tile_bb & visible).is_empty()) continue;
        refresh_group_state(i, round);
    }

    g_stage = "enemies";
    // ---- enemies ----
    std::vector<c::champion> enemies;
    if (!c::vision::champion_tiles(team::enemy).is_empty()) enemies = c::vision::champions(team::enemy);
    const c::champion* nearest_enemy = nullptr;
    int nearest_d = 99;
    for (const auto& e : enemies) {
        if (!e.is_alive()) continue;
        int d = chebyshev(pos, e.position());
        if (d < nearest_d) { nearest_d = d; nearest_enemy = &e; }
    }

    g_stage = "interrupt";
    // Interrupt enemy tributes / recalls with a stun.
    if (!enemies.empty() && me.can_cast()) {
        const int ss = stun_slot(W.type);
        if (ss >= 0) {
            for (const auto& e : enemies) {
                if (!e.is_alive() || chebyshev(pos, e.position()) > 4) continue;
                if (!(e.is_submitting_gems() || e.is_recalling())) continue;
                auto abs = me.abilities();
                if (ss < static_cast<int>(abs.size()) && abs[ss].info() && abs[ss].is_ready()) {
                    try { if (abs[ss].use(e)) break; } catch (const std::exception&) {}
                }
            }
        }
    }

    g_stage = "fight";
    // Fight when not outnumbered nearby (or the target is killable); flee only when losing.
    if (!enemies.empty()) {
        c::bitboard ball;
        ball.clear();
        ball.draw_chebyshev(pos, 4);
        const int allies_near = static_cast<int>((c::vision::champion_tiles(team::ally) & ball).count());  // includes me
        const int enemies_near = static_cast<int>((c::vision::champion_tiles(team::enemy) & ball).count());
        const c::champion* tgt = nullptr;
        float tgt_hp = 1e9f;
        int tgt_d = 99;
        for (const auto& e : enemies) {
            try {
                if (!e.is_visible() || !e.is_alive()) continue;
                const int d = chebyshev(pos, e.position());
                const float eh = e.health();
                if (d <= 4 && eh < tgt_hp) { tgt = &e; tgt_hp = eh; tgt_d = d; }
            } catch (const std::exception&) {}
        }
        if (tgt) W.seen_enemy = tgt->position(), W.seen_round = round;
        const float my_frac = hp / maxhp;
        const float burst = me.strength() * 2 + (is_caster(W.type) ? me.focus() * 2 : 0);
        const bool killable = tgt && tgt_hp <= burst;
        const bool outnumbered = enemies_near > allies_near + FIGHT_SLACK;
        const bool engage = tgt && (killable || (!outnumbered && my_frac >= FIGHT_MIN_HP));
        if (engage) {
            why("fight");
            const auto& e = *tgt;
            // Melee closes small gaps before swinging.
            if (!W.ranged && tgt_d > 1 && tgt_d <= 2 && me.can_move()) {
                auto blocked = dynamic_blocked(pos);
                for (auto d : DIRS) {
                    vec2 p{pos.x + d.x, pos.y + d.y};
                    if (!in_bounds(p) || blocked.is_set(p) || chebyshev(p, e.position()) > 1) continue;
                    try { if (me.move_absolute(p)) break; } catch (const std::exception&) {}
                }
            }
            const vec2 now = me.position();
            const bool walked = now != pos;
            if (!walked && me.can_cast()) {
                for (const auto& ab : me.abilities()) {
                    if (!ab.info() || !(ab.is_damage() || ab.is_cc() || ab.is_aoe_damage())) continue;
                    if (!ab.can_target_enemies() || !ab.is_ready()) continue;
                    try {
                        if (chebyshev(now, e.position()) > 4) break;
                        if (ab.is_aoe_damage() ? ab.use(e.position()) : ab.use(e)) break;
                    } catch (const std::exception&) {}
                }
            }
            try {
                if (e.is_visible() && chebyshev(now, e.position()) <= me.attack_range() && me.can_attack()) {
                    me.attack(e);
                    if (e.is_visible() && e.health() > 0 && use_slot_kind(true, false) && me.can_attack()) me.attack(e);
                }
            } catch (const std::exception&) {}
            if (me.can_attack() || me.position() != pos || !W.ranged) {
                // Did something (or melee will keep farming): fall through only if nothing happened.
                write_claim(W.target);
                build_fields(15000);
                return;
            }
        } else if (tgt && (outnumbered || my_frac < FIGHT_MIN_HP) && (gems >= FLEE_GEMS || my_frac < 0.5f)) {
            if (gems > 0 && fget(W.altar_field.get(), pos) <= 1) {
                for (auto a : W.altars)
                    if (chebyshev(pos, a) <= c::constants::submit_gems_range) {
                        try { me.submit_gems(a); write_claim(W.target); return; } catch (const std::exception&) {}
                    }
            }
            why("flee");
            step_away(tgt->position());
            write_claim(W.target);
            build_fields(15000);
            return;
        }
    }

    g_stage = "lowhp";
    // ---- low health ----
    if (!at_spawn && hp < maxhp * RECALL_HP_FRAC) {
        const int home_d = fget(W.home_field.get(), pos);
        // Bank first if an altar is adjacent.
        if (gems > 0 && fget(W.altar_field.get(), pos) <= 1) {
            for (auto a : W.altars)
                if (chebyshev(pos, a) <= c::constants::submit_gems_range) {
                    try { me.submit_gems(a); return; } catch (const std::exception&) {}
                }
        }
        // Camps within 3 tiles keep hitting us and cancel a recall; step out first.
        const auto camp_near = c::vision::camp_tiles();
        bool hot = nearest_d <= 5;
        vec2 threat = nearest_enemy ? nearest_enemy->position() : pos;
        for (auto p : camp_near.positions())
            if (chebyshev(pos, p) <= 3) { hot = true; threat = p; break; }
        why("lowhp");
        write_claim(-2);
        if (home_d <= WALK_HOME_DIST || hot) {
            if (hot && threat != pos) step_away(threat);
            else step_along(*W.home_field);
        } else {
            try { me.recall(); } catch (const std::exception&) {}
        }
        build_fields(15000);
        return;
    }

    g_stage = "bank";
    // ---- banking ----
    const int altar_d = fget(W.altar_field.get(), pos);
    const int team_total = static_cast<int>(c::global_submitted_gems(team::ally));
    const bool win_now = gems > 0 && team_total + gems >= static_cast<int>(c::constants::winning_gems);
    const bool late = gems > 0 && round + altar_d + gems / 10 + 6 >= static_cast<int>(c::constants::max_rounds);
    if (gems > 0 && (gems >= BANK_MIN_GEMS || win_now || late || (gems >= BANK_NEAR_MIN && altar_d <= BANK_NEAR_DIST))) {
        // Capture in progress on an altar-adjacent tile: tribute now (capture resolves first).
        if (altar_d <= c::constants::submit_gems_range) {
            for (auto a : W.altars)
                if (chebyshev(pos, a) <= c::constants::submit_gems_range) {
                    try { me.submit_gems(a); write_claim(W.target); return; } catch (const std::exception& ex) { if (BOT_DEBUG) std::cout << "ERR submit " << ex.what() << std::endl; }
                }
        }
        // Don't abandon a capture that is about to land unless winning now.
        bool capturing = false;
        if (W.target >= 0 && W.groups[W.target].tile_bb.is_set(pos) && W.groups[W.target].reward_known) capturing = true;
        if (!capturing || win_now) {
            why("bank");
            step_along(*W.altar_field);
            write_claim(W.target);
            build_fields(15000);
            return;
        }
    }

    // ---- shopping trip ----
    if (!at_spawn && gems == 0 && me.gold_count() >= SHOP_RECALL_GOLD && nearest_d > 6) {
        const int home_d = fget(W.home_field.get(), pos);
        write_claim(-2);
        why("shoptrip");
        if (home_d <= WALK_HOME_DIST) step_along(*W.home_field);
        else { try { me.recall(); } catch (const std::exception&) {} }
        build_fields(15000);
        return;
    }
    // Stay on spawn until healed (spawn heals fully at turn end).
    if (at_spawn && hp < maxhp * 0.95f) { write_claim(-2); build_fields(15000); return; }

    g_stage = "farm";
    // ---- farming ----
    W.target = choose_target(pos, round, hp, claims);
    write_claim(W.target);
    if (W.target < 0) { why("notarget"); build_fields(15000); return; }
    auto& g = W.groups[W.target];
    if (!g.field) g.field = make_field(g.tile_bb);

    // Only ever hit the target group, or harmless clusters: waking anything else can be lethal.
    std::vector<c::camp> targets;
    for (const auto& m : c::vision::camps())
        if (g.tile_bb.is_set(m.spawn_position()) || m.strength() <= 0) targets.push_back(m);

    // On a dead group's tile: hold the capture; hit anything harmless in range meanwhile.
    if (g.reward_known && g.tile_bb.is_set(pos)) {
        why("capture");
        attack_camps(targets, pos, -1);
        build_fields(15000);
        return;
    }

    // Never park on the tiles of a group that is respawning: occupied tiles delay its respawn.
    if (!g.reward_known && g.ready_round > round && g.tile_bb.is_set(pos)) {
        why("unblock");
        auto blocked = dynamic_blocked(pos) | g.tile_bb;
        for (auto d : DIRS) {
            vec2 p{pos.x + d.x, pos.y + d.y};
            if (!in_bounds(p) || blocked.is_set(p)) continue;
            try { if (me.move_absolute(p)) break; } catch (const std::exception&) {}
        }
        build_fields(15000);
        return;
    }
    // Waiting for a respawn: hold one step outside the tiles.
    if (!g.reward_known && g.ready_round > round && fget(g.field.get(), pos) <= 1) {
        why("wait");
        attack_camps(targets, pos, -1);
        build_fields(15000);
        return;
    }

    // Melee may step and then attack; ranged attacks from where it stands once in range.
    bool in_range = false;
    for (const auto& m : targets)
        if (g.tile_bb.is_set(m.spawn_position()) && chebyshev(pos, m.spawn_position()) <= me.attack_range()) { in_range = true; break; }
    why(in_range ? "hit" : "go");
    if (!in_range) step_along(*g.field);  // a dead group's field leads onto a free spawn tile
    const vec2 now = me.position();
    const bool walked = now != pos;
    g_stage = "cast";
    cast_on_camps(targets, now, walked);
    g_stage = "attack";
    attack_camps(targets, now, W.target);
    // Varg-style self shield while trading with camps.
    if (hp < maxhp * 0.85f) use_slot_kind(false, true);
    build_fields(15000);
}

void Bot::play_turn() {
    const auto f0 = c::fuel_remaining();
    g_why = "-";
    g_stage = "start";
    try {
        play_turn_inner();
    } catch (const std::exception& e) {
        if (BOT_DEBUG) std::cout << "ERR stage=" << g_stage << " " << e.what() << std::endl;
    }
    if (BOT_DEBUG) {
        auto& me = c::self();
        const auto r = c::round_number();
        if (r % 5 == 0 || r < 30) {
            vec2 p = me.is_alive() ? me.position() : vec2{-1, -1};
            std::cout << "T r" << r << " " << static_cast<int>(W.type) << " @" << p.x << "," << p.y << " hp" << static_cast<int>(me.health())
                      << " g" << me.gem_count() << " $" << me.gold_count() << " tgt" << W.target << " " << g_why
                      << " fuel" << (f0 - c::fuel_remaining()) << std::endl;
        }
    }
}
