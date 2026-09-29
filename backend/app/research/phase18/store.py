"""Append-only JSONL for the CAS evidence, plus the one file that is rewritten.

Everything measured is appended and never edited, so a row cannot be improved
after the fact. The single exception is the open-position file: an overnight leg
(§16) has to survive the app being restarted between 15:30 and the next morning,
and an append-only log of state transitions would have to be replayed to answer
"what is open right now" on every read. That file is small, rewritten atomically,
and carries no measurement — the measurements are still appended.

Write failures are counted and surfaced by ``health()`` rather than raised: a
research recorder that can take down a live tick is worse than a research
recorder with a gap in it, and a gap nobody can see is worse than both.
"""
from __future__ import annotations

import json
import os
import threading
import time

from app.config import settings

OBSERVATIONS = "phase18_cas_observations.jsonl"
PAPER = "phase18_cas_paper.jsonl"
COVERAGE = "phase18_cas_coverage.jsonl"
MARKS = "phase18_cas_marks.jsonl"
OPEN = "phase18_cas_open.json"

_LOCK = threading.Lock()
_STATE: dict[str, object] = {
    "writes": 0, "failures": 0, "last_error": None, "last_error_ts": None,
    "last_write_ts": None,
}


def path(name: str) -> str:
    return os.path.join(settings.data_dir, name)


def _note_failure(exc: OSError) -> None:
    with _LOCK:
        _STATE["failures"] = int(_STATE["failures"]) + 1  # type: ignore[arg-type]
        _STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        _STATE["last_error_ts"] = time.time()


def _append(name: str, rec: dict) -> bool:
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(path(name), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except OSError as exc:
        _note_failure(exc)
        return False
    with _LOCK:
        _STATE["writes"] = int(_STATE["writes"]) + 1  # type: ignore[arg-type]
        _STATE["last_write_ts"] = time.time()
    return True


def _read(name: str, limit: int | None = None) -> list[dict]:
    p = path(name)
    if not os.path.exists(p):
        return []
    rows: list[dict] = []
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn final line from a kill during a write
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError as exc:
        _note_failure(exc)
        return []
    return rows[-limit:] if limit else rows


# ----------------------------------------------------------------- writes
def write_observation(rec: dict) -> bool:
    return _append(OBSERVATIONS, rec)


def write_paper(rec: dict) -> bool:
    return _append(PAPER, rec)


def write_mark(rec: dict) -> bool:
    """A §6 underlying mark (15:10/15/20/25/30) or a next-morning open mark."""
    return _append(MARKS, rec)


def note_coverage(session: str, instrument: str, cas_state: str) -> bool:
    """A heartbeat, so a report can say which minutes of which sessions exist.

    Capture only happens while the app is running. Without this the dataset is
    silently biased towards the days someone remembered to start it.
    """
    return _append(COVERAGE, {
        "ts": time.time(),
        "session": session,
        "instrument": instrument,
        "cas_state": cas_state,
        "provider": settings.data_provider,
    })


# ------------------------------------------------------------------ reads
def observations(limit: int | None = None) -> list[dict]:
    return _read(OBSERVATIONS, limit)


def paper(limit: int | None = None) -> list[dict]:
    return _read(PAPER, limit)


def marks(limit: int | None = None) -> list[dict]:
    return _read(MARKS, limit)


def coverage(limit: int | None = None) -> list[dict]:
    return _read(COVERAGE, limit)


# ------------------------------------------------- open positions (§16/§21)
def save_open(rows: list[dict]) -> bool:
    """Rewrite the open-position file atomically."""
    tmp = path(OPEN) + ".tmp"
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"saved_ts": time.time(), "rows": rows}, fh, default=str)
        os.replace(tmp, path(OPEN))
    except OSError as exc:
        _note_failure(exc)
        return False
    return True


def load_open() -> list[dict]:
    p = path(OPEN)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        if isinstance(exc, OSError):
            _note_failure(exc)
        return []
    rows = obj.get("rows") if isinstance(obj, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def health() -> dict:
    with _LOCK:
        snap = dict(_STATE)
    snap["files"] = {
        name: os.path.exists(path(name))
        for name in (OBSERVATIONS, PAPER, COVERAGE, MARKS, OPEN)
    }
    return snap
