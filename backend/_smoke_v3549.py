"""v3.5.49: LIVE never auto-buys PULLBACK alone; PULLBACK + MOMENTUM still buys. Paper unchanged. No broker."""
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())

from app.config import settings  # noqa: E402
from app.models import Signal  # noqa: E402
from app.state import AppState  # noqa: E402

FAIL: list[str] = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    if not ok:
        FAIL.append(name)


settings.auto_trade_enabled = True
settings.auto_trade_allow_live = True
settings.auto_trade_instruments = ""
settings.auto_trade_strategy_gate = True
settings.auto_trade_allowed_triggers = ["SUPERTREND", "IGNITION", "BREAKOUT_RETEST", "MOMENTUM"]
settings.auto_trade_allowed_regimes = ["TRENDING"]


def gate(live, trig, trigs):
    AppState.is_live = property(lambda s: live)
    eng = AppState(instrument="CRUDEOIL")
    eng._auto_live_armed = lambda: live
    eng._live_squareoff_due = lambda now: False
    eng._last_market_status = "TRENDING"
    eng._auto_last_action = ""
    d = SimpleNamespace(signal=Signal.BUY, recommended_option="CRUDEOIL8600PE", current_premium=250.0,
                        stop_loss=230.0, target1=270.0, option_type=None,
                        entry_trigger=trig, entry_triggers=trigs)
    try:
        eng._auto_trade(d, False, 0)
    except Exception:
        pass  # anything past the strategy gate is not under test
    return "strategy needs" in (eng._auto_last_action or "")


check("default blocks PULLBACK in live", settings.auto_trade_live_blocked_triggers == ["PULLBACK"])
check("live: PULLBACK + MOMENTUM passes the gate", not gate(True, "PULLBACK", ["PULLBACK", "MOMENTUM"]))
check("live: PULLBACK alone is skipped", gate(True, "PULLBACK", ["PULLBACK"]))
settings.auto_trade_allowed_triggers = ["PULLBACK", "MOMENTUM"]
check("live: PULLBACK alone skipped even if listed as allowed", gate(True, "PULLBACK", ["PULLBACK"]))
check("paper: PULLBACK alone allowed when listed", not gate(False, "PULLBACK", ["PULLBACK"]))
settings.auto_trade_allowed_triggers = ["SUPERTREND", "IGNITION", "BREAKOUT_RETEST", "MOMENTUM"]
check("live: MOMENTUM still passes the gate", not gate(True, "MOMENTUM", ["MOMENTUM"]))
check("live: SUPERTREND (+PULLBACK) still passes", not gate(True, "SUPERTREND", ["SUPERTREND", "PULLBACK"]))
check("paper: PULLBACK (+MOMENTUM) unchanged, passes", not gate(False, "PULLBACK", ["PULLBACK", "MOMENTUM"]))
settings.auto_trade_live_blocked_triggers = []
settings.auto_trade_allowed_triggers = ["PULLBACK"]
check("setting emptied -> live PULLBACK alone passes when listed", not gate(True, "PULLBACK", ["PULLBACK"]))
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
