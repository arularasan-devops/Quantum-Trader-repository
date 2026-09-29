"""Phase 54 — completed daily bars and the levels that may be known before them.

Everything in this file exists to make one sentence true: **nothing a session
produced may take part in deciding that same session.**

* a daily bar is one trading session of the stored series. Its open is the first
  minute's open, its close is the last minute's close, and its extremes are the
  true extremes of its own minutes. No bar spans a session and no bar borrows a
  minute from the next one;
* ``prior_high``/``prior_low`` at session *k* are taken over sessions
  ``k-lookback .. k-1``. The session being judged is not in its own reference
  window, which is the difference between "today closed above the last twenty
  days' high" and the tautology "today closed above its own high";
* ``atr`` at session *k* is Wilder over twenty completed true ranges ending at
  session ``k-1``. A true range needs the previous close, so the first session
  has none and the first twenty carry ``NaN`` rather than a warm-up value
  computed from whatever was available;
* ``NaN`` is the only representation of "not yet knowable". There is no fill
  forward, no back fill and no default, because a default here is a look-ahead
  that survives every test that does not look for it.

The partitions are cut on **sessions, in order**, never shuffled. A shuffled
split of a time series leaks the future into the past through nothing more
exotic than sorting.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24.data import Series
from app.research.phase54 import (
    ATR_WINDOW_SESSIONS,
    DISCOVERY,
    UNTOUCHED_HOLDOUT,
    VALIDATION,
)

IST_OFFSET = 19_800  # +05:30 in seconds


def sessions_of(ts: np.ndarray) -> np.ndarray:
    """IST calendar day of every timestamp, as an integer day number."""
    return (np.asarray(ts, dtype=np.int64) + IST_OFFSET) // 86_400


def session_bounds(sess: np.ndarray) -> list[tuple[int, int]]:
    """Half-open ``[start, end)`` index ranges, one per session, in order."""
    n = sess.size
    if n == 0:
        return []
    starts = [0] + list(np.nonzero(sess[1:] != sess[:-1])[0] + 1)
    ends = starts[1:] + [n]
    return list(zip(starts, ends))


def _wilder(values: list[float], window: int) -> list[float]:
    """Wilder's smoothing, seeded by the simple mean of the first ``window``.

    Returns ``NaN`` for every position before the seed is complete. Index *k* of
    the result is the ATR **through** session *k*, so a caller that wants the
    value knowable before session *k* must read index ``k-1``.
    """
    out = [float("nan")] * len(values)
    if len(values) < window:
        return out
    seed = float(np.mean(values[:window]))
    out[window - 1] = seed
    prev = seed
    for i in range(window, len(values)):
        prev = (prev * (window - 1) + values[i]) / window
        out[i] = prev
    return out


def build(s5: Series) -> dict[str, np.ndarray]:
    """Completed daily bars, aggregated from the five-minute series.

    Returns parallel arrays, one entry per completed session, plus the index of
    each session's first and last five-minute bar so the resolver can walk the
    real intraday path of a multi-day position rather than four daily numbers.
    """
    sess = sessions_of(s5.ts)
    bounds = session_bounds(sess)
    n = len(bounds)
    out = {
        "session": np.zeros(n, dtype=np.int64),
        "ordinal": np.arange(n, dtype=np.int64),
        "open_ts": np.zeros(n, dtype=np.int64),
        "close_ts": np.zeros(n, dtype=np.int64),
        "open": np.zeros(n, dtype=np.float64),
        "high": np.zeros(n, dtype=np.float64),
        "low": np.zeros(n, dtype=np.float64),
        "close": np.zeros(n, dtype=np.float64),
        "first_i": np.zeros(n, dtype=np.int64),
        "last_i": np.zeros(n, dtype=np.int64),
        "bars": np.zeros(n, dtype=np.int64),
    }
    for k, (a, b) in enumerate(bounds):
        out["session"][k] = int(sess[a])
        out["open_ts"][k] = int(s5.ts[a])
        out["close_ts"][k] = int(s5.ts[b - 1])
        out["open"][k] = float(s5.open[a])
        out["high"][k] = float(s5.high[a:b].max())
        out["low"][k] = float(s5.low[a:b].min())
        out["close"][k] = float(s5.close[b - 1])
        out["first_i"][k] = a
        out["last_i"][k] = b - 1
        out["bars"][k] = b - a
    return out


def true_ranges(daily: dict[str, np.ndarray]) -> np.ndarray:
    """Daily true range; ``NaN`` for the first session, which has no prior close."""
    n = daily["close"].size
    tr = np.full(n, np.nan, dtype=np.float64)
    if n < 2:
        return tr
    prev_close = daily["close"][:-1]
    high = daily["high"][1:]
    low = daily["low"][1:]
    tr[1:] = np.maximum(
        high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
    )
    return tr


def atr_before(daily: dict[str, np.ndarray],
               window: int = ATR_WINDOW_SESSIONS) -> np.ndarray:
    """ATR knowable **before** each session: Wilder through the previous one."""
    tr = true_ranges(daily)
    n = tr.size
    usable = [float(v) for v in tr[1:]] if n > 1 else []
    smoothed = _wilder(usable, int(window))
    out = np.full(n, np.nan, dtype=np.float64)
    # ``smoothed[j]`` is the ATR through session ``j+1``; session ``k`` may only
    # see it when ``j + 1 <= k - 1``.
    for k in range(n):
        j = k - 2
        if 0 <= j < len(smoothed):
            out[k] = smoothed[j]
    return out


def extremes_before(daily: dict[str, np.ndarray],
                    lookback: int) -> tuple[np.ndarray, np.ndarray]:
    """Highest high and lowest low of the ``lookback`` sessions before each one."""
    high, low = daily["high"], daily["low"]
    n = high.size
    hi = np.full(n, np.nan, dtype=np.float64)
    lo = np.full(n, np.nan, dtype=np.float64)
    lb = int(lookback)
    for k in range(lb, n):
        hi[k] = float(high[k - lb:k].max())
        lo[k] = float(low[k - lb:k].min())
    return hi, lo


def chronological_partitions(daily: dict[str, np.ndarray],
                             shares: tuple[float, ...]) -> np.ndarray:
    """Label each session DISCOVERY / VALIDATION / UNTOUCHED_HOLDOUT, in time order."""
    n = daily["session"].size
    first = int(round(n * shares[0]))
    second = first + int(round(n * shares[1]))
    label = np.empty(n, dtype=object)
    for k in range(n):
        if k < first:
            label[k] = DISCOVERY
        elif k < second:
            label[k] = VALIDATION
        else:
            label[k] = UNTOUCHED_HOLDOUT
    return label
