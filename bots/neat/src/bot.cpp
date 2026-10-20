// Duel policy: enumerate legal (move, spell, attack) options and score each with learned per-champion
// weights (weights.h). Only "where to go when no enemy is visible" is a fixed rule.
#include <cambc.h>
#include "bot.h"
#include "weights.h"
#include "genome.h"
#include "budget.h"
#include "order.h"
#include <cstring>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <memory>
#include <variant>
#include <vector>

#ifndef BOT_DEBUG
#define BOT_DEBUG 0
#endif

namespace c = cambc;
using c::team;
using c::vec2;

namespace {

constexpr std::uint16_t UNREACH = c::distance_field::unreachable;
constexpr std::array<vec2, 9> MOVES{vec2{0, 0}, vec2{0, 1}, vec2{1, 1}, vec2{1, 0}, vec2{1, -1},
                                    vec2{0, -1}, vec2{-1, -1}, vec2{-1, 0}, vec2{-1, 1}};

struct State {
    bool ready = false;
    bool team_a = false;
    int w = 0, h = 0, type = 0;
    bool ranged = false;
    c::bitboard terrain, own_spawn;
    std::unique_ptr<c::distance_field> home, enemy_home, objective, altar;
    c::bitboard camp_tiles;
    std::vector<c::map::camp_group> groups;
    std::vector<c::bitboard> group_tiles;
    std::vector<vec2> altars;
    c::bitboard objective_live;
    bool objective_ok = false;
    vec2 last_seen{-1, -1};
    int last_seen_round = -1000;
};
State S;
// The strategy layer's order for this turn (order.h); default = none, i.e. the training behaviour.
FighterOrder ORDER;

int cheb(vec2 a, vec2 b) { return static_cast<int>(a.chebyshev_distance(b)); }
bool inb(vec2 p) { return p.x >= 0 && p.y >= 0 && p.x < S.w && p.y < S.h; }
int fv(const c::distance_field& f, vec2 p) { const int v = f.get(p); return v == UNREACH ? 999 : v; }

std::unique_ptr<c::distance_field> field_to(const c::bitboard& roots) {
    auto f = std::make_unique<c::distance_field>();
    c::bitboard visited;
    f->compute(S.terrain & ~roots, roots, visited);
    return f;
}

// The champion record (position, health, strength, focus, range, state flags) in ONE host query. Every
// cambc::champion getter re-fetches this same record; reading it once gives identical values for less fuel.
// Returns false when the champion is not in vision (what champion::is_visible() checks via the same query).
bool read_record(const c::champion& ch, c::detail::champion_info& out) {
    const auto code = c::detail::sdk_champion(static_cast<std::uint32_t>(static_cast<c::handle_champion_t>(ch)), &out);
    if (code == 97) return false;   // api_not_in_vision
    c::detail::check(code);
    return true;
}

// ---- learned network (NEAT phenotype from the patchable genome block) ----
struct Eval { int node, act; float bias, resp; int first, count; };
struct Net {
    bool ok = false;
    std::vector<Eval> evals;
    std::vector<std::pair<int, float>> links;
    int out = 0;
    int base = NF_V1;   // first hidden slot: NF_V1 when the net reads no team feature (see load_net)
};
Net NET;
std::uint32_t RESIGN_ROUND = 0;

float word_float(std::uint32_t w) { float f; std::memcpy(&f, &w, 4); return f; }

void load_net(int type) {
    RESIGN_ROUND = GENOME_BLOB[4];
    if (GENOME_BLOB[0] != 0x4E454154u || GENOME_BLOB[1] != 0x424C4F42u || type < 0 || type > 11) return;
    std::uint32_t o = GENOME_BLOB[5 + type];
    if (o == 0 || o >= GENOME_WORDS) return;
    // Format 1 blobs were written for NF_V1 features: shift their hidden/output slots up to NF.
    const int base = GENOME_BLOB[3] == 1u ? NF_V1 : NF;
    auto remap = [base](int s) { return s >= base ? s - base + NF : s; };
    const std::uint32_t n = GENOME_BLOB[o++];
    if (n == 0 || n > static_cast<std::uint32_t>(MAX_NODES)) return;
    const int max_slot = NF + MAX_NODES;
    for (std::uint32_t i = 0; i < n; ++i) {
        if (o + 5 >= GENOME_WORDS) return;
        Eval e{};
        e.node = remap(static_cast<int>(GENOME_BLOB[o++]));
        e.act = static_cast<int>(GENOME_BLOB[o++]);
        e.bias = word_float(GENOME_BLOB[o++]);
        e.resp = word_float(GENOME_BLOB[o++]);
        e.count = static_cast<int>(GENOME_BLOB[o++]);
        e.first = static_cast<int>(NET.links.size());
        if (e.node < NF || e.node >= max_slot || e.count < 0 || NET.links.size() + e.count > static_cast<std::size_t>(MAX_LINKS)) return;
        for (int k = 0; k < e.count; ++k) {
            if (o + 1 >= GENOME_WORDS) return;
            const int src = remap(static_cast<int>(GENOME_BLOB[o++]));
            const float w = word_float(GENOME_BLOB[o++]);
            if (src < 0 || src >= max_slot) return;
            NET.links.push_back({src, w});
        }
        NET.evals.push_back(e);
    }
    NET.out = remap(static_cast<int>(GENOME_BLOB[o]));
    NET.ok = NET.out >= 0 && NET.out < max_slot;
    // A net that reads no team feature (every 1v1 net) runs in the duel layout (hidden slots from NF_V1),
    // so its per-option fuel, and hence the NN fuel cap's cut-off, is exactly as in the 25-feature bot.
    bool team_inputs = false;
    for (const auto& l : NET.links) team_inputs |= l.first >= NF_V1 && l.first < NF;
    if (NET.ok && !team_inputs) {
        auto down = [](int s) { return s >= NF ? s - (NF - NF_V1) : s; };
        for (auto& l : NET.links) l.first = down(l.first);
        for (auto& e : NET.evals) e.node = down(e.node);
        NET.out = down(NET.out);
        NET.base = NF_V1;
    }
}

template <int BASE>
float run_net(const float* x) {
    float v[BASE + MAX_NODES];
    for (int i = 0; i < BASE; ++i) v[i] = x[i];
    for (int i = BASE; i < BASE + MAX_NODES; ++i) v[i] = 0.0f;
    for (const auto& e : NET.evals) {
        float s = 0.0f;
        for (int k = 0; k < e.count; ++k) s += v[NET.links[e.first + k].first] * NET.links[e.first + k].second;
        float z = e.bias + e.resp * s;
        switch (e.act) {
        case 1: z = std::tanh(std::clamp(2.5f * z, -60.0f, 60.0f)); break;
        case 2: z = 1.0f / (1.0f + std::exp(-std::clamp(5.0f * z, -60.0f, 60.0f))); break;
        case 3: z = z > 0 ? z : 0.0f; break;
        default: break;
        }
        v[e.node] = z;
    }
    return v[NET.out];
}

void init() {
    auto& me = c::self();
    S.w = c::map::width();
    S.h = c::map::height();
    S.own_spawn = c::map::spawn_region(team::ally);
    const auto enemy_spawn = c::map::spawn_region(team::enemy);
    S.terrain = c::map::walls() | c::map::holes() | c::map::altars() | enemy_spawn;
    S.home = field_to(S.own_spawn);
    S.enemy_home = field_to(enemy_spawn);
    S.camp_tiles.clear();
    S.groups = c::map::camp_groups();
    for (const auto& g : S.groups) {
        c::bitboard t;
        t.clear();
        for (const auto& m : g.camps()) { S.camp_tiles.set(m.spawn_position(), true); t.set(m.spawn_position(), true); }
        S.group_tiles.push_back(t);
    }
    S.objective = S.camp_tiles.is_empty() ? field_to(enemy_spawn) : field_to(S.camp_tiles);
    S.altars = c::map::altars().positions();
    S.altar = field_to(c::map::altars());
    S.type = static_cast<int>(me.champion_name());
    S.ranged = me.attack_range() > 1;
    const auto own = S.own_spawn.positions();
    S.team_a = !own.empty() && own[0].y < S.h / 2;
    load_net(S.type);
    S.ready = true;
}

// Champion slots whose damage spell also stuns (the API reports these as plain damage).
bool stun_slot(int type, int slot) {
    using T = c::champion_type;
    const auto t = static_cast<T>(type);
    return (t == T::cassi && slot == 1) || (t == T::ryth && slot == 0) || (t == T::prism && slot == 0) ||
           (t == T::tavi && slot == 1) || (t == T::ren && slot == 0);
}

struct Foe {
    const c::champion* h;
    vec2 pos;
    float hp, maxhp, dps;
    int reach;
    bool stunned, channel;
};

struct Spell {
    int slot;
    bool offensive, aoe, stun, reset, shield, dash;
    float amount;
};

template <int NX>
struct OptT {
    vec2 dest;
    int spell = -1;  // index into spells
    int target = -1; // foe index for spell/attack
    bool atk = false;
    int camp = -1;   // camp index attacked instead of a foe
    bool bank = false;
    float score = -1e30f;
    std::array<float, NX> x{};
};

bool step_along(const c::distance_field& f, const c::bitboard& blocked) {
    auto& me = c::self();
    if (!me.can_move()) return false;
    const vec2 p = me.position();
    int best = fv(f, p);
    vec2 to = p;
    for (std::size_t i = 1; i < MOVES.size(); ++i) {
        vec2 q{p.x + MOVES[i].x, p.y + MOVES[i].y};
        if (!inb(q) || blocked.is_set(q)) continue;
        if (fv(f, q) < best) { best = fv(f, q); to = q; }
    }
    if (to == p) return false;
    try { return me.move_absolute(to); } catch (const std::exception&) { return false; }
}

#if BOT_FUEL
std::uint64_t FUEL_MAX = 0;
#endif

// Objective = tiles of groups that are alive or waiting to be captured; groups seen respawning are dropped.
// Rebuilt only when the set of live groups changes (a full field is ~6k fuel, far more on big maps); the
// cached field is identical, so games are unchanged. A strategy layer may also call this in quiet turns
// (fighter::warm), so the rebuild seldom lands on a fight turn.
void refresh_objective() {
    if (S.camp_tiles.is_empty()) return;
    const auto visible = c::vision::visible_tiles();
    c::bitboard live;
    live.clear();
    for (std::size_t i = 0; i < S.groups.size(); ++i) {
        bool keep = true;
        if (!(S.group_tiles[i] & visible).is_empty()) {
            try { keep = !std::holds_alternative<c::map::camp_group_respawning>(S.groups[i].state()); }
            catch (const std::exception&) {}
        }
        if (keep) live = live | S.group_tiles[i];
    }
    if (!S.objective_ok || !(live == S.objective_live)) {
        S.objective = field_to(live.is_empty() ? S.camp_tiles : live);
        S.objective_live = live;
        S.objective_ok = true;
    }
}

// Allies seen this turn (team path only).
struct Ally { vec2 pos; float frac; int range; };
std::vector<Ally> ALLIES;

// TEAM = the loaded network reads team features (NET.base == NF). Without it (every 1v1 network and
// the hand prior) the team features could not change any score, so they are not computed at all and
// this instantiation is the 25-feature duel code, instruction for instruction: the per-turn fuel, and
// therefore the fuel_remaining() floor that cuts the NN shortlist short, match the old bot exactly.
template <bool TEAM>
void play() {
    constexpr int NX = TEAM ? NF : NF_V1;
    using Opt = OptT<NX>;
    auto& me = c::self();
    if (RESIGN_ROUND && S.team_a && c::round_number() >= RESIGN_ROUND) c::resign();  // end the duel early
    if (!me.is_alive()) return;
    // One host query for our whole record instead of one per getter (each getter re-fetches it); same values.
    c::detail::champion_info my_inf{};
    read_record(me, my_inf);
    const auto my_state = c::champion_state::from_bits_retain(my_inf.state);
    if (my_state.contains(c::champion_state::recalling) || my_state.contains(c::champion_state::submitting_gems) ||
        my_state.contains(c::champion_state::teleporting)) return;

    const vec2 pos{my_inf.x, my_inf.y};
    const float my_hp = my_inf.health, my_max = std::max(1.0f, my_inf.max_health);
    const float my_frac = my_hp / my_max;
    const float my_str = my_inf.strength;
    const int my_range = my_inf.attack_range;
    const int round = static_cast<int>(c::round_number());

    auto blocked = S.terrain | c::vision::champion_tiles() | c::vision::camp_tiles();
    blocked.set(pos, false);

    // ---- visible enemies ----
    std::vector<c::champion> raw;
    if (!c::vision::champion_tiles(team::enemy).is_empty()) raw = c::vision::champions(team::enemy);
    std::vector<Foe> foes;
    for (const auto& e : raw) {
        try {
            // is_visible() == the record query succeeds with x, y >= 0; then read every field from that record.
            c::detail::champion_info inf{};
            if (!read_record(e, inf) || inf.x < 0 || inf.y < 0 || !e.is_alive()) continue;
            const auto st = c::champion_state::from_bits_retain(inf.state);
            Foe f{&e, vec2{inf.x, inf.y}, inf.health, std::max(1.0f, inf.max_health), inf.strength + 0.25f * inf.focus,
                  inf.attack_range + 1, e.buffs().has_stun(),
                  st.contains(c::champion_state::submitting_gems) || st.contains(c::champion_state::recalling)};
            if (f.stunned) f.dps *= 0.3f;
            foes.push_back(f);
        } catch (const std::exception&) {}
    }

    // ---- visible allies (other bodies of our team; empty in a 1v1 except for Cassi & Issac) ----
    const auto& allies = ALLIES;
    if constexpr (TEAM) {
        ALLIES.clear();
        auto tiles = c::vision::champion_tiles(team::ally);
        tiles.set(pos, false);
        if (!tiles.is_empty())
            for (const auto& e : c::vision::champions(team::ally)) {
                try {
                    if (!e.is_alive()) continue;
                    c::detail::champion_info inf{};                // one record query (same values as the getters);
                    if (!read_record(e, inf)) continue;            // the getter would have thrown: skip, as before
                    const vec2 ap{inf.x, inf.y};
                    if (ap == pos) continue;
                    ALLIES.push_back(Ally{ap, inf.health / std::max(1.0f, inf.max_health),
                                          static_cast<int>(inf.attack_range)});
                } catch (const std::exception&) {}
            }
    }

    // Harmless clusters in view (the contested objective).
    std::vector<c::camp> camps;
    bool skip_camps = false;
    if (ORDER.near_camps_only) {
        c::bitboard reach;
        reach.clear();
        reach.draw_chebyshev(pos, my_range + 1);
        skip_camps = (c::vision::camp_tiles() & reach).is_empty();
    }
    if (!skip_camps) for (const auto& m : c::vision::camps()) {
        try {
            if (ORDER.near_camps_only && cheb(pos, m.spawn_position()) > my_range + 1) continue;
            if (m.strength() <= 0 && m.health() > 0) camps.push_back(m);
            else if (ORDER.has_camps && ORDER.camps.is_set(m.spawn_position()) && m.is_awake() && m.health() > 0)
                camps.push_back(m);   // the ordered group, already fighting us
        } catch (const std::exception&) {}
    }
    const int gems = static_cast<int>(me.gem_count());
    // Objective = tiles of groups that are alive or waiting to be captured; drop groups seen respawning.
    // An ordered goal replaces it (and skips this work).
    if (!ORDER.goal && ORDER.live && !S.camp_tiles.is_empty()) {
        if (!S.objective_ok || !(*ORDER.live == S.objective_live)) {
            S.objective = field_to(ORDER.live->is_empty() ? S.camp_tiles : *ORDER.live);
            S.objective_live = *ORDER.live;
            S.objective_ok = true;
        }
    } else if (!ORDER.goal) {
        refresh_objective();
    }
    const c::distance_field& OBJ = ORDER.goal ? *ORDER.goal : *S.objective;
    // Leash (order.h): a move may not end farther than max(leash, here) on the leash field.
    const int leash_cap = ORDER.leash_field ? std::max(ORDER.leash, fv(*ORDER.leash_field, pos)) : 0;
    auto leashed = [&](vec2 q) { return ORDER.leash_field && fv(*ORDER.leash_field, q) > leash_cap; };
    vec2 bank_altar{-1, -1};
    for (auto a : S.altars) if (cheb(pos, a) <= 1) bank_altar = a;

    if (foes.empty() && camps.empty() && !(gems > 0 && bank_altar.x >= 0)) {
        // Fixed rule only when nothing is in view: heal at spawn if hurt, else head for the objective.
        if (my_frac < 0.9f) { if (!S.own_spawn.is_set(pos)) step_along(*S.home, blocked); return; }
        step_along(OBJ, blocked);
        return;
    }
    if (!foes.empty()) { S.last_seen = foes[0].pos; S.last_seen_round = round; }

    // ---- spells ----
    std::vector<Spell> spells;
    int ready_count = 0;
    if (my_state.contains(c::champion_state::can_cast)) {
        const auto abs = me.abilities();
        for (const auto& ab : abs) {
            if (!ab.info()) continue;
            try {
                if (!ab.is_ready()) continue;
            } catch (const std::exception&) { continue; }
            Spell s{static_cast<int>(ab.slot()), ab.is_damage() || ab.is_aoe_damage() || ab.is_cc(), ab.is_aoe_damage(),
                    ab.is_cc() || stun_slot(S.type, static_cast<int>(ab.slot())), ab.is_attack_reset(), ab.is_shield(),
                    ab.is_dash(), 0.0f};
            if (s.offensive && !ab.is_cc()) s.amount = ab.damage_amount();
            spells.push_back(s);
            ++ready_count;
        }
    }

    // ---- enumerate and score options ----
    // champion_type 12 (Heart of Ruin, 20 Oct patch) has no trained row yet: the hand prior.
    const auto& W = S.type >= 0 && S.type <= 11 ? WEIGHTS[S.type] : PRIOR;
    const int home_now = fv(*S.home, pos);
    std::vector<Opt> opts;
    opts.reserve(40);
    auto add = [&](vec2 dest, int spell, int target, bool atk) { opts.push_back(Opt{dest, spell, target, atk}); };
    // Per-turn constants hoisted out of the destination loop (host calls; same values every iteration).
    const bool can_move = my_state.contains(c::champion_state::can_move), can_attack = my_state.contains(c::champion_state::can_attack);
    std::vector<vec2> camp_pos;
    camp_pos.reserve(camps.size());
    for (const auto& cm : camps) camp_pos.push_back(cm.spawn_position());
    for (std::size_t mi = 0; mi < MOVES.size(); ++mi) {
        const vec2 q{pos.x + MOVES[mi].x, pos.y + MOVES[mi].y};
        const bool stay = mi == 0;
        if (!stay && (!can_move || !inb(q) || blocked.is_set(q) || leashed(q))) continue;
        // Best attackable foe from q (lowest health).
        int atk_t = -1;
        if (can_attack && (stay || !S.ranged))
            for (int i = 0; i < static_cast<int>(foes.size()); ++i)
                if (cheb(q, foes[i].pos) <= my_range && (atk_t < 0 || foes[i].hp < foes[atk_t].hp)) atk_t = i;
        add(q, -1, atk_t, atk_t >= 0);
        if (atk_t >= 0) add(q, -1, -1, false);  // also allow holding the attack
        if (can_attack && (stay || !S.ranged))
            for (int ci = 0; ci < static_cast<int>(camps.size()); ++ci)
                if (cheb(q, camp_pos[ci]) <= my_range) {
                    Opt o{q, -1, -1, true};
                    o.camp = ci;
                    opts.push_back(o);
                    break;
                }
        if (stay && gems > 0 && bank_altar.x >= 0) {
            Opt o{q, -1, -1, false};
            o.bank = true;
            opts.push_back(o);
        }
        for (int si = 0; si < static_cast<int>(spells.size()); ++si) {
            const auto& s = spells[si];
            if (s.offensive) {
                if (!stay) continue;  // offensive spells exclude walking
                for (int i = 0; i < static_cast<int>(foes.size()); ++i)
                    if (cheb(pos, foes[i].pos) <= 4) add(q, si, i, atk_t >= 0);
            } else if (s.reset || s.shield) {
                add(q, si, atk_t, atk_t >= 0);
            } else if (s.dash && stay && !foes.empty()) {
                // Dash two tiles straight toward or away from the nearest foe.
                const vec2 f = foes[0].pos;
                for (int sign : {1, -1}) {
                    const int dx = (f.x > pos.x) - (f.x < pos.x), dy = (f.y > pos.y) - (f.y < pos.y);
                    vec2 d{pos.x + sign * 2 * dx, pos.y + sign * 2 * dy};
                    if (inb(d) && !blocked.is_set(d) && !leashed(d)) add(d, si, -1, false);
                }
            }
        }
    }

    Opt* best = nullptr;
    // Values that depend only on the destination tile or only on the target are computed once per turn
    // (several options share a destination: attack / hold / spells). Same numbers in the same order as
    // computing them per option, so the scores and games are unchanged; it only saves fuel (budget.h).
    const int obj_now = fv(OBJ, pos), alt_now = fv(*S.altar, pos);
    struct DestInfo { vec2 q; float threat; int home_q, obj_q, alt_q; float ally_d, ally_frac; };
    std::vector<DestInfo> dcache;
    auto dest_info = [&](vec2 q) -> const DestInfo& {
        for (const auto& d : dcache) if (d.q == q) return d;
        DestInfo d{q, 0.0f, fv(*S.home, q), fv(OBJ, q), fv(*S.altar, q), 0.0f, 0.0f};
        for (const auto& e : foes) if (cheb(q, e.pos) <= e.reach) d.threat += e.dps;
        d.threat /= my_max;
        if constexpr (TEAM) {
            int best_ad = 1 << 20;
            for (const auto& al : allies) {
                const int ad = cheb(q, al.pos);
                if (ad < best_ad) { best_ad = ad; d.ally_d = ad / 10.0f; d.ally_frac = al.frac; }
            }
        }
        dcache.push_back(d);
        return dcache.back();
    };
    struct TargetInfo { float on_target, near_target, near_foes; };
    std::vector<TargetInfo> tcache;
    if constexpr (TEAM) {
        if (!foes.empty()) {
            for (int ti = 0; ti < static_cast<int>(foes.size()); ++ti) {
                const Foe& f = foes[ti];
                TargetInfo t{0, 0, 0};
                for (const auto& al : allies) {
                    if (cheb(al.pos, f.pos) <= al.range) t.on_target += 1;
                    if (cheb(al.pos, f.pos) <= 3) t.near_target += 1;
                }
                for (int i = 0; i < static_cast<int>(foes.size()); ++i)
                    if (i != ti && cheb(foes[i].pos, f.pos) <= 3) t.near_foes += 1;
                tcache.push_back(t);
            }
        }
    }
    dcache.reserve(16);
    for (auto& o : opts) {
        const vec2 q = o.dest;
        const DestInfo& di = dest_info(q);
        const Spell* sp = o.spell >= 0 ? &spells[o.spell] : nullptr;
        const bool has_foe = !foes.empty();
        const int ti = o.target >= 0 ? o.target : 0;
        static const Foe none{nullptr, vec2{0, 0}, 1.0f, 1.0f, 0.0f, 0, false, false};
        const Foe& f = has_foe ? foes[ti] : none;
        const int d0 = has_foe ? cheb(pos, f.pos) : 0, d1 = has_foe ? cheb(q, f.pos) : 0;
        const float closing = static_cast<float>(d0 - d1);
        float dmg = (o.atk && o.camp < 0 && has_foe) ? my_str : 0.0f;
        if (sp && sp->offensive) dmg += sp->amount;
        if (sp && sp->reset && o.atk && o.camp < 0) dmg += my_str;
        const float threat = di.threat;
        const float stun = (sp && sp->offensive && sp->stun) ? 1.0f : 0.0f;
        const int home_q = di.home_q;
        float x[NX] = {
            dmg / f.maxhp,
            dmg >= f.hp ? 1.0f : 0.0f,
            threat,
            threat * (1 - my_frac),
            -closing,
            has_foe ? d1 / 10.0f : 0.0f,
            S.own_spawn.is_set(q) ? 1.0f : 0.0f,
            home_q / 20.0f,
            stun,
            (f.stunned ? 1.0f : 0.0f) * closing,
            (sp && (sp->reset || sp->shield)) ? 1.0f : 0.0f,
            (sp && sp->dash) ? 1.0f : 0.0f,
            my_frac * closing,
            (f.hp / f.maxhp) * closing,
            (o.atk && o.camp < 0) ? 1.0f : 0.0f,
            (has_foe && d1 <= my_range) ? 1.0f : 0.0f,
            ready_count * closing,
            threat * (f.hp / f.maxhp),
            (1 - my_frac) * static_cast<float>(home_now - home_q),
            (f.channel ? 1.0f : 0.0f) * stun,
            o.camp >= 0 ? 1.0f : 0.0f,
            static_cast<float>(obj_now - di.obj_q),
            o.bank ? 1.0f : 0.0f,
            gems / 50.0f * threat,
            gems / 50.0f * static_cast<float>(alt_now - di.alt_q),
        };
        if constexpr (TEAM) {
            // Team features: allies on the target, nearest ally, local counts around the target.
            const float on_target = has_foe ? tcache[ti].on_target : 0.0f;
            const float near_target = has_foe ? tcache[ti].near_target : 0.0f;
            const float near_foes = has_foe ? tcache[ti].near_foes : 0.0f;
            const float ally_d = di.ally_d, ally_frac = di.ally_frac;
            const bool hits = dmg > 0;
            x[NF_V1 + 0] = hits ? on_target : 0.0f;
            x[NF_V1 + 1] = ally_d;
            x[NF_V1 + 2] = ally_frac;
            x[NF_V1 + 3] = near_target;
            x[NF_V1 + 4] = near_foes;
            x[NF_V1 + 5] = (f.stunned && on_target > 0 && (hits || closing > 0)) ? 1.0f : 0.0f;
        }
        float s = 0;
        for (int i = 0; i < NX; ++i) s += W[i] * x[i];
        o.score = s;
        std::memcpy(o.x.data(), x, sizeof(x));
    }
    // The cheap linear scorer shortlists the top NET_TOP_K options; the learned network re-ranks only
    // those, under a hard per-turn fuel cap (about 13 fuel per link evaluation, measured), so the
    // network never takes more than about a third of the turn. If the cap is hit, keep the linear choice.
    std::vector<Opt*> order;
    for (auto& o : opts) order.push_back(&o);
    std::stable_sort(order.begin(), order.end(), [](const Opt* a, const Opt* b) { return a->score > b->score; });
    if (!order.empty()) best = order[0];
    if (NET.ok) {
        // Fixed work: always re-rank exactly the top budget::NET_TOP_K options, so the choice never depends on
        // how much fuel is left (budget.h). Fuel is checked once, as an emergency brake that cannot fire when
        // the fighter has the turn to itself (training) and only fires in a full bot that overspent elsewhere.
        const auto f0 = c::fuel_remaining();
        Opt* net_best = nullptr;
        float net_best_s = 0;
        const bool complete = f0 >= budget::NET_EMERGENCY_FLOOR;
        for (std::size_t i = 0; complete && i < order.size() && i < budget::NET_TOP_K; ++i) {
            const float v = run_net<NX>(order[i]->x.data());
            if (!net_best || v > net_best_s) { net_best = order[i]; net_best_s = v; }
        }
#if BOT_FUEL
        std::cout << "NN " << S.type << " " << (f0 - c::fuel_remaining()) << " " << complete << " " << c::fuel_remaining() << std::endl;
#endif
        if (complete && net_best) best = net_best;
    }
    if (!best) return;

    // ---- execute ----
    const Spell* sp = best->spell >= 0 ? &spells[best->spell] : nullptr;
    const auto abs = me.abilities();
    auto ability = [&](int slot) -> const c::ability* {
        for (const auto& ab : abs) if (ab.info() && static_cast<int>(ab.slot()) == slot) return &ab;
        return nullptr;
    };
    try {
        if (best->bank) { me.submit_gems(bank_altar); return; }
        if (sp && sp->offensive && best->target >= 0 && !foes.empty()) {
            if (auto ab = ability(sp->slot)) {
                const auto& f = foes[best->target];
                if (sp->aoe) ab->use(f.pos); else ab->use(*f.h);
            }
        }
        if (sp && sp->dash) {
            if (auto ab = ability(sp->slot)) ab->use(best->dest);
        } else if (best->dest != pos) {
            me.move_absolute(best->dest);
        }
        if (sp && sp->shield) if (auto ab = ability(sp->slot)) ab->use();
        if (best->atk && best->camp >= 0 && me.can_attack()) {
            me.attack(camps[best->camp]);
        } else if (best->atk && best->target >= 0 && me.can_attack()) {
            me.attack(*foes[best->target].h);
            if (sp && sp->reset) {
                if (auto ab = ability(sp->slot)) {
                    ab->use();
                    if (me.can_attack() && foes[best->target].h->is_visible()) me.attack(*foes[best->target].h);
                }
            }
        }
    } catch (const std::exception& e) {
        if (BOT_DEBUG) std::cout << "ERR exec " << e.what() << std::endl;
    }
}

} // namespace

void Bot::play_turn() {
    try {
#if BOT_FUEL
        // Fuel probe build (tools/nvn.py fuel): worst turn so far, excluding each body's first turn (init).
        const bool first = !S.ready;
        const auto f0 = c::fuel_remaining();
        if (!S.ready) init();
        if (NET.base == NF) play<true>(); else play<false>();
        const auto used = f0 - c::fuel_remaining();
        if (!first && used > FUEL_MAX) { FUEL_MAX = used; std::cout << "FUEL " << S.type << " " << used << std::endl; }
#else
        if (!S.ready) init();
        if (NET.base == NF) play<true>(); else play<false>();
#endif
    } catch (const std::exception& e) {
        if (BOT_DEBUG) std::cout << "ERR " << e.what() << std::endl;
    }
}
