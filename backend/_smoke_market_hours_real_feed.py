"""A real feed must respect exchange hours even with QT_IGNORE_MARKET_HOURS=true.

After the bell Angel keeps serving frozen quotes; they must never become a
fresh BUY, an auto-entry or a journaled BUY signal. The simulator stays 24/7.
"""
from __future__ import annotations

import datetime as dt
import sys
from types import SimpleNamespace

from app import state as app_state
from app.config import settings
from app.models import Decision, Signal

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def at(hh: int, mm: int) -> float:
    return dt.datetime(2026, 8, 11, hh, mm, tzinfo=IST).timestamp()  # a Tuesday


def buy() -> Decision:
    return Decision(signal=Signal.BUY, market_signal=Signal.BUY, confidence=85.0,
                    signal_strength=80.0, trade_quality="A", reasons=["PULLBACK"])


cls = next(c for c in vars(app_state).values()
           if isinstance(c, type) and hasattr(c, "_block_closed_market"))
block = cls._block_closed_market

prev = (settings.data_provider, settings.ignore_market_hours)
try:
    settings.ignore_market_hours = True
    settings.data_provider = "simulated"
    check("simulator keeps the 24/7 demo", settings.hours_ignored() is True)
    d = buy()
    block(SimpleNamespace(_instrument="NIFTY"), d, at(20, 0))
    check("simulator BUY untouched after hours", d.signal == Signal.BUY)

    settings.data_provider = "angelone"
    check("angelone ignores QT_IGNORE_MARKET_HOURS", settings.hours_ignored() is False)
    d = buy()
    block(SimpleNamespace(_instrument="NIFTY"), d, at(20, 0))
    check("NSE BUY after 15:30 becomes WAIT",
          d.signal == Signal.WAIT and d.market_signal == Signal.WAIT, d.reasons[0])
    check("reason names the closed market", "CLOSED" in d.reasons[0])
    d = buy()
    block(SimpleNamespace(_instrument="NIFTY"), d, at(11, 0))
    check("NSE BUY in session untouched", d.signal == Signal.BUY)
    d = buy()
    block(SimpleNamespace(_instrument="CRUDEOIL"), d, at(20, 0))
    check("MCX BUY at 20:00 untouched (MCX open to 23:30)", d.signal == Signal.BUY)
    d = buy()
    block(SimpleNamespace(_instrument="CRUDEOIL"), d, at(23, 45))
    check("MCX BUY after 23:30 becomes WAIT", d.signal == Signal.WAIT)
finally:
    settings.data_provider, settings.ignore_market_hours = prev

print("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
