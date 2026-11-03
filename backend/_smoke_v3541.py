"""Smoke v3.5.41: GOLDM / SILVERM instruments; GOLD/SILVER auto-buys go to the mini.
Simulated engine; provider.place_order is a tripwire, nothing reaches a broker."""
import os
import tempfile
import time

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())

from app.analysis import exec_funnel  # noqa: E402
from app.config import settings  # noqa: E402
from app.engine import account_risk  # noqa: E402
from app.market import instruments  # noqa: E402
from app.models import Decision, OptionType, Signal  # noqa: E402
from app.state import AppState  # noqa: E402

FAIL: list[str] = []
BROKER: list = []
BLOCKS: list = []
_blocked = exec_funnel.blocked
exec_funnel.blocked = lambda *a, **k: (BLOCKS.append(a[1]), _blocked(*a, **k))


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    if not ok:
        FAIL.append(name)


R = instruments.REGISTRY
check("GOLDM / SILVERM registered (Rs 10 / Rs 5 per point per lot)",
      R["GOLDM"].lot_size == 10 and R["SILVERM"].lot_size == 5 and R["GOLDM"].exchange == "MCX")
check("minis stay in the capped universe", {"GOLDM", "SILVERM"} <= set(instruments._apply_cap(list(R))))
check("GOLDM lot never overwritten by Angel's gram lotsize", "GOLDM" in instruments.PNL_LOT_FIXED)

TRIG = sorted(settings.auto_trade_allowed_triggers)[0]


def run(inst):
    account_risk.account.reset()
    eng = AppState(instrument=inst)
    settings.auto_trade_enabled = False
    eng.tick()
    settings.auto_trade_enabled = True
    eng.provider.place_order = lambda *a, **k: BROKER.append(a)
    eng._own_bid = lambda: None
    eng._last_market_status = "TRENDING"
    o = min((o for o in eng.provider.option_chain() if o.premium > 20),
            key=lambda o: abs(o.premium - 300))
    calls = []
    eng.buy = lambda *a, **k: calls.append(a) or None
    d = Decision(signal=Signal.BUY, confidence=90.0, signal_strength=90.0, trade_quality="A",
                 recommended_option=o.symbol, option_type=OptionType.CALL,
                 current_premium=o.premium, spot_price=70000.0, stop_loss=o.premium * 0.92,
                 target1=o.premium + 30, target2=o.premium + 40, target3=o.premium + 50,
                 entry_trigger=TRIG, entry_triggers=[TRIG], premium_momentum=1.0,
                 buy_trap_prob=0.0, premium_health="HEALTHY", reasons=["smoke"])
    BLOCKS.clear()
    try:
        eng._auto_trade(d, False, int(time.time()))
    except Exception:
        pass  # buy() stub returns None; only whether it was reached matters
    return calls, list(BLOCKS)


c, b = run("GOLD")
check("GOLD BUY is not bought on GOLD", not c and "TRADED_AS_MINI" in b, str(b))
c, b = run("SILVER")
check("SILVER BUY is not bought on SILVER", not c and "TRADED_AS_MINI" in b, str(b))
c, b = run("GOLDM")
check("GOLDM BUY is not blocked as mini", "TRADED_AS_MINI" not in b and "INSTRUMENT_NOT_ALLOWED" not in b, str(b))
settings.auto_trade_metal_mini = ""
c, b = run("GOLD")
check("setting empty -> GOLD trades as before", "TRADED_AS_MINI" not in b, str(b))
check("no broker calls", not BROKER, str(BROKER))
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
