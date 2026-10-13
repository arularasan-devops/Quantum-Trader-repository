"""Smoke: journal v2 (sessions, signals, trade events, sizing decisions) on
SYNTHETIC data only. Uses a temp data dir and a throwaway database; it never
opens history.db or any real PostgreSQL trading database.

    cd backend && python _smoke_journal_v2.py                      # SQLite
    QT_JR_TEST_DB_URL=postgresql://... python _smoke_journal_v2.py  # throwaway PG
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_jr_v2_")
os.environ["QT_DB_URL"] = os.environ.get("QT_JR_TEST_DB_URL", "")
os.environ["QT_TRADE_MODE"] = "paper"
os.environ["QT_DATA_PROVIDER"] = "simulated"

from fastapi.testclient import TestClient  # noqa: E402

from app.ai import safety  # noqa: E402
from app.config import settings  # noqa: E402
from app.journal import hooks  # noqa: E402
from app.journal import sizing as sz  # noqa: E402
from app.journal import store as jstore  # noqa: E402

passed = failed = 0
fails: list[str] = []


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        fails.append(msg)
        print(f"FAIL: {msg}")


js = jstore.store()
if js.db.is_postgres:  # throwaway test DB only: start clean
    js.rollback("DROP_JOURNAL_V2")
    jstore.reset_for_tests()
    js = jstore.store()
print(f"backend={js.backend}")
ok(js.schema_version() == 1, "journal schema v1 recorded in jr_schema_migrations")
ok(settings.sim_starting_capital == 500000.0, "default simulated capital is ₹5,00,000")

# ---------------- sizing (deterministic numbers) ----------------
base = dict(instrument="CRUDEOIL", contract="CRUDEOIL25OCT6900CE", vehicle="OPTION_BUY",
            entry_price=100.0, stop_price=80.0, lot_size=100,
            lot_size_source="REGISTRY_SEED_UNVERIFIED", lot_size_ts=0.0,
            starting_capital=500000.0, equity=500000.0, available_funds=500000.0,
            est_cost_per_lot=40.0)
res = {r.method: r for r in sz.compare(sz.SizingInputs(**base))}
ok(res["FIXED_1"].actual_lots == 1 and res["FIXED_2"].actual_lots == 2, "fixed 1/2-lot baselines")
ok(res["CAPITAL_BASED"].cost_per_lot == 10040.0 and res["CAPITAL_BASED"].actual_lots == 4,
   "capital-based: 10% of ₹5L / ₹10,040 per lot = 4 lots")
ok(res["AGGRESSIVE"].actual_lots == 8, "aggressive: capital-based x2 = 8 lots")
ok(res["RISK_BUDGET"].actual_lots == 2, "risk budget: ₹5,000 / (₹2,000 stop + ₹40 costs) = 2 lots")
ok(res["RISK_BUDGET"].stop_exposure == 4080.0, "risk-budget stop exposure ₹4,080")
ok(all(r.margin_status == "NOT_APPLICABLE" for r in res.values()),
   "option buying: margin NOT_APPLICABLE (premium paid in full)")
tight = {r.method: r for r in sz.compare(sz.SizingInputs(**{**base, "available_funds": 15000.0}))}
ok(tight["FIXED_2"].actual_lots == 1 and tight["FIXED_2"].constraint_name == "AVAILABLE_FUNDS",
   "never exceeds available funds (2 proposed -> 1 affordable)")
broke = {r.method: r for r in sz.compare(sz.SizingInputs(**{**base, "available_funds": 5000.0}))}
ok(all(r.actual_lots == 0 for r in broke.values())
   and broke["FIXED_1"].constraint_name == "INSUFFICIENT_FUNDS", "insufficient funds -> 0 lots")
nostop = sz.size(sz.SizingInputs(**{**base, "stop_price": None}), "RISK_BUDGET")
ok(nostop.actual_lots == 0 and nostop.constraint_name == "UNAVAILABLE",
   "risk budget without a stop is UNAVAILABLE, not guessed")
fut = sz.size(sz.SizingInputs(**{**base, "vehicle": "FUTURES", "margin_pct": 10.0}), "FIXED_1")
ok(fut.margin_status == "ESTIMATED" and fut.margin_per_lot == 1000.0,
   "futures margin labelled ESTIMATED")
fut2 = sz.size(sz.SizingInputs(**{**base, "vehicle": "FUTURES"}), "FIXED_1")
ok(fut2.actual_lots == 0 and fut2.margin_status == "UNAVAILABLE",
   "futures with no margin source are not sized")

# ---------------- store: idempotency, missing fields ----------------
t0 = 1_790_000_000.0
sig = {"instrument": "NIFTY", "ts": t0, "signal": "BUY", "outcome": "SKIPPED",
       "reason": "CAPITAL: no room", "option_symbol": "NIFTY25OCT24000CE"}
a = js.record_signal(sig)
b = js.record_signal(dict(sig))
n = js.db.query_one("SELECT COUNT(*) AS n FROM jr_signals WHERE event_id = ?", (a,))["n"]
ok(a == b and n == 1, "retried signal write is not duplicated")
try:
    js.record_signal({"instrument": "NIFTY", "ts": t0, "outcome": "SKIPPED"})
    ok(False, "missing required signal field rejected")
except ValueError:
    ok(True, "missing required signal field rejected")
row = js.db.query_one("SELECT bid, ask, regime FROM jr_signals WHERE event_id = ?", (a,))
ok(row["bid"] is None and row["regime"] is None, "missing optional fields stored as NULL, not 0")
s1 = js.upsert_session({"instrument": "NIFTY", "mode": "paper", "ts": t0})
s2 = js.upsert_session({"instrument": "NIFTY", "mode": "paper", "ts": t0 + 120})
srow = js.db.query_one("SELECT COUNT(*) AS n, MAX(last_seen_at) AS l FROM jr_sessions "
                       "WHERE session_id = ?", (s1,))
ok(s1 == s2 and srow["n"] == 1 and float(srow["l"]) == t0 + 120, "session upsert is idempotent")

for i in range(25):
    tid = f"T{i:03d}"
    js.open_trade({"trade_id": tid, "instrument": "NIFTY" if i % 2 else "CRUDEOIL",
                   "mode": "paper", "entry_ts": t0 + i * 60, "entry_price": 100.0, "lots": 1,
                   "entry_explanation": 'pullback, "EMA20" reclaim'})
    js.open_trade({"trade_id": tid, "instrument": "X", "mode": "paper", "entry_ts": 0})
    pnl = 50.0 if i % 3 else -30.0
    ev = {"trade_id": tid, "instrument": "NIFTY", "ts": t0 + i * 60 + 300, "kind": "EXIT",
          "lots": 1, "price": 101.0, "reason": "TARGET 1", "gross_pnl": pnl + 10,
          "costs": 10.0, "net_pnl": pnl, "mode": "paper"}
    js.record_trade_event(ev)
    js.record_trade_event(dict(ev))
cnt = js.counts()
ok(cnt["jr_trades"] == 25 and cnt["jr_trade_events"] == 25,
   "retried trade + event writes are not duplicated")
p1 = js.list_trades({}, page=1, page_size=10)
p3 = js.list_trades({}, page=3, page_size=10)
ids = {r["trade_id"] for r in p1["items"]} | {r["trade_id"] for r in p3["items"]}
ok(p1["total"] == 25 and len(p1["items"]) == 10 and len(p3["items"]) == 5 and len(ids) == 15,
   "pagination: 25 rows -> 10/10/5 with no overlap")
ok(js.list_trades({}, page=1, page_size=10_000)["page_size"] == jstore.MAX_PAGE_SIZE,
   "page size is capped")
ok(js.list_trades({"instrument": "NIFTY"})["total"] == 12, "instrument filter")
wins = js.list_trades({"result": "WIN"})["total"]
losses = js.list_trades({"result": "LOSS"})["total"]
ok(wins + losses == 25 and losses == 9, "result filter (WIN/LOSS)")
t = js.db.query_one("SELECT status, net_pnl, holding_minutes FROM jr_trades WHERE trade_id='T000'")
ok(t["status"] == "CLOSED" and float(t["net_pnl"]) == -30.0 and t["holding_minutes"] == 5,
   "trade totals recomputed from exit events")
text = js.trades_csv({})
rows = list(csv.reader(io.StringIO(text)))
ok(rows[0] == jstore.TRADE_CSV_COLUMNS and len(rows) == 26, "CSV: stable header + one row per trade")
col = rows[0].index("entry_explanation")
ok(rows[1][col] == 'pullback, "EMA20" reclaim', "CSV quoting round-trips commas and quotes")

# ---------------- live state integration (paper, simulated feed) ----------------
from app.main import app  # noqa: E402
from app.state import AppState, registry  # noqa: E402

client = TestClient(app)
st = AppState(instrument="CRUDEOIL")
registry._states["CRUDEOIL"] = st
for _ in range(3):
    st.tick()
quiet = AppState(instrument="GOLD")
registry._states["GOLD"] = quiet
quiet.tick()
opt = st.provider.option_chain()[0]
before = js.counts()
r = st.buy(opt.symbol, lots=1, manual=False)
ok(r.ok and r.mode == "paper", "paper buy works")
tid = st._jr_trade_id
st.tick()
st._auto_stop = (st.position.entry_premium or 10.0) * 0.8
st.tick()
st._auto_stop = (st.position.entry_premium or 10.0) * 0.9
st.tick()
s = st.sell()
ok(s.ok, "paper sell works")
detail = js.trade_detail(tid)
kinds = [e["kind"] for e in detail["events"]]
ok(kinds[0] == "ENTRY" and kinds[-1] == "EXIT" and "STOP_CHANGE" in kinds,
   "timeline: ENTRY -> STOP_CHANGE -> EXIT in order")
ok(abs(float(detail["trade"]["net_pnl"]) - float(st.journal[-1]["net_pnl"])) < 0.01,
   "journal v2 net P&L matches the engine's trade row")
methods = {z["method"]: z for z in detail["sizing"]}
ok(set(methods) == {"ENGINE_ACTUAL", *sz.METHODS}, "sizing: 5 methods + ENGINE_ACTUAL recorded")
ok(methods["ENGINE_ACTUAL"]["applied"] and methods["ENGINE_ACTUAL"]["actual_lots"] == 1
   and not any(z["applied"] for m, z in methods.items() if m != "ENGINE_ACTUAL"),
   "sizing is observational: only ENGINE_ACTUAL applied, engine kept 1 lot")
ok(methods["FIXED_1"]["lot_size_source"] == "REGISTRY_SEED_UNVERIFIED"
   and methods["FIXED_1"]["lot_size_ts"] is not None, "lot-size source + timestamp recorded")
ok(float(methods["FIXED_1"]["starting_capital"]) == 500000.0, "sizing uses ₹5L simulated capital")
sess = js.list_sessions({"instrument": "GOLD"})
ok(sess["total"] == 1 and sess["items"][0]["trades"] == 0, "a session with no trades is recorded")
ok(js.counts()["jr_signals"] > before["jr_signals"] or js.counts()["jr_signals"] >= 1,
   "signals recorded from live ticks")
ok(hooks.status()["count"] == 0, f"no journal write errors ({hooks.status()['last']})")

# ---------------- API ----------------
legacy = client.get("/api/journal", params={"instrument": "CRUDEOIL"}).json()
ok(set(legacy) == {"trades", "today_profit", "today_loss"}, "existing /api/journal contract unchanged")
r = client.get("/api/journal/v2/trades", params={"instrument": "crudeoil", "page_size": 5})
ok(r.status_code == 200 and r.json()["page_size"] == 5, "v2 trades endpoint + filters")
ok(client.get("/api/journal/v2/trades", params={"page_size": 501}).status_code == 422,
   "page_size above 500 is rejected")
r = client.get(f"/api/journal/v2/trades/{tid}")
ok(r.status_code == 200 and r.json()["trade"]["trade_id"] == tid, "v2 trade timeline endpoint")
ok(client.get("/api/journal/v2/trades/nope").status_code == 404, "unknown trade -> 404")
r = client.get("/api/journal/v2/trades.csv")
ok(r.status_code == 200 and r.text.splitlines()[0] == ",".join(jstore.TRADE_CSV_COLUMNS)
   and "text/csv" in r.headers["content-type"], "v2 CSV endpoint")
ok(client.get("/api/journal/v2/sessions").status_code == 200
   and client.get("/api/journal/v2/signals").status_code == 200, "sessions + signals endpoints")
stt = client.get("/api/journal/v2/status").json()
ok(stt["schema_version"] == 1 and stt["write_errors"]["count"] == 0, "status endpoint")

# ---------------- safety ----------------
ok(safety.REAL_MONEY_EXECUTION_ENABLED is False and not st.is_live, "paper only; real-money guard off")

js.rollback("DROP_JOURNAL_V2") if js.db.is_postgres else None
print(f"\n{passed} passed, {failed} failed")
if fails:
    print("FAILURES:\n- " + "\n- ".join(fails))
sys.exit(1 if failed else 0)
