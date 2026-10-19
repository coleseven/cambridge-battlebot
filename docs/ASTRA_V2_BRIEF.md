# Brief for Astra: forager v2, the director that gives orders to the trained fighters

Paste-ready (18 Oct 2026). Read `docs/STRATEGY_V2.md` (design and the evidence behind it), `docs/TEAM_BOT.md`
(combined-bot results), `docs/FUEL_BUDGET.md` and `docs/ASTRA_FUEL_BRIEF.md` (fuel), first.

## Why
The forager was built without knowing what the trained fighter networks do. In the combined bot
(`tools/team_bot.py`), on all 17 maps against Tarot / v1 / starter:
- the forager alone scores +0.327;
- with the networks attached at the best handoff it scores +0.245, with deaths up from 11.9 to 13.2 per game;
- the fight selector variants are worse still.

The main causes:
1. bodies abandon **captures** and **awake camps** when an enemy comes into range, because the network
   doesn't know about either;
2. in contact the network steers by its own objective, not the forager's plan;
3. the forager's own combat, flee, interrupt and fight-selector logic duplicates what the networks learned
   (target choice, kiting, retreat when hurt, stun use, **interrupting channels**, banking under threat,
   focus fire and peel).

## Task: director v2
1. **Orders.** Every turn, decide an Order per body:
   - FARM(g), CAPTURE(g), BANK(altar), RECALL, SHOP, HOLD/REGROUP(point);
   - later, ESCORT(ally) and INTERCEPT(enemy);
   - each with a goal tile set, the director's distance field to it, a `leash`, and a `locked` flag (e.g.
     CAPTURE with ≤ 2 rounds left).

   Keep your existing logic for camp scoring, clearing, the capture protocol, bank timing and altar choice,
   recall and shop timing, respawn timers and the opening; it now produces Orders.
2. **Contact rule.** If an enemy is within `FIGHTER_ENGAGE_RANGE` (or a champion damaged us last turn), call
   `fighter::take_turn(order, observation)`; the network acts toward `order.goal`. Otherwise execute the order
   with your scripted farm executor.
3. **Delete the duplicates when a fighter is attached:** your combat block (fight / flee / stun interrupts),
   the fight selector, flee-on-bag. Keep them for standalone forager testing behind a switch.
4. **Observation struct.** Publish what you already compute per turn: visible champion records, live camp
   groups, per-group distance fields, home and altar fields. The fighter will read it instead of
   re-querying, which removes its real-map fuel spike.
5. **Comms → roles.** Use the team belief to assign roles (farmer, carrier, escort, interceptor, regroup) as
   Orders. Intercept election stays yours; the network executes the INTERCEPT order (its channel × stun
   skill is the interrupt).
6. **Fuel:** director ≤ `MACRO` (25k) and comms ≤ `COMMS` (8k) at p99 (`bots/neat/src/budget.h`). See
   `docs/ASTRA_FUEL_BRIEF.md` for the measurements (features all on currently reaches 100k).
7. **Tunables:** every threshold you add (leash, lock, role rules) goes in `params.h` as a
   `constexpr <type> NAME = <number>;` line, so CMA-ES (`tools/tune.py` + `tools/space-team.json`) can tune
   it.

## Interfaces
- Claude provides the fighter side:
  - `fighter::take_turn(const Order&, const Observation&)`;
  - `fighter::prepare()`;
  - Order and Observation as shared headers, agreed before either side codes against them;
  - behaviour-preserving when the order's goal equals the fighter's old objective (`tools/same_games.py`
    proves it).
- `tools/team_bot.py` keeps generating the combined bot from your source, so keep `bot.cpp` / `draft.cpp` /
  `params.h` / `combat_hook.h` file names. If you change the draft's comp-selection line, tell Claude:
  the generator patches it for training.

## Evaluate
`tools/team_bot.py --build`, then `.venv/Scripts/python tools/eval.py team --vs sparring-tarot v1 starter-cpp`
(17 maps × both seats). Report fitness, wins per opponent, deaths per game, captures lost, and fuel p50/p99
(`tools/fuel_engine.py team`). Compare against the forager alone and the current team bot.

## Don't
Don't edit `bots/neat`, `bots/main`, `bots/duel` or `tools/*train*.py` (the training pipeline). The fighter
networks keep training on real maps every night, so judge v2 with them attached.
