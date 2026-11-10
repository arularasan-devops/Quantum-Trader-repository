"""Smoke v3.5.38: volatility stop/targets from the option 1-min ATR, MCX profit
hold (+10 arms, floor +5, 5-pt giveback), re-level of an open trade.
Simulated engine; provider.place_order is a tripwire, nothing reaches a broker."""
import os
import tempfile
import time

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())

from app import storage  # noqa: E402
from app.analysis import exec_funnel  # noqa: E402
from app.config import settings  # noqa: E402
from app.engine import account_risk  # noqa: E402
from app.market.instruments import REGISTRY, get_spec  # noqa: E402
from app.models import Decision, OptionType, OrderResult, Signal  # noqa: E402
from app.state import AppState  # noqa: E402

FAIL: list[str] = []
BROKER: list = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    if not ok:
        FAIL.append(name)


def fresh():
    account_risk.account.reset()
    eng = AppState(instrument="CRUDEOIL")
    settings.auto_trade_enabled = False
    eng.tick()
    settings.auto_trade_enabled = True
    eng.provider.place_order = lambda *a, **k: BROKER.append(a)
    eng._own_bid = lambda: None
    eng._last_market_status = "TRENDING"
    chain = [o for o in eng.provider.option_chain() if 100 <= o.premium <= 400] or \
        [o for o in eng.provider.option_chain() if o.premium >= 60]
    pick = min(chain, key=lambda o: abs(o.premium - 200))
    return eng, pick.symbol, pick.premium


def dec(sym, prem):
    return Decision(
        signal=Signal.BUY, confidence=88.0, signal_strength=88.0, trade_quality="A",
        recommended_option=sym, option_type=OptionType.CALL, current_premium=prem,
        spot_price=7900.0, stop_loss=prem * 0.92, target1=prem + 30, target2=prem + 40,
        target3=prem + 50, entry_trigger="MOMENTUM", entry_triggers=["MOMENTUM"],
        premium_momentum=1.0, buy_trap_prob=0.0, premium_health="HEALTHY", reasons=["smoke"],
    )


def step(eng, d, px):
    eng._mark_position(round(px, 2))
    eng._auto_trade(d, True, int(time.time()))


def last_row():
    return storage.store._db.execute(
        "SELECT exit_reason, entry, exit, net_pnl FROM trade_journal ORDER BY id DESC LIMIT 1"
    ).fetchone()


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
settings.auto_trade_no_progress_exit_min = 0
settings.auto_trade_stop_points = {}
settings.capital = 500000.0
settings.auto_trade_max_lots = 1
settings.auto_trade_capital_sizing = False
settings.auto_trade_risk_balanced_lots = False
settings.auto_trade_safe_sizing = False
settings.auto_trade_instrument_lots = {}
settings.mcx_hold_arm_points = 10.0
settings.vol_levels_enabled = True

from app.engine.decision import vol_levels  # noqa: E402

# 1. volatility levels
lv = vol_levels(474.0, 4.0, "MCX", 100)
check("ATR 4 on 474: stop at 6% floor, T1 +12", lv and abs(lv[0] - 28.44) < 0.01 and lv[1] == 12.0, lv)
lv = vol_levels(474.0, 40.0, "MCX", 100)
check("ATR 40: stop capped at 12%", lv and abs(lv[0] - 56.88) < 0.01, lv)
lv = vol_levels(474.0, 2.0, "MCX", 100)
check("MCX T1 at least +10, T1<T2<T3", lv and lv[1] == 10.0 and lv[1] < lv[2] < lv[3], lv)
lv = vol_levels(20.0, 2.0, "MCX", 1250)
check("big-lot MCX: no +10 minimum", lv and lv[1] == 6.0, lv)
check("no ATR -> old model", vol_levels(474.0, None) is None)

# 2. engine tick with an option ATR wired in does not break
eng, sym, prem = fresh()
eng._option_atr = lambda s: 4.0
try:
    eng.tick()
    check("tick with volatility levels", True)
except Exception as exc:  # noqa: BLE001
    check("tick with volatility levels", False, repr(exc))

# 3. MCX profit hold
eng, sym, prem = fresh()
check("CRUDEOIL uses MCX hold", eng._mcx_hold())
d = dec(sym, prem)
eng._auto_trade(d, False, int(time.time()))
check("entered 1 lot", eng.position.quantity_lots == 1, eng._auto_last_action or "")
entry = eng._auto_entry_prem or eng.position.entry_premium
be = entry + eng._exit_cost_per_unit(entry)
stop0 = eng._auto_stop
step(eng, d, be + 8.0)
check("+8: nothing locked, stop unchanged", eng.position.option_symbol and eng._auto_stop == stop0,
      f"{eng._auto_stop} vs {stop0}")
step(eng, d, be + 6.0)
check("+8 -> +6: still open (small profit left to run)", bool(eng.position.option_symbol))
step(eng, d, be + 12.0)
check("+12: floor locked at entry+charges+5", abs((eng._auto_stop or 0) - (be + 5.0)) < 0.01,
      f"{eng._auto_stop} vs {be + 5.0:.2f}")
step(eng, d, be + 7.1)
check("gives back <5 pts: still open", bool(eng.position.option_symbol))
step(eng, d, be + 6.9)
row = last_row()
check("gives back 5 pts -> exit, net positive", not eng.position.option_symbol and row
      and str(row[0]).startswith("PRE-T1") and row[3] > 0, str(row))

# 4. re-level an open trade from volatility (no order)
eng, sym, prem = fresh()
d = dec(sym, prem)
eng._auto_trade(d, False, int(time.time()))
entry = eng._auto_entry_prem or eng.position.entry_premium
eng._option_atr = lambda s: 4.0
n0 = len(BROKER)
r = eng.relevel_auto(use_vol=True)
exp = vol_levels(entry, 4.0, "MCX", 100)
check("re-level from volatility", r["ok"] and abs(eng._auto_stop - round(entry - exp[0], 1)) < 0.06
      and abs(eng._auto_target1 - round(entry + exp[1], 1)) < 0.06, r)
r = eng.relevel_auto(stop=entry + 50)
check("stop above price refused", not r["ok"] and "immediately" in r["message"], r)
check("position still open, no order", bool(eng.position.option_symbol) and len(BROKER) == n0)

check("no broker calls", not BROKER, str(BROKER))
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
