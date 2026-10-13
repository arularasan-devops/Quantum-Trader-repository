"""A real feed must respect exchange hours even with QT_IGNORE_MARKET_HOURS=true.

After the bell Angel keeps serving frozen quotes; they must never become a
fresh BUY, an auto-entry or a journaled BUY signal. The simulator stays 24/7.
"""
from __future__ import annotations

import datetime as dt
import sys

from app import state as app_state
from app.config import settings
from app.models import Decision, Signal

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def at(hh: int, mm: int, ss: int = 0) -> float:
    return dt.datetime(2026, 8, 11, hh, mm, ss, tzinfo=IST).timestamp()  # a Tuesday


def buy() -> Decision:
    return Decision(signal=Signal.BUY, market_signal=Signal.BUY, confidence=85.0,
                    signal_strength=80.0, trade_quality="A", reasons=["PULLBACK"])


cls = next(c for c in vars(app_state).values()
           if isinstance(c, type) and hasattr(c, "_block_closed_market"))


def mk(instrument: str):
    obj = cls.__new__(cls)
    obj._instrument = instrument
    return obj


prev = (settings.data_provider, settings.ignore_market_hours)
try:
    settings.ignore_market_hours = True
    settings.data_provider = "simulated"
    check("simulator keeps the 24/7 demo", settings.hours_ignored() is True)
    d = buy()
    mk("NIFTY")._block_closed_market(d, at(20, 0), False)
    check("simulator BUY untouched after hours", d.signal == Signal.BUY)

    settings.data_provider = "angelone"
    check("angelone ignores QT_IGNORE_MARKET_HOURS", settings.hours_ignored() is False)
    d = buy()
    mk("NIFTY")._block_closed_market(d, at(20, 0), False)
    check("NSE BUY after 15:30 becomes WAIT",
          d.signal == Signal.WAIT and d.market_signal == Signal.WAIT, d.reasons[0])
    check("reason names the closed market", "CLOSED" in d.reasons[0])
    d = buy()
    mk("NIFTY")._block_closed_market(d, at(11, 0), False)
    check("NSE BUY in session untouched", d.signal == Signal.BUY)
    d = buy()
    mk("CRUDEOIL")._block_closed_market(d, at(20, 0), False)
    check("MCX BUY at 20:00 untouched (MCX open to 23:30)", d.signal == Signal.BUY)
    d = buy()
    mk("CRUDEOIL")._block_closed_market(d, at(23, 45), False)
    check("MCX BUY after 23:30 becomes WAIT", d.signal == Signal.WAIT)
    d = buy()
    mk("NIFTY")._block_closed_market(d, at(15, 30, 40), False)
    check("NSE BUY during the 15:30 bell minute becomes WAIT", d.signal == Signal.WAIT)
    d = buy()
    mk("NIFTY")._block_closed_market(d, at(15, 29, 59), False)
    check("NSE BUY at 15:29:59 untouched", d.signal == Signal.BUY)
    d = buy()
    d.signal = Signal.HOLD
    mk("NIFTY")._block_closed_market(d, at(16, 0), True)
    check("held position keeps HOLD, market BUY dropped after close",
          d.signal == Signal.HOLD and d.market_signal == Signal.WAIT)
    check("expiry sleeve entry gate shut after NSE close",
          mk("NIFTY")._session_open(at(16, 0))[0] is False)
    check("expiry sleeve entry gate open in session",
          mk("NIFTY")._session_open(at(11, 0))[0] is True)
finally:
    settings.data_provider, settings.ignore_market_hours = prev

print("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
