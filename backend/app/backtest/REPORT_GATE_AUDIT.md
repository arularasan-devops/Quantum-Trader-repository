# Gate audit — why the engine says WAIT while the move happens

Phase 1–4 of the requested audit: architecture map, gate inventory, funnel
measurement, missed-opportunity analysis. **No gate was removed, loosened or
re-ordered.** Everything added is observational.

Evidence tiers are marked throughout:

* **[M]** measured in this audit, on real cached Angel candles
* **[L]** from existing runtime logs
* **[C]** read from code
* **[X]** not measurable yet — instrumentation missing, listed as such

---

## A. Architecture — the actual path a price takes to a BUY

```
Angel feed (WebSocket push, REST fallback)        market/angelone.py
  └─ 1-min futures candles + option chain         market/provider.py
      └─ compute_indicators()                     engine/decision.py
          └─ classify_market()  → regime          engine/decision.py
              └─ decide()       → Signal + levels engine/decision.py   ← LAYER 1
                  └─ signal_gate.evaluate()       engine/signal_gate.py ← LAYER 2
                      └─ AppState._auto_trade()   state.py              ← LAYER 3
                          └─ paper/live order
```

Three independent layers can each refuse a trade, and **only layer 1 was ever
visible on the dashboard**. [C]

| Layer | Where | Refuses by |
|---|---|---|
| 1 Engine | `decide()` | 15 conditions (below) |
| 2 Board | `signal_gate.evaluate()` | regime, ADX, expected move, 5-min agreement, 15-min bias, opportunity |
| 3 Auto-trade | `state._auto_trade()` | confidence floor/ceiling, opening window, stale feed, min premium, opportunity, cooldown, allowlist, blocked hours, momentum trigger, falling premium, no-chase, risk governor, max positions, capital |

Advisory engines (Early Momentum, Early-Early, Flow, Scalp) run **beside** this
path and never feed it. [C]

## B. Gate inventory (layer 1, in evaluation order)

`RISKY_REGIME · HTF_TREND · RANGING · TRAP · ONE_MIN_CONFLICT ·
DIRECTIONAL_STRENGTH · ENTRY_TRIGGER · OVER_EXTENDED · PREMIUM_EXPLOSION ·
INDICATOR_CONFLICT · CONFIDENCE · REWARD_RISK · NEWS_AGAINST · PREMIUM_QUALITY ·
TRAP_PROBABILITY`

Each is now captured per decision in `GateTrace` with the ranked primary and
secondary blocker (`app/models.py`, `engine/decision.py`). Layer 2 appends its
six board gates. Layer 3 is **not** yet in the trace — see §N. [C]

## C. Funnel — measured [M]

Replay of the frozen engine over cached 1-min candles, every 5th bar, 30-bar
forward window. 23,946 decisions per instrument.

| | CRUDEOIL | NIFTY |
|---|---|---|
| BUY | 15.5% | 13.4% |
| WAIT | 27.7% | 28.5% |
| AVOID | 56.8% | 58.0% |

Primary blocker on refused bars (CRUDEOIL):

| Gate | Primary blocker | Fails at all |
|---|---|---|
| HTF_TREND | 9,583 | 9,583 |
| RANGING | 2,574 | 5,493 |
| ENTRY_TRIGGER | 2,372 | 7,244 |
| REWARD_RISK | 2,069 | 2,069 |
| DIRECTIONAL_STRENGTH | 1,562 | 5,056 |
| TRAP | 1,027 | 1,579 |
| CONFIDENCE | 549 | 14,207 |

**HTF_TREND alone accounts for 47% of all refusals.** CONFIDENCE fails on 59% of
bars but is rarely the *first* failure, so removing it would change almost
nothing on its own. NIFTY is the same shape (HTF_TREND 9,509 primary).

`NEWS_AGAINST`, `PREMIUM_QUALITY`, `TRAP_PROBABILITY`, `RISKY_REGIME` and
`PREMIUM_EXPLOSION` **never fired once in 47,892 decisions** — in this replay
they are dead code paths (no news feed and a modelled chain, so treat as
"untested here", not "useless"). [M]

## D. The missed +10 / +20 — and why the framing is wrong [M]

Refused bars that went on to move ≥10 premium-equivalent points (CRUDEOIL):
5,605 of 9,583 HTF_TREND refusals (58%); ≥20 points: 3,381 (35%).

That looks damning until the base rate is applied: **the median forward move
after *any* bar is 13.2 points.** A ≥10-point excursion follows most bars,
including the ones the engine traded. Ranked by median forward move, blocked
bars (10.2–14.7) and taken bars (14.4) are barely distinguishable.

**Conclusion: "the market moved 10–20 points after a WAIT" is the normal state
of the market, not evidence of a missed trade.** The question that separates a
good refusal from a bad one is whether the move came *before* the stop, which is
§E.

## E. Does a gate refuse worse setups? [M]

Every refused bar is scored on the engine's own discipline — an 8% premium stop
and a 1.2 R target — and the *taken* BUYs are re-scored on those same levels, so
the two populations are comparable. Target-before-stop within 30 bars:

| Population | CRUDEOIL | NIFTY |
|---|---|---|
| **Taken BUYs** | **46.5%** | **46.1%** |
| Refused, sole blocker REWARD_RISK | 55.8% (n=2,069) | 53.7% (n=1,605) |
| Refused, sole blocker ENTRY_TRIGGER | 44.0% (n=1,547) | 45.1% (n=989) |
| Refused, sole blocker TRAP | 45.0% (n=204) | 42.5% (n=89) |
| Refused, sole blocker RANGING | 43.1% (n=890) | 44.7% (n=272) |
| Refused, sole blocker CONFIDENCE | 42.6% (n=549) | 44.3% (n=1,640) |
| Refused, sole blocker DIRECTIONAL_STRENGTH | 41.1% (n=66) | 72.7% (n=13) |
| Refused, sole blocker ONE_MIN_CONFLICT | 28.6% (n=14) | 0% (n=2) |

Read carefully:

1. **Most gates refuse setups that are statistically indistinguishable from the
   ones they let through** (41–45% vs 46%). They cost trades and buy ~1–4
   percentage points of quality.
2. **REWARD_RISK refuses the *better* population** on both instruments — 8–10
   points *better* than the trades taken. It is the strongest candidate for
   demotion to a score. One caveat that must not be dropped: those bars are by
   construction the ones whose structural target was near, and the
   counterfactual gives them a *nearer* 1.2 R target. So the honest reading is
   "these setups work at a smaller target", not "this gate is worthless".
3. **ONE_MIN_CONFLICT is the only gate refusing clearly worse setups**, on a
   sample far too small to act on (n=14 / n=2).

Median-MFE discrimination per gate is ~zero across the board: the largest edge
of any gate is CONFIDENCE at **+1.9 points** of median forward move. No gate
separates big movers from small ones. [M]

## F. Existing runtime evidence [L]

* `signal_log.json` — 272 tracked BUYs: TARGET 56, STOP 93, EXPIRED 123 →
  **37.6% of resolved signals reached target first.**
* `flow_signals.jsonl` — 1,119 rejected / 50 confirmed. Rejected: 130 reached
  ≥10 pts, mean final −0.2, win 31.4%. Confirmed: win 28.0%. The confirmation
  step is not improving on what it rejects.
* `early_momentum_transitions.jsonl` — 451 rows; reconstructed early→confirm
  delay median 140 s, ~0.67% premium given up. Small sample, reconstruction not
  yet validated — do not quote as a headline.
* `momentum_outcomes.jsonl` — 2 rows. Not usable.

## G. Latency and data quality [X]

Exchange, broker, receive, calculation and render timestamps are **not
recorded**; only WebSocket drop/rebuild counters exist (`/api/feed-health`). The
7-second REST stall found in the user's log is the only measured latency figure.
Candle aggregation, duplicate/out-of-order ticks and partial-candle use were
**not** verified in this pass. Not measurable without new instrumentation.

## H. Look-ahead check [C]

The replay feeds `candles[:i+1]` and scores on `candles[i+1:]`, so the audit
itself is clean. In production `decide()` reads the forming candle, which is
consistent between live and replay but means the last bar is partial in both.

## I. What is now instrumented (this change)

* `GateTrace` on every `Decision` — 21 gate booleans, ranked primary/secondary
  blocker, confidence vs gate, R:R, directional strength.
* `analysis/missed.py` — follows the leg every refusal *would* have bought for
  30 minutes and logs MFE/MAE against the blocking gate
  (`data/missed_opportunities.jsonl`, `QT_MISSED_OPPORTUNITY_LOG`).
* `GET /api/signal-diagnostics` + a Signal Diagnostics panel in Research.
* `funnel_audit.py` — the offline replay that produced §C–§E.

None of it can change a decision; `_smoke_gate_trace.py` (74 checks) pins that.

## J. Proposed architecture — hard gates vs soft score

Not implemented. Proposal only, for approval:

* **Hard (safety, keep absolute):** RISKY_REGIME, PREMIUM_QUALITY, liquidity /
  spread, stale feed, risk governor, capital and position limits.
* **Soft (score, currently hard):** HTF_TREND, RANGING, ENTRY_TRIGGER,
  CONFIDENCE, DIRECTIONAL_STRENGTH, REWARD_RISK, TRAP, INDICATOR_CONFLICT —
  each contributes to a probability rather than a veto.

Evidence for the split is §E: these gates cost 41–56% of the refused population
to buy ≤4 points of win-rate quality.

## K. AI layer — only if the above is approved

Target `P(target before stop)` on the existing gate-trace features, tabular
baseline first (LightGBM), chronological split, walk-forward, no shuffling.
There is **not yet enough live labelled data** — 272 signal-log rows. The offline
replay can supply ~48k labelled rows per instrument today, but on modelled
premiums, so a model trained on it must be validated on live traces before it
touches production.

## L. Backtest plan

Expectancy, profit factor, drawdown, Sortino, MFE/MAE, after costs — never win
rate alone. Break down by regime, time of day, expiry, instrument, CE/PE.

## M. Honest limits of these numbers

* Premiums are **modelled** from the underlying at a fixed 0.6 delta. No theta,
  no spread, no IV move, no liquidity. Every figure in §C–§E is directional.
* Overlapping windows: adjacent bars are not independent, so treat rates as
  descriptive, not significant.
* When one bar spans both stop and target, **stop is assumed first**.
* Layer-3 (auto-trade) refusals are not in these counts at all.

## N. Gaps still open

1. Layer-3 blockers are not in the trace — a BUY on screen that never fills
   still cannot be attributed.
2. No timestamp chain, so end-to-end latency is unmeasured.
3. Chase-guard formula not separately audited (it never fired in replay).
4. Real option-chain history is not stored; until it is, every premium-level
   conclusion rests on a model.

## O. Recommended next step

Run this build for a week to accumulate **live** gate traces and missed-
opportunity records, then re-run §E on real premiums. If the live data agrees
with the replay — that most gates buy ~2 points of quality for half the trades —
the case for converting them to a score is evidential rather than theoretical.
No production gate should move before that.
