#pragma once
// Learned action-scoring weights, one row per champion_type (12) x NF features.
// tools/duel_train.py regenerates this file for each candidate; this copy is the hand prior.
constexpr int NF = 31;
constexpr int NF_V1 = 25;   // feature count of format-1 genome blobs (hidden slots start here)
// Feature order (see features() in bot.cpp):
//  0 dmg/enemy_maxhp   1 kill           2 threat         3 threat*(1-myhp)  4 dist_change
//  5 dist/10           6 on_spawn       7 home_dist/20   8 stun_cast        9 enemy_stunned*closing
// 10 self_buff        11 dash          12 myhp*closing  13 enemyhp*closing 14 attack_used
// 15 can_hit_next     16 spells_ready*closing 17 threat*enemyhp 18 (1-myhp)*retreat 19 channel*stun
// 20 camp_attack      21 objective_closing 22 bank_now  23 gems_carried*threat 24 altar_closing*gems
// Team features (NvN; zero weight in the prior, so 1v1 play is unchanged):
// 25 focus_fire: allies in range of the target, if this option damages it
// 26 nearest_ally_dist/10 from dest   27 nearest ally hp frac  28 allies near target
// 29 other enemies near target        30 ally_cc: target stunned with an ally on it, if engaging
constexpr float PRIOR[NF] = {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f,
                             0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f,
                             0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
constexpr float WEIGHTS[12][NF] = {
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f, 1.0f, 1.0f, 2.0f, -1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
};
