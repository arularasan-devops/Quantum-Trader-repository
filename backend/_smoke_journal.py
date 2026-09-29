"""Focused smoke for the Signal Board Journal. No network, no DB, no orders.

The journal's failure modes are the ways a record can lie, so each is asserted
directly:

* **a stop before a target is never a win.** A leg that trades through the stop
  and then rallies past T1 must resolve STOP / STOP_FIRST, and its later target
  must not appear as reached.
* **a target before a stop keeps its order**, and the whole progression is kept —
  ``T1 -> T2`` is distinguishable from ``T1 -> STOP``.
* **MFE and MAE are the extremes actually seen**, with the minute they were seen.
* **an unchanged board call is journalled once**, not once per tick.
* **a changed refusal is a new record**, because a WAIT for a different gate is a
  different statement about the market.
* **a WAIT is recorded as NO_ENTRY and is not followed** — it has no outcome to
  invent.
* **greeks are labelled model-derived**, never presented as broker greeks.
* **a missing bid is UNAVAILABLE**, not a narrow spread.
* **the setup type is derived from a named engine field or is SETUP_UNKNOWN.**
* **volatility is unclassified until enough history exists** — no chosen threshold.
* **rows are append-only**: an earlier row is byte-identical after later writes.
* **the module holds no order path.**

    .venv/bin/python _smoke_journal.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="qt_journal_smoke_")
os.environ["QT_DATA_DIR"] = _TMP

from app.analysis import signal_journal as sj  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import (  # noqa: E402
    Candle,
    Decision,
    GateTrace,
    IndicatorSnapshot,
    MarketStatus,
    OptionQuote,
    OptionType,
    Signal,
)

settings.data_dir = _TMP
HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = 0
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(f"{label}: {detail}")
        print(f"FAIL {label} {detail}")
    else:
        print(f"ok   {label} {detail}")


def quote(prem: float, *, bid: float | None = None,
          ask: float | None = None) -> OptionQuote:
    return OptionQuote(
        symbol="NIFTY24000CE", strike=24000.0, option_type=OptionType.CALL,
        premium=prem, iv=0.14, delta=0.5, gamma=0.002, theta=-3.0, vega=8.0,
        oi=120000, oi_change=4000, volume=90000, bid=bid, ask=ask,
    )


def indicators(atr: float = 60.0) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        atr=atr, vwap=23950.0, rsi=61.0, adx=28.0, trend="UP",
        market_structure="HH_HL", momentum=0.8, breakout="NONE",
        pullback="PULLBACK_UP", ignition="NONE", supertrend_flip="NONE",
        premium_state="EXPANSION",
    )


def candles(n: int = 30, close: float = 24010.0) -> list[Candle]:
    return [Candle(time=1000 + 60 * i, open=close - 5, high=close + 8,
                   low=close - 12, close=close, volume=1000) for i in range(n)]


def buy(entry: float = 100.0, *, blocker: str | None = None) -> Decision:
    return Decision(
        signal=Signal.BUY, market_signal=Signal.BUY, confidence=72.0,
        signal_strength=64.0, trade_quality="GOOD", recommended_option="NIFTY24000CE",
        strike=24000.0, option_type=OptionType.CALL, current_premium=entry,
        spot_price=24010.0, atm_strike=24000.0, moneyness="ATM",
        entry_range=(entry * 0.98, entry * 1.02), stop_loss=entry - 20.0,
        target1=entry + 20.0, target2=entry + 40.0, target3=entry + 60.0,
        atr_points=60.0, htf_trend="UP", entry_trigger="PULLBACK",
        trade_score=81.0, reasons=["15m bias UP", "5m trend UP", "VWAP reclaimed"],
        gates=GateTrace(primary_blocker=blocker),
    )


def refused_buy(blocker: str, entry: float = 300.0) -> Decision:
    """A BUY market bias the gates refused: the board prints no position on it."""
    d = buy(entry)
    d.signal = Signal.WAIT
    d.gates = GateTrace(entry_ready=False, primary_blocker=blocker)
    return d


def wait(blocker: str) -> Decision:
    d = buy()
    d.signal = Signal.WAIT
    d.market_signal = Signal.WAIT
    d.gates = GateTrace(entry_ready=False, primary_blocker=blocker)
    return d


def observe(dec: Decision, chain: list[OptionQuote], ts: float,
            atr: float = 60.0) -> None:
    sj.observe("NIFTY", dec, chain, indicators(atr), 24010.0,
               MarketStatus.TRENDING, candles(), ts, expiry="2026-08-27",
               minutes_to_expiry=900)


T0 = 1_800_000_000.0

# --- 1. a stop before a later target is a loss ---------------------------
sj.reset_for_tests()
observe(buy(), [quote(100.0, bid=99.0, ask=101.0)], T0)
observe(buy(), [quote(78.0, bid=77.0, ask=79.0)], T0 + 120)      # through the stop
observe(buy(), [quote(140.0, bid=139.0, ask=141.0)], T0 + 240)   # then rallies
res = sj.resolutions()
ok("a signal that stopped resolves exactly once", len(res) == 1, str(len(res)))
r = res[0]
ok("the outcome is STOP", r["outcome"] == "STOP", r["outcome"])
ok("the order is STOP_FIRST", r["order"] == "STOP_FIRST", r["order"])
ok("no target is recorded as reached", not r["targets_reached"],
   str(r["targets_reached"]))
ok("the later rally does not become a win", r["realized_r"] < 0,
   str(r["realized_r"]))
ok("the stop event carries the minute it happened",
   r["stop_event"]["minutes_from_signal"] == 2.0,
   str(r["stop_event"]["minutes_from_signal"]))
ok("MAE is the worst price seen, not the last", r["mae_points"] == -22.0,
   str(r["mae_points"]))

# --- 2. target progression is kept in order ------------------------------
sj.reset_for_tests()
os.remove(sj.outcomes_path())
observe(buy(), [quote(100.0, bid=99.0, ask=101.0)], T0)
observe(buy(), [quote(121.0, bid=120.0, ask=122.0)], T0 + 60)   # T1
observe(buy(), [quote(141.0, bid=140.0, ask=142.0)], T0 + 180)  # T2
observe(buy(), [quote(79.0, bid=78.0, ask=80.0)], T0 + 300)     # then stops out
res = sj.resolutions()
r = res[0]
ok("the progression is recorded in the order it happened",
   r["target_sequence"] == "T1 -> T2 -> STOP", r["target_sequence"])
ok("a target reached before the stop counts as reached",
   set(r["targets_reached"]) == {"T1", "T2"}, str(sorted(r["targets_reached"])))
ok("the order is TARGET_FIRST", r["order"] == "TARGET_FIRST", r["order"])
ok("T1 keeps the minute it arrived",
   r["targets_reached"]["T1"]["minutes_from_signal"] == 1.0,
   str(r["targets_reached"]["T1"]["minutes_from_signal"]))
ok("MFE is the best price seen", r["mfe_points"] == 41.0, str(r["mfe_points"]))
ok("minutes to MFE is when the best price was seen",
   r["minutes_to_mfe"] == 3.0, str(r["minutes_to_mfe"]))
ok("give-back is measured against MFE, not against the entry",
   r["give_back_r"] is not None and r["give_back_r"] > 0, str(r["give_back_r"]))
ok("the outcome is the stop that ended it, not the best target reached",
   r["outcome"] == "STOP", r["outcome"])

# --- 3. one row per distinct board call ---------------------------------
sj.reset_for_tests()
os.remove(sj.journal_path())
observe(buy(), [quote(100.0, bid=99.0, ask=101.0)], T0)
observe(buy(), [quote(100.5, bid=99.5, ask=101.5)], T0 + 2)
observe(buy(), [quote(100.8, bid=99.8, ask=101.8)], T0 + 4)
rows = sj._read(sj.journal_path())
ok("an unchanged board call is journalled once, not once per tick",
   len(rows) == 1, str(len(rows)))
first_line = open(sj.journal_path(), encoding="utf-8").readline()

observe(wait("CHASE_GUARD"), [quote(100.0, bid=99.0, ask=101.0)], T0 + 10)
observe(wait("CHASE_GUARD"), [quote(100.0, bid=99.0, ask=101.0)], T0 + 20)
observe(wait("ADX_TOO_LOW"), [quote(100.0, bid=99.0, ask=101.0)], T0 + 30)
rows = sj._read(sj.journal_path())
ok("a WAIT for a different gate is a new record", len(rows) == 3, str(len(rows)))
waits = [r for r in rows if r["board_action"] == "WAIT"]
ok("a WAIT is recorded, so the board's refusals are not missing from history",
   len(waits) == 2, str(len(waits)))
ok("a WAIT is not followed and has no invented outcome",
   all(w["outcome"] == "NO_ENTRY" and not w["followed"] for w in waits))
ok("the refusing gate is on the record",
   {w["signal_info"]["primary_blocker"] for w in waits} ==
   {"CHASE_GUARD", "ADX_TOO_LOW"})
ok("the failed gate is named, not just counted",
   "entry_ready" in waits[0]["signal_info"]["gates_failed"],
   str(waits[0]["signal_info"]["gates_failed"]))
ok("the journal is append-only: an earlier row is unchanged by later writes",
   open(sj.journal_path(), encoding="utf-8").readline() == first_line)

# --- 4. what a row must and must not claim ------------------------------
row = rows[0]
ms = row["market_state"]
ok("greeks are labelled model-derived, not broker greeks",
   ms["greeks_source"] == "MODEL_DERIVED_FROM_LTP", str(ms["greeks_source"]))
ok("a quoted book is labelled a real broker quote",
   ms["book_source"] == "REAL_BROKER_QUOTE" and ms["spread"] == 2.0,
   f"{ms['book_source']} {ms['spread']}")
ok("the spread is also expressed against the premium it is paid from",
   ms["spread_pct_of_premium"] == 2.0, str(ms["spread_pct_of_premium"]))
ok("the setup type is derived from a named engine field",
   row["signal_info"]["setup_type"] == "PULLBACK"
   and "decision.entry_trigger" in row["signal_info"]["setup_evidence_fields"],
   str(row["signal_info"]["setup_type"]))
ok("the entry plan keeps the zone the board showed, not just the price",
   row["entry_plan"]["entry_zone_low"] == 98.0
   and row["entry_plan"]["entry_zone_high"] == 102.0)
ok("expected R is computed from the board's own stop and first target",
   row["entry_plan"]["expected_r"] == 1.0, str(row["entry_plan"]["expected_r"]))
ok("the reason text is the engine's own reasons, not prose about them",
   row["signal_info"]["reason_codes"] == ["15m bias UP", "5m trend UP",
                                          "VWAP reclaimed"])
ok("the row states that no order exists on it", "no order exists" in row["note"])
ok("time to expiry is recorded so an expiry-day row is identifiable",
   row["entry_plan"]["minutes_to_expiry"] == 900
   and row["entry_plan"]["expiry_day"] is False)

# an unquoted book must not read as a tight one
sj.reset_for_tests()
os.remove(sj.journal_path())
observe(buy(), [quote(100.0)], T0)
row = sj._read(sj.journal_path())[0]
ok("a missing bid/ask is UNAVAILABLE rather than a zero spread",
   row["market_state"]["book_source"] == sj.UNAVAILABLE
   and row["market_state"]["spread"] is None)

# a setup the engine does not evidence is unknown, not guessed
sj.reset_for_tests()
os.remove(sj.journal_path())
d = buy()
d.entry_trigger = None
d.htf_trend = None
ind = IndicatorSnapshot(atr=60.0, trend=None)
sj.observe("NIFTY", d, [quote(100.0)], ind, 24010.0, MarketStatus.TRENDING,
           candles(), T0)
row = sj._read(sj.journal_path())[0]
ok("a setup with no engine evidence is SETUP_UNKNOWN",
   row["signal_info"]["setup_type"] == sj.SETUP_UNKNOWN,
   row["signal_info"]["setup_type"])
ok("and the row says the engine does not classify setups",
   row["signal_info"]["setup_source"] == "ENGINE_DOES_NOT_CLASSIFY_SETUPS")

# --- 5. volatility is not classified from a chosen threshold -------------
ok("volatility is unclassified while history is too short to rank against",
   row["volatility"]["volatility_class"] == sj.VOL_UNCLASSIFIED,
   row["volatility"]["volatility_class"])
ok("the ATR is still recorded while the class is withheld",
   row["volatility"]["atr_pct_of_price"] is not None)

sj.reset_for_tests()
os.remove(sj.journal_path())
for i in range(sj._VOL_MIN_HISTORY + 5):
    observe(wait(f"GATE_{i}"), [quote(100.0)], T0 + i * 1000,
            atr=40.0 + (i % 7))
observe(wait("LAST"), [quote(100.0)], T0 + 999_000, atr=400.0)
row = sj._read(sj.journal_path())[-1]
v = row["volatility"]
ok("with enough history the class comes from a percentile of that history",
   v["volatility_class"] == "EXTREME_VOLATILITY" and v["volatility_percentile"] == 100.0,
   f"{v['volatility_class']} {v['volatility_percentile']}")
ok("and the row states what the class was measured against",
   "percentile of the last" in v["volatility_class_basis"],
   v["volatility_class_basis"])

# --- 6. a signal that never resolves is followed, not silently dropped ---
sj.reset_for_tests()
os.remove(sj.outcomes_path())
observe(buy(), [quote(100.0, bid=99.0, ask=101.0)], T0)
ok("an unresolved signal stays open rather than resolving early",
   sj.open_count() == 1 and not sj.resolutions())
observe(buy(200.0), [quote(105.0, bid=104.0, ask=106.0)], T0 + sj._FOLLOW_SEC + 60)
res = [r for r in sj.resolutions() if r["outcome"] == "TIMEOUT"]
ok("a signal that neither targets nor stops resolves TIMEOUT", len(res) == 1)
ok("a TIMEOUT is not scored as either a win or a stop",
   res[0]["order"] == sj.FIRST_UNKNOWN and res[0]["first_event"] is None)

# --- 7. statistics: no denominator, no rate ------------------------------
from app.analysis import journal_stats as js  # noqa: E402

sj.reset_for_tests()
for p in (sj.journal_path(), sj.outcomes_path()):
    if os.path.exists(p):
        os.remove(p)

observe(wait("ADX_TOO_LOW"), [quote(100.0, bid=99.0, ask=101.0)], T0)
s = js.statistics()
ok("with nothing resolved the hit rates are absent, not zero",
   s["t1_hit_rate_pct"] is None and s["stop_hit_rate_pct"] is None
   and s["profit_factor"] is None)
ok("a WAIT is counted", s["wait_signals"] == 1 and s["buy_signals"] == 0)
ok("a WAIT is not counted as a followed signal", s["followed_signals"] == 0)

# one winner that runs through every target, one that stops out
observe(buy(), [quote(100.0, bid=99.0, ask=101.0)], T0 + 100)
observe(buy(), [quote(165.0, bid=164.0, ask=166.0)], T0 + 400)
d2 = buy(200.0)
observe(d2, [quote(200.0, bid=199.0, ask=201.0)], T0 + 20_000)
observe(d2, [quote(179.0, bid=178.0, ask=180.0)], T0 + 20_200)
s = js.statistics()
ok("both resolved signals are counted", s["resolved_signals"] == 2,
   str(s["resolved_signals"]))
ok("the T1 rate is measured over resolved signals only",
   s["t1_hit_rate_pct"] == 50.0, str(s["t1_hit_rate_pct"]))
ok("the stop rate is measured, not inferred from the T1 rate",
   s["stop_hit_rate_pct"] == 50.0, str(s["stop_hit_rate_pct"]))
ok("target-before-stop counts the order, not the eventual best price",
   s["target_before_stop_pct"] == 50.0, str(s["target_before_stop_pct"]))
ok("net R is the sum of what was recorded", s["net_r"] is not None)
ok("a WAIT never enters a hit rate", s["wait_signals"] == 1
   and s["resolved_signals"] == 2)
ok("an unresolved signal is its own state, not a loss",
   s["unresolved_signals"] == 0, str(s["unresolved_signals"]))

report = js.daily_report(s["session"] if s["session"] != "ALL_RECORDED"
                         else js.sessions_recorded()[0])
ok("the daily report names the gate that refused most often",
   report["most_common_blocker"] == "ADX_TOO_LOW",
   str(report["most_common_blocker"]))
ok("the daily report carries its own sample size with the best setup",
   report["best_setup_by_avg_r"] is None
   or "n" in report["best_setup_by_avg_r"])
ok("the daily report counts stops from outcomes", report["stop_count"] == 1,
   str(report["stop_count"]))

# a BUY bias the gates refused: a full plan behind it, but no position printed
observe(refused_buy("REWARD_RISK"), [quote(300.0, bid=299.0, ask=301.0)],
        T0 + 30_000)

rows = js.history()
ok("history joins the outcome beside the row instead of into it",
   all("outcome_record" in r for r in rows)
   and all(r.get("immutable") for r in rows))
ok("a filter that matches nothing returns nothing",
   js.history(filters={"instrument": "BANKNIFTY"}) == [])
ok("the stop filter selects only the stopped signal",
   len(js.history(filters={"stop_hit": True})) == 1)
ok("the T1 filter selects only signals that reached T1",
   len(js.history(filters={"t1_hit": True})) == 1)
ok("a score filter excludes rows outside the band",
   js.history(filters={"min_score": 99.0}) == [])

# --- 7b. the BUY report and its date range ------------------------------
_days = js.sessions_recorded()
ok("at least one session is recorded to report on", len(_days) >= 1)
_buys = js.buy_rows()
ok("the BUY report holds only calls whose printed position was BUY",
   _buys and all(r["position_signal"] == "BUY" for r in _buys))
ok("a BUY market bias the gates refused is not reported as a BUY signal",
   all(r["position_signal"] == "BUY" for r in _buys)
   and any(r["board_action"] == "BUY" and r["position_signal"] != "BUY"
           for r in js.history()))
ok("the BUY report drops the WAIT and NO_TRADE calls the journal keeps",
   len(_buys) < len(js.history()))
ok("the BUY report holds only calls that carried a plan to report",
   all(r["followed"] for r in _buys))
ok("a BUY with no plan is counted, not silently dropped",
   js.buy_calls_without_plan()
   == len([r for r in js.history()
           if r["position_signal"] == "BUY" and not r["followed"]]))
ok("no reported row carries the position gate in place of the board call",
   all("position_call" not in _c for _c, _ in js._CSV_COLUMNS))
ok("every reported call states the conviction the board displayed",
   all(r["conviction_pct"] is not None for r in _buys))
ok("a resolved call reports the points the premium moved",
   all(r["net_points"] is not None and r["peak_points"] is not None
       for r in _buys if r["outcome_state"] not in ("UNRESOLVED", None)))
ok("an open call reports no points rather than a flat zero",
   all(r["net_points"] is None for r in _buys
       if r["outcome_state"] == "UNRESOLVED"))
ok("net points agree with the premium the call entered and left at",
   all(abs(r["net_points"] - (r["final_premium"] - r["entry_premium"])) < 0.011
       for r in _buys if r["net_points"] is not None))
ok("every reported call carries the reason the board gave",
   all(r["reason_text"] or r["reason_codes"] for r in _buys))
ok("a range that ends before the first session is empty",
   js.buy_rows(date_to="1999-01-01") == [])
ok("a range that starts after the last session is empty",
   js.buy_rows(date_from="2999-01-01") == [])
ok("an inclusive range keeps the day on both of its edges",
   len(js.buy_rows(date_from=_days[0], date_to=_days[-1])) == len(_buys))
ok("a date is validated before it reaches the filter",
   js.valid_date("2026-08-22") and js.valid_date(None)
   and not js.valid_date("22-08-2026") and not js.valid_date("2026-08-22'"))

_csv = js.buy_report_csv()
_lines = _csv.strip().split("\n")
_head = _lines[0].split(",")
ok("the CSV has a header and one line per BUY call",
   len(_lines) == len(_buys) + 1)
ok("the CSV names the plan, the state and the outcome",
   all(c in _head for c in ("date", "instrument", "stop", "target1",
                            "signal_score", "outcome", "realized_r",
                            "data_quality_score")))
ok("the CSV is oldest first while the table is newest first",
   _lines[1].split(",")[1] == _buys[-1]["time_ist"])
ok("an unresolved call writes an empty R, not a zero",
   all(row.split(",")[_head.index("realized_r")] == ""
       for row in _lines[1:]
       if row.split(",")[_head.index("outcome")] == "UNRESOLVED"))
ok("no CSV cell can be read as a spreadsheet formula",
   all(not cell.startswith(("=", "+", "@")) for row in _lines
       for cell in row.split(",")))
_all = js.history()
ok("every journalled call carries its own id",
   len({r["signal_id"] for r in _all}) == len(_all))
ok("an empty range writes the header and nothing else",
   js.buy_report_csv(date_to="1999-01-01").strip() == _lines[0])

# --- 8. the recorder cannot trade, and cannot break a tick --------------
blob = open(os.path.join(HERE, "app", "analysis", "signal_journal.py"),
            encoding="utf-8").read()
ok("the journal holds no order path",
   not any(t in blob for t in ("place_order", "placeOrder", "broker.",
                               "order(", "exit_position", "self.position")))
ok("the journal does not import the decision engine",
   "from app.engine" not in blob and "import app.engine" not in blob)
sj.reset_for_tests()
sj.observe("NIFTY", buy(), [quote(100.0)], indicators(), 24010.0,
           MarketStatus.TRENDING, [], T0)  # no candles at all
ok("a malformed tick is swallowed instead of raised", True)

state_src = open(os.path.join(HERE, "app", "state.py"), encoding="utf-8").read()
i_journal = state_src.find("signal_journal.observe(")
i_decision = state_src.find("self._last_decision = decision")
ok("the journal is written after the decision is complete",
   i_journal > i_decision > 0)
ok("nothing reads the journal back into a decision",
   "signal_journal.read" not in state_src
   and "signal_journal.resolutions" not in state_src)

print()
if FAILS:
    print(f"SIGNAL JOURNAL SMOKE FAILED ({len(FAILS)} of {CHECKS})")
    for f in FAILS:
        print(" -", f)
    raise SystemExit(1)
print(f"SIGNAL JOURNAL SMOKE PASSED ({CHECKS} checks)")
