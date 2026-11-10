"""Smoke: Reports tab API — BUY-signal list, summary and CSV from journal v2.

Uses a throwaway SQLite research DB (or QT_JR_TEST_DB_URL for PostgreSQL).
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import time

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_sig_report_")
os.environ["QT_DB_URL"] = os.environ.get("QT_JR_TEST_DB_URL", "")
os.environ["QT_TRADE_MODE"] = "paper"
os.environ["QT_DATA_PROVIDER"] = "simulated"

from fastapi.testclient import TestClient  # noqa: E402

from app.journal import store as jstore  # noqa: E402
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


jstore.reset_for_tests()
js = jstore.store()
t0 = time.time() - 3600
rows = [
    ("NIFTY", "BUY", "EXECUTED", "entered", "PULLBACK", "TRENDING", 86.0),
    ("NIFTY", "BUY", "SKIPPED", "STRATEGY_TRIGGER: needs PULLBACK", "MOMENTUM", "TRENDING", 82.0),
    ("CRUDEOIL", "BUY", "SKIPPED", "STRATEGY_TRIGGER: needs PULLBACK", "BREAKOUT", "TRENDING", 81.0),
    ("CRUDEOIL", "BUY", "SKIPPED", "STRATEGY_REGIME: needs TRENDING", "PULLBACK", "RANGING", 84.0),
    ("GOLD", "BUY", "NOT_EXECUTABLE", "no executable two-sided quote", "PULLBACK", "TRENDING", 88.0),
    ("GOLD", "WAIT", "FAILED_CONDITIONS", "confidence below threshold", None, "RANGING", 40.0),
]
for i, (inst, sig, outcome, reason, setup, regime, conf) in enumerate(rows):
    js.record_signal({"instrument": inst, "ts": t0 + i * 120, "signal": sig, "outcome": outcome,
                      "reason": reason, "setup": setup, "regime": regime, "confidence": conf,
                      "mode": "paper", "option_symbol": f"{inst}TEST{i}CE",
                      "bid": 10.0, "ask": 10.5, "stop": 8.0, "target1": 12.0})
# a duplicate write of the same event must not add a row
js.record_signal({"instrument": "NIFTY", "ts": t0, "signal": "BUY", "outcome": "EXECUTED",
                  "reason": "entered", "setup": "PULLBACK", "regime": "TRENDING",
                  "confidence": 86.0, "mode": "paper", "option_symbol": "NIFTYTEST0CE"})

c = TestClient(app)
lst = c.get("/api/journal/v2/signals", params={"signal": "BUY", "page_size": 50}).json()
ok(lst["total"] == 5, f"5 BUY signals listed (got {lst['total']})")
ok(lst["items"][0]["instrument"] == "GOLD", "newest first")

s = c.get("/api/journal/v2/signals/summary", params={"signal": "BUY"}).json()
ok(s["total"] == 5, "summary total matches list")
ok(s["by_outcome"] == {"SKIPPED": 3, "EXECUTED": 1, "NOT_EXECUTABLE": 1},
   f"by_outcome counts ({s['by_outcome']})")
ok(s["by_setup"].get("PULLBACK") == 3 and s["by_regime"].get("RANGING") == 1,
   "by_setup / by_regime counts")
top = s["top_skip_reasons"]
ok(top and top[0] == {"reason": "STRATEGY_TRIGGER: needs PULLBACK", "count": 2},
   "most frequent skip reason first")
ok(all(r["reason"] != "entered" for r in top), "executed entries are not counted as skip reasons")
ok(s["first_ts_iso"] is not None and s["last_ts_iso"] >= s["first_ts_iso"], "time span reported")

f = c.get("/api/journal/v2/signals/summary",
          params={"signal": "BUY", "instrument": "crudeoil"}).json()
ok(f["total"] == 2, "summary honours instrument filter (case-insensitive)")

r = c.get("/api/journal/v2/signals.csv", params={"signal": "BUY", "outcome": "SKIPPED"})
ok(r.status_code == 200 and r.headers["content-type"].startswith("text/csv"), "CSV served")
ok("journal_signals.csv" in r.headers.get("content-disposition", ""), "CSV download filename")
recs = list(csv.DictReader(io.StringIO(r.text)))
ok(len(recs) == 3 and {x["outcome"] for x in recs} == {"SKIPPED"}, "CSV honours outcome filter")
ok(recs and recs[0]["stop"] == "8.0" and recs[0]["ask"] == "10.5", "CSV carries levels and quote")
ok(all(x["reason"] for x in recs), "every skipped row has a reason")

empty = c.get("/api/journal/v2/signals/summary", params={"instrument": "NOPE"}).json()
ok(empty["total"] == 0 and empty["by_outcome"] == {} and empty["first_ts_iso"] is None,
   "empty filter returns zeros, not an error")

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("All checks passed!")
