"""Phase 46 — recording the overlay, and serving the board.

**Where the rows come from.** Phase 45's background worker hands over the rows
it has just journalled, after it has journalled them. That is the whole intake:
no tick-path work, no second evaluation of the same instant, and no
recomputation of any market quantity. Evaluating again would have been a second
copy of the arithmetic, and two copies of a formula agree only until one of
them is corrected.

**Why the hand-over cannot hurt anything.** It is called inside a ``try`` on
the shadow worker thread, which is itself off the production tick path, and a
failure here is counted and dropped. Phase 46 is the last thing in a chain
whose first link has already been written to disk: it can lose its own row, and
nothing upstream notices.

**What a dashboard refresh costs.** One indexed read of the overlay journal,
bounded by the caller's limit. No historical scan, no rescan of the raw store,
no fingerprint recomputation — the definition is memoised once per process. A
board that reran a study on every poll would be a study nobody could afford to
look at.

There is no function in this module that returns anything to a production
caller, and no import here reaches an order, a broker or an execution module.
"""
from __future__ import annotations

import threading
import time

from app.config import settings
from app.research.phase45 import CE, FUTURES, PE, VEHICLE_LABELS
from app.research.phase46 import (
    NO_ORDER_PATH,
    NOT_A_PROMOTION,
    PAPER_ONLY,
    PRODUCTION_UNCHANGED,
    RESEARCH_OVERLAY,
    SHADOW,
    STATES,
    VERSION,
)
from app.research.phase46 import compare as compare_mod
from app.research.phase46 import freeze, overlay
from app.research.phase46 import store as store_mod

_WRITE_LOCK = threading.Lock()
_READ_LOCK = threading.Lock()
_WCON = None
_RCON = None

_STATE: dict[str, object] = {
    "handovers": 0,
    "rows_seen": 0,
    "rows_written": 0,
    "duplicates": 0,
    "failures": 0,
    "last_error": None,
    "last_error_ts": None,
    "last_write_ts": None,
    "last_latency_ms": None,
    "states": {},
}


def enabled() -> bool:
    """Whether the overlay records at all.

    Separate from the Phase 45 switch on purpose: turning the overlay off must
    not turn the shadow journal off, because the journal is the evidence and
    the overlay is only a view of it.
    """
    return bool(settings.phase46_overlay and settings.phase45_shadow)


def definition() -> str:
    """This layer's fingerprint. Memoised in the freeze module."""
    return freeze.definition()


def _writer_connection():
    """One connection for the writer, reused. Held under ``_WRITE_LOCK``."""
    global _WCON
    if _WCON is None:
        _WCON = store_mod.connect()
    return _WCON


def _reader_connection():
    """A second connection for reads, so a poll never queues behind a write."""
    global _RCON
    if _RCON is None:
        _RCON = store_mod.connect()
    return _RCON


def record(rows: list[dict]) -> dict:
    """Classify and journal the overlay rows for one decision instant.

    Called by the Phase 45 worker with the rows it has already written. Returns
    counts, never a decision, and never raises: the caller is a research writer
    on a thread whose failure must not reach a tick.
    """
    if not rows or not enabled():
        return {"rows": 0, "written": 0, "duplicate": 0}
    started = time.time()
    try:
        fingerprint = definition()
        built = [
            {**overlay.build(row, definition=fingerprint), "written_ts": started}
            for row in rows
        ]
        with _WRITE_LOCK:
            counts = store_mod.insert_events(_writer_connection(), built)
        _tally(built, counts, started)
        _hand_over()
        return {"rows": len(built), **counts}
    except Exception as exc:  # research must not break the writer above it
        with _WRITE_LOCK:
            _STATE["failures"] = int(_STATE["failures"]) + 1
            _STATE["last_error"] = f"{type(exc).__name__}: {exc}"
            _STATE["last_error_ts"] = time.time()
        return {"rows": 0, "written": 0, "duplicate": 0, "error": True}


def _hand_over() -> None:
    """Let Phase 47 photograph its session tally, after this journal is written.

    Same arrangement Phase 45 has with this phase, and for the same reason: the
    row is on disk before the next layer is told about it, the import is local
    so a missing package is not a startup failure, and the call is inside a bare
    ``except`` so the layer below cannot break the one above it. Phase 47 also
    throttles itself, so this is not a board read per instant.
    """
    try:
        from app.research.phase47 import service as p47

        p47.maybe_record()
    except Exception:
        pass


def _tally(rows: list[dict], counts: dict, started: float) -> None:
    """Observability for the overlay writer itself, kept in memory."""
    with _WRITE_LOCK:
        _STATE["handovers"] = int(_STATE["handovers"]) + 1
        _STATE["rows_seen"] = int(_STATE["rows_seen"]) + len(rows)
        _STATE["rows_written"] = (
            int(_STATE["rows_written"]) + int(counts.get("written", 0))
        )
        _STATE["duplicates"] = (
            int(_STATE["duplicates"]) + int(counts.get("duplicate", 0))
        )
        _STATE["last_write_ts"] = time.time()
        _STATE["last_latency_ms"] = round((time.time() - started) * 1000.0, 3)
        seen = dict(_STATE["states"]) if isinstance(_STATE["states"], dict) else {}
        for row in rows:
            key = str(row.get("overlay_state"))
            seen[key] = int(seen.get(key, 0)) + 1
        _STATE["states"] = seen


def stats() -> dict:
    """What the writer has done this process. Row counts, not results."""
    with _WRITE_LOCK:
        snapshot = dict(_STATE)
    snapshot["states"] = dict(snapshot.get("states") or {})
    snapshot["enabled"] = enabled()
    snapshot["definition"] = definition()
    snapshot["version"] = VERSION
    snapshot["db_path"] = store_mod.db_path()
    snapshot["status"] = PAPER_ONLY
    snapshot["order_path"] = NO_ORDER_PATH
    snapshot["production_effect"] = PRODUCTION_UNCHANGED
    return snapshot


def journal(*, filters: dict | None = None, limit: int = 200) -> dict:
    """The overlay journal itself, newest decision instant first."""
    with _READ_LOCK:
        con = _reader_connection()
        rows = store_mod.events(con, filters=filters, limit=limit)
        totals = store_mod.counts(con)
        by_state = store_mod.state_counts(con, filters=filters)
    return {
        "rows": rows,
        "count": len(rows),
        "totals": totals,
        "state_counts": by_state,
        "definition": definition(),
        "classification": RESEARCH_OVERLAY,
        "mode": SHADOW,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
    }


def board(*, instrument: str | None = None, limit: int = 60) -> dict:
    """The overlay beside the current signal, for one instrument or all.

    One group per decision instant, each carrying the production call as it was
    recorded and the three vehicle rows as the research evidence saw them. The
    vehicles are listed, never ranked: FUTURES, CE and PE in a fixed order with
    their own states, because choosing between them here would make an
    observation into a recommendation.
    """
    filters = {"instrument": instrument} if instrument else None
    with _READ_LOCK:
        con = _reader_connection()
        rows = store_mod.events(con, filters=filters, limit=max(3, limit * 3))
        choices = store_mod.options(con)
        totals = store_mod.counts(con)
        by_state = store_mod.state_counts(con, filters=filters)
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        key = str(row.get("obs_id"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    instants = [
        {
            "obs_id": key,
            "production": _production_of(groups[key]),
            "vehicles": _vehicle_rows(groups[key]),
            "comparison": overlay.vehicle_comparison(groups[key]),
        }
        for key in order[:max(1, limit)]
    ]
    return {
        "instants": instants,
        "count": len(instants),
        "vehicle_labels": VEHICLE_LABELS,
        "vehicle_order": [FUTURES, CE, PE],
        "states": list(STATES),
        "state_counts": by_state,
        "totals": totals,
        "filters": choices,
        "definition": definition(),
        "inherited": freeze.inherited(),
        "classification": RESEARCH_OVERLAY,
        "mode": SHADOW,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
    }


def _production_of(rows: list[dict]) -> dict:
    """The production call at an instant, as Phase 17 recorded it.

    Read from the row and never rewritten. The overlay reports the production
    signal; it does not restate it, normalise it away or decide what it should
    have been.
    """
    first = rows[0] if rows else {}
    return {
        "instrument": first.get("instrument"),
        "session": first.get("session"),
        "decision_ts": first.get("decision_ts"),
        "signal": first.get("production_signal"),
        "signal_normalised": first.get("production_signal_normalised"),
        "engine_class": first.get("engine_class"),
        "selected_vehicle": first.get("engine_selected_vehicle"),
        "direction": first.get("direction"),
        "source": "PRODUCTION_LOGIC_UNCHANGED_READ_ONLY",
    }


def _vehicle_rows(rows: list[dict]) -> list[dict]:
    """The three vehicle rows in a fixed order, CE and PE never merged."""
    order = {FUTURES: 0, CE: 1, PE: 2}
    return sorted(rows, key=lambda r: order.get(str(r.get("vehicle")), 9))


def outcomes(*, limit: int = 100000) -> dict:
    """The A-versus-B outcome comparison, once resolved legs exist."""
    payload = compare_mod.report(limit=limit)
    payload["definition"] = definition()
    payload["classification"] = RESEARCH_OVERLAY
    payload["order_path"] = NO_ORDER_PATH
    payload["production_effect"] = PRODUCTION_UNCHANGED
    return payload


def reset() -> None:
    """Drop the connections and counters. For the smoke suite and the CLI."""
    global _WCON, _RCON
    with _WRITE_LOCK:
        for con in (_WCON, _RCON):
            if con is not None:
                try:
                    con.close()
                except Exception:  # pragma: no cover - closing a dead handle
                    pass
        _WCON = None
        _RCON = None
        _STATE.update({
            "handovers": 0, "rows_seen": 0, "rows_written": 0,
            "duplicates": 0, "failures": 0, "last_error": None,
            "last_error_ts": None, "last_write_ts": None,
            "last_latency_ms": None, "states": {},
        })
