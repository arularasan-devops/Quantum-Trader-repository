"""Smoke v3.5.43: a live position closed in the Angel app is booked at Angel's
sell average with no SELL sent; a SELL that never reached Angel keeps the
stop/targets and backs off. No broker."""
import os
import tempfile
import time

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())

from app.config import settings  # noqa: E402
from app.engine import account_risk  # noqa: E402
from app.market.instruments import REGISTRY  # noqa: E402
from app.models import OrderResult  # noqa: E402
from app.state import AppState  # noqa: E402

FAIL: list[str] = []
SENT: list = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    if not ok:
        FAIL.append(name)


def opened(row):
    account_risk.account.reset()
    AppState.is_live = property(lambda s: False)
    eng = AppState(instrument="CRUDEOIL")
    settings.auto_trade_enabled = False
    eng.tick()
    eng._own_bid = lambda: None
    chain = [o for o in eng.provider.option_chain() if o.premium >= 60]
    pick = min(chain, key=lambda o: abs(o.premium - 200))
    r = eng.buy(pick.symbol, lots=1, manual=False)
    assert r.ok and eng.position.option_symbol, r.message
    AppState.is_live = property(lambda s: True)
    eng.provider.place_order = lambda *a, **k: (SENT.append(a), OrderResult(
        ok=False, mode="live", side="SELL", option_symbol=pick.symbol, lots=1,
        message="Order error: ConnectTimeoutError", status="NOT_SENT"))[1]
    eng.provider.broker_position = lambda s: row
    eng._order_recovery = None
    eng._pending_exit = None
    return eng, pick.symbol, pick.premium


FLAT = {"net_qty": 0, "buy_avg": 200.0, "sell_avg": 237.5}

eng, sym, prem = opened(FLAT)
r = eng.sell(confirm=True, check_broker=True)
check("Exit now, Angel already flat: booked, no SELL", r.ok and not SENT
      and not eng.position.option_symbol, f"{r.message} sent={len(SENT)}")
check("booked at Angel sell avg", abs((r.fill_premium or 0) - 237.5) < 0.01 or "237.50" in r.message,
      r.message)
check("no recovery left", eng._order_recovery is None)

eng, sym, prem = opened(FLAT)
t = time.time()
eng._last_broker_pos_check = 0.0
eng._check_broker_position(t)
check("one flat read: still held", bool(eng.position.option_symbol))
eng._check_broker_position(t + settings.live_position_check_sec + 1)
check("two flat reads: auto-booked, no SELL, no recovery",
      not eng.position.option_symbol and not SENT and eng._order_recovery is None)

eng, sym, prem = opened({"net_qty": 0, "buy_avg": 200.0, "sell_avg": 0.0})
eng._check_broker_position(t)
eng._check_broker_position(t + settings.live_position_check_sec + 1)
check("flat but no sell price: recovery (old safe path)",
      bool(eng.position.option_symbol) and eng._order_recovery is not None and not SENT)

eng, sym, prem = opened({"net_qty": 100, "buy_avg": 200.0, "sell_avg": 0.0})
eng._auto_stop = prem * 0.9
r1 = eng.sell(confirm=True, check_broker=True)
check("Angel open: SELL is sent", len(SENT) == 1 and not r1.ok, r1.message)
r2 = eng.sell(confirm=True)
check("SELL not sent: retry waits (no second request)", len(SENT) == 1 and r2.status == "NOT_SENT")
eng._reset_auto_trade_marks()
check("failed live exit keeps the stop", eng._auto_stop is not None)
eng._exit_retry_at = 0.0
eng.sell(confirm=True)
check("after the wait the SELL is retried", len(SENT) == 2)
SENT.clear()

check("MAZDOCK in the instrument list", "MAZDOCK" in REGISTRY)
check("MAZDOCK lot 225, strikes every 20 (Angel scrip master)", REGISTRY["MAZDOCK"].lot_size == 225 and REGISTRY["MAZDOCK"].strike_step == 20)
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
