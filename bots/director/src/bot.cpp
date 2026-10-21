// Director: one order per body per turn (what and where); the trained fighter executes contact (how).
//
// Each turn: observe (cheap: bitboards, cached fields, a few group states) -> choose an Order (heal, bank,
// intercept, join, shop, farm / capture / wait) -> if an enemy is within ENGAGE_RANGE, the fighter takes the
// turn steering by the order (its objective = the order's goal field; capture and farm orders leash it and let
// it hit the group's awake monsters), else the order's scripted executor walks, clears, captures, banks.
//
// Built from the forager's farming (bots/forager/src/bot.cpp, Astra; its tuned numbers are the defaults in
// params.h) minus its combat, flee and fight-selector code, which the networks replace (docs/DIRECTOR.md).
//
// Fuel (measured on the forager): map::camp_groups() ~20k (once), a full distance field ~6k (more on big
// maps), vision::champions() a few k. Group fields are built lazily with spare fuel, never in contact turns.
#include <cambc.h>
#include "bot.h"
#include "director.h"
#include "fighter.h"
#include "genome.h"
#include "params.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <memory>
#include <span>
#include <variant>
#include <vector>

namespace c = cambc;
using c::team;
using c::vec2;
using director::Mode;

#ifndef BOT_DEBUG
#define BOT_DEBUG 0
#endif

namespace {

constexpr std::uint16_t UNREACH = c::distance_field::unreachable;
constexpr int CAPTURE_ROUNDS = 5;
constexpr int CAMP_REACH = 5;   // camps hit up to 5 tiles away (20 Oct patch: elk/golem 4, monolith 5)
const char* g_stage = "";   // last stage reached this turn (fuel probe)
std::uint64_t g_pre = 0;     // fuel left when the fighter started (fuel probe)

struct MonsterStats { float hp, str; };
MonsterStats monster_stats(c::camp_type t) {
    switch (t) {
    case c::camp_type::wolf: return {1350, 20};
    case c::camp_type::elk: return {2400, 60};
    case c::camp_type::gem_cluster: return {120, 0};      // 20 Oct patch: 500 -> 120 HP
    case c::camp_type::gold_cluster: return {120, 0};
    case c::camp_type::monolith: return {20000, 200};
    case c::camp_type::golem: return {5000, 120};         // new camp in the 20 Oct patch
    }
    return {1000, 20};
}

struct Group {
    std::vector<vec2> tiles;
    float hp_total = 0, dmg_full = 0;
    int gems = 0, gold = 0;
    vec2 anchor{0, 0};
    c::bitboard tile_bb;
    std::unique_ptr<c::distance_field> field;   // static-terrain distance to the group's tiles
    int ready_round = 0;                        // round from which the group is expected alive again
    bool reward_known = false;                  // seen dead with the reward available (capturable)
    int home_d = 0, enemy_d = 0;
};

struct World {
    bool ready = false;
    int w = 0, h = 0;
    c::bitboard terrain, own_spawn, enemy_spawn, altars_bb;
    std::vector<vec2> altars;
    std::vector<Group> groups;
    std::unique_ptr<c::distance_field> altar_field, home_field;
    std::size_t next_field = 0;
    int body = 0, bodies = 1;
    c::champion_type type = c::champion_type::ryth;
    bool ranged = false;
    int target = -1;
    int stuck = 0;
    int emblem_shards = 0;
    // Comms: the latest fight location an ally reported (bytes 6..7) and the round we saw it.
    vec2 fight{-1, -1};
    int fight_round = -1000;
    std::unique_ptr<c::distance_field> fight_field;
    vec2 fight_field_at{-1, -1};
};
World W;
// Live camp tiles for the fighter's objective (groups alive or capturable as far as we know); FighterOrder::live.
c::bitboard LIVE;
std::vector<c::map::camp_group> HANDLES;

int chebyshev(vec2 a, vec2 b) { return static_cast<int>(a.chebyshev_distance(b)); }
bool in_bounds(vec2 p) { return p.x >= 0 && p.y >= 0 && p.x < W.w && p.y < W.h; }

std::unique_ptr<c::distance_field> make_field(const c::bitboard& roots) {
    auto f = std::make_unique<c::distance_field>();
    c::bitboard visited;
    f->compute(W.terrain & ~roots, roots, visited);
    return f;
}

// Field distance at p; on a tile the field treats as a wall (e.g. a camp tile), the best neighbour + 1.
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
    // Body index from draft memory: [0xB7, n, type...] (draft.cpp).
    const auto& dm = c::draft_memory;
    if (dm.size() >= 2 && dm[0] == std::byte{0xB7}) {
        W.bodies = std::min(6, std::to_integer<int>(dm[1]));
        for (int i = 0; i < W.bodies && 2 + i < static_cast<int>(dm.size()); ++i)
            if (std::to_integer<int>(dm[2 + i]) == static_cast<int>(W.type)) W.body = i;
    } else {
        W.body = static_cast<int>(W.type) % 6;
        W.bodies = 6;
    }
    HANDLES = c::map::camp_groups();
    W.home_field = make_field(W.own_spawn);
    auto enemy_field = make_field(W.enemy_spawn);
    W.altar_field = make_field(W.altars_bb);
    for (const auto& g : HANDLES) {
        Group G;
        G.gems = static_cast<int>(g.gem_value());
        G.gold = static_cast<int>(g.gold_value());
        std::vector<MonsterStats> mons;
        for (const auto& m : g.camps()) {
            G.tiles.push_back(m.spawn_position());
            G.tile_bb.set(m.spawn_position(), true);
            mons.push_back(monster_stats(m.kind()));
        }
        // Damage taken while killing members weakest-first at 1 dps: every monster still alive hits while
        // monster i is being killed (scaled by 1/dps later).
        std::sort(mons.begin(), mons.end(), [](auto a, auto b) { return a.hp < b.hp; });
        for (std::size_t i = 0; i < mons.size(); ++i) {
            G.hp_total += mons[i].hp;
            float alive_str = 0;
            for (std::size_t j = i; j < mons.size(); ++j) alive_str += mons[j].str;
            G.dmg_full += alive_str * mons[i].hp;
        }
        G.anchor = G.tiles.empty() ? vec2{0, 0} : G.tiles[0];
        G.home_d = fget(W.home_field.get(), G.anchor);
        G.enemy_d = fget(enemy_field.get(), G.anchor);
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
    // A fixed number per turn (FIELDS_PER_TURN), so play never depends on how much fuel the rest of the turn
    // used: with "build while fuel lasts" any extra work (e.g. the learned director) changed which camp
    // distances were exact and so the targets (20 Oct). The fuel test is only an emergency brake (the most
    // expensive field so far +50% must fit above the reserve), since running out suspends the bot into its next turn.
    static std::uint64_t max_cost = 12000;
    int built = 0;
    while (W.next_field < order.size() && built++ < FIELDS_PER_TURN &&
           c::fuel_remaining() > reserve + max_cost + max_cost / 2) {
        const auto f0 = c::fuel_remaining();
        auto& g = W.groups[order[W.next_field++]];
        if (!g.field) g.field = make_field(g.tile_bb);
        max_cost = std::max(max_cost, f0 - c::fuel_remaining());
    }
}

int group_dist(const Group& g, vec2 p) {
    if (g.field) return fget(g.field.get(), p);
    return chebyshev(p, g.anchor) * 13 / 10 + 2;   // estimate until the field exists
}

// ---------- comms: 8 shared bytes ----------
// byte b (0..5): body b's claim = target group + 1 (0 = none, 1..127), bit 7 = "in a fight this turn".
// bytes 6..7: x, y of the latest fight a body reported (written with the bit-7 flag).
struct Comms {
    std::array<std::uint8_t, 8> b{};
    int claim(int body) const { return b[body] & 0x7F; }
    bool fighting(int body) const { return (b[body] & 0x80) != 0; }
};
Comms read_comms(int round) {
    Comms m;
    c::shared_memory.read(0, std::span{m.b});
    for (int i = 0; i < W.bodies && i < 6; ++i)
        if (i != W.body && m.fighting(i)) {
            const vec2 p{m.b[6], m.b[7]};
            if (in_bounds(p)) { W.fight = p; W.fight_round = round; }
        }
    return m;
}
void write_comms(int claim_group, bool fighting, vec2 fight_at) {
    std::uint8_t v = claim_group >= 0 && claim_group < 127 ? static_cast<std::uint8_t>(claim_group + 1) : 0;
    if (fighting) v |= 0x80;
    c::shared_memory.write(static_cast<std::size_t>(W.body), std::span{&v, 1});
    if (fighting && in_bounds(fight_at)) {
        const std::array<std::uint8_t, 2> xy{static_cast<std::uint8_t>(fight_at.x), static_cast<std::uint8_t>(fight_at.y)};
        c::shared_memory.write(6, std::span{xy});
    }
}

// ---------- movement ----------
c::bitboard dynamic_blocked(vec2 me) {
    auto b = W.terrain | c::vision::champion_tiles() | c::vision::camp_tiles() | c::vision::teleport_tiles();
    b.set(me, false);
    return b;
}

constexpr std::array<vec2, 8> DIRS{vec2{0, 1}, vec2{1, 1}, vec2{1, 0}, vec2{1, -1},
                                   vec2{0, -1}, vec2{-1, -1}, vec2{-1, 0}, vec2{-1, 1}};

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
        if (cand[i].first == cur_d && i > 0) break;
        try { if (self.move_absolute(cand[i].second)) { W.stuck = 0; return true; } } catch (const std::exception&) {}
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
    return is_caster(W.type) ? c::item_type::power_shard_of_focus : c::item_type::power_shard_of_strength;
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
        const bool emblem = FIRST_EMBLEM != 0 && W.emblem_shards < 5;
        const auto item = emblem ? emblem_shard() : main_shard();
        if (me.gold_count() < item.cost()) return;
        try {
            if (!me.can_buy(item, 1)) return;
            me.buy(item, 1);
        } catch (const std::exception&) { return; }
        if (emblem) ++W.emblem_shards;
    }
}

// ---------- camps ----------
bool cast_on_camps(const std::vector<c::camp>& camps, vec2 me, bool walked) {
    auto& self = c::self();
    if (walked || !self.can_cast() || camps.empty()) return false;   // offensive spells exclude walking
    for (const auto& ab : self.abilities()) {
        if (!ab.info()) continue;
        const bool dmg = ab.is_damage(), aoe = ab.is_aoe_damage(), cc = ab.is_cc();
        if (!(dmg || aoe || cc) || !ab.is_ready()) continue;
        const c::camp* best = nullptr;
        float best_v = -1e9f;
        for (const auto& m : camps) {
            if (chebyshev(me, m.spawn_position()) > 4) continue;
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
        try { v = m.health(); } catch (const std::exception&) { continue; }
        if (v <= 0) continue;
        if (prefer >= 0 && W.groups[prefer].tile_bb.is_set(m.spawn_position())) v -= 1e9f;
        if (v < best_v) { best_v = v; best = &m; }
    }
    if (!best) return false;
    try { self.attack(*best); } catch (const std::exception&) { return false; }
    try {
        if (best->is_visible() && best->health() > 0 && use_slot_kind(true, false) && self.can_attack()) self.attack(*best);
    } catch (const std::exception&) {}
    return true;
}

// ---------- target choice (the forager's baseline scorer, plus the comms fight cost) ----------
float my_dps() {
    auto& me = c::self();
    float d = me.strength() * (1.0f + 1.0f / 8);
    if (is_caster(W.type)) d += me.focus() * 2.0f / 8;
    if (W.type == c::champion_type::varg) d *= 1.3f;
    return std::max(d, 10.0f);
}

int squad_of(int b) { return b % std::max(1, SQUADS); }

int choose_target(vec2 pos, int round, float hp, const Comms& cm) {
    const int my_squad = squad_of(W.body);
    int k = 0;
    for (int b = 0; b < W.bodies; ++b) if (squad_of(b) == my_squad) ++k;
    // Follow the lowest-index squad-mate that already has a target (unless it is respawning).
    for (int b = 0; b < W.body && b < 6; ++b) {
        const int cl = cm.claim(b);
        if (squad_of(b) == my_squad && cl > 0 && cl <= static_cast<int>(W.groups.size()))
            if (W.groups[cl - 1].ready_round <= round + 3) return cl - 1;
    }
    const float dps = my_dps();
    const float dmg_mult = (FIRST_EMBLEM == 1 && W.emblem_shards >= 5) ? 0.7f : 1.0f;
    const float squad_dps = dps * (1.0f + SQUAD_DPS_SHARE * (k - 1));
    const float tank_budget = SAFE_DAMAGE_FRAC * hp * (1.0f + 0.5f * (k - 1));
    const bool fight_fresh = SIGHT_COST > 0 && round - W.fight_round <= 10;
    int best = -1, best_any = -1;
    float best_s = 0, best_any_s = 0, cur_s = -1;
    for (int i = 0; i < static_cast<int>(W.groups.size()); ++i) {
        auto& g = W.groups[i];
        const int d = group_dist(g, pos);
        if (d >= UNREACH) continue;
        int n_other = 0;
        for (int b = 0; b < W.bodies && b < 6; ++b)
            if (squad_of(b) != my_squad && cm.claim(b) == i + 1) ++n_other;
        const float kill = g.reward_known ? 0.0f : g.hp_total / squad_dps;
        const float dmg = g.reward_known ? 0.0f : g.dmg_full / squad_dps * dmg_mult;
        const float wait = std::max(0, g.ready_round - (round + d));
        const float side = std::max(0, g.home_d - g.enemy_d) * ENEMY_SIDE_WEIGHT;
        const float sight = fight_fresh && chebyshev(g.anchor, W.fight) <= SIGHT_RADIUS ? SIGHT_COST : 0.0f;
        const float cost = d + wait + kill + CAPTURE_ROUNDS + CLAIM_PENALTY * n_other + side + sight;
        const float s = (g.gems + GOLD_WEIGHT * g.gold) / std::max(cost, 1.0f);
        if (s > best_any_s) { best_any_s = s; best_any = i; }
        if (dmg > tank_budget) continue;
        if (i == W.target) cur_s = s;
        if (s > best_s) { best_s = s; best = i; }
    }
    // Nothing is safe to clear: take nothing (the none order heals / recalls), never the best-value group anyway.
    // 19 Oct ladder: the forager's fallback (best_any) sent hurt bodies one by one at contest's monoliths
    // (the map's only gem camps): 40-64 camp deaths and 0-400 gems a game. NO_SAFE_FALLBACK = 0 restores it.
    if (best < 0) return NO_SAFE_FALLBACK ? -1 : best_any;
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

bool bank_rule(int gems, int distance, bool winning, bool deadline, bool capturing) {
    if (gems <= 0 || distance >= UNREACH) return false;
    if (winning || deadline) return true;
    if (capturing && distance > 1) return false;   // finish the capture first unless already at an altar
    return gems >= BANK_MIN_GEMS || distance <= BANK_NEAR_DIST;
}

bool try_tribute(vec2 pos) {
    auto& me = c::self();
    for (auto a : W.altars)
        if (chebyshev(pos, a) <= c::constants::submit_gems_range) {
            try { me.submit_gems(a); return true; } catch (const std::exception&) {}
        }
    return false;
}

// One order for this body this turn.
struct Order {
    Mode mode = Mode::none;
    int group = -1;                               // farm / capture / wait
    const c::distance_field* goal = nullptr;      // where the order leads (fighter objective in contact)
    vec2 point{-1, -1};                           // join / intercept: where the action is
};

// Fighter order for contact turns (order.h): the goal steers its objective feature; farm and capture
// orders leash it to the group and let it hit the group's awake monsters.
FighterOrder fighter_order(const Order& o) {
    FighterOrder f;
    f.near_camps_only = true;
    f.live = &LIVE;
    if (ORDER_GOAL == 1 || (ORDER_GOAL == 2 && (o.mode == Mode::heal || o.mode == Mode::bank || o.mode == Mode::shop)))
        f.goal = o.goal;
    if (o.group >= 0 && W.groups[o.group].field) {
        const auto& g = W.groups[o.group];
        if (FIGHT_CAMPS) { f.has_camps = true; f.camps = g.tile_bb; }
        const int leash = o.mode == Mode::capture ? CAPTURE_LEASH : FARM_LEASH;
        if (leash >= 0) { f.leash_field = g.field.get(); f.leash = leash; }
    }
    return f;
}

// The forager's legacy fight (control for the networks): stun an enemy channel within 4, then fight the
// weakest enemy within 4 (melee closes a 2-tile gap; spells when standing; attack + attack reset).
// true = acted (the turn is spent).
bool scripted_fight(const std::vector<c::champion>& enemies, vec2 pos) {
    auto& me = c::self();
    if (me.can_cast()) {
        const int ss = stun_slot(W.type);
        if (ss >= 0)
            for (const auto& e : enemies) {
                try {
                    if (!e.is_alive() || chebyshev(pos, e.position()) > 4) continue;
                    if (!(e.is_submitting_gems() || e.is_recalling())) continue;
                    auto abs = me.abilities();
                    if (ss < static_cast<int>(abs.size()) && abs[ss].info() && abs[ss].is_ready() && abs[ss].use(e)) break;
                } catch (const std::exception&) {}
            }
    }
    const c::champion* tgt = nullptr;
    float tgt_hp = 1e9f;
    int tgt_d = 99;
    for (const auto& e : enemies) {
        try {
            if (!e.is_visible() || !e.is_alive()) continue;
            const int d = chebyshev(pos, e.position());
            if (d <= 4 && e.health() < tgt_hp) { tgt = &e; tgt_hp = e.health(); tgt_d = d; }
        } catch (const std::exception&) {}
    }
    if (!tgt) return false;
    const auto& e = *tgt;
    if (!W.ranged && tgt_d > 1 && tgt_d <= 2 && me.can_move()) {
        auto blocked = dynamic_blocked(pos);
        for (auto d : DIRS) {
            vec2 p{pos.x + d.x, pos.y + d.y};
            if (!in_bounds(p) || blocked.is_set(p) || chebyshev(p, e.position()) > 1) continue;
            try { if (me.move_absolute(p)) break; } catch (const std::exception&) {}
        }
    }
    const vec2 now = me.position();
    if (now == pos && me.can_cast()) {
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
    // As the forager: the turn ends here unless a ranged body attacked from where it stood (then farming
    // continues, e.g. a cast on the camp).
    return me.can_attack() || me.position() != pos || !W.ranged;
}

// ---------- learned director (docs/DIRECTOR.md, "Learned director") ----------
// A NEAT network scores candidate orders from features; its program sits in the genome blob at word
// DIRN_BLOCK ('DIRN', input count, then the fighter's program format with hidden slots from NFD). Without the
// block (or with LEARNED_DIRECTOR = 0) the rule cascade decides, exactly as before. Feature 0 is "the rule
// cascade chose this", so a network weighting it alone plays exactly like the rules (the training warm start).
constexpr std::uint32_t DIRN_BLOCK = 15000;
constexpr std::uint32_t DIRN_MAGIC = 0x4E524944u;   // 'DIRN'
constexpr int NFD = 25;
constexpr int DN_MAX_NODES = 64;
struct DEval { int node, act; float bias, resp; int first, count; };
struct DNet { bool ok = false; std::vector<DEval> evals; std::vector<std::pair<int, float>> links; int out = 0; };
DNet DN;
bool DN_loaded = false;

float dword_float(std::uint32_t w) { float f; std::memcpy(&f, &w, 4); return f; }

void load_dnet() {
    DN_loaded = true;
    std::uint32_t o = DIRN_BLOCK;
    if (o + 3 >= GENOME_WORDS || GENOME_BLOB[o] != DIRN_MAGIC || GENOME_BLOB[o + 1] != static_cast<std::uint32_t>(NFD)) return;
    o += 2;
    const std::uint32_t n = GENOME_BLOB[o++];
    if (n == 0 || n > static_cast<std::uint32_t>(DN_MAX_NODES)) return;
    const int max_slot = NFD + DN_MAX_NODES;
    for (std::uint32_t i = 0; i < n; ++i) {
        if (o + 5 >= GENOME_WORDS) return;
        DEval e{};
        e.node = static_cast<int>(GENOME_BLOB[o++]);
        e.act = static_cast<int>(GENOME_BLOB[o++]);
        e.bias = dword_float(GENOME_BLOB[o++]);
        e.resp = dword_float(GENOME_BLOB[o++]);
        e.count = static_cast<int>(GENOME_BLOB[o++]);
        e.first = static_cast<int>(DN.links.size());
        if (e.node < NFD || e.node >= max_slot || e.count < 0 || e.count > 400) return;
        for (int k = 0; k < e.count; ++k) {
            if (o + 1 >= GENOME_WORDS) return;
            const int src = static_cast<int>(GENOME_BLOB[o++]);
            const float w = dword_float(GENOME_BLOB[o++]);
            if (src < 0 || src >= max_slot) return;
            DN.links.push_back({src, w});
        }
        DN.evals.push_back(e);
    }
    DN.out = static_cast<int>(GENOME_BLOB[o]);
    DN.ok = DN.out >= 0 && DN.out < max_slot;
}

float run_dnet(const float* x) {
    float v[NFD + DN_MAX_NODES];
    for (int i = 0; i < NFD; ++i) v[i] = x[i];
    for (int i = NFD; i < NFD + DN_MAX_NODES; ++i) v[i] = 0.0f;
    for (const auto& e : DN.evals) {
        float s = 0.0f;
        for (int k = 0; k < e.count; ++k) s += v[DN.links[e.first + k].first] * DN.links[e.first + k].second;
        float z = e.bias + e.resp * s;
        switch (e.act) {
        case 1: z = std::tanh(std::clamp(2.5f * z, -60.0f, 60.0f)); break;
        case 2: z = 1.0f / (1.0f + std::exp(-std::clamp(5.0f * z, -60.0f, 60.0f))); break;
        case 3: z = z > 0 ? z : 0.0f; break;
        default: break;
        }
        v[e.node] = z;
    }
    return v[DN.out];
}

// Farm features per group (the rule scorer's terms, same formula as choose_target).
struct GroupScore { int i; float s, d, value, wait, kill, dmg_frac; int claimed; };
std::vector<GroupScore> score_groups(vec2 pos, int round, float hp, const Comms& cm) {
    const int my_squad = squad_of(W.body);
    int k = 0;
    for (int b = 0; b < W.bodies; ++b) if (squad_of(b) == my_squad) ++k;
    const float dps = my_dps();
    const float dmg_mult = (FIRST_EMBLEM == 1 && W.emblem_shards >= 5) ? 0.7f : 1.0f;
    const float squad_dps = dps * (1.0f + SQUAD_DPS_SHARE * (k - 1));
    const float tank_budget = std::max(1.0f, SAFE_DAMAGE_FRAC * hp * (1.0f + 0.5f * (k - 1)));
    const bool fight_fresh = round - W.fight_round <= 10;
    std::vector<GroupScore> out;
    for (int i = 0; i < static_cast<int>(W.groups.size()); ++i) {
        const auto& g = W.groups[i];
        const int d = group_dist(g, pos);
        if (d >= UNREACH) continue;
        int n_other = 0;
        for (int b = 0; b < W.bodies && b < 6; ++b)
            if (b != W.body && cm.claim(b) == i + 1) ++n_other;
        const float kill = g.reward_known ? 0.0f : g.hp_total / squad_dps;
        const float dmg = g.reward_known ? 0.0f : g.dmg_full / squad_dps * dmg_mult;
        const float wait = static_cast<float>(std::max(0, g.ready_round - (round + d)));
        const float side = std::max(0, g.home_d - g.enemy_d) * ENEMY_SIDE_WEIGHT;
        const float sight = fight_fresh && SIGHT_COST > 0 && chebyshev(g.anchor, W.fight) <= SIGHT_RADIUS ? SIGHT_COST : 0.0f;
        const float value = g.gems + GOLD_WEIGHT * g.gold;
        const float cost = d + wait + kill + CAPTURE_ROUNDS + CLAIM_PENALTY * n_other + side + sight;
        out.push_back({i, value / std::max(cost, 1.0f), static_cast<float>(d), value, wait, kill,
                       std::min(3.0f, dmg / tank_budget), n_other});
    }
    return out;
}

const c::distance_field* point_field(vec2 p, int radius) {
    if (!W.fight_field || W.fight_field_at != p) {
        c::bitboard roots;
        roots.clear();
        roots.draw_chebyshev(p, radius);
        roots &= ~W.terrain;
        W.fight_field = make_field(roots);
        W.fight_field_at = p;
    }
    return W.fight_field.get();
}

} // namespace

void play_turn_inner() {
    auto& me = c::self();
    const int round = static_cast<int>(c::round_number());
    if (!W.ready) init_world();
    const Comms cm = read_comms(round);

    if (!me.is_alive()) {
        shop();
        write_comms(-1, false, {});
        build_fields(15000);
        return;
    }
    if (me.is_recalling() || me.is_submitting_gems() || me.is_teleporting()) {
        write_comms(-1, false, {});
        build_fields(15000);
        return;
    }

    const vec2 pos = me.position();
    const float hp = me.health(), maxhp = me.max_health();
    const int gems = static_cast<int>(me.gem_count());
    const bool at_spawn = W.own_spawn.is_set(pos);
    if (at_spawn) shop();

    g_stage = "observe";
    // ---- observe ----
    const auto visible = c::vision::visible_tiles();
    for (int i = 0; i < static_cast<int>(W.groups.size()); ++i) {
        auto& g = W.groups[i];
        if (chebyshev(pos, g.anchor) > 8 || (g.tile_bb & visible).is_empty()) continue;
        refresh_group_state(i, round);
    }
    const auto enemy_tiles = c::vision::champion_tiles(team::enemy);
    int nearest_d = 99;
    vec2 nearest_pos = pos;
    for (auto p : enemy_tiles.positions()) {
        const int d = chebyshev(pos, p);
        if (d < nearest_d) { nearest_d = d; nearest_pos = p; }
    }
    const bool contact = nearest_d <= ENGAGE_RANGE;

    g_stage = "order";
    // ---- choose the order ----
    Order order;
    const int altar_d = fget(W.altar_field.get(), pos);
    const int team_total = static_cast<int>(c::global_submitted_gems(team::ally));
    const bool win_now = gems > 0 && team_total + gems >= static_cast<int>(c::constants::winning_gems);
    const bool late = gems > 0 && round + altar_d + gems / 10 + 6 >= static_cast<int>(c::constants::max_rounds);
    const bool holding_capture = W.target >= 0 && W.groups[W.target].reward_known
        && W.groups[W.target].tile_bb.is_set(pos);

    // Intercept: a visible enemy tribute with a real bag, and I am the closest visible ally to it.
    const c::champion* tribute = nullptr;
    std::vector<c::champion> enemies;
    if (INTERCEPT_DIST > 0 && nearest_d <= INTERCEPT_DIST && !win_now && hp >= maxhp * RECALL_HP_FRAC) {
        enemies = c::vision::champions(team::enemy);
        int best_d = INTERCEPT_DIST + 1;
        for (const auto& e : enemies) {
            try {
                if (!e.is_alive() || !e.is_visible() || !e.is_submitting_gems()) continue;
                if (static_cast<int>(e.gem_count()) < INTERCEPT_MIN_BAG) continue;
                const int d = chebyshev(pos, e.position());
                if (d >= best_d) continue;
                bool elected = true;
                for (auto a : c::vision::champion_tiles(team::ally).positions())
                    if (a != pos && chebyshev(a, e.position()) < d) elected = false;
                if (elected) { tribute = &e; best_d = d; }
            } catch (const std::exception&) {}
        }
    }

    if (!at_spawn && hp < maxhp * RECALL_HP_FRAC) {
        order.mode = Mode::heal;
        order.goal = W.home_field.get();
    } else if (tribute) {
        order.mode = Mode::intercept;
        order.point = tribute->position();
    } else if (bank_rule(gems, altar_d, win_now, late, holding_capture) && (!holding_capture || win_now || altar_d <= 1)) {
        order.mode = Mode::bank;
        order.goal = W.altar_field.get();
    } else if (JOIN_DIST > 0 && !contact && round - W.fight_round <= 1 && gems <= JOIN_MAX_GEMS &&
               hp >= maxhp * JOIN_MIN_HP && !holding_capture &&
               chebyshev(pos, W.fight) * 13 / 10 <= JOIN_DIST) {
        order.mode = Mode::join;
        order.point = W.fight;
    } else if (!at_spawn && gems == 0 && me.gold_count() >= SHOP_RECALL_GOLD && nearest_d > 6) {
        order.mode = Mode::shop;
        order.goal = W.home_field.get();
    } else if (at_spawn && hp < maxhp * 0.95f && !contact) {
        order.mode = Mode::heal;   // stand on spawn until healed (heals fully at turn end)
        order.goal = W.home_field.get();
    } else {
        W.target = choose_target(pos, round, hp, cm);
        if (W.target >= 0) {
            auto& g = W.groups[W.target];
            if (!g.field && !contact) g.field = make_field(g.tile_bb);   // no field build in a fight turn
            order.group = W.target;
            order.goal = g.field.get();
            order.mode = g.reward_known && g.tile_bb.is_set(pos) ? Mode::capture
                       : (!g.reward_known && g.ready_round > round) ? Mode::wait : Mode::farm;
        }
    }
    // ---- learned director: re-rank the candidate orders (the rule choice is one of them, flagged) ----
    // Not in contact turns: scoring the candidates costs ~22k fuel (13k for ~20 groups, 9k for the features;
    // the network itself ~0.3k), and a fight turn already spends 60-85k in the fighter. There the rule order
    // stands (it mostly sets the fighter's leash). 21 Oct: re-ranking every turn put 0.8% of turns over 90k.
    if (LEARNED_DIRECTOR && !contact) {
        if (!DN_loaded) load_dnet();
        // The candidates and features are built with or without a network, so the rule-only build spends the
        // same fuel and differs from a network build only in the decision (fuel-dependent brakes elsewhere
        // would otherwise make a warm-start network play different games from the rules).
        {
            struct Cand { Mode mode; int group; float x[NFD]; };
            std::vector<Cand> cands;
            cands.reserve(16);            // no reallocation copies of the 100-byte entries
            const Mode rule_mode = (order.mode == Mode::capture || order.mode == Mode::wait) ? Mode::farm : order.mode;
            const int rule_group = order.group;
            float ctx[8];
            const int enemy_total = static_cast<int>(c::global_submitted_gems(team::enemy));
            ctx[0] = hp / std::max(1.0f, maxhp);
            ctx[1] = gems / 50.0f;
            ctx[2] = me.gold_count() / 100.0f;
            ctx[3] = std::min(altar_d, 60) / 30.0f;
            ctx[4] = std::min(fget(W.home_field.get(), pos), 60) / 30.0f;
            ctx[5] = round / 2000.0f;
            ctx[6] = (team_total - enemy_total) / 1000.0f;
            ctx[7] = std::min(nearest_d, 10) / 10.0f;
            auto add = [&](Mode m, int grp, const GroupScore* gs, float best_s) {
                Cand cd{m, grp, {}};
                float* x = cd.x;
                x[0] = (m == rule_mode && grp == rule_group) ? 1.0f : 0.0f;
                x[1] = m == Mode::farm; x[2] = m == Mode::bank; x[3] = m == Mode::heal; x[4] = m == Mode::shop;
                x[5] = m == Mode::join; x[6] = m == Mode::intercept; x[7] = m == Mode::none;
                if (gs) {
                    x[8] = best_s > 0 ? gs->s / best_s : 0.0f;
                    x[9] = std::min(gs->d, 60.0f) / 30.0f;
                    x[10] = gs->value / 50.0f;
                    x[11] = std::min(gs->wait, 60.0f) / 30.0f;
                    x[12] = gs->dmg_frac;
                    x[13] = static_cast<float>(gs->claimed);
                    x[14] = grp == W.target ? 1.0f : 0.0f;
                    x[15] = std::max(0, W.groups[grp].home_d - W.groups[grp].enemy_d) / 30.0f;
                    x[16] = (round - W.fight_round <= 10 && chebyshev(W.groups[grp].anchor, W.fight) <= SIGHT_RADIUS) ? 1.0f : 0.0f;
                }
                for (int i = 0; i < 8; ++i) x[17 + i] = ctx[i];
                cands.push_back(cd);
            };
            // Group scores are reused for SCORE_EVERY turns (~13k fuel to recompute; values and distances drift
            // slowly). The cache is per body (one instance per body).
            static std::vector<GroupScore> cached_scores;
            static int scored_round = -1000;
            if (round - scored_round >= SCORE_EVERY) { cached_scores = score_groups(pos, round, hp, cm); scored_round = round; }
            auto scores = cached_scores;
            std::sort(scores.begin(), scores.end(), [](const GroupScore& a, const GroupScore& b) { return a.s > b.s; });
            const float best_s = scores.empty() ? 0.0f : scores[0].s;
            for (std::size_t i = 0; i < scores.size(); ++i)
                if (i < 5 || scores[i].i == W.target || scores[i].i == rule_group) add(Mode::farm, scores[i].i, &scores[i], best_s);
            if (gems > 0) add(Mode::bank, -1, nullptr, 0);
            if (hp < maxhp * (at_spawn ? 0.95f : 1.0f)) add(Mode::heal, -1, nullptr, 0);
            if (!at_spawn && gems == 0 && me.gold_count() >= 30) add(Mode::shop, -1, nullptr, 0);
            if (round - W.fight_round <= 1 && in_bounds(W.fight) && !contact) add(Mode::join, -1, nullptr, 0);
            if (tribute) add(Mode::intercept, -1, nullptr, 0);
            add(Mode::none, -1, nullptr, 0);
            const Cand* best = nullptr;
            float best_v = 0;
            for (const auto& cd : cands) {
                const float v = DN.ok ? run_dnet(cd.x) : cd.x[0];      // no network: the rule choice
                if (!best || v > best_v) { best = &cd; best_v = v; }
            }
            if (best && !(best->mode == rule_mode && best->group == rule_group)) {
                Order o;
                o.mode = best->mode;
                switch (best->mode) {
                case Mode::farm: {
                    auto& g = W.groups[best->group];
                    if (!g.field && !contact) g.field = make_field(g.tile_bb);
                    W.target = best->group;
                    o.group = best->group;
                    o.goal = g.field.get();
                    o.mode = g.reward_known && g.tile_bb.is_set(pos) ? Mode::capture
                           : (!g.reward_known && g.ready_round > round) ? Mode::wait : Mode::farm;
                    if (!g.field) o.mode = Mode::none;          // a fight turn without the group's field yet
                    break;
                }
                case Mode::bank: o.goal = W.altar_field.get(); break;
                case Mode::heal: case Mode::shop: o.goal = W.home_field.get(); break;
                case Mode::join: o.point = W.fight; break;
                case Mode::intercept: o.point = tribute->position(); break;
                default: break;
                }
                order = o;
            }
        }
    }
    const int claim = order.group >= 0 ? order.group : -1;

    g_stage = "gate";
    // ---- contact: fight (the fighter executes the order), flee, or ignore and keep the order ----
    bool fight = contact;
    if (contact && CONTACT_GATE && order.mode != Mode::intercept) {
        c::bitboard ball;
        ball.clear();
        ball.draw_chebyshev(pos, 4);
        const int allies_near = static_cast<int>((c::vision::champion_tiles(team::ally) & ball).count());   // includes me
        const int enemies_near = static_cast<int>((enemy_tiles & ball).count());
        if (enemies.empty()) enemies = c::vision::champions(team::enemy);
        float weakest = 1e9f;
        for (const auto& e : enemies) {
            try {
                if (e.is_alive() && e.is_visible() && chebyshev(pos, e.position()) <= 4) weakest = std::min(weakest, e.health());
            } catch (const std::exception&) {}
        }
        const float my_frac = hp / std::max(1.0f, maxhp);
        const float burst = me.strength() * 2 + (is_caster(W.type) ? me.focus() * 2 : 0);
        const bool outnumbered = enemies_near > allies_near + FIGHT_SLACK;
        fight = weakest <= burst || (!outnumbered && my_frac >= FIGHT_MIN_HP);
        if (!fight && (outnumbered || my_frac < FIGHT_MIN_HP) && (gems >= FLEE_GEMS || my_frac < 0.5f)) {
            if (gems > 0 && altar_d <= 1 && try_tribute(pos)) { write_comms(claim, true, nearest_pos); return; }
            step_away(nearest_pos);
            write_comms(claim, true, nearest_pos);
            build_fields(15000);
            return;
        }
    }
    if (fight && NN_FIGHT) {
        LIVE.clear();
        for (const auto& g : W.groups) if (g.ready_round <= round) LIVE = LIVE | g.tile_bb;
    }
    if (fight) {
        if (order.mode == Mode::bank && gems > 0 && altar_d <= 1 && (win_now || late) && try_tribute(pos)) {
            write_comms(claim, true, nearest_pos);
            return;
        }
        if (!NN_FIGHT || !((NN_HEROES >> static_cast<int>(W.type)) & 1)) {
            if (enemies.empty()) enemies = c::vision::champions(team::enemy);
            if (scripted_fight(enemies, pos)) {
                write_comms(claim, true, nearest_pos);
                build_fields(15000);
                return;
            }
        } else if ((g_stage = "fighter", g_pre = c::fuel_remaining(), fighter::take_order(fighter_order(order)))) {
            write_comms(claim, true, nearest_pos);
            return;
        }
    }
    write_comms(claim, contact, nearest_pos);

    g_stage = "script";
    // ---- no contact (or the fighter passed): scripted executor ----
    switch (order.mode) {
    case Mode::heal: {
        if (at_spawn) break;
        if (gems > 0 && altar_d <= 1 && try_tribute(pos)) return;
        // Camps within 3 tiles keep hitting us and cancel a recall; step out first.
        bool hot = nearest_d <= 5;
        vec2 threat = nearest_d < 99 ? nearest_pos : pos;
        for (auto p : c::vision::camp_tiles().positions())
            if (chebyshev(pos, p) <= CAMP_REACH) { hot = true; threat = p; break; }
        if (fget(W.home_field.get(), pos) <= WALK_HOME_DIST || hot) {
            if (hot && threat != pos) step_away(threat);
            else step_along(*W.home_field);
        } else {
            try { me.recall(); } catch (const std::exception&) {}
        }
        break;
    }
    case Mode::intercept: {
        const int ss = stun_slot(W.type);
        const int d = chebyshev(pos, order.point);
        if (ss >= 0 && d <= 4 && me.can_cast()) {
            try {
                auto abs = me.abilities();
                if (ss < static_cast<int>(abs.size()) && abs[ss].info() && abs[ss].is_ready() && abs[ss].use(*tribute)) break;
            } catch (const std::exception&) {}
        }
        if (d <= me.attack_range() && me.can_attack()) {
            try { me.attack(*tribute); } catch (const std::exception&) {}
            break;
        }
        step_along(*point_field(order.point, 1));
        break;
    }
    case Mode::bank:
        if (altar_d <= c::constants::submit_gems_range && try_tribute(pos)) return;
        step_along(*W.altar_field);
        break;
    case Mode::join:
    case Mode::shop:
        if (order.mode == Mode::shop && fget(W.home_field.get(), pos) > WALK_HOME_DIST) {
            try { me.recall(); } catch (const std::exception&) {}
        } else {
            step_along(order.mode == Mode::join ? *point_field(order.point, 2) : *order.goal);
        }
        break;
    case Mode::none:
        if (gems > 0) step_along(*W.altar_field);
        else if (!at_spawn && hp < maxhp) {
            if (nearest_d > 6) { try { me.recall(); } catch (const std::exception&) {} }
            else step_along(*W.home_field);
        }
        break;
    case Mode::farm:
    case Mode::capture:
    case Mode::wait: {
        auto& g = W.groups[order.group];
        if (!g.field) g.field = make_field(g.tile_bb);
        // Only ever hit the target group, or harmless clusters: waking anything else can be lethal.
        std::vector<c::camp> targets;
        for (const auto& m : c::vision::camps())
            if (g.tile_bb.is_set(m.spawn_position()) || m.strength() <= 0) targets.push_back(m);
        if (order.mode == Mode::capture) { attack_camps(targets, pos, -1); break; }
        if (order.mode == Mode::wait) {
            if (g.tile_bb.is_set(pos)) {
                // Never park on a respawning group's tiles: occupied tiles delay its respawn.
                auto blocked = dynamic_blocked(pos) | g.tile_bb;
                for (auto d : DIRS) {
                    vec2 p{pos.x + d.x, pos.y + d.y};
                    if (!in_bounds(p) || blocked.is_set(p)) continue;
                    try { if (me.move_absolute(p)) break; } catch (const std::exception&) {}
                }
                break;
            }
            if (fget(g.field.get(), pos) <= 1) { attack_camps(targets, pos, -1); break; }
        }
        bool in_range = false;
        for (const auto& m : targets)
            if (g.tile_bb.is_set(m.spawn_position()) && chebyshev(pos, m.spawn_position()) <= me.attack_range()) { in_range = true; break; }
        if (!in_range) step_along(*g.field);   // a dead group's field leads onto a free spawn tile
        const vec2 now = me.position();
        cast_on_camps(targets, now, now != pos);
        attack_camps(targets, now, order.group);
        if (hp < maxhp * 0.85f) use_slot_kind(false, true);
        break;
    }
    }
    g_stage = "fields";
    if (!contact) {
        // (fighter::warm() here measured badly: the fighter's live-group set changes with what is in view, so
        //  it rebuilt its field nearly every quiet turn: p50 fuel 12k -> 27k. Left out.)
        build_fields(15000);
    }
}

void Bot::play_turn() {
    const auto f0 = c::fuel_remaining();
    g_pre = 0;
    try {
        play_turn_inner();
    } catch (const std::exception& e) {
        if (BOT_DEBUG) std::cout << "ERR " << e.what() << std::endl;
    }
    // Fuel probe (DEBUG_FUEL, default build only: release builds drop stdout): what a heavy turn was doing.
    if (DEBUG_FUEL && f0 - c::fuel_remaining() > static_cast<std::uint64_t>(DEBUG_FUEL))
        std::cout << "FUELHI r" << c::round_number() << " type" << static_cast<int>(W.type) << " stage" << g_stage
                  << " used" << (f0 - c::fuel_remaining()) << " director" << (g_pre ? f0 - g_pre : 0)
                  << " fighter" << (g_pre ? g_pre - c::fuel_remaining() : 0) << std::endl;
}
