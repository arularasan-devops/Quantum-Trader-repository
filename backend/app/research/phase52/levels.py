"""Phase 52 §2 — yesterday's closed levels, and an ATR that cannot see today.

Four levels and one volatility measure, all of them derived from **completed
previous sessions** only:

* ``PDH`` / ``PDL`` / ``PDC`` / ``PDR`` — the previous session's high, low,
  close and range. NaN through the whole first session of the series, because
  there is no previous session and a first session priced off its own levels is
  the look-ahead this phase exists to avoid;
* ``ATR`` — a trailing Wilder ATR over the previous sessions' **daily** true
  ranges, ending at the last completed session. The session that is being traded
  contributes nothing to its own ATR.

The distinction that matters: a daily true range needs the session's close, so
the ATR for session *k* is built from sessions *< k* and is a constant for the
whole of session *k*. Recomputing it intraday from today's unfolding range would
make every threshold in §3 and §7 depend on the move it is supposed to measure.

The per-bar arrays this module returns are aligned to the **decision timeframe**
series (5-minute bars labelled with their closing minute), so a rule reads its
levels and the bar it decided on from one alignment and cannot mix two.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24.data import Series
from app.research.phase52 import ATR_WINDOW_SESSIONS, WAIT_MINUTES

IST_OFFSET = 19_800


def sessions_of(ts: np.ndarray) -> np.ndarray:
    """IST calendar-day index per bar."""
    return (ts + IST_OFFSET) // 86_400


def minutes_into_day(ts: np.ndarray) -> np.ndarray:
    return ((ts + IST_OFFSET) % 86_400) // 60


def session_bounds(sess: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, end) index ranges, one per session, in order."""
    n = sess.size
    if n == 0:
        return []
    starts = [0] + list(np.nonzero(sess[1:] != sess[:-1])[0] + 1)
    ends = starts[1:] + [n]
    return list(zip(starts, ends))


def _wilder(tr: list[float], win: int) -> list[float]:
    """Wilder ATR over a list of daily true ranges, NaN until the window fills.

    Element *k* of the result uses ``tr[:k + 1]``. The caller shifts it by one
    session, so a session reads the ATR that ended yesterday.
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
    """Per-bar previous-day levels, trailing ATR, and session bookkeeping.

    Keys returned, all aligned to ``series``:

    ``pdh`` ``pdl`` ``pdc`` ``pdr``  previous session's high/low/close/range
    ``atr``                          trailing daily ATR, constant within a session
    ``day_open``                     this session's opening price
    ``session``                      IST day index
    ``session_ordinal``              0-based position of the session in the series
    ``bar_in_session``               0-based position of the bar in its session
    ``minutes_since_open``           from the session's first bar's close
    ``open_area_high`` ``open_area_low``
                                     extremes of the bars closing inside the
                                     first ``WAIT_MINUTES``, NaN before the area
                                     is complete
    ``session_high_to_date`` ``session_low_to_date``
                                     running extremes, inclusive of this bar
    ``session_last_bar``             index of the last bar of this bar's session
    """
    ts = series.ts.astype(np.int64)
    n = ts.size
    sess = sessions_of(ts)
    mins = minutes_into_day(ts)
    bounds = session_bounds(sess)

    nan = float("nan")
    out = {
        k: np.full(n, nan) for k in (
            "pdh", "pdl", "pdc", "pdr", "atr", "day_open",
            "open_area_high", "open_area_low",
            "session_high_to_date", "session_low_to_date",
        )
    }
    out["session"] = sess.astype(np.float64)
    out["session_ordinal"] = np.full(n, nan)
    out["bar_in_session"] = np.full(n, nan)
    out["minutes_since_open"] = np.full(n, nan)
    out["session_last_bar"] = np.zeros(n, dtype=np.int64)

    # Daily true ranges over completed sessions, then a Wilder ATR whose element
    # k uses sessions <= k. Session k is handed element k-1.
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
        out["day_open"][a:b] = series.open[a]
        elapsed = (mins[a:b] - mins[a]).astype(np.float64)
        out["minutes_since_open"][a:b] = elapsed
        out["session_high_to_date"][a:b] = np.maximum.accumulate(series.high[a:b])
        out["session_low_to_date"][a:b] = np.minimum.accumulate(series.low[a:b])

        # §4's opening area: the bars that close inside the waiting period. It
        # is published only on the bars after the area is complete, so a bar
        # inside the area cannot be compared against an area that includes it.
        inside = elapsed < float(WAIT_MINUTES)
        if inside.any() and (~inside).any():
            after = np.nonzero(~inside)[0] + a
            out["open_area_high"][after] = float(series.high[a:b][inside].max())
            out["open_area_low"][after] = float(series.low[a:b][inside].min())

        if k == 0:
            continue
        out["pdh"][a:b] = day_high[k - 1]
        out["pdl"][a:b] = day_low[k - 1]
        out["pdc"][a:b] = day_close[k - 1]
        out["pdr"][a:b] = day_high[k - 1] - day_low[k - 1]
        out["atr"][a:b] = atr_by_session[k - 1]

    return out


def chronological_partitions(
    sess: np.ndarray, shares: tuple[float, ...]
) -> np.ndarray:
    """Session-based chronological split, never shuffled.

    The boundary is a **session** boundary, so no session is divided between two
    partitions and no partition can contain a bar that precedes another
    partition's bars.
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
