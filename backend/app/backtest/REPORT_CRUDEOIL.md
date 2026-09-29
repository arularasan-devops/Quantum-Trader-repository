# Candle-Flow Backtest — CRUDEOIL (1 year, 1-minute)

**Data:** real Angel One `getCandleData` history for the front-month CRUDEOIL
future, **2025-08-04 → 2026-08-04**, **211,895 one-minute candles** (downloaded
in rate-limited chunks, de-duplicated, cached offline).

**Method:** the real `app.execution.flow.detect` decision logic (the exact
"brain" of the live Flow tab) is replayed bar-by-bar; every BUY / SWITCH it would
have fired is opened and tracked to its EXIT / SWITCH exactly like the live
`flow_tracker`. The outcome is measured on the **real underlying futures move**
between entry and exit, signed for the chosen side (CE = +up, PE = +down). Option
premium P&L is only *modelled* (fixed delta, no theta) and is illustrative, not
measured — real option trading would be **worse** (theta + bid/ask spread).

## Headline result (default config: body 0.30, confirm 2, giveback 8)

| metric | value |
|---|---|
| trades taken | 27,137 (8,189 were SWITCHes) |
| win rate | **32.2%** |
| avg / median per trade | −0.86 / −2.0 pts |
| best / worst | +532 / −597 pts |
| total | **−23,298 pts** |
| profit factor | **0.83** |

## Config sweep (same year, underlying move)

| config | trades | win% | avg | total | PF |
|---|---|---|---|---|---|
| loose (0.10/1) | 86,363 | 32.3 | −0.46 | −39,599 | 0.87 |
| default (0.30/2) | 27,137 | 32.2 | −0.86 | −23,298 | 0.83 |
| steady (0.35/3) | 8,976 | 32.3 | −0.97 | −8,742 | 0.82 |
| strict (0.45/3) | 7,486 | 32.1 | −1.17 | −8,742 | 0.79 |
| very strict (0.50/4) | 2,149 | 33.6 | −1.36 | −2,932 | 0.75 |

## Trend-aligned filter (only take entries agreeing with an EMA trend)

| config | trades | win% | avg | total | PF |
|---|---|---|---|---|---|
| raw | 27,137 | 32.2 | −0.86 | −23,298 | 0.83 |
| EMA20/60 | 14,381 | 32.2 | −0.96 | −13,795 | 0.82 |
| EMA30/120 | 14,114 | 31.9 | −0.98 | −13,784 | 0.81 |
| EMA60/240 | 13,870 | 31.7 | −0.96 | −13,277 | 0.82 |

## Honest conclusion

Over the past year on CRUDEOIL, the **raw 1-minute Candle-Flow entry has no
measured directional edge** — win rate stays ~32% and profit factor stays below
1.0 in **every** configuration and **even with a higher-timeframe trend filter**.
Stricter settings only lose less by trading less; they do not create an edge.
The exit rules (giveback / reversal) limit damage but cannot rescue a coin-flip
entry, and real option costs (theta, spread) would make live results worse.

**Implication for the tool:** Flow is useful only as a *fast manual alert* during
clearly trending / news-driven sessions where a human applies discretion — not as
a mechanical signal to trade every fire. This is exactly the kind of validation
the Command Center's "win rate / probability" panels should show as **measured**
numbers, replacing the "calibrating" placeholders, so no one trades it expecting
an edge it does not have.

*Numbers are reproducible offline via `backtest_run.py` / `backtest_variants.py`
/ `backtest_trend.py` on the cached candle file.*
