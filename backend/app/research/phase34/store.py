"""Phase 34 store — option contract registry and 1-minute premium bars.

Two tables, one job each. ``contracts`` is a registry of every option contract the
scrip master has ever shown us, so a token stays resolvable after the contract
expires and disappears from the master. ``option_bars`` holds the premium candles,
keyed by (token, ts) so a re-run overlapping an already-downloaded window is a
no-op rather than a duplicate.

Research-only: this store is never read by the live engine and holds no order.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import time

from app.research.phase24 import data as p24data
from app.research.phase34 import DB_NAME

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS contracts (
        token TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        root TEXT NOT NULL,
        exchange TEXT NOT NULL,
        option_type TEXT NOT NULL,
        strike REAL NOT NULL,
        expiry TEXT NOT NULL,
        lot_size INTEGER,
        first_seen_date TEXT NOT NULL,
        last_seen_date TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS option_bars (
        token TEXT NOT NULL,
        ts INTEGER NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL,
        PRIMARY KEY (token, ts)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS harvest_log (
        token TEXT NOT NULL,
        from_date TEXT NOT NULL,
        to_date TEXT NOT NULL,
        bars INTEGER NOT NULL,
        fetched_ts INTEGER NOT NULL,
        PRIMARY KEY (token, from_date, to_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_bars_ts ON option_bars (ts)",
    "CREATE INDEX IF NOT EXISTS ix_contracts_root ON contracts (root, expiry)",
)


def db_path() -> str:
    return p24data._resolve(os.path.join("data", DB_NAME))


def connect() -> sqlite3.Connection:
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=30.0)
    con.execute("PRAGMA journal_mode=WAL")
    for stmt in SCHEMA:
        con.execute(stmt)
    con.commit()
    return con


def record_contracts(con: sqlite3.Connection, rows: list[dict], seen: dt.date) -> int:
    """Register contracts seen in today's scrip master.

    ``first_seen_date`` is kept from the earliest sighting and ``last_seen_date``
    advanced, so the registry doubles as evidence of when a contract was listed.
    """
    day = seen.isoformat()
    n = 0
    for r in rows:
        con.execute(
            "INSERT INTO contracts (token, symbol, root, exchange, option_type, "
            "strike, expiry, lot_size, first_seen_date, last_seen_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(token) DO UPDATE SET last_seen_date = excluded.last_seen_date",
            (
                str(r["token"]),
                str(r["symbol"]),
                str(r["root"]),
                str(r["exchange"]),
                str(r["option_type"]),
                float(r["strike"]),
                str(r["expiry"]),
                int(r["lot_size"] or 0),
                day,
                day,
            ),
        )
        n += 1
    con.commit()
    return n


def save_bars(con: sqlite3.Connection, token: str, rows: list[list]) -> int:
    """Insert candles for one token. Existing (token, ts) rows are left alone."""
    payload = []
    for row in rows:
        try:
            ts = int(dt.datetime.fromisoformat(row[0]).timestamp())
            payload.append(
                (token, ts, float(row[1]), float(row[2]), float(row[3]),
                 float(row[4]), float(row[5] or 0.0))
            )
        except (TypeError, ValueError, IndexError):
            continue
    before = con.total_changes
    con.executemany(
        "INSERT OR IGNORE INTO option_bars (token, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        payload,
    )
    con.commit()
    # Rows actually inserted, not rows offered: a re-harvested window must add 0 to
    # the harvest log rather than appear to have collected the same bars twice.
    return int(con.total_changes - before)


def log_window(con: sqlite3.Connection, token: str, start: dt.date, end: dt.date,
               bars: int) -> None:
    con.execute(
        "INSERT OR REPLACE INTO harvest_log (token, from_date, to_date, bars, fetched_ts) "
        "VALUES (?, ?, ?, ?, ?)",
        (token, start.isoformat(), end.isoformat(), int(bars), int(time.time())),
    )
    con.commit()


def window_done(con: sqlite3.Connection, token: str, start: dt.date, end: dt.date) -> bool:
    """True when this exact window was already fetched.

    Only an exact match counts. A window that returned zero bars is still logged,
    so a contract that had not started trading is not re-requested on every run.
    """
    row = con.execute(
        "SELECT 1 FROM harvest_log WHERE token = ? AND from_date = ? AND to_date = ?",
        (token, start.isoformat(), end.isoformat()),
    ).fetchone()
    return row is not None


def contract_rows(con: sqlite3.Connection, roots: tuple[str, ...]) -> list[dict]:
    marks = ",".join("?" for _ in roots)
    rows = con.execute(
        "SELECT token, symbol, root, option_type, strike, expiry, first_seen_date "
        f"FROM contracts WHERE root IN ({marks}) ORDER BY expiry, strike",
        tuple(r.upper() for r in roots),
    ).fetchall()
    return [
        {"token": r[0], "symbol": r[1], "root": r[2], "option_type": r[3],
         "strike": float(r[4]), "expiry": r[5], "first_seen_date": r[6]}
        for r in rows
    ]


def coverage(con: sqlite3.Connection) -> dict:
    contracts = con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    bars = con.execute("SELECT COUNT(*) FROM option_bars").fetchone()[0]
    tokens = con.execute("SELECT COUNT(DISTINCT token) FROM option_bars").fetchone()[0]
    span = con.execute("SELECT MIN(ts), MAX(ts) FROM option_bars").fetchone()
    sessions = con.execute(
        "SELECT COUNT(DISTINCT ts / 86400) FROM option_bars"
    ).fetchone()[0]
    per_root = con.execute(
        "SELECT c.root, COUNT(DISTINCT b.token), COUNT(b.ts) "
        "FROM option_bars b JOIN contracts c ON c.token = b.token GROUP BY c.root"
    ).fetchall()
    return {
        "contracts_registered": int(contracts),
        "tokens_with_bars": int(tokens),
        "bars": int(bars),
        "sessions": int(sessions),
        "first_bar_ts": int(span[0]) if span and span[0] else None,
        "last_bar_ts": int(span[1]) if span and span[1] else None,
        "per_root": [
            {"root": r[0], "tokens": int(r[1]), "bars": int(r[2])} for r in per_root
        ],
    }
