"""Focused smoke for the ungated shadow book. No network, no DB, no orders.

The shadow ledger exists to settle "do the gates cost me money?", so what it
must not do is quietly lose the evidence or invent it. Each of those failure
modes is asserted directly:

* **every board BUY is recorded**, including one the gated book refused, and
  including a BUY that appears only on the position-independent market signal.
* **a refused call keeps the blocker** — the gate it would have died on, the
  stage and the code location — because a row without it cannot be attributed.
* **the gated flag is per call**, so a taken call and a refused one are
  distinguishable in the same session.
* **a BUY with no plan is recorded as unmeasurable**, never entered at an
  invented price and never silently dropped.
* **an unaffordable call is recorded with a reason**, not as a position.
* **outcomes are tracked**: targets in order, stop-first is not a win, MFE/MAE,
  realized R, timeout.
* **the costed number never flatters the gross one** — no bid/ask means null,
  not equal to the mid result.
* **a repeated call does not ladder** into an entry per tick.
* **disabled means silent**: nothing is written when the flag is off.
* **the module has no order path** and does not import the broker, the provider,
  the decision engine or the gated storage.
* **the gated path is untouched** — the shadow write runs after the gated
  auto-trader, and live arming is unchanged.

    .venv/bin/python _smoke_shadow.py
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="qt_shadow_smoke_")
os.environ["QT_DATA_DIR"] = _TMP

from app.analysis import exec_funnel, shadow_book as sb  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import (  # noqa: E402
    Decision,
    GateTrace,
    OptionQuote,
    OptionType,
    Signal,
)

settings.data_dir = _TMP
settings.shadow_book_enabled = True
settings.shadow_book_capital = 500000.0
settings.shadow_book_risk_per_trade_pct = 1.0
settings.shadow_book_max_lots = 10
settings.shadow_book_follow_minutes = 90

HERE = os.path.dirname(os.path.abspath(__file__))
# Wall clock, because the funnel stamps its refusals with the real clock and the
# blocker lookup is deliberately bounded to the current tick.
T0 = time.time()
CHECKS = 0
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(f"{label}: {detail}")


def fresh() -> None:
    """Empty ledger, empty open set — each section measures only its own writes."""
    sb.reset_for_tests()
    exec_funnel.reset()
    path = sb.ledger_path()
    if os.path.exists(path):
        os.remove(path)


def quote(premium: float, symbol: str = "NIFTY24500CE",
          bid: float | None = None, ask: float | None = None) -> OptionQuote:
    return OptionQuote(
        symbol=symbol, strike=24500.0, option_type=OptionType.CALL,
        premium=premium, iv=14.0, delta=0.52, gamma=0.001, theta=-6.0,
        vega=8.0, oi=100_000, oi_change=5_000, volume=20_000,
        bid=bid, ask=ask,
    )


def buy(*, symbol: str | None = "NIFTY24500CE", premium: float | None = 100.0,
        stop: float | None = 90.0, t1: float | None = 110.0,
        t2: float | None = 120.0, t3: float | None = 130.0,
        signal: Signal = Signal.BUY,
        market_signal: Signal | None = None,
        blockers: list[str] | None = None) -> Decision:
    return Decision(
        signal=signal, confidence=72.0, signal_strength=68.0, trade_quality="A",
        market_signal=market_signal, recommended_option=symbol, strike=24500.0,
        option_type=OptionType.CALL if symbol else None,
        current_premium=premium, stop_loss=stop,
        target1=t1, target2=t2, target3=t3,
        entry_trigger="PULLBACK", reasons=["5m trend up", "1m pullback held"],
        win_probability=0.44,
        gates=GateTrace(primary_blocker=(blockers or [None])[0],
                        blockers=blockers or []),
    )


def rows() -> list[dict]:
    """Ledger in write order (read_ledger is newest-first for the UI)."""
    return sb.read_ledger(limit=20000)[::-1]


def of(event: str) -> list[dict]:
    return [r for r in rows() if r["event"] == event]


# --- 1. every call is recorded, refused or not -------------------------------
fresh()
sb.observe("NIFTY", buy(), [quote(100.0, bid=99.0, ask=101.0)], T0,
           gated_taken=True, tick_started=T0)
sb.observe("BANKNIFTY", buy(symbol="BANKNIFTY52000CE"),
           [quote(100.0, symbol="BANKNIFTY52000CE")], T0,
           gated_taken=False, tick_started=T0)
entries = of(sb.ENTRY)
ok("a taken call is recorded", any(r["instrument"] == "NIFTY" for r in entries))
ok("a call the gated book refused is recorded too",
   any(r["instrument"] == "BANKNIFTY" for r in entries), str(entries))
ok("the gated flag is per call, not per session",
   sorted(r["gated_taken"] for r in entries) == [False, True])
ok("every entry carries the plan it was recorded on",
   all(r["symbol"] and r["entry_mid"] and r["stop"] and r["target1"]
       for r in entries))
ok("conviction and reasons are kept with the call",
   all(r["confidence"] == 72.0 and r["reasons"] for r in entries))
ok("the ledger states it is paper-only", all(r["paper_only"] for r in entries))
_nifty = [r for r in entries if r["instrument"] == "NIFTY"][0]
ok("the ask actually payable at entry is recorded beside the mid",
   _nifty["entry_ask"] == 101.0 and _nifty["entry_mid"] == 100.0)
ok("an unquoted book records no ask rather than the mid",
   [r for r in entries if r["instrument"] == "BANKNIFTY"][0]["entry_ask"] is None)

# a BUY visible only on the position-independent scan is still a call the user
# saw, and is exactly the kind the gated book skips while holding.
fresh()
sb.observe("NIFTY", buy(signal=Signal.HOLD, market_signal=Signal.BUY),
           [quote(100.0)], T0, gated_taken=False, tick_started=T0)
ok("a BUY on the market signal while holding is recorded", len(of(sb.ENTRY)) == 1)

fresh()
sb.observe("NIFTY", buy(signal=Signal.WAIT), [quote(100.0)], T0,
           gated_taken=False, tick_started=T0)
ok("a WAIT is not booked as a shadow entry", of(sb.ENTRY) == [])

# --- 2. the would-be blocker survives ----------------------------------------
fresh()
exec_funnel.blocked("EXECUTION", "REWARD_RISK", instrument="NIFTY",
                    option="NIFTY24500CE", value=1.1, threshold=1.5,
                    where="state.py:_auto_trade", reason="reward:risk too thin")
sb.observe("NIFTY", buy(blockers=["REWARD_RISK"]), [quote(100.0)], T0,
           gated_taken=False, tick_started=T0 - 5)
row = of(sb.ENTRY)[0]
ok("the gate that refused the gated book is named",
   row["would_be_blocker"] == "REWARD_RISK", str(row))
ok("the stage it died at is kept", row["would_be_blocker_stage"] == "EXECUTION")
ok("the refusal reason is kept",
   row["would_be_blocker_reason"] == "reward:risk too thin")
ok("the code location is kept so a blocker can be traced",
   row["would_be_blocker_where"] == "state.py:_auto_trade")
ok("the engine's own gate trace is kept as well",
   row["gate_primary_blocker"] == "REWARD_RISK"
   and row["gate_blockers"] == ["REWARD_RISK"])

# a refusal from an earlier tick is not this call's reason
fresh()
exec_funnel.blocked("EXECUTION", "COOLDOWN", instrument="NIFTY")
sb.observe("NIFTY", buy(), [quote(100.0)], T0 + 600,
           gated_taken=False, tick_started=T0 + 599)
ok("a stale refusal is not attributed to this call",
   of(sb.ENTRY)[0]["would_be_blocker"] is None)

# the commonest reason the gated book takes nothing is not a gate at all
fresh()
sb.observe("NIFTY", buy(), [quote(100.0)], T0,
           gated_taken=False, gated_in_position=True, tick_started=T0)
ok("a call the gated book never looked at is attributed to the open position",
   of(sb.ENTRY)[0]["would_be_blocker"] == sb.ALREADY_IN_POSITION)
ok("holding is recorded as a fact of its own, not as a gate",
   of(sb.ENTRY)[0]["gated_in_position"] is True)
fresh()
exec_funnel.blocked("EXECUTION", "REWARD_RISK", instrument="NIFTY")
sb.observe("NIFTY", buy(), [quote(100.0)], T0,
           gated_taken=False, gated_in_position=True, tick_started=T0 - 5)
ok("a real gate refusal outranks the open-position explanation",
   of(sb.ENTRY)[0]["would_be_blocker"] == "REWARD_RISK")

# the tick boundary is sub-second, so the refusal stamp has to be too, or a
# refusal from this tick reads as older than the tick that produced it
fresh()
before = time.time()
exec_funnel.blocked("EXECUTION", "SPREAD_TOO_WIDE", instrument="NIFTY")
recorded = exec_funnel.last_block("NIFTY", since=before)
ok("a refusal recorded within the same second as the tick is still attributable",
   recorded is not None and recorded["primary_blocker"] == "SPREAD_TOO_WIDE")
ok("the refusal carries an unrounded stamp, not only a whole second",
   recorded is not None and float(recorded["ts_precise"]) % 1 != 0.0)

# --- 3. a call with nothing to measure is not invented -----------------------
fresh()
sb.observe("NIFTY", buy(symbol=None, premium=None, stop=None, t1=None), [], T0,
           gated_taken=False, tick_started=T0)
skips = of(sb.SKIPPED)
ok("a BUY with no contract is recorded as unmeasurable, not dropped",
   len(skips) == 1 and skips[0]["reason"] == sb.NO_PLAN)
ok("an unmeasurable call is never booked as an entry", of(sb.ENTRY) == [])

# a call with a contract but no levels: measurable in points, not in R
fresh()
sb.observe("NIFTY", buy(stop=None, t1=None), [quote(100.0, bid=99.5, ask=100.5)],
           T0, gated_taken=False, tick_started=T0)
watch = of(sb.WATCH)
ok("a BUY the engine gave no stop is followed rather than only counted",
   len(watch) == 1 and watch[0]["reason"] == sb.NO_LEVELS)
ok("a levelless call is not booked as a sized position",
   of(sb.ENTRY) == [] and watch[0]["lots"] is None)
ok("no stop or target is invented for it",
   watch[0]["stop"] is None and watch[0]["target1"] is None)
sb.follow("NIFTY", [quote(140.0, bid=139.5, ask=140.5)], T0 + 60)
sb.follow("NIFTY", [quote(112.0, bid=111.5, ask=112.5)], T0 + 60 * 200)
end = of(sb.WATCH_END)
ok("a levelless call ends on time, since it has no level to end on",
   len(end) == 1 and end[0]["outcome"] == sb.TIMEOUT)
ok("its premium movement is measured", end[0]["net_points"] == 12.0)
ok("its peak is measured too", end[0]["mfe_points"] == 40.0)
ok("crossing the book is charged in points as well",
   end[0]["costed_points"] == 11.0)
ok("no rupee result is claimed where there was no size",
   end[0]["gross_rupees"] is None and end[0]["net_rupees"] is None)
ok("no R is claimed where there was no risk", end[0]["realized_r"] is None)

fresh()
sb.observe("NIFTY", buy(premium=100.0, stop=100.0), [quote(100.0)], T0,
           gated_taken=False, tick_started=T0)
ok("a zero-risk call cannot produce a divide-by-zero position",
   of(sb.ENTRY) == [] and of(sb.WATCH)[0]["reason"] == sb.STOP_ABOVE_ENTRY)

# the engine's commonest defect on the recorded data: an entry plan held while
# the premium moved past its own first target. Entering it would resolve as a
# TARGET the instant it opened, so it must not be booked as one.
fresh()
sb.observe("NIFTY", buy(premium=110.0, stop=95.0, t1=105.0), [quote(110.0)], T0,
           gated_taken=False, tick_started=T0)
stale = of(sb.WATCH)
ok("a plan whose target is below the entry is not entered",
   of(sb.ENTRY) == [] and len(stale) == 1)
ok("it is recorded as a target that is not above the entry",
   stale[0]["reason"] == sb.NO_UPSIDE)
ok("the plan that could not be traded is kept as printed",
   stale[0]["plan_target1"] == 105.0 and stale[0]["plan_stop"] == 95.0)
ok("no target win is claimed for it",
   all(r["outcome"] != sb.TARGET_HIT for r in of(sb.EXIT)))
sb.follow("NIFTY", [quote(108.0)], T0 + 60 * 200)
ok("it is followed in points only",
   of(sb.WATCH_END)[0]["net_points"] == -2.0
   and of(sb.WATCH_END)[0]["realized_r"] is None)
ok("the comparison names the defect that sent it there",
   sb.compare([])["levelless_calls"]["by_reason"].get(sb.NO_UPSIDE) == 1)

fresh()
_cap = settings.shadow_book_capital
settings.shadow_book_capital = 1000.0
sb.observe("NIFTY", buy(), [quote(100.0)], T0, gated_taken=False, tick_started=T0)
ok("a call the shadow pot cannot afford is recorded with the reason",
   of(sb.SKIPPED)[0]["reason"] == sb.UNAFFORDABLE)
ok("an unaffordable call opens no shadow position", sb.open_count() == 0)
settings.shadow_book_capital = _cap

# --- 4. sizing stays sane even though the call is ungated --------------------
fresh()
sb.observe("NIFTY", buy(), [quote(100.0)], T0, gated_taken=False, tick_started=T0)
ok("lots are capped by the configured ceiling",
   of(sb.ENTRY)[0]["lots"] <= settings.shadow_book_max_lots)
ok("lots are at least one when the pot can carry it",
   of(sb.ENTRY)[0]["lots"] >= 1)

# --- 5. a repeated call is recorded once, not laddered ----------------------
fresh()
for i in range(5):
    sb.observe("NIFTY", buy(), [quote(100.0 + i * 0.1)], T0 + i,
               gated_taken=False, tick_started=T0 + i)
ok("a repeated call does not open a second shadow position",
   len(of(sb.ENTRY)) == 1, str(len(of(sb.ENTRY))))
ok("only one shadow position is open on the leg", sb.open_count() == 1)
for i in range(5):
    sb.observe("NIFTY", buy(), [quote(100.0)], T0 + 120 + i,
               gated_taken=False, tick_started=T0 + 120 + i)
ok("a repeat is still recorded, so no call is lost", len(of(sb.REPEAT)) >= 1)
ok("repeats are throttled rather than written per tick",
   len(of(sb.REPEAT)) <= 2, str(len(of(sb.REPEAT))))

# --- 6. outcomes ------------------------------------------------------------
# 6a. target in order, MFE/MAE and R
fresh()
sb.observe("NIFTY", buy(), [quote(100.0, bid=99.5, ask=100.5)], T0,
           gated_taken=False, tick_started=T0)
sb.follow("NIFTY", [quote(96.0, bid=95.5, ask=96.5)], T0 + 60)     # dip, no stop
sb.follow("NIFTY", [quote(122.0, bid=121.5, ask=122.5)], T0 + 120)  # T1+T2
sb.follow("NIFTY", [quote(119.0, bid=118.5, ask=119.5)], T0 + 180)  # back to T2
ex = of(sb.EXIT)[0]
ok("a target-first call resolves TARGET", ex["outcome"] == sb.TARGET_HIT, str(ex))
ok("the targets reached are kept in the order they arrived",
   ex["targets_reached"] == ["T1", "T2"], str(ex["targets_reached"]))
ok("MFE is the best premium actually seen", ex["mfe_points"] == 22.0)
ok("MAE is the worst premium actually seen", ex["mae_points"] == -4.0)
ok("realized R is measured against the recorded risk",
   ex["realized_r"] == round(19.0 / 10.0, 3), str(ex["realized_r"]))
ok("gross rupees is the mid-to-mid result",
   ex["gross_rupees"] == round(19.0 * ex["lots"] * ex["lot_size"], 2))
ok("net rupees pays the ask and sells the bid",
   ex["net_rupees"] == round((118.5 - 100.5) * ex["lots"] * ex["lot_size"], 2),
   str(ex["net_rupees"]))
ok("crossing the book is never better than the mid result",
   ex["net_rupees"] < ex["gross_rupees"])
ok("the position is closed out of the open set", sb.open_count() == 0)

# 6b. stop before a later target is not a win
fresh()
sb.observe("NIFTY", buy(), [quote(100.0)], T0, gated_taken=False, tick_started=T0)
sb.follow("NIFTY", [quote(89.0)], T0 + 60)    # through the stop
sb.follow("NIFTY", [quote(140.0)], T0 + 120)  # rallies past every target after
ex = of(sb.EXIT)[0]
ok("a stop before a target resolves STOP", ex["outcome"] == sb.STOP_HIT)
ok("a target reached after the stop is not counted",
   ex["targets_reached"] == [], str(ex["targets_reached"]))
ok("a losing call reports negative R", (ex["realized_r"] or 0) < 0)
ok("an unquoted book leaves the costed result unknown, not equal to the gross",
   ex["net_rupees"] is None and ex["costed"] is False)

# 6c. timeout
fresh()
sb.observe("NIFTY", buy(), [quote(100.0)], T0, gated_taken=False, tick_started=T0)
sb.follow("NIFTY", [quote(103.0)], T0 + settings.shadow_book_follow_minutes * 60 + 1)
ok("a call that never resolves times out rather than staying open forever",
   of(sb.EXIT)[0]["outcome"] == sb.TIMEOUT)
ok("the follow window is bounded", sb.open_count() == 0)

# --- 7. comparison ----------------------------------------------------------
fresh()
exec_funnel.blocked("EXECUTION", "REWARD_RISK", instrument="NIFTY",
                    option="NIFTY24500CE")
sb.observe("NIFTY", buy(), [quote(100.0, bid=99.5, ask=100.5)], T0,
           gated_taken=False, tick_started=T0 - 1)
sb.follow("NIFTY", [quote(112.0, bid=111.5, ask=112.5)], T0 + 60)
sb.follow("NIFTY", [quote(109.5, bid=109.0, ask=110.0)], T0 + 120)
exec_funnel.blocked("VALIDATION", "SPREAD_TOO_WIDE", instrument="BANKNIFTY",
                    option="BANKNIFTY52000CE")
sb.observe("BANKNIFTY", buy(symbol="BANKNIFTY52000CE", premium=200.0,
                            stop=180.0, t1=220.0, t2=240.0, t3=260.0),
           [quote(200.0, symbol="BANKNIFTY52000CE", bid=190.0, ask=210.0)], T0,
           gated_taken=False, tick_started=T0 - 1)
sb.follow("BANKNIFTY", [quote(178.0, symbol="BANKNIFTY52000CE",
                              bid=170.0, ask=186.0)], T0 + 60)
cmp_all = sb.compare([{"net_pnl": 1500.0}, {"net_pnl": -800.0}])
ok("the comparison counts both books", cmp_all["shadow"]["closed"] == 2
   and cmp_all["gated"]["trades"] == 2)
ok("the gated book's own result is reported from the trades passed in",
   cmp_all["gated"]["total_rupees"] == 700.0)
ok("the calls a gate refused are isolated",
   cmp_all["refused_by_a_gate"]["closed"] == 2)
ok("the refused calls are attributed to the blocker that refused them",
   set(cmp_all["refused_by_a_gate"]["by_blocker"]) ==
   {"REWARD_RISK", "SPREAD_TOO_WIDE"},
   str(cmp_all["refused_by_a_gate"]["by_blocker"]))
_wide = cmp_all["refused_by_a_gate"]["by_blocker"]["SPREAD_TOO_WIDE"]
ok("a blocker that saved money shows a negative costed total",
   _wide["net_rupees"] < 0, str(_wide))
ok("the gross and costed totals are reported side by side, never merged",
   cmp_all["shadow"]["gross"]["total_rupees"]
   != cmp_all["shadow"]["costed"]["total_rupees"])
ok("costed coverage is reported so a partial book is visible",
   cmp_all["shadow"]["costed_coverage_pct"] == 100.0)
ok("the reading note tells the user to compare the costed total",
   "COSTED" in cmp_all["reading"])
ok("the comparison states the shadow book placed no orders",
   "paper-only" in cmp_all["safety"])
# a levelless call must not be blended into the sized book's rupee totals
sb.observe("NIFTY", buy(symbol="NIFTY24300CE", premium=150.0, stop=None, t1=None),
           [quote(150.0, symbol="NIFTY24300CE", bid=149.0, ask=151.0)], T0,
           gated_taken=False, tick_started=T0)
sb.follow("NIFTY", [quote(160.0, symbol="NIFTY24300CE", bid=159.0, ask=161.0)],
          T0 + 60 * 200)
cmp_mixed = sb.compare([{"net_pnl": 1500.0}])
ok("levelless calls are reported on their own, in points",
   cmp_mixed["levelless_calls"]["followed_to_an_end"] == 1
   and cmp_mixed["levelless_calls"]["mid_points"] == 10.0)
ok("a levelless call does not enter the sized book's trade count",
   cmp_mixed["shadow"]["closed"] == cmp_all["shadow"]["closed"])
ok("its costed points are charged the book too",
   cmp_mixed["levelless_calls"]["costed_points"] == 8.0)
fresh()
empty = sb.compare([])
ok("an empty book reports no win rate rather than 0%",
   empty["shadow"]["gross"]["win_rate_pct"] is None
   and empty["gated"]["win_rate_pct"] is None)

# --- 8. disabled means silent ----------------------------------------------
fresh()
settings.shadow_book_enabled = False
sb.observe("NIFTY", buy(), [quote(100.0)], T0, gated_taken=False, tick_started=T0)
sb.follow("NIFTY", [quote(100.0)], T0 + 60)
ok("nothing is recorded while the shadow book is disabled",
   not os.path.exists(sb.ledger_path()) or rows() == [])
settings.shadow_book_enabled = True

# --- 9. it cannot trade, and cannot break a tick ---------------------------
blob = open(os.path.join(HERE, "app", "analysis", "shadow_book.py"),
            encoding="utf-8").read()
# Docstrings *discuss* the order path they must not contain, so the code is
# checked with the prose removed rather than the prose being softened.
code = ast.parse(blob)
for _node in ast.walk(code):
    if isinstance(_node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
        _doc = ast.get_docstring(_node, clean=False)
        if _doc:
            blob = blob.replace(_doc, "")
ok("the shadow book holds no order path",
   not any(t in blob for t in ("place_order", "placeOrder", "broker",
                               "self.position", ".buy(", ".sell(",
                               "OrderResult")), blob[:0])
ok("it does not import the decision engine",
   "from app.engine" not in blob and "import app.engine" not in blob)
ok("it does not import the market provider",
   "app.market.provider" not in blob and "angelone" not in blob)
ok("it does not reach into the gated book's storage",
   "app.storage" not in blob and "from app import storage" not in blob)
ok("the gated trades are injected rather than read here",
   "def compare(gated_trades" in blob)
ok("live arming is not referenced anywhere in the shadow book",
   "auto_trade_allow_live" not in blob and "is_live" not in blob)
fresh()
sb.observe("NIFTY", buy(), [], T0, gated_taken=False, tick_started=T0)  # no chain
sb.follow("NIFTY", [], T0 + 60)
ok("a tick with no chain at all is swallowed instead of raised", True)

# --- 10. the gated path is unchanged ---------------------------------------
state_src = open(os.path.join(HERE, "app", "state.py"), encoding="utf-8").read()
i_auto = state_src.find("self._auto_trade(decision, in_pos, now)")
i_shadow = state_src.find("shadow_book.observe(")
ok("the shadow write runs after the gated auto-trader has had its turn",
   0 < i_auto < i_shadow, f"auto={i_auto} shadow={i_shadow}")
ok("nothing reads the shadow ledger back into a decision",
   "shadow_book.read_ledger" not in state_src
   and "shadow_book.compare" not in state_src)
ok("live arming still requires all three switches",
   "return bool(self.is_live and settings.auto_trade_enabled "
   "and settings.auto_trade_allow_live)" in state_src)
ok("the shadow book did not become a way to ignore the gates",
   "QT_AUTO_BUY_IGNORE_GATES" not in state_src
   and "ignore_gates" not in state_src)

# --- 11. the ledger is append-only ----------------------------------------
fresh()
sb.observe("NIFTY", buy(), [quote(100.0)], T0, gated_taken=False, tick_started=T0)
first = open(sb.ledger_path(), encoding="utf-8").readline()
sb.follow("NIFTY", [quote(112.0)], T0 + 60)
sb.follow("NIFTY", [quote(109.5)], T0 + 120)
ok("an earlier row is byte-identical after later writes",
   open(sb.ledger_path(), encoding="utf-8").readline() == first)
ok("every line is a self-contained json record",
   all(isinstance(json.loads(ln), dict)
       for ln in open(sb.ledger_path(), encoding="utf-8") if ln.strip()))
ok("read_ledger returns the newest row first",
   sb.read_ledger(limit=10)[0]["event"] == sb.EXIT)
ok("a session filter only returns that session",
   all(r["session"] == sb.read_ledger(limit=1)[0]["session"]
       for r in sb.read_ledger(session=sb.read_ledger(limit=1)[0]["session"])))

print()
if FAILS:
    print(f"SHADOW BOOK SMOKE FAILED ({len(FAILS)} of {CHECKS})")
    for f in FAILS:
        print(" -", f)
    raise SystemExit(1)
print(f"SHADOW BOOK SMOKE PASSED ({CHECKS} checks)")
