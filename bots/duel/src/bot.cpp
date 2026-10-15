// Duel policy: enumerate legal (move, spell, attack) options and score each with learned per-champion
// weights (weights.h). Only "where to go when no enemy is visible" is a fixed rule.
#include <cambc.h>
#include "bot.h"
#include "weights.h"
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
    int w = 0, h = 0, type = 0;
    bool ranged = false;
    c::bitboard terrain, own_spawn;
    std::unique_ptr<c::distance_field> home, enemy_home, objective, altar;
    c::bitboard camp_tiles;
    std::vector<c::map::camp_group> groups;
    std::vector<c::bitboard> group_tiles;
    std::vector<vec2> altars;
    vec2 last_seen{-1, -1};
    int last_seen_round = -1000;
};
State S;

int cheb(vec2 a, vec2 b) { return static_cast<int>(a.chebyshev_distance(b)); }
bool inb(vec2 p) { return p.x >= 0 && p.y >= 0 && p.x < S.w && p.y < S.h; }
int fv(const c::distance_field& f, vec2 p) { const int v = f.get(p); return v == UNREACH ? 999 : v; }

std::unique_ptr<c::distance_field> field_to(const c::bitboard& roots) {
    auto f = std::make_unique<c::distance_field>();
    c::bitboard visited;
    f->compute(S.terrain & ~roots, roots, visited);
    return f;
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

struct Opt {
    vec2 dest;
    int spell = -1;  // index into spells
    int target = -1; // foe index for spell/attack
    bool atk = false;
    int camp = -1;   // camp index attacked instead of a foe
    bool bank = false;
    float score = -1e30f;
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

void play() {
    auto& me = c::self();
    if (!S.ready) init();
    if (!me.is_alive()) return;
    if (me.is_recalling() || me.is_submitting_gems() || me.is_teleporting()) return;

    const vec2 pos = me.position();
    const float my_hp = me.health(), my_max = std::max(1.0f, me.max_health());
    const float my_frac = my_hp / my_max;
    const float my_str = me.strength();
    const int my_range = me.attack_range();
    const int round = static_cast<int>(c::round_number());

    auto blocked = S.terrain | c::vision::champion_tiles() | c::vision::camp_tiles();
    blocked.set(pos, false);

    // ---- visible enemies ----
    std::vector<c::champion> raw;
    if (!c::vision::champion_tiles(team::enemy).is_empty()) raw = c::vision::champions(team::enemy);
    std::vector<Foe> foes;
    for (const auto& e : raw) {
        try {
            if (!e.is_visible() || !e.is_alive()) continue;
            Foe f{&e, e.position(), e.health(), std::max(1.0f, e.max_health()), e.strength() + 0.25f * e.focus(),
                  e.attack_range() + 1, e.buffs().has_stun(), e.is_submitting_gems() || e.is_recalling()};
            if (f.stunned) f.dps *= 0.3f;
            foes.push_back(f);
        } catch (const std::exception&) {}
    }

    // Harmless clusters in view (the contested objective).
    std::vector<c::camp> camps;
    for (const auto& m : c::vision::camps()) {
        try { if (m.strength() <= 0 && m.health() > 0) camps.push_back(m); } catch (const std::exception&) {}
    }
    const int gems = static_cast<int>(me.gem_count());
    // Objective = tiles of groups that are alive or waiting to be captured; drop groups seen respawning.
    if (!S.camp_tiles.is_empty()) {
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
        S.objective = field_to(live.is_empty() ? S.camp_tiles : live);
    }
    vec2 bank_altar{-1, -1};
    for (auto a : S.altars) if (cheb(pos, a) <= 1) bank_altar = a;

    if (foes.empty() && camps.empty() && !(gems > 0 && bank_altar.x >= 0)) {
        // Fixed rule only when nothing is in view: heal at spawn if hurt, else head for the objective.
        if (my_frac < 0.9f) { if (!S.own_spawn.is_set(pos)) step_along(*S.home, blocked); return; }
        step_along(*S.objective, blocked);
        return;
    }
    if (!foes.empty()) { S.last_seen = foes[0].pos; S.last_seen_round = round; }

    // ---- spells ----
    std::vector<Spell> spells;
    int ready_count = 0;
    if (me.can_cast()) {
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
    const auto& W = WEIGHTS[std::clamp(S.type, 0, 11)];
    const int home_now = fv(*S.home, pos);
    std::vector<Opt> opts;
    auto add = [&](vec2 dest, int spell, int target, bool atk) { opts.push_back(Opt{dest, spell, target, atk}); };
    for (std::size_t mi = 0; mi < MOVES.size(); ++mi) {
        const vec2 q{pos.x + MOVES[mi].x, pos.y + MOVES[mi].y};
        const bool stay = mi == 0;
        if (!stay && (!me.can_move() || !inb(q) || blocked.is_set(q))) continue;
        // Best attackable foe from q (lowest health).
        int atk_t = -1;
        if (me.can_attack() && (stay || !S.ranged))
            for (int i = 0; i < static_cast<int>(foes.size()); ++i)
                if (cheb(q, foes[i].pos) <= my_range && (atk_t < 0 || foes[i].hp < foes[atk_t].hp)) atk_t = i;
        add(q, -1, atk_t, atk_t >= 0);
        if (atk_t >= 0) add(q, -1, -1, false);  // also allow holding the attack
        if (me.can_attack() && (stay || !S.ranged))
            for (int ci = 0; ci < static_cast<int>(camps.size()); ++ci)
                if (cheb(q, camps[ci].spawn_position()) <= my_range) {
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
                    if (inb(d) && !blocked.is_set(d)) add(d, si, -1, false);
                }
            }
        }
    }

    Opt* best = nullptr;
    for (auto& o : opts) {
        const vec2 q = o.dest;
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
        float threat = 0;
        for (const auto& e : foes) if (cheb(q, e.pos) <= e.reach) threat += e.dps;
        threat /= my_max;
        const float stun = (sp && sp->offensive && sp->stun) ? 1.0f : 0.0f;
        const int home_q = fv(*S.home, q);
        float x[NF] = {
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
            static_cast<float>(fv(*S.objective, pos) - fv(*S.objective, q)),
            o.bank ? 1.0f : 0.0f,
            gems / 50.0f * threat,
            gems / 50.0f * static_cast<float>(fv(*S.altar, pos) - fv(*S.altar, q)),
        };
        float s = 0;
        for (int i = 0; i < NF; ++i) s += W[i] * x[i];
        o.score = s;
        if (!best || s > best->score) best = &o;
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
        play();
    } catch (const std::exception& e) {
        if (BOT_DEBUG) std::cout << "ERR " << e.what() << std::endl;
    }
}
