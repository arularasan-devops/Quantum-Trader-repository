"""Research store — schema + typed insert/query helpers.

Tables (backend-agnostic DDL; works on SQLite and PostgreSQL):

* ``mcx_futures``       — futures OHLC + volume/OI/vwap/atr/spread per bar
* ``mcx_options``       — option OHLC + volume/OI/IV/greeks per strike per bar
* ``signal_snapshots``  — every decision the frozen engine produced (with context)
* ``trade_history``     — completed trades incl. MFE / MAE / slippage / brokerage
* ``shadow_log``        — live recommendations vs. eventual outcome (no orders)
* ``data_quality``      — data-validation issues found on import

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
    )""",
]


class ResearchStore:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()
        for ddl in _DDL:
            self.db.execute(ddl)
        self._migrate()

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
