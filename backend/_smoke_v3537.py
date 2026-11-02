"""Smoke v3.5.37: pre-T1 point profit lock (+5 pts after charges, then the
pre-T1 peak trail, never below the lock), charge-aware break-even, and a
traded-and-closed signal episode re-arming after the re-entry cooldown.
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

# 1. lock points by contract size
eng, sym, prem = fresh()
check("CRUDEOIL locks 5 points", eng._pre_t1_lock_points() == 5.0, eng._pre_t1_lock_points())
big = next((k for k in REGISTRY if int(get_spec(k).lot_size or 0) >= 300), None)
if big:
    e2 = AppState(instrument=big)
    check(f"{big} (lot {get_spec(big).lot_size}) locks 1 point", e2._pre_t1_lock_points() == 1.0)
settings.auto_trade_pre_t1_lock_points_by_instrument = {"CRUDEOIL": 7.0}
check("per-instrument override wins", eng._pre_t1_lock_points() == 7.0)
settings.auto_trade_pre_t1_lock_points_by_instrument = {}

# 2. +5 lock then exit at the lock, net positive
d = dec(sym, prem)
eng._auto_trade(d, False, int(time.time()))
check("entered 1 lot", eng.position.quantity_lots == 1, eng._auto_last_action or "")
entry = eng._auto_entry_prem or eng.position.entry_premium
cpu = eng._exit_cost_per_unit(entry)
check("charges per unit > 0", cpu > 0, cpu)
stop0 = eng._auto_stop
step(eng, d, entry + 3.0)
check("+3 pts: no lock yet, stop unchanged", eng.position.option_symbol and eng._auto_stop == stop0,
      f"{eng._auto_stop} vs {stop0}")
lock_px = entry + cpu + 5.0
step(eng, d, lock_px + 0.5)
check("+5 after charges: stop locked at entry+charges+5",
      eng.position.option_symbol and abs((eng._auto_stop or 0) - lock_px) < 0.01,
      f"stop {eng._auto_stop} lock {lock_px:.2f}")
step(eng, d, lock_px - 0.05)
row = last_row()
check("falls back to the lock -> PRE-T1 PROFIT LOCK exit", not eng.position.option_symbol
      and row and row[0] == "PRE-T1 PROFIT LOCK", str(row))
check("profit-lock exit is net positive after charges", row and row[3] > 0, str(row))

# 3. episode: blocked during cooldown, re-armed after it
eng._auto_trade(d, False, int(time.time()))
b = exec_funnel.last_block(eng.instrument, since=time.time() - 5, option=sym) or {}
check("same episode blocked during cooldown, says re-arms",
      not eng.position.option_symbol and "re-arms" in str(b.get("reason") or b), str(b))
shift = int(settings.auto_trade_reentry_cooldown_sec) + 5
eng._auto_last_exit_ts -= shift
eng._auto_key_traded_ts -= shift
account_risk.account.reset()
eng._auto_trade(d, False, int(time.time()))
check("same episode bought again after the cooldown", bool(eng.position.option_symbol),
      str(exec_funnel.last_block(eng.instrument, since=time.time() - 5, option=sym)))
if not eng.position.option_symbol:
    raise SystemExit(1)

# 4. peak trail above the lock
entry = eng._auto_entry_prem or eng.position.entry_premium
step(eng, d, entry + 20.0)
trail_px = entry + 20.0 - max(entry * 0.008, 20.0 * 0.30)
step(eng, d, trail_px + 0.3)
check("above the trail: still open", bool(eng.position.option_symbol))
step(eng, d, trail_px - 0.1)
row = last_row()
check("gives back 30% of the run -> PRE-T1 PEAK TRAIL", not eng.position.option_symbol
      and row and row[0] == "PRE-T1 PEAK TRAIL" and row[3] > 0, str(row))

# 5. a failed / timed-out order keeps the episode consumed
eng, sym, prem = fresh()
eng.buy = lambda *a, **k: OrderResult(ok=False, mode="paper", side="BUY", option_symbol=sym,
                                      lots=1, message="timed out")
d = dec(sym, prem)
eng._auto_trade(d, False, int(time.time()))
check("failed buy: episode stays consumed (no blind retry)",
      eng._auto_last_key is not None and eng._episode_rearm_wait(time.time()) is None)

check("no broker calls", not BROKER, str(BROKER))
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
