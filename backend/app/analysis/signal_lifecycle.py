"""One immutable record of what happened to every signal, end to end.

Part 25/26 of the Phase 11A extension. The measured problem this exists for: a
BUY appears in the Reports journal but never on the dashboard, or on the
dashboard but never in the paper book, and nothing in the system can say which
step dropped it. The funnel records refusals inside the execution path only, and
the journal is written after publication, so a signal that dies before either of
them leaves no trace at all.

This ledger takes a different position: **a signal may be refused at any stage,
but it may never disappear without a stage and a reason.** Every signal gets

    global_signal_id   one id from generation to resolution
    episode_id         the de-duplicated call it belongs to
    event_id           this one transition

and every transition carries a timestamp, a stage, a status, a reason, the value
and threshold that produced it, the instrument, the vehicle and the source.

Guarantees, deliberately narrow so this can run in the tick path:

* append-only JSONL; nothing is rewritten and nothing is deleted;
* observability only — no function here decides, sizes, routes or blocks
  anything, and nothing in the trading path reads it back;
* every write is best-effort: a ledger must never be able to break a tick.

Duplicates and misses are different things (Part 29). Five raw BUY events that
collapse into one episode and one dashboard signal are correct de-duplication;
five raw BUY events with no dashboard signal at all are
``MISSING_DASHBOARD_PUBLICATION``. The episode id is what lets a reader tell
those apart, so it is derived from the call, not from the tick.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from app.config import settings

LEDGER_LOG = "signal_lifecycle.jsonl"

# --- stages, in the order a live signal passes through them -----------------
GENERATED = "GENERATED"
CLASSIFIED = "CLASSIFIED"
PLAN_CREATED = "PLAN_CREATED"
PLAN_VALIDATED = "PLAN_VALIDATED"
DASHBOARD_PUBLISHED = "DASHBOARD_PUBLISHED"
USER_VISIBLE = "USER_VISIBLE"
PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
EXECUTION_CHECK = "EXECUTION_CHECK"
EXECUTION_ACCEPTED = "EXECUTION_ACCEPTED"
EXECUTION_REJECTED = "EXECUTION_REJECTED"
FILLED = "FILLED"
NOT_FILLED = "NOT_FILLED"
POSITION_OPEN = "POSITION_OPEN"
EXITED = "EXITED"
RESOLVED = "RESOLVED"

STAGES = (
    GENERATED, CLASSIFIED, PLAN_CREATED, PLAN_VALIDATED, DASHBOARD_PUBLISHED,
    USER_VISIBLE, PAPER_ELIGIBLE, EXECUTION_CHECK, EXECUTION_ACCEPTED,
    EXECUTION_REJECTED, FILLED, NOT_FILLED, POSITION_OPEN, EXITED, RESOLVED,
)

# Terminal-by-design stages: reaching one of these is not a signal going missing.
TERMINAL = (RESOLVED, EXITED, EXECUTION_REJECTED, NOT_FILLED)

# --- statuses ---------------------------------------------------------------
OK = "OK"
REFUSED = "REFUSED"
MISSED = "MISSED"

# --- why a signal stopped where it stopped (Part 26) ------------------------
NOT_PUBLISHED = "NOT_PUBLISHED"
PLAN_INVALID = "PLAN_INVALID"
PLAN_STALE = "PLAN_STALE"
DUPLICATE_EPISODE = "DUPLICATE_EPISODE"
UI_STATE_REPLACED = "UI_STATE_REPLACED"
EPISODE_SUPERSEDED = "EPISODE_SUPERSEDED"
STALE_FEED = "STALE_FEED"
RISK_BLOCK = "RISK_BLOCK"
CAPITAL_BLOCK = "CAPITAL_BLOCK"
COOLDOWN = "COOLDOWN"
ALREADY_TRADED = "ALREADY_TRADED"
EXECUTION_BLOCK = "EXECUTION_BLOCK"
BROKER_REJECTION = "BROKER_REJECTION"
UNKNOWN = "UNKNOWN"

MISS_REASONS = (
    NOT_PUBLISHED, PLAN_INVALID, PLAN_STALE, DUPLICATE_EPISODE,
    UI_STATE_REPLACED, EPISODE_SUPERSEDED, STALE_FEED, RISK_BLOCK,
    CAPITAL_BLOCK, COOLDOWN, ALREADY_TRADED, EXECUTION_BLOCK,
    BROKER_REJECTION, UNKNOWN,
)

# --- vehicles ---------------------------------------------------------------
CE = "CE"
PE = "PE"
FUTURES = "FUTURES"

# --- markets ----------------------------------------------------------------
OPTIONS = "OPTIONS"
MARKET_FUTURES = "FUTURES"

# Why a journal signal legitimately has no dashboard record (Part 28).
EXEMPT_REASONS = ("HISTORICAL", "RESEARCH_ONLY", "SUPERSEDED",
                  "NOT_DASHBOARD_ELIGIBLE")

_IST = timezone(timedelta(hours=5, minutes=30))
_LOCK = threading.Lock()

# How many signals are kept in memory for the reconciliation API. Older ones are
# read back from the ledger file rather than held forever.
_MAX_TRACKED = 4000

# global_signal_id -> {"stages": {...}, "meta": {...}} in insertion order, so the
# oldest is the one evicted.
_tracked: OrderedDict[str, dict] = OrderedDict()
# episode_id -> global_signal_id of the first signal that opened the episode
_episodes: dict[str, str] = {}
# episode_id -> how many raw events collapsed into it
_episode_events: dict[str, int] = {}


def ledger_path() -> str:
    return os.path.join(settings.data_dir, LEDGER_LOG)


def _ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d %H:%M:%S")


def session_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d")


def _append(rec: dict) -> None:
    try:
        if not settings.signal_lifecycle_log:
            return
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(ledger_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        return


def episode_id(instrument: str, call_key: str, now: float) -> str:
    """The de-duplicated call this signal belongs to.

    Derived from the instrument, the IST session and a caller-supplied key that
    describes the *call* (action, leg, gate) rather than the tick, so repeats of
    the same call land on the same episode and can be counted as repeats instead
    of being mistaken for missing signals.
    """
    return f"{instrument}-{session_of(now)}-{call_key}"


def new_signal_id(instrument: str, vehicle: str, now: float) -> str:
    """A fresh id for one signal, unique down to the millisecond plus a suffix.

    Two calls inside the same millisecond would otherwise share an id and a
    report could not tell them apart.
    """
    return f"{instrument}-{vehicle}-{int(now * 1000)}-{uuid.uuid4().hex[:6]}"


def record(
    *,
    global_signal_id: str,
    episode: str,
    stage: str,
    status: str = OK,
    instrument: str = "",
    vehicle: str = "",
    market: str = OPTIONS,
    source: str = "",
    reason: str | None = None,
    value: float | str | None = None,
    threshold: float | str | None = None,
    detail: dict | None = None,
    now: float | None = None,
) -> str:
    """Stamp one lifecycle transition and return its ``event_id``.

    Best-effort by contract: any failure is swallowed, because this is
    measurement and a tick must not be able to fail inside it.
    """
    ts = time.time() if now is None else now
    event_id = uuid.uuid4().hex[:12]
    rec = {
        "event_id": event_id,
        "global_signal_id": global_signal_id,
        "episode_id": episode,
        "ts": int(ts),
        "ts_precise": ts,
        "time_ist": _ist(ts),
        "session": session_of(ts),
        "stage": stage,
        "status": status,
        "instrument": instrument,
        "vehicle": vehicle,
        "market": market,
        "source": source,
        "reason": reason,
        "value": value,
        "threshold": threshold,
    }
    if detail:
        rec["detail"] = detail
    try:
        with _LOCK:
            entry = _tracked.get(global_signal_id)
            if entry is None:
                entry = {
                    "global_signal_id": global_signal_id,
                    "episode_id": episode,
                    "instrument": instrument,
                    "vehicle": vehicle,
                    "market": market,
                    "first_ts": ts,
                    "stages": {},
                    "order": [],
                }
                _tracked[global_signal_id] = entry
                _episodes.setdefault(episode, global_signal_id)
                while len(_tracked) > _MAX_TRACKED:
                    _tracked.popitem(last=False)
            _episode_events[episode] = _episode_events.get(episode, 0) + 1
            entry["last_ts"] = ts
            entry["last_stage"] = stage
            entry["last_status"] = status
            entry["stages"][stage] = {
                "ts": ts, "status": status, "reason": reason,
                "value": value, "threshold": threshold, "source": source,
            }
            entry["order"].append(stage)
    except Exception:
        pass
    _append(rec)
    return event_id


def missed(
    *,
    global_signal_id: str,
    episode: str,
    stage: str,
    reason: str,
    instrument: str = "",
    vehicle: str = "",
    market: str = OPTIONS,
    source: str = "",
    value: float | str | None = None,
    threshold: float | str | None = None,
    detail: dict | None = None,
    now: float | None = None,
) -> str:
    """A signal stopped before it reached the user or the book, and why.

    ``stage`` is the stage it failed to reach — MISSED_STAGE — and ``reason`` is
    MISSED_REASON. An unrecognised reason is recorded as given *and* flagged, so
    a new failure mode shows up as itself rather than being silently bucketed
    into UNKNOWN.
    """
    detail = dict(detail or {})
    if reason not in MISS_REASONS:
        detail["unclassified_reason"] = True
    return record(
        global_signal_id=global_signal_id, episode=episode, stage=stage,
        status=MISSED, instrument=instrument, vehicle=vehicle, market=market,
        source=source, reason=reason, value=value, threshold=threshold,
        detail=detail, now=now,
    )


def episode_repeat_count(episode: str) -> int:
    """How many raw events have been recorded against this episode."""
    with _LOCK:
        return _episode_events.get(episode, 0)


def is_new_episode(episode: str, global_signal_id: str) -> bool:
    """True when ``global_signal_id`` is the signal that opened ``episode``."""
    with _LOCK:
        return _episodes.get(episode) == global_signal_id


def tracked(global_signal_id: str) -> dict | None:
    with _LOCK:
        entry = _tracked.get(global_signal_id)
        return json.loads(json.dumps(entry, default=str)) if entry else None


def snapshot(session: str | None = None) -> list[dict]:
    """Every signal held in memory, oldest first, optionally one session only."""
    with _LOCK:
        rows = [json.loads(json.dumps(e, default=str)) for e in _tracked.values()]
    if session is None:
        return rows
    return [r for r in rows if session_of(r["first_ts"]) == session]


# One parse of the ledger per version of the file, per (session, limit) view.
# The whole ledger is scanned several times to build one dashboard, and it only
# grows, so the scan is kept against the file's size and modification time: an
# event written by the live tick changes both and the next scan re-reads. A read
# error is never cached, so a partial read can never be served as the session.
_scan_cache: dict[tuple[str, str | None, int | None],
                  tuple[int, int, list[dict], dict]] = {}


def scan_ledger(session: str | None = None,
                limit: int | None = None) -> tuple[list[dict], dict]:
    """The ledger plus an explicit account of what was and was not read.

    Phase 12 §2. A reader that silently returns a tail produces a report that
    looks complete and is not: the 25 Aug session wrote 24,301 events and the
    daily reconciliation read the last 20,000 of them, dropping the whole market
    open without saying so. So the row count is no longer the only thing a caller
    gets — ``coverage`` states how many rows matched the session, how many were
    processed, and how many were left out, and ``limit=None`` (the default) means
    the complete session.
    """
    path = ledger_path()
    try:
        st = os.stat(path)
        stamp = (st.st_size, st.st_mtime_ns)
    except OSError:
        stamp = (0, 0)
    key = (path, session, limit)
    hit = _scan_cache.get(key)
    if hit is not None and (hit[0], hit[1]) == stamp:
        return hit[2], dict(hit[3])
    coverage = {
        "file_lines": 0,
        "records_unparsable": 0,
        "records_seen": 0,
        "records_processed": 0,
        "records_omitted": 0,
        "limit": limit,
        "truncated": False,
        "read_error": None,
    }
    if not os.path.exists(path):
        return [], coverage
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                coverage["file_lines"] += 1
                try:
                    rec = json.loads(line)
                except Exception:
                    coverage["records_unparsable"] += 1
                    continue
                if session and rec.get("session") != session:
                    continue
                coverage["records_seen"] += 1
                out.append(rec)
    except OSError as exc:
        # Report the truncation rather than pretending the short read is the day.
        coverage["read_error"] = type(exc).__name__
    if limit is not None and len(out) > limit:
        coverage["records_omitted"] = len(out) - limit
        coverage["truncated"] = True
        out = out[-limit:]
    coverage["records_processed"] = len(out)
    if coverage["read_error"] is None:
        _scan_cache[key] = (stamp[0], stamp[1], out, dict(coverage))
    return out, coverage


def read_ledger(limit: int | None = None,
                session: str | None = None) -> list[dict]:
    """The ledger, complete by default. ``limit`` keeps only the newest rows."""
    return scan_ledger(session=session, limit=limit)[0]


def reset_for_tests() -> None:
    """Clear process state. Used by the smoke; never called by the app."""
    with _LOCK:
        _tracked.clear()
        _episodes.clear()
        _episode_events.clear()
        _scan_cache.clear()
