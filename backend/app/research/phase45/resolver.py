"""Phase 45 — what happened after a shadow signal, resolved on measured quotes.

Runs after the fact, over journalled events that carry a shadow action, and
writes one ``paper_outcome`` row each. The forward path is Phase 35's:
:func:`app.research.phase35.path.forward_samples` returns later quotes for *the
same contract symbol* in *the same session* and nothing else — no interpolation,
no forward fill, no next-session bar — and :func:`app.research.phase35.path.
resolve` turns them into the horizon table, the T1/T2/T3 flags and the giveback
decomposition. An event whose contract was quoted once and never again resolves
to ``NO_FORWARD_PATH_CAPTURED``, which is a real answer about the contract's
liquidity rather than a missing row.

The exit is the executable side: a long option leaves at the BID, a short
futures leg at the ASK. That is :func:`app.research.phase35.book.exit_fill`,
called inside ``resolve``, so this module cannot quietly exit at a midpoint.

**One direction only.** This module imports the evaluator's package constants
but never the evaluator, and the evaluator never imports this module. Outcomes
are written into their own table and are not readable by anything that decides
an admission — the property is enforced by the import graph and checked by
``_smoke_phase45``.
"""
from __future__ import annotations

import sqlite3
import time

from app.research.phase35 import path as p35path
from app.research.phase35 import store as p35store
from app.research.phase45 import (
    PAPER_ENTRY,
    RESOLVED,
    SHADOW_BUY,
    SHADOW_SELL,
    store,
)
from app.research.phase45.freeze import definition

SIGNAL_ACTIONS: tuple[str, ...] = (SHADOW_BUY, SHADOW_SELL)

# Outcome statuses. A refusal is a status, not an absent row: an event skipped
# silently is indistinguishable from one never reached.
RESOLVED_OK = "RESOLVED"
NO_PATH = "NO_FORWARD_PATH_CAPTURED"
NO_ENTRY = "NO_EXECUTABLE_ENTRY_RECORDED"
NO_RAW_STORE = "NO_RAW_STORE_TO_RESOLVE_AGAINST"


def resolve_pending(
    con: sqlite3.Connection,
    *,
    raw_path: str | None = None,
    limit: int = 500,
    min_age_sec: float = 900.0,
    now: float | None = None,
) -> dict:
    """Resolve journalled signals old enough to have a forward path.

    ``min_age_sec`` exists so an event minutes old is left alone rather than
    resolved on the two quotes that happen to exist yet and recorded as if that
    were its outcome. It is a delay, not a filter: the same event is picked up
    on the next pass.
    """
    clock = float(now if now is not None else time.time())
    pending = [
        e for e in store.unresolved(con, actions=SIGNAL_ACTIONS, limit=limit)
        if clock - float(e["decision_ts"] or 0.0) >= float(min_age_sec)
    ]
    if not pending:
        return {"considered": 0, "resolved": 0, "written": 0, "duplicate": 0,
                "statuses": {}}
    target = raw_path or p35store.db_path()
    try:
        raw = store.open_read_only(target)
    except sqlite3.OperationalError:
        return {"considered": len(pending), "resolved": 0, "written": 0,
                "duplicate": 0, "statuses": {NO_RAW_STORE: len(pending)}}
    statuses: dict[str, int] = {}
    written = duplicate = 0
    try:
        for event in pending:
            row = resolve_one(raw, event)
            statuses[row["outcome_status"]] = (
                statuses.get(row["outcome_status"], 0) + 1
            )
            counts = store.insert_outcome(con, row)
            written += counts["written"]
            duplicate += counts["duplicate"]
    finally:
        raw.close()
    return {
        "considered": len(pending),
        "resolved": statuses.get(RESOLVED_OK, 0),
        "written": written,
        "duplicate": duplicate,
        "statuses": statuses,
    }


def resolve_one(raw: sqlite3.Connection, event: dict) -> dict:
    """One outcome row for one journalled event."""
    entry = event.get("entry_price")
    contract = event.get("contract")
    if not isinstance(entry, (int, float)) or float(entry) <= 0 or not contract:
        return _empty(event, NO_ENTRY)
    samples = p35path.forward_samples(
        raw,
        symbol=str(contract),
        vehicle=str(event["vehicle"]),
        after_ts=float(event["decision_ts"]),
    )
    if not samples:
        return _empty(event, NO_PATH)
    resolved = p35path.resolve(
        entry_price=float(entry),
        entry_ts=float(event["decision_ts"]),
        vehicle=str(event["vehicle"]),
        direction=str(event.get("direction") or ""),
        samples=samples,
        cost_points=_num(event.get("measured_cost_points")),
    )
    if not resolved.get("horizons"):
        return _empty(event, NO_PATH, reason=resolved.get("reason"))
    close = resolved["horizons"][p35path.SESSION_CLOSE]
    give = resolved["giveback"]
    entry_px = float(entry)
    gross_points = _points(resolved.get("final_gross_pct"), entry_px)
    net_points = _points(resolved.get("final_net_pct"), entry_px)
    return {
        "event_id": event["event_id"],
        "resolved_ts": time.time(),
        "lifecycle": RESOLVED,
        "outcome_status": RESOLVED_OK,
        "reason": None,
        "entry_side": event.get("entry_side"),
        "entry_price": entry_px,
        "entry_ts": float(event["decision_ts"]),
        "exit_side": resolved.get("exit_side"),
        "exit_price": resolved.get("exit_price"),
        "exit_ts": resolved.get("exit_ts"),
        # The last measured quote of the entry session, which is what a paper
        # leg held to the close would have left on. Not a rule, a convention,
        # and named so no reader mistakes it for an exit strategy.
        "exit_trigger": p35path.SESSION_CLOSE,
        "hold_minutes": give.get("hold_minutes"),
        "samples": resolved.get("samples"),
        "gross_pnl_points": gross_points,
        "gross_pct": resolved.get("final_gross_pct"),
        "cost_points": _num(event.get("measured_cost_points")),
        "net_pnl_points": net_points,
        "net_pct": resolved.get("final_net_pct"),
        "mfe_pct": close.get("mfe_pct"),
        "mae_pct": close.get("mae_pct"),
        "peak_pct": give.get("peak_pct"),
        "giveback_pct": give.get("max_giveback_pct"),
        "t1_hit": 1 if close.get("t1") else 0,
        "t2_hit": 1 if close.get("t2") else 0,
        "t3_hit": 1 if close.get("t3") else 0,
        "definition": definition(),
    }


def _empty(event: dict, status: str, *, reason: str | None = None) -> dict:
    """A resolution that could not be measured, recorded rather than skipped.

    It occupies the same primary key as a real outcome would, so the event is
    not re-attempted forever, and it carries the reason so the unresolved count
    on the board can be explained instead of merely counted.
    """
    return {
        "event_id": event["event_id"],
        "resolved_ts": time.time(),
        "lifecycle": PAPER_ENTRY,
        "outcome_status": status,
        "reason": reason,
        "entry_side": event.get("entry_side"),
        "entry_price": _num(event.get("entry_price")),
        "entry_ts": float(event["decision_ts"]),
        "definition": definition(),
    }


def _num(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


def _points(pct: object, entry: float) -> float | None:
    """A percentage move restated in points of the contract it was measured on."""
    if not isinstance(pct, (int, float)) or isinstance(pct, bool):
        return None
    return round(float(pct) * entry / 100.0, 4)
