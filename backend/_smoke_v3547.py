"""Smoke v3.5.47: stock/index options (NFO/BFO) have no stop-loss exit; MCX keeps
it; break-even stop still exits everywhere. No broker."""
import os
import tempfile

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())

from app.config import settings  # noqa: E402
from app.engine import account_risk  # noqa: E402
from app.state import AppState  # noqa: E402

FAIL: list[str] = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    if not ok:
        FAIL.append(name)


AppState.is_live = property(lambda s: False)
settings.auto_trade_enabled = False


def held(inst):
    AppState.is_live = property(lambda s: False)
    account_risk.account.reset()
    eng = AppState(instrument=inst)
    eng.tick()
    eng._own_bid = lambda: None
    chain = [o for o in eng.provider.option_chain() if o.premium >= 20]
    pick = min(chain, key=lambda o: abs(o.premium - 100))
    r = eng.buy(pick.symbol, lots=1, manual=False)
    assert r.ok, r.message
    AppState.is_live = property(lambda s: True)
    e = eng.position.entry_premium
    eng._auto_entry_active = True
    eng._auto_target1, eng._auto_target2, eng._auto_target3 = e * 1.3, e * 1.6, e * 2
    eng._auto_stop, eng._auto_entry_prem, eng._auto_entry_lots = e * 0.9, e, 1
    eng.SOLD = []
    eng.sell = lambda confirm=False, lots=None, fill_px=None, check_broker=False: eng.SOLD.append(1)
    eng._live_squareoff_due = lambda now: False
    eng.position.current_premium = e * 0.8
    return eng, e


for inst, exits in (("CRUDEOIL", True), ("NIFTY", False), ("RELIANCE", False)):
    eng, e = held(inst)
    eng._auto_trade(None, True, 0)
    check(f"{inst}: below the stop -> {'exits' if exits else 'holds'}", bool(eng.SOLD) == exits,
          getattr(eng, "_pending_exit_reason", None))
    if not exits:
        eng._auto_stop = e * 1.01            # stop raised to break-even/lock
        eng.position.current_premium = e
        eng._auto_trade(None, True, 0)
        check(f"{inst}: break-even/lock stop still exits", bool(eng.SOLD)
              and eng._pending_exit_reason == "BREAK-EVEN STOP", eng._pending_exit_reason)
settings.auto_trade_no_stop_loss_exchanges = []
eng, e = held("NIFTY")
eng._auto_trade(None, True, 0)
check("setting emptied -> NIFTY stop-loss exits again", bool(eng.SOLD))
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
