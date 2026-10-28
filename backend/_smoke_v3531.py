"""Smoke v3.5.31: QT_AUTO_TRADE_INSTRUMENTS limits auto-buys to the listed
instruments (empty = all). Paper/simulated feed only.
    cd backend && python _smoke_v3531.py
"""
import os
import tempfile
import time
from types import SimpleNamespace

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_v3531_")
os.environ.pop("QT_DB_URL", None)
os.environ["QT_DATA_PROVIDER"] = "simulated"
from app.config import settings  # noqa: E402
from app import state as state_mod  # noqa: E402
from app.state import AppState  # noqa: E402
from app.models import Signal  # noqa: E402

passed = failed = 0


def ok(cond, msg):
    global passed, failed
    passed, failed = passed + bool(cond), failed + (not cond)
    print(("PASS " if cond else "FAIL ") + msg)


seen = []
_orig = state_mod.exec_funnel.blocked
state_mod.exec_funnel.blocked = lambda stage, blocker, **kw: (seen.append(blocker), _orig(stage, blocker, **kw))

ok(settings.auto_trade_instruments == "", "default is empty (all instruments)")
settings.trade_mode = "paper"
settings.ignore_market_hours = True
settings.auto_trade_enabled = True


def attempt(inst):
    st = AppState(instrument=inst)
    st.tick()
    opt = st.provider.option_chain()[0]
    dec = SimpleNamespace(signal=Signal.BUY, recommended_option=opt.symbol, current_premium=100.0,
                          stop_loss=94.0, target1=105.0, target2=110.0, target3=115.0,
                          option_type=opt.option_type, confidence=0.0, entry_trigger="NONE")
    seen.clear()
    try:
        st._auto_trade(dec, False, int(time.time()))
    except Exception:
        pass
    return list(seen), st


for inst in ("NIFTY", "CRUDEOIL"):
    b, _ = attempt(inst)
    ok("INSTRUMENT_NOT_ALLOWED" not in b, f"empty list: {inst} not blocked by instrument")
settings.auto_trade_instruments = "crudeoil, SILVER ,COPPER,NATURALGAS"
b, st = attempt("NIFTY")
ok("INSTRUMENT_NOT_ALLOWED" in b, "listed set: NIFTY auto-buy blocked")
ok("not in auto-trade instruments" in (st._auto_last_action or ""), "skip reason shown")
ok(st.position.option_symbol is None, "no NIFTY position opened")
b, _ = attempt("CRUDEOIL")
ok("INSTRUMENT_NOT_ALLOWED" not in b, "listed set: CRUDEOIL passes (case/space tolerant)")
settings.auto_trade_instruments = ""
print(f"{passed} passed, {failed} failed")
