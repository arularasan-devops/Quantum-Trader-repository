"""Smoke: live auto entries Angel already refused are not re-sent."""
import sys
from app.engine import reject_guard as g

fails = 0
def check(name, ok):
    global fails
    print(("PASS  " if ok else "FAIL  ") + name)
    fails += 0 if ok else 1

t = 1794800000.0
g.reset()
check("fresh name allowed", g.block_reason("NIFTY", "NFO", t) is None)
g.record_reject("NIFTY", "NFO", "Order rejected: price band", t)
check("rejected name waits", g.block_reason("NIFTY", "NFO", t + 60) is not None)
check("other name unaffected", g.block_reason("SBIN", "NFO", t + 60) is None)
check("name cooldown ends after 60 min", g.block_reason("NIFTY", "NFO", t + 3601) is None)
g.record_reject("SILVERM", "MCX",
    "RMS:Rule: Position limit including CNC exceeds ,Current:10, limit set:0 for entity", t)
check("RMS limit 0 blocks whole exchange",
      g.block_reason("CRUDEOIL", "MCX", t + 3700) is not None)
check("exchange block clears next day", g.block_reason("CRUDEOIL", "MCX", t + 86400) is None)
g.record_reject("SBIN", "NFO", "limit set:0.5 lakh", t)
check("limit 0.5 is not a zero limit", g.block_reason("ITC", "NFO", t + 10) is None)
check("affordable buy passes", g.funds_shortfall(100.0, 750, 100000.0) is None)
check("unaffordable buy refused", g.funds_shortfall(170.0, 750, 117589.0) is not None)
check("charges buffer counted", g.funds_shortfall(100.0, 1000, 100050.0) is not None)
check("unknown balance -> Angel decides", g.funds_shortfall(170.0, 750, None) is None)
check("funds reject recognised",
      g.is_funds_reject("You require Rs. 128881 funds to execute this order"))
g.reset()
g.record_reject("SBIN", "NFO", "You require Rs. 128881 funds to execute this order", t)
check("funds reject pauses every name",
      g.block_reason("ITC", "BSE", t + 60) is not None)
check("funds pause ends after 15 min", g.block_reason("ITC", "BSE", t + 901) is None)
g.reset()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
