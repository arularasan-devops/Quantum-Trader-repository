"""Research store — schema + typed insert/query helpers.

Tables (backend-agnostic DDL; works on SQLite and PostgreSQL):

* ``mcx_futures``       — futures OHLC + volume/OI/vwap/atr/spread per bar
* ``mcx_options``       — option OHLC + volume/OI/IV/greeks per strike per bar
* ``signal_snapshots``  — every decision the frozen engine produced (with context)
* ``trade_history``     — completed trades incl. MFE / MAE / slippage / brokerage
* ``shadow_log``        — live recommendations vs. eventual outcome (no orders)
* ``data_quality``      — data-validation issues found on import
* ``order_events``      — live order audit events mirrored from ``order_events.jsonl``
* ``trade_journal``     — mirror of the closed-trade journal in ``history.db``

All numeric metrics are REAL captured/derived values — nothing here is synthetic.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.research.db import Database

# Data provenance labels. Real broker data and simulator data must never be
# mixed in research without being distinguishable (Phase 4, Part V).
SOURCE_REAL = "REAL_BROKER"
SOURCE_SIM = "SIMULATOR"
SOURCE_UNKNOWN = "UNKNOWN"

_DDL = [
    """CREATE TABLE IF NOT EXISTS mcx_futures (
        instrument TEXT NOT NULL,
        ts         BIGINT NOT NULL,
        open       DOUBLE PRECISION,
        high       DOUBLE PRECISION,
        low        DOUBLE PRECISION,
        close      DOUBLE PRECISION,
        volume     DOUBLE PRECISION,
        oi         DOUBLE PRECISION,
        oi_change  DOUBLE PRECISION,
        vwap       DOUBLE PRECISION,
        atr        DOUBLE PRECISION,
        spread     DOUBLE PRECISION,
        PRIMARY KEY (instrument, ts)
    )""",
    """CREATE TABLE IF NOT EXISTS mcx_options (
        instrument  TEXT NOT NULL,
        ts          BIGINT NOT NULL,
        strike      DOUBLE PRECISION NOT NULL,
        option_type TEXT NOT NULL,
        open        DOUBLE PRECISION,
        high        DOUBLE PRECISION,
        low         DOUBLE PRECISION,
        close       DOUBLE PRECISION,
        volume      DOUBLE PRECISION,
        oi          DOUBLE PRECISION,
        oi_change   DOUBLE PRECISION,
        iv          DOUBLE PRECISION,
        delta       DOUBLE PRECISION,
        theta       DOUBLE PRECISION,
        vega        DOUBLE PRECISION,
        gamma       DOUBLE PRECISION,
        bid         DOUBLE PRECISION,
        ask         DOUBLE PRECISION,
        source      TEXT,
        PRIMARY KEY (instrument, ts, strike, option_type)
    )""",
    """CREATE TABLE IF NOT EXISTS signal_snapshots (
        id           TEXT PRIMARY KEY,
        instrument   TEXT NOT NULL,
        ts           BIGINT NOT NULL,
        source       TEXT NOT NULL,
        signal       TEXT,
        confidence   DOUBLE PRECISION,
        trade_score  DOUBLE PRECISION,
        opportunity  TEXT,
        risk         TEXT,
        trend        TEXT,
        market_regime TEXT,
        premium_quality DOUBLE PRECISION,
        trap_score   DOUBLE PRECISION,
        quantum_score DOUBLE PRECISION,
        entry        DOUBLE PRECISION,
        stop         DOUBLE PRECISION,
        target1      DOUBLE PRECISION,
        target2      DOUBLE PRECISION,
        target3      DOUBLE PRECISION,
        reasoning    TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS trade_history (
        id           TEXT PRIMARY KEY,
        instrument   TEXT NOT NULL,
        source       TEXT NOT NULL,
        option_symbol TEXT,
        option_type  TEXT,
        entry_ts     BIGINT,
        exit_ts      BIGINT,
        entry        DOUBLE PRECISION,
        exit         DOUBLE PRECISION,
        pnl          DOUBLE PRECISION,
        duration_min DOUBLE PRECISION,
        exit_reason  TEXT,
        mfe          DOUBLE PRECISION,
        mae          DOUBLE PRECISION,
        slippage     DOUBLE PRECISION,
        brokerage    DOUBLE PRECISION,
        trade_score  DOUBLE PRECISION,
        market_regime TEXT,
        entry_hour   INTEGER,
        win          BOOLEAN,
        context      TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS shadow_log (
        id           TEXT PRIMARY KEY,
        instrument   TEXT NOT NULL,
        ts           BIGINT NOT NULL,
        signal       TEXT,
        confidence   DOUBLE PRECISION,
        option_type  TEXT,
        ref_price    DOUBLE PRECISION,
        horizon_min  INTEGER,
        eval_ts      BIGINT,
        eval_price   DOUBLE PRECISION,
        expected     TEXT,
        actual       TEXT,
        correct      BOOLEAN
    )""",
    """CREATE TABLE IF NOT EXISTS data_quality (
        id          TEXT PRIMARY KEY,
        instrument  TEXT NOT NULL,
        ts          BIGINT NOT NULL,
        kind        TEXT NOT NULL,
        detail      TEXT
    )""",    # v3: live order audit trail (idempotent on event_id; the JSONL log under
    # data_dir is the durable source, see app/execution/order_ledger.py).
    """CREATE TABLE IF NOT EXISTS order_events (
        event_id      TEXT PRIMARY KEY,
        ts            DOUBLE PRECISION NOT NULL,
        instrument    TEXT NOT NULL,
        kind          TEXT NOT NULL,
        order_id      TEXT,
        side          TEXT,
        option_symbol TEXT,
        status        TEXT,
        lots          INTEGER,
        filled_lots   INTEGER,
        inc_lots      INTEGER,
        inc_avg_price DOUBLE PRECISION,
        avg_price     DOUBLE PRECISION,
        mode          TEXT,
        detail        TEXT
    )""",
    # v3: closed-trade journal mirror (source_id = history.db trade_journal.id).
    """CREATE TABLE IF NOT EXISTS trade_journal (
        source_id       BIGINT PRIMARY KEY,
        instrument      TEXT NOT NULL,
        ts              BIGINT NOT NULL,
        option_symbol   TEXT,
        option_type     TEXT,
        entry           DOUBLE PRECISION,
        exit            DOUBLE PRECISION,
        lots            INTEGER,
        net_pnl         DOUBLE PRECISION,
        win             BOOLEAN,
        holding_minutes INTEGER,
        mode            TEXT,
        auto            BOOLEAN,
        exit_reason     TEXT,
        confidence      DOUBLE PRECISION,
        note            TEXT,
        context         TEXT
    )""",
]


# Bump when a new additive migration is added to ``_migrate``. Recorded in
# ``schema_migrations`` so operators can verify which schema a store is on.
STORE_SCHEMA_VERSION = 3

_SCHEMA_MIGRATIONS_DDL = """CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at BIGINT NOT NULL,
    note       TEXT
)"""


class ResearchStore:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()
        for ddl in _DDL:
            self.db.execute(ddl)
        self.db.execute(_SCHEMA_MIGRATIONS_DDL)
        self._migrate()
        self._record_schema_version()

    def _record_schema_version(self) -> None:
        import time as _time

        verb = "INSERT OR IGNORE INTO" if not self.db.is_postgres else "INSERT INTO"
        conflict = " ON CONFLICT (version) DO NOTHING" if self.db.is_postgres else ""
        for version, note in ((1, "base research tables"),
                              (2, "mcx_options bid/ask/source + schema_migrations"),
                              (3, "order_events + trade_journal mirror")):
            self.db.execute(
                f"{verb} schema_migrations (version, applied_at, note) VALUES (?,?,?)"
                + conflict,
                (version, int(_time.time()), note),
            )

    def schema_version(self) -> int:
        rows = self.db.query("SELECT MAX(version) AS v FROM schema_migrations")
        return int(rows[0]["v"] or 0) if rows else 0

    def status(self) -> dict[str, Any]:
        """Read-only health of the store: backend, schema version, row counts."""
        counts: dict[str, int] = {}
        for table in ("trade_history", "signal_snapshots", "shadow_log",
                      "mcx_futures", "mcx_options", "data_quality",
                      "order_events", "trade_journal"):
            rows = self.db.query(f"SELECT COUNT(*) AS n FROM {table}")
            counts[table] = int(rows[0]["n"]) if rows else 0
        return {
            "backend": self.backend,
            "schema_version": self.schema_version(),
            "expected_schema_version": STORE_SCHEMA_VERSION,
            "row_counts": counts,
        }

    def _migrate(self) -> None:
        """Additive-only column migrations for stores created before Phase 4.

        ``source`` matters more than it looks: Phase 3 could not tell broker rows
        from simulator rows, so an audit silently mixed them. Legacy rows stay
        NULL and are reported as UNKNOWN rather than being guessed at.
        """
        for table, col in (("mcx_options", "bid"), ("mcx_options", "ask"),
                           ("mcx_options", "source")):
            try:
                kind = "TEXT" if col == "source" else "DOUBLE PRECISION"
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
            except Exception:
                pass  # already present

    @property
    def backend(self) -> str:
        return "postgresql" if self.db.is_postgres else "sqlite"

    # ---- futures ----
    def insert_futures(self, instrument: str, rows: list[dict[str, Any]]) -> int:
        payload = [
            (
                instrument, int(r["ts"]), r.get("open"), r.get("high"), r.get("low"),
                r.get("close"), r.get("volume"), r.get("oi"), r.get("oi_change"),
                r.get("vwap"), r.get("atr"), r.get("spread"),
            )
            for r in rows
        ]
        verb = "INSERT OR REPLACE INTO" if not self.db.is_postgres else "INSERT INTO"
        conflict = "" if not self.db.is_postgres else (
            " ON CONFLICT (instrument, ts) DO NOTHING"
        )
        self.db.executemany(
            f"{verb} mcx_futures (instrument, ts, open, high, low, close, volume, "
            f"oi, oi_change, vwap, atr, spread) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)" + conflict,
            payload,
        )
        return len(payload)

    def insert_options(self, instrument: str, rows: list[dict[str, Any]]) -> int:
        payload = [
            (
                instrument, int(r["ts"]), r["strike"], r["option_type"],
                r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                r.get("volume"), r.get("oi"), r.get("oi_change"), r.get("iv"),
                r.get("delta"), r.get("theta"), r.get("vega"), r.get("gamma"),
                r.get("bid"), r.get("ask"), r.get("source") or SOURCE_UNKNOWN,
            )
            for r in rows
        ]
        verb = "INSERT OR REPLACE INTO" if not self.db.is_postgres else "INSERT INTO"
        conflict = "" if not self.db.is_postgres else (
            " ON CONFLICT (instrument, ts, strike, option_type) DO NOTHING"
        )
        self.db.executemany(
            f"{verb} mcx_options (instrument, ts, strike, option_type, open, high, "
            f"low, close, volume, oi, oi_change, iv, delta, theta, vega, gamma, "
            f"bid, ask, source) "
            f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)" + conflict,
            payload,
        )
        return len(payload)

    def futures_count(self, instrument: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM mcx_futures WHERE instrument=?", (instrument,)
        )
        return int(row["n"]) if row else 0

    def futures_rows(self, instrument: str, limit: int = 100000) -> list[dict]:
        return self.db.query(
            "SELECT ts, open, high, low, close, volume, oi, vwap, atr FROM mcx_futures "
            "WHERE instrument=? ORDER BY ts ASC LIMIT ?",
            (instrument, limit),
        )

    # ---- signal snapshots ----
    def insert_signal(self, row: dict[str, Any]) -> str:
        sid = row.get("id") or uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO signal_snapshots (id, instrument, ts, source, signal, "
            "confidence, trade_score, opportunity, risk, trend, market_regime, "
            "premium_quality, trap_score, quantum_score, entry, stop, target1, "
            "target2, target3, reasoning) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, row["instrument"], int(row["ts"]), row.get("source", "live"),
                row.get("signal"), row.get("confidence"), row.get("trade_score"),
                row.get("opportunity"), row.get("risk"), row.get("trend"),
                row.get("market_regime"), row.get("premium_quality"),
                row.get("trap_score"), row.get("quantum_score"), row.get("entry"),
                row.get("stop"), row.get("target1"), row.get("target2"),
                row.get("target3"),
                json.dumps(row.get("reasoning")) if row.get("reasoning") is not None else None,
            ),
        )
        return sid

    def signal_count(self, instrument: str, source: str | None = None) -> int:
        if source:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM signal_snapshots WHERE instrument=? AND source=?",
                (instrument, source),
            )
        else:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM signal_snapshots WHERE instrument=?", (instrument,)
            )
        return int(row["n"]) if row else 0

    def signal_distribution(self, instrument: str, source: str | None = None) -> dict[str, int]:
        if source:
            rows = self.db.query(
                "SELECT signal, COUNT(*) AS n FROM signal_snapshots WHERE instrument=? "
                "AND source=? GROUP BY signal",
                (instrument, source),
            )
        else:
            rows = self.db.query(
                "SELECT signal, COUNT(*) AS n FROM signal_snapshots WHERE instrument=? "
                "GROUP BY signal",
                (instrument,),
            )
        return {str(r["signal"]): int(r["n"]) for r in rows}

    # ---- trades ----
    def insert_trade(self, row: dict[str, Any]) -> str:
        tid = row.get("id") or uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO trade_history (id, instrument, source, option_symbol, "
            "option_type, entry_ts, exit_ts, entry, exit, pnl, duration_min, "
            "exit_reason, mfe, mae, slippage, brokerage, trade_score, market_regime, "
            "entry_hour, win, context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid, row["instrument"], row.get("source", "replay"),
                row.get("option_symbol"), row.get("option_type"), row.get("entry_ts"),
                row.get("exit_ts"), row.get("entry"), row.get("exit"), row.get("pnl"),
                row.get("duration_min"), row.get("exit_reason"), row.get("mfe"),
                row.get("mae"), row.get("slippage"), row.get("brokerage"),
                row.get("trade_score"), row.get("market_regime"), row.get("entry_hour"),
                bool(row.get("win")),
                json.dumps(row.get("context")) if row.get("context") is not None else None,
            ),
        )
        return tid

    def _insert_ignore(self, table: str, key: str) -> tuple[str, str]:
        if self.db.is_postgres:
            return f"INSERT INTO {table}", f" ON CONFLICT ({key}) DO NOTHING"
        return f"INSERT OR IGNORE INTO {table}", ""

    # ---- live order audit trail (v3) ----
    def insert_order_events(self, events: list[dict[str, Any]]) -> int:
        """Idempotent: an event_id already stored is ignored, so a retried mirror
        batch never duplicates rows."""
        if not events:
            return 0
        verb, conflict = self._insert_ignore("order_events", "event_id")
        known = {"event_id", "ts", "instrument", "kind", "order_id", "side",
                 "option_symbol", "status", "lots", "filled_lots", "inc_lots",
                 "inc_avg_price", "avg_price", "mode"}
        rows = []
        for e in events:
            extra = {k: v for k, v in e.items() if k not in known}
            rows.append((
                str(e["event_id"]), float(e.get("ts") or 0.0), str(e.get("instrument") or ""),
                str(e.get("kind") or ""), e.get("order_id"), e.get("side"),
                e.get("option_symbol"), e.get("status"), e.get("lots"), e.get("filled_lots"),
                e.get("inc_lots"), e.get("inc_avg_price"), e.get("avg_price"), e.get("mode"),
                json.dumps(extra, default=str) if extra else None,
            ))
        self.db.executemany(
            f"{verb} (event_id, ts, instrument, kind, order_id, side, option_symbol, "
            "status, lots, filled_lots, inc_lots, inc_avg_price, avg_price, mode, detail) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)" + conflict,
            rows,
        )
        return len(rows)

    # ---- closed-trade journal mirror (v3) ----
    def max_journal_source_id(self) -> int:
        row = self.db.query_one("SELECT MAX(source_id) AS m FROM trade_journal")
        return int(row["m"] or 0) if row else 0

    def insert_journal_rows(self, rows: list[dict[str, Any]]) -> int:
        """Idempotent on ``source_id`` (the history.db row id)."""
        if not rows:
            return 0
        verb, conflict = self._insert_ignore("trade_journal", "source_id")
        self.db.executemany(
            f"{verb} (source_id, instrument, ts, option_symbol, option_type, entry, exit, "
            "lots, net_pnl, win, holding_minutes, mode, auto, exit_reason, confidence, "
            "note, context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)" + conflict,
            [(
                int(r["id"]), r["instrument"], int(r["ts"]), r.get("option"),
                r.get("option_type"), r.get("entry"), r.get("exit"), r.get("lots"),
                r.get("net_pnl"), bool(r.get("win")), r.get("holding_minutes"),
                r.get("mode"), bool(r.get("auto")), r.get("exit_reason"),
                r.get("confidence"), r.get("note"), r.get("context"),
            ) for r in rows],
        )
        return len(rows)

    def trades(self, instrument: str, source: str | None = None) -> list[dict]:
        if source:
            rows = self.db.query(
                "SELECT * FROM trade_history WHERE instrument=? AND source=? ORDER BY entry_ts ASC",
                (instrument, source),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM trade_history WHERE instrument=? ORDER BY entry_ts ASC",
                (instrument,),
            )
        for r in rows:
            if r.get("context"):
                try:
                    r["context"] = json.loads(r["context"])
                except (ValueError, TypeError):
                    pass
        return rows

    def clear_trades(self, instrument: str, source: str) -> None:
        self.db.execute(
            "DELETE FROM trade_history WHERE instrument=? AND source=?", (instrument, source)
        )
        self.db.execute(
            "DELETE FROM signal_snapshots WHERE instrument=? AND source=?", (instrument, source)
        )

    # ---- shadow ----
    def insert_shadow(self, row: dict[str, Any]) -> str:
        sid = row.get("id") or uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO shadow_log (id, instrument, ts, signal, confidence, "
            "option_type, ref_price, horizon_min, eval_ts, eval_price, expected, "
            "actual, correct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, row["instrument"], int(row["ts"]), row.get("signal"),
                row.get("confidence"), row.get("option_type"), row.get("ref_price"),
                row.get("horizon_min"), row.get("eval_ts"), row.get("eval_price"),
                row.get("expected"), row.get("actual"),
                None if row.get("correct") is None else bool(row.get("correct")),
            ),
        )
        return sid

    def pending_shadow(self, instrument: str, before_ts: int) -> list[dict]:
        return self.db.query(
            "SELECT * FROM shadow_log WHERE instrument=? AND eval_ts IS NULL "
            "AND ts <= ? ORDER BY ts ASC",
            (instrument, before_ts),
        )

    def resolve_shadow(self, sid: str, eval_ts: int, eval_price: float,
                       expected: str, actual: str, correct: bool) -> None:
        self.db.execute(
            "UPDATE shadow_log SET eval_ts=?, eval_price=?, expected=?, actual=?, "
            "correct=? WHERE id=?",
            (eval_ts, eval_price, expected, actual, correct, sid),
        )

    def shadow_rows(self, instrument: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM shadow_log WHERE instrument=? ORDER BY ts ASC", (instrument,)
        )

    # ---- data quality ----
    def insert_quality_issues(self, instrument: str, issues: list[dict]) -> int:
        payload = [
            (uuid.uuid4().hex, instrument, int(i["ts"]), i["kind"], i.get("detail"))
            for i in issues
        ]
        self.db.executemany(
            "INSERT INTO data_quality (id, instrument, ts, kind, detail) VALUES (?,?,?,?,?)",
            payload,
        )
        return len(payload)


_store: ResearchStore | None = None


def store() -> ResearchStore:
    """Lazily-initialised singleton (so import never fails if the DB is absent)."""
    global _store
    if _store is None:
        _store = ResearchStore()
    return _store
