"""Phase 27 §1 — 5-minute and 15-minute bars, aggregated from the stored minutes.

The whole study rests on this file being boring and correct, so the rules are
stated before the code:

* the source is the same stored 1-minute series Phase 24 used. No second feed, no
  vendor's pre-aggregated bars, nothing interpolated and nothing filled;
* a bar never spans a session. Buckets are anchored to each session's own first
  minute, so a 15-minute bar is the first fifteen traded minutes of that session,
  then the next fifteen, and the session's tail is a short bar rather than a bar
  that borrows tomorrow's minutes;
* a bar carries the timestamp of its **last** constituent minute, because that is
  the instant the decision can be taken. Labelling an aggregated bar with its
  opening minute and then deciding on its close is a fifteen-minute look-ahead,
  and it is invisible in the output;
* the high and the low of an aggregated bar are the true extremes of its own
  minutes, so a bar whose range contains both the stop and the target still
  cannot say which came first. That tie stays a loss, exactly as at one minute —
  at fifteen minutes the ambiguity is wider, which is a reason to be stricter,
  not looser;
* bars built from fewer minutes than the timeframe (a session's tail, a feed gap)
  are counted and reported. They are real bars, but a reader is entitled to know
  how many of them there are.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase24.data import Series

IST_OFFSET = 19_800  # +05:30 in seconds

# The two timeframes this study exists to test. One minute is not re-run here:
# Phase 24 already measured it on the same series, and its geometry sweep is
# read back as the reference row instead of being recomputed.
TIMEFRAMES = (5, 15)


def _sessions(ts: np.ndarray) -> np.ndarray:
    return (ts + IST_OFFSET) // 86_400


def _minutes_into_day(ts: np.ndarray) -> np.ndarray:
    return ((ts + IST_OFFSET) % 86_400) // 60


def _group_starts(sess: np.ndarray, mins: np.ndarray, timeframe: int) -> np.ndarray:
    """Index of the first minute of every aggregated bar.

    Anchored per session: the bucket of a minute is measured from its own
    session's first minute, so a session that opens at 09:00 and one that opens
    at 09:15 both get whole bars from their own open.
    """
    n = sess.size
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    new_session = np.empty(n, dtype=bool)
    new_session[0] = True
    new_session[1:] = sess[1:] != sess[:-1]
    starts = np.flatnonzero(new_session)
    first_min = np.repeat(mins[starts], np.diff(np.append(starts, n)))
    bucket = (mins - first_min) // int(timeframe)
    boundary = new_session.copy()
    boundary[1:] |= bucket[1:] != bucket[:-1]
    return np.flatnonzero(boundary).astype(np.int64)


def resample(series: Series, timeframe: int) -> tuple[Series, dict]:
    """Aggregate a 1-minute series to ``timeframe`` minutes.

    Returns the aggregated series and the counts a reader needs to trust it: how
    many minutes went in, how many bars came out, how many bars are short, and
    the assertion that no bar spans a session.
    """
    tf = int(timeframe)
    if tf <= 1:
        raise ValueError("phase 27 aggregates to more than one minute")
    ts = series.ts
    sess = _sessions(ts)
    mins = _minutes_into_day(ts)
    starts = _group_starts(sess, mins, tf)
    ends = np.append(starts[1:], ts.size)          # exclusive
    last = ends - 1                                 # inclusive, the decision bar

    out = Series.__new__(Series)
    out.instrument = series.instrument
    out.ts = ts[last]
    out.open = series.open[starts]
    out.close = series.close[last]
    out.high = np.maximum.reduceat(series.high, starts)
    out.low = np.minimum.reduceat(series.low, starts)
    out.volume = np.add.reduceat(series.volume, starts)

    minutes_per_bar = (ends - starts).astype(np.int64)
    stats = {
        "timeframe_minutes": tf,
        "source_minute_bars": int(ts.size),
        "bars": int(out.ts.size),
        "sessions": int(np.unique(sess).size),
        "full_bars": int((minutes_per_bar == tf).sum()),
        "short_bars": int((minutes_per_bar < tf).sum()),
        "short_bar_pct": round(
            100.0 * float((minutes_per_bar < tf).mean()) if out.ts.size else 0.0, 2
        ),
        "median_minutes_per_bar": float(np.median(minutes_per_bar))
        if minutes_per_bar.size else 0.0,
        "spans_a_session": bool((sess[starts] != sess[last]).any()),
        "first_ts": int(out.ts[0]) if out.ts.size else 0,
        "last_ts": int(out.ts[-1]) if out.ts.size else 0,
        "note": (
            "aggregated from the stored 1-minute series; each bar is labelled with "
            "its closing minute, and no bar spans a session"
        ),
    }
    return out, stats


def load(instrument: str, timeframe: int) -> tuple[Series, dict] | None:
    """The instrument's series at ``timeframe`` minutes, or ``None`` if absent."""
    s = p24data.load_series(instrument)
    if s is None or len(s) == 0:
        return None
    return resample(s, timeframe)


def coverage() -> dict:
    """What Phase 27 can run on, and how many higher-timeframe bars that is.

    Phase 24's own coverage decides eligibility — the same five-year bar and the
    same ``INSUFFICIENT_HISTORY`` label — so an instrument cannot enter this study
    by being resampled into a shorter-looking history.
    """
    cov = p24data.coverage()
    per_tf: list[dict] = []
    for row in cov["usable"]:
        for tf in TIMEFRAMES:
            got = load(row["instrument"], tf)
            if got is None:
                continue
            per_tf.append({"instrument": row["instrument"], **got[1]})
    return {
        **cov,
        "timeframes": list(TIMEFRAMES),
        "resampled": per_tf,
        "one_minute_reference": (
            "the 1-minute result is not recomputed here; Phase 24's geometry sweep "
            "on the same series is read back as the reference row"
        ),
    }
