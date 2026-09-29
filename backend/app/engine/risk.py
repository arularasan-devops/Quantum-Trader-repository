"""Risk management and position sizing.

Protects capital independently of signal quality: caps daily loss, number of
trades, and consecutive losses; advises an intraday square-off time; and sizes
positions from a fixed fractional-risk rule. This is what turns "good signals"
into "protected capital" — the engine can be wrong; the risk layer bounds how
much a wrong call costs.

The COUNTERS are not held here. Each instrument owns a ``RiskManager``, so
keeping the day's loss and trade count in the instance made every cap
per-instrument: a Rs5,000 daily cap became Rs5,000 x seven instruments, and the
book lost Rs16,834 in a session with the cap enabled and never tripped. They now
live in one shared, disk-persisted governor (:mod:`app.engine.account_risk`);
this class is the per-instrument view of it, so sizing and the status card keep
working unchanged.
"""
from __future__ import annotations

import datetime as dt
import time

from app.config import settings
from app.engine.account_risk import account
from app.models import RiskStatus


class RiskManager:
    def __init__(self, instrument: str = "") -> None:
        self.instrument = instrument

    # The counters read through to the shared account governor, so every
    # instrument sees the same pot rather than its own private allowance.
    @property
    def trades_today(self) -> int:
        return account.trades_today

    @property
    def consecutive_losses(self) -> int:
        return account.consecutive_losses

    @property
    def day_pnl(self) -> float:
        return account.day_pnl

    def _roll_day(self, now_utc: float) -> None:
        account.roll_day(now_utc)

    def record_exit(self, net_pnl: float, *, stopped: bool = False) -> None:
        account.record_exit(self.instrument, net_pnl, time.time(), stopped=stopped)

    def record_entry(self) -> None:
        account.record_entry(self.instrument, time.time())

    def _past_squareoff(self, now_utc: float) -> bool:
        ist = dt.datetime.utcfromtimestamp(now_utc) + dt.timedelta(hours=5, minutes=30)
        try:
            h, m = (int(x) for x in settings.auto_squareoff_ist.split(":"))
        except ValueError:
            return False
        return (ist.hour * 60 + ist.minute) >= (h * 60 + m)

    def can_open(self, now_utc: float) -> tuple[bool, str | None]:
        blocked = account.blocked(now_utc)
        if blocked:
            return False, blocked
        if self._past_squareoff(now_utc):
            return False, f"Past square-off time ({settings.auto_squareoff_ist} IST)"
        return True, None

    def suggested_lots(self, entry: float, stop: float, lot_size: int) -> int:
        risk_amount = settings.capital * settings.risk_per_trade_pct / 100.0
        per_lot_risk = max(0.01, (entry - stop)) * max(1, lot_size)
        return max(1, int(risk_amount // per_lot_risk))

    def status(self, now_utc: float, entry: float, stop: float, lot_size: int) -> RiskStatus:
        self._roll_day(now_utc)
        can, reason = self.can_open(now_utc)
        loss_used = 0.0
        if settings.max_daily_loss > 0 and self.day_pnl < 0:
            loss_used = min(100.0, abs(self.day_pnl) / settings.max_daily_loss * 100.0)
        note = (
            f"Auto square-off {settings.auto_squareoff_ist} IST"
            if not self._past_squareoff(now_utc)
            else f"Past square-off ({settings.auto_squareoff_ist} IST) — flat by now"
        )
        return RiskStatus(
            capital=settings.capital,
            risk_per_trade_pct=settings.risk_per_trade_pct,
            day_pnl=round(self.day_pnl, 1),
            trades_today=self.trades_today,
            trades_left=max(0, settings.max_trades_per_day - self.trades_today),
            consecutive_losses=self.consecutive_losses,
            loss_limit=settings.max_daily_loss,
            loss_used_pct=round(loss_used, 1),
            can_trade=can,
            block_reason=reason,
            suggested_lots=self.suggested_lots(entry, stop, lot_size) if entry > 0 else 1,
            lot_size=max(1, lot_size),
            brokerage_per_lot=settings.brokerage_per_lot,
            squareoff_note=note,
        )
