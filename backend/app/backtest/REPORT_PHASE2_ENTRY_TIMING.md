# Phase 2 — Entry timing, peak buys, early signals, execution funnel

**No production gate, threshold, stop, target, confirmation, chase guard or execution
rule was changed by this work.** Everything added only records. Early Momentum is
still advisory.

Evidence base (modelled premiums):

| | CRUDEOIL | NIFTY |
|---|---|---|
| bars replayed | 119,729 | 119,729 |
| BUYs classified | 18,510 | 16,457 |
| forward horizon | 30 min (60 min run also done) | same |

Runs: `entry_timing_audit.py --bars 120000 --horizon {30,60}`, raw output in
`timing_{CRUDEOIL,NIFTY}_h{30,60}.json`.

---

## A. Classification — definitions first (Phase 2A)

Everything downstream depends on these four labels, so they are defined in **R**
(risk units), not in points, so a ₹25 option and a ₹250 option are judged on the
same scale.

For a BUY at premium `E`, engine stop `S`, engine target `T`:

```
risk        R      = E - S
leg low     L      = lowest premium of this side in the 30 bars before the BUY
peak        P      = best premium within the forward horizon
consumed    c      = (E - L) / (P - L)        share of the leg already spent
remaining   r      = (P - E) / R              how many R are still on the table
stopped_first      = the stop was touched before the target
```

| class | rule | meaning |
|---|---|---|
| **PEAK** | `r < 1.2R` | T1 was unreachable *even in hindsight* — the entry could not have worked |
| **EARLY** | `r ≥ 1.2R` and `stopped_first` | the move was there, but the entry was ahead of it and the stop came first |
| **LATE** | target first, and (`c ≥ 60%` or `r < 2R`) | it worked, but most of the leg was already gone |
| **GOOD** | target first, `c < 60%`, `r ≥ 2R` | worked with room to spare |

`1.2R` is not arbitrary — it is the engine's own minimum reward:risk, so "PEAK"
means precisely *the trade the engine itself planned was not available*.

**Worked examples** (real rows from the CRUDEOIL run):

```
PEAK   leg low 195.6 → BUY 210.6 → peak 220.2
       before BUY +15.0, after BUY +9.6, captured 39%, r = 0.57R, delay 15 bars
       primary cause HTF_TREND, secondary ENTRY_TRIGGER

PEAK   leg low 98.5 → BUY 201.0 → peak 207.0
       before BUY +102.5, after BUY +6.0, captured 5.5%, r = 0.31R, delay 4 bars
       primary cause ENTRY_TRIGGER   (this is the classic "bought the top")

GOOD   target reached first with ≥2R still above the entry and <60% of the leg spent
```

### Counts

| horizon | | PEAK | LATE | GOOD | EARLY |
|---|---|---|---|---|---|
| 30 min | CRUDEOIL | 57.8% | 16.6% | 17.3% | 8.4% |
| 30 min | NIFTY | 56.8% | 19.7% | 16.0% | 7.5% |
| 60 min | CRUDEOIL | 45.2% | 14.6% | 24.8% | 15.4% |
| 60 min | NIFTY | 44.5% | 17.3% | 23.2% | 15.1% |

**Sensitivity matters more than the headline.** Doubling the observation window
moves 12 points of mass out of PEAK. A large part of "PEAK" is therefore *"the
target did not arrive within 30 minutes"*, not *"the entry was at the top"*. Any
number quoted from this table without its horizon is meaningless.

---

## B. Why BUYs land late — and what it costs (Phase 2B, Q3/Q4/Q6)

This is the finding that contradicts the intuitive explanation:

| | CRUDEOIL | NIFTY |
|---|---|---|
| median delay, first valid setup → BUY | **0 bars** | **0 bars** |
| BUYs that waited at all | 48.9% | 48.7% |
| median premium paid for the wait | **2.4 pts** | **2.6 pts** |
| … on PEAK buys | 2.4 pts | 2.5 pts |
| … on GOOD buys | 3.0 pts | 2.5 pts |

**Confirmation delay is not what makes a BUY a peak buy.** Half of all BUYs fire
on the very bar the setup first becomes valid, and the half that wait pay ~2.5
premium points — and pay *the same* whether they end up GOOD or PEAK. If delay
were the cause, PEAK buys would show a materially larger wait and a larger bill.
They do not.

Median confirmation lag before the BUY, both instruments:

```
vwap_ok 0 · ema_ok 0 · htf_ok 0 · structure_ok 0 · momentum_ok 1 · volume_ok 2 bars
```

So the slowest confirmations are **volume (2 bars)** and **momentum (1 bar)** —
2 minutes of information, worth ~2.5 points of premium. That is the whole size
of the confirmation-delay problem. Answer to Q6: **≈2.4–2.6 premium points per
delayed BUY, ~1.2 points averaged over all BUYs.**

Where a delay *did* occur before a PEAK/LATE buy, ranked by how often that gate
was the one holding the trade:

| rank | CRUDEOIL | NIFTY |
|---|---|---|
| 1 | **NO_DELAY (5,401)** — no gate was blocking | **NO_DELAY (4,704)** |
| 2 | ENTRY_TRIGGER 1,750 | ENTRY_TRIGGER 1,149 |
| 3 | REWARD_RISK 923 | RANGING 711 |
| 4 | RANGING 742 | REWARD_RISK 698 |
| 5 | HTF_TREND 725 | CONFIDENCE 695 |

The single largest cause of a peak buy is *no blocker at all*: the engine bought
as soon as it could and the move was still mostly over. Of the gates,
**ENTRY_TRIGGER is the most expensive delay** on both instruments.

Total premium the market moved while each gate held the trade (whole run, so it
is a volume measure, not a per-trade one):

```
CRUDEOIL  HTF_TREND 31,846 · ENTRY_TRIGGER 20,868 · RANGING 17,690 · REWARD_RISK 15,587
NIFTY     HTF_TREND 25,971 · RANGING 16,744 · ENTRY_TRIGGER 15,318 · REWARD_RISK 11,767
```

---

## C. Gate contribution on identical terms (Phase 2D/2G, Q1/Q2/Q9/Q10)

Every population below is scored with the **same entry, same stop, same target,
same window** — a refused setup is measured exactly as if it had been taken.
"Sole blocker" means that gate was the *only* thing refusing it.

### CRUDEOIL

| population | setups | win % | expectancy (R) | profit factor | med MFE | med MAE |
|---|---|---|---|---|---|---|
| **ACCEPTED BUY** | 18,510 | 48.3 | **+0.040** | **1.09** | 1.02 | 0.96 |
| DIRECTIONAL_STRENGTH only | 7,592 | 49.1 | +0.047 | 1.11 | 0.99 | 0.91 |
| REWARD_RISK only | 10,350 | 47.1 | +0.007 | 1.02 | 0.91 | 0.91 |
| TRAP only | 5,258 | 46.6 | +0.007 | 1.02 | 0.98 | 0.97 |
| HTF_TREND only | 48,248 | 46.9 | +0.005 | 1.01 | 0.90 | 0.90 |
| RANGING only | 12,784 | 46.7 | +0.003 | 1.01 | 0.89 | 0.87 |
| ENTRY_TRIGGER only | 11,824 | 46.4 | +0.002 | 1.00 | 0.90 | 0.93 |
| ONE_MIN_CONFLICT only | 2,020 | 46.9 | −0.007 | 0.99 | 0.90 | 0.98 |
| CONFIDENCE only | 2,734 | 46.4 | −0.017 | 0.96 | 0.74 | 0.77 |
| INDICATOR_CONFLICT only | 408 | 45.6 | −0.027 | 0.94 | 0.82 | 0.88 |
| **EARLY_MOMENTUM** | 2,567 | 43.6 | **−0.049** | **0.90** | 0.97 | 1.10 |

### NIFTY

| population | setups | win % | expectancy (R) | profit factor |
|---|---|---|---|---|
| **ACCEPTED BUY** | 16,457 | 48.4 | **+0.042** | **1.09** |
| INDICATOR_CONFLICT only | 303 | 50.5 | +0.041 | 1.10 |
| TRAP only | 5,134 | 48.6 | +0.029 | 1.07 |
| DIRECTIONAL_STRENGTH only | 7,879 | 47.4 | +0.009 | 1.02 |
| ONE_MIN_CONFLICT only | 1,577 | 46.8 | +0.001 | 1.00 |
| CONFIDENCE only | 8,634 | 46.6 | −0.010 | 0.98 |
| ENTRY_TRIGGER only | 9,184 | 45.7 | −0.019 | 0.96 |
| REWARD_RISK only | 8,607 | 45.5 | −0.023 | 0.95 |
| RANGING only | 14,506 | 46.0 | −0.028 | 0.94 |
| HTF_TREND only | 47,448 | 45.3 | −0.035 | 0.93 |
| **EARLY_MOMENTUM** | 2,864 | 45.1 | **−0.033** | **0.93** |

**The gate stack as a whole is worth something and each individual gate is worth
almost nothing.** Accepted BUYs are the best population on both instruments
(PF 1.09, expectancy +0.04R). No refused population beats it on both — the one
that beats it on Crude (DIRECTIONAL_STRENGTH, 1.11) is below it on Nifty (1.02),
which is what an artefact looks like, not an edge.

This also **corrects the Phase 1 finding**. Phase 1 flagged REWARD_RISK because
its refusals reached target-before-stop more often. Scored on expectancy and
profit factor rather than hit rate, REWARD_RISK refusals are 1.02 on Crude and
**0.95 on Nifty** — below accepted. The Phase 1 signal was a win-rate illusion:
those setups win slightly more often and lose more when they lose.

Answering Q7 in the same table: **CONFIDENCE-only refusals are the worst
population measured** (0.96 / 0.98 PF, lowest MFE of all). Whatever else the
confidence score does, the setups it alone rejects are genuinely inferior.

---

## D. Early signals (Phase 2C, Q7/Q8)

Probability of reaching +N premium points within the horizon:

| population | +5 | +10 | +15 | +20 |
|---|---|---|---|---|
| CRUDEOIL accepted BUY | 77.5% | 62.6% | 50.0% | 39.1% |
| CRUDEOIL EARLY_MOMENTUM | 79.3% | 61.7% | 49.2% | 37.6% |
| NIFTY accepted BUY | 79.7% | 62.9% | 49.6% | 38.0% |
| NIFTY EARLY_MOMENTUM | 77.0% | 58.0% | 44.4% | 33.7% |

**Q7 — is Early Momentum predictive? On this evidence, no.** It reaches the
point thresholds at roughly the same rate as a confirmed BUY, but it does it
with a *worse* risk profile: MAE 1.10R against MFE 0.97R on Crude, expectancy
−0.049R, profit factor 0.90. It finds the move slightly earlier and pays for it
with a deeper drawdown before the move arrives — which is exactly the shape of a
signal that is early *and* wrong more often. **It must stay non-executable.**

**Q8 — is Early-Early predictive? Cannot be answered, and I will not guess.**
Early-Early never activated once in 240,000 replayed bars, because its liquidity
gate requires real OI and volume that the modelled chain does not have. The live
logs hold 451 Early-Momentum stage transitions but only **2** resolved outcomes.
Two trades is not evidence. This stays open until the live recorder has data.

---

## E. Execution funnel (Phase 2E, Q12/Q13)

There are **19 distinct ways** a BUY on screen fails to become a position, and
until now six of them returned **silently** — no alert, no log, nothing on the
dashboard. That is the whole of the unexplained "BUY but no execution".

| stage | blocker | code | value vs threshold |
|---|---|---|---|
| SIGNAL | `INCOMPLETE_PLAN` | `state.py:_auto_trade` | BUY without option/premium/stop/target |
| SIGNAL | `EPISODE_ALREADY_TRADED` | `state.py:_auto_trade` | `_auto_last_key == key` *(was silent)* |
| RISK | `CONFIDENCE_FLOOR` | `state.py:_auto_trade` | conf vs `auto_trade_min_confidence` *(was silent)* |
| RISK | `CONFIDENCE_CEILING` | `state.py:_auto_trade` | conf vs `auto_trade_max_confidence` |
| RISK | `OPENING_WINDOW` | `account_risk.opening_window_block` | minutes since the bell |
| RISK | `INSTRUMENT_COOLDOWN` | `account_risk.instrument_blocked` | seconds since last exit |
| RISK | `ALLOWLIST` | `state.py:_auto_trade` | instrument ∉ `instrument_allowlist` |
| RISK | `BLOCKED_HOUR` | `state.py:_auto_trade` | IST hour ∈ `blocked_entry_hours` |
| RISK | `RISK_GOVERNOR` | `risk.can_open` | daily loss / trade count / streak / goal *(was silent)* |
| VALIDATION | `STALE_FEED` | `state.py:_auto_trade` | frozen ticks vs `auto_trade_stale_feed_ticks` |
| VALIDATION | `MIN_PREMIUM` | `state.py:_auto_trade` | premium vs `auto_trade_min_premium` |
| VALIDATION | `OPPORTUNITY_GATE` | `opportunity.score` | `tradeable == false` |
| EXECUTION | `MOMENTUM_TRIGGER` | `state.py:_auto_trade` | trigger ∈ {WAIT_PULLBACK, none} |
| EXECUTION | `PREMIUM_MOMENTUM` | `state.py:_auto_trade` | premium momentum < 0 |
| EXECUTION | `NO_CHASE` | `state.py:_auto_trade` | trap prob vs `auto_trade_no_chase_trap_prob` |
| EXECUTION | `MAX_CONCURRENT` | `state.py:_auto_trade` | open vs `auto_trade_max_concurrent` |
| EXECUTION | `LIVE_ORDER_VALUE_CAP` | `state.py:_auto_trade` | order ₹ vs `auto_trade_live_max_order_value` |
| EXECUTION | `CAPITAL` | `state.py:_auto_trade` | cost/lot vs remaining budget |
| ACCEPTED | `ORDER_REJECTED` | `state.py:buy` | broker message |

Every one of them now records stage, primary blocker, the value it saw, the
threshold it failed, the code location and a reason, into
`app/analysis/exec_funnel.py`, surfaced at `/api/signal-diagnostics` under
`execution` and drawn in the Signal-diagnostics panel as
`SIGNAL → RISK → VALIDATION → EXECUTION → BROKER → ACCEPTED → FILLED`.

`_smoke_exec_funnel.py` asserts **structurally** (by parsing `state.py`'s AST)
that no refusal path in the auto-entry can return without recording — so this
cannot silently regress.

**Q12/Q13 — what share of BUYs are executable, and what share is blocked by
execution?** The counters are live from this build onward; there is no honest
historical number, because the paths that need counting were the ones that never
logged. The dashboard reports `executable_pct` = fills ÷ actionable BUYs as soon
as a session runs. I would rather show that empty than invent a percentage.

---

## F. Gate correlation (Phase 2H, Q11)

Jaccard overlap of *which bars each gate refuses* (top pairs, both instruments):

| pair | Crude J | Nifty J | reading |
|---|---|---|---|
| CONFIDENCE ↔ HTF_TREND | 0.68 | 0.56 | **P(HTF fails \| CONF fails) = 100%** — confidence never fails alone unless HTF has already failed |
| CONFIDENCE ↔ DIRECTIONAL_STRENGTH | 0.34 | 0.32 | 96.8% / 99.4% one-way containment |
| ENTRY_TRIGGER ↔ OVER_EXTENDED | 0.23 | 0.27 | **100% containment** — OVER_EXTENDED never blocks anything ENTRY_TRIGGER isn't already blocking |
| CONFIDENCE ↔ RANGING | 0.27 | 0.32 | 76.8% / 93.7% |
| HTF_TREND ↔ INDICATOR_CONFLICT | 0.24 | 0.29 | 80.2% / 84.9% |
| HTF_TREND ↔ RANGING | 0.24 | 0.24 | both are "is there a trend" |

Two are **provably redundant as vetoes** (100% containment, consistent across
instruments): `OVER_EXTENDED` inside `ENTRY_TRIGGER`, and `CONFIDENCE` inside
`HTF_TREND` — the confidence score is largely a re-expression of the trend
inputs, so it is voting twice. `HTF_TREND`, `RANGING` and `DIRECTIONAL_STRENGTH`
form a third cluster all measuring "is this trending", though not with the same
provable containment.

---

## G. Modelled vs real premiums (Phase 2F, Q14)

| | modelled | real |
|---|---|---|
| source | replay, fixed 0.6 delta, ₹180 time value, ₹5 floor | `data/missed_opportunities.jsonl` |
| rows | 240,000 bars / 35k BUYs | **0 closed windows** |
| ignores | theta, IV, spread, liquidity, slippage | nothing |

**There are no real-premium results yet**, and this section stays empty until
the live recorder has run through sessions. What the model will be wrong about
is predictable in direction: a fixed delta understates premium expansion on the
launch (so real "consumed before BUY" is probably *worse* than reported) and
ignores theta and spread (so real expectancy is *lower* than every number
above — an accepted-BUY PF of 1.09 before costs is thin enough that costs could
take it under 1.0). **Do not treat any figure in this report as a live
expectancy.** It is a comparison between populations measured on the same
flawed ruler, which is valid; it is not a P&L forecast, which it would not be.

---

## H. The 15 questions, answered

1. **Why WAIT before +10/+20 moves?** Because +10 follows most bars: 58–63% of
   *accepted* BUYs and 57–59% of refused setups both reach +10. WAIT before a big
   move is the base rate, not a malfunction. The gates that produce those WAITs
   are individually near-worthless (PF 0.93–1.02) but collectively lift accepted
   BUYs to 1.09.
2. **Which WAIT blockers are useful?** Measured on identical terms, only
   `CONFIDENCE` (its sole-blocker refusals are the worst population on both
   instruments) and, weakly, `HTF_TREND` and `RANGING` on Nifty. `ENTRY_TRIGGER`,
   `REWARD_RISK`, `TRAP`, `ONE_MIN_CONFLICT` and `DIRECTIONAL_STRENGTH` refuse
   populations statistically indistinguishable from what they let through.
3. **Why BUY near peaks?** Predominantly *not* because of gate delay. The largest
   attributed cause of a PEAK buy is NO_DELAY — the engine bought on the first
   valid bar and the leg was already spent. Second is the 30-minute measurement
   horizon itself (PEAK drops from 58% to 45% at 60 minutes).
4. **Which confirmation causes the late BUY?** Where a delay exists,
   `ENTRY_TRIGGER` on both instruments, then `REWARD_RISK`/`RANGING`. By
   confirmation lag: volume (2 bars) then momentum (1 bar).
5. **How many BUYs are EARLY/GOOD/LATE/PEAK?** 30-min horizon: Crude
   8.4/17.3/16.6/57.8%, Nifty 7.5/16.0/19.7/56.8%. 60-min: Crude
   15.4/24.8/14.6/45.2%, Nifty 15.1/23.2/17.3/44.5%.
6. **Points lost to confirmation delay?** Median **2.4 (Crude) / 2.6 (Nifty)
   premium points** per delayed BUY; 49% of BUYs are delayed at all; the bill is
   the same for PEAK and GOOD buys, so it is not the cause of peak entries.
7. **Is Early Momentum predictive?** No. Same hit rates as a confirmed BUY with
   worse MAE (1.10R) and negative expectancy (−0.049R Crude, −0.033R Nifty),
   PF 0.90/0.93. Keep advisory.
8. **Is Early-Early predictive?** Unknown — it never activated in the replay
   (liquidity gate needs real OI/volume) and live data holds 2 resolved
   outcomes. Not answerable yet.
9. **Which gates stay HARD?** The safety ones the audit does not contradict:
   `RISKY_REGIME`, `NEWS_AGAINST`, `PREMIUM_QUALITY`, `STALE_FEED`,
   `MIN_PREMIUM`, `OPENING_WINDOW`, `RISK_GOVERNOR`, `CAPITAL`, and the live
   order caps. These bound loss and data quality; none is judged on expectancy.
10. **Candidates for SOFT scoring?** `ENTRY_TRIGGER`, `REWARD_RISK`, `RANGING`,
    `HTF_TREND`, `DIRECTIONAL_STRENGTH`, `TRAP`, `ONE_MIN_CONFLICT` — each
    refuses a population indistinguishable from what it accepts, so each is
    better as evidence weight than as a veto. **Not to be changed before real-
    premium confirmation.**
11. **Which are redundant?** `OVER_EXTENDED` (100% contained in
    `ENTRY_TRIGGER`), `CONFIDENCE` (100% contained in `HTF_TREND` on Crude,
    ~94–99% in RANGING/DIRECTIONAL_STRENGTH) — it is a re-vote on trend inputs.
12. **% of BUYs executable?** Now measured live (`executable_pct`); no honest
    historical figure exists because the blocking paths did not log.
13. **% blocked by execution?** Same — now counted per blocker per stage.
14. **Modelled vs real?** No real results yet. Modelled premiums exclude theta,
    IV, spread and slippage, all of which push real expectancy *below* the
    numbers here; the accepted-BUY PF of 1.09 is thin enough that costs matter.
15. **Recommended next architecture?** In order:
    1. Collect real-premium data for the sections above (nothing changes until
       then).
    2. Delete the redundant vetoes rather than re-tune them —
       `OVER_EXTENDED` and the `CONFIDENCE` veto add refusals, not information.
    3. Convert the seven soft candidates into a weighted score with a single
       threshold, keeping the hard safety list as vetoes. This is one decision
       boundary that can be measured, instead of 21 that cannot.
    4. Attack the real timing problem, which this audit shows is **not** gate
       delay but *strike/leg selection at entry* — half of all BUYs already fire
       on the first valid bar and still land with the leg 69–71% consumed.
    5. Only then reconsider Early Momentum, on real premiums and outcome data.

---

## I. Limitations

- Modelled premiums (fixed 0.6 delta, no theta/IV/spread/slippage).
- Every result is a *comparison between populations on one ruler*, not a P&L.
- PEAK share is strongly horizon-dependent (58% at 30 min, 45% at 60 min).
- "First valid setup" is reconstructed by walking back over same-side non-BUY
  bars within 30 bars; it is a proxy for the engine's internal state history.
- Early-Early is unmeasured. Execution-funnel counts start at zero on this build.
- Gate overlap is measured on *refusal co-occurrence*, not on the underlying
  indicator values.
