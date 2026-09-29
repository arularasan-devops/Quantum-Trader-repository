"""Phase 53 — the opening range, and an ATR that cannot see the session it scales.

Two causal objects per session:

* ``OR_HIGH`` / ``OR_LOW`` / ``OR_RANGE`` — the extremes of the bars that close
  inside the first thirty minutes. They are published **only to the bars after
  the range is complete**, so a bar inside the opening range can never be
  compared against a range that includes itself. That is the single most common
  way an opening-range study leaks, and the smoke test asserts the withheld
  values are NaN rather than trusting this comment;
* ``ATR`` — a trailing Wilder ATR over previous sessions' **daily** true ranges,
  ending at the last completed session. A daily true range needs a close, so the
  ATR for session *k* is built from sessions ``< k`` and is a constant across the
  whole of session *k*. Recomputing it intraday would scale the retest tolerance
  and the stop cap by the very move they are meant to bound.

Everything returned is aligned to the five-minute decision series, so a rule
reads its level and the bar it decided on from one alignment and cannot mix two.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24.data import Series
from app.research.phase53 import ATR_WINDOW_SESSIONS, OPENING_RANGE_MINUTES

IST_OFFSET = 19_800
SECONDS_PER_DAY = 86_400


def sessions_of(ts: np.ndarray) -> np.ndarray:
    """IST calendar-day index per bar."""
    return (ts + IST_OFFSET) // SECONDS_PER_DAY


def minutes_into_day(ts: np.ndarray) -> np.ndarray:
    return ((ts + IST_OFFSET) % SECONDS_PER_DAY) // 60


def session_bounds(sess: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, end) index ranges, one per session, in order."""
    n = sess.size
    if n == 0:
        return []
    starts = [0] + list(np.nonzero(sess[1:] != sess[:-1])[0] + 1)
    ends = starts[1:] + [n]
    return list(zip(starts, ends))


def _wilder(tr: list[float], win: int) -> list[float]:
    """Wilder ATR over daily true ranges, NaN until the window fills.

    Element *k* uses ``tr[:k + 1]``. The caller shifts by one session, so a
    session reads the ATR that ended yesterday.
    """
    out = [float("nan")] * len(tr)
    if len(tr) < win:
        return out
    running = sum(tr[:win]) / float(win)
    out[win - 1] = running
    alpha = 1.0 / float(win)
    for i in range(win, len(tr)):
        running = running + alpha * (tr[i] - running)
        out[i] = running
    return out


def build(series: Series) -> dict[str, np.ndarray]:
    """Per-bar opening range, trailing ATR and session bookkeeping.

    Keys returned, all aligned to ``series``:

    ``or_high`` ``or_low`` ``or_range``  the opening range, NaN on the bars that
                                         form it and on any session too short to
                                         close one
    ``atr``                              trailing daily ATR, constant in-session
    ``session`` ``session_ordinal`` ``bar_in_session``
    ``minutes_since_open``               from the first bar's close
    ``session_last_bar``                 index of the session's last bar
    ``or_complete``                      True on bars that may act on the range
    """
    ts = series.ts.astype(np.int64)
    n = ts.size
    sess = sessions_of(ts)
    mins = minutes_into_day(ts)
    bounds = session_bounds(sess)

    nan = float("nan")
    out: dict[str, np.ndarray] = {
        k: np.full(n, nan) for k in ("or_high", "or_low", "or_range", "atr")
    }
    out["session"] = sess.astype(np.float64)
    out["session_ordinal"] = np.full(n, nan)
    out["bar_in_session"] = np.full(n, nan)
    out["minutes_since_open"] = np.full(n, nan)
    out["session_last_bar"] = np.zeros(n, dtype=np.int64)
    out["or_complete"] = np.zeros(n, dtype=bool)

    day_high: list[float] = []
    day_low: list[float] = []
    day_close: list[float] = []
    for a, b in bounds:
        day_high.append(float(series.high[a:b].max()))
        day_low.append(float(series.low[a:b].min()))
        day_close.append(float(series.close[b - 1]))
    daily_tr: list[float] = []
    for k in range(len(bounds)):
        if k == 0:
            daily_tr.append(day_high[0] - day_low[0])
            continue
        prev_close = day_close[k - 1]
        daily_tr.append(max(
            day_high[k] - day_low[k],
            abs(day_high[k] - prev_close),
            abs(day_low[k] - prev_close),
        ))
    atr_by_session = _wilder(daily_tr, ATR_WINDOW_SESSIONS)

    for k, (a, b) in enumerate(bounds):
        out["session_ordinal"][a:b] = k
        out["bar_in_session"][a:b] = np.arange(b - a)
        out["session_last_bar"][a:b] = b - 1
        elapsed = (mins[a:b] - mins[a]).astype(np.float64)
        out["minutes_since_open"][a:b] = elapsed
        if k > 0:
            out["atr"][a:b] = atr_by_session[k - 1]

        inside = elapsed < float(OPENING_RANGE_MINUTES)
        after = ~inside
        if not (inside.any() and after.any()):
            # Either the session never closed a thirty-minute range or it ended
            # with it. Both leave nothing to trade and the range stays NaN.
            continue
        idx = np.nonzero(after)[0] + a
        hi = float(series.high[a:b][inside].max())
        lo = float(series.low[a:b][inside].min())
        out["or_high"][idx] = hi
        out["or_low"][idx] = lo
        out["or_range"][idx] = hi - lo
        out["or_complete"][idx] = True

    return out


def chronological_partitions(
    sess: np.ndarray, shares: tuple[float, ...]
) -> np.ndarray:
    """Session-based chronological split, never shuffled.

    The boundary is a **session** boundary, so no session is divided between two
    partitions and no partition holds a bar that precedes another partition's.
    """
    days = np.unique(sess)
    total = days.size
    first = int(round(total * shares[0]))
    second = first + int(round(total * shares[1]))
    label = np.empty(sess.size, dtype=object)
    for i, day in enumerate(days):
        if i < first:
            tag = "DISCOVERY"
        elif i < second:
            tag = "VALIDATION"
        else:
            tag = "UNTOUCHED_HOLDOUT"
        label[sess == day] = tag
    return label
