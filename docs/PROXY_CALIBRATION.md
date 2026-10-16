# Macro proxy calibration

Does `tools/macro_proxy.py` (see `docs/STRATEGY_PROXY.md`) predict real games well enough to rank
macro strategies, or to train them inside it? `tools/proxy_calibrate.py` answers this. It fits the
proxy's free assumptions to full-engine replays and reruns the STRATEGY_PROXY.md comparisons under the
fitted settings. It imports the proxy unchanged.

```bash
.venv/Scripts/python tools/fight_data.py pool --bots main v1 --vs starter-cpp sparring-tarot   # the real games
.venv/Scripts/python tools/proxy_calibrate.py           # tables -> .arena/proxycal/report.md
```

## Setup (16 Oct 2026)

- **Real games:** 136 full-engine games on all 17 published maps, both seats: main and v1 against
  starter-cpp and sparring-tarot. main and v1 are identical code, so there are 68 distinct games;
  the duplicates only double their weight.
- **Real metrics per team,** decoded with `tools/replay`: first capture, first completed deposit,
  captures by round 300, and banked gems at rounds 150, 300 and 500.
- **Fitted parameters:**
  - clear DPS: 40–400;
  - tie rule: stall or incumbent;
  - carrier risk: none, displace or loot;
  - one proxy policy per bot from `spread`, `pair`, `selective` and `reinforce` (bank at 24), plus
    `spread:48` and `pair:48`, chosen by coordinate descent.
- **Error:** the mean, over metrics, of |proxy − real| divided by a per-metric "meaningful
  difference": 30 rounds, 50 rounds, 5 captures, then 60, 120 and 200 gems. Each term is capped at 4.

## Results

| | value |
|---|---|
| best fit | clear DPS 60, stall, carrier risk none |
| policies | main/v1 → `pair:24`; starter-cpp and Tarot → `selective:24` |
| fitted error | **1.118** |
| baseline error (predict the real mean for every team) | 1.382 |
| Spearman correlation, proxy vs real banked margin at round 500 | **0.53** (n = 136) |

**The fit is flat.** Every combination of tie rule and carrier risk at 60–80 DPS scores between
1.118 and 1.133. The real games can't tell the proxy's contest and carrier assumptions apart, so the
fit says nothing about which counterfactual rule is "true". The fitted DPS of 60 is implausibly low;
it mainly offsets a timing bias the proxy has elsewhere.

Per-metric bias, proxy minus real, at the best fit:

| metric | mean bias | mean abs error |
|---|---:|---:|
| first capture (rounds) | +37.8 | 40.5 |
| first completed deposit (rounds) | +31.6 | 36.0 |
| captures by round 300 | −4.1 | 5.8 |
| banked at round 150 | +4.4 | 58.1 |
| banked at round 300 | +12.6 | 113.3 |
| banked at round 500 | −41.8 | 227.3 |

**The proxy is structurally slow early.** Its first capture comes about 30–40 rounds late and it has
about 4 fewer captures by round 300. Lowering DPS makes this worse (at 80 DPS the first-capture bias
was +26.7), so the cause isn't the damage rate. Candidates:
- routes treat every monster spawn tile as a wall;
- `max(travel, wait)` is used for respawns;
- each pick is one body, so Cassi & Issac's second body is missing;
- no first-camp-near-spawn opening.

These are the proxy owner's to look at.

Worst-fitting maps across all four sides: interlock (2.2), expanse (1.6), rushbait-v2 (1.6) and
monolith (1.5). Best: fragment (0.5), elkring (0.6), rows (0.6). The full per-map table is in
`.arena/proxycal/report.md`.

## The STRATEGY_PROXY.md conclusions under the calibrated settings

All 17 maps, both seats, 600 rounds:

| candidate | vs | mean banked margin | wins | holds up? |
|---|---|---:|---:|---|
| `pair:24` | `spread:24` | +388.5 | 30/34 | **yes**, and more strongly; loses only on expanse and rushbait-v2 |
| `selective:24` | `pair:24` | −182.2 | 6/34 | **no**: reversed. STRATEGY_PROXY.md had selective ahead under all three risk rules |
| `pair:80` | `pair:24` | −47.1 | 6/34 | **yes**: the 80-gem threshold stays worse |
| `pair:80:escort` | `pair:24` | −68.3 | 4/34 | **yes**: the escort doesn't rescue it |

## What this means

- **Ranking.** With a rank correlation of about 0.5 and a fit only about 19% better than predicting
  the mean, the proxy is usable for coarse screening: "pair beats spread", "don't bank at 80". It
  can't settle close calls such as selective vs pair, where the verdict flips with the settings.
- **Training inside it.** Not yet. A policy trained in the proxy would exploit its early-game lag and
  its fight rule. Fix the opening timing first, then replace the fixed contest rules with the learned
  fight win-probability (`docs/WIN_MODEL.md`), then recalibrate. A proxy-trained policy still needs
  matched full-engine games with the trained fighters attached before it goes into `forager`.
- **Data.** Starter-cpp and Tarot rarely fight, so these games barely exercise the contest rules.
  Games with the trained fighters attached would make the fit more informative.
