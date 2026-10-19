"""Smoke v3.5: per-trade loss budget, lock-room sizing, 30k lock minimum,
signal repeat counting, trade price path. SQLite temp DB; no broker."""
import os
import tempfile

os.environ.setdefault("QT_TRADE_MODE", "paper")
from app.config import settings  # noqa: E402
from app.engine import account_risk as ar  # noqa: E402
from app.journal.store import JournalStore  # noqa: E402
from app.research.db import Database  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    fails += 0 if ok else 1


check("default lock minimum is ₹30,000", settings.daily_profit_target == 30000.0,
      settings.daily_profit_target)
check("no lock below ₹30k", ar.day_lock_level(29_000) is None)
check("₹40k high locks ₹30k", ar.day_lock_level(40_000) == 30_000.0, ar.day_lock_level(40_000))
check("₹60k high locks ₹45k", ar.day_lock_level(60_000) == 45_000.0, ar.day_lock_level(60_000))

settings.auto_trade_slippage_buffer_pct = 1.0
b = ar.entry_loss_budget(0.0, 5_000.0, per_trade_cap=10_000.0)
check("before the lock: budget = ₹10k cap", b == 10_000.0, b)
# 16 Oct PE: entry 757.2 stop 696 lot 100 -> 1 lot under a ₹10k cap (was 6)
n = ar.lots_for_budget(10_000.0, 757.2, 696.0, 100)
check("wide-stop CRUDEOIL PE sized to 1 lot", n == 1, n)
# day high 40k, equity 36k: lock 30k, room 6k, 50% -> ₹3k
b = ar.entry_loss_budget(40_000.0, 36_000.0, per_trade_cap=10_000.0)
check("after the lock: budget = 50% of the room", abs(b - 3_000.0) < 1e-6, b)
n = ar.lots_for_budget(b, 465.0, 427.2, 100)
check("22:07-type trade (₹3,780/lot risk) is skipped", n == 0, n)
check("equity at the lock leaves no budget",
      ar.entry_loss_budget(40_000.0, 30_000.0, per_trade_cap=10_000.0) == 0.0)
check("books (no cap) unlimited before the lock", ar.entry_loss_budget(0.0, 0.0) is None)
check("lots never from a zero budget", ar.lots_for_budget(0.0, 100.0, 90.0, 50) == 0)

tmp = tempfile.mkdtemp()
st = JournalStore(Database(f"sqlite:///{tmp}/j.db"))
JournalStore(st.db)  # second init: additive columns are idempotent
eid = st.record_signal({"instrument": "CRUDEOIL", "ts": 1_790_000_000.0, "signal": "BUY",
                        "outcome": "SKIPPED", "reason": "SPREAD_TOO_WIDE",
                        "option_symbol": "X9500CE"})
st.touch_signal(eid, 1_790_000_060.0)
st.touch_signal(eid, 1_790_000_120.0)
row = st.db.query_one("SELECT repeat_count, last_seen_ts FROM jr_signals WHERE event_id = ?", (eid,))
check("repeated signal bumps one row", row["repeat_count"] == 3 and row["last_seen_ts"] == 1_790_000_120.0, row)
check("signals CSV carries repeat_count", "repeat_count" in st.signals_csv().splitlines()[0])

st.open_trade({"trade_id": "t1", "instrument": "CRUDEOIL", "mode": "paper",
               "entry_ts": 1_790_000_000.0, "entry_price": 100.0, "lots": 1})
for i, (ltp, bid, ask) in enumerate([(101, 100.5, 101.5), (103, 102.5, 103.5)]):
    st.record_trade_event({"trade_id": "t1", "instrument": "CRUDEOIL", "ts": 1_790_000_005.0 + 5 * i,
                           "kind": "PRICE", "price": ltp, "detail": {"bid": bid, "ask": ask}})
d = st.trade_detail("t1")
check("price path stored and returned", len(d["path"]) == 2 and d["path"][1]["bid"] == 102.5, d["path"])
check("price samples kept out of the event list", all(e["kind"] != "PRICE" for e in d["events"]))

print("ALL PASS" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
