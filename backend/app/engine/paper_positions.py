"""Open PAPER option positions, saved so a backend restart resumes them.

One row per instrument in the research database (PostgreSQL in Docker). The
state module writes the row while a paper position is open and deletes it on
the full exit; on start-up a fresh row is restored and its exits keep running.
Live positions never use this: they go through the order ledger and broker
reconciliation.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from app.research.db import Database

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_db: Database | None = None

_DDL = """CREATE TABLE IF NOT EXISTS jr_open_positions (
    instrument TEXT PRIMARY KEY, saved_ts DOUBLE PRECISION NOT NULL,
    payload TEXT NOT NULL)"""


def _store() -> Database:
    global _db
    with _lock:
        if _db is None:
            db = Database()
            db.execute(_DDL)
            _db = db
        return _db


def save(instrument: str, payload: dict) -> None:
    _store().execute(
        "INSERT INTO jr_open_positions (instrument, saved_ts, payload) VALUES (?,?,?) "
        "ON CONFLICT (instrument) DO UPDATE SET saved_ts = excluded.saved_ts, "
        "payload = excluded.payload",
        (instrument, time.time(), json.dumps(payload, default=str)))


def clear(instrument: str) -> None:
    _store().execute("DELETE FROM jr_open_positions WHERE instrument = ?", (instrument,))


def load(instrument: str) -> tuple[float, dict] | None:
    row = _store().query_one(
        "SELECT saved_ts, payload FROM jr_open_positions WHERE instrument = ?", (instrument,))
    if not row:
        return None
    try:
        return float(row["saved_ts"]), json.loads(row["payload"])
    except Exception:
        _log.warning("unreadable saved paper position for %s", instrument, exc_info=True)
        return None
