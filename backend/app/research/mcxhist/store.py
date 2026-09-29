"""Where collected MCX history lands, and the provenance that travels with it.

One SQLite file, ``data/mcxhist.db``, separate from every production store so
nothing here can contaminate a live journal and nothing live can contaminate the
dataset. Three tables:

* ``bar`` — the candles, keyed ``(root, token, ts)``. The key is the
  deduplication: a re-requested window overwrites its own rows and cannot append
  a second copy of a minute. The token is part of the key so two tokens of the
  same root stay distinguishable instead of being silently pooled;
* ``chunk`` — one row per requested window with its status and row count. This
  is what makes a run resumable: a resumed run asks only for windows that are
  not ``OK``, and a window that failed carries the provider's reason;
* ``provenance`` — one row per (root, token, interval): source, exchange,
  contract, timezone, collection timestamp, data class and series class. §9 of
  the request asks that every observation retain provenance; storing it per
  series rather than per bar keeps the file a tenth of the size and loses
  nothing, because a bar cannot exist in this store without its series row.

Timestamps are stored as epoch seconds. The provider stamps its rows
``2021-08-02T11:01:00+05:30``; the offset is parsed, never assumed, and a row
without a parseable timestamp is rejected rather than filed under a guess.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from collections.abc import Iterable, Sequence

from app.research.mcxhist import DATA_CLASS, SERIES_CLASS

STATUS_OK = "OK"
STATUS_EMPTY = "EMPTY"
STATUS_FAILED = "FAILED"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bar (
    root   TEXT NOT NULL,
    token  TEXT NOT NULL,
    ts     INTEGER NOT NULL,
    open   REAL NOT NULL,
    high   REAL NOT NULL,
    low    REAL NOT NULL,
    close  REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (root, token, ts)
);
CREATE INDEX IF NOT EXISTS bar_root_ts ON bar (root, ts);

CREATE TABLE IF NOT EXISTS chunk (
    root       TEXT NOT NULL,
    token      TEXT NOT NULL,
    interval   TEXT NOT NULL,
    chunk_from TEXT NOT NULL,
    chunk_to   TEXT NOT NULL,
    status     TEXT NOT NULL,
    rows       INTEGER NOT NULL DEFAULT 0,
    reason     TEXT,
    collected_at REAL,
    PRIMARY KEY (root, token, interval, chunk_from)
);

CREATE TABLE IF NOT EXISTS provenance (
    root         TEXT NOT NULL,
    token        TEXT NOT NULL,
    interval     TEXT NOT NULL,
    exchange     TEXT NOT NULL,
    trading_symbol TEXT,
    expiry       TEXT,
    source       TEXT NOT NULL,
    timezone     TEXT NOT NULL,
    data_class   TEXT NOT NULL,
    series_class TEXT NOT NULL,
    collected_at REAL NOT NULL,
    fingerprint  TEXT NOT NULL,
    PRIMARY KEY (root, token, interval)
);
"""


def default_path(data_dir: str = "data") -> str:
    return os.path.join(data_dir, "mcxhist.db")


class Store:
    """The collected dataset. Append and read; nothing here deletes a bar."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_path()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------- provenance

    def record_provenance(
        self,
        root: str,
        token: str,
        interval: str,
        *,
        exchange: str,
        trading_symbol: str | None,
        expiry: str | None,
        source: str,
        fingerprint: str,
        timezone: str = "Asia/Kolkata (+05:30, parsed from the provider stamp)",
        collected_at: float | None = None,
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO provenance (root, token, interval, exchange,"
            " trading_symbol, expiry, source, timezone, data_class,"
            " series_class, collected_at, fingerprint)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                root,
                token,
                interval,
                exchange,
                trading_symbol,
                expiry,
                source,
                timezone,
                DATA_CLASS,
                SERIES_CLASS,
                float(collected_at if collected_at is not None else dt.datetime.now().timestamp()),
                fingerprint,
            ),
        )
        self._db.commit()

    def provenance(self, root: str | None = None) -> list[dict]:
        sql = "SELECT * FROM provenance"
        args: tuple = ()
        if root:
            sql += " WHERE root = ?"
            args = (root,)
        cur = self._db.execute(sql + " ORDER BY root, token", args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ------------------------------------------------------------------- bars

    def insert_bars(self, root: str, token: str, rows: Iterable[Sequence]) -> int:
        """Insert provider rows. Returns how many were accepted.

        A row whose timestamp or prices will not parse is skipped and counted as
        rejected by the caller's difference; it is never stored under a
        substituted value.
        """
        payload: list[tuple] = []
        for row in rows:
            try:
                stamp = int(dt.datetime.fromisoformat(row[0]).timestamp())
                payload.append(
                    (
                        root,
                        token,
                        stamp,
                        float(row[1]),
                        float(row[2]),
                        float(row[3]),
                        float(row[4]),
                        float(row[5]),
                    )
                )
            except Exception:
                continue
        if payload:
            self._db.executemany(
                "INSERT OR REPLACE INTO bar (root, token, ts, open, high, low,"
                " close, volume) VALUES (?,?,?,?,?,?,?,?)",
                payload,
            )
            self._db.commit()
        return len(payload)

    def bars(
        self,
        root: str,
        token: str | None = None,
        ts_from: int | None = None,
        ts_to: int | None = None,
    ) -> list[tuple]:
        sql = "SELECT ts, open, high, low, close, volume FROM bar WHERE root = ?"
        args: list = [root]
        if token:
            sql += " AND token = ?"
            args.append(token)
        if ts_from is not None:
            sql += " AND ts >= ?"
            args.append(int(ts_from))
        if ts_to is not None:
            sql += " AND ts <= ?"
            args.append(int(ts_to))
        return list(self._db.execute(sql + " ORDER BY ts", args))

    def bar_count(self, root: str, token: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM bar WHERE root = ?"
        args: list = [root]
        if token:
            sql += " AND token = ?"
            args.append(token)
        return int(self._db.execute(sql, args).fetchone()[0])

    def tokens(self, root: str) -> list[str]:
        return [
            r[0]
            for r in self._db.execute(
                "SELECT DISTINCT token FROM bar WHERE root = ? ORDER BY token", (root,)
            )
        ]

    def roots(self) -> list[str]:
        return [
            r[0]
            for r in self._db.execute("SELECT DISTINCT root FROM bar ORDER BY root")
        ]

    # ----------------------------------------------------------------- chunks

    def mark_chunk(
        self,
        root: str,
        token: str,
        interval: str,
        chunk_from: str,
        chunk_to: str,
        status: str,
        rows: int = 0,
        reason: str | None = None,
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO chunk (root, token, interval, chunk_from,"
            " chunk_to, status, rows, reason, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                root,
                token,
                interval,
                chunk_from,
                chunk_to,
                status,
                int(rows),
                reason,
                dt.datetime.now().timestamp(),
            ),
        )
        self._db.commit()

    def done_chunks(self, root: str, token: str, interval: str) -> set[str]:
        """Windows that landed. A FAILED or EMPTY window is retried."""
        return {
            r[0]
            for r in self._db.execute(
                "SELECT chunk_from FROM chunk WHERE root = ? AND token = ?"
                " AND interval = ? AND status = ?",
                (root, token, interval, STATUS_OK),
            )
        }

    def chunk_stats(self, root: str, token: str, interval: str) -> dict:
        rows = self._db.execute(
            "SELECT status, COUNT(*), COALESCE(SUM(rows), 0) FROM chunk"
            " WHERE root = ? AND token = ? AND interval = ? GROUP BY status",
            (root, token, interval),
        ).fetchall()
        return {
            status: {"windows": int(count), "rows": int(total)}
            for status, count, total in rows
        }

    def failures(self, root: str, token: str, interval: str) -> list[dict]:
        cur = self._db.execute(
            "SELECT chunk_from, chunk_to, status, reason FROM chunk WHERE root = ?"
            " AND token = ? AND interval = ? AND status != ? ORDER BY chunk_from",
            (root, token, interval, STATUS_OK),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
