"""Phase 53 — breakout, retest, confirmation, next-bar entry, detected causally.

The sequence, in the order the session reveals it, for each direction
independently:

1. **opening range** — the first thirty minutes close. Nothing before that may
   break anything out;
2. **breakout** — a completed 15- or 30-minute bucket closes outside the range.
   The breakout bar itself is never entered on: that is the "no breakout
   chasing" clause, and it is also where most opening-range backtests quietly
   buy the close that triggered them;
3. **retest** — within sixty minutes of the breakout close, price trades back to
   the level, or within the declared ATR tolerance of it. The touch is read from
   a bar's *range*, so it is detected but never acted on inside the same bar;
4. **confirmation** — a completed bar **after** the touching bar closes back
   beyond the level, within sixty minutes of the touch;
5. **entry** — the next five-minute bar's open, long above the range and short
   below it;
6. **once** — the **first** qualifying sequence in each direction is the trade.
   Scanning forward for a later one that happens to clear the stop cap or the
   cost gate would select on a condition correlated with the move that follows
   it, which is a look-ahead wearing the costume of a filter.

A structural note about the registered grid, which decides how many independent
tests this really is: only ``breakout_confirmation``, ``retest_confirmation``
and ``retest_tolerance`` change **which instants are entered** — eight event
tables. ``stop_buffer`` changes the stop and therefore the risk, ``target_r``
scales the target off that risk, and ``cost_gate`` only filters. So the
sixty-four parameterizations are thirty-two resolved tables seen through two
gates, and the event-family hashing in the study is what reports that honestly
instead of claiming sixty-four discoveries.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24.data import Series
from app.research.phase53 import (
    BREAKOUT_15M,
    CONFIRMATION_WINDOW_MINUTES,
    LONG_BREAKOUT,
    NO_BREAKOUT,
    NO_FORWARD_WINDOW,
    NO_OPENING_RANGE,
    NO_RETEST,
    NO_RETEST_CONFIRMATION,
    NOT_KNOWABLE,
    RETEST_5M,
    RETEST_WINDOW_MINUTES,
    SHORT_BREAKOUT,
)
from app.research.phase53 import levels as lv_mod
from app.research.phase53.execute import LONG, SHORT, cost_at_price


def _bucket_mask(ts: np.ndarray, allowed: set[int] | None) -> np.ndarray:
    """Which five-minute bars are also the close of an allowed coarser bucket."""
    if allowed is None:
        return np.ones(ts.size, dtype=bool)
    return np.array([int(t) in allowed for t in ts], dtype=bool)


def _direction_event(
    *,
    instrument: str,
    a: int,
    b: int,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    open_: np.ndarray,
    ts: np.ndarray,
    elapsed: np.ndarray,
    usable: np.ndarray,
    breakout_ok: np.ndarray,
    retest_ok: np.ndarray,
    level: float,
    atr: float,
    tolerance_atr: float,
    side: int,
) -> tuple[dict | None, str]:
    """The first breakout -> retest -> confirmation sequence, or why there wasn't one.

    All index arithmetic is session-local; ``a`` is added only when an absolute
    index is stored.
    """
    if side == LONG:
        broke = usable & breakout_ok & (close > level)
    else:
        broke = usable & breakout_ok & (close < level)
    if not broke.any():
        return None, NO_BREAKOUT

    tol = tolerance_atr * atr
    for bo_k in np.nonzero(broke)[0]:
        bo_k = int(bo_k)
        # 3 — the retest, strictly after the breakout bar and inside its window.
        window = usable & (np.arange(elapsed.size) > bo_k) & (
            elapsed <= elapsed[bo_k] + float(RETEST_WINDOW_MINUTES)
        )
        if side == LONG:
            touched = window & (low <= level + tol)
        else:
            touched = window & (high >= level - tol)
        if not touched.any():
            continue
        rt_k = int(np.nonzero(touched)[0][0])

        # 4 — the confirming close, on a later completed bar. Never on the
        # touching bar: a candle cannot say whether its own close came after
        # the wick that reached the level.
        conf_window = usable & retest_ok & (np.arange(elapsed.size) > rt_k) & (
            elapsed <= elapsed[rt_k] + float(CONFIRMATION_WINDOW_MINUTES)
        )
        if side == LONG:
            confirmed = conf_window & (close > level)
        else:
            confirmed = conf_window & (close < level)
        if not confirmed.any():
            continue
        cf_k = int(np.nonzero(confirmed)[0][0])

        # 5 — the fill is the next five-minute bar's open, in the same session.
        if cf_k + 1 > (b - a - 1):
            return None, NO_FORWARD_WINDOW
        fill_i = a + cf_k + 1
        entry = float(open_[cf_k + 1])

        # The retest's own extreme, measured from the touching bar through the
        # confirming bar. This is the low the market actually made while coming
        # back to the level, and every bar in it is complete before the fill.
        if side == LONG:
            extreme = float(low[rt_k:cf_k + 1].min())
        else:
            extreme = float(high[rt_k:cf_k + 1].max())

        gate_cost = float(cost_at_price(instrument, np.array([entry]))[0])
        return {
            "instrument": instrument,
            "direction": LONG_BREAKOUT if side == LONG else SHORT_BREAKOUT,
            "side": int(side),
            "atr": atr,
            "or_level": float(level),
            "breakout_bar": bo_k,
            "breakout_ts": int(ts[bo_k]),
            "breakout_close": float(close[bo_k]),
            "retest_bar": rt_k,
            "retest_ts": int(ts[rt_k]),
            "retest_extreme": extreme,
            "retest_delay_minutes": float(elapsed[rt_k] - elapsed[bo_k]),
            "confirm_bar": cf_k,
            "confirm_ts": int(ts[cf_k]),
            "confirm_close": float(close[cf_k]),
            "confirm_delay_minutes": float(elapsed[cf_k] - elapsed[rt_k]),
            "fill_index": int(fill_i),
            "entry_ts": int(ts[cf_k + 1]),
            "entry": entry,
            "gate_cost_points": gate_cost,
            "minutes_since_open_at_entry": float(elapsed[cf_k + 1]),
        }, ""

    # Every breakout in the session failed at the retest or the confirmation.
    # Which of the two it was is worth separating, so the loop is re-walked
    # cheaply for the reason rather than guessed at.
    for bo_k in np.nonzero(broke)[0]:
        bo_k = int(bo_k)
        window = usable & (np.arange(elapsed.size) > bo_k) & (
            elapsed <= elapsed[bo_k] + float(RETEST_WINDOW_MINUTES)
        )
        touched = (window & (low <= level + tol)) if side == LONG else (
            window & (high >= level - tol)
        )
        if touched.any():
            return None, NO_RETEST_CONFIRMATION
    return None, NO_RETEST


def candidate_events(
    instrument: str,
    s5: Series,
    lv: dict[str, np.ndarray],
    *,
    breakout_ts: set[int] | None,
    retest_ts: set[int] | None,
    tolerance_atr: float,
) -> tuple[list[dict], dict[str, int]]:
    """Every session's long and short event, or the reason there wasn't one.

    Returns the candidate rows — at most one long and one short per session —
    and a funnel of refusals counted per **direction attempt**, so the two
    directions of one session are two entries in the funnel rather than one.
    """
    sess = lv["session"]
    bounds = lv_mod.session_bounds(sess.astype(np.int64))
    funnel: dict[str, int] = {
        "sessions": len(bounds),
        "direction_attempts": 0,
        NOT_KNOWABLE: 0, NO_OPENING_RANGE: 0, NO_BREAKOUT: 0, NO_RETEST: 0,
        NO_RETEST_CONFIRMATION: 0, NO_FORWARD_WINDOW: 0,
        "candidates": 0,
        # Diagnostic, not a gate: sessions where a completed bucket closed
        # outside the opening range at all, in either direction. Without it a
        # reader cannot tell whether the mechanism is starved by the breakout
        # itself or by everything that has to happen after it.
        "sessions_with_any_breakout": 0,
    }
    rows: list[dict] = []
    bo_mask_all = _bucket_mask(s5.ts, breakout_ts)
    rt_mask_all = _bucket_mask(s5.ts, retest_ts)

    for a, b in bounds:
        atr = float(lv["atr"][a])
        if not (np.isfinite(atr) and atr > 0):
            funnel[NOT_KNOWABLE] += 2
            funnel["direction_attempts"] += 2
            continue
        usable = lv["or_complete"][a:b]
        if not usable.any():
            funnel[NO_OPENING_RANGE] += 2
            funnel["direction_attempts"] += 2
            continue

        or_high = float(np.nanmax(lv["or_high"][a:b]))
        or_low = float(np.nanmin(lv["or_low"][a:b]))
        high, low = s5.high[a:b], s5.low[a:b]
        close, open_ = s5.close[a:b], s5.open[a:b]
        ts = s5.ts[a:b]
        elapsed = lv["minutes_since_open"][a:b]
        bo_ok = bo_mask_all[a:b]
        rt_ok = rt_mask_all[a:b]
        any_breakout = False

        for side, level in ((LONG, or_high), (SHORT, or_low)):
            funnel["direction_attempts"] += 1
            row, reason = _direction_event(
                instrument=instrument, a=a, b=b, high=high, low=low,
                close=close, open_=open_, ts=ts, elapsed=elapsed, usable=usable,
                breakout_ok=bo_ok, retest_ok=rt_ok, level=level, atr=atr,
                tolerance_atr=tolerance_atr, side=side,
            )
            if reason != NO_BREAKOUT:
                any_breakout = True
            if row is None:
                funnel[reason] += 1
                continue
            row.update({
                "session": int(sess[a]),
                "session_ordinal": int(lv["session_ordinal"][a]),
                "or_high": or_high,
                "or_low": or_low,
                "or_range": or_high - or_low,
                "session_last_bar": int(lv["session_last_bar"][a]),
            })
            funnel["candidates"] += 1
            rows.append(row)

        if any_breakout:
            funnel["sessions_with_any_breakout"] += 1

    return rows, funnel


def bucket_timestamps(series: Series | None) -> set[int] | None:
    """Closing timestamps of a coarser series, or ``None`` for no restriction."""
    if series is None:
        return None
    return {int(t) for t in series.ts}


def event_table(
    instrument: str,
    s5: Series,
    s15: Series,
    s30: Series,
    lv: dict[str, np.ndarray],
    breakout_confirmation: str,
    retest_confirmation: str,
    tolerance_atr: float,
) -> tuple[list[dict], dict[str, int]]:
    """The candidate event table for one (breakout, retest, tolerance) triple."""
    breakout_ts = bucket_timestamps(
        s15 if breakout_confirmation == BREAKOUT_15M else s30
    )
    retest_ts = bucket_timestamps(None if retest_confirmation == RETEST_5M else s15)
    return candidate_events(
        instrument, s5, lv,
        breakout_ts=breakout_ts, retest_ts=retest_ts,
        tolerance_atr=tolerance_atr,
    )
