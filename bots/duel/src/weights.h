#pragma once
// Learned action-scoring weights, one row per champion_type (12) x NF features.
// tools/duel_train.py regenerates this file for each candidate; this copy is the hand prior.
constexpr int NF = 20;
// Feature order (see features() in bot.cpp):
//  0 dmg/enemy_maxhp   1 kill           2 threat         3 threat*(1-myhp)  4 dist_change
//  5 dist/10           6 on_spawn       7 home_dist/20   8 stun_cast        9 enemy_stunned*closing
// 10 self_buff        11 dash          12 myhp*closing  13 enemyhp*closing 14 attack_used
// 15 can_hit_next     16 spells_ready*closing 17 threat*enemyhp 18 (1-myhp)*retreat 19 channel*stun
constexpr float PRIOR[NF] = {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f,
                             0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f};
constexpr float WEIGHTS[12][NF] = {
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
    {3.0f, 3.0f, -1.0f, -3.0f, -0.2f, 0.0f, 0.3f, 0.0f, 1.0f, 0.5f, 0.3f, 0.0f, 0.5f, -0.3f, 1.0f, 0.5f, 0.1f, 0.0f, 1.0f, 2.0f},
};
