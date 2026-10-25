"""Smoke v3.5.15: the UI Live Auto-Buy switch only arms/disarms live. It must
not change the paper strategy (v4 trail/stop settings, balance sizing), must
need the live token, must refuse while the kill switch is engaged, and
switching it off must leave the paper bot running. No broker is touched."""
from __future__ import annotations

import os
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_v3515_")
os.environ["QT_LIVE_ARM_TOKEN"] = "smoke-token"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.execution import live_control  # noqa: E402
from app.main import app  # noqa: E402
from app.market import angelone  # noqa: E402

settings.live_arm_token = "smoke-token"
angelone.available_cash = lambda ttl=30.0: 9_000_000.0
H = {"X-QT-Live-Token": "smoke-token"}
fails: list[str] = []


def ok(c: bool, m: str) -> None:
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


c = TestClient(app)
strategy_keys = ["auto_trade_ride_trail", "auto_trade_pre_t1_trail", "auto_trade_pre_t1_trail_arm_profit_pct",
                 "auto_trade_breakeven_after_arm", "auto_trade_quick_scalp_lots", "auto_trade_max_stop_pct",
                 "auto_trade_momentum_entry", "auto_trade_safe_sizing", "auto_trade_min_confidence"]
before = {k: getattr(settings, k) for k in strategy_keys}
settings.auto_trade_enabled = True

ok(c.post("/api/auto-buy", json={"on": True}).status_code == 401, "on without token -> 401")
c.post("/api/kill-switch", json={"engaged": True, "reason": "smoke"})
r = c.post("/api/auto-buy", json={"on": True}, headers=H)
ok(r.status_code == 409, "on while kill switch engaged -> 409")
r = c.post("/api/kill-switch", json={"engaged": False}, headers=H)
ok(r.status_code == 200 and not live_control.kill_switch_engaged(), "kill switch released with token")
r = c.post("/api/auto-buy", json={"on": True}, headers=H)
ok(r.status_code == 200 and r.json()["on"], "authorised on -> on=true")
ok(settings.auto_trade_enabled and settings.auto_trade_allow_live, "bot enabled and live armed")
ok(not settings.auto_trade_use_live_balance, "sizing stays on QT_CAPITAL (no balance sizing)")
ok({k: getattr(settings, k) for k in strategy_keys} == before, "paper strategy settings untouched")
ok(settings.auto_trade_live_max_order_value <= settings.capital, "order ceiling never above capital")
ok(r.json()["live_active"] == (settings.trade_mode.lower() == "live"), "live_active follows QT_TRADE_MODE")
r = c.post("/api/auto-buy", json={"on": False})
ok(r.status_code == 200 and not r.json()["on"], "off needs no token -> on=false")
ok(not settings.auto_trade_allow_live and settings.auto_trade_enabled, "off disarms live, paper bot keeps running")
print("ALL V3.5.15 CHECKS PASSED" if not fails else f"V3.5.15 FAILED: {fails}")
