"""AI decision journal, paper-trade book and outcome dataset.

Three tables in the existing research database (same ``Database`` wrapper, same
parameterised-SQL convention, no ORM):

``ai_decisions``
    Every evaluation the AI engine makes, including the ones that decided NOT to
    trade, with the baseline engine's simultaneous verdict alongside. Recording
    only the trades would make the dataset a record of the model's own selection
    and nothing else — the NO_TRADE rows are what make the comparison and any
    future retraining honest.

``ai_decision_outcomes``
    What the market subsequently did, on the SAME geometry the research used
    (0.8 ATR stop, 1.2R target, 30-bar horizon), measured on the underlying and
    filled in by the resolver once the horizon has passed. Written once and never
    updated, so a decision cannot be relabelled after the fact.

``ai_paper_trades``
    The live paper book: entry context, live-managed extremes and the realised
    result with costs.

Nothing in here can place an order; it is a record, and the retraining loop that
reads it stops at "model candidate" — promotion is manual (see
``phase6_train.py --promote``, which requires a human to run it).
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from app.research.db import Database

_DDL = [
    """CREATE TABLE IF NOT EXISTS ai_decisions (
        id              TEXT PRIMARY KEY,
        ts              BIGINT NOT NULL,
        instrument      TEXT NOT NULL,
        price           DOUBLE PRECISION,
        atr             DOUBLE PRECISION,
        feed_state      TEXT,
        data_age_ms     DOUBLE PRECISION,
        scan_state      TEXT,
        scan_verdict    TEXT,
        scan_score      DOUBLE PRECISION,
        regime          TEXT,
        regime_conf     DOUBLE PRECISION,
        direction_side  TEXT,
        p_ce            DOUBLE PRECISION,
        p_pe            DOUBLE PRECISION,
        entry_quality   TEXT,
        entry_score     DOUBLE PRECISION,
        ai_probability  DOUBLE PRECISION,
        expected_r      DOUBLE PRECISION,
        ai_decision     TEXT NOT NULL,
        ai_side         TEXT,
        paper_decision  TEXT,
        blocked_by      TEXT,
        baseline_decision TEXT,
        baseline_side   TEXT,
        baseline_conf   DOUBLE PRECISION,
        model_version   TEXT,
        reasons         TEXT,
        features        TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ai_decision_outcomes (
        decision_id   TEXT PRIMARY KEY,
        resolved_ts   BIGINT NOT NULL,
        horizon_bars  INTEGER,
        basis         TEXT,
        ce_result     TEXT,
        ce_r          DOUBLE PRECISION,
        ce_mfe_r      DOUBLE PRECISION,
        ce_mae_r      DOUBLE PRECISION,
        pe_result     TEXT,
        pe_r          DOUBLE PRECISION,
        pe_mfe_r      DOUBLE PRECISION,
        pe_mae_r      DOUBLE PRECISION,
        taken_side    TEXT,
        taken_result  TEXT,
        taken_r       DOUBLE PRECISION
    )""",
    """CREATE TABLE IF NOT EXISTS ai_paper_trades (
        id             TEXT PRIMARY KEY,
        decision_id    TEXT,
        instrument     TEXT NOT NULL,
        symbol         TEXT,
        side           TEXT NOT NULL,
        strike         DOUBLE PRECISION,
        expiry         TEXT,
        entry_ts       BIGINT NOT NULL,
        entry_premium  DOUBLE PRECISION,
        entry_quote    DOUBLE PRECISION,
        entry_spread   DOUBLE PRECISION,
        entry_slippage DOUBLE PRECISION,
        underlying_entry DOUBLE PRECISION,
        stop           DOUBLE PRECISION,
        target1        DOUBLE PRECISION,
        target2        DOUBLE PRECISION,
        target3        DOUBLE PRECISION,
        lots           INTEGER,
        lot_size       INTEGER,
        risk_amount    DOUBLE PRECISION,
        probability    DOUBLE PRECISION,
        regime         TEXT,
        direction      TEXT,
        entry_quality  TEXT,
        status         TEXT NOT NULL,
        current_premium DOUBLE PRECISION,
        unrealized_pnl DOUBLE PRECISION,
        mfe            DOUBLE PRECISION,
        mae            DOUBLE PRECISION,
        exit_ts        BIGINT,
        exit_premium   DOUBLE PRECISION,
        exit_reason    TEXT,
        realized_pnl   DOUBLE PRECISION,
        costs          DOUBLE PRECISION,
        r_multiple     DOUBLE PRECISION,
        hold_sec       DOUBLE PRECISION,
        notes          TEXT
    )""",
]

_lock = threading.Lock()
_store: "AIJournal | None" = None


def _dumps(v: dict | list | None) -> str | None:
    return None if v is None else json.dumps(v)


class AIJournal:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()
        for ddl in _DDL:
            self.db.execute(ddl)

    # ------------------------------------------------------------- decisions
    def insert_decision(self, row: dict) -> str:
        did = row.get("id") or uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO ai_decisions (id, ts, instrument, price, atr, feed_state, "
            "data_age_ms, scan_state, scan_verdict, scan_score, regime, regime_conf, "
            "direction_side, p_ce, p_pe, entry_quality, entry_score, ai_probability, "
            "expected_r, ai_decision, ai_side, paper_decision, blocked_by, "
            "baseline_decision, baseline_side, baseline_conf, model_version, reasons, "
            "features) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                did, int(row["ts"]), row["instrument"], row.get("price"),
                row.get("atr"), row.get("feed_state"), row.get("data_age_ms"),
                row.get("scan_state"), row.get("scan_verdict"), row.get("scan_score"),
                row.get("regime"), row.get("regime_conf"), row.get("direction_side"),
                row.get("p_ce"), row.get("p_pe"), row.get("entry_quality"),
                row.get("entry_score"), row.get("ai_probability"),
                row.get("expected_r"), row["ai_decision"], row.get("ai_side"),
                row.get("paper_decision"), row.get("blocked_by"),
                row.get("baseline_decision"), row.get("baseline_side"),
                row.get("baseline_conf"), row.get("model_version"),
                _dumps(row.get("reasons")), _dumps(row.get("features")),
            ),
        )
        return did

    def decisions(self, instrument: str | None = None, limit: int = 200) -> list[dict]:
        if instrument:
            return self.db.query(
                "SELECT * FROM ai_decisions WHERE instrument=? ORDER BY ts DESC LIMIT ?",
                (instrument, limit))
        return self.db.query(
            "SELECT * FROM ai_decisions ORDER BY ts DESC LIMIT ?", (limit,))

    def decision_count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM ai_decisions")
        return int(row["n"]) if row else 0

    def outcome_counts(self) -> dict:
        """Resolved rows by basis — real labels and give-ups counted separately, so a
        queue that is only *marked* resolved can never look like a dataset."""
        rows = self.db.query(
            "SELECT basis, COUNT(*) AS n FROM ai_decision_outcomes GROUP BY basis")
        out = {str(r["basis"] or "UNKNOWN"): int(r["n"]) for r in rows}
        labelled = sum(n for b, n in out.items() if not b.startswith("UNRESOLVABLE"))
        return {"by_basis": out, "labelled": labelled,
                "total": sum(out.values())}

    def unresolved_decisions(self, before_ts: int, limit: int = 500) -> list[dict]:
        return self.db.query(
            "SELECT d.id, d.ts, d.instrument, d.price, d.atr, d.ai_side "
            "FROM ai_decisions d LEFT JOIN ai_decision_outcomes o ON o.decision_id=d.id "
            "WHERE o.decision_id IS NULL AND d.ts <= ? ORDER BY d.ts ASC LIMIT ?",
            (int(before_ts), limit))

    def insert_outcome(self, row: dict) -> None:
        self.db.execute(
            "INSERT INTO ai_decision_outcomes (decision_id, resolved_ts, horizon_bars, "
            "basis, ce_result, ce_r, ce_mfe_r, ce_mae_r, pe_result, pe_r, pe_mfe_r, "
            "pe_mae_r, taken_side, taken_result, taken_r) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["decision_id"], int(row["resolved_ts"]), row.get("horizon_bars"),
                row.get("basis"), row.get("ce_result"), row.get("ce_r"),
                row.get("ce_mfe_r"), row.get("ce_mae_r"), row.get("pe_result"),
                row.get("pe_r"), row.get("pe_mfe_r"), row.get("pe_mae_r"),
                row.get("taken_side"), row.get("taken_result"), row.get("taken_r"),
            ),
        )

    def dataset(self, limit: int = 100000) -> list[dict]:
        """Resolved decisions joined to their outcomes — the retraining dataset."""
        return self.db.query(
            "SELECT d.*, o.ce_result, o.ce_r, o.ce_mfe_r, o.ce_mae_r, o.pe_result, "
            "o.pe_r, o.pe_mfe_r, o.pe_mae_r, o.taken_side, o.taken_result, o.taken_r "
            "FROM ai_decisions d JOIN ai_decision_outcomes o ON o.decision_id=d.id "
            # Give-up rows carry no labels; they exist only to keep the resolver
            # queue moving and must never enter the training dataset.
            "WHERE o.ce_result IS NOT NULL "
            "ORDER BY d.ts ASC LIMIT ?", (limit,))

    # ----------------------------------------------------------- paper book
    def insert_trade(self, row: dict) -> str:
        tid = row.get("id") or uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO ai_paper_trades (id, decision_id, instrument, symbol, side, "
            "strike, expiry, entry_ts, entry_premium, entry_quote, entry_spread, "
            "entry_slippage, underlying_entry, stop, target1, target2, target3, lots, "
            "lot_size, risk_amount, probability, regime, direction, entry_quality, "
            "status, current_premium, unrealized_pnl, mfe, mae, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid, row.get("decision_id"), row["instrument"], row.get("symbol"),
                row["side"], row.get("strike"), row.get("expiry"), int(row["entry_ts"]),
                row.get("entry_premium"), row.get("entry_quote"),
                row.get("entry_spread"), row.get("entry_slippage"),
                row.get("underlying_entry"), row.get("stop"), row.get("target1"),
                row.get("target2"), row.get("target3"), row.get("lots"),
                row.get("lot_size"), row.get("risk_amount"), row.get("probability"),
                row.get("regime"), row.get("direction"), row.get("entry_quality"),
                row.get("status", "OPEN"), row.get("current_premium"),
                row.get("unrealized_pnl", 0.0), row.get("mfe", 0.0),
                row.get("mae", 0.0), row.get("notes"),
            ),
        )
        return tid

    def update_live(self, tid: str, premium: float, unrealized: float,
                    mfe: float, mae: float, stop: float, target1: float) -> None:
        self.db.execute(
            "UPDATE ai_paper_trades SET current_premium=?, unrealized_pnl=?, mfe=?, "
            "mae=?, stop=?, target1=? WHERE id=?",
            (premium, unrealized, mfe, mae, stop, target1, tid))

    def close_trade(self, tid: str, exit_ts: int, exit_premium: float, reason: str,
                    realized: float, costs: float, r_multiple: float,
                    hold_sec: float) -> None:
        self.db.execute(
            "UPDATE ai_paper_trades SET status='CLOSED', exit_ts=?, exit_premium=?, "
            "exit_reason=?, realized_pnl=?, costs=?, r_multiple=?, hold_sec=?, "
            "current_premium=?, unrealized_pnl=0 WHERE id=?",
            (int(exit_ts), exit_premium, reason, realized, costs, r_multiple,
             hold_sec, exit_premium, tid))

    def open_trades(self) -> list[dict]:
        return self.db.query(
            "SELECT * FROM ai_paper_trades WHERE status='OPEN' ORDER BY entry_ts ASC")

    def trades(self, limit: int = 500, instrument: str | None = None) -> list[dict]:
        if instrument:
            return self.db.query(
                "SELECT * FROM ai_paper_trades WHERE instrument=? "
                "ORDER BY entry_ts DESC LIMIT ?", (instrument, limit))
        return self.db.query(
            "SELECT * FROM ai_paper_trades ORDER BY entry_ts DESC LIMIT ?", (limit,))

    def closed_trades(self, since_ts: int = 0) -> list[dict]:
        return self.db.query(
            "SELECT * FROM ai_paper_trades WHERE status='CLOSED' AND entry_ts>=? "
            "ORDER BY entry_ts ASC", (int(since_ts),))

    def trades_today(self, day_start_ts: int) -> list[dict]:
        return self.db.query(
            "SELECT * FROM ai_paper_trades WHERE entry_ts>=? ORDER BY entry_ts ASC",
            (int(day_start_ts),))

    def has_open(self, instrument: str, side: str) -> bool:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM ai_paper_trades WHERE status='OPEN' AND "
            "instrument=? AND side=?", (instrument, side))
        return bool(row and int(row["n"]) > 0)

    def get_trade(self, tid: str) -> dict | None:
        return self.db.query_one("SELECT * FROM ai_paper_trades WHERE id=?", (tid,))

    def set_levels(self, tid: str, stop: float | None = None,
                   target1: float | None = None) -> None:
        if stop is not None:
            self.db.execute("UPDATE ai_paper_trades SET stop=? WHERE id=?", (stop, tid))
        if target1 is not None:
            self.db.execute("UPDATE ai_paper_trades SET target1=? WHERE id=?",
                            (target1, tid))


def journal() -> AIJournal:
    global _store
    with _lock:
        if _store is None:
            _store = AIJournal()
        return _store


def use_journal(store: AIJournal | None) -> AIJournal | None:
    """Swap the process-wide journal (tests use this for a temp database).

    Returns the previous journal so a caller can restore it.
    """
    global _store
    with _lock:
        previous = _store
        _store = store
        return previous


def day_start_ts(now: float | None = None) -> int:
    """Start of the current IST trading day, as a unix timestamp."""
    import datetime as dt

    now = now if now is not None else time.time()
    ist = dt.datetime.utcfromtimestamp(now) + dt.timedelta(hours=5, minutes=30)
    midnight_ist = ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((midnight_ist - dt.timedelta(hours=5, minutes=30))
               .replace(tzinfo=dt.timezone.utc).timestamp())
