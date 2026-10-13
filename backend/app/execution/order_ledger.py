"""Crash-safe live-order ledger and append-only order audit log.

Two files under ``settings.data_dir`` (the ``backend-data`` volume in Compose):

``live_orders.json``
    Current live order state per instrument: outstanding entry/exit orders
    (broker order id, requested lots, lots already confirmed filled, broker
    average price) and the position the app believes it holds. Rewritten whole
    per update: temp file + fsync + ``os.replace`` + directory fsync, under a
    process lock and an inter-process ``flock``. An unreadable ledger raises
    ``LedgerError`` so callers fail closed. This file is the durable recovery
    source; after a restart it is only a *claim* to be reconciled against the
    broker, never assumed to be the broker's position.

``order_events.jsonl``
    Append-only audit log (fsync per event). Every event carries a unique
    ``event_id``. A torn last line from a crash is isolated by a newline before
    the next append and is skipped and counted when read, never guessed at.

PostgreSQL mirror: events are copied to the research store's ``order_events``
table from a byte cursor (``order_events.pg_cursor``). Inserts are idempotent
on ``event_id`` (duplicates are ignored), and the cursor only advances after a
successful insert, so a failed or partial mirror retries from the same offset
and never loses an event. The JSONL file stays the source of truth.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import uuid

from app.config import settings

try:  # POSIX (Docker/WSL). Without it only the in-process lock applies.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

LEDGER_FILE = "live_orders.json"
EVENTS_FILE = "order_events.jsonl"
CURSOR_FILE = "order_events.pg_cursor"
_LOCK_FILE = "live_orders.lock"
_MIRROR_BATCH = 500

_lock = threading.RLock()
_mirror_lock = threading.Lock()
_stats: dict = {
    "ledger_write_errors": 0,
    "event_write_errors": 0,
    "last_error": None,
    "mirror_errors": 0,
    "mirror_last_error": None,
    "mirror_last_ok_ts": None,
    "corrupt_event_lines": 0,
}


class LedgerError(RuntimeError):
    """The durable order ledger/audit log could not be read or written."""


def _path(name: str) -> str:
    return os.path.join(settings.data_dir, name)


@contextlib.contextmanager
def _locked():
    with _lock:
        os.makedirs(settings.data_dir, exist_ok=True)
        if fcntl is None:
            yield
            return
        fd = os.open(_path(_LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _fsync_dir() -> None:
    with contextlib.suppress(OSError, AttributeError):
        fd = os.open(settings.data_dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _atomic_write(name: str, text: str) -> None:
    target = _path(name)
    tmp = f"{target}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        _fsync_dir()
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)


def _read_all_unlocked() -> dict:
    try:
        with open(_path(LEDGER_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise LedgerError(f"{LEDGER_FILE} unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise LedgerError(f"{LEDGER_FILE} has unexpected structure")
    return data


def load(instrument: str) -> dict:
    """Saved state for one instrument ({} when none). Raises ``LedgerError``."""
    with _locked():
        return dict(_read_all_unlocked().get(instrument.upper()) or {})


def load_all() -> dict:
    with _locked():
        return _read_all_unlocked()


def save(instrument: str, record: dict | None) -> None:
    """Replace one instrument's record; an empty record removes it."""
    key = instrument.upper()
    clean = {k: v for k, v in (record or {}).items() if v is not None}
    try:
        with _locked():
            data = _read_all_unlocked()
            if clean:
                clean["updated_at"] = time.time()
                data[key] = clean
            else:
                data.pop(key, None)
            _atomic_write(LEDGER_FILE, json.dumps(data, default=str, sort_keys=True))
    except Exception as exc:
        _stats["ledger_write_errors"] += 1
        _stats["last_error"] = f"ledger write: {exc}"[:300]
        log.error("order ledger write failed: %s", exc)
        raise LedgerError(str(exc)) from exc


def record_event(instrument: str, kind: str, **fields) -> dict:
    """Durably append one audit event, then try to mirror. Raises ``LedgerError``
    when the durable append fails (the caller must surface it)."""
    event = {"event_id": uuid.uuid4().hex, "ts": time.time(),
             "instrument": instrument.upper(), "kind": kind}
    event.update({k: v for k, v in fields.items() if v is not None})
    line = json.dumps(event, default=str, sort_keys=True)
    try:
        with _locked():
            path = _path(EVENTS_FILE)
            with open(path, "a+b") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell() > 0:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        fh.write(b"\n")  # isolate a torn line left by a crash
                fh.write(line.encode("utf-8") + b"\n")
                fh.flush()
                os.fsync(fh.fileno())
    except Exception as exc:
        _stats["event_write_errors"] += 1
        _stats["last_error"] = f"event append: {exc}"[:300]
        log.error("order event append failed (%s): %s", kind, exc)
        raise LedgerError(str(exc)) from exc
    mirror_pending()
    return event


def _cursor() -> int:
    try:
        with open(_path(CURSOR_FILE), encoding="utf-8") as fh:
            return max(0, int(fh.read().strip() or 0))
    except (FileNotFoundError, ValueError):
        return 0


def mirror_backlog_bytes() -> int:
    try:
        size = os.path.getsize(_path(EVENTS_FILE))
    except OSError:
        return 0
    return max(0, size - _cursor())


def mirror_pending() -> dict:
    """Copy unmirrored events to PostgreSQL. Never raises; failures are counted,
    logged and retried on the next call from the same cursor."""
    if not _mirror_lock.acquire(blocking=False):
        return {"mirrored": 0, "busy": True}
    try:
        from app.research.store import store as research_store

        if mirror_backlog_bytes() <= 0:
            return {"mirrored": 0}
        rs = research_store()
        if rs.backend != "postgresql":
            return {"mirrored": 0, "disabled": "research store is not PostgreSQL"}
        start = _cursor()
        with open(_path(EVENTS_FILE), "rb") as fh:
            fh.seek(start)
            chunk = fh.read()
        pos = 0
        events: list[dict] = []
        while len(events) < _MIRROR_BATCH:
            nl = chunk.find(b"\n", pos)
            if nl < 0:
                break  # incomplete trailing line: wait for it, do not advance
            raw = chunk[pos:nl].strip()
            pos = nl + 1
            if not raw:
                continue
            try:
                events.append(json.loads(raw))
            except ValueError:
                _stats["corrupt_event_lines"] += 1
                log.error("skipping unparsable order event line at byte %d", start + pos)
        if events:
            rs.insert_order_events(events)
        if pos:
            with _locked():
                _atomic_write(CURSOR_FILE, str(start + pos))
        _stats["mirror_last_ok_ts"] = time.time()
        _stats["mirror_last_error"] = None
        return {"mirrored": len(events)}
    except Exception as exc:
        _stats["mirror_errors"] += 1
        _stats["mirror_last_error"] = str(exc)[:300]
        log.warning("order event PostgreSQL mirror failed (will retry): %s", exc)
        return {"mirrored": 0, "error": str(exc)[:300]}
    finally:
        _mirror_lock.release()


def status() -> dict:
    """Read-only persistence health for the operator API."""
    try:
        instruments = sorted(load_all().keys())
        ledger_ok, ledger_error = True, None
    except LedgerError as exc:
        instruments, ledger_ok, ledger_error = [], False, str(exc)
    return {
        "ledger_file": LEDGER_FILE,
        "events_file": EVENTS_FILE,
        "ledger_readable": ledger_ok,
        "ledger_error": ledger_error,
        "instruments_with_live_state": instruments,
        "mirror_backlog_bytes": mirror_backlog_bytes(),
        **_stats,
    }
