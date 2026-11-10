"""Smoke v3.5.40: a live auto-buy that never reached Angel (connect timeout) retries
while the signal is valid; an unknown outcome keeps the pending record. No broker."""
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



from app.market.angelone import order_not_sent  # noqa: E402

TRIG = sorted(settings.auto_trade_allowed_triggers)[0]
BLOCKS: list = []
_blocked = exec_funnel.blocked
exec_funnel.blocked = lambda *a, **k: (BLOCKS.append((a[1], k.get("reason"))), _blocked(*a, **k))


def vdec(sym, prem):
    return dec(sym, prem).model_copy(update={"entry_trigger": TRIG, "entry_triggers": [TRIG]})


check("connect timeout = not sent", order_not_sent(Exception(
    "HTTPSConnectionPool(host='apiconnect.angelone.in', port=443): Max retries exceeded with url: "
    "/rest/secure/angelbroking/order/v1/placeOrder (Caused by ConnectTimeoutError(...))")))
check("read timeout = unknown (may be at Angel)", not order_not_sent(Exception(
    "HTTPSConnectionPool(host='apiconnect.angelone.in', port=443): Read timed out. (read timeout=4)")))

# auto-trade: an order that never reached Angel retries while the signal is valid
eng, sym, prem = fresh()
CALLS = []


def unsent(*a, **k):
    CALLS.append(a)
    return OrderResult(ok=False, mode="live", side="BUY", option_symbol=sym, lots=1,
                       message="Order error: ConnectTimeoutError", status="NOT_SENT")


eng.buy = unsent
d = vdec(sym, prem)
t0 = int(time.time())
eng._auto_trade(d, False, t0)
check("not sent: episode NOT consumed", len(CALLS) == 1 and eng._auto_last_key is None, str(BLOCKS[-3:]))
eng._auto_trade(d, False, t0 + 5)
check("not sent: waits the retry delay", len(CALLS) == 1)
for i in range(1, 5):
    eng._auto_trade(d, False, t0 + 20 * i)
check("not sent: retried, at most 3 retries then consumed",
      len(CALLS) == 4 and eng._auto_last_key is not None, str(len(CALLS)))

eng, sym, prem = fresh()
eng.buy = lambda *a, **k: OrderResult(ok=False, mode="live", side="BUY", option_symbol=sym,
                                      lots=1, message="Order rejected", status="REJECTED")
eng._auto_trade(vdec(sym, prem), False, int(time.time()))
check("broker rejection: episode stays consumed",
      eng._auto_last_key is not None and eng._episode_rearm_wait(time.time()) is None)

# _submit_live: UNKNOWN keeps the pending record, NOT_SENT clears it
eng, sym, prem = fresh()
eng.provider.place_order = lambda *a, **k: OrderResult(
    ok=False, mode="live", side="BUY", option_symbol=sym, lots=1,
    message="Order error: Read timed out", status="UNKNOWN")
r = eng._submit_live("_pending_order", sym, "BUY", 1)
check("unknown outcome: pending entry kept (no duplicate)", not r.ok and eng._pending_order is not None)
eng._pending_order = None
eng.provider.place_order = lambda *a, **k: OrderResult(
    ok=False, mode="live", side="BUY", option_symbol=sym, lots=1,
    message="Order error: ConnectTimeoutError", status="NOT_SENT")
r = eng._submit_live("_pending_order", sym, "BUY", 1)
check("not sent: pending entry cleared", not r.ok and eng._pending_order is None)
eng._pending_order = None

check("no broker calls", not BROKER, str(BROKER))
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
