"""Smoke v3.5.30: the registry lot size follows the broker scrip master, so
sizing/risk/journal match the real order quantity. No login, no orders.
    cd backend && python _smoke_v3530.py
"""
import os
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_v3530_")
os.environ.pop("QT_DB_URL", None)
from app.market import angelone  # noqa: E402
from app.market.instruments import get_spec  # noqa: E402

passed = failed = 0


def ok(cond, msg):
    global passed, failed
    passed, failed = passed + bool(cond), failed + (not cond)
    print(("PASS " if cond else "FAIL ") + msg)


def rows(name, seg, lots):
    r = [{"exch_seg": seg, "name": name, "expiry": "25DEC2099", "instrumenttype": "FUTIDX",
          "token": "1", "symbol": name + "25DEC99FUT", "strike": "-1", "lotsize": str(lots[0])}]
    for i, lot in enumerate(lots):
        r.append({"exch_seg": seg, "name": name, "expiry": "25DEC2099", "instrumenttype": "OPTIDX",
                  "token": str(10 + i), "symbol": f"{name}25DEC99{50000 + i * 100}CE",
                  "strike": str((50000 + i * 100) * 100), "lotsize": str(lot)})
    return r


def load(name, seg, lots):
    p = object.__new__(angelone.AngelOneProvider)
    p._exchange, p._root, p._store_key = seg, name, name
    angelone._get_scrip_master = lambda: rows(name, seg, lots)
    p._load_instruments()
    return p


before = get_spec("BANKNIFTY").lot_size
p = load("BANKNIFTY", "NFO", [30, 30])
ok(before == 15 and get_spec("BANKNIFTY").lot_size == 30, f"BANKNIFTY {before} -> 30 from broker")
ok(all(o.lot_size == 30 for o in p._options), "order lot (broker) unchanged at 30")
load("CRUDEOIL", "MCX", [100, 100])
ok(get_spec("CRUDEOIL").lot_size == 100, "CRUDEOIL matching lot left at 100")
load("NIFTY", "NFO", [65, 75])
ok(get_spec("NIFTY").lot_size == 65, "mixed broker lots: registry lot kept")
print(f"{passed} passed, {failed} failed")
