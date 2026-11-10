"""Smoke v3.5.45: a live bot trade's exit state (stop, T1-T3, locked floor,
peak) survives a restart + adopt_position. No broker."""
import copy
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


account_risk.account.reset()
AppState.is_live = property(lambda s: False)
settings.auto_trade_enabled = False
eng = AppState(instrument="CRUDEOIL")
eng.tick()
eng._own_bid = lambda: None
chain = [o for o in eng.provider.option_chain() if o.premium >= 60]
pick = min(chain, key=lambda o: abs(o.premium - 200))
r = eng.buy(pick.symbol, lots=1, manual=False)
assert r.ok, r.message
AppState.is_live = property(lambda s: True)
e = eng.position.entry_premium
eng._auto_entry_active = True
eng._auto_target1, eng._auto_target2, eng._auto_target3 = e + 10, e + 20, e + 30
eng._auto_stop, eng._auto_entry_prem, eng._auto_entry_lots = e - 8, e, 1
eng._ledger_position = {"symbol": pick.symbol, "lots": 1, "entry": e, "order_ids": [],
                        "ts": 1, "verified": True}
eng._save_live_mgmt()
check("levels saved with the live trade", eng._ledger_position["mgmt"]["_auto_target3"] == e + 30)
eng._auto_tgt_floor, eng._auto_peak = e + 21, e + 26   # T2 locked, peak above it
eng._save_live_mgmt()
check("T2 lock + peak saved", eng._ledger_position["mgmt"]["_auto_tgt_floor"] == e + 21
      and eng._ledger_position["mgmt"]["_auto_peak"] == e + 26)
saved = copy.deepcopy(eng._ledger_position)

# restart: fresh engine, saved claim, operator adopts
new = AppState(instrument="CRUDEOIL")
new._ledger_position = copy.deepcopy(saved)
new._order_recovery = {"reason": "restart", "since": 0}
new.resolve_recovery("adopt_position", "Angel still holds it")
check("adopted: T1/T2/T3 back", (new._auto_target1, new._auto_target2, new._auto_target3)
      == (e + 10, e + 20, e + 30))
check("adopted: stop back", new._auto_stop == e - 8)
check("adopted: T2 lock floor + peak back", new._auto_tgt_floor == e + 21 and new._auto_peak == e + 26)
check("adopted: bot manages the exit", new._auto_entry_active and new._order_recovery is None)

# old ledger (only t1/t2): still gets a hard stop
old = {k: v for k, v in saved.items() if k != "mgmt"}
old.update(t1=e + 10, t2=e + 20)
o = AppState(instrument="CRUDEOIL")
o._ledger_position = old
o._order_recovery = {"reason": "restart", "since": 0}
o.resolve_recovery("adopt_position", "Angel still holds it")
check("old saved trade: hard stop 10% below entry", o._auto_stop == round(e * 0.9, 1)
      and o._auto_target1 == e + 10)

# lock is honoured after adopt: price falls to the floor -> exit
SOLD = []
new.sell = lambda confirm=False, lots=None, fill_px=None, check_broker=False: SOLD.append(1)
new._live_squareoff_due = lambda now: False
new._own_bid = lambda: None
new.position.current_premium = e + 25
new._auto_trade(None, True, 0)
check("after adopt, above the T2 lock: holds", not SOLD)
new.position.current_premium = e + 20.5
new._auto_trade(None, True, 0)
check("after adopt, back to the T2 lock: exits", len(SOLD) == 1
      and new._pending_exit_reason.endswith("lock"), new._pending_exit_reason)
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
