"""Storage for collected underlying history. RESEARCH ONLY.

Two tables, kept apart from the existing research store's contract tables so
nothing that already reads ``mcx_futures`` can accidentally be handed cash-index
bars:

* ``spot_candles`` — one OHLCV row per instrument per minute, primary-keyed on
  ``(instrument, ts)`` so a re-fetched chunk overwrites rather than duplicates;
* ``spot_chunks`` — one row per fetched date window, which is what makes a
  multi-day collection resumable: a run that dies at instrument 9 of 20 resumes
  at the chunk it died on instead of re-downloading everything behind it.

A chunk that failed is recorded with its reason and *not* marked done, so the
next run retries exactly the windows that are missing.
"""
from __future__ import annotations

from typing import Any

from app.research.db import Database

STATUS_OK = "OK"
STATUS_EMPTY = "EMPTY"
STATUS_FAILED = "FAILED"

_DDL = [
    """CREATE TABLE IF NOT EXISTS spot_candles (
        instrument TEXT NOT NULL,
        ts         BIGINT NOT NULL,
        open       DOUBLE PRECISION,
        high       DOUBLE PRECISION,
        low        DOUBLE PRECISION,
        close      DOUBLE PRECISION,
        volume     DOUBLE PRECISION,
        PRIMARY KEY (instrument, ts)
    )""",
    """CREATE TABLE IF NOT EXISTS spot_chunks (
        instrument  TEXT NOT NULL,
        chunk_from  TEXT NOT NULL,
        chunk_to    TEXT NOT NULL,
        interval    TEXT NOT NULL,
        rows        BIGINT,
        status      TEXT NOT NULL,
        detail      TEXT,
        fetched_at  BIGINT,
        PRIMARY KEY (instrument, chunk_from, interval)
    )""",
]


class SpotStore:
    """Typed access to the collected underlying history."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()
        for stmt in _DDL:
            self.db.execute(stmt)

    @property
    def backend(self) -> str:
        return "postgresql" if self.db.is_postgres else "sqlite"

    # ---- candles ----
    def insert_candles(self, instrument: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        payload = [
            (instrument, int(r["ts"]), r.get("open"), r.get("high"),
             r.get("low"), r.get("close"), r.get("volume"))
            for r in rows
        ]
        verb = "INSERT OR REPLACE INTO" if not self.db.is_postgres else "INSERT INTO"
        conflict = "" if not self.db.is_postgres else (
            " ON CONFLICT (instrument, ts) DO NOTHING"
        )
        self.db.executemany(
            f"{verb} spot_candles (instrument, ts, open, high, low, close, volume) "
            f"VALUES (?,?,?,?,?,?,?)" + conflict,
            payload,
        )
        return len(payload)

    def candles(self, instrument: str, ts_from: int | None = None,
                ts_to: int | None = None) -> list[dict]:
        sql = ("SELECT ts, open, high, low, close, volume FROM spot_candles "
               "WHERE instrument=?")
        params: list[Any] = [instrument]
        if ts_from is not None:
            sql += " AND ts >= ?"
            params.append(int(ts_from))
        if ts_to is not None:
            sql += " AND ts <= ?"
            params.append(int(ts_to))
        return self.db.query(sql + " ORDER BY ts ASC", tuple(params))

    def coverage(self, instrument: str) -> dict:
        row = self.db.query_one(
            "SELECT COUNT(*) AS rows, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
            "FROM spot_candles WHERE instrument=?", (instrument,))
        return {
            "instrument": instrument,
            "rows": int((row or {}).get("rows") or 0),
            "first_ts": (row or {}).get("first_ts"),
            "last_ts": (row or {}).get("last_ts"),
        }

    def instruments(self) -> list[str]:
        return [str(r["instrument"]) for r in self.db.query(
            "SELECT DISTINCT instrument FROM spot_candles ORDER BY instrument ASC")]

    # ---- chunk bookkeeping ----
    def mark_chunk(self, instrument: str, chunk_from: str, chunk_to: str,
                   interval: str, rows: int, status: str,
                   detail: str | None, fetched_at: int) -> None:
        verb = "INSERT OR REPLACE INTO" if not self.db.is_postgres else "INSERT INTO"
        conflict = "" if not self.db.is_postgres else (
            " ON CONFLICT (instrument, chunk_from, interval) DO UPDATE SET "
            "chunk_to=EXCLUDED.chunk_to, rows=EXCLUDED.rows, status=EXCLUDED.status, "
            "detail=EXCLUDED.detail, fetched_at=EXCLUDED.fetched_at"
        )
        self.db.execute(
            f"{verb} spot_chunks (instrument, chunk_from, chunk_to, interval, rows, "
            f"status, detail, fetched_at) VALUES (?,?,?,?,?,?,?,?)" + conflict,
            (instrument, chunk_from, chunk_to, interval, int(rows), status,
             detail, int(fetched_at)),
        )

    def done_chunks(self, instrument: str, interval: str) -> set[str]:
        """The chunk start dates that need no re-fetch.

        A window that came back empty counts as done: outside market hours and
        over exchange holidays an empty answer is the correct answer, and retrying
        it every run would spend the rate limit on nothing. A failed window is
        deliberately absent so the next run picks it up again.
        """
        rows = self.db.query(
            "SELECT chunk_from FROM spot_chunks WHERE instrument=? AND interval=? "
            "AND status IN (?, ?)",
            (instrument, interval, STATUS_OK, STATUS_EMPTY))
        return {str(r["chunk_from"]) for r in rows}

    def chunk_stats(self, instrument: str, interval: str) -> dict[str, int]:
        rows = self.db.query(
            "SELECT status, COUNT(*) AS n FROM spot_chunks "
            "WHERE instrument=? AND interval=? GROUP BY status",
            (instrument, interval))
        return {str(r["status"]): int(r["n"]) for r in rows}

    def failures(self, instrument: str, interval: str) -> list[dict]:
        return self.db.query(
            "SELECT chunk_from, chunk_to, detail FROM spot_chunks WHERE instrument=? "
            "AND interval=? AND status=? ORDER BY chunk_from ASC",
            (instrument, interval, STATUS_FAILED))
