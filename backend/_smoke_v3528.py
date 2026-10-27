"""Smoke v3.5.28: live auto-buy follows the paper rules — stand-down follows the
current settings, no live-only opening window or daily-room sizing, targets
reached on LTP or bid. Pure checks; nothing contacts Angel.
    cd backend && python _smoke_v3528.py
"""
from __future__ import annotations

import os
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_v3528_")
os.environ.pop("QT_DB_URL", None)
os.environ["QT_DATA_PROVIDER"] = "simulated"

from app.config import settings  # noqa: E402
from app.engine import account_risk as ar  # noqa: E402

passed = failed = 0


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print("PASS", msg)
    else:
        failed += 1
        print("FAIL", msg)


acct = ar.account
acct.reset()
settings.max_daily_loss = 20000.0
settings.max_consecutive_losses = 2
settings.risk_stand_down_sec = 86400.0
t = 1793109759.0
for i, pnl in enumerate((-6101.0, -5338.0, -5573.0)):
    acct.record_exit("CRUDEOIL", pnl, t + i, stopped=True)
last = t + 2
ok("standing down" in (acct.blocked(last + 600) or ""), "3 losses, 24h stand-down: blocked")
settings.risk_stand_down_sec = 900.0
ok("standing down" in (acct.blocked(last + 600) or ""), "stand-down changed to 15 min: still blocked at 10 min")
ok(acct.blocked(last + 901) is None, "stand-down changed to 15 min: released after 15 min")
settings.risk_stand_down_sec = 86400.0
ok(acct.blocked(last + 901) is not None, "back to 24h: blocked again")
settings.max_consecutive_losses = 4
ok(acct.blocked(last + 901) is None, "limit raised above the streak: released")
settings.max_consecutive_losses = 2
settings.risk_stand_down_sec = 900.0
acct.record_exit("SILVER", -1000.0, last + 1000, stopped=True)
ok("standing down" in (acct.blocked(last + 1100) or ""), "next loss stands down again for 15 min")
ok(acct.blocked(last + 1000 + 901) is None, "and releases 15 min after it")
acct.reset()

ok(ar.entry_loss_budget(0.0, -16000.0, per_trade_cap=10000.0, lock=None) == 10000.0,
   "per-trade budget = ₹10,000 cap (same as paper, no daily-room clamp)")
settings.auto_trade_slippage_buffer_pct = 1.0
ok(ar.lots_for_budget(10000.0, 650.0, 650.0 * 0.92, 100) == 1, "8% stop on a ₹650 CRUDE option: 1 lot")
ok(ar.lots_for_budget(10000.0, 650.0, 650.0 * 0.94, 100) == 2, "6% stop on a ₹650 CRUDE option: 2 lots")

src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "state.py")).read()
ok("if open_block and not eng:" in src, "opening window: live pullback engine same as paper")
ok("daily_loss_room(equity) if live" not in src, "no live-only daily-room lot clamp")
ok("tgt_px = max(prem, ltp_now)\n" in src, "targets reached on LTP or bid, live same as paper")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
