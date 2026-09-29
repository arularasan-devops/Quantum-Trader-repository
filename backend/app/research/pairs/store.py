"""SQLite store for the frozen pair capture. Its own file, its own tables.

Deliberately separate from the production research store: this package must be
able to write every tick without any chance of touching a table the trading path
reads. All SQL is parameterized; the connection is created under ``data_dir``.

Five tables:

``leg_quotes``      one instrument's futures book at one bar timestamp;
``pair_obs``        the two legs paired on an EXACT shared timestamp, with z;
``pair_trades``     paper trades of the frozen rule, with measured fills;
``contract_quotes`` near/next futures contracts quoted at one instant (Test B);
``leg_diag``        per-leg, per-session sampling counters (why coverage is low).

Nothing here is ever rewritten except a trade's closing columns, so a crash can
lose the last row but cannot corrupt an earlier one.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

from app.config import settings

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CONN_PATH: str | None = None

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS leg_quotes (
        instrument   TEXT NOT NULL,
        bar_ts       INTEGER NOT NULL,
        captured_ts  REAL NOT NULL,
        session      TEXT NOT NULL,
        price        REAL NOT NULL,
        fut_symbol   TEXT,
        fut_expiry   TEXT,
        fut_dte      INTEGER,
        lot_size     INTEGER,
        ltp          REAL,
        bid          REAL,
        ask          REAL,
        oi           INTEGER,
        volume       INTEGER,
        feed_age_sec REAL,
        source       TEXT,
        PRIMARY KEY (instrument, bar_ts)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pair_obs (
        bar_ts       INTEGER PRIMARY KEY,
        session      TEXT NOT NULL,
        fingerprint  TEXT NOT NULL,
        a_price      REAL NOT NULL,
        b_price      REAL NOT NULL,
        ratio        REAL NOT NULL,
        z            REAL,
        window_n     INTEGER NOT NULL,
        a_bid        REAL, a_ask REAL,
        b_bid        REAL, b_ask REAL,
        spread_status TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pair_trades (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint   TEXT NOT NULL,
        session       TEXT NOT NULL,
        direction     TEXT NOT NULL,
        entry_ts      INTEGER NOT NULL,
        exit_ts       INTEGER,
        lots_a        INTEGER NOT NULL,
        lots_b        INTEGER NOT NULL,
        qty_a         INTEGER NOT NULL,
        qty_b         INTEGER NOT NULL,
        entry_a       REAL NOT NULL,
        entry_b       REAL NOT NULL,
        exit_a        REAL,
        exit_b        REAL,
        z_entry       REAL NOT NULL,
        z_exit        REAL,
        held_obs      INTEGER,
        reason        TEXT,
        gross         REAL,
        cost          REAL,
        net           REAL,
        notional_a    REAL,
        notional_b    REAL,
        hedged_pnl    REAL,
        residual_pnl  REAL,
        cost_status   TEXT NOT NULL,
        cost_detail   TEXT,
        status        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contract_quotes (
        instrument   TEXT NOT NULL,
        captured_ts  INTEGER NOT NULL,
        seq          INTEGER NOT NULL,
        symbol       TEXT NOT NULL,
        expiry       TEXT,
        dte          INTEGER,
        lot_size     INTEGER,
        ltp          REAL,
        bid          REAL,
        ask          REAL,
        oi           INTEGER,
        volume       INTEGER,
        feed_age_sec REAL,
        source       TEXT,
        PRIMARY KEY (instrument, captured_ts, symbol)
    )
    """,
    # Sampling diagnostics. Measured coverage depends on how often a leg is
    # sampled WHILE ITS BAR IS CURRENT, and none of that is recoverable from the
    # stored rows afterwards: a sample that produced no new bar leaves no trace.
    # Persisted rather than kept in memory so the CLI, which runs in a separate
    # process from the capturing server, can report it.
    """
    CREATE TABLE IF NOT EXISTS leg_diag (
        instrument             TEXT NOT NULL,
        session                TEXT NOT NULL,
        samples                INTEGER NOT NULL DEFAULT 0,
        no_price               INTEGER NOT NULL DEFAULT 0,
        bar_unchanged          INTEGER NOT NULL DEFAULT 0,
        new_bars               INTEGER NOT NULL DEFAULT 0,
        book_two_sided         INTEGER NOT NULL DEFAULT 0,
        book_missing           INTEGER NOT NULL DEFAULT 0,
        book_dropped_stale_bar INTEGER NOT NULL DEFAULT 0,
        out_of_session         INTEGER NOT NULL DEFAULT 0,
        no_bar                 INTEGER NOT NULL DEFAULT 0,
        last_bar_ts            INTEGER,
        last_bar_age_sec       REAL,
        last_source            TEXT,
        updated_ts             REAL,
        PRIMARY KEY (instrument, session)
    )
    """,
)

_DIAG_COUNTERS = (
    "samples", "no_price", "bar_unchanged", "new_bars",
    "book_two_sided", "book_missing", "book_dropped_stale_bar",
    "out_of_session", "no_bar",
)


def path() -> str:
    return os.path.join(settings.data_dir, "pair_capture.db")


def connect() -> sqlite3.Connection:
    """One shared connection, created on first use and reused thereafter."""
    global _CONN, _CONN_PATH
    want = path()
    with _LOCK:
        if _CONN is not None and _CONN_PATH == want:
            return _CONN
        if _CONN is not None:
            _CONN.close()
        os.makedirs(os.path.dirname(want) or ".", exist_ok=True)
        conn = sqlite3.connect(want, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        for stmt in _DDL:
            conn.execute(stmt)
        _add_missing_diag_columns(conn)
        conn.commit()
        _CONN, _CONN_PATH = conn, want
        return conn


def _add_missing_diag_columns(conn: sqlite3.Connection) -> None:
    """Give an already-created ``leg_diag`` any counter added since.

    A capture database is accumulating evidence across sessions, so it must be
    extended in place; recreating it would discard the days already recorded.
    """
    have = {str(r["name"]) for r in conn.execute("PRAGMA table_info(leg_diag)")}
    for name in _DIAG_COUNTERS:
        if name not in have:
            conn.execute(
                f"ALTER TABLE leg_diag ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")


def reset_for_tests() -> None:
    """Drop the cached connection so a test can point at another data_dir."""
    global _CONN, _CONN_PATH
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN, _CONN_PATH = None, None


def write_leg_quote(row: dict) -> None:
    conn = connect()
    with _LOCK:
        conn.execute(
            """INSERT OR IGNORE INTO leg_quotes
               (instrument, bar_ts, captured_ts, session, price, fut_symbol,
                fut_expiry, fut_dte, lot_size, ltp, bid, ask, oi, volume,
                feed_age_sec, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["instrument"], int(row["bar_ts"]), float(row["captured_ts"]),
             row["session"], float(row["price"]), row.get("fut_symbol"),
             row.get("fut_expiry"), row.get("fut_dte"), row.get("lot_size"),
             row.get("ltp"), row.get("bid"), row.get("ask"), row.get("oi"),
             row.get("volume"), row.get("feed_age_sec"), row.get("source")),
        )
        conn.commit()


def leg_quote(instrument: str, bar_ts: int) -> dict | None:
    conn = connect()
    cur = conn.execute(
        "SELECT * FROM leg_quotes WHERE instrument = ? AND bar_ts = ?",
        (instrument, int(bar_ts)),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def max_pair_ts() -> int:
    """Newest paired bar already recorded, or 0."""
    conn = connect()
    return int(conn.execute("SELECT MAX(bar_ts) FROM pair_obs").fetchone()[0] or 0)


def write_pair_obs(row: dict) -> bool:
    """Write one paired observation. Returns True only if it is NEW.

    The caller advances the frozen rule on the return value, so a bar that is
    seen twice (both legs tick again inside the same minute) cannot be traded
    twice.
    """
    conn = connect()
    with _LOCK:
        cur = conn.execute(
            """INSERT OR IGNORE INTO pair_obs
               (bar_ts, session, fingerprint, a_price, b_price, ratio, z,
                window_n, a_bid, a_ask, b_bid, b_ask, spread_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (int(row["bar_ts"]), row["session"], row["fingerprint"],
             float(row["a_price"]), float(row["b_price"]), float(row["ratio"]),
             row.get("z"), int(row["window_n"]), row.get("a_bid"),
             row.get("a_ask"), row.get("b_bid"), row.get("b_ask"),
             row["spread_status"]),
        )
        conn.commit()
        return bool(cur.rowcount)


def recent_ratios(limit: int, before_ts: int) -> list[float]:
    """The last ``limit`` ratios STRICTLY BEFORE ``before_ts``, oldest first.

    Strictly before, because the z-score that decides an entry may not be
    computed from the bar it is deciding on.
    """
    conn = connect()
    cur = conn.execute(
        "SELECT ratio FROM pair_obs WHERE bar_ts < ? ORDER BY bar_ts DESC LIMIT ?",
        (int(before_ts), int(limit)),
    )
    return [float(r["ratio"]) for r in cur.fetchall()][::-1]


def open_trade(row: dict) -> int:
    conn = connect()
    with _LOCK:
        cur = conn.execute(
            """INSERT INTO pair_trades
               (fingerprint, session, direction, entry_ts, lots_a, lots_b,
                qty_a, qty_b, entry_a, entry_b, z_entry, notional_a, notional_b,
                cost_status, cost_detail, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')""",
            (row["fingerprint"], row["session"], row["direction"],
             int(row["entry_ts"]), int(row["lots_a"]), int(row["lots_b"]),
             int(row["qty_a"]), int(row["qty_b"]), float(row["entry_a"]),
             float(row["entry_b"]), float(row["z_entry"]),
             row.get("notional_a"), row.get("notional_b"),
             row["cost_status"], json.dumps(row.get("cost_detail") or {})),
        )
        conn.commit()
        return int(cur.lastrowid)


def close_trade(trade_id: int, row: dict) -> None:
    conn = connect()
    with _LOCK:
        conn.execute(
            """UPDATE pair_trades
                  SET exit_ts = ?, exit_a = ?, exit_b = ?, z_exit = ?,
                      held_obs = ?, reason = ?, gross = ?, cost = ?, net = ?,
                      hedged_pnl = ?, residual_pnl = ?, cost_status = ?,
                      cost_detail = ?, status = 'CLOSED'
                WHERE id = ?""",
            (int(row["exit_ts"]), float(row["exit_a"]), float(row["exit_b"]),
             row.get("z_exit"), int(row["held_obs"]), row["reason"],
             float(row["gross"]), float(row["cost"]), float(row["net"]),
             row.get("hedged_pnl"), row.get("residual_pnl"),
             row["cost_status"], json.dumps(row.get("cost_detail") or {}),
             int(trade_id)),
        )
        conn.commit()


def open_trades() -> list[dict]:
    conn = connect()
    cur = conn.execute("SELECT * FROM pair_trades WHERE status = 'OPEN' ORDER BY id")
    return [dict(r) for r in cur.fetchall()]


def closed_trades(fingerprint: str | None = None) -> list[dict]:
    conn = connect()
    if fingerprint:
        cur = conn.execute(
            "SELECT * FROM pair_trades WHERE status = 'CLOSED' AND fingerprint = ?"
            " ORDER BY entry_ts", (fingerprint,))
    else:
        cur = conn.execute(
            "SELECT * FROM pair_trades WHERE status = 'CLOSED' ORDER BY entry_ts")
    return [dict(r) for r in cur.fetchall()]


def write_contract_quotes(instrument: str, captured_ts: int,
                          books: list[dict]) -> int:
    """Store the near/next futures books quoted at ONE instant.

    ``seq`` is 0 for the nearest contract. Rows with no expiry are still stored:
    a contract the feed cannot date is evidence about the feed.
    """
    conn = connect()
    written = 0
    with _LOCK:
        for seq, book in enumerate(books):
            symbol = book.get("symbol")
            if not symbol:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO contract_quotes
                   (instrument, captured_ts, seq, symbol, expiry, dte, lot_size,
                    ltp, bid, ask, oi, volume, feed_age_sec, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (instrument, int(captured_ts), seq, str(symbol),
                 book.get("expiry"), book.get("days_to_expiry"),
                 book.get("lot_size"), book.get("ltp"), book.get("bid"),
                 book.get("ask"), book.get("oi"), book.get("volume"),
                 book.get("feed_age_sec"), book.get("source")),
            )
            written += 1
        conn.commit()
    return written


def contract_snapshots() -> dict[tuple[str, int], list[dict]]:
    """Every stored instant, grouped by (instrument, captured_ts)."""
    conn = connect()
    cur = conn.execute(
        "SELECT * FROM contract_quotes ORDER BY instrument, captured_ts, seq")
    out: dict[tuple[str, int], list[dict]] = {}
    for r in cur.fetchall():
        out.setdefault((r["instrument"], int(r["captured_ts"])), []).append(dict(r))
    return out


def write_leg_diag(instrument: str, session: str, counters: dict,
                   last: dict) -> None:
    """Add this sample's counters to the leg's row for ``session``.

    Counters accumulate in SQL rather than being overwritten from memory, so a
    restart mid-session adds to the day's totals instead of resetting them.
    """
    conn = connect()
    adds = [int(counters.get(name) or 0) for name in _DIAG_COUNTERS]
    with _LOCK:
        conn.execute(
            "INSERT OR IGNORE INTO leg_diag (instrument, session) VALUES (?,?)",
            (instrument, session))
        sets = ", ".join(f"{name} = {name} + ?" for name in _DIAG_COUNTERS)
        conn.execute(
            f"""UPDATE leg_diag
                  SET {sets},
                      last_bar_ts = COALESCE(?, last_bar_ts),
                      last_bar_age_sec = COALESCE(?, last_bar_age_sec),
                      last_source = COALESCE(?, last_source),
                      updated_ts = ?
                WHERE instrument = ? AND session = ?""",
            (*adds, last.get("last_bar_ts"), last.get("last_bar_age_sec"),
             last.get("last_source"), last.get("updated_ts"),
             instrument, session),
        )
        conn.commit()


def leg_diag() -> dict[str, dict]:
    """Latest session's sampling counters per leg, newest session first."""
    conn = connect()
    cur = conn.execute(
        "SELECT * FROM leg_diag ORDER BY instrument, session DESC")
    out: dict[str, dict] = {}
    for r in cur.fetchall():
        out.setdefault(str(r["instrument"]), dict(r))
    return out


def leg_coverage() -> dict[str, dict]:
    """Per-leg rows, sessions, last bar and measured-book share.

    Capture only happens on instruments the session is actually running, so a leg
    that is absent from the universe would otherwise look exactly like a quiet
    market. This makes the difference visible.
    """
    conn = connect()
    cur = conn.execute(
        """SELECT instrument,
                  COUNT(*) AS rows_n,
                  COUNT(DISTINCT session) AS sessions,
                  MAX(bar_ts) AS last_bar_ts,
                  SUM(CASE WHEN bid IS NOT NULL AND ask IS NOT NULL
                           THEN 1 ELSE 0 END) AS two_sided
           FROM leg_quotes GROUP BY instrument""")
    out: dict[str, dict] = {}
    for r in cur.fetchall():
        rows_n = int(r["rows_n"] or 0)
        two = int(r["two_sided"] or 0)
        out[str(r["instrument"])] = {
            "rows": rows_n,
            "sessions": int(r["sessions"] or 0),
            "last_bar_ts": int(r["last_bar_ts"] or 0),
            "two_sided_rows": two,
            "two_sided_pct": round(100.0 * two / rows_n, 2) if rows_n else 0.0,
        }
    return out


def counts() -> dict:
    conn = connect()

    def one(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0] or 0)

    obs = one("SELECT COUNT(*) FROM pair_obs")
    obs_measured = one(
        "SELECT COUNT(*) FROM pair_obs WHERE spread_status = 'MEASURED'")
    return {
        "leg_quotes": one("SELECT COUNT(*) FROM leg_quotes"),
        "leg_quotes_backfilled": one(
            "SELECT COUNT(*) FROM leg_quotes WHERE source = 'CANDLE_BACKFILL'"),
        "pair_obs": obs,
        # Share of observations where BOTH legs had a two-sided book at that bar.
        # Backfilled bars keep the series dense but can never be measured, so
        # this is the number that says how executable the evidence really is.
        "pair_obs_measured": obs_measured,
        "pair_obs_measured_pct": round(100.0 * obs_measured / obs, 2) if obs else 0.0,
        "pair_sessions": one("SELECT COUNT(DISTINCT session) FROM pair_obs"),
        "trades_open": one("SELECT COUNT(*) FROM pair_trades WHERE status='OPEN'"),
        "trades_closed": one("SELECT COUNT(*) FROM pair_trades WHERE status='CLOSED'"),
        "contract_quotes": one("SELECT COUNT(*) FROM contract_quotes"),
        "contract_instants": one(
            "SELECT COUNT(*) FROM (SELECT 1 FROM contract_quotes"
            " GROUP BY instrument, captured_ts)"),
    }
