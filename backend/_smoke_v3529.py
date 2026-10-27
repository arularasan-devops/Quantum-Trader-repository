"""Smoke v3.5.29: no-progress exit. A pre-T1 auto trade whose trail never armed
is sold at the bid after auto_trade_no_progress_exit_min; off at 0, never fires
once break-even is armed or before the time. Paper/simulated feed only.
    cd backend && python _smoke_v3529.py
"""
import os
import tempfile
import time
from types import SimpleNamespace

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_v3529_")
os.environ.pop("QT_DB_URL", None)
os.environ["QT_DATA_PROVIDER"] = "simulated"
from app.config import settings  # noqa: E402
from app.state import AppState  # noqa: E402

passed = failed = 0


def ok(cond, msg):
    global passed, failed
    passed, failed = passed + bool(cond), failed + (not cond)
    print(("PASS " if cond else "FAIL ") + msg)


ok(settings.auto_trade_no_progress_exit_min == 0.0, "default is off (0)")
settings.trade_mode = "paper"
settings.ignore_market_hours = True
settings.auto_trade_enabled = True
st = AppState(instrument="CRUDEOIL")
st.tick()


def open_trade(age_min, stop_frac=0.5):
    opt = st.provider.option_chain()[0]
    r = st.buy(opt.symbol, lots=2, manual=False)
    assert r.ok, r.message
    st._auto_entry_active = True
    st._auto_entry_prem = st.position.entry_premium
    st._auto_entry_lots = 2
    st._auto_stop = st.position.entry_premium * stop_frac
    st._auto_tgt_floor = None
    st._auto_peak = None
    st._entry_time = int(time.time() - age_min * 60)
    return opt.symbol


def run():
    st._auto_trade(SimpleNamespace(), True, int(time.time()))
    return st.position.option_symbol


settings.auto_trade_no_progress_exit_min = 0.0
open_trade(45)
ok(run() is not None, "off: a 45-min unarmed trade is kept")
settings.auto_trade_no_progress_exit_min = 30.0
ok(run() is None, "30 min: a 45-min unarmed trade is sold")
ok("no progress in 30 min" in (st._auto_last_action or ""), "action names the no-progress exit")
open_trade(10)
ok(run() is not None, "30 min: a 10-min trade is kept")
st.sell(lots=st.position.quantity_lots)
open_trade(45, stop_frac=1.0)
st._auto_last_action = ""
run()
ok("no progress" not in (st._auto_last_action or ""),
   "armed (stop at break-even): no-progress exit does not fire")
if st.position.option_symbol:
    st.sell(lots=st.position.quantity_lots)
print(f"{passed} passed, {failed} failed")
