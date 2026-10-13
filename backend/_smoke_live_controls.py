"""Smoke: live-arming auth, kill switch, ₹5L capital source of truth, and the
mocked broker order lifecycle (ack != fill, pending, partial, reject, idempotent).

No real broker is touched: the broker is a mock subclass of the simulator and
the real Angel One order path is asserted to stay hard-blocked.
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_live_ctl_")
os.environ.pop("QT_LIVE_ARM_TOKEN", None)
os.environ.pop("QT_CAPITAL", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.ai import safety  # noqa: E402
from app.config import settings  # noqa: E402
from app.execution import live_control  # noqa: E402
from app.main import app  # noqa: E402
from app.market.simulated import SimulatedProvider  # noqa: E402
from app.models import OrderResult  # noqa: E402
from app.state import AppState  # noqa: E402

passed = failed = 0
fails: list[str] = []


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        fails.append(msg)
        print(f"FAIL: {msg}")


client = TestClient(app)  # no lifespan: no background feed loops
H = live_control.LIVE_TOKEN_HEADER

# ---------------- capital source of truth ----------------
ok(settings.capital == 500000.0, "default strategy capital is ₹5,00,000")
ok(live_control.CONFIGURED_CAPITAL == 500000.0, "configured capital captured at startup")
r = client.post("/api/auto-trade", json={"capital": 900000})
ok(r.status_code == 200 and settings.capital == 500000.0, "API cannot raise capital above ₹5,00,000")
client.post("/api/auto-trade", json={"capital": 200000})
ok(settings.capital == 200000.0, "API may lower capital")
client.post("/api/auto-trade", json={"capital": 500000})
client.post("/api/auto-trade", json={"instrument_capital": {"NIFTY": 10_000_000}})
ok(settings.auto_trade_instrument_capital.get("NIFTY") == 500000.0,
   "per-instrument capital is clamped to strategy capital")
client.post("/api/auto-trade", json={"instrument_capital": {}})

# ---------------- live-arming authorization ----------------
r = client.post("/api/auto-trade", json={"allow_live": True})
ok(r.status_code == 403 and not settings.auto_trade_allow_live,
   "with no QT_LIVE_ARM_TOKEN configured, live arming is refused")
settings.live_arm_token = "s3cret-test-token"
r = client.post("/api/auto-trade", json={"allow_live": True})
ok(r.status_code == 401 and not settings.auto_trade_allow_live, "unauthenticated live arming is refused")
r = client.post("/api/auto-trade", json={"allow_live": True}, headers={H: "wrong"})
ok(r.status_code == 401 and not settings.auto_trade_allow_live, "wrong token is refused")
r = client.post("/api/auto-trade", json={"live_max_order_value": 10_000_000})
ok(r.status_code == 401, "raising live order caps needs the token")
r = client.post("/api/auto-trade", json={"allow_live": True, "live_max_order_value": 10_000_000},
                headers={H: "s3cret-test-token"})
ok(r.status_code == 200 and settings.auto_trade_allow_live, "valid token arms live")
ok(settings.auto_trade_live_max_order_value <= settings.capital,
   "live per-order ceiling never exceeds strategy capital")
r = client.post("/api/auto-buy", json={"on": True})
ok(r.status_code == 401, "one-click live auto-buy needs the token")
lots_before = settings.auto_trade_live_max_lots
from app.market import angelone  # noqa: E402

angelone.available_cash = lambda ttl=30.0: 9_000_000.0
r = client.post("/api/auto-buy", json={"on": True}, headers={H: "s3cret-test-token"})
ok(r.status_code == 200, "authorised auto-buy is accepted")
ok(settings.auto_trade_live_max_lots == lots_before, "auto-buy no longer silently raises live lots to 50")
ok(settings.auto_trade_live_max_order_value <= settings.capital,
   "a ₹90L broker balance does not raise the order ceiling above capital")
client.post("/api/auto-buy", json={"on": False})
settings.auto_trade_allow_live = False

# ---------------- kill switch ----------------
r = client.post("/api/kill-switch", json={"engaged": True, "reason": "smoke"})
ok(r.status_code == 200 and r.json()["engaged"], "engaging the kill switch needs no token")
ok(not settings.auto_trade_enabled and not settings.auto_trade_allow_live,
   "engaging disables the bot and disarms live")
r = client.post("/api/auto-trade", json={"enabled": True})
ok(r.status_code == 409 and not settings.auto_trade_enabled, "bot cannot be re-enabled while engaged")
r = client.post("/api/kill-switch", json={"engaged": False})
ok(r.status_code == 401 and live_control.kill_switch_engaged(), "releasing needs the token")
live_control._reset_for_tests()
ok(live_control.kill_switch_engaged(), "kill switch survives a restart (persisted)")


# ---------------- mocked broker lifecycle ----------------
class MockBroker(SimulatedProvider):
    def __init__(self, base):
        self.__dict__.update(base.__dict__)
        self.placed: list[tuple] = []
        self.next_place = None
        self.statuses: dict[str, OrderResult] = {}

    def supports_trading(self) -> bool:
        return True

    def place_order(self, option_symbol, side, lots):
        self.placed.append((option_symbol, side, lots))
        oid = f"OID{len(self.placed)}"
        if self.next_place == "reject":
            return OrderResult(ok=False, mode="live", side=side, option_symbol=option_symbol,
                               lots=lots, status="REJECTED", message="RMS reject")
        return OrderResult(ok=True, mode="live", side=side, option_symbol=option_symbol,
                           lots=lots, order_id=oid, status="ACKNOWLEDGED")

    def order_status(self, order_id):
        return self.statuses.get(order_id)


settings.order_fill_timeout_sec = 0.3
st = AppState(instrument="CRUDEOIL")
st.tick()
mock = MockBroker(st.provider)
st.provider = mock
st._providers[st.instrument] = mock
opt = mock.option_chain()[0]
settings.trade_mode = "live"
ok(st.is_live, "mock broker + trade_mode=live makes the engine live")

r = st.buy(opt.symbol, lots=1, confirm=True, manual=False)
ok(not r.ok and "Kill switch" in r.message and not mock.placed,
   "kill switch blocks new entries before any order is sent")
client.post("/api/kill-switch", json={"engaged": False}, headers={H: "s3cret-test-token"})
ok(not live_control.kill_switch_engaged(), "authorised release works")

r = st.buy(opt.symbol, lots=2, confirm=True, manual=False)
ok(not r.ok and r.status == "ACKNOWLEDGED", "an ack with no fill does not open a position")
ok(st.position.option_symbol is None, "...position stays flat")
r2 = st.buy(opt.symbol, lots=2, confirm=True, manual=False)
ok(not r2.ok and len(mock.placed) == 1, "repeat signal while pending sends NO duplicate order")
mock.statuses["OID1"] = OrderResult(ok=True, mode="live", side="BUY", option_symbol=opt.symbol,
                                    lots=2, filled_lots=1, fill_premium=12.5, order_id="OID1",
                                    status="PARTIAL")
r3 = st.buy(opt.symbol, lots=2, confirm=True, manual=False)
ok(r3.ok and len(mock.placed) == 1, "a later fill is adopted without a new order")
ok(st.position.quantity_lots == 1 and st.position.entry_premium == 12.5,
   "partial fill: position = filled lots at the broker's average price, not the quote")

r = st.sell(confirm=True)
ok(not r.ok and st.position.option_symbol == opt.symbol, "unfilled exit keeps the position open")
r = st.sell(confirm=True)
ok(len(mock.placed) == 2, "repeat exit while pending sends no duplicate SELL")
mock.statuses["OID2"] = OrderResult(ok=True, mode="live", side="SELL", option_symbol=opt.symbol,
                                    lots=1, filled_lots=1, fill_premium=13.0, order_id="OID2",
                                    status="FILLED")
r = st.sell(confirm=True)
ok(r.ok and st.position.option_symbol is None and len(mock.placed) == 2,
   "exit fill is reconciled and the position closed")

mock.next_place = "reject"
r = st.buy(opt.symbol, lots=1, confirm=True, manual=False)
ok(not r.ok and st.position.option_symbol is None, "broker rejection opens nothing")
mock.next_place = None


def fill_now(order_id):
    return OrderResult(ok=True, mode="live", side="BUY", option_symbol=opt.symbol, lots=1,
                       filled_lots=1, fill_premium=11.0, order_id=order_id, status="FILLED")


mock.order_status = fill_now
r = st.buy(opt.symbol, lots=1, confirm=True, manual=False)
ok(r.ok and st.position.entry_premium == 11.0, "ack -> FILLED within timeout opens at fill price")
mock.order_status = lambda oid: OrderResult(ok=True, mode="live", side="SELL",
                                            option_symbol=opt.symbol, lots=1, filled_lots=1,
                                            fill_premium=11.5, order_id=oid, status="FILLED")
st.sell(confirm=True)

settings.trade_mode = "paper"
n = len(mock.placed)
r = st.buy(opt.symbol, lots=1, confirm=False, manual=False)
ok(r.ok and r.mode == "paper" and len(mock.placed) == n, "paper mode never calls the broker")
st.sell()

# ---------------- real order path stays hard-blocked ----------------
from app.market.angelone import AngelOneProvider  # noqa: E402

try:
    AngelOneProvider.place_order(object(), "X", "BUY", 1)
    blocked = False
except safety.RealMoneyBlocked:
    blocked = True
ok(blocked and safety.REAL_MONEY_EXECUTION_ENABLED is False,
   "Angel One place_order is refused by the source-level real-money guard")

print(f"\nLIVE CONTROLS SMOKE — {passed} passed, {failed} failed")
for f in fails:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
