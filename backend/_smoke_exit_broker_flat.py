"""Smoke: a bot exit SELL is not sent when Angel already shows the position
flat after its exchange stop was dropped or a SELL was rejected. MOCKED broker
only — nothing contacts Angel One. Run:
    cd backend && python _smoke_exit_broker_flat.py
"""
from __future__ import annotations

import os
import tempfile
import time

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_exit_flat_")
os.environ.pop("QT_DB_URL", None)
os.environ.pop("QT_CAPITAL", None)

from app.config import settings  # noqa: E402
from app.execution import live_control, order_ledger  # noqa: E402
from app.market.simulated import SimulatedProvider  # noqa: E402
from app.models import OrderResult  # noqa: E402
from app.state import AppState, registry  # noqa: E402

FAIL: list[str] = []
INST = "CRUDEOIL"


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {info}"))
    if not cond:
        FAIL.append(name)


class Broker(SimulatedProvider):
    def __init__(self, base):
        self.__dict__.update(base.__dict__)
        self.orders: list[tuple] = []
        self.stops: list[tuple] = []
        self.status: dict[str, OrderResult] = {}
        self.positions: dict[str, dict] = {}
        self.pos_calls = 0
        self.reject_sells = False

    def supports_trading(self):
        return True

    def tick_size(self, sym):
        return 0.1

    def place_order(self, sym, side, lots, product=None):
        self.orders.append((sym, side, lots))
        oid = f"M{len(self.orders)}"
        if side == "SELL" and self.reject_sells:
            self.status[oid] = OrderResult(ok=False, mode="live", side=side, option_symbol=sym,
                                           lots=lots, filled_lots=0, order_id=oid,
                                           status="REJECTED", message="Insufficient Funds")
        else:
            self.status[oid] = OrderResult(ok=True, mode="live", side=side, option_symbol=sym,
                                           lots=lots, filled_lots=lots, fill_premium=100.0,
                                           order_id=oid, status="FILLED")
        return OrderResult(ok=True, mode="live", side=side, option_symbol=sym, lots=lots,
                           order_id=oid, status="ACKNOWLEDGED")

    def place_stop_order(self, sym, lots, trigger, limit, product=None):
        self.stops.append((sym, lots, trigger, limit))
        oid = f"S{len(self.stops)}"
        self.status[oid] = OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym,
                                       lots=lots, filled_lots=0, order_id=oid, status="OPEN")
        return OrderResult(ok=True, mode="live", side="SELL", option_symbol=sym, lots=lots,
                           order_id=oid, status="ACKNOWLEDGED")

    def modify_stop_order(self, oid, sym, lots, trigger, limit, product=None):
        return OrderResult(ok=True, mode="live", side="SELL", option_symbol=sym, lots=lots,
                           order_id=oid, status="ACKNOWLEDGED")

    def cancel_order(self, oid, variety="STOPLOSS"):
        st = self.status[oid]
        self.status[oid] = st.model_copy(update={"status": "CANCELLED"})
        return OrderResult(ok=True, mode="live", side="SELL", option_symbol="", lots=0,
                           order_id=oid, status="ACKNOWLEDGED")

    def broker_position(self, sym):
        self.pos_calls += 1
        return self.positions.get(sym)

    def order_status(self, oid):
        return self.status.get(oid)


settings.order_fill_timeout_sec = 0.2
settings.max_trades_per_day = 0
settings.auto_trade_enabled = True
settings.auto_trade_allow_live = True
settings.auto_trade_exit_on_bid = False
settings.auto_trade_tiered_exit = False
settings.auto_trade_scale_out = False
settings.live_auto_sync_angel_close = True
live_control.set_kill_switch(False, "smoke start")


def engine():
    order_ledger.save(INST, None)
    from app.engine import account_risk
    account_risk.account.reset()
    settings.trade_mode = "paper"
    st = AppState(instrument=INST)
    registry._states[INST] = st
    st.tick()
    b = Broker(st.provider)
    st.provider = b
    st._providers[st.instrument] = b
    settings.trade_mode = "live"
    settings.live_exchange_trail_stop = True
    st._auto_live_armed = lambda: True
    st._live_squareoff_due = lambda now: False
    sym = b.option_chain()[0].symbol
    r = st.buy(sym, lots=2, confirm=True, manual=False)
    assert st.position.option_symbol == sym, r.message
    st._order_recovery = None
    st._auto_entry_active = True
    st._auto_entry_prem = st.position.entry_premium = 100.0
    st._auto_target1, st._auto_target2, st._auto_target3 = 110.0, 120.0, 130.0
    st._auto_stop = 94.0
    st._auto_entry_lots = 2
    st._auto_tgt_floor = 110.0
    st.position.current_premium = 115.0
    st._auto_trade(None, True, int(time.time()))
    return st, b, sym


def drop_stop(st, b):
    oid = st._exch_stop["order_id"]
    b.status[oid] = b.status[oid].model_copy(update={"status": "CANCELLED"})
    st._poll_exch_stop(time.time() + 10, min_gap=0.0)


def sells(b):
    return [o for o in b.orders if o[1] == "SELL"]


check("config default on", type(settings).model_fields["live_exit_broker_check"].default is True)

# 1. exchange stop dropped, Angel flat -> booked from Angel, no SELL
st, b, sym = engine()
check("exchange stop rests", st._exch_stop is not None, str(b.stops))
drop_stop(st, b)
check("drop recorded", st._exch_stop is None and st._exch_stop_dropped_sym == sym)
b.positions[sym] = {"net_qty": 0, "buy_avg": 100.0, "sell_avg": 112.0}
r = st.sell(confirm=True)
check("dropped+flat: no SELL sent", not sells(b), str(b.orders))
check("dropped+flat: position closed", not st.position.option_symbol, r.message)

# 2. exchange stop dropped, Angel still long -> SELL is sent
st, b, sym = engine()
drop_stop(st, b)
lot = 2
b.positions[sym] = {"net_qty": 2 * lot, "buy_avg": 100.0, "sell_avg": 0.0}
st.sell(confirm=True)
check("dropped+long: SELL sent", len(sells(b)) == 1, str(b.orders))

# 3. normal path (stop resting): no extra broker read before the SELL
st, b, sym = engine()
calls = b.pos_calls
r1 = st.sell(confirm=True)  # first call cancels the resting stop
r2 = st.sell(confirm=True)
check("resting stop: no position read", b.pos_calls == calls, f"{b.pos_calls} vs {calls}")
check("resting stop: SELL sent after cancel", len(sells(b)) == 1,
      f"{b.orders} | {r1.status} {r1.message} | {r2.status} {r2.message} | {st._exch_stop}")

# 4. SELL rejected, then Angel flat -> second attempt books from Angel
st, b, sym = engine()
drop_stop(st, b)
b.reject_sells = True
st.sell(confirm=True)
check("first SELL rejected", len(sells(b)) == 1 and st.position.option_symbol == sym,
      str(b.orders))
b.positions[sym] = {"net_qty": 0, "buy_avg": 100.0, "sell_avg": 111.0}
st.sell(confirm=True)
check("after reject+flat: no second SELL", len(sells(b)) == 1, str(b.orders))
check("after reject+flat: position closed", not st.position.option_symbol)

# 5. flag off -> old behaviour (SELL sent even though Angel is flat)
settings.live_exit_broker_check = False
st, b, sym = engine()
drop_stop(st, b)
b.positions[sym] = {"net_qty": 0, "buy_avg": 100.0, "sell_avg": 112.0}
st.sell(confirm=True)
check("flag off: SELL sent", len(sells(b)) == 1, str(b.orders))
settings.live_exit_broker_check = True

# 6. SELL rejected for funds (Angel would open a short) -> no further SELLs
st, b, sym = engine()
drop_stop(st, b)
b.reject_sells = True
b.positions[sym] = {"net_qty": 2 * 2, "buy_avg": 100.0, "sell_avg": 0.0}
st.sell(confirm=True)
n_stops = len(b.stops)
for _ in range(5):
    r = st.sell(confirm=True)
    st._auto_trade(None, True, int(time.time()))
check("funds reject: one SELL only", len(sells(b)) == 1, str(b.orders))
check("funds reject: recovery + halt", (st._order_recovery or {}).get("sell_halted") == sym,
      str(st._order_recovery))
check("funds reject: no new exchange stop", len(b.stops) == n_stops, str(b.stops))
check("funds reject: NOT_SENT message", r.status == "NOT_SENT" and "short" in r.message,
      r.message)
check("funds reject: critical alert",
      any(a.kind == "SELL_HALTED_FUNDS_REJECT" for a in st.alerts))
st._recovery_cleared("operator checked Angel")
check("cleared recovery lifts the halt", st._sell_halt_reason(sym) is None)

# 6b. halt is manual-only; confirm_flat books the app position, no SELL
st, b, sym = engine()
drop_stop(st, b)
b.reject_sells = True
b.positions[sym] = {"net_qty": 4, "buy_avg": 100.0, "sell_avg": 0.0}
st.sell(confirm=True)
st._reconcile_recovery()
check("halt survives auto reconciliation", (st._order_recovery or {}).get("sell_halted") == sym)
b.positions[sym] = {"net_qty": 0, "buy_avg": 100.0, "sell_avg": 108.0}
st.resolve_recovery("confirm_flat", "closed in Angel app")
check("confirm_flat books the app position", not st.position.option_symbol)
check("confirm_flat: still one SELL", len(sells(b)) == 1, str(b.orders))

# 6c. funds reject arriving later via pending reconciliation -> halt, no second SELL
st, b, sym = engine()
drop_stop(st, b)
b.positions[sym] = {"net_qty": 4, "buy_avg": 100.0, "sell_avg": 0.0}
_orig_place = b.place_order
def _ack_only(sym_, side, lots, product=None):
    r = _orig_place(sym_, side, lots, product)
    b.status[r.order_id] = OrderResult(ok=False, mode="live", side=side, option_symbol=sym_,
                                       lots=lots, filled_lots=0, order_id=r.order_id,
                                       status="OPEN")
    return r
b.place_order = _ack_only
st.sell(confirm=True)
oid = st._pending_exit["order_id"] if st._pending_exit else None
check("async: SELL pending", oid is not None, str(st._pending_exit))
if oid:
    b.status[oid] = b.status[oid].model_copy(update={
        "status": "REJECTED", "message": "rejected due to Insufficient Funds"})
st.sell(confirm=True)
check("async funds reject: halted", (st._order_recovery or {}).get("sell_halted") == sym,
      str(st._order_recovery))
check("async funds reject: no second SELL", len(sells(b)) == 1, str(b.orders))

# 7. exchange stop rejected for funds -> halt, no bot SELL
st, b, sym = engine()
oid = st._exch_stop["order_id"]
b.status[oid] = b.status[oid].model_copy(update={
    "status": "REJECTED", "message": "Your order has been rejected due to Insufficient "
    "Funds. Available funds - Rs. 147920.51. You require Rs. 305662.04"})
st._poll_exch_stop(time.time() + 10, min_gap=0.0)
st.sell(confirm=True)
check("stop funds reject: no bot SELL", not sells(b), str(b.orders))
check("stop funds reject: halted", (st._order_recovery or {}).get("sell_halted") == sym)

# 8. non-funds reject -> no halt (old behaviour)
st, b, sym = engine()
oid = st._exch_stop["order_id"]
b.status[oid] = b.status[oid].model_copy(update={"status": "REJECTED",
                                                 "message": "RMS: price band"})
st._poll_exch_stop(time.time() + 10, min_gap=0.0)
check("other reject: no halt", st._order_recovery is None, str(st._order_recovery))

# 9. halt flag off -> old behaviour (SELL re-sent after a funds reject)
settings.live_sell_funds_reject_halt = False
st, b, sym = engine()
drop_stop(st, b)
b.reject_sells = True
b.positions[sym] = {"net_qty": 4, "buy_avg": 100.0, "sell_avg": 0.0}
st.sell(confirm=True)
st.sell(confirm=True)
check("halt flag off: SELL re-sent", len(sells(b)) == 2, str(b.orders))
settings.live_sell_funds_reject_halt = True

print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
