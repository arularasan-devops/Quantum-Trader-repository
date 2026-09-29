# Pattern → Next-Move Hit-Rate Study (1 year, 1-minute, real Angel data)

**Question:** can a candlestick pattern predict the next move — "will the next
candle be green/red?" or "will this pattern repeat / continue?"

**Method:** for every 1-min candle we ran the live engine's own
`candle_pattern` recogniser, then measured what price actually did next:
- **next→** — did the *next* candle close in the pattern's expected direction?
- **hit≥T** — did price reach the target (5 pts MFE) within the next 10 candles?
- **avgFwd/medFwd** — signed move 10 candles later (+ = the pattern's way).

Everything is compared to the **baseline** (all bars, unconditional). Data:
NIFTY 92,320 candles, CRUDEOIL 211,895 candles (2025-08-04 → 2026-08-04).

## Verdict: no measurable edge — patterns do NOT predict the next move

**NIFTY** (baseline next→ 48.7%, avgFwd −0.02):

| pattern | n | next→ | avgFwd |
|---|---|---|---|
| BULLISH_ENGULFING | 5,014 | 45.5% | −0.4 |
| BEARISH_ENGULFING | 4,938 | 45.1% | −0.9 |
| THREE_WHITE_SOLDIERS | 4,916 | 45.0% | −0.6 |
| MORNING_STAR | 3,400 | 42.8% | −0.5 |
| HAMMER | 2,549 | 41.5% | −1.3 |

**CRUDEOIL** (baseline next→ 43.5%, avgFwd +0.09):

| pattern | n | next→ | avgFwd |
|---|---|---|---|
| MARUBOZU_UP | 16,839 | 36.9% | −0.4 |
| MARUBOZU_DOWN | 16,662 | 35.4% | −0.7 |
| BULLISH_ENGULFING | 9,395 | 40.5% | −0.4 |
| MORNING_STAR | 8,119 | 40.0% | −1.1 |

Key findings:
- **next→ follow-through is at or BELOW the coin-flip baseline** for every
  pattern on both instruments. A "bullish" pattern is *not* followed by an up
  candle more often than random — often slightly less.
- **avgFwd is ~0 or negative** 10 candles out — no positive drift in the
  pattern's direction.
- **Filtering by EMA20/60 trend does not rescue it** — trend-aligned patterns
  score the same ~40–50% and still show no positive expectancy.

## Honest conclusion

On a full year of real data, **candlestick patterns carry no predictive edge for
the next move** on NIFTY or CRUDEOIL. You cannot reliably say "the next candle
will be green" or "this pattern will repeat." This matches the earlier Flow
result (~32% win rate): the edge is not in the shape of one or two candles.

Where a (small, still-to-be-validated) edge can live is *context* — regime +
higher-timeframe alignment + volatility — which is exactly what the Quantum
Signal gate already uses. Patterns are therefore kept only as *descriptive*
context in the UI, never presented as a predictor. This is why the signal card
honestly shows win-probability as **calibrating** rather than a fabricated %.

Reproduce:
```
.venv/bin/python pattern_run.py --instrument NIFTY   --horizon 10 --target 5
.venv/bin/python pattern_run.py --instrument CRUDEOIL --horizon 10 --target 5
```
