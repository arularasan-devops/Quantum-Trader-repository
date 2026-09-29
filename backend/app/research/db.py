"""Database abstraction for the research store.

Supports PostgreSQL (the intended production store) and falls back to a local
SQLite file so the platform runs with zero external services. Both backends use
the same DDL and a single ``?``-style parameter convention that is translated to
the driver's paramstyle. No ORM — plain SQL keeps the dependency surface small
and the schema explicit.

Security: the connection URL (which may contain a password) is read only from
configuration/env (`QT_DB_URL`), never hard-coded, and is never logged.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any

from app.config import settings

_PG_SCHEMES = ("postgres://", "postgresql://")


def _is_postgres(url: str) -> bool:
    return url.lower().startswith(_PG_SCHEMES)


class Database:
    """Thin, thread-safe wrapper over a SQLite or PostgreSQL connection."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url if url is not None else settings.db_url
        self.is_postgres = _is_postgres(self.url or "")
        self._lock = threading.Lock()
        if self.is_postgres:
            self._conn = self._connect_postgres(self.url)
            self._placeholder = "%s"
        else:
            path = self._sqlite_path(self.url)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._placeholder = "?"

    # ---- connection helpers ----
    @staticmethod
    def _sqlite_path(url: str) -> str:
        if url and url.startswith("sqlite:///"):
            return url[len("sqlite:///"):]
        return os.path.join(settings.data_dir, "research.db")

    @staticmethod
    def _connect_postgres(url: str):  # pragma: no cover - needs a live DB
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "QT_DB_URL is a PostgreSQL URL but the 'psycopg' package is not "
                "installed. Run `pip install psycopg[binary]` or set QT_DB_URL "
                "to empty / a sqlite:/// path to use the SQLite fallback."
            ) from exc
        conn = psycopg.connect(url, autocommit=True)
        return conn

    # ---- query helpers ----
    def _translate(self, sql: str) -> str:
        # Author queries with '?'; convert to the driver's placeholder.
        if self._placeholder == "?":
            return sql
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: tuple | list = ()) -> None:
        stmt = self._translate(sql)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(stmt, tuple(params))
            if not self.is_postgres:
                self._conn.commit()
            cur.close()

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        stmt = self._translate(sql)
        with self._lock:
            cur = self._conn.cursor()
            cur.executemany(stmt, rows)
            if not self.is_postgres:
                self._conn.commit()
            cur.close()

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        stmt = self._translate(sql)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(stmt, tuple(params))
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            cur.close()
        return [dict(zip(cols, row)) for row in rows]

    def query_one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None
