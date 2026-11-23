"""Smoke: instruments in auto_trade_cooldown_exempt skip the re-entry/post-stop wait."""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())
from app.config import settings  # noqa: E402
from app.engine import account_risk  # noqa: E402

fails = []
now = time.time()
settings.auto_trade_reentry_cooldown_sec = 3600
settings.auto_trade_post_stop_cooldown_sec = 3600
acct = account_risk.account
for inst in ("NIFTY", "SENSEX", "CRUDEOIL"):
    acct.record_exit(inst, -100.0, now - 60, stopped=True)

settings.auto_trade_cooldown_exempt = []
for inst in ("NIFTY", "SENSEX", "CRUDEOIL"):
    if not acct.instrument_blocked(inst, now):
        fails.append(f"default: {inst} should wait")

settings.auto_trade_cooldown_exempt = ["nifty", " SENSEX "]
for inst in ("NIFTY", "SENSEX"):
    if acct.instrument_blocked(inst, now):
        fails.append(f"exempt: {inst} should not wait")
if not acct.instrument_blocked("CRUDEOIL", now):
    fails.append("exempt list must not free CRUDEOIL")
if not account_risk.cooldown_exempt("NIFTY") or account_risk.cooldown_exempt("CRUDEOIL"):
    fails.append("cooldown_exempt helper")

print("FAILURES:", fails) if fails else print("cooldown exempt smoke OK")
sys.exit(1 if fails else 0)
