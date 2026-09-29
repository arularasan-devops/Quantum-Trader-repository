"""Phase 52 §3/§4/§5/§6/§11 — the one mechanism, detected causally.

The sequence, in the order the session reveals it:

1. **gap** — the session's open is clear of the previous day's extreme by at
   least the declared ATR fraction. Known at the open;
2. **wait** — nothing may trigger on a bar closing inside the first fifteen
   minutes. Those bars define the opening area instead;
3. **extension** — a completed bar trades beyond the opening area, in the
   direction of the gap. This is the "price must extend" clause: without it, a
   session that opened above yesterday's high and sagged all morning is not a
   *failed* extension, it is a session that never extended;
4. **failure** — a completed bar closes back inside the previous day's range.
   That close is the trigger event;
5. **entry** — the next bar's open, short after an upside failure, long after a
   downside one;
6. **once** — the **first** failure close in the session is the trigger, and if
   it fails the stop cap or the cost gate the session is refused. Scanning
   forward for a later trigger that happens to clear the gate would select on a
   condition correlated with the move that follows it, which is a look-ahead
   wearing the costume of a filter.

A structural fact about §13's grid, which turns out to be the most important
thing in this phase: the gap threshold, the stop cap and the cost gate are all
**filters on the same event**, and none of them changes where the trade enters,
where it stops, where it targets or when it exits. Only the confirmation
timeframe changes the trigger bar. So the thirty-six parameterizations are
eighteen subsets of one 5-minute event table plus eighteen subsets of one
15-minute table — not thirty-six independent tests, and the outcome of a given
session is identical in every variant that admits it. The engine therefore
resolves each session's event **once** and lets the variants select.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24.data import Series
from app.research.phase52 import (
    CONFIRM_15M,
    GAP_ATR_THRESHOLDS,
    NO_EXTENSION,
    NO_FAILURE_CLOSE,
    NO_FORWARD_WINDOW,
    NO_GAP,
    NOT_KNOWABLE,
    STOP_BUFFER_ATR,
    STOP_TOO_WIDE,
    TARGET_WRONG_SIDE,
    WAIT_MINUTES,
)
from app.research.phase52 import levels as lv_mod
from app.research.phase52.execute import LONG, SHORT, cost_at_price

GAP_UP = "UPSIDE_GAP_FAILURE_SHORT"
GAP_DOWN = "DOWNSIDE_GAP_FAILURE_LONG"

MIN_GAP_ATR = min(GAP_ATR_THRESHOLDS)


def candidate_events(
    instrument: str,
    s5: Series,
    lv: dict[str, np.ndarray],
    confirm_ts: set[int] | None,
) -> tuple[list[dict], dict[str, int]]:
    """Every session's event, or the reason there wasn't one.

    ``confirm_ts`` restricts the bars whose close may confirm the failure. For
    the 5-minute confirmation it is ``None`` (every completed decision bar may
    confirm); for the 15-minute confirmation it is the set of closing timestamps
    of the 15-minute bars, so the confirming close is the close of a completed
    fifteen-minute bucket and the entry is still the next five-minute bar.

    Returns the candidate rows — sessions that reached a priced entry — and a
    funnel of the refusals that are true for every variant.
    """
    sess = lv["session"]
    bounds = lv_mod.session_bounds(sess.astype(np.int64))
    funnel: dict[str, int] = {
        "sessions": len(bounds), NOT_KNOWABLE: 0, NO_GAP: 0, NO_EXTENSION: 0,
        NO_FAILURE_CLOSE: 0, NO_FORWARD_WINDOW: 0, STOP_TOO_WIDE: 0,
        TARGET_WRONG_SIDE: 0, "candidates": 0,
        # Diagnostic, not a gate: how many sessions opened beyond the previous
        # day's extreme *at all*. Without it a reader cannot tell whether the
        # 0.10 ATR threshold is starving the sample or whether the gap itself
        # is simply rare, and those call for opposite conclusions.
        "opened_beyond_the_level_at_all": 0,
    }
    rows: list[dict] = []

    for a, b in bounds:
        atr = float(lv["atr"][a])
        pdh, pdl, pdc = (
            float(lv["pdh"][a]), float(lv["pdl"][a]), float(lv["pdc"][a])
        )
        day_open = float(lv["day_open"][a])
        if not (np.isfinite(atr) and atr > 0 and np.isfinite(pdh)
                and np.isfinite(pdl) and np.isfinite(pdc)):
            funnel[NOT_KNOWABLE] += 1
            continue

        gap_up_atr = (day_open - pdh) / atr
        gap_down_atr = (pdl - day_open) / atr
        if gap_up_atr >= gap_down_atr:
            direction, gap_atr, side = GAP_UP, gap_up_atr, SHORT
        else:
            direction, gap_atr, side = GAP_DOWN, gap_down_atr, LONG
        if gap_atr > 0:
            funnel["opened_beyond_the_level_at_all"] += 1
        if gap_atr < MIN_GAP_ATR:
            funnel[NO_GAP] += 1
            continue

        elapsed = lv["minutes_since_open"][a:b]
        area_hi = lv["open_area_high"][a:b]
        area_lo = lv["open_area_low"][a:b]
        decision = (elapsed >= float(WAIT_MINUTES)) & np.isfinite(area_hi)
        if not decision.any():
            funnel[NO_EXTENSION] += 1
            continue

        high = s5.high[a:b]
        low = s5.low[a:b]
        close = s5.close[a:b]

        # 3 — the extension beyond the opening area, in the gap's direction.
        if direction == GAP_UP:
            extended = decision & (high > area_hi)
        else:
            extended = decision & (low < area_lo)
        if not extended.any():
            funnel[NO_EXTENSION] += 1
            continue
        ext_k = int(np.nonzero(extended)[0][0])

        # 4 — the first close back inside the previous day's range, at or after
        # the extension. The same bar may both extend and close back inside: the
        # close is by definition the bar's last trade, so the high that extended
        # necessarily happened at or before it, and no intrabar ordering is
        # assumed.
        eligible = decision.copy()
        eligible[:ext_k] = False
        if confirm_ts is not None:
            in_bucket = np.array(
                [int(t) in confirm_ts for t in s5.ts[a:b]], dtype=bool
            )
            eligible &= in_bucket
        if direction == GAP_UP:
            failed = eligible & (close < pdh)
        else:
            failed = eligible & (close > pdl)
        if not failed.any():
            funnel[NO_FAILURE_CLOSE] += 1
            continue
        trig_k = int(np.nonzero(failed)[0][0])

        # 5 — the fill is the next bar's open, inside the same session.
        if trig_k + 1 > (b - a - 1):
            funnel[NO_FORWARD_WINDOW] += 1
            continue
        fill_i = a + trig_k + 1
        entry = float(s5.open[fill_i])

        # 7 — the stop sits beyond the failed extension's extreme.
        if direction == GAP_UP:
            extreme = float(lv["session_high_to_date"][a + trig_k])
            stop = extreme + STOP_BUFFER_ATR * atr
            risk = stop - entry
        else:
            extreme = float(lv["session_low_to_date"][a + trig_k])
            stop = extreme - STOP_BUFFER_ATR * atr
            risk = entry - stop
        if not (risk > 0):
            # The fill gapped past its own stop. There is no trade to take, and
            # counting it as a loss would charge the rule for a bar it never
            # traded on.
            funnel[STOP_TOO_WIDE] += 1
            continue
        stop_atr_needed = risk / atr

        # 8 — the target is the previous day's close, and it has to be on the
        # far side of the entry for the trade to mean anything.
        target = pdc
        if (direction == GAP_UP and not target < entry) or (
            direction == GAP_DOWN and not target > entry
        ):
            funnel[TARGET_WRONG_SIDE] += 1
            continue
        target_distance = abs(entry - target)

        # 9 — the gate's cost, modelled at the entry price, knowable before the
        # trade. Not the cost that will be charged, which uses the real exit.
        gate_cost = float(cost_at_price(instrument, np.array([entry]))[0])
        cost_multiple = (
            target_distance / gate_cost if gate_cost > 0 else float("inf")
        )

        funnel["candidates"] += 1
        rows.append({
            "instrument": instrument,
            "session": int(sess[a]),
            "session_ordinal": int(lv["session_ordinal"][a]),
            "direction": direction,
            "side": int(side),
            "gap_atr": float(gap_atr),
            "atr": atr,
            "pdh": pdh, "pdl": pdl, "pdc": pdc, "pdr": float(lv["pdr"][a]),
            "day_open": day_open,
            "extension_bar": int(ext_k),
            "trigger_bar": int(trig_k),
            "trigger_ts": int(s5.ts[a + trig_k]),
            "trigger_close": float(close[trig_k]),
            "fill_index": int(fill_i),
            "entry_ts": int(s5.ts[fill_i]),
            "entry": entry,
            "failure_extreme": extreme,
            "stop": float(stop),
            "risk_points": float(risk),
            "stop_atr_needed": float(stop_atr_needed),
            "target": float(target),
            "target_distance": float(target_distance),
            "gate_cost_points": gate_cost,
            "cost_multiple": float(cost_multiple),
            "session_last_bar": int(lv["session_last_bar"][a]),
            "minutes_since_open_at_entry": float(lv["minutes_since_open"][fill_i]),
        })

    return rows, funnel


def confirm_timestamps(s15: Series | None) -> set[int] | None:
    """Closing timestamps of the 15-minute bars, or ``None`` for 5-minute."""
    if s15 is None:
        return None
    return {int(t) for t in s15.ts}


def event_table(
    instrument: str,
    s5: Series,
    s15: Series,
    lv: dict[str, np.ndarray],
    confirmation: str,
) -> tuple[list[dict], dict[str, int]]:
    """The candidate event table for one confirmation timeframe."""
    confirm_ts = confirm_timestamps(s15 if confirmation == CONFIRM_15M else None)
    return candidate_events(instrument, s5, lv, confirm_ts)
