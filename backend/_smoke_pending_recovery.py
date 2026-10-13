"""Smoke: crash-safe live order ledger, restart recovery, partial fills, and
persistence-failure handling — against a MOCKED broker only.

Nothing here contacts Angel One: the broker is a subclass of the simulator and
the source-level real-money guard is asserted unchanged. Run locally:
    cd backend && python _smoke_pending_recovery.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_pending_rec_")
os.environ["QT_LIVE_ARM_TOKEN"] = "rec-test-token"
os.environ.pop("QT_DB_URL", None)  # research store -> SQLite in the temp dir
os.environ.pop("QT_CAPITAL", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.ai import safety  # noqa: E402
from app.config import settings  # noqa: E402
from app.execution import live_control, order_ledger  # noqa: E402
from app.main import app  # noqa: E402
from app.market.simulated import SimulatedProvider  # noqa: E402
from app.models import OrderResult  # noqa: E402
from app.research.store import store as research_store  # noqa: E402
from app.state import AppState, registry  # noqa: E402

passed = failed = 0
fails: list[str] = []
TOKEN = "rec-test-token"
H = live_control.LIVE_TOKEN_HEADER
INST = "CRUDEOIL"


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        fails.append(msg)
        print(f"FAIL: {msg}")


class MockBroker(SimulatedProvider):
    def __init__(self, base):
        self.__dict__.update(base.__dict__)
        self.placed: list[tuple] = []
        self.statuses: dict[str, OrderResult | None] = {}
        self.raise_on_place = False

    def supports_trading(self) -> bool:
        return True

    def place_order(self, option_symbol, side, lots):
        if self.raise_on_place:
            raise ConnectionError("network dropped after send")
        self.placed.append((option_symbol, side, lots))
        oid = f"OID{len(self.placed)}"
        return OrderResult(ok=True, mode="live", side=side, option_symbol=option_symbol,
                           lots=lots, order_id=oid, status="ACKNOWLEDGED")

    def order_status(self, order_id):
        return self.statuses.get(order_id)


def status(oid, sym, side, lots, filled, avg, st):
    return OrderResult(ok=filled > 0, mode="live", side=side, option_symbol=sym, lots=lots,
                       filled_lots=filled, fill_premium=avg, order_id=oid, status=st)


def fresh_state(mock: MockBroker | None = None) -> AppState:
    """A new AppState = a backend restart for this instrument."""
    st = AppState(instrument=INST)
    registry._states[INST] = st
    if mock is not None:
        st.provider = mock
        st._providers[st.instrument] = mock
    return st


def ledger() -> dict:
    return order_ledger.load(INST)


def events() -> list[dict]:
    path = os.path.join(settings.data_dir, order_ledger.EVENTS_FILE)
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def poll(st: AppState) -> None:
    st._last_order_poll = 0.0
    st._poll_live_orders()


client = TestClient(app)
settings.order_fill_timeout_sec = 0.3
live_control.set_kill_switch(False, "smoke start")

st = fresh_state()
st.tick()
mock = MockBroker(st.provider)
st.provider = mock
st._providers[st.instrument] = mock
sym = mock.option_chain()[0].symbol
settings.trade_mode = "live"
ok(st.is_live, "mock broker + trade_mode=live makes the engine live")

# ---------- A: ack only -> restart -> reconcile -> operator adopt ----------
r = st.buy(sym, lots=2, confirm=True, manual=False)
ok(not r.ok and r.status == "ACKNOWLEDGED" and st.position.option_symbol is None,
   "ack without a fill opens nothing")
saved = ledger()
ok(saved.get("pending_entry", {}).get("order_id") == "OID1",
   "pending entry (broker order id) is persisted to the ledger")
kinds = [e["kind"] for e in events()]
ok("SUBMIT_INTENT" in kinds and "ACKNOWLEDGED" in kinds,
   "intent is logged before the order and the ack after it")

st = fresh_state(mock)  # restart
rs = st.recovery_status()
ok(rs["recovery_required"] and rs["pending_entry"]["order_id"] == "OID1",
   "restart restores the pending order and requires recovery")
n = len(mock.placed)
r = st.buy(sym, lots=1, confirm=True, manual=False)
ok(not r.ok and len(mock.placed) == n, "recovery blocks new live entries (no duplicate order)")

poll(st)  # broker status unavailable (None)
ok(st.recovery_status()["recovery_required"], "unavailable broker status keeps recovery blocked")

mock.statuses["OID1"] = status("OID1", sym, "BUY", 2, 1, 10.0, "PARTIAL")
poll(st)
rs = st.recovery_status()
ok(rs["saved_position"] and rs["saved_position"]["lots"] == 1
   and rs["saved_position"]["entry"] == 10.0 and not rs["saved_position"]["verified"],
   "confirmed partial fill updates the UNVERIFIED saved position at the broker price")
ok(st.position.option_symbol is None, "the saved position is NOT assumed to be held")

mock.statuses["OID1"] = status("OID1", sym, "BUY", 2, 2, 10.5, "FILLED")
poll(st)
rs = st.recovery_status()
ok(rs["saved_position"]["lots"] == 2 and abs(rs["saved_position"]["entry"] - 10.5) < 1e-6,
   "incremental fill priced from the cumulative broker average (weighted entry 10.5)")
ok(rs["pending_entry"] is None and rs["recovery_required"],
   "terminal order cleared but recovery waits for the operator (fills were seen)")

body = {"instrument": INST, "action": "adopt_position", "note": "checked broker positions"}
r = client.post("/api/order-recovery", json=body)
ok(r.status_code in (401, 403), "operator resolution requires the live-arm token")
r = client.post("/api/order-recovery", json=body, headers={H: TOKEN})
ok(r.status_code == 200 and not r.json()["recovery_required"], "authorised adopt clears recovery")
ok(TOKEN not in r.text and TOKEN not in client.get("/api/order-recovery").text,
   "the token never appears in API responses")
ok(st.position.quantity_lots == 2 and st.position.entry_premium == 10.5,
   "adopted position = broker-confirmed lots and average price")
ok(ledger().get("position", {}).get("verified") is True, "ledger now holds the verified position")

# full exit at the broker's price
mock.statuses["OID2"] = None
r = st.sell(confirm=True)
ok(not r.ok and len(mock.placed) == 2, "unfilled exit keeps the position and tracks the order")
r = st.sell(confirm=True)
ok(len(mock.placed) == 2, "repeat exit while pending sends no duplicate SELL")
mock.statuses["OID2"] = status("OID2", sym, "SELL", 2, 2, 11.0, "FILLED")
poll(st)
ok(st.position.option_symbol is None and ledger() == {},
   "exit fill booked by the read-only poll; ledger record removed")
ok(st.journal and st.journal[-1]["exit"] == 11.0, "exit is booked at the broker fill, not the LTP")

# ---------- B: crash between send and ack (outcome unknown) ----------
st = fresh_state(mock)
mock.raise_on_place = True
try:
    st.buy(sym, lots=1, confirm=True, manual=False)
except ConnectionError:
    pass
mock.raise_on_place = False
ok(ledger().get("pending_entry", {}).get("state") == "SUBMITTING",
   "an order whose send outcome is unknown stays recorded as SUBMITTING")
st = fresh_state(mock)
poll(st)
ok(st.recovery_status()["recovery_required"], "unknown submission cannot auto-clear")
r = client.post("/api/order-recovery", headers={H: TOKEN},
                json={"instrument": INST, "action": "clear_pending", "which": "entry",
                      "note": "no such order in broker order book"})
ok(r.status_code == 200 and not r.json()["recovery_required"],
   "operator clear_pending (audited) resolves it")
ok(any(e["kind"] == "OPERATOR_CLEARED_PENDING" for e in events()),
   "operator action is written to the audit log")

# ---------- C: restored order rejected with no fill -> auto-clear ----------
st = fresh_state(mock)
r = st.buy(sym, lots=1, confirm=True, manual=False)
oid = r.order_id
st = fresh_state(mock)
mock.statuses[oid] = status(oid, sym, "BUY", 1, 0, None, "REJECTED")
poll(st)
ok(not st.recovery_status()["recovery_required"] and ledger() == {},
   "restored order rejected with no fill and no saved position auto-clears")

# ---------- D: in-process partial fill + residual ----------
st = fresh_state(mock)
nxt = f"OID{len(mock.placed) + 1}"
mock.statuses[nxt] = status(nxt, sym, "BUY", 2, 1, 12.0, "PARTIAL")
r = st.buy(sym, lots=2, confirm=True, manual=False)
ok(r.ok and st.position.quantity_lots == 1 and st.position.entry_premium == 12.0,
   "partial fill opens only the filled lots at the broker price")
ok(st._pending_order and st._pending_order["order_id"] == nxt
   and st._pending_order["filled_lots"] == 1, "residual keeps the original order id + filled qty")
n = len(mock.placed)
r = st.buy(sym, lots=2, confirm=True, manual=False)
ok(not r.ok and len(mock.placed) == n, "residual still working blocks a duplicate entry")
mock.statuses[nxt] = status(nxt, sym, "BUY", 2, 2, 12.5, "FILLED")
poll(st)
ok(st.position.quantity_lots == 2 and abs(st.position.entry_premium - 12.5) < 1e-6
   and st._pending_order is None, "residual fill adds lots at a correct weighted average")

nxt = f"OID{len(mock.placed) + 1}"
mock.statuses[nxt] = status(nxt, sym, "SELL", 2, 1, 13.0, "PARTIAL")
r = st.sell(confirm=True)
ok(r.ok and st.position.quantity_lots == 1 and ledger()["position"]["lots"] == 1,
   "partial exit books the filled lot and keeps the rest open")
mock.statuses[nxt] = status(nxt, sym, "SELL", 2, 2, 13.5, "FILLED")
poll(st)
ok(st.position.option_symbol is None and st.journal[-1]["exit"] == 14.0,
   "remaining exit lot booked at its own incremental broker price (14.0)")

# ---------- E: persistence failure ----------
st = fresh_state(mock)
real_save = order_ledger.save


def broken_save(*_a, **_k):
    raise order_ledger.LedgerError("disk full (simulated)")


order_ledger.save = broken_save
n = len(mock.placed)
r = st.buy(sym, lots=1, confirm=True, manual=False)
ok(not r.ok and len(mock.placed) == n, "unwritable ledger refuses a live entry before sending")
ok(st.recovery_status()["persist_error"], "persistence failure is reported")
order_ledger.save = real_save
poll(st)
ok(st.recovery_status()["persist_error"] is None, "persistence error clears after a good write")

# ---------- F: kill switch blocks live entries immediately ----------
r = client.post("/api/kill-switch", json={"engaged": True, "reason": "smoke"})
ok(r.status_code == 200, "engaging needs no token")
n = len(mock.placed)
r = st.buy(sym, lots=1, confirm=True, manual=False)
ok(not r.ok and len(mock.placed) == n, "engaged kill switch blocks the next live entry")
ok(client.post("/api/kill-switch", json={"engaged": False}).status_code in (401, 403),
   "release without the token is refused")
ok(client.post("/api/kill-switch", json={"engaged": False},
               headers={H: TOKEN}).status_code == 200, "release with the token works")

# ---------- G: audit log + mirror idempotency ----------
evs = events()
ok(len({e["event_id"] for e in evs}) == len(evs), "every audit event has a unique id")
path = os.path.join(settings.data_dir, order_ledger.EVENTS_FILE)
with open(path, "ab") as fh:
    fh.write(b'{"torn": ')  # crash mid-append
order_ledger.record_event(INST, "SMOKE_AFTER_TORN")
with open(path, encoding="utf-8") as fh:
    last = fh.read().splitlines()[-1]
ok(json.loads(last)["kind"] == "SMOKE_AFTER_TORN", "a torn line is isolated, not merged")
rs_store = research_store()
before = rs_store.db.query_one("SELECT COUNT(*) AS n FROM order_events")["n"]
rs_store.insert_order_events(evs[:3])
rs_store.insert_order_events(evs[:3])
after = rs_store.db.query_one("SELECT COUNT(*) AS n FROM order_events")["n"]
ok(after - before == 3, "order_events insert is idempotent on event_id")
ok(rs_store.schema_version() == 3, "research store schema is v3")

# ---------- H: paper mode untouched ----------
settings.trade_mode = "paper"
st = fresh_state(mock)
n = len(mock.placed)
r = st.buy(sym, lots=1, confirm=False, manual=False)
st.sell()
ok(r.ok and r.mode == "paper" and len(mock.placed) == n and ledger() == {},
   "paper trades never call the broker or write the live ledger")
ok(safety.REAL_MONEY_EXECUTION_ENABLED is False, "real-money guard unchanged")

print(f"\n{passed} passed, {failed} failed")
if fails:
    print("FAILURES:\n- " + "\n- ".join(fails))
sys.exit(1 if failed else 0)
