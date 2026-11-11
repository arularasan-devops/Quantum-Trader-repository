"""Smoke: LIVE exchange stop order at the locked trail floor (option B) against
a MOCKED broker only — nothing contacts Angel One. Run:
    cd backend && python _smoke_exch_trail_stop.py
"""
from __future__ import annotations

import os
import tempfile
import time

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_exch_stop_")
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
        self.orders: list[tuple] = []      # market orders
        self.stops: list[tuple] = []       # place_stop_order calls
        self.modifies: list[tuple] = []
        self.cancels: list[str] = []
        self.status: dict[str, OrderResult] = {}
        self.cancel_confirms = True
        self.fill_px = 100.0
        self.hold_fill = False
        self.positions: dict[str, dict] = {}

    def supports_trading(self):
        return True

    def tick_size(self, sym):
        return 0.1

    def place_order(self, sym, side, lots, product=None):
        self.orders.append((sym, side, lots))
        oid = f"M{len(self.orders)}"
        self.status[oid] = OrderResult(ok=True, mode="live", side=side, option_symbol=sym,
                                       lots=lots, filled_lots=lots, fill_premium=self.fill_px,
                                       order_id=oid, status="FILLED")
        if self.hold_fill:  # Angel filled it, but the order book can't show it
            self.status.pop(oid)
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
        self.modifies.append((oid, lots, trigger, limit))
        return OrderResult(ok=True, mode="live", side="SELL", option_symbol=sym, lots=lots,
                           order_id=oid, status="OPEN")

    def cancel_order(self, oid, variety="STOPLOSS"):
        self.cancels.append(oid)
        if self.cancel_confirms:
            st = self.status[oid]
            self.status[oid] = st.model_copy(update={"status": "CANCELLED"})
        return OrderResult(ok=True, mode="live", side="SELL", option_symbol="", lots=0,
                           order_id=oid, status="ACKNOWLEDGED")

    def broker_position(self, sym):
        return self.positions.get(sym)

    def order_status(self, oid):
        return self.status.get(oid)

    def fill_stop(self, oid, px):
        st = self.status[oid]
        self.status[oid] = st.model_copy(update={"ok": True, "status": "FILLED",
                                                 "filled_lots": st.lots, "fill_premium": px})


settings.order_fill_timeout_sec = 0.2
settings.max_trades_per_day = 0
settings.auto_trade_enabled = True
settings.auto_trade_allow_live = True
settings.auto_trade_exit_on_bid = False
settings.auto_trade_tiered_exit = False
settings.auto_trade_scale_out = False
settings.live_auto_sync_angel_close = True
live_control.set_kill_switch(False, "smoke start")


def engine(flag=True):
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
    settings.live_exchange_trail_stop = flag
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
    return st, b, sym


def at(st, prem):
    st.position.current_premium = prem
    st._auto_trade(None, True, int(time.time()))


# 1. off by default / flag off: nothing placed
check("feature is off by default", AppState.__init__ and
      type(settings).model_fields["live_exchange_trail_stop"].default is False)
st, b, sym = engine(flag=False)
st._auto_tgt_floor = 110.0
at(st, 115.0)
check("flag off: no exchange stop placed", not b.stops and st._exch_stop is None)

# 2. flag on: stop rests at the floor (tick-rounded), limit 1% below
st, b, sym = engine()
st._auto_tgt_floor = 110.03
at(st, 115.0)
check("stop placed once at the floor", len(b.stops) == 1 and b.stops[0][2] == 110.0,
      str(b.stops))
check("limit = trigger - 1% on the tick", b.stops and b.stops[0][3] == 108.9, str(b.stops))
check("stop qty = held lots", b.stops and b.stops[0][1] == 2)
check("exchange stop persisted in the ledger",
      (order_ledger.load(INST).get("exch_stop") or {}).get("order_id") == "S1")
check("no market SELL sent", [o for o in b.orders if o[1] == "SELL"] == [])

# 3. ratchet: small step ignored, big step modifies (never down)
st._auto_tgt_floor = 110.2
at(st, 116.0)
check("small floor rise: no modify", not b.modifies)
st._exch_stop["moved"] = 0
st._auto_tgt_floor = 120.0
at(st, 124.0)
check("floor ratchets to T2: order modified up", b.modifies and b.modifies[-1][2] == 120.0,
      str(b.modifies))
check("no second stop order", len(b.stops) == 1)

# 4. exchange fills the stop: booked from the fill, no market SELL
b.fill_stop("S1", 119.6)
at(st, 119.0)
check("filled stop booked, position flat", not st.position.option_symbol)
check("no market SELL on top of the exchange fill",
      [o for o in b.orders if o[1] == "SELL"] == [], str(b.orders))
last = st.journal.rows[-1] if hasattr(st.journal, "rows") else None
rows = list(getattr(st.journal, "trades", None) or [])
check("exchange stop cleared after fill", st._exch_stop is None and st._pending_exit is None)

# 5. floor below entry: never placed (stop losses stay bot market exits)
st, b, sym = engine()
st._auto_tgt_floor = 99.0
at(st, 104.0)
check("floor at/below entry: no exchange stop", not b.stops)

# 6. breach without exchange fill: waits, then cancels and sends ONE market SELL
st, b, sym = engine()
st._auto_tgt_floor = 110.0
at(st, 115.0)
at(st, 109.5)
check("breach: waits for the exchange order (no market SELL yet)",
      st.position.option_symbol and not [o for o in b.orders if o[1] == "SELL"])
st._exch_stop_breach_at = time.time() - 10
at(st, 109.0)
sells = [o for o in b.orders if o[1] == "SELL"]
check("fallback: cancel confirmed then one market SELL", b.cancels == ["S1"] and len(sells) == 1,
      f"{b.cancels} {sells}")
check("position closed after fallback", not st.position.option_symbol)

# 7. manual exit while resting; cancel unconfirmed -> no SELL
st, b, sym = engine()
st._auto_tgt_floor = 110.0
at(st, 115.0)
b.cancel_confirms = False
r = st.sell(confirm=True)
check("cancel unconfirmed: no market SELL (no double sell)",
      not r.ok and r.status == "NOT_SENT" and not [o for o in b.orders if o[1] == "SELL"])
b.cancel_confirms = True
r = st.sell(confirm=True)
check("cancel confirmed: manual exit sends one SELL",
      r.ok and len([o for o in b.orders if o[1] == "SELL"]) == 1, r.message)

# 8. manual exit after the stop already filled: booked, no SELL
st, b, sym = engine()
st._auto_tgt_floor = 110.0
at(st, 115.0)
b.fill_stop("S1", 109.8)
r = st.sell(confirm=True)
check("stop already filled: manual exit sends nothing",
      not [o for o in b.orders if o[1] == "SELL"] and not st.position.option_symbol, r.message)

# 9. restart restores the resting stop
st, b, sym = engine()
st._auto_tgt_floor = 110.0
at(st, 115.0)
st2 = AppState(instrument=INST)
check("restart restores the resting exchange stop",
      (st2._exch_stop or {}).get("order_id") == "S1")
check("recovery status shows it", st2.recovery_status().get("exchange_stop") is not None)
registry._states[INST] = st

# 10. closed in Angel: the resting stop is cancelled before booking
b.status["S1"] = b.status["S1"]  # still open
st._sync_angel_close(sym, 112.0, "closed in Angel")
check("Angel close cancels the resting stop", b.cancels == ["S1"] and st._exch_stop is None)
check("Angel close books the position", not st.position.option_symbol)

# 11. paper: nothing
settings.trade_mode = "paper"
st3 = AppState(instrument=INST)
st3._sync_exch_stop(115.0)
check("paper engine never places an exchange stop", st3._exch_stop is None)

# 12. hard stop at Angel right after the buy (live_exchange_hard_stop)
settings.trade_mode = "live"
check("hard stop is off by default",
      type(settings).model_fields["live_exchange_hard_stop"].default is False)
st, b, sym = engine(flag=True)
at(st, 101.0)
check("hard stop off: nothing resting before T1", not b.stops)
settings.live_exchange_hard_stop = True
st, b, sym = engine(flag=True)
at(st, 101.0)
check("hard stop placed at the stop after the buy", len(b.stops) == 1 and b.stops[0][2] == 94.0,
      str(b.stops))
check("hard stop reason is STOP LOSS", (st._exch_stop or {}).get("reason", "").startswith("STOP LOSS"))
at(st, 95.0)
check("price near the stop: no market SELL", not [o for o in b.orders if o[1] == "SELL"])
b.fill_stop("S1", 93.8)
at(st, 93.5)
check("hard stop filled at Angel: booked, no market SELL",
      not st.position.option_symbol and not [o for o in b.orders if o[1] == "SELL"],
      str(b.orders))

# 13. hard stop breach with no exchange fill: wait, then cancel + ONE market SELL
st, b, sym = engine(flag=True)
at(st, 101.0)
at(st, 93.9)
check("hard-stop breach: waits for the exchange order",
      st.position.option_symbol and not [o for o in b.orders if o[1] == "SELL"])
st._exch_stop_breach_at = time.time() - 10
at(st, 93.5)
sells = [o for o in b.orders if o[1] == "SELL"]
check("hard-stop fallback: cancel then one market SELL", b.cancels == ["S1"] and len(sells) == 1,
      f"{b.cancels} {sells}")

# 14. stop raised (break-even) -> order modified up; then T1 floor -> modified to floor
st, b, sym = engine(flag=True)
at(st, 101.0)
st._exch_stop["moved"] = 0
st._auto_stop = 100.5
at(st, 104.0)
check("raised stop modifies the resting order up",
      b.modifies and b.modifies[-1][2] == 100.5, str(b.modifies))
check("raised stop at entry is a break-even stop",
      (st._exch_stop or {}).get("reason", "").startswith("BREAK-EVEN"))
st._exch_stop["moved"] = 0
st._auto_tgt_floor = 110.0
at(st, 115.0)
check("T1 floor: same order moved to the floor", len(b.stops) == 1
      and b.modifies[-1][2] == 110.0 and st._exch_stop.get("kind") == "floor", str(b.modifies))

# 15. hard stop never lowered by the trail; only a manual re-set can lower it
st, b, sym = engine(flag=True)
at(st, 101.0)
st._exch_stop["moved"] = 0
st._auto_stop = 90.0
at(st, 101.0)
check("manual lower re-set follows the stop down", b.modifies and b.modifies[-1][2] == 90.0,
      str(b.modifies))

# 16. exchanges with no stop-loss exit: no hard stop resting
settings.auto_trade_no_stop_loss_exchanges = ["MCX"]
st, b, sym = engine(flag=True)
at(st, 101.0)
check("no-stop-loss exchange: no hard stop placed", not b.stops)
settings.auto_trade_no_stop_loss_exchanges = []
settings.live_exchange_hard_stop = False

# 17. BUY acknowledged, fill never visible in the order book: adopted from the
#     Angel position after the wait, and managed with the signal's stop/targets
st, b, sym = engine(flag=True)
st.sell(confirm=True)
check("flat before the pending-entry test", not st.position.option_symbol)
b.hold_fill = True
r = st.buy(sym, lots=2, confirm=True, manual=False)
check("unconfirmed BUY stays pending (no position yet)",
      not r.ok and st._pending_order and not st.position.option_symbol, r.message)
st._pending_order["auto"] = {"option": sym, "stop": 92.0, "t1": 111.0, "t2": 121.0,
                             "t3": 131.0, "strike": None, "conf": 85.0, "lots": 2}
lot = next(o.lot_size for o in b.option_chain() if o.symbol == sym)
b.positions[sym] = {"net_qty": 2 * lot, "buy_avg": 101.5, "sell_avg": 0.0}
st._last_order_poll = 0
st._poll_live_orders()
check("not adopted before the wait", st._pending_order and not st.position.option_symbol)
st._pending_order["ts"] = time.time() - 60
st._last_order_poll = 0
st._poll_live_orders()
check("adopted from the Angel position at Angel's buy average",
      st.position.option_symbol == sym and st.position.quantity_lots == 2
      and abs((st.position.entry_premium or 0) - 101.5) < 1e-6 and not st._pending_order,
      f"{st.position.option_symbol} {st.position.entry_premium}")
check("late fill is managed with the signal levels",
      st._auto_entry_active and st._auto_stop == 92.0 and st._auto_target1 == 111.0,
      f"{st._auto_stop} {st._auto_target1}")
check("adoption sent no extra order", len([o for o in b.orders if o[1] == "BUY"]) == 2,
      str(b.orders))
b.hold_fill = False

print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
