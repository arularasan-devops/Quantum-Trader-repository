# Phase 8 — calibration, costs, attribution, expiry & profit capture (research only)

Sessions analysed: **2026-08-18** · resolved trades: **141** · instruments: CRUDEOIL, NIFTY, SILVER, GOLD, COPPER, BANKNIFTY, SENSEX

Evidence labels: `OBSERVATION` describes this sample; `HYPOTHESIS` is a relationship worth testing; `REQUIRES MORE DATA` means the cell is too small to read; `VALIDATED` requires the gates to pass **and** a chronological holdout to agree, so it cannot be earned from one dataset and appears nowhere below. Nothing here is promoted to a trading rule.

## Acceptance gates

Phase 8 status: **EXPLORATORY_ONLY** — failed: REAL_SESSIONS, ADEQUATE_EXPIRY_SESSIONS, EXPIRY_TRADE_COUNT, CELL_SIZE

| gate | need | have | pass | a failure forbids |
| --- | --- | --- | --- | --- |
| REAL_SESSIONS | >= 20 recorded sessions | 1 | FAIL | any statement that one exit policy is better than another |
| RESOLVED_TRADES | >= 100 resolved trades | 141 | PASS | expectancy and profit-factor comparisons |
| ADEQUATE_EXPIRY_SESSIONS | >= 4 instrument-sessions on expiry day | 1 | FAIL | any claim that expiry day behaves differently — one expiry session measures that contract's last day, not expiry |
| EXPIRY_TRADE_COUNT | >= 30 resolved trades classified EXPIRY_DAY | 23 | FAIL | expiry-specific exit policy comparison |
| EXPIRY_CLASSIFIED | >= 90.0% of trades with a parsed contract expiry | 100.0 | PASS | treating the expiry split as complete |
| SPREAD_COVERAGE | >= 90.0% of trades priced through a real bid/ask at both ends | 100.0 | PASS | net-of-cost conclusions on the unpriced trades |
| CELL_SIZE | >= 30 trades in every reported cell | 5 | FAIL | expiry / regime / instrument / entry-quality breakdowns |

Phase 7 gates carried over: REAL_SESSIONS FAIL (2), SAMPLE_SIZE PASS (141), CELL_SIZE FAIL (0), DATA_COMPLETENESS FAIL (69.87), SPREAD_AVAILABLE PASS (True), MANUAL_COMPARISON FAIL (0)

## Confidence calibration — is the displayed number meaningful?

Population: CONTROL entry | A_BASELINE exit (the engine's own trade) · resolved 141

| bucket | n | predicted | actual target-before-stop % | expectancy R | PF | median MFE R | median MAE R | label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| <70 | 0 | — | — | — | — | — | — | REQUIRES MORE DATA |
| 70-75 | 0 | — | — | — | — | — | — | REQUIRES MORE DATA |
| 75-80 | 65 | 78.1 | 10.8 | 0.012 | 1.046 | 0.05 | -0.229 | HYPOTHESIS |
| 80-85 | 29 | 82.4 | 6.9 | -0.23 | 0.421 | 0.081 | -0.447 | HYPOTHESIS |
| 85-90 | 21 | 87.3 | 0.0 | 0.037 | 1.182 | 0.232 | -0.072 | HYPOTHESIS |
| 90-95 | 17 | 92.7 | 11.8 | 0.209 | 2.527 | 0.202 | -0.053 | HYPOTHESIS |
| 95+ | 9 | 95.9 | 22.2 | 0.256 | 1.885 | 0.61 | 0.0 | REQUIRES MORE DATA |

**Monotonicity verdict: NON_MONOTONE** — calibration_failure = `True`  → the confidence system is marked **NON-MONOTONIC / MIS-CALIBRATED** on this sample

- CALIBRATION FAILURE on this sample: at least one higher confidence bucket performed worse than a lower one, so the displayed number did not rank outcomes here. The immediate consequence is interpretive — do not read a higher number as a better trade — not a change to how confidence is computed
- hit-rate inversions (higher bucket did worse): 75-80→80-85 −3.9pp, 80-85→85-90 −6.9pp
- expectancy inversions: 75-80→80-85 −0.242R
- Spearman(confidence, realised R) = 0.1416 · buckets with 10+ trades: 4
- label: **REQUIRES MORE DATA** — 9 trades in the smallest reported cell — below 10, so the split is reported for transparency and must not be read as a difference
- the displayed confidence is read here and never written; no gate, threshold or confidence formula is touched

Splits (expiry / instrument / direction), monotonicity verdict per cell:

| split | cell | resolved | buckets with 10+ | verdict | Spearman |
| --- | --- | --- | --- | --- | --- |
| by_expiry_class | EXPIRY_DAY | 23 | 1 | UNTESTABLE | -0.2256 |
| by_expiry_class | NON_EXPIRY | 113 | 4 | NON_MONOTONE | 0.2041 |
| by_expiry_class | PRE_EXPIRY | 5 | 0 | UNTESTABLE | 0.5 |
| by_instrument | BANKNIFTY | 14 | 0 | UNTESTABLE | 0.4464 |
| by_instrument | CRUDEOIL | 46 | 3 | NON_MONOTONE | 0.1756 |
| by_instrument | GOLD | 27 | 1 | UNTESTABLE | 0.67 |
| by_instrument | NIFTY | 23 | 1 | UNTESTABLE | -0.2256 |
| by_instrument | SENSEX | 5 | 0 | UNTESTABLE | 0.5 |
| by_instrument | SILVER | 26 | 1 | UNTESTABLE | -0.4747 |
| by_direction | CE | 45 | 2 | NON_MONOTONE | 0.4383 |
| by_direction | PE | 96 | 4 | NON_MONOTONE | 0.0821 |

## Spread cost — how much expectancy is lost to the real book?

Model: buy at ask, sell at bid, against Phase 7's snapshot-premium fills; brokerage excluded and still an assumption. A leg without a usable two-sided quote at both ends is `SPREAD_UNKNOWN` and is excluded, never assigned a fabricated spread.

| group | trades | measured | median entry spread % | median cost R | spread % of risk | gross expectancy R | net expectancy R | flipped to loss | label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ALL | 141 | 141 | 0.42 | 0.051 | 5.1 | 0.006 | -0.359 | 16 | HYPOTHESIS |
| side CE | 45 | 45 | 0.38 | 0.041 | 4.1 | -0.004 | -0.291 | 2 | HYPOTHESIS |
| side PE | 96 | 96 | 0.44 | 0.064 | 6.4 | 0.01 | -0.391 | 14 | HYPOTHESIS |
| BANKNIFTY | 14 | 14 | 0.275 | 0.033 | 3.3 | 0.092 | 0.053 | 0 | HYPOTHESIS |
| CRUDEOIL | 46 | 46 | 0.335 | 0.041 | 4.1 | -0.001 | -0.051 | 3 | HYPOTHESIS |
| GOLD | 27 | 27 | 10.85 | 1.644 | 164.4 | 0.291 | -1.326 | 10 | HYPOTHESIS |
| NIFTY | 23 | 23 | 0.25 | 0.036 | 3.6 | -0.073 | -0.133 | 0 | HYPOTHESIS |
| SENSEX | 5 | 5 | 0.21 | 0.026 | 2.6 | 0.891 | 0.865 | 0 | REQUIRES MORE DATA |
| SILVER | 26 | 26 | 0.98 | 0.088 | 8.8 | -0.427 | -0.558 | 3 | HYPOTHESIS |

## Expiry analysis — is expiry day materially different?

Classification: dte==0 EXPIRY_DAY · 1..2 PRE_EXPIRY · >=3 NON_EXPIRY, from the expiry encoded in the tradingsymbol (the recorded leg has no expiry field) · coverage 100.0% · instrument-sessions on expiry day: 1

| class | n | sessions | target-before-stop % | expectancy R | PF | median MFE R | median MAE R | MFE capture % | give-back R | min to MFE | min to MAE | held min | spread cost R | premium expansion % | label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| EXPIRY_DAY | 23 | 1 | 26.1 | -0.073 | 0.899 | 0.909 | -1.014 | 15.9 | 0.075 | 29.0 | 18.5 | 13.317 | 0.036 | 14.29 | HYPOTHESIS |
| PRE_EXPIRY | 5 | 1 | 60.0 | 0.891 | 22.631 | 1.411 | -0.146 | 100.0 | 0.0 | 17.2 | 14.6 | 18.217 | 0.026 | 17.45 | REQUIRES MORE DATA |
| NON_EXPIRY | 113 | 1 | 3.5 | -0.017 | 0.907 | 0.082 | -0.103 | 61.55 | 0.0 | 30.4 | 17.7 | 29.8 | 0.081 | 0.66 | HYPOTHESIS |

Per instrument (never pooled — the same date is expiry for one instrument and not another):

| instrument | venue | expiries seen | class | n | expectancy R | MFE capture % | give-back R | label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BANKNIFTY | NFO | 2026-08-25 | NON_EXPIRY | 14 | 0.092 | 100.0 | 0.0 | HYPOTHESIS |
| CRUDEOIL | MCX | 2026-09-17 | NON_EXPIRY | 46 | -0.001 | 67.0 | 0.039 | HYPOTHESIS |
| GOLD | MCX | 2026-08-31 | NON_EXPIRY | 27 | 0.291 | 100.0 | 0.0 | HYPOTHESIS |
| NIFTY | NFO | 2026-08-18 | EXPIRY_DAY | 23 | -0.073 | 15.9 | 0.075 | HYPOTHESIS |
| SENSEX | BFO | 2026-08-20 | PRE_EXPIRY | 5 | 0.891 | 100.0 | 0.0 | REQUIRES MORE DATA |
| SILVER | MCX | 2026-08-28 | NON_EXPIRY | 26 | -0.427 | 0.0 | 0.556 | HYPOTHESIS |

- an instrument-session is expiry for one instrument and not for another on the same date; instruments are never pooled in the per-instrument tables above

## Profit capture and give-back — which exit keeps more, net of costs?

policies are ranked on NET expectancy after the recorded spread; a ranking on gross R rewards the policies that trade most and is not reported as a result. A policy is also charged with its premature exits: the share of trades it left while the premium later exceeded its exit price by 0.25R.

| policy | n | gross exp R | **net exp R** | net PF | win % | median MFE capture % | median give-back R | stopped % | target % | premature exit % | median held min | label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_BASELINE | 141 | 0.006 | **-0.359** | 0.363 | 37.6 | 47.5 | 0.0 | 14.9 | 9.2 | 0.0 | 29.55 | HYPOTHESIS |
| B_FIXED_2PCT | 141 | 0.084 | **-0.254** | 0.401 | 51.8 | 100.0 | 0.0 | 9.2 | 0.0 | 17.0 | 29.2 | HYPOTHESIS |
| B_FIXED_3PCT | 141 | 0.068 | **-0.265** | 0.413 | 48.2 | 100.0 | 0.0 | 9.2 | 0.0 | 16.3 | 29.2 | HYPOTHESIS |
| B_FIXED_4PCT | 141 | 0.069 | **-0.264** | 0.427 | 47.5 | 99.2 | 0.0 | 9.9 | 0.0 | 14.2 | 29.2 | HYPOTHESIS |
| B_FIXED_5PCT | 141 | 0.068 | **-0.265** | 0.442 | 45.4 | 93.3 | 0.0 | 9.9 | 0.0 | 12.8 | 29.4 | HYPOTHESIS |
| B_FIXED_7PCT | 141 | 0.055 | **-0.306** | 0.397 | 43.3 | 74.4 | 0.0 | 10.6 | 0.0 | 12.1 | 29.5 | HYPOTHESIS |
| C_TRAIL_25 | 141 | 0.028 | **-0.332** | 0.35 | 44.7 | 60.8 | 0.003 | 9.9 | 5.0 | 10.6 | 29.267 | HYPOTHESIS |
| C_TRAIL_30 | 141 | 0.033 | **-0.327** | 0.36 | 44.7 | 60.8 | 0.0 | 9.9 | 5.7 | 9.9 | 29.267 | HYPOTHESIS |
| C_TRAIL_40 | 141 | 0.032 | **-0.328** | 0.362 | 43.3 | 47.5 | 0.0 | 9.9 | 6.4 | 8.5 | 29.267 | HYPOTHESIS |
| C_TRAIL_50 | 141 | 0.029 | **-0.331** | 0.357 | 43.3 | 47.5 | 0.0 | 9.9 | 6.4 | 8.5 | 29.267 | HYPOTHESIS |
| D_MOMENTUM_FAIL | 141 | -0.014 | **-0.373** | 0.309 | 40.4 | 43.7 | 0.011 | 12.1 | 5.7 | 7.8 | 29.317 | HYPOTHESIS |
| E_TIMEOUT_10 | 141 | 0.065 | **-0.268** | 0.396 | 39.7 | 48.1 | 0.0 | 5.7 | 5.0 | 22.0 | 9.5 | HYPOTHESIS |
| E_STAGNATION | 141 | 0.075 | **-0.249** | 0.395 | 30.5 | 36.7 | 0.0 | 2.1 | 6.4 | 22.7 | 6.317 | HYPOTHESIS |
| F_HYBRID_HALF | 141 | 0.005 | **-0.372** | 0.337 | 39.0 | 60.8 | 0.0 | 14.2 | 7.1 | 2.8 | 29.533 | HYPOTHESIS |

Best net policy per slice — **candidates only**, within-sample:

| slice | policy | n | net exp R | baseline net exp R | margin R | MFE capture % | premature % |
| --- | --- | --- | --- | --- | --- | --- | --- |
| overall | E_STAGNATION | 141 | -0.249 | -0.359 | 0.11 | 36.7 | 22.7 |
| expiry_class:EXPIRY_DAY | B_FIXED_5PCT | 23 | 0.264 | -0.133 | 0.397 | 100.0 | 43.5 |
| expiry_class:NON_EXPIRY | B_FIXED_2PCT | 113 | -0.374 | -0.459 | 0.085 | 100.0 | 5.3 |

### Entry interaction — does a better entry change which exit wins?

| entry quality | policy | n | net exp R | MFE capture % | give-back R |
| --- | --- | --- | --- | --- | --- |
| ACCEPTABLE_ENTRY | A_BASELINE | 31 | -0.405 | 67.9 | 0.011 |
| ACCEPTABLE_ENTRY | B_FIXED_3PCT | 31 | -0.314 | 76.2 | 0.0 |
| ACCEPTABLE_ENTRY | C_TRAIL_30 | 31 | -0.346 | 67.9 | 0.011 |
| ACCEPTABLE_ENTRY | F_HYBRID_HALF | 31 | -0.405 | 67.9 | 0.011 |
| CHASED_ENTRY | A_BASELINE | 34 | -0.386 | 0.0 | 0.0 |
| CHASED_ENTRY | B_FIXED_3PCT | 34 | -0.096 | 84.7 | 0.0 |
| CHASED_ENTRY | C_TRAIL_30 | 34 | -0.334 | 0.0 | 0.0 |
| CHASED_ENTRY | F_HYBRID_HALF | 34 | -0.372 | 0.0 | 0.0 |
| GOOD_ENTRY | A_BASELINE | 2 | -0.645 | 0.0 | 1.365 |
| GOOD_ENTRY | B_FIXED_3PCT | 2 | 0.341 | 100.0 | 0.0 |
| GOOD_ENTRY | C_TRAIL_30 | 2 | 0.238 | 30.5 | 0.485 |
| GOOD_ENTRY | F_HYBRID_HALF | 2 | -0.645 | 0.0 | 1.365 |
| IDEAL_ENTRY | A_BASELINE | 50 | 0.184 | 68.4 | 0.091 |
| IDEAL_ENTRY | B_FIXED_3PCT | 50 | 0.145 | 100.0 | 0.0 |
| IDEAL_ENTRY | C_TRAIL_30 | 50 | 0.168 | 64.15 | 0.129 |
| IDEAL_ENTRY | F_HYBRID_HALF | 50 | 0.14 | 67.05 | 0.127 |
| SEVERELY_CHASED | A_BASELINE | 3 | -1.326 | — | 0.0 |
| SEVERELY_CHASED | B_FIXED_3PCT | 3 | -1.326 | — | 0.0 |
| SEVERELY_CHASED | C_TRAIL_30 | 3 | -1.326 | — | 0.0 |
| SEVERELY_CHASED | F_HYBRID_HALF | 3 | -1.326 | — | 0.0 |
| UNKNOWN | A_BASELINE | 21 | -1.377 | 100.0 | 0.0 |
| UNKNOWN | B_FIXED_3PCT | 21 | -1.35 | 100.0 | 0.0 |
| UNKNOWN | C_TRAIL_30 | 21 | -1.377 | 100.0 | 0.0 |
| UNKNOWN | F_HYBRID_HALF | 21 | -1.377 | 100.0 | 0.0 |

## Hold or protect? (continuation, past-information only)

extension, premium momentum over the last 3 quotes, give-back so far, quotes since the running peak — all computed from quotes at or before the observation; no future price is used as a feature

**these are in-sample conditional frequencies on one session, NOT probabilities. There is no holdout, so a state that looks informative here may simply be describing this day's path**

Observations: 1512 in-profit quotes · continued 26.7% · reversed to break-even 35.5%

| state (extension · momentum · peak) | observations | continued % | reversed % | median remaining MFE R | label |
| --- | --- | --- | --- | --- | --- |
| EXT_0.5_1R · MOM_DOWN · OFF_PEAK | 37 | 54.1 | 54.1 | 0.419 | HYPOTHESIS |
| EXT_0.5_1R · MOM_FLAT · AT_PEAK | 2 | 0.0 | 0.0 | 0.0 | REQUIRES MORE DATA |
| EXT_0.5_1R · MOM_FLAT · OFF_PEAK | 226 | 0.0 | 2.7 | 0.0 | HYPOTHESIS |
| EXT_0.5_1R · MOM_UP · AT_PEAK | 51 | 43.1 | 56.9 | 0.064 | HYPOTHESIS |
| EXT_0.5_1R · MOM_UP · OFF_PEAK | 63 | 55.6 | 34.9 | 0.885 | HYPOTHESIS |
| EXT_1_2R · MOM_DOWN · OFF_PEAK | 35 | 68.6 | 0.0 | 0.984 | HYPOTHESIS |
| EXT_1_2R · MOM_FLAT · AT_PEAK | 1 | 100.0 | 0.0 | 1.609 | REQUIRES MORE DATA |
| EXT_1_2R · MOM_UP · AT_PEAK | 60 | 85.0 | 20.0 | 0.972 | HYPOTHESIS |
| EXT_1_2R · MOM_UP · OFF_PEAK | 17 | 82.4 | 17.6 | 0.862 | HYPOTHESIS |
| EXT_GE_2R · MOM_DOWN · OFF_PEAK | 40 | 20.0 | 0.0 | -0.008 | HYPOTHESIS |
| EXT_GE_2R · MOM_FLAT · OFF_PEAK | 13 | 0.0 | 0.0 | 0.0 | HYPOTHESIS |
| EXT_GE_2R · MOM_UP · AT_PEAK | 34 | 64.7 | 8.8 | 0.479 | HYPOTHESIS |
| EXT_GE_2R · MOM_UP · OFF_PEAK | 14 | 7.1 | 0.0 | -0.201 | HYPOTHESIS |
| EXT_LT_0.5R · MOM_DOWN · OFF_PEAK | 265 | 24.9 | 44.9 | 0.172 | HYPOTHESIS |
| EXT_LT_0.5R · MOM_FLAT · AT_PEAK | 11 | 45.5 | 81.8 | 0.234 | HYPOTHESIS |
| EXT_LT_0.5R · MOM_FLAT · OFF_PEAK | 171 | 11.7 | 40.9 | 0.074 | HYPOTHESIS |
| EXT_LT_0.5R · MOM_UP · AT_PEAK | 191 | 24.6 | 58.6 | 0.068 | HYPOTHESIS |
| EXT_LT_0.5R · MOM_UP · OFF_PEAK | 281 | 23.8 | 47.0 | 0.147 | HYPOTHESIS |

- separation: continuation frequency varies by 85.0pp across states with 20+ observations — a candidate signal to test, not a probability

## Loss attribution — what actually causes losses?

Resolved 141 · wins 53 · losses 88 · top loss cause: **BAD_STRIKE**

Priority order (first match wins, so this order *is* the definition of primary, and it is a research classification rule with no production counterpart): DATA_QUALITY → WRONG_DIRECTION → BAD_STRIKE → INSUFFICIENT_ROOM → LOW_LIQUIDITY → BAD_SPREAD → ENTRY_CHASE → STOP_TOO_TIGHT → EXIT_TOO_LATE → REGIME_FAILURE → UNKNOWN

| cause | loss (primary) | % of losses | win (primary) | loss flagged | win flagged | flag loss rate % | label |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BAD_STRIKE | 36 | 40.9 | 12 | 51 | 17 | 75.0 | HYPOTHESIS |
| DATA_QUALITY | 18 | 20.5 | 18 | 18 | 18 | 50.0 | HYPOTHESIS |
| INSUFFICIENT_ROOM | 12 | 13.6 | 10 | 57 | 30 | 65.5 | HYPOTHESIS |
| ENTRY_CHASE | 9 | 10.2 | 1 | 44 | 4 | 91.7 | HYPOTHESIS |
| WRONG_DIRECTION | 6 | 6.8 | 0 | 6 | 0 | 100.0 | REQUIRES MORE DATA |
| UNKNOWN | 5 | 5.7 | 8 | 0 | 0 | — | HYPOTHESIS |
| BAD_SPREAD | 1 | 1.1 | 4 | 21 | 12 | 63.6 | REQUIRES MORE DATA |
| EXIT_TOO_LATE | 1 | 1.1 | 0 | 2 | 0 | 100.0 | REQUIRES MORE DATA |
| LOW_LIQUIDITY | 0 | 0.0 | 0 | 7 | 0 | 100.0 | REQUIRES MORE DATA |
| REGIME_FAILURE | 0 | 0.0 | 0 | 0 | 0 | — | REQUIRES MORE DATA |
| STOP_TOO_TIGHT | 0 | 0.0 | 0 | 4 | 0 | 100.0 | REQUIRES MORE DATA |

- signal-side causes (WRONG_DIRECTION, BAD_STRIKE, REGIME_FAILURE, INSUFFICIENT_ROOM) argue for taking fewer trades; execution-side causes (ENTRY_CHASE, BAD_SPREAD, LOW_LIQUIDITY, STOP_TOO_TIGHT, EXIT_TOO_LATE) argue for keeping the same trades and changing the handling. The two must not be averaged into one conclusion.
- label: **HYPOTHESIS** — 1 recorded session(s) — a single session measures that session's character, so any relationship here is a candidate to test over 20+ sessions, not a finding

Does the cause change on expiry day? top cause per class: EXPIRY_DAY → ENTRY_CHASE, NON_EXPIRY → BAD_STRIKE, PRE_EXPIRY → EXIT_TOO_LATE

- the leading cause is not the same in every expiry class on this sample — worth testing, and far too thin to act on

## Contract / strike quality (descriptive only)

Legs recorded 141 · usable greeks 141 · degenerate greek legs 0

| delta band | n | expectancy R | target-before-stop % | label |
| --- | --- | --- | --- | --- |
| delta 0.25-0.45 | 3 | -0.469 | 0.0 | REQUIRES MORE DATA |
| delta 0.45-0.65 | 138 | 0.016 | 9.4 | HYPOTHESIS |

- descriptive only. The strike selector is untouched and no band above is a recommendation; single-day cells cannot rank strikes

## Paper results

- live AI paper book: **0 trades** (0 resolved) — the live paper book is empty, so the ledger below is the replay ledger and is NOT evidence of live paper performance
- replay ledger: 141 trades with entry/exit, both books, spread cost, assumed fees, MFE, MAE, capture, give-back, realised and net R (full rows in the JSON under `paper_ledger`)
- the replay ledger is not live paper performance: no cooldowns, no concurrency limit, no risk governor

## By instrument (context, not a ranking)

| instrument | n | win % | expectancy R | PF | median MFE R | median MAE R | missing bars % |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CRUDEOIL | 46 | 47.8 | -0.001 | 0.992 | 0.093 | -0.099 | 28.19 |
| NIFTY | 23 | 39.1 | -0.073 | 0.899 | 0.909 | -1.014 | 38.03 |
| SILVER | 26 | 11.5 | -0.427 | 0.029 | 0.102 | -0.524 | 38.41 |
| GOLD | 27 | 37.0 | 0.291 | — | 0.0 | 0.0 | 45.92 |
| COPPER | 0 | — | — | — | — | — | — |
| BANKNIFTY | 14 | 42.9 | 0.092 | 1.23 | 0.272 | -0.754 | 45.38 |
| SENSEX | 5 | 60.0 | 0.891 | 22.631 | 1.411 | -0.146 | 69.87 |

## Limits carried into every number above

- premiums are ~60s snapshot closes: a stop or target touch between two snapshots is invisible, so MFE/MAE are snapshot extremes
- spread is measured from the recorded book; brokerage is still the ₹120/round trip assumption and the two are never added into one number
- the reconstruction replays the production engine on stored bars; live cooldowns, concurrency and the risk governor are not applied, so it produces MORE BUYs than the live funnel did — the paper ledger is a replay ledger, not live paper performance
- greeks are taken as recorded; deep-ITM legs carry degenerate iv/delta in this dataset and are excluded from greek aggregates, so delta-derived quantities (room, mirror pricing) are weaker on them
- contract expiry is parsed from the tradingsymbol because the recorded leg has no expiry field; unrecognised grammars are EXPIRY_UNKNOWN and never guessed
- the continuation table is in-sample conditional frequency with no holdout: it is not a probability and cannot be used as one

## The question this phase was set

> On expiry day, can the system identify when a profitable option move still has enough continuation probability to hold, and when the probability of reversal is high enough that profit should be protected?

**Partially, as a candidate.** Continuation frequency varies by 85.0pp across states carrying 20+ observations, which means the states are not interchangeable on this sample. That is a candidate signal to test over many sessions, not a probability, and not a rule: one session cannot establish it.

What would answer it: 20+ sessions including 4+ expiry sessions per instrument, then the same table computed on the earlier sessions and checked on the later ones. Until the check exists, no state may be used to hold or protect a live trade.

## What this changes, and what it does not

**Immediately actionable as an observation (no code change, no rule change):**

- The displayed confidence did not rank outcomes in this sample. Until it does, read it as a description of how many gates passed, not as a probability of profit.
- Spread is real and measurable: the book takes a median 5.1% of the risk unit per round trip on the measured legs, and it is wildly uneven between instruments — that unevenness is visible on the board before you trade.
- Losses concentrate in **BAD_STRIKE**. Whether that is a signal-side or execution-side cause decides which half of the system is worth working on next.

**Needs 20+ sessions (and 4+ expiry sessions) before it can support any change:**

- every policy ranking above, including the candidates table — a within-sample best is the single easiest way to overfit one day;
- any expiry-specific exit behaviour, and any claim that expiry is different;
- any recalibration of the displayed confidence;
- the continuation states as a hold/protect decision.

**Should not be modelled yet:**

- no AI model on this sample: per-cell counts are in single digits and the missing one-minute bar rate is far above the 5% gate, so ATR/VWAP/regime features are contaminated;
- greeks-derived features need the degenerate-leg problem fixed at the recording end first;
- no feature may be called predictive from one day, however large its apparent effect.

## Production confirmation

- buy logic unchanged: **yes**
- production exits unchanged: **yes**
- production stops unchanged: **yes**
- production targets unchanged: **yes**
- strike selection unchanged: **yes**
- gates and thresholds unchanged: **yes**
- chase guard unchanged: **yes**
- ai paper only: **yes**
- real money execution disabled: **yes**
- expiry rule wired into production: **no**
- enforcement: Phase 8 lives in app/research/phase8 and is imported by phase8_report.py only; no production module imports it, and it holds no order-placing code path
- no production change is authorised by this phase

status=EXPLORATORY_ONLY resolved=141 sessions=1 expiry_day_trades=23 calibration_failure=True top_loss_cause=BAD_STRIKE failed_gates=REAL_SESSIONS,ADEQUATE_EXPIRY_SESSIONS,EXPIRY_TRADE_COUNT,CELL_SIZE
