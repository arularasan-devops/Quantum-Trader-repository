"""Phase 28 §3 — daily features, computed backwards only.

Same discipline as Phase 24: the value at index ``i`` uses daily bars ``<= i`` and
nothing later, and the smoke test proves it by recomputing on a truncated series
and comparing the prefix. On a daily series a look-ahead bug is worth far more
than intraday — one bar of hindsight is a whole trading day — so nothing here
touches ``i + 1``.

The windows are the ones a swing trader would actually name, in days, and they are
round numbers on purpose: a window tuned before the chronological split is a
parameter fitted on the answer.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from app.research.phase24.data import Series

ATR_WIN = 14
SLOW_VOL_WIN = 60
BREAKOUT_SHORT = 20
BREAKOUT_LONG = 50
SWING_WIN = 20
MOM_SHORT = 5
MOM_LONG = 20
EMA_FAST = 20
EMA_SLOW = 50
EMA_REGIME = 200
RSI_WIN = 14
VOLUME_WIN = 20
YEAR_WIN = 250

UP, DOWN, FLAT = 1, -1, 0


def _shift1(a: np.ndarray) -> np.ndarray:
    """``a`` as it was known one bar earlier, first value repeated."""
    out = np.empty_like(a)
    out[0] = a[0]
    out[1:] = a[:-1]
    return out


def _rolling(
    a: np.ndarray, win: int, fn: Callable[[np.ndarray], np.floating]
) -> np.ndarray:
    """``fn`` over a trailing window of ``win`` bars, inclusive of ``i``."""
    n = a.size
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        lo = max(0, i - win + 1)
        out[i] = fn(a[lo:i + 1])
    return out


def _rolling_max(a: np.ndarray, win: int) -> np.ndarray:
    return _rolling(a, win, np.max)


def _rolling_min(a: np.ndarray, win: int) -> np.ndarray:
    return _rolling(a, win, np.min)


def _rolling_mean(a: np.ndarray, win: int) -> np.ndarray:
    return _rolling(a, win, np.mean)


def _ema(a: np.ndarray, span: int) -> np.ndarray:
    """Standard EMA, seeded on the first value so index 0 is defined."""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(a, dtype=np.float64)
    out[0] = a[0]
    for i in range(1, a.size):
        out[i] = alpha * a[i] + (1.0 - alpha) * out[i - 1]
    return out


def _true_range(s: Series) -> np.ndarray:
    prev_close = _shift1(s.close)
    return np.maximum(
        s.high - s.low,
        np.maximum(np.abs(s.high - prev_close), np.abs(s.low - prev_close)),
    )


def _rsi(close: np.ndarray, win: int = RSI_WIN) -> np.ndarray:
    """Wilder's RSI. Seeded flat, so early bars sit at 50 rather than at a
    number invented from one observation."""
    n = close.size
    out = np.full(n, 50.0, dtype=np.float64)
    if n < 2:
        return out
    delta = np.diff(close, prepend=close[0])
    gain = np.maximum(delta, 0.0)
    loss = np.maximum(-delta, 0.0)
    avg_g = avg_l = 0.0
    for i in range(1, n):
        if i <= win:
            avg_g = (avg_g * (i - 1) + gain[i]) / i
            avg_l = (avg_l * (i - 1) + loss[i]) / i
        else:
            avg_g = (avg_g * (win - 1) + gain[i]) / win
            avg_l = (avg_l * (win - 1) + loss[i]) / win
        if avg_l <= 0.0:
            out[i] = 100.0 if avg_g > 0.0 else 50.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def _trend(close: np.ndarray, win: int, atr: np.ndarray) -> np.ndarray:
    """Direction of the last ``win`` bars, in ATR units, as UP/DOWN/FLAT.

    Measured in ATR rather than in per cent so a ₹120 stock and a 78,000-point
    index are graded on the same scale.
    """
    prior = np.empty_like(close)
    for i in range(close.size):
        prior[i] = close[max(0, i - win)]
    move = (close - prior) / np.maximum(atr, 1e-9)
    out = np.full(close.size, FLAT, dtype=np.int8)
    out[move >= 0.5] = UP
    out[move <= -0.5] = DOWN
    return out


def build(s: Series) -> dict[str, np.ndarray]:
    """Every daily feature the condition vocabulary reads, all causal."""
    close, high, low, open_ = s.close, s.high, s.low, s.open
    tr = _true_range(s)
    atr = _rolling_mean(tr, ATR_WIN)
    atr = np.maximum(atr, 1e-9)
    slow_atr = _rolling_mean(tr, SLOW_VOL_WIN)

    ema_fast = _ema(close, EMA_FAST)
    ema_slow = _ema(close, EMA_SLOW)
    ema_regime = _ema(close, EMA_REGIME)

    # Breakout levels exclude today's own bar: "closed above the 20-day high"
    # must mean the high of the 20 days BEFORE today, or every up day is a
    # breakout by construction.
    prior_high_20 = _shift1(_rolling_max(high, BREAKOUT_SHORT))
    prior_low_20 = _shift1(_rolling_min(low, BREAKOUT_SHORT))
    prior_high_50 = _shift1(_rolling_max(high, BREAKOUT_LONG))
    prior_low_50 = _shift1(_rolling_min(low, BREAKOUT_LONG))
    year_high = _rolling_max(high, YEAR_WIN)
    year_low = _rolling_min(low, YEAR_WIN)

    swing_high = _rolling_max(high, SWING_WIN)
    swing_low = _rolling_min(low, SWING_WIN)
    swing_range = np.maximum(swing_high - swing_low, 1e-9)

    prev_close = _shift1(close)
    gap_pct = (open_ - prev_close) / np.maximum(np.abs(prev_close), 1e-9) * 100.0
    day_range = high - low
    body = np.abs(close - open_)

    vol_mean = _rolling_mean(s.volume, VOLUME_WIN)

    mom_short = np.empty_like(close)
    mom_long = np.empty_like(close)
    for i in range(close.size):
        mom_short[i] = close[i] - close[max(0, i - MOM_SHORT)]
        mom_long[i] = close[i] - close[max(0, i - MOM_LONG)]

    return {
        "close": close,
        "high": high,
        "low": low,
        "open": open_,
        "atr": atr,
        "atr_pct": atr / np.maximum(np.abs(close), 1e-9) * 100.0,
        "vol_ratio": atr / np.maximum(slow_atr, 1e-9),
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_regime": ema_regime,
        "ema_fast_above_slow": (ema_fast > ema_slow).astype(np.int8),
        "above_regime_ema": (close > ema_regime).astype(np.int8),
        "rsi": _rsi(close),
        "trend_short": _trend(close, MOM_SHORT, atr),
        "trend_long": _trend(close, MOM_LONG, atr),
        "prior_high_20": prior_high_20,
        "prior_low_20": prior_low_20,
        "prior_high_50": prior_high_50,
        "prior_low_50": prior_low_50,
        "year_high": year_high,
        "year_low": year_low,
        "pct_from_year_high": (close - year_high)
        / np.maximum(np.abs(year_high), 1e-9) * 100.0,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "swing_range": swing_range,
        "pullback_from_high_pct": (swing_high - close) / swing_range * 100.0,
        "pullback_from_low_pct": (close - swing_low) / swing_range * 100.0,
        "gap_pct": gap_pct,
        "range_over_atr": day_range / atr,
        "body_over_range": body / np.maximum(day_range, 1e-9),
        "mom_short_atr": mom_short / atr,
        "mom_long_atr": mom_long / atr,
        "volume_ratio": s.volume / np.maximum(vol_mean, 1e-9),
        "closed_up": (close > open_).astype(np.int8),
    }


__all__ = ["build", "ATR_WIN", "UP", "DOWN", "FLAT"]
