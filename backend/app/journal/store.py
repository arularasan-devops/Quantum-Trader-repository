"""Journal v2 storage: sessions, signals, trade lifecycle events, trades and
sizing decisions.

* Additive tables with a ``jr_`` prefix in the research database; no existing
  table is altered. Versioned in ``jr_schema_migrations``.
* Idempotent: every row has a deterministic id derived from its natural key,
  and inserts use ``ON CONFLICT DO NOTHING`` (PostgreSQL and SQLite >= 3.24),
  so a retried write never duplicates a session, signal, event or trade.
* Rollback: the tables are independent of every other table, so rolling back
  is ``rollback("DROP_JOURNAL_V2")`` (drops only ``jr_*``). Take a
  ``pg_dump -t 'jr_*'`` first if the rows matter.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.research.db import Database

SCHEMA_VERSION = 1
IST = timezone(timedelta(hours=5, minutes=30))
MAX_PAGE_SIZE = 500

_DDL = [
    """CREATE TABLE IF NOT EXISTS jr_schema_migrations (
        version    INTEGER PRIMARY KEY,
        applied_at BIGINT NOT NULL,
        note       TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS jr_sessions (
        session_id         TEXT PRIMARY KEY,
        instrument         TEXT NOT NULL,
        session_date       TEXT NOT NULL,
        mode               TEXT NOT NULL,
        started_at         DOUBLE PRECISION NOT NULL,
        last_seen_at       DOUBLE PRECISION NOT NULL,
        strategy_version   TEXT,
        config_fingerprint TEXT,
        data_source        TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS jr_signals (
        event_id           TEXT PRIMARY KEY,
        session_id         TEXT,
        trade_id           TEXT,
        instrument         TEXT NOT NULL,
        ts                 DOUBLE PRECISION NOT NULL,
        ts_iso             TEXT NOT NULL,
        signal             TEXT NOT NULL,
        outcome            TEXT NOT NULL,
        reason             TEXT,
        strategy           TEXT,
        setup              TEXT,
        regime             TEXT,
        htf_trend          TEXT,
        confidence         DOUBLE PRECISION,
        option_symbol      TEXT,
        expiry             TEXT,
        quote_ltp          DOUBLE PRECISION,
        bid                DOUBLE PRECISION,
        ask                DOUBLE PRECISION,
        quote_ts           DOUBLE PRECISION,
        expected_entry     DOUBLE PRECISION,
        stop               DOUBLE PRECISION,
        target1            DOUBLE PRECISION,
        target2            DOUBLE PRECISION,
        target3            DOUBLE PRECISION,
        mode               TEXT,
        strategy_version   TEXT,
        config_fingerprint TEXT,
        data_quality       TEXT,
        explanation        TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS jr_trades (
        trade_id           TEXT PRIMARY KEY,
        session_id         TEXT,
        instrument         TEXT NOT NULL,
        mode               TEXT NOT NULL,
        strategy           TEXT,
        setup              TEXT,
        regime             TEXT,
        option_symbol      TEXT,
        lot_size           INTEGER,
        entry_ts           DOUBLE PRECISION NOT NULL,
        entry_ts_iso       TEXT,
        entry_price        DOUBLE PRECISION,
        lots               INTEGER,
        capital_used       DOUBLE PRECISION,
        initial_stop       DOUBLE PRECISION,
        target1            DOUBLE PRECISION,
        target2            DOUBLE PRECISION,
        target3            DOUBLE PRECISION,
        entry_explanation  TEXT,
        status             TEXT NOT NULL,
        exit_ts            DOUBLE PRECISION,
        exit_ts_iso        TEXT,
        exit_price         DOUBLE PRECISION,
        exit_reason        TEXT,
        lots_closed        INTEGER,
        gross_pnl          DOUBLE PRECISION,
        costs              DOUBLE PRECISION,
        net_pnl            DOUBLE PRECISION,
        holding_minutes    INTEGER,
        mfe_mae_status     TEXT,
        strategy_version   TEXT,
        config_fingerprint TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS jr_trade_events (
        event_id      TEXT PRIMARY KEY,
        trade_id      TEXT NOT NULL,
        session_id    TEXT,
        instrument    TEXT NOT NULL,
        ts            DOUBLE PRECISION NOT NULL,
        ts_iso        TEXT NOT NULL,
        kind          TEXT NOT NULL,
        option_symbol TEXT,
        lots          INTEGER,
        price         DOUBLE PRECISION,
        stop          DOUBLE PRECISION,
        target        DOUBLE PRECISION,
        reason        TEXT,
        gross_pnl     DOUBLE PRECISION,
        costs         DOUBLE PRECISION,
        net_pnl       DOUBLE PRECISION,
        mode          TEXT,
        detail        TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS jr_sizing_decisions (
        decision_id       TEXT PRIMARY KEY,
        trade_id          TEXT,
        session_id        TEXT,
        instrument        TEXT NOT NULL,
        ts                DOUBLE PRECISION NOT NULL,
        method            TEXT NOT NULL,
        applied           BOOLEAN NOT NULL,
        contract          TEXT,
        vehicle           TEXT,
        starting_capital  DOUBLE PRECISION,
        equity            DOUBLE PRECISION,
        available_funds   DOUBLE PRECISION,
        entry_price       DOUBLE PRECISION,
        stop_price        DOUBLE PRECISION,
        lot_size          INTEGER,
        lot_size_source   TEXT,
        lot_size_ts       DOUBLE PRECISION,
        margin_per_lot    DOUBLE PRECISION,
        margin_status     TEXT,
        total_margin      DOUBLE PRECISION,
        cost_per_lot      DOUBLE PRECISION,
        proposed_lots     INTEGER,
        actual_lots       INTEGER,
        stop_exposure     DOUBLE PRECISION,
        est_costs         DOUBLE PRECISION,
        cost_status       TEXT,
        constraint_name   TEXT,
        reason            TEXT,
        mode              TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS jr_trades_entry_idx ON jr_trades (entry_ts)",
    "CREATE INDEX IF NOT EXISTS jr_trades_inst_idx ON jr_trades (instrument, entry_ts)",
    "CREATE INDEX IF NOT EXISTS jr_events_trade_idx ON jr_trade_events (trade_id, ts)",
    "CREATE INDEX IF NOT EXISTS jr_signals_ts_idx ON jr_signals (instrument, ts)",
    "CREATE INDEX IF NOT EXISTS jr_sizing_trade_idx ON jr_sizing_decisions (trade_id)",
]

ROLLBACK_SQL = [
    "DROP TABLE IF EXISTS jr_sizing_decisions",
    "DROP TABLE IF EXISTS jr_trade_events",
    "DROP TABLE IF EXISTS jr_trades",
    "DROP TABLE IF EXISTS jr_signals",
    "DROP TABLE IF EXISTS jr_sessions",
    "DROP TABLE IF EXISTS jr_schema_migrations",
]

TRADE_CSV_COLUMNS = [
    "trade_id", "session_id", "instrument", "mode", "strategy", "setup", "regime",
    "option_symbol", "lot_size", "entry_ts_iso", "entry_price", "lots", "capital_used",
    "initial_stop", "target1", "target2", "target3", "status", "exit_ts_iso",
    "exit_price", "exit_reason", "lots_closed", "gross_pnl", "costs", "net_pnl",
    "holding_minutes", "mfe_mae_status", "entry_explanation", "strategy_version",
    "config_fingerprint",
]

SIGNAL_OUTCOMES = ("EXECUTED", "SKIPPED", "REJECTED", "NOT_EXECUTABLE", "FAILED_CONDITIONS")
EVENT_KINDS = ("ENTRY", "EXIT", "PARTIAL_EXIT", "STOP_CHANGE", "TARGET_LEVEL",
               "EXECUTION_FAILURE")


def stable_id(*parts: Any) -> str:
    """Deterministic id from a natural key, so a retried write is a no-op."""
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), IST).isoformat(timespec="seconds")


def session_date(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), IST).date().isoformat()


def _require(row: dict, *keys: str) -> None:
    missing = [k for k in keys if row.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")


class JournalStore:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()
        for ddl in _DDL:
            self.db.execute(ddl)
        self.db.execute(
            "INSERT INTO jr_schema_migrations (version, applied_at, note) VALUES (?,?,?) "
            "ON CONFLICT (version) DO NOTHING",
            (1, int(time.time()), "jr_sessions/signals/trades/trade_events/sizing_decisions"),
        )

    @property
    def backend(self) -> str:
        return "postgresql" if self.db.is_postgres else "sqlite"

    def schema_version(self) -> int:
        row = self.db.query_one("SELECT MAX(version) AS v FROM jr_schema_migrations")
        return int(row["v"] or 0) if row else 0

    # ------------------------------------------------------------ writes
    def _insert(self, table: str, key: str, row: dict) -> None:
        cols = list(row.keys())
        self.db.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT ({key}) DO NOTHING",
            tuple(row[c] for c in cols),
        )

    def upsert_session(self, row: dict) -> str:
        _require(row, "instrument", "mode", "ts")
        ts = float(row["ts"])
        day = session_date(ts)
        sid = row.get("session_id") or f"{row['instrument']}:{day}:{row['mode']}"
        self.db.execute(
            "INSERT INTO jr_sessions (session_id, instrument, session_date, mode, started_at, "
            "last_seen_at, strategy_version, config_fingerprint, data_source) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (session_id) DO UPDATE SET "
            "last_seen_at = CASE WHEN excluded.last_seen_at > jr_sessions.last_seen_at "
            "THEN excluded.last_seen_at ELSE jr_sessions.last_seen_at END",
            (sid, row["instrument"], day, row["mode"], ts, ts, row.get("strategy_version"),
             row.get("config_fingerprint"), row.get("data_source")),
        )
        return sid

    def record_signal(self, row: dict) -> str:
        _require(row, "instrument", "ts", "signal", "outcome")
        if row["outcome"] not in SIGNAL_OUTCOMES:
            raise ValueError(f"unknown outcome {row['outcome']}")
        eid = row.get("event_id") or stable_id(
            "signal", row["instrument"], row["signal"], row.get("option_symbol"),
            int(float(row["ts"]) // 60), row["outcome"])
        cols = ["session_id", "trade_id", "instrument", "signal", "outcome", "reason",
                "strategy", "setup", "regime", "htf_trend", "confidence", "option_symbol",
                "expiry", "quote_ltp", "bid", "ask", "quote_ts", "expected_entry", "stop",
                "target1", "target2", "target3", "mode", "strategy_version",
                "config_fingerprint", "data_quality", "explanation"]
        data = {"event_id": eid, "ts": float(row["ts"]), "ts_iso": iso(row["ts"])}
        data.update({c: row.get(c) for c in cols})
        self._insert("jr_signals", "event_id", data)
        return eid

    def open_trade(self, row: dict) -> str:
        _require(row, "trade_id", "instrument", "mode", "entry_ts")
        cols = ["trade_id", "session_id", "instrument", "mode", "strategy", "setup", "regime",
                "option_symbol", "lot_size", "entry_ts", "entry_price", "lots", "capital_used",
                "initial_stop", "target1", "target2", "target3", "entry_explanation",
                "mfe_mae_status", "strategy_version", "config_fingerprint"]
        data = {c: row.get(c) for c in cols}
        data["entry_ts_iso"] = iso(row["entry_ts"])
        data["status"] = "OPEN"
        self._insert("jr_trades", "trade_id", data)
        return row["trade_id"]

    def record_trade_event(self, row: dict) -> str:
        _require(row, "trade_id", "instrument", "ts", "kind")
        if row["kind"] not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {row['kind']}")
        eid = row.get("event_id") or stable_id(
            "event", row["trade_id"], row["kind"], round(float(row["ts"]), 3),
            row.get("lots"), row.get("price"), row.get("stop"), row.get("target"))
        cols = ["trade_id", "session_id", "instrument", "kind", "option_symbol", "lots",
                "price", "stop", "target", "reason", "gross_pnl", "costs", "net_pnl", "mode"]
        data = {"event_id": eid, "ts": float(row["ts"]), "ts_iso": iso(row["ts"])}
        data.update({c: row.get(c) for c in cols})
        detail = row.get("detail")
        data["detail"] = json.dumps(detail, default=str) if detail is not None else None
        self._insert("jr_trade_events", "event_id", data)
        if row["kind"] in ("EXIT", "PARTIAL_EXIT"):
            self.refresh_trade(row["trade_id"])
        return eid

    def refresh_trade(self, trade_id: str) -> None:
        """Recompute a trade's exit totals from its events (idempotent)."""
        agg = self.db.query_one(
            "SELECT COALESCE(SUM(lots),0) AS lots_closed, SUM(gross_pnl) AS gross, "
            "SUM(costs) AS costs, SUM(net_pnl) AS net, MAX(ts) AS last_ts "
            "FROM jr_trade_events WHERE trade_id = ? AND kind IN ('EXIT','PARTIAL_EXIT')",
            (trade_id,))
        if not agg or agg["last_ts"] is None:
            return
        last = self.db.query_one(
            "SELECT price, reason, kind FROM jr_trade_events WHERE trade_id = ? AND "
            "kind IN ('EXIT','PARTIAL_EXIT') ORDER BY ts DESC, event_id DESC LIMIT 1",
            (trade_id,))
        trade = self.db.query_one("SELECT entry_ts FROM jr_trades WHERE trade_id = ?",
                                  (trade_id,))
        closed = last is not None and last["kind"] == "EXIT"
        held = None
        if trade and trade["entry_ts"] is not None:
            held = int((float(agg["last_ts"]) - float(trade["entry_ts"])) // 60)
        self.db.execute(
            "UPDATE jr_trades SET status = ?, exit_ts = ?, exit_ts_iso = ?, exit_price = ?, "
            "exit_reason = ?, lots_closed = ?, gross_pnl = ?, costs = ?, net_pnl = ?, "
            "holding_minutes = ? WHERE trade_id = ?",
            ("CLOSED" if closed else "OPEN", float(agg["last_ts"]), iso(agg["last_ts"]),
             last["price"] if last else None, last["reason"] if last else None,
             int(agg["lots_closed"] or 0), agg["gross"], agg["costs"], agg["net"], held,
             trade_id),
        )

    def record_sizing(self, row: dict) -> str:
        _require(row, "instrument", "ts", "method")
        did = row.get("decision_id") or stable_id(
            "sizing", row.get("trade_id"), row["instrument"], round(float(row["ts"]), 3),
            row["method"])
        cols = ["trade_id", "session_id", "instrument", "method", "contract", "vehicle",
                "starting_capital", "equity", "available_funds", "entry_price", "stop_price",
                "lot_size", "lot_size_source", "lot_size_ts", "margin_per_lot",
                "margin_status", "total_margin", "cost_per_lot", "proposed_lots",
                "actual_lots", "stop_exposure", "est_costs", "cost_status",
                "constraint_name", "reason", "mode"]
        data = {"decision_id": did, "ts": float(row["ts"]), "applied": bool(row.get("applied"))}
        data.update({c: row.get(c) for c in cols})
        self._insert("jr_sizing_decisions", "decision_id", data)
        return did

    def realised_net(self, mode: str) -> float:
        row = self.db.query_one(
            "SELECT COALESCE(SUM(net_pnl), 0) AS n FROM jr_trade_events "
            "WHERE mode = ? AND kind IN ('EXIT','PARTIAL_EXIT')", (mode,))
        return float(row["n"] or 0.0) if row else 0.0

    # ------------------------------------------------------------ reads
    @staticmethod
    def _page(page: int, page_size: int) -> tuple[int, int]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        return page, page_size

    def _where(self, filters: dict, ts_col: str, allowed: tuple[str, ...]) -> tuple[str, list]:
        clauses, params = [], []
        for key in allowed:
            val = filters.get(key)
            if val not in (None, ""):
                clauses.append(f"{key} = ?")
                params.append(val)
        if filters.get("since") is not None:
            clauses.append(f"{ts_col} >= ?")
            params.append(float(filters["since"]))
        if filters.get("until") is not None:
            clauses.append(f"{ts_col} < ?")
            params.append(float(filters["until"]))
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def list_trades(self, filters: dict | None = None, page: int = 1,
                    page_size: int = 50) -> dict:
        filters = dict(filters or {})
        where, params = self._where(filters, "entry_ts", (
            "instrument", "mode", "strategy", "setup", "regime", "status"))
        result = (filters.get("result") or "").upper()
        if result in ("WIN", "LOSS"):
            where += (" AND " if where else " WHERE ") + "status = 'CLOSED' AND net_pnl " + (
                ">= 0" if result == "WIN" else "< 0")
        page, page_size = self._page(page, page_size)
        total = self.db.query_one(f"SELECT COUNT(*) AS n FROM jr_trades{where}", params)["n"]
        rows = self.db.query(
            f"SELECT * FROM jr_trades{where} ORDER BY entry_ts DESC, trade_id ASC "
            "LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size])
        return {"items": rows, "total": int(total), "page": page, "page_size": page_size}

    def trades_csv(self, filters: dict | None = None) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(TRADE_CSV_COLUMNS)
        page = 1
        while True:
            chunk = self.list_trades(filters, page=page, page_size=MAX_PAGE_SIZE)
            for r in chunk["items"]:
                writer.writerow(["" if r.get(c) is None else r.get(c) for c in TRADE_CSV_COLUMNS])
            if page * MAX_PAGE_SIZE >= chunk["total"]:
                break
            page += 1
        return buf.getvalue()

    def trade_detail(self, trade_id: str) -> dict | None:
        trade = self.db.query_one("SELECT * FROM jr_trades WHERE trade_id = ?", (trade_id,))
        if trade is None:
            return None
        events = self.db.query(
            "SELECT * FROM jr_trade_events WHERE trade_id = ? ORDER BY ts ASC, event_id ASC",
            (trade_id,))
        for e in events:
            if e.get("detail"):
                try:
                    e["detail"] = json.loads(e["detail"])
                except ValueError:
                    pass
        sizing = self.db.query(
            "SELECT * FROM jr_sizing_decisions WHERE trade_id = ? ORDER BY applied DESC, method",
            (trade_id,))
        signals = self.db.query(
            "SELECT * FROM jr_signals WHERE trade_id = ? ORDER BY ts ASC", (trade_id,))
        return {"trade": trade, "events": events, "sizing": sizing, "signals": signals}

    def list_sessions(self, filters: dict | None = None, page: int = 1,
                      page_size: int = 50) -> dict:
        filters = dict(filters or {})
        where, params = self._where(filters, "started_at", ("instrument", "mode"))
        page, page_size = self._page(page, page_size)
        total = self.db.query_one(f"SELECT COUNT(*) AS n FROM jr_sessions{where}", params)["n"]
        rows = self.db.query(
            "SELECT s.*, (SELECT COUNT(*) FROM jr_signals g WHERE g.session_id = s.session_id) "
            "AS signals, (SELECT COUNT(*) FROM jr_trades t WHERE t.session_id = s.session_id) "
            f"AS trades FROM jr_sessions s{where} ORDER BY started_at DESC, session_id ASC "
            "LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size])
        return {"items": rows, "total": int(total), "page": page, "page_size": page_size}

    def list_signals(self, filters: dict | None = None, page: int = 1,
                     page_size: int = 50) -> dict:
        filters = dict(filters or {})
        where, params = self._where(filters, "ts", (
            "instrument", "mode", "outcome", "signal", "setup", "regime", "session_id"))
        page, page_size = self._page(page, page_size)
        total = self.db.query_one(f"SELECT COUNT(*) AS n FROM jr_signals{where}", params)["n"]
        rows = self.db.query(
            f"SELECT * FROM jr_signals{where} ORDER BY ts DESC, event_id ASC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size])
        return {"items": rows, "total": int(total), "page": page, "page_size": page_size}

    def counts(self) -> dict:
        out = {}
        for t in ("jr_sessions", "jr_signals", "jr_trades", "jr_trade_events",
                  "jr_sizing_decisions"):
            out[t] = int(self.db.query_one(f"SELECT COUNT(*) AS n FROM {t}")["n"])
        return out

    def rollback(self, confirm: str) -> None:
        """Drop ONLY the jr_* tables. Requires confirm == 'DROP_JOURNAL_V2'."""
        if confirm != "DROP_JOURNAL_V2":
            raise ValueError("refusing to drop journal tables without confirm='DROP_JOURNAL_V2'")
        for sql in ROLLBACK_SQL:
            self.db.execute(sql)


_store: JournalStore | None = None
_store_lock = threading.Lock()


def store() -> JournalStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = JournalStore()
        return _store


def reset_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None
