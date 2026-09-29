"""Technical indicator library (pure functions over price/volume series).

Implemented with numpy for speed. Each function is defensive about short
inputs and returns ``None`` (or NaN-free values) when there is not enough
history, so the decision engine can degrade gracefully at session open.
"""
from __future__ import annotations

import numpy as np


def ema(values: np.ndarray, period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return float(e)


def ema_series(values: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1.0)
    out = np.empty_like(values, dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, volumes: np.ndarray) -> float | None:
    if len(closes) == 0 or volumes.sum() == 0:
        return None
    typical = (highs + lows + closes) / 3.0
    return float((typical * volumes).sum() / volumes.sum())


def rsi(closes: np.ndarray, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[-period:].mean()
    avg_loss = losses[-period:].mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def macd(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema_series(macd_line, signal)
    hist = macd_line - signal_line
    return float(macd_line[-1]), float(signal_line[-1]), float(hist[-1])


def true_range(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    prev_close = np.roll(closes, 1)
    prev_close[0] = closes[0]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))
    return tr


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    tr = true_range(highs, lows, closes)
    return float(tr[-period:].mean())


def adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float | None:
    if len(closes) < 2 * period:
        return None
    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = true_range(highs, lows, closes)[1:]

    def smooth(x):
        s = np.empty_like(x, dtype=float)
        s[0] = x[:period].sum()
        for i in range(1, len(x)):
            s[i] = s[i - 1] - (s[i - 1] / period) + x[i]
        return s

    atr_s = smooth(tr)
    atr_s[atr_s == 0] = 1e-9
    plus_di = 100.0 * smooth(plus_dm) / atr_s
    minus_di = 100.0 * smooth(minus_dm) / atr_s
    denom = plus_di + minus_di
    denom[denom == 0] = 1e-9
    dx = 100.0 * np.abs(plus_di - minus_di) / denom
    return float(dx[-period:].mean())


def supertrend_series(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 10,
    mult: float = 3.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Classic Supertrend (ATR trailing band) — the TradingView flip indicator.

    Returns ``(direction, line)`` where ``direction`` is +1 in the up-trend (band
    below price) and -1 in the down-trend. The band RATCHETS: it only moves in the
    direction of the trend, so a flip needs a close through the trailing line
    rather than a touch. That state is what makes it flip at the turn; a
    single-bar reading has no trend memory and cannot flip at all.
    """
    n = len(closes)
    direction = np.ones(n, dtype=int)
    line = np.full(n, np.nan)
    if n < period + 2:
        return direction, line

    tr = true_range(highs, lows, closes)
    # Wilder-smoothed ATR (what the TradingView built-in uses).
    atr_arr = np.full(n, np.nan)
    atr_arr[period] = tr[1 : period + 1].mean()
    for i in range(period + 1, n):
        atr_arr[i] = (atr_arr[i - 1] * (period - 1) + tr[i]) / period

    hl2 = (highs + lows) / 2.0
    upper = hl2 + mult * atr_arr
    lower = hl2 - mult * atr_arr

    final_upper = upper.copy()
    final_lower = lower.copy()
    for i in range(period + 1, n):
        if upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]:
            final_upper[i] = upper[i]
        else:
            final_upper[i] = final_upper[i - 1]
        if lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]:
            final_lower[i] = lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        prev = direction[i - 1]
        if prev == 1 and closes[i] < final_lower[i - 1]:
            direction[i] = -1
        elif prev == -1 and closes[i] > final_upper[i - 1]:
            direction[i] = 1
        else:
            direction[i] = prev
        line[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return direction, line


def supertrend(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 10, mult: float = 3.0):
    """Latest ``(line, direction)`` of the Supertrend."""
    if len(closes) < period + 2:
        return None, None
    direction, line = supertrend_series(highs, lows, closes, period, mult)
    if np.isnan(line[-1]):
        return None, None
    return float(line[-1]), int(direction[-1])


def supertrend_flip(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 10,
    mult: float = 3.0,
    confirm_bars: int = 1,
) -> str:
    """Fire on the candle the Supertrend flips (the chart's BUY/SELL marker).

    ``confirm_bars`` is how recently the flip must have happened for it to still
    be actionable — 1 means only the just-closed candle, the earliest possible
    entry. Older flips are deliberately ignored so this cannot degrade into a
    late chase after the move has already run.

    Returns ``FLIP_UP`` / ``FLIP_DOWN`` / ``NONE``.
    """
    n = min(len(highs), len(lows), len(closes))
    if n < period + 3:
        return "NONE"
    direction, _ = supertrend_series(highs[:n], lows[:n], closes[:n], period, mult)
    for back in range(1, max(1, int(confirm_bars)) + 1):
        i = n - back
        if i - 1 < 0:
            break
        if direction[i] != direction[i - 1]:
            return "FLIP_UP" if direction[i] == 1 else "FLIP_DOWN"
    return "NONE"


def bollinger(closes: np.ndarray, period: int = 20, mult: float = 2.0):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = window.mean()
    sd = window.std()
    return float(mid + mult * sd), float(mid), float(mid - mult * sd)


def momentum(closes: np.ndarray, period: int = 10) -> float | None:
    if len(closes) < period + 1:
        return None
    return float((closes[-1] - closes[-period - 1]) / closes[-period - 1] * 100.0)


def volume_spike(volumes: np.ndarray, period: int = 20, factor: float = 2.0) -> bool:
    if len(volumes) < period + 1:
        return False
    avg = volumes[-period - 1:-1].mean()
    return bool(avg > 0 and volumes[-1] > factor * avg)
