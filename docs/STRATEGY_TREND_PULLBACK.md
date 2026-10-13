# Existing strategy: trend + pullback (recovered, not replaced)

This documents the strategy **as it exists in the repository**. No thresholds
were changed. Pinned by `backend/_smoke_strategy_trend_pullback.py`.

## 1. Direction gate: 5-minute trend (`app/engine/decision.py::_htf_trend`)
- 1-min candles are aggregated by `settings.htf_factor` (default 5).
- Fewer than 6 HTF bars → `SIDEWAYS`.
- `UP` = EMA9 > EMA21 on HTF closes **and** 4-bar slope > 0, not making
  lower-high/lower-low. `DOWN` is the mirror.
- Strength = `min(1, |EMA sep|·350 + |slope|·120)`, ×0.7 without a clean HH/HL (LH/LL).
- Calls (CE) only in `UP`, puts (PE) only in `DOWN`; `SIDEWAYS` → `AVOID`
  (no forced trade).

## 2. Pullback (`app/analysis/structure.py::pullback`)
- Trend from strict EMA stacking (`trend_from_emas`: EMA9>EMA20>EMA50 = UP).
- `PULLBACK_UP`: trend UP and last close ≤ EMA20 or within 0.6·ATR of EMA20.
- `PULLBACK_DOWN`: mirror. Otherwise `NONE`.
- Adds a weight-9 vote in `_gather_votes`.

## 3. Entry trigger (`decide`, ~L800–910)
`pullback_ok` is also true when, on the trend side of EMA20, price has
retraced 0.25–1.6 ATR from the 8-bar extreme. `base_ready = (pullback_ok or
retest_ok)` and not on a fresh breakout candle. Over-extension (RSI ≥ 80 /
≤ 20, or outside Bollinger band) without a pullback/retest blocks the entry
("no chasing"). Fake-breakout / liquidity-sweep traps against the side block it.
Other enabled triggers (momentum, pattern, ignition, SuperTrend) are all
still gated on the HTF trend. `entry_trigger` records which one fired:
`PULLBACK`, `BREAKOUT_RETEST`, `MOMENTUM`, `PATTERN`, `IGNITION`, `SUPERTREND`,
or `WAIT_SETUP` / `NONE`.

## 4. Option selection, sizing, exits
- `_select_option` picks the strike from the live chain using spot and ATR.
- Sizing / risk / exits are in `app/state.py` (`_auto_trade`, `buy`, `sell`)
  and `app/engine/account_risk.py`; see `docs/RECOVERY_REPORT.md` §Execution.

## Evidence status
No profitability claim is made. Historical replay depends on datasets not
shipped in this repo (see recovery report, blocked items).
