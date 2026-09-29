"""Phase 54 — expansion, pullback, reclaim, next-session-open entry.

The sequence, in the order the calendar reveals it, per direction:

1. **expansion** — a completed daily close beyond the highest high (or lowest
   low) of the previous ``lookback`` completed sessions. The expansion session
   is a setup, never an entry;
2. **pullback** — inside the next ``window`` completed sessions, a session whose
   range actually reaches back to the level. A completed close back **inside**
   the range at any point in the window kills the setup outright;
3. **reclaim** — a completed close beyond the level again, at or after the
   touching session, inside the same window. The touching session may be the
   reclaiming session (see ``RECLAIM_BASIS``);
4. **entry** — the **next session's open** after the reclaim;
5. **once** — the first qualifying sequence per expansion. Scanning on for a
   later, better-shaped one would select on a condition correlated with the
   move that follows it.

Only ``lookback`` and ``pullback_window`` change **which instants are entered**,
so the expensive detection runs four times per instrument rather than 128. The
stop buffer, the risk cap, the target, the hold and the gate change what happens
to those instants — and, because the rule holds at most one position at a time,
they also change *which* of them are reachable. That second effect is why the
acceptance walk in ``study`` is sequential rather than a filter.

A diagnostic is counted beside the frozen detection: how many of these events
would survive the **stricter** reading in which the reclaim must be a session
strictly later than the touch. It is reported and never graded, so a reader can
price the interpretation this phase had to make.
"""
from __future__ import annotations

import numpy as np

from app.research.phase54 import (
    INVALIDATED,
    LONG_CONTINUATION,
    NO_EXPANSION,
    NO_FORWARD_SESSION,
    NO_PULLBACK,
    NO_RECLAIM,
    NOT_KNOWABLE,
    SHORT_CONTINUATION,
)
from app.research.phase54.execute import LONG, SHORT, cost_at_price


def _sequence(
    *,
    daily: dict[str, np.ndarray],
    level: float,
    k: int,
    window: int,
    side: int,
) -> tuple[dict | None, str]:
    """The pullback/reclaim sequence after the expansion at session ``k``.

    Returns the touch/reclaim session indices and the pullback extreme, or the
    reason there was no trade. Every session inspected is complete.
    """
    n = daily["close"].size
    high, low, close = daily["high"], daily["low"], daily["close"]
    last_j = min(k + int(window), n - 1)
    touch: int | None = None

    for j in range(k + 1, last_j + 1):
        inside = close[j] < level if side == LONG else close[j] > level
        if inside:
            # A completed close back inside the range ends the setup. It is not
            # a deeper pullback and it is not a slower reclaim.
            return None, INVALIDATED
        reached = low[j] <= level if side == LONG else high[j] >= level
        if reached:
            touch = j
            break
    if touch is None:
        return None, NO_PULLBACK

    # The touching session's own close is already beyond the level — the
    # ``inside`` test above would have returned otherwise — so under the frozen
    # reading it is the reclaim. ``strict_reclaim_ordinal`` answers the separate
    # diagnostic question: would a *later* session inside the same window also
    # have closed beyond the level, as the stricter reading demands? It never
    # changes the frozen geometry.
    strict: int | None = None
    if touch < last_j:
        nxt = touch + 1
        inside_next = close[nxt] < level if side == LONG else close[nxt] > level
        strict = None if inside_next else nxt
    extreme = float(low[touch]) if side == LONG else float(high[touch])
    return {
        "touch_ordinal": int(touch),
        "reclaim_ordinal": int(touch),
        "pullback_extreme": extreme,
        "pullback_sessions": int(touch - k),
        "strict_reclaim_available": strict is not None,
        "strict_reclaim_ordinal": int(strict) if strict is not None else -1,
    }, ""


def events(
    instrument: str,
    daily: dict[str, np.ndarray],
    prior_high: np.ndarray,
    prior_low: np.ndarray,
    atr: np.ndarray,
    *,
    lookback: int,
    window: int,
) -> tuple[list[dict], dict[str, int]]:
    """Every detected entry candidate for one (lookback, window) pair.

    The rows are candidates, not trades: the stop cap, the cost gate and the
    one-position rule are applied later, because each of them depends on a
    parameter this detection deliberately does not know about.
    """
    n = daily["close"].size
    close = daily["close"]
    funnel: dict[str, int] = {
        "sessions": n,
        "direction_attempts": 0,
        NOT_KNOWABLE: 0,
        NO_EXPANSION: 0,
        NO_PULLBACK: 0,
        INVALIDATED: 0,
        NO_RECLAIM: 0,
        NO_FORWARD_SESSION: 0,
        "expansions": 0,
        "candidates": 0,
        # Diagnostic only — see the module docstring.
        "candidates_under_the_strict_reclaim_reading": 0,
    }
    rows: list[dict] = []

    for k in range(n):
        for side, level in (
            (LONG, float(prior_high[k])), (SHORT, float(prior_low[k]))
        ):
            funnel["direction_attempts"] += 1
            a = float(atr[k])
            if not (np.isfinite(level) and np.isfinite(a) and a > 0):
                funnel[NOT_KNOWABLE] += 1
                continue
            expanded = close[k] > level if side == LONG else close[k] < level
            if not expanded:
                funnel[NO_EXPANSION] += 1
                continue
            funnel["expansions"] += 1
            seq, reason = _sequence(
                daily=daily, level=level, k=k, window=window, side=side
            )
            if seq is None:
                funnel[reason] += 1
                continue
            entry_k = seq["reclaim_ordinal"] + 1
            if entry_k >= n:
                funnel[NO_FORWARD_SESSION] += 1
                continue

            entry = float(daily["open"][entry_k])
            gate_cost = float(cost_at_price(instrument, np.array([entry]))[0])
            funnel["candidates"] += 1
            if seq["strict_reclaim_available"]:
                funnel["candidates_under_the_strict_reclaim_reading"] += 1
            rows.append({
                "instrument": instrument,
                "lookback": int(lookback),
                "pullback_window": int(window),
                "direction": (
                    LONG_CONTINUATION if side == LONG else SHORT_CONTINUATION
                ),
                "side": int(side),
                "atr": a,
                "level": level,
                "expansion_ordinal": int(k),
                "expansion_ts": int(daily["close_ts"][k]),
                "expansion_close": float(close[k]),
                "expansion_beyond_atr": float(abs(close[k] - level) / a),
                "touch_ordinal": seq["touch_ordinal"],
                "reclaim_ordinal": seq["reclaim_ordinal"],
                "reclaim_ts": int(daily["close_ts"][seq["reclaim_ordinal"]]),
                "pullback_extreme": seq["pullback_extreme"],
                "pullback_sessions": seq["pullback_sessions"],
                "strict_reclaim_available": seq["strict_reclaim_available"],
                "entry_ordinal": int(entry_k),
                "entry_session": int(daily["session"][entry_k]),
                "entry_ts": int(daily["open_ts"][entry_k]),
                "fill_index": int(daily["first_i"][entry_k]),
                "entry": entry,
                "gate_cost_points": gate_cost,
            })
    rows.sort(key=lambda r: (r["entry_ordinal"], -r["side"]))
    return rows, funnel
