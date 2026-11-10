"""Smoke: startup PAPER-mode verification and the 80% auto-entry confidence floor.

An unrecognised QT_TRADE_MODE must fail closed (auto-trader off, enabling it
refused with an actionable message); a valid paper mode leaves it usable.
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_mode_guard_")
os.environ.pop("QT_LIVE_ARM_TOKEN", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.ai import safety  # noqa: E402
from app.config import settings  # noqa: E402
from app.execution import live_control  # noqa: E402
from app.main import app  # noqa: E402

passed = failed = 0


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        print(f"FAIL: {msg}")


client = TestClient(app)  # no lifespan: no background feed loops

# ---------------- default config is paper ----------------
ok(settings.trade_mode == "paper", "default trade_mode is paper")
ok(live_control.verify_trade_mode() == "", "paper mode verifies cleanly")
ok(client.get("/health").json().get("mode_error") is None, "/health has no mode error in paper")

# ---------------- unknown mode fails closed ----------------
settings.auto_trade_enabled = True
settings.trade_mode = "papr"
err = live_control.verify_trade_mode()
ok(bool(err) and "QT_TRADE_MODE=paper" in err, "unknown mode yields an actionable error")
ok(settings.auto_trade_enabled is False, "unknown mode switches the auto-trader off")
r = client.post("/api/auto-trade", json={"enabled": True})
ok(r.status_code == 409 and settings.auto_trade_enabled is False,
   "enabling the auto-trader is refused while the mode is unknown")
ok(client.get("/health").json().get("mode_error") == err, "/health reports the mode error")
ok(client.get("/api/settings").json()["auto_trade"].get("mode_error") == err,
   "/api/settings reports the mode error")
ok(safety.REAL_MONEY_EXECUTION_ENABLED is False, "real-money guard unchanged")

# ---------------- recovery after fixing the config ----------------
settings.trade_mode = " PAPER "
ok(live_control.verify_trade_mode() == "", "paper mode is case/space tolerant")
r = client.post("/api/auto-trade", json={"enabled": True})
ok(r.status_code == 200 and settings.auto_trade_enabled is True and r.json()["mode"] == "paper",
   "auto-trader can be enabled in paper mode")

# ---------------- 80% confidence floor ----------------
client.post("/api/auto-trade", json={"min_confidence": 70})
ok(settings.auto_trade_min_confidence == 80.0, "API cannot lower min confidence below 80%")
client.post("/api/auto-trade", json={"min_confidence": 85})
ok(settings.auto_trade_min_confidence == 85.0, "API may raise min confidence")

settings.trade_mode = "paper"
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
