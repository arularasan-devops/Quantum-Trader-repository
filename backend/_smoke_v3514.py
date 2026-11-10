"""v3.5.14 LIVE parity, against a MOCK broker only (no Angel call): an open live
auto position keeps its exits after the kill switch, live square-off times,
live defaults use the paper engine + capital sizing."""
import os
import sys
import tempfile
import time
from types import SimpleNamespace

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_v3514_")
from app.config import settings  # noqa: E402
from app.execution import live_control  # noqa: E402
from app.market.simulated import SimulatedProvider  # noqa: E402
from app.models import OrderResult  # noqa: E402
from app.state import AppState  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    fails += 0 if ok else 1


class MockBroker(SimulatedProvider):
    def __init__(self, base):
        self.__dict__.update(base.__dict__)
        self.placed, self.statuses = [], {}

    def supports_trading(self):
        return True

    def place_order(self, option_symbol, side, lots):
        self.placed.append((option_symbol, side, lots))
        oid = f"OID{len(self.placed)}"
        self.statuses[oid] = OrderResult(ok=True, mode="live", side=side, option_symbol=option_symbol,
                                         lots=lots, filled_lots=lots, fill_premium=10.0,
                                         order_id=oid, status="FILLED")
        return OrderResult(ok=True, mode="live", side=side, option_symbol=option_symbol,
                           lots=lots, order_id=oid, status="ACKNOWLEDGED")

    def order_status(self, order_id):
        return self.statuses.get(order_id)


check("live uses paper engine by default", settings.auto_trade_live_pullback_engine)
check("live lots from capital by default", settings.auto_trade_live_capital_sizing)
check("live exits on bid by default", settings.auto_trade_live_exit_on_bid)

settings.order_fill_timeout_sec = 0.3
st = AppState(instrument="CRUDEOIL")
st.tick()
mock = MockBroker(st.provider)
st.provider = mock
st._providers[st.instrument] = mock
settings.trade_mode = "live"
settings.auto_trade_enabled = True
settings.auto_trade_allow_live = True
opt = mock.option_chain()[0]
r = st.buy(opt.symbol, lots=1, confirm=True, manual=False)
check("mock live entry filled", r.ok and st.position.option_symbol == opt.symbol, r.message)
st._auto_entry_active = True
st._auto_stop = 1e9
st._auto_tgt_floor = None
live_control.set_kill_switch(True, "smoke")
check("kill switch disarms the bot", not settings.auto_trade_enabled and not settings.auto_trade_allow_live)
st._auto_trade(SimpleNamespace(), True, int(time.time()))
check("open live position still exits after kill switch",
      mock.placed[-1][1] == "SELL" and st.position.option_symbol is None, mock.placed)
n = len(mock.placed)
st._auto_trade(SimpleNamespace(), False, int(time.time()))
check("no new entry while kill switch is on", len(mock.placed) == n)

settings.ignore_market_hours = False
day = time.time()
mid = (int(day) + 19800) // 86400 * 86400 - 19800
check("MCX live square-off due 23:05", st._live_squareoff_due(mid + 23 * 3600 + 5 * 60))
check("MCX live square-off not at 22:00", not st._live_squareoff_due(mid + 22 * 3600))
st._instrument = "SBIN"
check("NSE live square-off due 15:21", st._live_squareoff_due(mid + 15 * 3600 + 21 * 60))
check("NSE live square-off not at 15:00", not st._live_squareoff_due(mid + 15 * 3600))
st._instrument = "CRUDEOIL"

print("ALL V3.5.14 CHECKS PASSED" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
