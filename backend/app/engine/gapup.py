"""Gap-up morning mode (off by default: ``QT_GAPUP_MODE``).

On a gap-up open the bot buys ``gapup_lots`` of the ATM CE inside the morning
window, exits on a fixed-point target (resting LIMIT SELL at Angel when live)
or on a market stop, waits ``gapup_cooldown_sec`` before buying the same
instrument again, and ratchets a day profit lock across all gap-up trades:
first lock at ``gapup_lock_first``, then every further ``gapup_lock_step``
raises it. Falling back to the lock sells every gap-up position.

The day book is shared by all instruments and saved in the order ledger, so
cooldowns, the lock and open gap-up trades survive a restart.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

from app.config import settings
from app.execution import order_ledger

log = logging.getLogger(__name__)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BOOK_KEY = "__GAPUP__"
# After a restart, wait this long for every open trade to report MTM before
# judging the profit lock.
RESTART_MARK_GRACE_SEC = 120.0


def gap_pct(open_: float | None, prev_close: float | None) -> float | None:
    if not open_ or not prev_close or prev_close <= 0:
        return None
    return (float(open_) - float(prev_close)) / float(prev_close) * 100.0


def gap_side(g: float | None) -> str | None:
    """'CE' for a qualifying gap-up, 'PE' for a qualifying gap-down, else None."""
    if g is None:
        return None
    lo, hi = float(settings.gapup_min_gap_pct), float(settings.gapup_max_gap_pct)
    if lo <= g <= hi:
        return "CE"
    if settings.gapup_trade_gapdown and lo <= -g <= hi:
        return "PE"
    return None


def _minutes(hhmm: str) -> int:
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


def in_window(exchange: str, now: float) -> bool:
    t = dt.datetime.fromtimestamp(now, IST)
    if t.weekday() >= 5:
        return False
    win = settings.gapup_window_mcx if (exchange or "").upper() == "MCX" else settings.gapup_window_nse
    try:
        start, end = (_minutes(x) for x in win.split("-"))
    except ValueError:
        return False
    m = t.hour * 60 + t.minute
    return start <= m < end


def target_points(lot_size: int) -> float:
    if int(lot_size or 0) >= settings.gapup_hq_lot:
        return float(settings.gapup_target_pts_hq)
    return float(settings.gapup_target_pts_lq)


def lock_floor(peak: float) -> float | None:
    """Locked day profit for a peak: first lock, then +step per completed step."""
    first, step = float(settings.gapup_lock_first), float(settings.gapup_lock_step)
    if first <= 0 or peak < first:
        return None
    if step <= 0:
        return first
    return first + int((peak - first) // step) * step


def stop_price(entry: float, quantity: int) -> float:
    """Tighter of the percent stop and the rupee loss cap on ``quantity`` units."""
    stop = entry * (1.0 - float(settings.gapup_stop_pct) / 100.0)
    cap = float(settings.gapup_max_loss_inr)
    if cap > 0 and quantity > 0:
        stop = max(stop, entry - cap / quantity)
    return round(max(0.05, stop), 2)


def _today(now: float) -> str:
    return dt.datetime.fromtimestamp(now, IST).date().isoformat()


class DayBook:
    """Gap-up P&L, lock, cooldowns and open gap-up trades for one IST day."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loaded = False
        self._loaded_at: float | None = None
        self.day = ""
        self.realised = 0.0
        self.peak = 0.0
        self.floor: float | None = None
        self.halted = ""
        self.last_exit: dict[str, float] = {}
        self.positions: dict[str, dict] = {}
        self._mtm: dict[str, float] = {}

    # ---------------------------------------------------------------- storage
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._loaded_at = None
        try:
            rec = order_ledger.load(BOOK_KEY)
        except Exception as exc:  # a broken ledger must not crash the tick
            log.error("gap-up book load failed: %s", exc)
            rec = {}
        self.day = str(rec.get("day") or "")
        self.realised = float(rec.get("realised") or 0.0)
        self.peak = float(rec.get("peak") or 0.0)
        self.floor = rec.get("floor")
        self.halted = str(rec.get("halted") or "")
        self.last_exit = {k: float(v) for k, v in (rec.get("last_exit") or {}).items()}
        self.positions = dict(rec.get("positions") or {})

    def _save(self) -> bool:
        try:
            order_ledger.save(BOOK_KEY, {
                "day": self.day, "realised": round(self.realised, 2),
                "peak": round(self.peak, 2), "floor": self.floor, "halted": self.halted,
                "last_exit": self.last_exit, "positions": self.positions,
            })
            return True
        except Exception as exc:
            log.error("gap-up book save failed: %s", exc)
            return False

    def _roll(self, now: float) -> None:
        self._load()
        if self._loaded_at is None:
            self._loaded_at = float(now)
        today = _today(now)
        if self.day != today:
            # Cooldowns (60 min) never span a night; open trades carry over.
            self.day, self.realised, self.peak, self.floor = today, 0.0, 0.0, None
            self.halted, self.last_exit, self._mtm = "", {}, {}
            self._save()

    # ------------------------------------------------------------------- P&L
    def total(self) -> float:
        return self.realised + sum(self._mtm.values())

    def _ratchet(self) -> None:
        tot = self.total()
        if tot > self.peak:
            self.peak = tot
        fl = lock_floor(self.peak)
        if fl is not None and (self.floor is None or fl > self.floor):
            self.floor = fl
            self._save()

    def mark(self, inst: str, mtm: float, now: float) -> None:
        with self._lock:
            self._roll(now)
            self._mtm[inst.upper()] = float(mtm)
            self._ratchet()

    def breached(self, now: float) -> bool:
        with self._lock:
            self._roll(now)
            if self.floor is None or self.total() > self.floor:
                return False
            # Fresh after a restart the open trades' MTM is unknown until each has
            # ticked once; an unmarked winner must not trip a false lock exit.
            if now - float(self._loaded_at or now) < RESTART_MARK_GRACE_SEC and any(
                    k not in self._mtm for k, r in self.positions.items() if r.get("entry")):
                return False
            if not self.halted:
                self.halted = f"profit lock ₹{self.floor:,.0f} hit"
                self._save()
            return True

    # ---------------------------------------------------------------- trades
    def open_trade(self, inst: str, rec: dict, now: float) -> bool:
        with self._lock:
            self._roll(now)
            self.positions[inst.upper()] = dict(rec)
            return self._save()

    def trade(self, inst: str) -> dict | None:
        with self._lock:
            self._load()
            return self.positions.get(inst.upper())

    def update_trade(self, inst: str, **fields) -> None:
        with self._lock:
            rec = self.positions.get(inst.upper())
            if rec is not None:
                rec.update(fields)
                self._save()

    def discard_trade(self, inst: str) -> None:
        """The buy never reached a position (rejected / not sent): no cooldown."""
        with self._lock:
            self._load()
            if self.positions.pop(inst.upper(), None) is not None:
                self._save()

    def open_notional(self) -> float:
        with self._lock:
            self._load()
            return sum(float(r.get("notional") or 0.0) for r in self.positions.values())

    def reject_trade(self, inst: str, now: float) -> None:
        """Broker rejected the buy: drop the intent and start the cooldown so the
        same order is not re-sent on every tick."""
        with self._lock:
            self._roll(now)
            key = inst.upper()
            self.positions.pop(key, None)
            self._mtm.pop(key, None)
            self.last_exit[key] = float(now)
            self._save()

    def close_trade(self, inst: str, pnl: float, now: float) -> None:
        with self._lock:
            self._roll(now)
            key = inst.upper()
            self.positions.pop(key, None)
            self._mtm.pop(key, None)
            self.realised += float(pnl)
            self.last_exit[key] = float(now)
            self._ratchet()
            self._save()

    # ---------------------------------------------------------------- gating
    def entry_block(self, inst: str, now: float) -> str | None:
        with self._lock:
            self._roll(now)
            key = inst.upper()
            if key in self.positions:
                return "gap-up trade already open"
            if self.halted:
                return self.halted
            if settings.gapup_stop_new_after_lock and self.floor is not None:
                return f"profit lock ₹{self.floor:,.0f} active — no new gap-up buys"
            ex = self.last_exit.get(key)
            cool = float(settings.gapup_cooldown_sec)
            if ex is not None and cool > 0 and now - ex < cool:
                return f"gap-up cooldown ({int((cool - (now - ex)) / 60) + 1} min left)"
            return None

    def snapshot(self) -> dict:
        with self._lock:
            self._load()
            return {"day": self.day, "realised": round(self.realised, 1),
                    "open_mtm": round(sum(self._mtm.values()), 1),
                    "total": round(self.total(), 1), "peak": round(self.peak, 1),
                    "lock": self.floor, "halted": self.halted or None,
                    "open": sorted(self.positions), "last_exit": dict(self.last_exit)}


book = DayBook()
