"""Execution funnel — where a BUY dies between the signal and the fill.

The gate trace explains why the ENGINE refused. This explains the other half of
the complaint: a BUY is on screen, no order appears, and nothing anywhere says
why. Every early return in the auto-entry path reports itself here with the
stage it died at, the value it saw and the threshold it failed, so
"BUY but no execution" always has an answer.

Measurement only: nothing here is read by any trading decision, and every
recording call is a no-op as far as the caller's control flow is concerned.

The counters are also appended to ``exec_funnel.jsonl`` under the data directory.
Until that existed the funnel lived in memory only, so the question "why did the
book take 7 entries when the cap was 50?" became unanswerable the moment the
backend restarted: the evidence was gone and the answer could only be guessed.
The file makes the next session's answer a measurement. Writing is best-effort
and never raises into a live tick.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, deque
from typing import Callable

from app.config import settings

LOG_NAME = "exec_funnel.jsonl"

# A rollup line carries the stage counts, which no single event does. One per
# interval keeps the file readable over a session instead of one line per tick.
_ROLLUP_MIN_SEC = 60.0

# The funnel a BUY must walk, in order. Kept as data so the dashboard can render
# the stages even before any of them has been hit.
STAGES: tuple[str, ...] = (
    "SIGNAL",       # engine produced an actionable BUY
    "RISK",         # account / instrument level permission
    "VALIDATION",   # the contract itself is tradeable (premium, feed, opportunity)
    "EXECUTION",    # bot-level entry rules (trigger, no-chase, concurrency, capital)
    "BROKER",       # order sent to the broker
    "ACCEPTED",     # broker accepted it
    "FILLED",       # a fill came back
)

_MAX_EVENTS = 300

_lock = threading.Lock()
# Observers are notified of every stage reached and every refusal, so a second
# ledger (the signal lifecycle) can attribute them to the signal they belong to
# without every gate in the execution path having to report itself twice. An
# observer is measurement: it cannot alter the funnel event, and its failure is
# swallowed, because this runs inside the tick path.
_observers: list[Callable[[dict], None]] = []
_reached: Counter[str] = Counter()
_blocked: Counter[tuple[str, str]] = Counter()
_events: deque[dict] = deque(maxlen=_MAX_EVENTS)
_last_rollup: float = 0.0


def log_path() -> str:
    return os.path.join(settings.data_dir, LOG_NAME)


def _append(rec: dict) -> None:
    try:
        if not settings.exec_funnel_log:
            return
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _rollup_due(now: float) -> dict | None:
    global _last_rollup
    if now - _last_rollup < _ROLLUP_MIN_SEC:
        return None
    _last_rollup = now
    return {
        "ts": int(now),
        "kind": "COUNTS",
        "reached": {s: _reached.get(s, 0) for s in STAGES},
        "blocked": {f"{stage}:{blocker}": n
                    for (stage, blocker), n in _blocked.items()},
    }


def subscribe(fn: Callable[[dict], None]) -> None:
    """Register an observer of funnel events. Registered once, at import time."""
    with _lock:
        if fn not in _observers:
            _observers.append(fn)


def _notify(event: dict) -> None:
    for fn in list(_observers):
        try:
            fn(event)
        except Exception:
            continue


def reached(stage: str) -> None:
    """A candidate BUY got as far as ``stage``."""
    now = time.time()
    with _lock:
        _reached[stage] += 1
        rollup = _rollup_due(now)
    _notify({"kind": "REACHED", "stage": stage, "ts": now})
    if rollup is not None:
        _append(rollup)


def blocked(
    stage: str,
    blocker: str,
    *,
    instrument: str = "",
    option: str = "",
    value: float | str | None = None,
    threshold: float | str | None = None,
    where: str = "",
    reason: str = "",
    secondary: str | None = None,
) -> None:
    """A candidate BUY died at ``stage``.

    ``where`` is the code location (``file:function``) so a blocker on the
    dashboard can be traced to the line that produced it without a grep.
    """
    now = time.time()
    event = {
        "ts": int(now),
        # The whole-second ``ts`` is what the log and the dashboard show; this is
        # the same instant unrounded, so a reader can tell whether a refusal
        # belongs to the tick it is being compared against.
        "ts_precise": now,
        "kind": "BLOCKED",
        "stage": stage,
        "primary_blocker": blocker,
        "secondary_blocker": secondary,
        "instrument": instrument,
        "option": option,
        "value": value,
        "threshold": threshold,
        "where": where,
        "reason": reason,
    }
    with _lock:
        _blocked[(stage, blocker)] += 1
        _events.append(event)
        rollup = _rollup_due(now)
    _notify(event)
    _append(event)
    if rollup is not None:
        _append(rollup)


def last_block(instrument: str, *, since: float = 0.0,
               option: str = "") -> dict | None:
    """The most recent refusal recorded for ``instrument``, or None.

    Read by the shadow book so an ungated entry can name the gate that stopped
    the gated one. Read by nothing that trades: the return value can only end up
    in a research ledger, never in a decision, a size or an order.

    ``since`` bounds it to the current tick — a refusal from ten minutes ago is
    not the reason this call was not taken. The comparison uses the unrounded
    stamp, because rounding to the second can place a refusal from this tick
    before the tick that produced it.
    """
    with _lock:
        for event in reversed(_events):
            if event["instrument"] != instrument:
                continue
            if float(event.get("ts_precise", event["ts"])) < since:
                break
            if option and event["option"] and event["option"] != option:
                continue
            return dict(event)
    return None


def summary(limit: int = 50) -> dict:
    """Funnel counts, blocker league table and the most recent refusals."""
    with _lock:
        reached_counts = {s: _reached.get(s, 0) for s in STAGES}
        rows = [
            {
                "stage": stage,
                "blocker": blocker,
                "count": n,
            }
            for (stage, blocker), n in _blocked.most_common()
        ]
        recent = list(_events)[-limit:][::-1]
    signals = max(1, reached_counts["SIGNAL"])
    filled = reached_counts["FILLED"]
    return {
        "stages": STAGES,
        "reached": reached_counts,
        "blocked": rows,
        "recent": recent,
        "executable_pct": round(100.0 * filled / signals, 1),
        "blocked_after_signal": sum(r["count"] for r in rows),
        "persisted_to": log_path() if settings.exec_funnel_log else None,
        "note": (
            "Counts in this response are since the backend last started. Every "
            "refusal, plus a stage-count rollup, is also appended to "
            f"{LOG_NAME} so a past session can be explained after a restart. "
            "'executable_pct' is fills divided by actionable BUYs, so it answers "
            "'what share of BUY signals actually became a position?'."
        ),
    }


def reset() -> None:
    """Test helper — clears the counters."""
    global _last_rollup
    with _lock:
        _reached.clear()
        _blocked.clear()
        _events.clear()
        _last_rollup = 0.0
