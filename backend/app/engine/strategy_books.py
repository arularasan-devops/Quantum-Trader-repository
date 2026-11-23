"""PAPER strategy books: one virtual account per entry strategy.

Each book has its own capital (``strategy_books_capital``, default ₹2,50,000),
its own balance (capital + its realised P&L), used margin, open positions,
day lock and ledger rows (``jr_book_trades`` with book id ``S-<STRATEGY>``).
A book only takes signals of its own strategy, and nothing is shared between
books: one book's profit or loss never changes another book's capital.

Ledger only: no broker, provider order path, LIVE position, LIVE sizing or
LIVE risk state is touched. Off unless ``QT_STRATEGY_BOOKS_ENABLED=true``.

Fills: entry at the ask, exit at the bid, net of option charges.
"""
from __future__ import annotations

import logging
import threading
import time

from app.config import settings
from app.engine import gapup
from app.engine.paper_books import Book, PaperBooks, Pos
from app.journal.store import iso, session_date, stable_id
from app.market.instruments import get_spec
from app.models import Signal

_log = logging.getLogger(__name__)

DECISION_STRATEGIES = ["PULLBACK", "BREAKOUT_RETEST", "MOMENTUM", "IGNITION", "REVERSAL",
                       "SUPERTREND", "PATTERN"]
STRATEGIES = DECISION_STRATEGIES + ["GAPUP", "BASE_BREAKOUT"]
PREFIX = "S-"


def book_id(strategy: str) -> str:
    return PREFIX + strategy


class StrategyBooks(PaperBooks):
    scope_sql = "substr(book, 1, 2) = 'S-'"

    def __init__(self, db=None) -> None:
        super().__init__(db, specs=[])
        cap = float(settings.strategy_books_capital)
        self.books = [Book(book_id(s), cap, "RISK") for s in STRATEGIES]
        self._by_id = {b.id: b for b in self.books}
        self.realized: dict[str, float] = {}
        self._gap_done: set[tuple[str, str]] = set()
        self._load()
        for b in self.books:
            self._refresh_realized(b)

    def _enabled(self) -> bool:
        return bool(settings.strategy_books_enabled)

    # ---------------------------------------------------------------- money
    def _refresh_realized(self, b: Book) -> None:
        r = self.db.query_one("SELECT COALESCE(SUM(net_pnl), 0) AS n FROM jr_book_trades "
                              "WHERE book = ? AND status = 'CLOSED'", (b.id,))
        self.realized[b.id] = float((r or {}).get("n") or 0.0)

    def balance(self, b: Book) -> float:
        return b.capital + self.realized.get(b.id, 0.0)

    def available(self, b: Book) -> float:
        return self.balance(b) - b.deployed()

    def size(self, b: Book, entry: float, stop: float, lot_size: int) -> tuple[int, float]:
        """Lots this book alone can carry: its own available cash at the ask, and
        a stop loss within ``strategy_books_risk_pct`` of its own capital."""
        if entry <= 0 or lot_size <= 0 or stop >= entry:
            return 0, 0.0
        affordable = int(max(0.0, self.available(b)) // (entry * lot_size))
        per_lot = (entry - stop) * lot_size
        budget = b.capital * settings.strategy_books_risk_pct / 100.0
        lots = min(affordable, int(budget // per_lot))
        return lots, round(per_lot * lots, 2)

    def _close(self, b, p, price, reason, now) -> None:
        super()._close(b, p, price, reason, now)
        self._refresh_realized(b)

    # -------------------------------------------------------------- entries
    def _open_long(self, b: Book, st, inst: str, q, stop: float, t1, t2, t3, setup: str,
                   confidence: float | None, now: float) -> None:
        if b.locked or any(x.instrument == inst for x in b.positions.values()):
            return
        bid, ask = float(q.bid or 0), float(q.ask or 0)
        simulated = str(settings.data_provider).lower() == "simulated"
        if not simulated and (bid <= 0 or ask < bid):
            return self._skip(inst, f"{b.id}: no two-sided quote")
        entry = ask if ask > 0 else float(q.premium or 0)
        lot_size = int(get_spec(inst).lot_size or 0)
        lots, risk = self.size(b, entry, float(stop), lot_size)
        if lots < 1:
            return self._skip(inst, f"{b.id}: own capital/risk allows 0 lots")
        tid = stable_id("sbook", b.id, inst, q.symbol, int(now))
        p = Pos(tid, inst, q.symbol, lot_size, lots, entry, float(stop), t1, t2, t3, now, entry,
                mark=entry)
        real = {"stop": float(stop), "t1": t1, "t2": t2, "t3": t3}

        class _D:  # what _open() records for setup / confidence
            entry_trigger = setup
        _D.confidence = confidence
        self._open(b, st, _D, p, real, risk, f"{setup} · ask {ask:g} bid {bid:g}", now)

    def _entries(self, st, d, quotes: dict, now: float) -> None:
        inst = st.instrument
        if self._past_cutoff(inst, now):
            return
        self._gapup_entry(st, quotes, now)
        opt = d.recommended_option
        if d.signal != Signal.BUY or not opt or d.stop_loss is None:
            for b in self.books:
                b.last_key.pop(inst, None)
            return
        if d.confidence < settings.auto_trade_min_confidence or not st._feed_ready():
            return
        q = quotes.get(opt)
        if q is None:
            return
        trigs = {str(t).upper() for t in (d.entry_triggers or [])}
        if d.entry_trigger:
            trigs.add(str(d.entry_trigger).upper())
        key = f"{opt}|{d.option_type.value if d.option_type else ''}"
        for s in DECISION_STRATEGIES:
            b = self._by_id[book_id(s)]
            if s not in trigs or b.last_key.get(inst) == key:
                continue
            b.last_key[inst] = key
            self._open_long(b, st, inst, q, float(d.stop_loss), d.target1, d.target2,
                            d.target3, s, d.confidence, now)

    def _gapup_entry(self, st, quotes: dict, now: float) -> None:
        """GAPUP rules (engine/gapup.py) on paper: first window tick with a qualifying
        gap buys the ATM CE (gap-down: PE); one entry per instrument per day."""
        inst = st.instrument
        b = self._by_id[book_id("GAPUP")]
        day = session_date(now)
        if (inst, day) in self._gap_done:
            return
        try:
            spec = get_spec(inst)
            if not gapup.in_window(spec.exchange, now) or not st._feed_ready():
                return
            ohlc = st.provider.day_ohlc() or {}
        except Exception:
            return
        side = gapup.gap_side(gapup.gap_pct(ohlc.get("open"), ohlc.get("prev_close")))
        if side is None:
            return
        d0 = time.mktime(time.strptime(day, "%Y-%m-%d")) - time.timezone - 19800
        if self.db.query_one("SELECT COUNT(*) AS n FROM jr_book_trades WHERE book = ? AND "
                             "instrument = ? AND entry_ts >= ?", (b.id, inst, d0))["n"]:
            self._gap_done.add((inst, day))  # already traded today (e.g. before a restart)
            return
        want = "CE" if side.upper() in ("UP", "CE", "CALL") else "PE"
        ces = {q.strike: q for q in quotes.values() if q.option_type.value == "CE"}
        pes = {q.strike: q for q in quotes.values() if q.option_type.value == "PE"}
        both = [k for k in ces if k in pes and ces[k].premium and pes[k].premium]
        if not both:
            return
        atm = min(both, key=lambda k: abs(float(ces[k].premium) - float(pes[k].premium)))
        q = (ces if want == "CE" else pes)[atm]
        self._gap_done.add((inst, day))
        lot_size = int(spec.lot_size or 0)
        entry = float(q.ask or q.premium or 0)
        if entry <= 0:
            return
        stop = gapup.stop_price(entry, lot_size)
        self._open_long(b, st, inst, q, stop, round(entry + gapup.target_points(lot_size), 2),
                        None, None, "GAPUP", None, now)

    def on_breakout(self, st, row: dict, chain, now: float) -> None:
        """BASE_BREAKOUT book from the shadow detector row. Premium stop = entry minus
        half the underlying R (ATM delta ~0.5), targets 1R / 2R / 3R."""
        if not self._enabled():
            return
        with self._lock:
            self._roll(now)
            inst = st.instrument
            q = next((x for x in chain or [] if x.symbol == row.get("option")), None)
            if q is None or self._past_cutoff(inst, now):
                return
            entry = float(q.ask or q.premium or 0)
            r = 0.5 * float(row.get("r_pts") or 0)
            if entry <= 0 or r <= 0 or r >= entry:
                return
            self._open_long(self._by_id[book_id("BASE_BREAKOUT")], st, inst, q,
                            round(entry - r, 2), round(entry + r, 2), round(entry + 2 * r, 2),
                            round(entry + 3 * r, 2), "BASE_BREAKOUT", None, now)

    # -------------------------------------------------------------- reports
    def summary(self) -> dict:
        out = super().summary()
        for row in out["books"]:
            b = self._by_id.get(row["book"])
            if b is None:
                continue
            row["strategy"] = b.id[len(PREFIX):]
            row["balance"] = round(self.balance(b), 2)
            row["used_margin"] = round(b.deployed(), 2)
            row["available"] = round(self.available(b), 2)
            row["realized_pnl"] = round(self.realized.get(b.id, 0.0), 2)
            row["unrealized_pnl"] = round(sum(p.unrealized() for p in b.positions.values()), 2)
        out.update(enabled=self._enabled(), risk_pct=settings.strategy_books_risk_pct,
                   total_capital=sum(b.capital for b in self.books))
        return out


_books: StrategyBooks | None = None
_init_lock = threading.Lock()


def books() -> StrategyBooks:
    global _books
    with _init_lock:
        if _books is None:
            _books = StrategyBooks()
        return _books


def on_tick(st, decision, chain, now: float) -> None:
    if not settings.strategy_books_enabled:
        return
    try:
        books().on_tick(st, decision, chain, now)
    except Exception:  # noqa: BLE001
        _log.exception("strategy books tick failed")


def on_breakout(st, row: dict, chain, now: float) -> None:
    if not settings.strategy_books_enabled:
        return
    try:
        books().on_breakout(st, row, chain, now)
    except Exception:  # noqa: BLE001
        _log.exception("strategy books breakout failed")


__all__ = ["STRATEGIES", "books", "on_tick", "on_breakout", "iso"]
