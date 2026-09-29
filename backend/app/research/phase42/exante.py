"""Phase 42 — the ratio the live gate computes, reconstructed before the entry.

Every function here may read only what existed at or before the decision
instant. That is not left to care: the module names its inputs in
:data:`RATIO_INPUTS` and its forbidden fields in :data:`OUTCOME_FIELDS`, and the
smoke parses this file and fails if an outcome field is referenced anywhere in
it. The mistake being guarded against already happened once in this project — a
band table built on the realised peak was read as an entry filter — so the guard
is a parser rather than a paragraph.

The trailing range is the only part that needs care about time. It is taken from
the minutes *strictly before* the minute the decision was taken in, so the
minute of the entry itself cannot contribute to the forecast of its own move.
"""
from __future__ import annotations

import sqlite3
from bisect import bisect_left
from statistics import median

from app.research.phase42 import (
    EX_ANTE_MEASURED,
    EX_ANTE_UNMEASURED,
    FUTURES_DELTA,
    MIN_RANGE_SAMPLES,
    NO_COST,
    NO_DELTA,
    NO_RANGE,
    RANGE_FROM_FUTURES,
    RANGE_FROM_UNDERLYING,
    RANGE_WINDOW,
)

# The three quantities the ratio is built from, and nothing else. Read by the
# freeze so the declared inputs and the hashed behaviour cannot drift apart.
RATIO_INPUTS: tuple[str, ...] = ("delta", "trailing_range_points", "cost_points")
# Fields that only exist once the leg is over. Any mention of one of these in
# this module would be look-ahead, and the smoke enforces their absence.
OUTCOME_FIELDS: tuple[str, ...] = (
    "peak_pct", "mfe_pct", "mae_pct", "giveback", "giveback_pct",
    "net_pct", "gross_pct", "exit_price", "exit_ts", "exit_side",
    "exit_reason", "time_to_peak_min", "resolved",
)

MINUTE = 60.0


class TrailingRange:
    """One instrument's underlying path in a session, as minute observations.

    Built once per (session, instrument) and queried per leg. Minutes are
    deduplicated to the last observation in each minute so that a densely
    sampled minute does not weigh more than a quiet one, which would make the
    typical range a function of the capture rate rather than of the market.
    """

    def __init__(self, minutes: list[float], prices: list[float], source: str):
        self.minutes = minutes
        self.prices = prices
        self.source = source

    def before(self, ts: float) -> tuple[float | None, str | None]:
        """The typical move per minute, from minutes strictly before ``ts``.

        Strictly: the decision's own minute is excluded, because part of it had
        not happened when the decision was taken and including it would let the
        move being forecast contribute to its own forecast.
        """
        cut = bisect_left(self.minutes, ts - (ts % MINUTE))
        if cut < MIN_RANGE_SAMPLES + 1:
            return None, NO_RANGE
        window = self.prices[max(0, cut - RANGE_WINDOW - 1):cut]
        moves = [abs(b - a) for a, b in zip(window, window[1:]) if b != a]
        if len(moves) < MIN_RANGE_SAMPLES:
            return None, NO_RANGE
        return round(median(moves), 4), None


def trailing_ranges(
    con: sqlite3.Connection, session: str,
) -> dict[str, TrailingRange]:
    """Per-instrument minute series for one session, from the raw store.

    The underlying carried on option quotes is preferred; a futures-only
    instrument has none, so its traded price stands in and the substitution is
    labelled rather than hidden.
    """
    rows = con.execute(
        "SELECT o.instrument AS instrument, q.ts AS ts,"
        " q.underlying AS underlying, q.traded AS traded, q.vehicle AS vehicle"
        " FROM raw_quote q JOIN raw_observation o ON o.obs_id = q.obs_id"
        " WHERE o.session = ? ORDER BY q.ts",
        (session,),
    )
    picked: dict[str, dict[float, tuple[float, str]]] = {}
    for row in rows:
        instrument = str(row["instrument"] or "")
        if not instrument:
            continue
        price, source = None, ""
        if isinstance(row["underlying"], (int, float)) and row["underlying"] > 0:
            price, source = float(row["underlying"]), RANGE_FROM_UNDERLYING
        elif (str(row["vehicle"] or "") == "FUTURES"
                and isinstance(row["traded"], (int, float))
                and row["traded"] > 0):
            price, source = float(row["traded"]), RANGE_FROM_FUTURES
        if price is None:
            continue
        ts = float(row["ts"])
        slot = picked.setdefault(instrument, {})
        minute = ts - (ts % MINUTE)
        # Later observation in the same minute wins; the rows arrive in ts order.
        if minute not in slot or source == RANGE_FROM_UNDERLYING:
            slot[minute] = (price, source)
    out: dict[str, TrailingRange] = {}
    for instrument, slot in picked.items():
        minutes = sorted(slot)
        prices = [slot[m][0] for m in minutes]
        sources = {slot[m][1] for m in minutes}
        source = (RANGE_FROM_UNDERLYING if RANGE_FROM_UNDERLYING in sources
                  else RANGE_FROM_FUTURES)
        out[instrument] = TrailingRange(minutes, prices, source)
    return out


def leg_delta(vehicle: str, delta: object) -> float | None:
    """The leg's sensitivity to a move in what it is written on.

    A futures leg is one-for-one by definition, so it needs no captured delta;
    an option leg without one cannot be forecast and says so.
    """
    if vehicle == "FUTURES":
        return FUTURES_DELTA
    if isinstance(delta, (int, float)) and delta != 0:
        return abs(float(delta))
    return None


def ratio(
    *, delta: float | None, trailing_range_points: float | None,
    cost_points: object, entry_price: object,
) -> dict:
    """The gate's own quantity: forecast move over the leg's own round trip.

    Returns the ratio with its inputs and, when it cannot be formed, which
    input was missing — the gate treats an unmeasurable leg as unmeasured
    rather than as refused, so which one it was has to survive into the report.
    """
    cost = (float(cost_points)
            if isinstance(cost_points, (int, float)) and cost_points > 0
            else None)
    entry = (float(entry_price)
             if isinstance(entry_price, (int, float)) and entry_price > 0
             else None)
    if delta is None:
        return _unmeasured(NO_DELTA)
    if trailing_range_points is None:
        return _unmeasured(NO_RANGE)
    if cost is None or entry is None:
        return _unmeasured(NO_COST)
    expected = delta * trailing_range_points
    return {
        "evidence": EX_ANTE_MEASURED,
        "reason": None,
        "delta": round(delta, 4),
        "trailing_range_points": trailing_range_points,
        "expected_move_points": round(expected, 4),
        "cost_points": round(cost, 4),
        "cost_pct_of_entry": round(100.0 * cost / entry, 4),
        "ratio": round(expected / cost, 4),
    }


def _unmeasured(reason: str) -> dict:
    return {
        "evidence": EX_ANTE_UNMEASURED, "reason": reason, "delta": None,
        "trailing_range_points": None, "expected_move_points": None,
        "cost_points": None, "cost_pct_of_entry": None, "ratio": None,
    }


def admits(value: float | None, multiple: float) -> bool | None:
    """Whether an arm at ``multiple`` would admit this leg.

    ``None`` for an unmeasurable leg: the live gate neither admits nor refuses
    on economics it could not compute, and collapsing that into either answer
    would measure a gate the engine does not run.
    """
    return None if value is None else value >= multiple
