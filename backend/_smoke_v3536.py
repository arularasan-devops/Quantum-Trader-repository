"""Smoke v3.5.36: the strategy gate accepts a BUY when ANY satisfied trigger is
allowed (a PULLBACK+MOMENTUM tick is a trend buy), and Angel history covers 96h.
Simulated engine; provider.place_order is a tripwire, nothing reaches a broker."""
import os
import tempfile
import time

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("QT_TRADE_MODE", "paper")

from app.analysis import exec_funnel  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import Decision, OptionType, Signal  # noqa: E402
from app.state import AppState  # noqa: E402

FAIL: list[str] = []
BROKER: list = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    if not ok:
        FAIL.append(name)


def fresh():
    eng = AppState(instrument="CRUDEOIL")
    settings.auto_trade_enabled = False
    eng.tick()
    settings.auto_trade_enabled = True
    eng.provider.place_order = lambda *a, **k: BROKER.append(a)
    eng._last_market_status = "TRENDING"
    ok = [o for o in eng.provider.option_chain() if o.premium >= settings.auto_trade_min_premium * 1.5]
    pick = min(ok, key=lambda o: o.premium)
    return eng, pick.symbol, pick.premium


def buy(sym, prem, trig, trigs):
    return Decision(
        signal=Signal.BUY, confidence=90.0, signal_strength=90.0, trade_quality="A",
        recommended_option=sym, option_type=OptionType.CALL, current_premium=prem,
        spot_price=7900.0, stop_loss=prem * 0.92, target1=prem * 1.11, target2=prem * 1.2,
        target3=prem * 1.3, entry_trigger=trig, entry_triggers=trigs, premium_momentum=1.0,
        buy_trap_prob=0.0, premium_health="HEALTHY", reasons=["smoke"],
    )


def attempt(trig, trigs):
    eng, sym, prem = fresh()
    eng._auto_trade(buy(sym, prem, trig, trigs), False, int(time.time()))
    b = exec_funnel.last_block(eng.instrument, since=time.time() - 5, option=sym) or {}
    return bool(eng.position.option_symbol), b.get("primary_blocker") or ""


settings.trade_mode = "paper"
settings.auto_trade_min_confidence = 80.0
settings.auto_trade_strategy_gate = True
settings.auto_trade_allowed_triggers = ["SUPERTREND", "IGNITION", "BREAKOUT_RETEST", "MOMENTUM"]
settings.auto_trade_allowed_regimes = ["TRENDING"]
settings.opportunity_gate_enabled = False
settings.auto_trade_max_concurrent = 1000
settings.blocked_entry_hours = {}
settings.instrument_allowlist = []
settings.auto_trade_no_entry_open_minutes = 0
settings.capital = 500000.0

for trig, trigs, want in (
    ("PULLBACK", ["PULLBACK", "MOMENTUM"], True),
    ("PULLBACK", ["PULLBACK", "BREAKOUT_RETEST"], True),
    ("MOMENTUM", ["MOMENTUM"], True),
    ("PULLBACK", ["PULLBACK"], False),
    ("WAIT_SETUP", [], False),
    ("REVERSAL", ["REVERSAL"], False),
):
    took, why = attempt(trig, trigs)
    if want:
        check(f"{trig}{trigs} -> past the strategy gate", why != "STRATEGY_TRIGGER", why)
    else:
        check(f"{trig}{trigs} -> STRATEGY_TRIGGER", not took and why == "STRATEGY_TRIGGER", why)

eng, _, _ = fresh()
for _ in range(3):
    eng.tick()
d = eng._committed
check("committed decision carries entry_triggers list", d is not None and isinstance(d.entry_triggers, list))
check("entry_trigger (if a real trigger) is in entry_triggers",
      d is None or d.entry_trigger not in {"SUPERTREND", "IGNITION", "PULLBACK", "BREAKOUT_RETEST",
                                           "MOMENTUM", "PATTERN", "REVERSAL"}
      or d.entry_trigger in d.entry_triggers, f"{d.entry_trigger} {d.entry_triggers}")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app/market/angelone.py")).read()
check("Angel candle window 96h (2 places)", src.count("_ist_window(96)") == 2 and "_ist_window(48)" not in src)
check("no broker calls", not BROKER, str(BROKER))
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
