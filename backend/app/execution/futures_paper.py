"""FUTURES paper-trading tool — a SEPARATE tool, run alongside the option engine.

Why this is its own tool and not a mode of the option engine
-----------------------------------------------------------
The option engine is denominated in PREMIUM: its stop is a percentage of the
premium, its T1/T2/T3 and the past-T3 ratchet are premium points, and its sizing
is ``premium x lot_size``. None of that transfers to a futures contract, where
risk is measured in INDEX POINTS and one point is ``lot_size`` rupees regardless
of any premium. Rather than thread a second unit system through the validated
money path, this module is self-contained: its own entry rules, its own
point-based risk, its own journal and its own scoreboard, so the two can be
compared side by side on the same market without either affecting the other.

What actually differs from buying options
-----------------------------------------
* **No theta.** Buying an option bleeds every minute you wait to be proven
  right; a futures position does not. That is the single biggest structural
  difference and the reason a slower, wider trade can work here that would not
  work on a premium.
* **No IV.** No volatility crush, no premium over-extension to veto.
* **Symmetric.** A bearish view is a SHORT, not a long put, so a downtrend is
  traded directly instead of through a decaying instrument.
* **Unbounded loss.** An option's maximum loss is the premium paid; a futures
  loss is unbounded. The stop is therefore not optional — it IS the risk
  control, and every entry here is sized from it.

Honest status
-------------
PAPER ONLY and hard-locked: ``place_order`` is never called from this module and
there is no live route into it. Nothing here has been validated against real
fills, so it produces a track record to be judged, not a recommendation. Fills
are modelled at the candle close plus a slippage allowance; real futures fills
will differ.
"""
from __future__ import annotations

import json as _json
import logging
import os as _os
import time as _time

import numpy as np

from app.analysis import analyst as _analyst
from app.analysis import indicators as ind
from app import storage as _storage
from app.config import settings
from app.market.instruments import get_spec
from app.models import Candle, FuturesSignal, MarketStatus

_log = logging.getLogger(__name__)

_LOG_NAME = "futures_paper.jsonl"
_OPEN_NAME = "futures_open.json"
_GUARD_NAME = "futures_guard.json"

# A futures position is only ever intraday here: an overnight gap on a leveraged,
# unbounded-loss instrument cannot be managed by a stop.
_SQUARE_OFF_MINUTES = 15
# Cap on stored path samples per trade. An intraday futures trade ticks a few
# hundred times at most; the cap keeps a stuck position from growing the log
# without bound while still covering a normal hold.
_MAX_PATH_SAMPLES = 600


def _max_cost_to_risk() -> float:
    """A round trip must not cost a meaningful share of the risk being taken.

    Above this ratio the stop loses well over 1R when it is hit and no win rate
    rescues it. Measured on a real session: every CRUDEOIL trade taken at ~19%
    cost-to-risk lost money, which is why the ceiling is a setting now and why
    its default came down from 25%.
    """
    return max(0.01, float(settings.futures_max_cost_to_risk_pct) / 100.0)


def _log_path() -> str:
    return _os.path.join(settings.data_dir, _LOG_NAME)


def _open_path() -> str:
    return _os.path.join(settings.data_dir, _OPEN_NAME)


def _guard_path() -> str:
    return _os.path.join(settings.data_dir, _GUARD_NAME)


def _lot_size(instrument: str) -> int:
    try:
        return int(get_spec(instrument).lot_size)
    except Exception:
        return 1


def _arrays(candles: list[Candle]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    h = np.array([c.high for c in candles], dtype=float)
    lo = np.array([c.low for c in candles], dtype=float)
    c = np.array([c.close for c in candles], dtype=float)
    return h, lo, c


class _Position:
    """One open paper futures position. Points-based throughout."""

    __slots__ = (
        "instrument", "side", "entry", "lots", "lot_size", "stop", "t1", "t2", "t3",
        "opened", "peak", "trough", "floor", "hit_t1", "hit_t2", "hit_t3", "risk_pts",
        "entry_reason", "armed", "path",
    )

    def __init__(
        self,
        instrument: str,
        side: str,
        entry: float,
        lots: int,
        stop: float,
        t1: float,
        t2: float,
        t3: float,
        opened: int,
        entry_reason: str,
    ) -> None:
        self.instrument = instrument
        self.side = side
        self.entry = entry
        self.lots = lots
        self.lot_size = _lot_size(instrument)
        self.stop = stop
        self.t1 = t1
        self.t2 = t2
        self.t3 = t3
        self.opened = opened
        self.peak = entry
        self.trough = entry
        self.floor: float | None = None
        self.hit_t1 = False
        self.hit_t2 = False
        self.hit_t3 = False
        self.risk_pts = abs(entry - stop)
        self.entry_reason = entry_reason
        # Set once the pre-T1 trail has armed. R is measured from the ENTRY stop,
        # so it is stored separately from ``stop`` — the break-even ratchet moves
        # the stop, and recomputing R from it would silently shrink every later
        # R-multiple, target lock and trail gap.
        self.armed = False
        # Bounded (ts, price) samples for the life of the trade. Without a stored
        # path an exit rule cannot be re-measured afterwards: the recorded book
        # kept only the peak, and the candle series in history.db neither covers
        # the minutes these trades ran nor matches their price level, so no
        # counterfactual could be graded on it.
        self.path: list[tuple[int, float]] = []

    @property
    def long(self) -> bool:
        return self.side == "LONG"

    def points(self, price: float) -> float:
        """Open profit in index points, signed for the direction held."""
        return (price - self.entry) if self.long else (self.entry - price)

    def rupees(self, price: float) -> float:
        return self.points(price) * self.lot_size * self.lots

    def favourable(self, price: float) -> float:
        """How far price has travelled in our favour, in points."""
        return (self.peak - self.entry) if self.long else (self.entry - self.trough)

    def adverse(self) -> float:
        """How far price has travelled against us, in points (>= 0)."""
        worst = self.trough if self.long else self.peak
        return abs(self.entry - worst)

    def sample(self, now: int, price: float) -> None:
        """Record one path point, capped so a long hold cannot grow unbounded."""
        if len(self.path) < _MAX_PATH_SAMPLES:
            self.path.append((int(now), round(float(price), 2)))

    def floor_points(self) -> float:
        """Distance above entry to lock once T1 is reached, in points.

        A fraction of R plus the full round-trip cost, so an exit on this floor
        is genuinely positive rather than a cost-sized loss booked at the entry
        price. Never wider than T1 itself — the floor must stay behind the level
        that triggered it, or it would close the trade the instant it is set.
        """
        cost = _cost_points(self.instrument, self.entry, self.entry, self.lot_size, self.lots)
        lock = max(0.0, settings.futures_t1_lock_r) * self.risk_pts + cost
        return min(lock, abs(self.t1 - self.entry) * 0.9)


class _Store:
    """Per-instrument open paper positions + the finished-trade log.

    Open positions are mirrored to disk on every change and reloaded at startup.
    Without that, a restart silently drops a position that is mid-trade: it never
    reaches the closed-trade log, so the history the tool exists to build would
    quietly lose exactly the trades that were running when it went down.
    """

    def __init__(self) -> None:
        self._open: dict[str, _Position] = {}
        self._loaded = False

    def get(self, instrument: str) -> _Position | None:
        self._ensure_loaded()
        return self._open.get(instrument)

    def set(self, instrument: str, pos: _Position | None) -> None:
        self._ensure_loaded()
        if pos is None:
            self._open.pop(instrument, None)
        else:
            self._open[instrument] = pos
        self._save()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(_open_path(), encoding="utf-8") as fh:
                raw = _json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(raw, list):
            return
        for r in raw:
            if not isinstance(r, dict):
                continue
            try:
                p = _Position(
                    str(r["instrument"]), str(r["side"]), float(r["entry"]),
                    int(r["lots"]), float(r["stop"]), float(r["t1"]),
                    float(r["t2"]), float(r["t3"]), int(r["opened"]),
                    str(r.get("entry_reason") or ""),
                )
            except (KeyError, TypeError, ValueError):
                continue
            p.peak = float(r.get("peak", p.entry))
            p.trough = float(r.get("trough", p.entry))
            fl = r.get("floor")
            p.floor = float(fl) if fl is not None else None
            p.hit_t1 = bool(r.get("hit_t1"))
            p.hit_t2 = bool(r.get("hit_t2"))
            p.hit_t3 = bool(r.get("hit_t3"))
            p.armed = bool(r.get("armed"))
            raw_path = r.get("path")
            if isinstance(raw_path, list):
                for pt in raw_path[:_MAX_PATH_SAMPLES]:
                    try:
                        p.path.append((int(pt[0]), float(pt[1])))
                    except (IndexError, TypeError, ValueError):
                        continue
            try:
                risk = float(r["risk_pts"])
            except (KeyError, TypeError, ValueError):
                risk = p.risk_pts
            if risk > 0:
                p.risk_pts = risk
            self._open[p.instrument] = p

    def _save(self) -> None:
        rows = [{
            "instrument": p.instrument, "side": p.side, "entry": p.entry,
            "lots": p.lots, "stop": p.stop, "t1": p.t1, "t2": p.t2, "t3": p.t3,
            "opened": p.opened, "entry_reason": p.entry_reason, "peak": p.peak,
            "trough": p.trough, "floor": p.floor, "hit_t1": p.hit_t1,
            "hit_t2": p.hit_t2, "hit_t3": p.hit_t3, "armed": p.armed,
            "risk_pts": p.risk_pts, "path": [[t, v] for t, v in p.path],
        } for p in self._open.values()]
        try:
            _os.makedirs(settings.data_dir, exist_ok=True)
            tmp = _open_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                _json.dump(rows, fh)
            _os.replace(tmp, _open_path())
        except OSError:
            _log.warning("could not persist open futures positions", exc_info=True)

    def checkpoint(self) -> None:
        """Persist in-flight changes (floor ratchets, peaks) made during a tick."""
        if self._loaded:
            self._save()

    def open_count(self) -> int:
        self._ensure_loaded()
        return len(self._open)

    def open_positions(self) -> list[dict]:  # noqa: D401
        self._ensure_loaded()
        """Every open paper position, across instruments.

        The bot runs on several instruments at once, so the board must show what
        it is holding elsewhere — otherwise an instrument that is merely BLOCKED
        by the concurrency cap looks like the bot is doing nothing.
        """  # noqa: D208
        out: list[dict] = []
        for p in self._open.values():
            out.append({
                "instrument": p.instrument,
                "side": p.side,
                "entry": round(p.entry, 2),
                "lots": p.lots,
                "lot_size": p.lot_size,
                "stop": round(p.stop, 2),
                "target1": round(p.t1, 2),
                "target2": round(p.t2, 2),
                "target3": round(p.t3, 2),
                "locked_floor": round(p.floor, 2) if p.floor is not None else None,
                "levels_hit": "T3+" if p.hit_t3 else ("T2" if p.hit_t2 else (
                    "T1" if p.hit_t1 else "—")),
                "risk_points": round(p.risk_pts, 2),
                # Risk still on the table: to the ratcheted floor once one exists,
                # otherwise to the hard stop.
                "risk_rupees": round(
                    abs(p.entry - (p.floor if p.floor is not None else p.stop))
                    * p.lot_size
                    * p.lots,
                    0,
                ),
                "opened": p.opened,
                "entry_reason": p.entry_reason,
            })
        return sorted(out, key=lambda r: r["opened"])

    def deployed_margin(self) -> float:
        """Approximate margin tied up across open paper positions."""
        self._ensure_loaded()
        total = 0.0
        for p in self._open.values():
            total += p.entry * p.lot_size * p.lots * settings.futures_margin_pct / 100.0
        return total


store = _Store()


class _Guard:
    """Session risk governor for the futures tool: caps, cooldowns, stand-downs.

    The option engine has had a daily-loss cap, a per-session trade cap and a
    consecutive-loss halt from the start. The futures tool had none of them, and
    a single choppy afternoon produced six stop-outs in a row, alternating side,
    one to seventeen minutes apart — each paying a full round trip to discover
    the market was ranging. On an instrument whose loss is unbounded these caps
    are more important than they are on a premium, not less.

    State is per trading day and per instrument, persisted so a restart does not
    hand the tool a fresh set of lives mid-session.
    """

    def __init__(self) -> None:
        self.day: str = ""
        self.day_pnl: float = 0.0
        self.trades: dict[str, int] = {}
        self.losses: dict[str, int] = {}
        self.last_exit: dict[str, int] = {}
        self.last_stop: dict[str, tuple[int, str]] = {}
        self.stand_down_until: dict[str, int] = {}
        self._loaded = False

    @staticmethod
    def _today(now: int) -> str:
        return _time.strftime("%Y-%m-%d", _time.gmtime(int(now) + 19800))

    def _roll(self, now: int) -> None:
        self._load()
        today = self._today(now)
        if self.day == today:
            return
        self.day = today
        self.day_pnl = 0.0
        self.trades.clear()
        self.losses.clear()
        self.last_exit.clear()
        self.last_stop.clear()
        self.stand_down_until.clear()
        self._save()

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(_guard_path(), encoding="utf-8") as fh:
                raw = _json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self.day = str(raw.get("day") or "")
        self.day_pnl = float(raw.get("day_pnl") or 0.0)
        self.trades = {str(k): int(v) for k, v in (raw.get("trades") or {}).items()}
        self.losses = {str(k): int(v) for k, v in (raw.get("losses") or {}).items()}
        self.last_exit = {str(k): int(v) for k, v in (raw.get("last_exit") or {}).items()}
        self.stand_down_until = {
            str(k): int(v) for k, v in (raw.get("stand_down_until") or {}).items()
        }
        for k, v in (raw.get("last_stop") or {}).items():
            try:
                self.last_stop[str(k)] = (int(v[0]), str(v[1]))
            except (TypeError, ValueError, IndexError):
                continue

    def _save(self) -> None:
        payload = {
            "day": self.day,
            "day_pnl": self.day_pnl,
            "trades": self.trades,
            "losses": self.losses,
            "last_exit": self.last_exit,
            "last_stop": {k: [v[0], v[1]] for k, v in self.last_stop.items()},
            "stand_down_until": self.stand_down_until,
        }
        try:
            _os.makedirs(settings.data_dir, exist_ok=True)
            tmp = _guard_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                _json.dump(payload, fh)
            _os.replace(tmp, _guard_path())
        except OSError:
            _log.warning("could not persist the futures risk governor", exc_info=True)

    def record_entry(self, instrument: str, now: int) -> None:
        self._roll(now)
        self.trades[instrument] = self.trades.get(instrument, 0) + 1
        self._save()

    def record_exit(self, rec: dict, now: int) -> None:
        self._roll(now)
        inst = str(rec.get("instrument") or "")
        net = float(rec.get("net_rupees") or 0.0)
        self.day_pnl += net
        if net > 0:
            self.losses[inst] = 0
        else:
            self.losses[inst] = self.losses.get(inst, 0) + 1
            if self.losses[inst] >= max(1, settings.futures_max_consecutive_losses):
                self.stand_down_until[inst] = int(now) + int(settings.futures_stand_down_sec)
        self.last_exit[inst] = int(now)
        if str(rec.get("exit_reason") or "").startswith("STOP"):
            self.last_stop[inst] = (int(now), str(rec.get("side") or ""))
        self._save()

    def blocked(self, instrument: str, side: str | None, now: int) -> str | None:
        """Why this instrument may not be entered right now, or None if it may."""
        self._roll(now)
        cap = abs(float(settings.futures_max_daily_loss))
        if cap > 0 and self.day_pnl <= -cap:
            return (
                f"futures day P&L is ₹{self.day_pnl:,.0f} — the ₹{cap:,.0f} daily loss "
                f"cap is hit, the tool is done for the session"
            )
        until = self.stand_down_until.get(instrument, 0)
        if until > now:
            return (
                f"{self.losses.get(instrument, 0)} losses in a row on {instrument} — "
                f"standing down for {int((until - now) / 60)} more min"
            )
        limit = max(1, settings.futures_max_trades_per_day)
        taken = self.trades.get(instrument, 0)
        if taken >= limit:
            return f"{taken} {instrument} trades today — the {limit}/day cap is reached"
        cool = float(settings.futures_reentry_cooldown_sec)
        last = self.last_exit.get(instrument)
        if cool > 0 and last is not None and now - last < cool:
            return (
                f"re-entry cooldown on {instrument} — {int(cool - (now - last))}s left "
                f"after the last exit"
            )
        # A stop means the level just proved itself against us. Taking the OTHER
        # side moments later is not a new signal, it is the same chop: the
        # measured sequence was SHORT stop, LONG stop, LONG stop, SHORT stop.
        flip = float(settings.futures_flip_block_sec)
        stopped = self.last_stop.get(instrument)
        if flip > 0 and side and stopped and stopped[1] and stopped[1] != side:
            if now - stopped[0] < flip:
                return (
                    f"stopped out {int((now - stopped[0]) / 60)} min ago on the "
                    f"{stopped[1]} side — no immediate flip to {side}"
                )
        return None

    def reset(self) -> None:
        """Clear the day's governor state. Used by tests and a manual day reset."""
        self.day = ""
        self.day_pnl = 0.0
        self.trades.clear()
        self.losses.clear()
        self.last_exit.clear()
        self.last_stop.clear()
        self.stand_down_until.clear()
        self._loaded = True
        self._save()

    def snapshot(self, now: int) -> dict:
        self._roll(now)
        return {
            "day": self.day,
            "day_pnl": round(self.day_pnl, 0),
            "daily_loss_cap": settings.futures_max_daily_loss,
            "trades_today": dict(self.trades),
            "consecutive_losses": dict(self.losses),
            "standing_down": {
                k: int((v - now) / 60) for k, v in self.stand_down_until.items() if v > now
            },
        }


guard = _Guard()


def _append_log(rec: dict) -> None:
    try:
        _os.makedirs(settings.data_dir, exist_ok=True)
        with open(_log_path(), "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec) + "\n")
    except Exception:
        pass


def read_log(limit: int = 500) -> list[dict]:
    """Finished paper futures trades, newest last. Best-effort."""
    path = _log_path()
    out: list[dict] = []
    try:
        if not _os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(_json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out[-limit:]


def report(limit: int = 2000) -> dict:
    """Scoreboard for the futures tool: trades, win rate, profit factor, points.

    Deliberately reports PROFIT FACTOR next to win rate — a tool can win most of
    its trades and still lose money, and on a leveraged instrument that is the
    normal way to lose money.
    """
    rows = read_log(limit)
    closed = [r for r in rows if r.get("exit") is not None]
    n = len(closed)
    wins = [r for r in closed if (r.get("net_points") or 0) > 0]
    losses = [r for r in closed if (r.get("net_points") or 0) <= 0]
    gross_win = sum(float(r.get("net_rupees") or 0) for r in wins)
    gross_loss = -sum(float(r.get("net_rupees") or 0) for r in losses)
    pf = round(gross_win / gross_loss, 3) if gross_loss > 0 else None
    net = sum(float(r.get("net_rupees") or 0) for r in closed)
    pts = sum(float(r.get("net_points") or 0) for r in closed)
    by_reason: dict[str, dict] = {}
    for r in closed:
        key = str(r.get("exit_reason") or "OTHER")
        b = by_reason.setdefault(key, {"trades": 0, "net_rupees": 0.0})
        b["trades"] += 1
        b["net_rupees"] = round(b["net_rupees"] + float(r.get("net_rupees") or 0), 1)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / n, 1) if n else None,
        "profit_factor": pf,
        "net_rupees": round(net, 1),
        "net_points": round(pts, 1),
        "avg_win_points": round(
            sum(float(r.get("net_points") or 0) for r in wins) / len(wins), 2
        ) if wins else None,
        "avg_loss_points": round(
            sum(float(r.get("net_points") or 0) for r in losses) / len(losses), 2
        ) if losses else None,
        "by_exit_reason": by_reason,
        "paper_only": True,
        "note": (
            "Paper fills modelled at the candle close plus a slippage allowance. "
            "Not validated against real futures fills."
        ),
    }


def _cost_points(
    instrument: str, entry: float, exit_price: float, lot_size: int, lots: int
) -> float:
    """Round-trip cost of one futures trade, expressed in index points.

    Brokerage is a flat fee but the statutory charges are a percentage of
    TURNOVER, and on a large-notional contract they dominate: STT alone on one
    NIFTY lot is several times the brokerage. Charging only the flat fee makes
    the paper P&L look better than any real fill could be.
    """
    qty = max(1.0, float(lot_size * lots))
    buy_value = entry * qty
    sell_value = exit_price * qty
    try:
        commodity = get_spec(instrument).exchange.upper() == "MCX"
    except Exception:
        commodity = False
    sell_tax_pct = (
        settings.futures_ctt_sell_pct if commodity else settings.futures_stt_sell_pct
    )
    rupees = (
        settings.futures_brokerage_per_order * 2.0
        + sell_value * sell_tax_pct / 100.0
        + (buy_value + sell_value) * settings.futures_txn_pct / 100.0
    )
    return rupees / qty + 2.0 * settings.futures_slippage_points


def _close(pos: _Position, price: float, reason: str, now: int) -> dict:
    """Book the paper trade, charging costs in points, and write it to the log."""
    lot_size = pos.lot_size
    gross_pts = pos.points(price)
    cost_pts = _cost_points(pos.instrument, pos.entry, price, lot_size, pos.lots)
    net_pts = gross_pts - cost_pts
    qty = lot_size * pos.lots
    net_rupees = net_pts * qty
    # Capital used on a futures trade is the MARGIN blocked, not the notional
    # value of the contract: a Crude lot is ~Rs7.9L of oil carried on ~Rs95k of
    # margin, so measuring a win against the notional understates the return by
    # ~8x and measuring it against nothing at all says nothing.
    notional = pos.entry * qty
    margin_rupees = notional * settings.futures_margin_pct / 100.0
    rec = {
        "tool": "futures_paper",
        "notional_rupees": round(notional, 0),
        "margin_rupees": round(margin_rupees, 0),
        "capital_used": round(margin_rupees, 0),
        "risk_rupees": round(pos.risk_pts * qty, 0),
        "cost_rupees": round(cost_pts * qty, 0),
        "return_on_capital_pct": (
            round(net_rupees / margin_rupees * 100.0, 2) if margin_rupees > 0 else None
        ),
        "capital_pot": settings.futures_capital,
        "instrument": pos.instrument,
        "side": pos.side,
        "entry": round(pos.entry, 2),
        "exit": round(price, 2),
        "lots": pos.lots,
        "lot_size": lot_size,
        "gross_points": round(gross_pts, 2),
        "cost_points": round(cost_pts, 2),
        "net_points": round(net_pts, 2),
        "net_rupees": round(net_rupees, 1),
        "risk_points": round(pos.risk_pts, 2),
        "r_multiple": round(net_pts / pos.risk_pts, 2) if pos.risk_pts > 0 else None,
        "max_favourable_points": round(pos.favourable(price), 2),
        "max_adverse_points": round(pos.adverse(), 2),
        # Whether the pre-T1 protection had armed, and the path it armed on, so
        # this trade can be re-graded under a different exit rule without
        # re-running the market.
        "pre_t1_armed": pos.armed,
        "path": [[t, p] for t, p in pos.path],
        "stop": round(pos.stop, 2),
        "t1": round(pos.t1, 2),
        "t2": round(pos.t2, 2),
        "t3": round(pos.t3, 2),
        "exit_reason": reason,
        "entry_reason": pos.entry_reason,
        "opened": pos.opened,
        "closed": int(now),
        "holding_minutes": int(max(0, (int(now) - pos.opened)) / 60),
        "win": net_pts > 0,
        "mode": "paper",
    }
    _append_log(rec)
    _mirror_journal(rec)
    return rec


def _mirror_journal(rec: dict) -> None:
    """Mirror the finished futures trade into the unified Journal tagged FUT ·
    paper, so it sits alongside the option trades for comparison. Never raises."""
    try:
        trade = {
            "time": rec.get("closed") or int(_time.time()),
            "instrument": rec.get("instrument"),
            "option": f"{rec.get('instrument')} FUT",
            "option_type": None,
            "entry": rec.get("entry"),
            "exit": rec.get("exit"),
            "lots": rec.get("lots"),
            "net_pnl": rec.get("net_rupees"),
            "win": bool(rec.get("win")),
            "holding_minutes": rec.get("holding_minutes"),
            "mode": "paper",
            "auto": True,
            "engine": "futures",
            "exit_reason": rec.get("exit_reason"),
            "confidence": None,
            # The trigger is read back out of ``context``, so FUT has to live in
            # here — at the top level it would be dropped and the trade would
            # show as UNTAGGED next to the option entries it exists to be
            # compared against.
            "context": {
                "entry_trigger": "FUT",
                "tool": "futures_paper",
                "side": rec.get("side"),
                "risk_points": rec.get("risk_points"),
                "r_multiple": rec.get("r_multiple"),
                # Capital accounting. A bare "+Rs1,626" says nothing about how
                # much money was tied up to earn it: on futures that is the
                # MARGIN blocked, not the notional value of the contract.
                "capital_used": rec.get("margin_rupees"),
                "notional": rec.get("notional_rupees"),
                "risk_rupees": rec.get("risk_rupees"),
                "cost_rupees": rec.get("cost_rupees"),
                "return_on_capital_pct": rec.get("return_on_capital_pct"),
                "lot_size": rec.get("lot_size"),
            },
        }
        _storage.store.record_journal(
            rec.get("instrument") or "", trade, _analyst.note_for_trade(trade)
        )
    except Exception:
        # Never break the tick over a mirror, but do not swallow it silently
        # either — a quiet failure here loses the comparison the tool is for.
        _log.warning("futures journal mirror failed", exc_info=True)


def _size_lots(entry: float, stop_pts: float, lot_size: int) -> tuple[int, str]:
    """Size from RISK first, then check the capital can carry the margin.

    On futures the loss is unbounded, so the only sound way to size is: decide the
    rupees you accept losing if the stop is hit, then derive the lots from the
    stop distance. A lot that cannot be margined is rejected outright rather than
    silently traded.
    """
    if stop_pts <= 0 or lot_size <= 0:
        return 0, "stop distance unavailable"
    risk_budget = settings.futures_capital * settings.futures_risk_per_trade_pct / 100.0
    risk_per_lot = stop_pts * lot_size
    lots = int(risk_budget // risk_per_lot)
    if lots < 1:
        return 0, (
            f"one lot risks ₹{risk_per_lot:,.0f} at a {stop_pts:.0f}-point stop, above the "
            f"₹{risk_budget:,.0f} risk budget ({settings.futures_risk_per_trade_pct:g}% of "
            f"₹{settings.futures_capital:,.0f})"
        )
    margin_per_lot = entry * lot_size * settings.futures_margin_pct / 100.0
    free = settings.futures_capital - store.deployed_margin()
    affordable = int(free // margin_per_lot) if margin_per_lot > 0 else 0
    if affordable < 1:
        return 0, (
            f"margin for one lot is ≈₹{margin_per_lot:,.0f} but only ₹{free:,.0f} of the "
            f"₹{settings.futures_capital:,.0f} is free"
        )
    if settings.futures_size_by_margin:
        # Deliberate override: take every lot the free margin can carry. This
        # abandons the risk budget — at a 20-point stop on Crude, five affordable
        # lots risk Rs10,000, not the Rs5,000 the 1% rule allows — so the rupee
        # risk it implies is spelled out in the note rather than left implicit.
        lots = affordable
    lots = min(lots, affordable)
    hard_cap = max(0, settings.futures_paper_lots)
    if hard_cap > 0:
        lots = min(lots, hard_cap)
    if lots < 1:
        return 0, "lot cap is set to zero"
    risk_rupees = risk_per_lot * lots
    risk_pct = risk_rupees / settings.futures_capital * 100.0 if settings.futures_capital else 0.0
    return lots, (
        f"{lots} lot(s): risking ≈₹{risk_rupees:,.0f} at the stop "
        f"({risk_pct:.1f}% of ₹{settings.futures_capital:,.0f}), "
        f"margin ≈₹{margin_per_lot * lots:,.0f}"
    )


def _entry_setup(
    candles: list[Candle],
    price: float,
    atr_val: float,
    status: MarketStatus,
) -> tuple[str | None, str]:
    """This tool's own entry rules, in points — no premium, no delta, no theta.

    Trend-following with two ways in, both requiring the higher-timeframe
    direction to agree:

      * PULLBACK  — price pulls back toward EMA20 inside an EMA20>EMA50 trend and
        turns back up. Because there is no decay to pay, waiting for the pullback
        costs nothing here, unlike on a premium.
      * BREAK     — price closes beyond the recent range extreme with a strong
        body, i.e. a genuine expansion rather than a drift.

    A ranging/choppy market returns no setup: without theta income there is no
    edge in chopping around a mean.
    """
    if len(candles) < 60 or atr_val <= 0:
        return None, "not enough 1-min history yet"

    h, lo, c = _arrays(candles)
    ema20 = ind.ema(c, 20)
    ema50 = ind.ema(c, 50)
    adx_val = ind.adx(h, lo, c, 14)
    if ema20 is None or ema50 is None:
        return None, "trend not computable yet"

    # Dead-market filter, expressed as a FRACTION OF PRICE. An absolute point
    # threshold means a different thing on every contract, which is the unit bug
    # that made this tool Crude-only: 5 points is 0.06% of a Rs7,900 Crude
    # contract and trivially cleared, but a 1-minute ATR on a Rs150 stock future
    # is well under one point, so every stock and Nat Gas was refused as "too
    # quiet" before any other rule ran. The legacy absolute setting is honoured
    # only when explicitly set above zero.
    min_atr_pct = max(0.0, settings.futures_min_atr_pct)
    if min_atr_pct > 0 and price > 0 and atr_val < price * min_atr_pct / 100.0:
        need = price * min_atr_pct / 100.0
        return None, (
            f"ATR {atr_val:.2f} is below {min_atr_pct:g}% of price ({need:.2f}) — too quiet"
        )
    min_atr = settings.futures_min_atr_points
    if min_atr > 0 and atr_val < min_atr:
        return None, f"ATR {atr_val:.1f} pts is below the {min_atr:g}-pt minimum — too quiet"
    if adx_val is not None and adx_val < settings.futures_min_adx:
        return None, (
            f"ADX {adx_val:.0f} is below {settings.futures_min_adx:g} — no trend to follow"
        )
    if status == MarketStatus.RANGING:
        return None, "market is ranging — this tool only trades expansion"

    up = ema20 > ema50 and price > ema50
    down = ema20 < ema50 and price < ema50
    if not (up or down):
        return None, "EMA20/EMA50 are not aligned — no directional edge"

    body = abs(c[-1] - candles[-1].open)
    bodies = np.abs(c[-20:] - np.array([x.open for x in candles[-20:]], dtype=float))
    avg_body = float(bodies.mean()) if len(bodies) else 0.0
    strong_body = avg_body > 0 and body >= settings.futures_body_factor * avg_body

    look = 20
    range_hi = float(h[-look:-1].max())
    range_lo = float(lo[-look:-1].min())

    if up:
        pulled = (float(h[-8:].max()) - price) >= 0.3 * atr_val
        turned = c[-1] > c[-2]
        if pulled and turned and price >= ema20 - 0.5 * atr_val:
            return "LONG", "pullback into an EMA20/50 uptrend, turning back up"
        if strong_body and price > range_hi:
            return "LONG", f"range break above {range_hi:.1f} on a strong body"
        return None, "uptrend intact but no pullback or break yet — waiting"

    pulled = (price - float(lo[-8:].min())) >= 0.3 * atr_val
    turned = c[-1] < c[-2]
    if pulled and turned and price <= ema20 + 0.5 * atr_val:
        return "SHORT", "pullback into an EMA20/50 downtrend, turning back down"
    if strong_body and price < range_lo:
        return "SHORT", f"range break below {range_lo:.1f} on a strong body"
    return None, "downtrend intact but no pullback or break yet — waiting"


def _levels(
    side: str, price: float, atr_val: float, support: float | None, resistance: float | None
) -> tuple[float, float, float, float]:
    """Stop and T1/T2/T3 in INDEX POINTS, anchored to structure.

    The stop is never placed inside the level price must break, and the targets
    are R-multiples of the resulting risk so reward:risk is explicit rather than
    implied.
    """
    stop_dist = settings.futures_stop_atr * atr_val
    if side == "LONG":
        if support and 0 < price - support < 2.5 * atr_val:
            stop_dist = max(stop_dist, price - support + 0.15 * atr_val)
        stop = price - stop_dist
        r = price - stop
        return (
            round(stop, 1),
            round(price + settings.futures_t1_r * r, 1),
            round(price + settings.futures_t2_r * r, 1),
            round(price + settings.futures_t3_r * r, 1),
        )
    if resistance and 0 < resistance - price < 2.5 * atr_val:
        stop_dist = max(stop_dist, resistance - price + 0.15 * atr_val)
    stop = price + stop_dist
    r = stop - price
    return (
        round(stop, 1),
        round(price - settings.futures_t1_r * r, 1),
        round(price - settings.futures_t2_r * r, 1),
        round(price - settings.futures_t3_r * r, 1),
    )


def _manage(pos: _Position, price: float, minutes_to_close: float, now: int) -> tuple[
    FuturesSignal | None, dict | None
]:
    """Advance an open paper position: trail, bank targets, or exit."""
    # Both extremes, every tick: the favourable one drives the trail, the adverse
    # one is the MAE the exit rules have to be graded against later.
    pos.peak = max(pos.peak, price)
    pos.trough = min(pos.trough, price)
    pos.sample(now, price)

    # Intraday only — an overnight gap on an unbounded-loss instrument cannot be
    # managed by a stop, so the position is squared off near the bell.
    if minutes_to_close <= _SQUARE_OFF_MINUTES:
        return None, _close(pos, price, "SQUARE-OFF (intraday only)", now)

    # Hard stop first: on futures this is the entire risk control.
    hit_stop = price <= pos.stop if pos.long else price >= pos.stop
    if hit_stop:
        return None, _close(pos, price, "STOP LOSS", now)

    # Locked floor from a previously banked level.
    if pos.floor is not None:
        gave_back = price <= pos.floor if pos.long else price >= pos.floor
        if gave_back:
            lvl = "T3+ RATCHET" if pos.hit_t3 else ("TARGET 2" if pos.hit_t2 else "TARGET 1")
            return None, _close(pos, price, f"{lvl} lock", now)

    reached = (lambda lv: price >= lv) if pos.long else (lambda lv: price <= lv)

    # --- pre-T1 profit protection ------------------------------------------
    # Before this existed the only thing under an open futures trade until T1
    # was the hard stop, so a trade that ran part of the way and reversed gave
    # back every point of shown profit and then lost a full R. Two stages, both
    # measured from the ENTRY risk so they cannot move after the fact:
    #   1. once the favourable excursion reaches ``futures_pre_t1_arm_r``, lift
    #      the hard stop to entry + round-trip cost (a genuine scratch, not the
    #      entry price, which on futures is a cost-sized loss);
    #   2. optionally (OFF by default) exit if price hands back
    #      ``futures_pre_t1_giveback_pct`` of the run from entry to the peak,
    #      floored at the round-trip cost so the exit is still positive and noise
    #      alone cannot trigger it.
    # Stage 1 cannot clip a winner; stage 2 can, and the recorded book has more
    # winner value above the arm threshold (Rs22,356) than loss it could avoid
    # (Rs13,942), so stage 2 stays off until tick paths can grade it. Neither
    # stage creates an edge — they bound the loss on a trade that showed profit.
    if (
        settings.futures_pre_t1_trail
        and not pos.hit_t1
        and pos.floor is None
        and pos.risk_pts > 0
    ):
        cost_pts = _cost_points(pos.instrument, pos.entry, price, pos.lot_size, pos.lots)
        run = pos.favourable(price)
        if not pos.armed and run >= max(0.0, settings.futures_pre_t1_arm_r) * pos.risk_pts:
            pos.armed = True
        if pos.armed:
            if settings.futures_pre_t1_breakeven:
                scratch = pos.entry + cost_pts if pos.long else pos.entry - cost_pts
                # Only ratchet while price is still beyond the scratch level, or
                # the new stop would close the trade on the same tick it is set.
                if (price > scratch) if pos.long else (price < scratch):
                    pos.stop = max(pos.stop, scratch) if pos.long else min(pos.stop, scratch)
            if settings.futures_pre_t1_giveback_exit:
                giveback = max(
                    cost_pts, run * max(0.0, settings.futures_pre_t1_giveback_pct) / 100.0
                )
                trail = (pos.peak - giveback) if pos.long else (pos.trough + giveback)
                gave_back = price <= trail if pos.long else price >= trail
                if gave_back and pos.points(price) > cost_pts:
                    return None, _close(pos, price, "PRE-T1 PEAK TRAIL", now)

    if reached(pos.t3):
        pos.hit_t3 = True
        # Past T3 the trade is left to run and the floor ratchets up in ATR-scaled
        # steps, so a big trend is not capped at T3. It cannot exit at the exact
        # top — one step is always given back.
        step = max(
            settings.futures_beyond_t3_min_step,
            settings.futures_beyond_t3_step_atr * max(1e-9, pos.risk_pts),
        )
        if pos.long:
            floor = pos.t3 + int((price - pos.t3) // step) * step
            pos.floor = max(pos.floor or floor, floor)
        else:
            floor = pos.t3 - int((pos.t3 - price) // step) * step
            pos.floor = min(pos.floor if pos.floor is not None else floor, floor)
    elif reached(pos.t2):
        pos.hit_t2 = True
        pos.floor = pos.t2 if pos.floor is None else (
            max(pos.floor, pos.t2) if pos.long else min(pos.floor, pos.t2)
        )
    elif reached(pos.t1):
        pos.hit_t1 = True
        # At T1 the floor moves ABOVE the entry, not to it. Two reasons, both
        # measured: exiting at the entry price is not break-even on futures — it
        # is a loss of the whole round-trip cost (four such exits closed at 0 or
        # +1 point for -Rs375 each) — and a trade that has already travelled a
        # full 1R should not be allowed to hand all of it back.
        lock = pos.floor_points()
        be = pos.entry + lock if pos.long else pos.entry - lock
        pos.floor = be if pos.floor is None else (
            max(pos.floor, be) if pos.long else min(pos.floor, be)
        )

    # Between T1 and T2 the floor FOLLOWS the peak instead of sitting still at
    # the T1 lock. Measured 13 Aug: all three winners exited on the T1 lock at
    # ~0.5R after running further, because nothing moved the floor until T2 was
    # touched. Trailing it means a run to 1.5R banks ~1R rather than 0.5R, and it
    # can never fall below the cost-aware T1 lock already set above.
    trail_r = max(0.0, settings.futures_post_t1_trail_r)
    if pos.hit_t1 and not pos.hit_t2 and trail_r > 0:
        gap = trail_r * max(1e-9, pos.risk_pts)
        trailed = pos.peak - gap if pos.long else pos.trough + gap
        if pos.floor is not None:
            pos.floor = max(pos.floor, trailed) if pos.long else min(pos.floor, trailed)
    return _holding_signal(pos, price), None


def _holding_signal(pos: _Position, price: float) -> FuturesSignal:
    hit = "T3+" if pos.hit_t3 else ("T2" if pos.hit_t2 else ("T1" if pos.hit_t1 else "—"))
    return FuturesSignal(
        enabled=True,
        state="HOLDING",
        side=pos.side,
        entry=round(pos.entry, 1),
        price=round(price, 1),
        stop=round(pos.stop, 1),
        target1=round(pos.t1, 1),
        target2=round(pos.t2, 1),
        target3=round(pos.t3, 1),
        locked_floor=round(pos.floor, 1) if pos.floor is not None else None,
        lots=pos.lots,
        lot_size=pos.lot_size,
        risk_points=round(pos.risk_pts, 1),
        # Live risk, not entry risk: once the floor has ratcheted, what is still
        # on the table is the distance to the floor, which is what the trader
        # actually stands to lose from here.
        risk_rupees=round(
            abs(pos.entry - (pos.floor if pos.floor is not None else pos.stop))
            * pos.lot_size
            * pos.lots,
            0,
        ),
        open_points=round(pos.points(price), 1),
        open_rupees=round(pos.rupees(price), 0),
        levels_hit=hit,
        reason=f"{pos.side} open · {pos.entry_reason}",
        sizing_note=None,
        paper_only=True,
    )


def tick(
    instrument: str,
    candles: list[Candle],
    price: float,
    atr_val: float | None,
    status: MarketStatus,
    support: float | None,
    resistance: float | None,
    minutes_to_close: float,
    now: int,
) -> FuturesSignal:
    """Advance the futures paper tool one tick and return its board state.

    Never places an order and never touches the option engine. Best-effort: any
    failure returns a disabled board rather than breaking the tick.
    """
    if not settings.futures_paper_enabled:
        return FuturesSignal(enabled=False, state="OFF", reason="Futures tool is off.")
    try:
        pos = store.get(instrument)
        if pos is not None:
            sig, closed = _manage(pos, price, minutes_to_close, now)
            if closed is not None:
                store.set(instrument, None)
                guard.record_exit(closed, int(now))
                return FuturesSignal(
                    enabled=True,
                    state="CLOSED",
                    side=closed["side"],
                    entry=closed["entry"],
                    price=closed["exit"],
                    lots=closed["lots"],
                    lot_size=closed["lot_size"],
                    net_points=closed["net_points"],
                    net_rupees=closed["net_rupees"],
                    reason=f"Closed: {closed['exit_reason']} ({closed['net_points']:+.1f} pts)",
                    paper_only=True,
                )
            # A tick can ratchet the floor or mark a level hit; persist that so a
            # restart resumes the trade where it actually is, not where it opened.
            store.checkpoint()
            return sig or FuturesSignal(enabled=True, state="HOLDING", paper_only=True)

        if minutes_to_close <= settings.futures_no_entry_minutes:
            return FuturesSignal(
                enabled=True,
                state="BLOCKED",
                reason=(
                    f"{minutes_to_close:.0f} min to the close — no new entry inside the last "
                    f"{settings.futures_no_entry_minutes:g} min (intraday only)."
                ),
                paper_only=True,
            )
        if store.open_count() >= max(1, settings.futures_max_concurrent):
            return FuturesSignal(
                enabled=True,
                state="BLOCKED",
                reason=(
                    f"{store.open_count()} futures paper position(s) already open "
                    f"(max {settings.futures_max_concurrent})."
                ),
                paper_only=True,
            )

        # Session risk governor: daily loss cap, per-instrument trade cap,
        # consecutive-loss stand-down and the post-exit cooldown. Checked before
        # the setup is even computed so the board says why it is standing aside.
        halted = guard.blocked(instrument, None, int(now))
        if halted:
            return FuturesSignal(
                enabled=True, state="BLOCKED", reason=halted, paper_only=True
            )

        atrv = float(atr_val or 0.0)
        side, why = _entry_setup(candles, price, atrv, status)
        if side is None:
            return FuturesSignal(enabled=True, state="WAIT", reason=why, paper_only=True)

        # Re-checked with the direction known: a stop just proved the level
        # against us, so the opposite side moments later is the same chop.
        flipped = guard.blocked(instrument, side, int(now))
        if flipped:
            return FuturesSignal(
                enabled=True, state="BLOCKED", side=side, reason=flipped, paper_only=True
            )

        # Slippage is charged on the modelled fill, and every level is then
        # derived FROM that fill. Pricing the stop and targets off the unslipped
        # price understates the real risk and hands back reward:risk that was
        # never there — a 1-pt slip on a 1.2-pt risk is most of the trade.
        fill = price + settings.futures_slippage_points * (1 if side == "LONG" else -1)
        stop, t1, t2, t3 = _levels(side, fill, atrv, support, resistance)
        stop_pts = abs(fill - stop)
        pos_lot_size = _lot_size(instrument)
        lots, note = _size_lots(fill, stop_pts, pos_lot_size)
        if lots < 1:
            return FuturesSignal(
                enabled=True,
                state="BLOCKED",
                side=side,
                price=round(price, 1),
                stop=stop,
                target1=t1,
                target2=t2,
                target3=t3,
                risk_points=round(stop_pts, 1),
                reason=f"{side} setup found but not sized: {note}",
                sizing_note=note,
                paper_only=True,
            )

        # Cost-to-risk gate. Slippage and brokerage are charged per round trip, so
        # a stop that is close to the cost of trading loses more than 1R when it is
        # hit and cannot be profitable in either direction. Refuse rather than take
        # a trade whose arithmetic is already against it.
        cost_pts = _cost_points(instrument, fill, fill, pos_lot_size, lots)
        if cost_pts > _max_cost_to_risk() * stop_pts:
            return FuturesSignal(
                enabled=True,
                state="BLOCKED",
                side=side,
                price=round(price, 1),
                stop=stop,
                risk_points=round(stop_pts, 1),
                reason=(
                    f"{side} setup skipped: a round trip costs {cost_pts:.2f} pts against "
                    f"a {stop_pts:.2f}-pt stop — the costs would exceed "
                    f"{_max_cost_to_risk():.0%} of the risk"
                ),
                paper_only=True,
            )

        # Target-to-cost gate. The cost-to-risk gate above bounds the LOSS side;
        # this one bounds the WIN side, which is not the same test when T1 sits
        # close in. Measured: a NATURALGAS plan carried T1 0.2 pts from entry
        # against ~2.1 pts of round-trip cost, so reaching the target still
        # booked -Rs2,580. A target inside its own cost cannot be profitable.
        t1_pts = abs(t1 - fill)
        min_mult = max(0.0, settings.futures_min_t1_cost_multiple)
        if min_mult > 0 and t1_pts < min_mult * cost_pts:
            return FuturesSignal(
                enabled=True,
                state="BLOCKED",
                side=side,
                price=round(price, 1),
                stop=stop,
                target1=t1,
                target2=t2,
                target3=t3,
                risk_points=round(stop_pts, 1),
                reason=(
                    f"{side} setup skipped: Target 1 is {t1_pts:.2f} pts away against "
                    f"{cost_pts:.2f} pts of round-trip cost — the target is inside "
                    f"{min_mult:g}x its own cost, so even hitting it would not pay"
                ),
                paper_only=True,
            )

        pos = _Position(instrument, side, fill, lots, stop, t1, t2, t3, int(now), why)
        store.set(instrument, pos)
        guard.record_entry(instrument, int(now))
        return FuturesSignal(
            enabled=True,
            state="ENTERED",
            side=side,
            entry=round(fill, 1),
            price=round(price, 1),
            stop=stop,
            target1=t1,
            target2=t2,
            target3=t3,
            lots=lots,
            lot_size=pos.lot_size,
            risk_points=round(stop_pts, 1),
            risk_rupees=round(stop_pts * pos.lot_size * lots, 0),
            reason=f"{side} (paper) — {why}",
            sizing_note=note,
            paper_only=True,
        )
    except Exception as exc:  # never break a live tick
        return FuturesSignal(
            enabled=True,
            state="ERROR",
            reason=f"futures tool skipped this tick ({type(exc).__name__})",
            paper_only=True,
        )
