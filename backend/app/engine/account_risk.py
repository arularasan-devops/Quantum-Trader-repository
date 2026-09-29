"""Account-wide risk governor for the option engine — one pot, persisted.

Why this exists, measured rather than assumed. On 13 Aug the option bot lost
Rs16,834 across 19 trades with a Rs5,000 daily loss cap switched on. The cap did
nothing, for two reasons that were both defects here:

* Every instrument owned its OWN counter, so a "Rs5,000 cap" was really
  Rs5,000 x the number of instruments (seven that day, up to fifty possible).
  Walked account-wide, the day crossed -Rs5,000 at 09:43; the fourteen trades
  taken after that point lost a further Rs10,492.
* The counters lived in memory only. The futures governor persisted its state
  from the start, this one did not, so any restart mid-session handed the bot a
  fresh set of lives: the day's loss, the trade count and the consecutive-loss
  streak all went back to zero.

The rules are deliberately account-level, because capital is account-level: a
loss on Nifty spends exactly the same money as a loss on Crude.

Also here, and separate from the caps, is the OPENING WINDOW block. In the same
session four entries in the first minutes of trading exited far past their 8%
stop — Sensex at -47.8%, Infosys at -22.4%, Nifty at -16.7% — because an option
premium gaps straight through a stop in the opening volatility. Those four cost
Rs6,997 of the day. No stop distance can fix a gap; not being in the position is
the only thing that does.
"""
from __future__ import annotations

import json
import logging
import os
import time as _time

from app.config import settings

_log = logging.getLogger(__name__)

_IST_OFFSET = 19800  # +05:30 in seconds


def _path() -> str:
    return os.path.join(settings.data_dir, "risk_guard.json")


def ist_minutes(now: float) -> int:
    """Minutes since IST midnight for a UTC timestamp."""
    st = _time.gmtime(int(now) + _IST_OFFSET)
    return st.tm_hour * 60 + st.tm_min


def session_open_min(exchange: str) -> int:
    """Minutes since IST midnight at which this instrument's session opens."""
    raw = settings.market_open_ist if (exchange or "").upper() == "MCX" else settings.equity_open_ist
    try:
        h, m = (int(x) for x in raw.split(":"))
    except ValueError:
        return 0
    return h * 60 + m


def _today(now: float) -> str:
    return _time.strftime("%Y-%m-%d", _time.gmtime(int(now) + _IST_OFFSET))


class AccountRisk:
    """Shared, disk-persisted daily risk state for the whole option book."""

    def __init__(self) -> None:
        self.day: str = ""
        self.day_pnl: float = 0.0
        self.trades_today: int = 0
        self.consecutive_losses: int = 0
        # Instrument -> when its last position was closed, and whether that exit
        # was a stop. A stop is treated more strictly than a target: the view
        # that produced it has just been proven wrong.
        self.last_exit: dict[str, float] = {}
        self.last_stop: dict[str, float] = {}
        self.stand_down_until: float = 0.0
        self._loaded = False

    # --- persistence ------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(_path(), encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self.day = str(raw.get("day") or "")
        self.day_pnl = float(raw.get("day_pnl") or 0.0)
        self.trades_today = int(raw.get("trades_today") or 0)
        self.consecutive_losses = int(raw.get("consecutive_losses") or 0)
        self.stand_down_until = float(raw.get("stand_down_until") or 0.0)
        self.last_exit = {str(k): float(v) for k, v in (raw.get("last_exit") or {}).items()}
        self.last_stop = {str(k): float(v) for k, v in (raw.get("last_stop") or {}).items()}

    def _save(self) -> None:
        payload = {
            "day": self.day,
            "day_pnl": self.day_pnl,
            "trades_today": self.trades_today,
            "consecutive_losses": self.consecutive_losses,
            "stand_down_until": self.stand_down_until,
            "last_exit": self.last_exit,
            "last_stop": self.last_stop,
        }
        try:
            os.makedirs(settings.data_dir, exist_ok=True)
            tmp = _path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, _path())
        except OSError:
            _log.warning("could not persist the account risk governor", exc_info=True)

    def roll_day(self, now: float) -> None:
        """Load persisted state and roll the counters if the IST day has changed.

        Public because callers outside this module (the per-instrument
        RiskManager) need the day rolled before they read the counters.
        """
        self._roll(now)

    def _roll(self, now: float) -> None:
        self._load()
        today = _today(now)
        if self.day == today:
            return
        self.day = today
        self.day_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.last_exit.clear()
        self.last_stop.clear()
        self.stand_down_until = 0.0
        self._save()

    # --- recording --------------------------------------------------------
    def record_entry(self, instrument: str, now: float) -> None:
        self._roll(now)
        self.trades_today += 1
        self._save()

    def record_exit(
        self, instrument: str, net_pnl: float, now: float, *, stopped: bool = False
    ) -> None:
        self._roll(now)
        self.day_pnl += net_pnl
        self.last_exit[instrument] = now
        if stopped:
            self.last_stop[instrument] = now
        if net_pnl < 0:
            self.consecutive_losses += 1
            limit = max(1, settings.max_consecutive_losses)
            if self.consecutive_losses >= limit and settings.risk_stand_down_sec > 0:
                # A streak stands the whole book down for a while rather than
                # for the rest of the day: three losses in chop is information
                # about the next hour, not about the session.
                self.stand_down_until = now + settings.risk_stand_down_sec
        else:
            self.consecutive_losses = 0
        self._save()

    # --- gates ------------------------------------------------------------
    def blocked(self, now: float) -> str | None:
        """Account-level reason no new option position may be opened, or None."""
        self._roll(now)
        if settings.max_daily_loss > 0 and self.day_pnl <= -abs(settings.max_daily_loss):
            return (
                f"account daily loss cap hit (₹{self.day_pnl:,.0f} of "
                f"₹{settings.max_daily_loss:,.0f}) — flat for the day"
            )
        if settings.daily_profit_target > 0 and self.day_pnl >= settings.daily_profit_target:
            return (
                f"daily profit goal reached (₹{self.day_pnl:,.0f}) — locking gains"
            )
        if settings.max_trades_per_day > 0 and self.trades_today >= settings.max_trades_per_day:
            return f"account trade cap reached ({self.trades_today} today)"
        if self.stand_down_until > now:
            return (
                f"{self.consecutive_losses} losses in a row — standing down for "
                f"{int(self.stand_down_until - now)}s"
            )
        return None

    def instrument_blocked(self, instrument: str, now: float) -> str | None:
        """Per-instrument cooldown, measured from its last exit.

        A stop gets the longer wait. Nine Crude signals between 20:33 and 21:27
        produced five stops at exactly -8% each: the engine kept re-expressing a
        view the market had already refused, on a new strike each time, and the
        five-minute cooldown was short enough to allow it.
        """
        self._roll(now)
        stop_ts = self.last_stop.get(instrument)
        stop_wait = settings.auto_trade_post_stop_cooldown_sec
        if stop_ts is not None and stop_wait > 0 and now - stop_ts < stop_wait:
            return (
                f"stopped out {int((now - stop_ts) / 60)}m ago — "
                f"{int(stop_wait - (now - stop_ts))}s of the post-stop cooldown left"
            )
        exit_ts = self.last_exit.get(instrument)
        cool = settings.auto_trade_reentry_cooldown_sec
        if exit_ts is not None and cool > 0 and now - exit_ts < cool:
            return f"re-entry cooldown ({int(cool - (now - exit_ts))}s left)"
        return None

    def opening_window_block(self, now: float, session_open_ist_min: int) -> str | None:
        """Refuse an entry inside the opening gap window of a session."""
        mins = settings.auto_trade_no_entry_open_minutes
        if mins <= 0:
            return None
        since = ist_minutes(now) - session_open_ist_min
        if 0 <= since < mins:
            return (
                f"first {mins:g} minutes of the session — option premiums gap "
                f"through a stop here (measured: exits at -48%, -22%, -17% "
                f"against an 8% stop)"
            )
        return None

    def snapshot(self, now: float) -> dict:
        self._roll(now)
        return {
            "day": self.day,
            "day_pnl": round(self.day_pnl, 1),
            "daily_loss_cap": settings.max_daily_loss,
            "trades_today": self.trades_today,
            "max_trades_per_day": settings.max_trades_per_day,
            "consecutive_losses": self.consecutive_losses,
            "stand_down_sec_left": max(0, int(self.stand_down_until - now)),
            "blocked": self.blocked(now),
            "account_wide": True,
        }

    def reset(self) -> None:
        """Clear the day (used by tests and the manual 'reset day' control)."""
        self.day = ""
        self.day_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.last_exit.clear()
        self.last_stop.clear()
        self.stand_down_until = 0.0
        self._loaded = True
        self._save()


# One governor for the whole book — the point of the module.
account = AccountRisk()
