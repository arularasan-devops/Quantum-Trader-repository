"""Smoke: first-hour re-entry lockout (non-index instruments; NIFTY/SENSEX exempt).
No broker. Run: cd backend && python _smoke_first_hour.py"""
from __future__ import annotations

import calendar
import importlib
import os
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_first_hour_")
os.environ.pop("QT_DB_URL", None)

from app.config import settings  # noqa: E402
from app.engine import first_hour as fh  # noqa: E402

FAIL: list[str] = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {info}"))
    if not cond:
        FAIL.append(name)


def ist(y, mo, d, h, mi):
    return calendar.timegm((y, mo, d, h, mi, 0)) - 19800


check("default off", type(settings).model_fields["first_hour_lockout"].default is False)
check("off: nothing recorded", fh.record_full_exit("RELIANCE", ist(2026, 11, 23, 9, 20),
                                                   ist(2026, 11, 23, 9, 40)) is None)
settings.first_hour_lockout = True
e, x = ist(2026, 11, 23, 9, 20), ist(2026, 11, 23, 9, 40)
r = fh.record_full_exit("RELIANCE", e, x)
check("full exit in first hour arms lockout", r and r["lockout_until_ts"] == ist(2026, 11, 23, 10, 15), str(r))
check("record has entry/exit", r["first_entry_ts"] == e and r["full_exit_ts"] == x)
check("blocked at 09:41", fh.blocked("RELIANCE", ist(2026, 11, 23, 9, 41)) is not None)
check("blocked at 10:14", fh.blocked("reliance", ist(2026, 11, 23, 10, 14)) is not None)
check("free at 10:15", fh.blocked("RELIANCE", ist(2026, 11, 23, 10, 15)) is None)
r2 = fh.record_full_exit("RELIANCE", ist(2026, 11, 23, 9, 50), ist(2026, 11, 23, 10, 0))
check("second exit keeps the first record", r2["full_exit_ts"] == x)
check("other instrument not locked", fh.blocked("CRUDEOIL", ist(2026, 11, 23, 9, 45)) is None)
check("NIFTY exempt", fh.record_full_exit("NIFTY", e, x) is None
      and fh.blocked("NIFTY", ist(2026, 11, 23, 9, 45)) is None)
check("SENSEX exempt", fh.record_full_exit("SENSEX", e, x) is None)
check("exit after 10:15 does not arm",
      fh.record_full_exit("TCS", ist(2026, 11, 23, 10, 0), ist(2026, 11, 23, 10, 20)) is None)
check("exit before 09:15 does not arm",
      fh.record_full_exit("CRUDEOIL", ist(2026, 11, 23, 9, 0), ist(2026, 11, 23, 9, 10)) is None)
# restart: reload the module, record survives on disk
importlib.reload(fh)
check("survives restart", fh.blocked("RELIANCE", ist(2026, 11, 23, 9, 55)) is not None)
check("next day clean", fh.blocked("RELIANCE", ist(2026, 11, 24, 9, 30)) is None)
fh.record_full_exit("TCS", ist(2026, 11, 24, 9, 20), ist(2026, 11, 24, 9, 30))
check("old day pruned", "2026-11-23" not in fh._load())

# AppState wiring: a scale-out doesn't arm, a full exit does (paper, simulated feed)
import time  # noqa: E402

from app.state import AppState, registry  # noqa: E402

settings.trade_mode = "paper"
settings.first_hour_lockout_session_start_ist = "00:00"
settings.first_hour_lockout_until_ist = "23:59"
st = AppState(instrument="CRUDEOIL")
registry._states["CRUDEOIL"] = st
st.tick()
sym = st.provider.option_chain()[0].symbol
st.buy(sym, lots=2, confirm=False, manual=False)
st.sell(confirm=False, lots=1)
check("partial exit does not arm", fh.blocked("CRUDEOIL", time.time()) is None)
st.sell(confirm=False)
check("full exit arms (any strike of the underlying)", fh.blocked("CRUDEOIL", time.time()) is not None)

print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
