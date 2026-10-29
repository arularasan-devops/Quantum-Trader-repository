"""Smoke v3.5.33: LIVE stock-option T2 holds carried overnight (auto_trade_stock_hold_carry_forward).
Off by default; CARRYFORWARD product only for live stock-option holds; kept past the square-off
until near expiry; targets survive a restart + adopt. Simulated feed, no broker.
    cd backend && python _smoke_v3533.py
"""
import datetime as dt
import inspect
import os
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_v3533_")
os.environ.pop("QT_DB_URL", None)
os.environ["QT_DATA_PROVIDER"] = "simulated"
from app.config import settings  # noqa: E402
from app.market.angelone import AngelOneProvider  # noqa: E402
from app.state import AppState  # noqa: E402

passed = failed = 0


def ok(cond, msg):
    global passed, failed
    passed, failed = passed + bool(cond), failed + (not cond)
    print(("PASS " if cond else "FAIL ") + msg)


def ist(y, m, d):
    return dt.datetime(y, m, d, 15, 5, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp()


ok(settings.auto_trade_stock_hold_carry_forward is False, "default off")
ok(settings.auto_trade_stock_hold_exit_days_before_expiry == 3, "expiry buffer default 3 days")
ok("product" in inspect.signature(AngelOneProvider.place_order).parameters, "Angel order takes a product")
settings.trade_mode = "paper"
settings.ignore_market_hours = True
st = AppState(instrument="RELIANCE")
mcx = AppState(instrument="CRUDEOIL")
settings.auto_trade_stock_hold_to_target = True
ok(st._buy_product() is None, "carry off: default product")
settings.auto_trade_stock_hold_carry_forward = True
ok(st._buy_product() == "CARRYFORWARD", "carry on: stock buy is CARRYFORWARD")
ok(mcx._buy_product() is None, "carry on: MCX keeps the default product")

sym = "RELIANCE23NOV261180PE"
st._ledger_position = {"symbol": sym, "product": "CARRYFORWARD"}
ok(st._carry_keep(sym, ist(2026, 10, 29)), "25 days to expiry: kept past square-off")
ok(not st._carry_keep(sym, ist(2026, 11, 21)), "2 days to expiry: squared off")
ok(not st._carry_keep("RELIANCEX", ist(2026, 10, 29)), "unknown expiry: squared off")
st._ledger_position = {"symbol": sym}
ok(not st._carry_keep(sym, ist(2026, 10, 29)), "intraday-bought position: squared off")
st._ledger_position = {"symbol": sym, "product": "CARRYFORWARD"}
settings.auto_trade_stock_hold_carry_forward = False
ok(not st._carry_keep(sym, ist(2026, 10, 29)), "switch off: squared off")

st.tick()
opt = st.provider.option_chain()[0].symbol
st._order_recovery = {"reason": "restart", "since": 0}
st._ledger_position = {"symbol": opt, "lots": 1, "entry": 10.0, "ts": 0, "t1": 11.0, "t2": 12.0,
                       "product": "CARRYFORWARD", "verified": False}
st.resolve_recovery("adopt_position", "checked Angel positions")
ok(st._auto_target2 == 12.0 and st._auto_target1 == 11.0, "adopt after restart restores T1/T2")
ok(st._auto_entry_active and st._auto_entry_prem == 10.0, "adopted hold is managed by the bot")
ok((st._ledger_position or {}).get("product") == "CARRYFORWARD", "product kept for the SELL")
print(f"{passed} passed, {failed} failed")
