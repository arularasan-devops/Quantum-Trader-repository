"""Phase 34 — the two frozen flip rules the adaptive-exit study rides.

The chart's arrows come from a flip indicator: it turns long, stays long, then turns
short. To test the *exit* honestly the entry has to be one of those rules, and it
has to carry a sign at every bar so ``FLIP`` has something to flip on.

Both rules are standard and were written before any result existed:

``EMA_FLIP``
    Sign of EMA(9) - EMA(21) on 1-minute closes. Entry on the bar the sign turns.
``SUPERTREND_FLIP``
    Classic ATR band: sign flips when the close crosses the trailing band built from
    ATR(10) x 3. Entry on the bar the sign turns.

Every flip is an entry, including the ones that reverse two bars later. That is the
point — the chart shows one good leg and hides the whipsaws around it.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data
from app.research.phase31.excursion import LONG, _prepare
from app.research.phase34.adaptive import atr

EMA_FLIP = "EMA_FLIP"
SUPERTREND_FLIP = "SUPERTREND_FLIP"
FAMILIES = (EMA_FLIP, SUPERTREND_FLIP)

EMA_FAST = 9
EMA_SLOW = 21
ST_PERIOD = 10
ST_MULTIPLE = 3.0


def ema(x: np.ndarray, period: int) -> np.ndarray:
    """Causal EMA; the first ``period-1`` values are NaN rather than seeded."""
    out = np.full(len(x), np.nan)
    if len(x) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    run = float(np.nanmean(x[:period]))
    out[period - 1] = run
    for i in range(period, len(x)):
        v = x[i]
        if not np.isfinite(v):
            out[i] = run
            continue
        run = alpha * v + (1.0 - alpha) * run
        out[i] = run
    return out


def ema_sign(s: data.Series) -> np.ndarray:
    fast = ema(s.close, EMA_FAST)
    slow = ema(s.close, EMA_SLOW)
    sign = np.zeros(len(s), dtype=np.int8)
    diff = fast - slow
    sign[diff > 0] = 1
    sign[diff < 0] = -1
    sign[~np.isfinite(diff)] = 0
    return sign


def supertrend_sign(s: data.Series) -> np.ndarray:
    """Sign of a classic ATR supertrend, reset at each session boundary.

    Resetting matters: carrying a band across an overnight gap would flip every
    instrument at 09:15 on the gap rather than on price action.
    """
    sess, _ = _prepare(s)
    a = atr(s, ST_PERIOD)
    close = s.close
    hl2 = (s.high + s.low) / 2.0
    sign = np.zeros(len(s), dtype=np.int8)
    up = np.nan
    dn = np.nan
    cur = 0
    for i in range(len(s)):
        if not np.isfinite(a[i]):
            sign[i] = 0
            continue
        if i == 0 or sess[i] != sess[i - 1]:
            up, dn, cur = np.nan, np.nan, 0
        basic_up = hl2[i] - ST_MULTIPLE * a[i]
        basic_dn = hl2[i] + ST_MULTIPLE * a[i]
        up = basic_up if not np.isfinite(up) else max(basic_up, up)
        dn = basic_dn if not np.isfinite(dn) else min(basic_dn, dn)
        if cur >= 0 and close[i] < up:
            cur = -1
            dn = basic_dn
        elif cur <= 0 and close[i] > dn:
            cur = 1
            up = basic_up
        elif cur == 0:
            cur = 1 if close[i] > hl2[i] else -1
        sign[i] = cur
    return sign


def sign_of(s: data.Series, family: str) -> np.ndarray:
    if family == EMA_FLIP:
        return ema_sign(s)
    if family == SUPERTREND_FLIP:
        return supertrend_sign(s)
    raise KeyError(f"unknown family {family}")


def entries(sign: np.ndarray, side: int) -> np.ndarray:
    """Bars where the trend sign turns to ``side``. Every turn, no filtering."""
    want = 1 if side == LONG else -1
    prev = np.concatenate(([0], sign[:-1]))
    return (sign == want) & (prev != want)
