"""Smoke v3.5.26: live protections — daily-loss room sizing, streak re-read
against the current limit, live opening window, and manual Angel exits
(broker shows flat) — against a MOCKED broker only. Nothing contacts Angel.
    cd backend && python _smoke_v3526.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_v3526_")
os.environ["QT_LIVE_ARM_TOKEN"] = "v3526-token"
os.environ.pop("QT_DB_URL", None)
os.environ["QT_DATA_PROVIDER"] = "simulated"  # never log in to Angel from a smoke
os.environ.pop("QT_CAPITAL", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.engine import account_risk as ar  # noqa: E402
from app.execution import live_control  # noqa: E402
from app.main import app  # noqa: E402
from app.market.simulated import SimulatedProvider  # noqa: E402
from app.models import OrderResult  # noqa: E402
from app.state import AppState, registry  # noqa: E402

passed = failed = 0
fails: list[str] = []
INST = "CRUDEOIL"
H = live_control.LIVE_TOKEN_HEADER


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        fails.append(msg)
        print(f"FAIL: {msg}")


# ---------- daily-loss room ----------
settings.max_daily_loss = 20000.0
ok(ar.daily_loss_room(-11439.0) == 8561.0, "room left = 20,000 - 11,439 = 8,561")
ok(ar.daily_loss_room(-25000.0) == 0.0, "room never negative")
ok(ar.entry_loss_budget(0.0, -11439.0, per_trade_cap=10000.0, lock=None,
                        daily_room=ar.daily_loss_room(-11439.0)) == 8561.0,
   "loss budget = min(per-trade 10,000, daily room 8,561)")
ok(ar.entry_loss_budget(0.0, 0.0, per_trade_cap=10000.0, lock=None) == 10000.0,
   "without daily_room the budget is unchanged (paper path)")
settings.max_daily_loss = 0.0
ok(ar.daily_loss_room(-5000.0) is None, "cap off -> no daily room limit")
settings.max_daily_loss = 20000.0

# ---------- streak re-read against the current limit ----------
acct = ar.account
acct.reset()
settings.risk_stand_down_sec = 86400.0
settings.max_consecutive_losses = 3
now = 1793080000.0
acct.record_exit("CRUDEOIL", -5338.0, now, stopped=True)
acct.record_exit("SILVER", -6101.0, now + 60, stopped=True)
ok(acct.blocked(now + 120) is None, "2 losses under a limit of 3: not stood down")
settings.max_consecutive_losses = 2
b = acct.blocked(now + 120)
ok(b is not None and "losses in a row" in b, "limit lowered to 2 mid-day: the streak stands down now")
settings.max_consecutive_losses = 3
acct.reset()

# ---------- opening window ----------
open_min = ar.session_open_min("MCX")
ist_0905 = 1793068200.0 - 19800 + (open_min + 5) * 60  # 09:05 IST on a day boundary
ist_0905 -= ist_0905 % 60
day0 = ist_0905 - ((ist_0905 + 19800) % 86400) + (open_min + 5) * 60
ok(acct.opening_window_block(day0, open_min) is not None, "09:05 IST MCX inside the 15-min window")
ok(acct.opening_window_block(day0 + 11 * 60, open_min) is None, "09:16 IST MCX outside it")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "state.py")).read()
ok("if open_block and not eng:" in src, "v3.5.28: pullback-engine entries (paper and live) skip the opening window, as in paper")


# ---------- manual exit in Angel ----------
class MockBroker(SimulatedProvider):
    def __init__(self, base):
        self.__dict__.update(base.__dict__)
        self.placed: list[tuple] = []
        self.statuses: dict[str, OrderResult | None] = {}
        self.pos_row: dict | None = None

    def supports_trading(self) -> bool:
        return True

    def place_order(self, option_symbol, side, lots):
        self.placed.append((option_symbol, side, lots))
        oid = f"OID{len(self.placed)}"
        return OrderResult(ok=True, mode="live", side=side, option_symbol=option_symbol,
                           lots=lots, order_id=oid, status="ACKNOWLEDGED")

    def order_status(self, order_id):
        return self.statuses.get(order_id)

    def broker_position(self, option_symbol):
        return self.pos_row


def check(st):
    st._last_order_poll = 0.0
    st._last_broker_pos_check = 0.0
    st._poll_live_orders()


client = TestClient(app)
settings.order_fill_timeout_sec = 0.3
live_control.set_kill_switch(False, "smoke start")
st = AppState(instrument=INST)
registry._states[INST] = st
st.tick()
mock = MockBroker(st.provider)
st.provider = mock
st._providers[st.instrument] = mock
settings.trade_mode = "live"
ok(st.is_live, "mock broker + trade_mode=live is live")
sym = mock.option_chain()[0].symbol
mock.statuses["OID1"] = OrderResult(ok=True, mode="live", side="BUY", option_symbol=sym,
                                    lots=1, filled_lots=1, fill_premium=815.6,
                                    order_id="OID1", status="FILLED")
r = st.buy(sym, lots=1, confirm=True, manual=False)
ok(r.ok and st.position.option_symbol == sym and st.position.entry_premium == 815.6,
   "live BUY filled at the broker price 815.6")

mock.pos_row = None
check(st); check(st)
ok(not st.recovery_status()["recovery_required"], "unknown broker answer is never read as flat")
mock.pos_row = {"net_qty": 100, "buy_avg": 815.6, "sell_avg": None}
check(st); check(st)
ok(not st.recovery_status()["recovery_required"], "broker still long: no recovery")
mock.pos_row = {"net_qty": 0, "buy_avg": 815.6, "sell_avg": 823.0}
check(st)
ok(not st.recovery_status()["recovery_required"], "one flat reading alone does not flag")
check(st)
rs = st.recovery_status()
ok(rs["recovery_required"] and "closed outside the app" in (rs["reason"] or ""),
   "two flat readings flag a manual Angel exit")
ok(st._live_entry_block() is not None, "new live entries blocked")
n = len(mock.placed)
r = st.sell(confirm=True)
ok(not r.ok and len(mock.placed) == n, "no SELL sent for a position Angel already closed")
ok(any(a.kind == "ORDER_RECOVERY" for a in st.alerts), "recovery alert raised")

r = client.post("/api/order-recovery", headers={H: "v3526-token"},
                json={"instrument": INST, "action": "confirm_flat",
                      "note": "sold manually in Angel; positions flat"})
ok(r.status_code == 200 and not r.json()["recovery_required"], "confirm_flat clears recovery")
ok(st.position.option_symbol is None, "app position closed without any order")
ok(len(mock.placed) == n, "confirm_flat sent nothing to the broker")
j = st.journal[-1] if st.journal else {}
ok(j.get("exit") == 823.0 and j.get("exit_reason") == "MANUAL EXIT (ANGEL)",
   "journal row booked at Angel's sell average 823.0")
ok(not any(a.kind == "ORDER_RECOVERY" for a in st.alerts), "stale recovery alerts removed")
ok(any(a.kind == "ORDER_RECOVERY_CLEARED" for a in st.alerts), "cleared notice shown")

settings.trade_mode = "paper"
print(f"\n{passed} passed, {failed} failed")
if fails:
    print("FAILED:", *fails, sep="\n  ")
sys.exit(1 if failed else 0)
