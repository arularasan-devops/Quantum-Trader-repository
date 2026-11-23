"""Base Breakout shadow detector (LOG-ONLY research signal, never trades).

Rules are frozen from the pre-registered Base Breakout Runner test:
base = the 20 completed 1-min candles before the signal candle, with a range of
at most 4x the mean 1-min range of the 60 candles before the base; signal = the
first completed candle that closes outside the base, on >= 1.5x the base's mean
volume (the volume check is skipped when the feed has no volume, e.g. indices).
Stop = base mid; R = |close - base mid|. Backtests of these rules were negative
(NIFTY/SENSEX 1y, CRUDEOIL 2021-26), so this only records live candidates for
comparison on real quotes. Pure function, no I/O.
"""
from __future__ import annotations

import statistics

from app.models import Candle

BASE = 20
RANGE_X = 4.0
ATR_N = 60
VOL_X = 1.5


def detect(candles: list[Candle], now: float) -> dict | None:
    """Breakout on the last COMPLETED candle (time + 60 <= now), else None."""
    done = [c for c in candles if c.time + 60 <= now]
    if len(done) < BASE + ATR_N + 1:
        return None
    sig = done[-1]
    base = done[-1 - BASE:-1]
    prior = done[-1 - BASE - ATR_N:-1 - BASE]
    # A gap in the feed would stretch the windows; require contiguous minutes.
    if sig.time - prior[0].time > (BASE + ATR_N + 5) * 60:
        return None
    hi = max(c.high for c in base)
    lo = min(c.low for c in base)
    atr = statistics.mean(c.high - c.low for c in prior)
    if atr <= 0 or hi - lo > RANGE_X * atr:
        return None
    side = "CE" if sig.close > hi else "PE" if sig.close < lo else None
    if side is None:
        return None
    mean_vol = statistics.mean(float(c.volume or 0) for c in base)
    vol_ratio = float(sig.volume or 0) / mean_vol if mean_vol > 0 else None
    if vol_ratio is not None and vol_ratio < VOL_X:
        return None
    mid = (hi + lo) / 2
    return {
        "candle_time": int(sig.time), "side": side, "close": round(sig.close, 2),
        "base_high": round(hi, 2), "base_low": round(lo, 2), "base_mid": round(mid, 2),
        "base_range": round(hi - lo, 2), "atr_1m": round(atr, 2),
        "r_pts": round(abs(sig.close - mid), 2),
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
    }
