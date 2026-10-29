"""Smoke v3.5.32: LIVE single-stock option hold-to-T2 (auto_trade_stock_hold_to_target).
Off by default; only live stock options (not MCX/index); no stop/no-progress exit; sells at T2,
or at the optional rupee loss limit. Simulated feed, no broker.
    cd backend && python _smoke_v3532.py
"""
import os
import tempfile
import time
from types import SimpleNamespace

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_v3532_")
os.environ.pop("QT_DB_URL", None)
os.environ["QT_DATA_PROVIDER"] = "simulated"
from app.config import settings  # noqa: E402
from app.state import AppState  # noqa: E402

passed = failed = 0


def ok(cond, msg):
    global passed, failed
    passed, failed = passed + bool(cond), failed + (not cond)
    print(("PASS " if cond else "FAIL ") + msg)


ok(settings.auto_trade_stock_hold_to_target is False, "default off")
ok(settings.auto_trade_stock_hold_max_loss_rupees == 0.0, "loss limit default off")
settings.trade_mode = "paper"
settings.ignore_market_hours = True
settings.auto_trade_enabled = True
st = AppState(instrument="RELIANCE")
st.tick()
ok(not st._stock_hold_active(True), "off: live RELIANCE not under the rule")
settings.auto_trade_stock_hold_to_target = True
ok(st._stock_hold_active(True), "on: live RELIANCE under the rule")
ok(not st._stock_hold_active(False), "on: paper RELIANCE unchanged")
for k in ("CRUDEOIL", "NIFTY", "BANKNIFTY"):
    ok(not AppState(instrument=k)._stock_hold_active(True), f"on: live {k} unchanged")

st._stock_hold_active = lambda live: True
settings.auto_trade_no_progress_exit_min = 30.0


def open_trade():
    opt = st.provider.option_chain()[0]
    r = st.buy(opt.symbol, lots=1, manual=False)
    assert r.ok, r.message
    e = st.position.entry_premium
    st._auto_entry_active = True
    st._auto_entry_prem = e
    st._auto_entry_lots = 1
    st._auto_stop = e * 5          # far above price: a normal trade would stop out
    st._auto_target1 = e * 3
    st._auto_target2 = e * 4
    st._auto_tgt_floor = None
    st._auto_peak = None
    st._entry_time = int(time.time() - 45 * 60)
    return e


def run():
    st._auto_trade(SimpleNamespace(), True, int(time.time()))
    return st.position.option_symbol


e = open_trade()
ok(run() is not None, "no stop and no 30-min exit: position kept")
st._auto_target2 = e * 0.5
ok(run() is None, "T2 reached: sold")
ok("STOCK HOLD TARGET" in (st._auto_last_action or ""), "exit named STOCK HOLD TARGET")
open_trade()
st._auto_target2 = None
st._auto_target1 = e * 0.5
ok(run() is None and "STOCK HOLD TARGET" in (st._auto_last_action or ""), "no T2: sells at T1")
e = open_trade()
settings.auto_trade_stock_hold_max_loss_rupees = 1.0
st._auto_entry_prem = e * 3
ok(run() is None and "STOCK HOLD MAX LOSS" in (st._auto_last_action or ""), "loss limit sells when set")
print(f"{passed} passed, {failed} failed")
