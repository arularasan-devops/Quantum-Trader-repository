"""Parallel PAPER comparison books.

Every book sees the same gated BUY signals as the main engine (TRENDING +
PULLBACK, confidence floor, fresh feed, two-sided quote, spread and structural
risk caps) but keeps its own capital, lot sizing, staged exits, day profit lock
and ledger (``jr_book_trades`` / ``jr_book_days``). It never calls the broker,
the provider order path or the main engine's position; it is a ledger only.

Fills: entry at the ask, exit at the bid (premium only when the feed has no
book, i.e. the simulated provider). Top-of-book size is recorded per entry
(``depth_note``) but never used to invent a fill: Angel's quantity units differ
by segment, so lots are not cut from it.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from app.analysis import option_costs
from app.config import settings
from app.engine.account_risk import day_lock_level
from app.market.instruments import get_spec
from app.journal.store import iso, session_date, stable_id
from app.models import Signal
from app.research.db import Database

_log = logging.getLogger(__name__)

_DDL = [
    """CREATE TABLE IF NOT EXISTS jr_book_trades (
        trade_id TEXT PRIMARY KEY, book TEXT NOT NULL, capital DOUBLE PRECISION,
        sizing TEXT, instrument TEXT NOT NULL, option_symbol TEXT, lot_size INTEGER,
        lots INTEGER, capital_used DOUBLE PRECISION, risk_rupees DOUBLE PRECISION,
        entry_ts DOUBLE PRECISION NOT NULL, entry_ts_iso TEXT, entry_price DOUBLE PRECISION,
        initial_stop DOUBLE PRECISION, stop DOUBLE PRECISION, target1 DOUBLE PRECISION,
        target2 DOUBLE PRECISION, target3 DOUBLE PRECISION, peak DOUBLE PRECISION,
        floor DOUBLE PRECISION, mark DOUBLE PRECISION, setup TEXT, regime TEXT,
        confidence DOUBLE PRECISION, depth_note TEXT, status TEXT NOT NULL,
        exit_ts DOUBLE PRECISION, exit_ts_iso TEXT, exit_price DOUBLE PRECISION,
        exit_reason TEXT, gross_pnl DOUBLE PRECISION, costs DOUBLE PRECISION,
        net_pnl DOUBLE PRECISION)""",
    "CREATE INDEX IF NOT EXISTS ix_jr_book_trades_book ON jr_book_trades (book, status)",
    """CREATE TABLE IF NOT EXISTS jr_book_days (
        book TEXT NOT NULL, day TEXT NOT NULL, peak_equity DOUBLE PRECISION,
        locked INTEGER, lock_ts DOUBLE PRECISION, lock_level DOUBLE PRECISION,
        PRIMARY KEY (book, day))""",
]

_POS_COLS = ("stop", "peak", "floor", "mark")


@dataclass
class Pos:
    trade_id: str
    instrument: str
    option: str
    lot_size: int
    lots: int
    entry: float
    stop: float
    t1: float | None
    t2: float | None
    t3: float | None
    entry_ts: float
    peak: float
    floor: float | None = None
    mark: float | None = None
    armed: bool = False

    @property
    def qty(self) -> int:
        return self.lot_size * self.lots

    def unrealized(self) -> float:
        return ((self.mark if self.mark is not None else self.entry) - self.entry) * self.qty


@dataclass
class Book:
    id: str
    capital: float
    sizing: str  # FULL | RISK
    positions: dict[str, Pos] = field(default_factory=dict)
    last_key: dict[str, str] = field(default_factory=dict)
    day: str = ""
    day_realized: float = 0.0
    day_peak: float = 0.0
    locked: bool = False

    def deployed(self) -> float:
        return sum(p.entry * p.qty for p in self.positions.values())

    def equity(self) -> float:
        return self.day_realized + sum(p.unrealized() for p in self.positions.values())


def parse_books(specs: list[str]) -> list[Book]:
    out: list[Book] = []
    for raw in specs:
        cap_s, _, mode = str(raw).partition(":")
        cap = float(cap_s)
        mode = (mode or "FULL").strip().upper()
        if mode not in ("FULL", "RISK") or cap <= 0:
            raise ValueError(f"bad paper book spec {raw!r}")
        lakh = cap / 100000.0
        out.append(Book(f"{lakh:g}L-{mode}", cap, mode))
    return out


def size_lots(book: Book, entry: float, stop: float, lot_size: int) -> tuple[int, float]:
    """(lots, risk ₹). FULL: every lot free capital buys. RISK: lots whose
    entry->stop loss fits paper_books_risk_pct of the book, capped by capital."""
    if entry <= 0 or lot_size <= 0:
        return 0, 0.0
    affordable = int(max(0.0, book.capital - book.deployed()) // (entry * lot_size))
    lots = affordable
    if book.sizing == "RISK":
        per_lot = (entry - stop) * lot_size
        if per_lot <= 0:
            return 0, 0.0
        budget = book.capital * settings.paper_books_risk_pct / 100.0
        lots = min(affordable, max(1, int(budget // per_lot)) if affordable else 0)
    return lots, round(max(0.0, (entry - stop)) * lot_size * lots, 2)


def manage(pos: Pos, mark: float, capital: float) -> str | None:
    """Staged exit of one book position at ``mark`` (the bid). Mirrors the main
    engine: hard stop / PRE-T1 peak trail (break-even once armed) / T1 lock /
    T1->T2, T2->T3 trails / post-T3 runner. Floors never step down. The rupee
    arm scales with book capital (₹10k on ₹5L -> ₹30k on ₹15L)."""
    pos.mark = mark
    pos.peak = max(pos.peak, mark)
    e, pk, t1, t2, t3 = pos.entry, pos.peak, pos.t1, pos.t2, pos.t3
    floor = None
    if t3 is not None and mark >= t3:
        floor = max(t3, pk * (1.0 - settings.tier_t3_runner_trail_pct / 100.0))
    elif t2 is not None and mark >= t2:
        floor = max(t2, pk - (pk - (t1 or t2)) * settings.tier_t2_giveback_pct / 100.0)
    elif t1 is not None and mark >= t1:
        floor = t1
        if pos.floor is not None:
            floor = max(t1, pk - (pk - t1) * settings.tier_t1_giveback_pct / 100.0)
    if floor is not None and (pos.floor is None or floor > pos.floor):
        pos.floor = floor
    if pos.floor is not None:
        if mark < pos.floor - 1e-9 or (floor is None and mark <= pos.floor + 1e-9):
            if t3 is not None and pos.floor >= t3:
                return "T3 RUNNER TRAIL"
            if t2 is not None and pos.floor >= t2:
                return "T2 TRAIL"
            return "T1 LOCK"
        return None
    if settings.auto_trade_pre_t1_trail:
        arm_rs = settings.auto_trade_pre_t1_trail_arm_rupees * capital / max(1.0, settings.paper_capital)
        armed = (
            (t1 is not None and t1 > e
             and pk >= e + (t1 - e) * settings.auto_trade_pre_t1_trail_arm_pct / 100.0)
            or pk >= e * (1.0 + settings.auto_trade_pre_t1_trail_arm_profit_pct / 100.0)
            or (arm_rs > 0 and (pk - e) * pos.qty >= arm_rs)
        )
        if armed:
            pos.armed = True
            if settings.auto_trade_breakeven_after_arm and pos.stop < e:
                pos.stop = e
            gb = max((pk - e) * settings.auto_trade_pre_t1_trail_giveback_pct / 100.0,
                     pk * settings.auto_trade_pre_t1_trail_min_giveback_pct / 100.0)
            trail = pk - gb
            if trail > pos.stop:
                pos.stop = trail
    if mark <= pos.stop:
        return "PRE-T1 PEAK TRAIL" if pos.armed else "STOP LOSS"
    return None


def entry_block(instrument: str, entry: float, bid: float, ask: float,
                stop: float | None, now: float) -> str | None:
    """Spread and structural-risk checks of the pullback engine; None = ok."""
    if bid > 0 and ask >= bid:
        spread = (ask - bid) / ((ask + bid) / 2.0) * 100.0
        if spread > settings.auto_trade_max_spread_pct:
            return f"spread {spread:.1f}%"
    if entry <= 0 or stop is None or stop >= entry:
        return "invalid stop"
    risk_pct = (entry - stop) / entry * 100.0
    metals = {m.upper() for m in settings.auto_trade_metal_roots}
    if instrument.upper() in metals and settings.auto_trade_metal_risk_caps:
        caps = settings.auto_trade_metal_risk_caps
        cap = float(caps[int(now // 60) % len(caps)])
    else:
        cap = float(settings.auto_trade_max_structural_risk_pct)
    if cap > 0 and risk_pct > cap:
        return f"structural risk {risk_pct:.1f}% > {cap:g}%"
    return None


class PaperBooks:
    def __init__(self, db: Database | None = None, specs: list[str] | None = None) -> None:
        self.db = db or Database()
        for ddl in _DDL:
            self.db.execute(ddl)
        self.books = parse_books(specs if specs is not None else settings.paper_books)
        self._lock = threading.Lock()
        self.last_skip: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        by_id = {b.id: b for b in self.books}
        for r in self.db.query("SELECT * FROM jr_book_trades WHERE status = 'OPEN'"):
            b = by_id.get(r["book"])
            if b is None:
                continue
            b.positions[r["instrument"]] = Pos(
                r["trade_id"], r["instrument"], r["option_symbol"], int(r["lot_size"]),
                int(r["lots"]), float(r["entry_price"]), float(r["stop"]),
                r["target1"], r["target2"], r["target3"], float(r["entry_ts"]),
                float(r["peak"] or r["entry_price"]), r["floor"], r["mark"],
                armed=float(r["stop"]) >= float(r["entry_price"]))
        self._roll(time.time(), force=True)

    def _roll(self, now: float, force: bool = False) -> None:
        day = session_date(now)
        for b in self.books:
            if b.day == day and not force:
                continue
            b.day = day
            row = self.db.query_one(
                "SELECT peak_equity, locked FROM jr_book_days WHERE book = ? AND day = ?",
                (b.id, day))
            b.day_peak = float(row["peak_equity"] or 0.0) if row else 0.0
            b.locked = bool(row["locked"]) if row else False
            b.day_realized = 0.0
            for r in self.db.query(
                    "SELECT net_pnl, exit_ts FROM jr_book_trades WHERE book = ? AND status = 'CLOSED'",
                    (b.id,)):
                if r["exit_ts"] is not None and session_date(float(r["exit_ts"])) == day:
                    b.day_realized += float(r["net_pnl"] or 0.0)

    def _save_day(self, b: Book, now: float) -> None:
        self.db.execute(
            "INSERT INTO jr_book_days (book, day, peak_equity, locked, lock_ts, lock_level) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT (book, day) DO UPDATE SET "
            "peak_equity = excluded.peak_equity, locked = excluded.locked, "
            "lock_ts = excluded.lock_ts, lock_level = excluded.lock_level",
            (b.id, b.day, b.day_peak, int(b.locked), now if b.locked else None,
             day_lock_level(b.day_peak)))

    def _close(self, b: Book, p: Pos, price: float, reason: str, now: float) -> None:
        gross = (price - p.entry) * p.qty
        costs = option_costs.charges(p.entry, price, p.qty).total
        net = gross - costs
        self.db.execute(
            "UPDATE jr_book_trades SET status = 'CLOSED', exit_ts = ?, exit_ts_iso = ?, "
            "exit_price = ?, exit_reason = ?, gross_pnl = ?, costs = ?, net_pnl = ?, "
            "stop = ?, peak = ?, floor = ?, mark = ? WHERE trade_id = ?",
            (now, iso(now), price, reason, round(gross, 2), round(costs, 2), round(net, 2),
             p.stop, p.peak, p.floor, price, p.trade_id))
        b.positions.pop(p.instrument, None)
        b.day_realized += net
        _log.info("BOOK %s EXIT %s @ %.2f · %s · net ₹%.0f", b.id, p.option, price, reason, net)

    # ------------------------------------------------------------ lifecycle
    def on_tick(self, st, decision, chain, now: float) -> None:
        if not settings.paper_books_enabled:
            return
        with self._lock:
            self._roll(now)
            inst = st.instrument
            quotes = {q.symbol: q for q in chain or []}
            for b in self.books:
                p = b.positions.get(inst)
                if p is None:
                    continue
                q = quotes.get(p.option)
                if q is None:
                    continue
                mark = float(q.bid or 0) if q.bid else float(q.premium or 0)
                if mark <= 0:
                    continue
                before = tuple(getattr(p, c) for c in _POS_COLS)
                reason = manage(p, mark, b.capital)
                if reason:
                    self._close(b, p, mark, reason, now)
                elif tuple(getattr(p, c) for c in _POS_COLS) != before:
                    self.db.execute(
                        "UPDATE jr_book_trades SET stop = ?, peak = ?, floor = ?, mark = ? "
                        "WHERE trade_id = ?", (p.stop, p.peak, p.floor, p.mark, p.trade_id))
            for b in self.books:
                self._profit_lock(b, now)
            self._entries(st, decision, quotes, now)

    def _profit_lock(self, b: Book, now: float) -> None:
        eq = b.equity()
        changed = False
        if eq > b.day_peak:
            b.day_peak = eq
            changed = True
        lvl = day_lock_level(b.day_peak)
        if not b.locked and lvl is not None and eq < lvl - 1e-6:
            b.locked = True
            changed = True
            _log.warning("BOOK %s PROFIT_LOCK: equity ₹%.0f under lock ₹%.0f (peak ₹%.0f)",
                         b.id, eq, lvl, b.day_peak)
            if settings.profit_lock_flatten:
                for p in list(b.positions.values()):
                    self._close(b, p, p.mark if p.mark is not None else p.entry, "PROFIT LOCK", now)
        if changed:
            self._save_day(b, now)

    def _skip(self, inst: str, why: str) -> None:
        self.last_skip[inst] = why

    def _entries(self, st, d, quotes: dict, now: float) -> None:
        inst = st.instrument
        opt = d.recommended_option
        if d.signal != Signal.BUY or not opt:
            for b in self.books:
                b.last_key.pop(inst, None)
            return
        if settings.auto_trade_strategy_gate:
            trig = (d.entry_trigger or "").upper()
            regime = (getattr(st, "_last_market_status", "") or "").upper()
            if trig not in {t.upper() for t in settings.auto_trade_allowed_triggers}:
                return self._skip(inst, f"trigger {trig or '-'}")
            if regime not in {r.upper() for r in settings.auto_trade_allowed_regimes}:
                return self._skip(inst, f"regime {regime or '-'}")
        if d.confidence < settings.auto_trade_min_confidence:
            return self._skip(inst, f"confidence {d.confidence:.0f}")
        if not st._feed_ready():
            return self._skip(inst, "feed not ready")
        q = quotes.get(opt)
        if q is None:
            return self._skip(inst, "no quote")
        bid, ask = float(q.bid or 0), float(q.ask or 0)
        simulated = str(settings.data_provider).lower() == "simulated"
        if not simulated and (bid <= 0 or ask < bid):
            return self._skip(inst, "no two-sided quote")
        entry = ask if ask > 0 else float(q.premium or 0)
        why = entry_block(inst, entry, bid, ask, d.stop_loss, now)
        if why:
            return self._skip(inst, why)
        stop = float(d.stop_loss)
        key = f"{opt}|{d.option_type.value if d.option_type else ''}"
        lot_size = int(get_spec(inst).lot_size or 0)
        for b in self.books:
            if b.locked or inst in b.positions or b.last_key.get(inst) == key:
                continue
            b.last_key[inst] = key
            lots, risk = size_lots(b, entry, stop, lot_size)
            if lots < 1:
                self._skip(inst, f"{b.id}: capital")
                continue
            depth = (f"needs {lots * lot_size}, top ask shows {q.ask_size}"
                     if q.ask_size else "top ask size n/a")
            tid = stable_id("book", b.id, inst, opt, int(now))
            self.db.execute(
                "INSERT INTO jr_book_trades (trade_id, book, capital, sizing, instrument, "
                "option_symbol, lot_size, lots, capital_used, risk_rupees, entry_ts, "
                "entry_ts_iso, entry_price, initial_stop, stop, target1, target2, target3, "
                "peak, floor, mark, setup, regime, confidence, depth_note, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (trade_id) DO NOTHING",
                (tid, b.id, b.capital, b.sizing, inst, opt, lot_size, lots,
                 round(entry * lot_size * lots, 2), risk, now, iso(now), entry, stop, stop,
                 d.target1, d.target2, d.target3, entry, None, entry, d.entry_trigger,
                 getattr(st, "_last_market_status", None), d.confidence, depth, "OPEN"))
            b.positions[inst] = Pos(tid, inst, opt, lot_size, lots, entry, stop,
                                    d.target1, d.target2, d.target3, now, entry, mark=entry)
            _log.info("BOOK %s BUY %s x%d @ %.2f (stop %.2f, risk ₹%.0f)",
                      b.id, opt, lots, entry, stop, risk)

    # ------------------------------------------------------------ read API
    def summary(self) -> dict:
        with self._lock:
            self._roll(time.time())
            out = []
            for b in self.books:
                rows = self.db.query(
                    "SELECT net_pnl, costs, exit_ts, depth_note, lot_size, lots FROM jr_book_trades "
                    "WHERE book = ? AND status = 'CLOSED' ORDER BY exit_ts", (b.id,))
                nets = [float(r["net_pnl"] or 0.0) for r in rows]
                wins = [n for n in nets if n > 0]
                losses = [n for n in nets if n <= 0]
                cum = peak = dd = 0.0
                for n in nets:
                    cum += n
                    peak = max(peak, cum)
                    dd = max(dd, peak - cum)
                out.append({
                    "book": b.id, "capital": b.capital, "sizing": b.sizing,
                    "trades": len(nets), "wins": len(wins),
                    "win_rate": round(len(wins) / len(nets) * 100, 1) if nets else None,
                    "net_pnl": round(sum(nets), 2),
                    "costs": round(sum(float(r["costs"] or 0) for r in rows), 2),
                    "profit_factor": (round(sum(wins) / -sum(losses), 2)
                                      if losses and sum(losses) < 0 else None),
                    "max_drawdown": round(dd, 2),
                    "worst_trade": round(min(nets), 2) if nets else None,
                    "best_trade": round(max(nets), 2) if nets else None,
                    "return_pct": round(sum(nets) / b.capital * 100, 2),
                    "today_realized": round(b.day_realized, 2),
                    "today_equity": round(b.equity(), 2),
                    "day_peak": round(b.day_peak, 2),
                    "lock_level": day_lock_level(b.day_peak),
                    "locked": b.locked,
                    "deployed": round(b.deployed(), 2),
                    "open": [{"instrument": p.instrument, "option": p.option, "lots": p.lots,
                              "entry": p.entry, "mark": p.mark, "stop": p.stop,
                              "floor": p.floor, "unrealized": round(p.unrealized(), 2)}
                             for p in b.positions.values()],
                })
            return {"enabled": settings.paper_books_enabled,
                    "risk_pct": settings.paper_books_risk_pct,
                    "lock_keep_pct": settings.daily_lock_keep_pct,
                    "profit_target": settings.daily_profit_target,
                    "books": out, "last_skip": dict(self.last_skip)}

    def trades(self, book: str | None = None, status: str | None = None,
               page: int = 1, page_size: int = 50) -> dict:
        where, args = [], []
        if book:
            where.append("book = ?")
            args.append(book)
        if status:
            where.append("status = ?")
            args.append(status.upper())
        w = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.query_one(f"SELECT COUNT(*) AS n FROM jr_book_trades{w}", tuple(args))
        rows = self.db.query(
            f"SELECT * FROM jr_book_trades{w} ORDER BY entry_ts DESC LIMIT ? OFFSET ?",
            tuple(args) + (page_size, (page - 1) * page_size))
        return {"total": int(total["n"]) if total else 0, "page": page, "rows": rows}


_books: PaperBooks | None = None
_init_lock = threading.Lock()


def books() -> PaperBooks:
    global _books
    with _init_lock:
        if _books is None:
            _books = PaperBooks()
        return _books


def on_tick(st, decision, chain, now: float) -> None:
    """Never lets a book error reach the trading loop."""
    try:
        books().on_tick(st, decision, chain, now)
    except Exception:  # noqa: BLE001
        _log.exception("paper books tick failed")
