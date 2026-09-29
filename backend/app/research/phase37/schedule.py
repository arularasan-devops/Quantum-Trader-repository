"""Take the session snapshot by itself, once per session, after the last close.

Why this exists: the snapshot is the only artefact the floors are counted from,
and until now it depended on somebody running a command at the right time of
night. Ten captured sessions produced two countable ones, and the eight were not
lost to a bug — they were lost to nobody being there at 23:40. A bar that is only
met when an operator remembers is not a bar, it is a habit.

What it deliberately does NOT do, and why each refusal matters:

**It never files a snapshot against a past date.** The tabs report cumulative
totals over the whole store, so a collection run this evening describes the store
as it is *now*. Writing that under last Thursday's date would put today's numbers
into a session that had fewer of them, and the inflated count would land on the
one number the floors are measured against. A session whose window passed with
the app down stays uncollected, and the rollup keeps saying so.

**It only fires inside the window it is named for.** Between the trigger time and
IST midnight, for the current IST date, and nowhere else. A restart at 09:00 does
not sweep up yesterday.

**It retries a partial session, spaced out, and then stops.** Snapshot writes are
append-only, so a retry adds an attempt beside the first rather than replacing
it. Attempts are five minutes apart rather than one, because three tries inside
three minutes would all fail for whatever reason the first did; five is what fits
before midnight closes the window. The budget exists so a tab that is down all
night cannot spin against the backend until morning — and a session that stays
partial stays partial on the record, uncounted, rather than being retried into
the next day where it would no longer be the same evidence.

Diagnostic bookkeeping only: it reads the read-only research tabs over loopback
HTTP, writes JSON under ``data/session_evidence``, and touches no capture path,
no signal, no order path.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

from app.research.phase37 import (
    DEFAULT_BASE_URL,
    SESSION_COMPLETE,
)
from app.research.phase37 import collect as p37collect

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# 23:40 IST: ten minutes after the MCX close at 23:30, which is the last close of
# the Indian trading day, and far enough before midnight that a slow tab cannot
# push the collection into the next calendar date.
TRIGGER_IST = "23:40"

# How often the thread wakes to look at the clock. Small enough that the window
# cannot be stepped over, large enough to cost nothing.
POLL_SEC = 60.0

# Attempts per session, counting the first. A partial session is worth retrying —
# one tab can be briefly unavailable — but not indefinitely.
MAX_ATTEMPTS = 3

# Gap between attempts: 23:40, 23:45, 23:50. Three attempts a minute apart would
# all fail for the same reason, so they are spaced — but only as far as the window
# allows. The window ends at IST midnight because after it the current date is
# tomorrow, and filing tonight's cumulative tabs under yesterday is the
# back-dating this module exists to refuse. Five minutes is what fits.
RETRY_AFTER_SEC = 5 * 60.0

# Minutes before the trigger at which the slow tabs are rebuilt off the request
# path. The CAS report is rebuilt from the whole CAS journal on every request, so
# its response time grows with the store: it took ~30s on 2026-09-15 and timed
# out, costing that session its CAS tab and therefore the session. Warming it
# here means the collection reads a finished answer instead of racing the
# rebuild. This computes nothing the endpoint would not compute itself and
# serves nothing stale — the cache is bound to the journal it was built from.
PREWARM_MINUTES_BEFORE = 5

# A warm that raised is recorded under this state and left unmarked, so the next
# poll tries again. A failed warm is not a failed snapshot: the collection still
# rebuilds the report itself, it just pays for it inside the tab's budget.
PREWARM_FAILED = "PREWARM_RAISED_THE_COLLECTION_WILL_REBUILD_INSIDE_ITS_OWN_BUDGET"

_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
# Last attempt per session, on this process's clock, so a retry can be spaced
# without reading a file's mtime (which is wall-clock and would not agree with a
# supplied timestamp). A restart forgets it, which at worst allows one extra
# attempt inside the gap — the on-disk attempt count still caps the total.
_ATTEMPTED_AT: dict[str, float] = {}
_WARMED: dict[str, object] = {}
_STATE: dict[str, object] = {
    "last_prewarm_session": None,
    "last_prewarm": None,
    "running": False,
    "last_attempt_session": None,
    "last_attempt_at": None,
    "last_status": None,
    "attempts_this_session": 0,
    "failures": 0,
    "last_error": None,
}


def base_url() -> str:
    """Where to fetch the tabs from. Loopback unless overridden."""
    return os.environ.get("QT_PHASE37_BASE_URL", DEFAULT_BASE_URL).strip() \
        or DEFAULT_BASE_URL


def _trigger_minute() -> int:
    hh, mm = TRIGGER_IST.split(":")
    return int(hh) * 60 + int(mm)


def in_prewarm_window(now: float | None = None) -> bool:
    """Whether the clock is inside the warm-up run-up to the trigger."""
    when = dt.datetime.fromtimestamp(
        float(now) if now is not None else time.time(), _IST)
    minute = when.hour * 60 + when.minute
    return minute >= _trigger_minute() - PREWARM_MINUTES_BEFORE


def in_window(now: float | None = None) -> bool:
    """Whether the clock is between the trigger and IST midnight."""
    when = dt.datetime.fromtimestamp(
        float(now) if now is not None else time.time(), _IST)
    return when.hour * 60 + when.minute >= _trigger_minute()


def existing_status(session: str) -> str | None:
    """What a previous attempt at this session recorded, if any.

    Reads the manifest rather than counting files: a directory can hold several
    attempts, and what matters is whether any of them came out complete.
    """
    manifest = os.path.join(p37collect.root_dir(), session, "manifest.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            return str(json.load(fh).get("session_status") or "") or None
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def attempts_made(session: str) -> int:
    """Snapshot attempts already written for this session's first tab.

    The first tab is representative because the collector writes every tab on
    every attempt, and counting one tab avoids calling a partial write an attempt
    several times over.
    """
    directory = os.path.join(p37collect.root_dir(), session)
    if not os.path.isdir(directory):
        return 0
    from app.research.phase37 import rollup as p37rollup

    return len(p37rollup.attempts(directory, "A_PLUS_PAPER"))


def due(now: float | None = None) -> str | None:
    """The session to snapshot right now, or None.

    None covers all of: too early in the day, already complete, and out of
    retries. The caller does not need to know which — but ``status()`` reports it.
    """
    if not in_window(now):
        return None
    session = p37collect.session_date(now)
    if existing_status(session) == SESSION_COMPLETE:
        return None
    if attempts_made(session) >= MAX_ATTEMPTS:
        return None
    with _LOCK:
        last = _ATTEMPTED_AT.get(session)
    if last is not None:
        clock = float(now) if now is not None else time.time()
        if clock - last < RETRY_AFTER_SEC:
            return None
    return session


def prewarm_once(now: float | None = None) -> dict | None:
    """Rebuild the slow tabs' cache before the trigger. Returns what it did.

    Once per session on success: the cache is bound to the journals, so a second
    warm in the same evening either finds it current or rebuilds it because the
    store moved — and a store that is still moving will be rebuilt by the
    collection anyway. A warm that raised is not marked done, so the next poll
    tries again; the mark is claimed first so two polls cannot both rebuild.
    None means it was not the moment, not that the warm failed.
    """
    if not in_prewarm_window(now):
        return None
    session = p37collect.session_date(now)
    with _LOCK:
        if _WARMED.get(session):
            return None
        _WARMED[session] = True
    from app.research.phase18 import cas_cache

    try:
        outcome = cas_cache.ensure()
    except Exception as exc:
        failure = {"state": PREWARM_FAILED, "error": f"{type(exc).__name__}: {exc}"}
        with _LOCK:
            _WARMED.pop(session, None)
            _STATE["last_prewarm_session"] = session
            _STATE["last_prewarm"] = failure
        return failure
    with _LOCK:
        _STATE["last_prewarm_session"] = session
        _STATE["last_prewarm"] = outcome
    return outcome


def run_once(now: float | None = None) -> dict | None:
    """Snapshot the current session if it is due. Returns the manifest or None."""
    session = due(now)
    if session is None:
        return None
    with _LOCK:
        _ATTEMPTED_AT[session] = (
            float(now) if now is not None else time.time())
    manifest = p37collect.collect(base_url=base_url(), session=session)
    with _LOCK:
        _STATE["last_attempt_session"] = session
        _STATE["last_attempt_at"] = dt.datetime.now(_IST).isoformat()
        _STATE["last_status"] = manifest.get("session_status")
        _STATE["attempts_this_session"] = attempts_made(session)
    return manifest


def _loop() -> None:
    """Watch the clock until asked to stop. Never raises.

    A failure here must not kill the thread, or the automation quietly reverts to
    the manual arrangement it was written to replace — and nobody would know.
    """
    while not _STOP.wait(POLL_SEC):
        try:
            prewarm_once()
            run_once()
        except Exception as exc:  # noqa: BLE001 - a scheduler must outlive a bad night
            with _LOCK:
                _STATE["failures"] = int(_STATE["failures"] or 0) + 1
                _STATE["last_error"] = f"{type(exc).__name__}: {exc}"


def start() -> bool:
    """Start the scheduler thread once. Returns whether it is running."""
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return True
        _STOP.clear()
        _THREAD = threading.Thread(
            target=_loop, name="phase37-session-snapshot", daemon=True)
        _THREAD.start()
        _STATE["running"] = True
    return True


def stop() -> None:
    """Test and shutdown seam. Leaves every written snapshot alone."""
    global _THREAD
    _STOP.set()
    thread = _THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    with _LOCK:
        _THREAD = None
        _STATE["running"] = False


def status(now: float | None = None) -> dict:
    """What the scheduler is waiting for, and what it last did."""
    session = p37collect.session_date(now)
    prior = existing_status(session)
    attempts = attempts_made(session)
    if prior == SESSION_COMPLETE:
        waiting = "ALREADY_COLLECTED_THIS_SESSION_IS_COMPLETE"
    elif not in_window(now):
        waiting = f"WAITING_FOR_{TRIGGER_IST}_IST_THE_LAST_CLOSE_HAS_NOT_PASSED"
    elif attempts >= MAX_ATTEMPTS:
        waiting = "OUT_OF_ATTEMPTS_THIS_SESSION_STAYS_PARTIAL_ON_THE_RECORD"
    elif due(now) is None:
        waiting = "PARTIAL_WAITING_OUT_THE_GAP_BEFORE_THE_NEXT_ATTEMPT"
    else:
        waiting = "DUE_NOW"
    with _LOCK:
        state = dict(_STATE)
    state.update({
        "phase": "PHASE37_SESSION_SNAPSHOT_SCHEDULE",
        "trigger_ist": TRIGGER_IST,
        "poll_sec": POLL_SEC,
        "max_attempts": MAX_ATTEMPTS,
        "retry_after_sec": RETRY_AFTER_SEC,
        "base_url": base_url(),
        "session": session,
        "session_status_on_disk": prior,
        "attempts_on_disk": attempts,
        "in_window": in_window(now),
        "prewarm_minutes_before": PREWARM_MINUTES_BEFORE,
        "in_prewarm_window": in_prewarm_window(now),
        "waiting_for": waiting,
        "back_fill": (
            "NEVER_A_SNAPSHOT_IS_ONLY_EVER_FILED_AGAINST_THE_DAY_IT_WAS_TAKEN"),
        "research_only": True,
        "paper_only": True,
    })
    return state
