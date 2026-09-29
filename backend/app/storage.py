"""Persistent market history (SQLite).

Records live futures candles and option-chain snapshots per instrument so the
backtest can replay REAL captured history instead of a fresh synthetic series.
Lightweight and dependency-free (stdlib sqlite3); swap for TimescaleDB/Postgres
when scaling out (see DEPLOYMENT.md).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from app.analysis import books
from app.analysis import option_costs
from app.config import settings
from app.models import Candle, OptionQuote

# How often the full trailing candle window is re-offered to the database, rather
# than only the bars newer than the last write. Bounds the cost of gap-closing on
# the live path while still absorbing a backfill that arrives after a warm-up.
_FULL_REWRITE_SEC = 300.0

# How long a writer waits for a lock before giving up. Long, because the thing
# on the other side is a live capture flushing a minute's rows, not a hung query.
_BUSY_TIMEOUT_SEC = 60.0


class HistoryStore:
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False: the tick loop runs in an executor thread.
        self._db = sqlite3.connect(
            path, check_same_thread=False, timeout=_BUSY_TIMEOUT_SEC)
        # WAL and a wait, for the same reason the Phase 35 store needs them: this
        # store is opened at import time, so ANY second process that imports the
        # app — a CLI, a smoke run — raced the running app for it and died on
        # ``database is locked`` before its first line of work. On the default
        # rollback journal that is not a timeout, it is an immediate refusal.
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_SEC * 1000)}")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS candles (
                   instrument TEXT NOT NULL,
                   ts INTEGER NOT NULL,
                   open REAL, high REAL, low REAL, close REAL, volume REAL,
                   PRIMARY KEY (instrument, ts)
               )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS chain_snapshots (
                   instrument TEXT NOT NULL,
                   ts INTEGER NOT NULL,
                   payload TEXT NOT NULL,
                   source TEXT,
                   PRIMARY KEY (instrument, ts)
               )"""
        )
        # Additive migration for stores created before provenance labelling.
        # Legacy rows keep source NULL and must be reported as UNKNOWN: a Phase 3
        # audit unknowingly ran on simulator chains, which is exactly what an
        # explicit label prevents.
        try:
            self._db.execute("ALTER TABLE chain_snapshots ADD COLUMN source TEXT")
        except sqlite3.OperationalError:
            pass
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS paper_trades (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   instrument TEXT NOT NULL,
                   ts INTEGER NOT NULL,
                   option TEXT,
                   option_type TEXT,
                   entry REAL, exit REAL,
                   lots INTEGER,
                   net_pnl REAL,
                   win INTEGER,
                   holding_minutes INTEGER,
                   mode TEXT
               )"""
        )
        # Complete trade journal — the FULL record of every closed trade (paper
        # AND live) with the reason, confidence, whether the bot opened it, a
        # plain-English analyst note and the frozen decision context (JSON). This
        # is the "record everything" store, separate from the compact
        # paper_trades table used by the older Account report.
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS trade_journal (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   instrument TEXT NOT NULL,
                   ts INTEGER NOT NULL,
                   option TEXT,
                   option_type TEXT,
                   entry REAL, exit REAL,
                   lots INTEGER,
                   net_pnl REAL,
                   win INTEGER,
                   holding_minutes INTEGER,
                   mode TEXT,
                   auto INTEGER,
                   exit_reason TEXT,
                   confidence REAL,
                   note TEXT,
                   context TEXT
               )"""
        )
        self._db.commit()
        self._last_chain_ts: dict[str, int] = {}
        self._last_candle_ts: dict[str, tuple[int, float]] = {}

    def record_candle(self, instrument: str, candle: Candle) -> None:
        self.record_candles(instrument, [candle])

    def record_candles(self, instrument: str, candles: list[Candle]) -> int:
        """Persist a window of bars in one transaction; returns rows written.

        Recording only the newest bar per pass was why 38% of Crude's one-minute
        history was missing: an instrument is re-processed every few seconds only
        when the scan reaches it, so every minute that elapsed between two passes
        was simply never stored, while the provider's window held those bars all
        along. The primary key makes re-writing the same bar a no-op, so passing
        the recent tail costs nothing and closes the gaps.

        Between passes only bars at or after the newest one already written are
        re-offered: the same tail is re-passed every few seconds, and rewriting all
        of it each time is a transaction per instrument per pass on the live path
        for no new data. The newest stored bar is always included because it is
        still forming, and the whole window is re-offered every
        ``_FULL_REWRITE_SEC`` so that a later, longer provider window (a warm-up
        backfill) still lands.
        """
        if not candles:
            return 0
        now = time.time()
        high_water, last_full = self._last_candle_ts.get(instrument, (None, 0.0))
        full = high_water is None or now - last_full >= _FULL_REWRITE_SEC
        rows = [
            (instrument, int(c.time), c.open, c.high, c.low, c.close, c.volume)
            for c in candles
            if full or int(c.time) >= high_water
        ]
        if not rows:
            return 0
        self._last_candle_ts[instrument] = (
            max(r[1] for r in rows), now if full else last_full)
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?)", rows)
            self._db.commit()
        return len(rows)

    def candle_gaps(self, instrument: str, limit: int = 1200,
                    step: int = 60) -> dict:
        """Missing-bar report for the stored series: expected vs present bars
        inside each contiguous session, so a data-quality claim is measurable.

        Session boundaries are inferred from gaps larger than an hour (overnight /
        weekend), which are absences by definition and are not counted as missing.
        """
        stamps = [int(c.time) for c in self.candles(instrument, limit)]
        if len(stamps) < 2:
            return {"instrument": instrument, "bars": len(stamps), "expected": 0,
                    "missing": 0, "missing_pct": None, "gaps": []}
        session_break = 3600
        expected = 0
        missing = 0
        gaps: list[dict] = []
        for prev, cur in zip(stamps, stamps[1:]):
            delta = cur - prev
            if delta <= 0 or delta > session_break:
                continue
            expected += delta // step
            if delta > step:
                lost = delta // step - 1
                missing += lost
                gaps.append({"from_ts": prev, "to_ts": cur, "missing_bars": lost})
        expected += 1  # the first bar of the window is present by definition
        gaps.sort(key=lambda g: g["missing_bars"], reverse=True)
        return {
            "instrument": instrument,
            "bars": len(stamps),
            "expected": expected,
            "missing": missing,
            "missing_pct": round(100.0 * missing / expected, 2) if expected else None,
            "from_ts": stamps[0],
            "to_ts": stamps[-1],
            "gaps": gaps[:20],
        }

    def record_chain(self, instrument: str, ts: int, chain: list[OptionQuote],
                     min_interval: int = 60) -> None:
        """Store an option-chain snapshot, throttled to min_interval seconds."""
        last = self._last_chain_ts.get(instrument, 0)
        if ts - last < min_interval:
            return
        payload = json.dumps([q.model_dump(mode="json") for q in chain])
        from app.research.capture import current_source

        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO chain_snapshots "
                "(instrument, ts, payload, source) VALUES (?,?,?,?)",
                (instrument, ts, payload, current_source()),
            )
            self._db.commit()
        self._last_chain_ts[instrument] = ts

    def last_chain_ts(self, instrument: str) -> int | None:
        """Timestamp of the newest recorded chain snapshot, or None if this
        instrument has never had one stored. Read from the table rather than the
        in-memory throttle map, so it survives a restart and reports what is
        actually on disk."""
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(ts) FROM chain_snapshots WHERE instrument=?",
                (instrument,),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def candles(self, instrument: str, limit: int = 600) -> list[Candle]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, open, high, low, close, volume FROM candles "
                "WHERE instrument=? ORDER BY ts DESC LIMIT ?",
                (instrument, limit),
            ).fetchall()
        rows.reverse()
        return [
            Candle(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows
        ]

    def record_trade(self, instrument: str, trade: dict) -> None:
        """Persist one closed paper trade so the Account tab and monthly
        reports survive restarts (the in-memory journal does not)."""
        with self._lock:
            self._db.execute(
                "INSERT INTO paper_trades (instrument, ts, option, option_type, "
                "entry, exit, lots, net_pnl, win, holding_minutes, mode) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    instrument, int(trade.get("time", 0)), trade.get("option"),
                    trade.get("option_type"), trade.get("entry"), trade.get("exit"),
                    int(trade.get("lots") or 0), float(trade.get("net_pnl") or 0.0),
                    1 if trade.get("win") else 0,
                    trade.get("holding_minutes"), trade.get("mode", "paper"),
                ),
            )
            self._db.commit()

    def paper_trades(self, *, since: int | None = None,
                     until: int | None = None) -> list[dict]:
        """Return closed paper trades (all instruments) ordered oldest-first,
        optionally bounded to a [since, until) unix-time window."""
        clauses = []
        params: list[int] = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(int(since))
        if until is not None:
            clauses.append("ts < ?")
            params.append(int(until))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._db.execute(
                "SELECT instrument, ts, option, option_type, entry, exit, lots, "
                "net_pnl, win, holding_minutes, mode FROM paper_trades" + where
                + " ORDER BY ts ASC",
                tuple(params),
            ).fetchall()
        cols = ["instrument", "ts", "option", "option_type", "entry", "exit",
                "lots", "net_pnl", "win", "holding_minutes", "mode"]
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["win"] = bool(d["win"])
            out.append(d)
        return out

    def record_journal(self, instrument: str, trade: dict, note: str) -> None:
        """Persist the COMPLETE closed-trade record (reason, confidence, bot flag,
        analyst note, full context) so nothing shown on the dashboard is lost on
        restart. Best-effort — a logging failure never blocks trading."""
        ctx = trade.get("context")
        conf = trade.get("confidence")
        if conf is None and isinstance(ctx, dict):
            conf = ctx.get("confidence")
        try:
            conf_val = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf_val = None
        context_json = json.dumps(ctx, default=str) if ctx is not None else None
        with self._lock:
            self._db.execute(
                "INSERT INTO trade_journal (instrument, ts, option, option_type, "
                "entry, exit, lots, net_pnl, win, holding_minutes, mode, auto, "
                "exit_reason, confidence, note, context) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    instrument, int(trade.get("time", 0)), trade.get("option"),
                    trade.get("option_type"), trade.get("entry"), trade.get("exit"),
                    int(trade.get("lots") or 0), float(trade.get("net_pnl") or 0.0),
                    1 if trade.get("win") else 0, trade.get("holding_minutes"),
                    trade.get("mode", "paper"), 1 if trade.get("auto") else 0,
                    trade.get("exit_reason"), conf_val, note, context_json,
                ),
            )
            self._db.commit()

    def journal(self, *, since: int | None = None, until: int | None = None,
                limit: int | None = None, exclude_flow: bool = False,
                only_flow: bool = False) -> list[dict]:
        """Return the complete trade journal (all instruments), newest-first,
        optionally bounded to a [since, until) unix-time window.

        ``exclude_flow`` omits the Flow book so the board shows only real
        Signal/Auto-Buy trades; ``only_flow`` returns nothing else, which is how
        Flow stays readable in its own right instead of being deleted. Every row
        carries a ``book`` either way, so a caller can always see which book a
        number came from.
        """
        clauses = []
        params: list[int] = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(int(since))
        if until is not None:
            clauses.append("ts < ?")
            params.append(int(until))
        # SQL narrows on the markers a row can be recognised by before LIMIT is
        # applied (so the requested number of rows still comes back); the parsed
        # classification below is authoritative.
        if exclude_flow:
            clauses.append("COALESCE(note, '') NOT LIKE 'Candle-Flow%'")
            clauses.append("COALESCE(context, '') NOT LIKE '%\"engine\": \"flow\"%'")
        if only_flow:
            clauses.append(
                "(COALESCE(note, '') LIKE 'Candle-Flow%' "
                "OR COALESCE(context, '') LIKE '%\"engine\": \"flow\"%')"
            )
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # ``context`` is pulled only to surface the entry trigger (PULLBACK /
        # REVERSAL / IGNITION / MOMENTUM); the raw JSON stays out of the result
        # so the journal payload remains small.
        sql = ("SELECT instrument, ts, option, option_type, entry, exit, lots, "
               "net_pnl, win, holding_minutes, mode, auto, exit_reason, "
               "confidence, note, context FROM trade_journal" + where
               + " ORDER BY ts DESC")
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            rows = self._db.execute(sql, tuple(params)).fetchall()
        cols = ["instrument", "ts", "option", "option_type", "entry", "exit",
                "lots", "net_pnl", "win", "holding_minutes", "mode", "auto",
                "exit_reason", "confidence", "note", "context"]
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["win"] = bool(d["win"])
            d["auto"] = bool(d["auto"])
            raw = d.pop("context", None)
            trigger = None
            parsed = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, dict):
                    val = parsed.get("entry_trigger")
                    trigger = str(val).upper() if val else None
            d["entry_trigger"] = trigger
            # Capital accounting is carried inside ``context`` (no schema change,
            # so every existing row in the history keeps loading) and lifted to
            # the top level here for the journal view and the CSV. Rows written
            # before this existed simply report None.
            for field in (
                "capital_used",
                "notional",
                "risk_rupees",
                "cost_rupees",
                "return_on_capital_pct",
                "lot_size",
                # Charges itemised at exit, and the recorded book. Absent on
                # every row written before they existed, which is exactly what
                # ``cost_model`` below is for.
                "brokerage",
                "statutory",
                "brokerage_per_order",
                "entry_statutory",
                "exit_statutory",
                "cost_status",
                "entry_bid",
                "entry_ask",
                "entry_spread",
                "entry_spread_pct",
                "exit_bid",
                "exit_ask",
                "exit_spread",
                "exit_spread_pct",
                "spread_cost_rupees",
                "entry_book_quality",
                "exit_book_quality",
                "entry_book_age_sec",
                "exit_book_age_sec",
            ):
                d[field] = parsed.get(field) if isinstance(parsed, dict) else None
            # ``total`` inside the context is the charge total; named here so it
            # cannot be read as the trade total.
            d["total_costs"] = parsed.get("total") if isinstance(parsed, dict) else None
            # A row that does not name its cost model was priced by the old one,
            # which charged brokerage per LOT: a 39-lot leg carried 40x the true
            # brokerage, so 34 rows read as losses while the option had risen.
            # The stored P&L is left exactly as it was written — labelled, not
            # rewritten, because silently repricing history would destroy the
            # only record of what the system actually reported at the time.
            model = parsed.get("cost_model") if isinstance(parsed, dict) else None
            d["cost_model"] = model or option_costs.COST_MODEL_OLD
            d["book"] = books.classify(d, d.get("note"), parsed)
            if exclude_flow and d["book"] == books.FLOW:
                continue
            if only_flow and d["book"] != books.FLOW:
                continue
            out.append(d)
        return out

    def candle_count(self, instrument: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM candles WHERE instrument=?", (instrument,)
            ).fetchone()
        return int(row[0]) if row else 0


store = HistoryStore(os.path.join(settings.data_dir, "history.db"))
