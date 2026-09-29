"""Phase 47 smoke — the properties that keep a running P&L screen honest.

A board that prints money is the most dangerous thing in this repository, so
every check here exists because its absence would make the number on screen
flatter the research, or has already done so somewhere in this project:

* a leg marked at its own entry, at a mid, or at a traded print, any of which
  turns an unpriceable call into a flat or a profitable one. The mark must be
  the executable side and nothing else;
* an unmarkable call dropped from the list, which makes a tally of 6 marked
  legs look like a session of 6 calls;
* a round trip not charged, or charged from a basis the row does not name —
  the exact defect that ruined the Phase 42 holdout;
* a mark taken from before the call opened, which prices a leg on its own past;
* a mark taken from another session, which is the overnight gap Phase 34 had to
  have removed from its resolver;
* admission reaching into the future: the call board must price what Phase 46
  already admitted and never re-admit an instant knowing its outcome;
* a duplicate snapshot appending a second version of the same moment;
* an order, broker or execution module reachable from any of it — walked
  transitively, because a one-level grep passes a helper that imports one;
* Phase 41-44 moved, Phase 44 armed, or the production signal path touched.

    .venv/bin/python _smoke_phase47.py
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import tempfile
import time
from pathlib import Path

from app.config import settings
from app.research import phase45, phase46, phase47
from app.research.phase17 import quality, schema
from app.research.phase17 import service as p17service
from app.research.phase35 import book
from app.research.phase35 import store as p35store
from app.research.phase41 import freeze as p41freeze
from app.research.phase42 import freeze as p42freeze
from app.research.phase43 import freeze as p43freeze
from app.research.phase44 import freeze as p44freeze
from app.research.phase44 import store as p44store
from app.research.phase44 import switch as p44switch
from app.research.phase45 import evaluator, ranges
from app.research.phase45 import freeze as p45freeze
from app.research.phase45 import store as p45store
from app.research.phase46 import freeze as p46freeze
from app.research.phase46 import overlay
from app.research.phase46 import service as p46service
from app.research.phase46 import store as p46store
from app.research.phase47 import mark as mark_mod
from app.research.phase49 import events as p49events
from app.research.phase47 import service
from app.research.phase47 import store as store47

PASS = 0
FAIL: list[str] = []

FROZEN_41 = "34fc64a299d29760"
FROZEN_42 = "b67709af902c8bff"
FROZEN_43 = "e45c3e72ad37f864"
FROZEN_44 = "95d393256ed1f505"
FROZEN_45 = "a3b0b9b0b321ee47"

MINUTE = 60.0

FORBIDDEN = (
    "order", "broker", "execution", "smartapi", "angel", "kite", "trade_api",
    "place_order", "autobot", "executor",
)

# Fields that exist only after a leg is over. Admission may not read one, and
# on this board neither may the marker: it prices a leg on a quote, not on what
# the leg turned out to do.
FUTURE_FIELDS = (
    "mfe_pct", "mae_pct", "giveback_pct", "resolved_ts", "outcome",
    "exit_reason", "realised_pct", "t1_hit", "sl_hit",
)


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


# --------------------------------------------------------------------------
# Fixtures, built through Phase 45's evaluator and Phase 46's adapter — never
# by hand. A call assembled from a literal would prove the marker works on
# input the system never produces.
# --------------------------------------------------------------------------
def quote(
    vehicle: str,
    *,
    instrument: str = "NIFTY",
    bid: float | None = 100.0,
    ask: float | None = 101.0,
    delta: float | None = 0.5,
    dq: str = quality.EXACT,
    symbol: str | None = None,
    strike: float | None = 24000.0,
) -> schema.Quote:
    return schema.Quote(
        instrument=instrument, vehicle=vehicle,
        symbol=symbol or f"NIFTY26SEP{vehicle}",
        strike=None if vehicle == phase45.FUTURES else strike,
        expiry="2026-09-24", days_to_expiry=13,
        bid=bid, ask=ask, bid_size=750, ask_size=1200,
        premium=None if bid is None or ask is None else (bid + ask) / 2.0,
        lot_size=50, delta=delta, underlying_price=24010.0,
        moneyness=schema.ATM, source="ANGELONE_WS",
        feed_age_ms=120.0, book_age_ms=140.0, data_quality=dq,
    )


def observation(
    *,
    obs_id: str = "obs-1",
    ts: float,
    ce: schema.Quote | None = None,
) -> schema.Observation:
    return schema.Observation(
        observation_id=obs_id, signal_ts=ts, capture_ts=ts + 0.4,
        instrument="NIFTY", family="INDEX", candidate_class=schema.BUY,
        direction="LONG", selected_vehicle=phase45.CE,
        session="2026-09-14",
        selected=ce if ce is not None else quote(phase45.CE),
        opposite=quote(phase45.PE, delta=-0.48, symbol="NIFTY26SEPPE",
                       strike=23900.0),
        futures=quote(phase45.FUTURES, bid=24008.0, ask=24009.0, delta=1.0,
                      symbol="NIFTY26SEPFUT"),
        plan=schema.Plan(lot_size=50, expected_move_points=40.0),
        context=schema.MarketContext(
            atr_points=90.0, regime="TREND", session_period="MORNING",
            market_signal="BUY",
        ),
    )


def live_range(ts: float) -> ranges.LiveRanges:
    live = ranges.LiveRanges()
    for i in range(12, 0, -1):
        live.note("NIFTY", ts - i * MINUTE, 24000.0 + (i % 3) * 60.0)
    return live


def overlay_rows(obs: schema.Observation, live: ranges.LiveRanges) -> list[dict]:
    """Phase 46 rows for one instant, exactly as the writer would build them."""
    fingerprint = p46freeze.definition()
    rows = evaluator.evaluate(
        obs,
        trailing_range=live.before(obs.instrument, obs.signal_ts),
        definition=p45freeze.definition(),
        ingest_ts=time.time(),
    )
    return [overlay.build(r, definition=fingerprint) for r in rows]


def raw_store(path: str, quotes: list[dict]) -> None:
    """A Phase 35 raw store holding later quotes for the fixture contracts."""
    con = p35store.connect(path)
    try:
        for i, q in enumerate(quotes):
            con.execute(
                "INSERT OR REPLACE INTO raw_quote "
                "(obs_id, vehicle, ts, symbol, bid, ask, traded, evidence) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"later-{i}", q["vehicle"], q["ts"], q["symbol"],
                 q.get("bid"), q.get("ask"), q.get("traded"),
                 q.get("evidence", "MEASURED_EXECUTABLE")),
            )
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------------------
# Static helpers
# --------------------------------------------------------------------------
def imports_of(module_name: str, seen: set[str]) -> set[str]:
    if module_name in seen:
        return seen
    seen.add(module_name)
    module = sys.modules.get(module_name)
    if module is None:
        return seen
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, TypeError, SyntaxError):
        return seen
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    imports_of(alias.name, seen)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app."):
            base = node.module or ""
            imports_of(base, seen)
            for alias in node.names:
                imports_of(f"{base}.{alias.name}", seen)
    return seen


def fields_read(module: object) -> set[str]:
    """Only dictionary reads: row.get("x") and row["x"].

    Deliberately narrower than names_read: an attribute called book.net_pct is
    arithmetic this board is supposed to do, while call.get("net_pct") would be
    it reading a result off a row it is meant to be computing.
    """
    names: set[str] = set()
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                names.add(node.slice.value)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            names.add(node.args[0].value)
    return names


def names_read(module: object) -> set[str]:
    names: set[str] = set()
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                names.add(node.slice.value)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            names.add(node.args[0].value)
    return names


def source_of(module: object) -> str:
    return inspect.getsource(module)


def p44_armed() -> bool:
    path = p44store.db_path()
    if not os.path.exists(path):
        return False
    con = p45store.open_read_only(path)
    try:
        return bool(p44switch.state(con).get("may_record"))
    finally:
        con.close()


def call(**over: object) -> dict:
    """A minimal journalled call, for the arithmetic checks only."""
    base = {
        "event_id": "e1", "obs_id": "obs-1", "session": "2026-09-14",
        "decision_ts": 1000.0, "instrument": "NIFTY", "vehicle": phase45.CE,
        "contract": "NIFTY26SEPCE", "direction": "LONG",
        "entry_side": "ASK", "entry_price": 100.0,
        "measured_cost_points": 2.0, "modelled_cost_points": 5.0,
    }
    base.update(over)
    return base


def main() -> int:  # noqa: C901 - one flat list of properties reads better
    # ==================================================== the mark arithmetic
    priced = mark_mod.mark(call(), {"ts": 1200.0, "bid": 110.0, "ask": 112.0})
    ok(priced["state"] == phase47.MARKED, "a call with a later book is MARKED")
    ok(priced["mark_side"] == book.BID and priced["mark_price"] == 110.0,
       "a long option is marked at the BID, never the ask and never the mid")
    ok(priced["entry_side"] == "ASK" and priced["entry_price"] == 100.0,
       "and was entered at the ASK recorded at the instant")
    ok(priced["gross_pct"] == 10.0,
       "the gross is the move from the ask paid to the bid available")
    ok(priced["net_pct"] == 8.0,
       "and the net charges the round trip once: 10% less 2 points on 100")
    ok(priced["net_pct"] < priced["gross_pct"],
       "so the net is never better than the gross")
    ok(priced["cost_basis"] == phase47.MEASURED_COST,
       "a leg with a measured round trip says so")
    ok(mark_mod.mark(
        call(measured_cost_points=None), {"ts": 1200.0, "bid": 110.0, "ask": 112.0},
    )["cost_basis"] == phase47.MODELLED_COST,
       "and one charged on the modelled round trip says that instead, so a "
       "column cannot mix two cost bases silently — the defect that ruined "
       "the Phase 42 holdout")
    ok(mark_mod.mark(
        call(measured_cost_points=None, modelled_cost_points=None),
        {"ts": 1200.0, "bid": 110.0, "ask": 112.0},
    )["state"] == phase47.UNMARKABLE_NO_COST,
       "a leg with no round trip at all is unmarkable, not a gross result")

    short_fut = mark_mod.mark(
        call(vehicle=phase45.FUTURES, direction="SHORT", entry_price=24000.0,
             entry_side="BID", measured_cost_points=10.0),
        {"ts": 1200.0, "bid": 23900.0, "ask": 23910.0},
    )
    ok(short_fut["mark_side"] == book.ASK,
       "a short futures leg is marked at the ASK it would have to pay")
    ok(short_fut["gross_pct"] > 0,
       "and a fall in price is a gain for it, not a loss")

    loser = mark_mod.mark(call(), {"ts": 1200.0, "bid": 99.0, "ask": 101.0})
    ok(loser["net_pct"] == -3.0,
       "a leg whose bid sits below its entry ask is negative after cost, "
       "which is the majority case the research predicts")
    flatish = mark_mod.mark(call(), {"ts": 1200.0, "bid": 100.0, "ask": 101.0})
    ok(flatish["net_pct"] == -2.0,
       "and a leg marked back at its own entry still loses its round trip — "
       "there is no free exit on this board")

    # ==================================================== what cannot be marked
    no_quote = mark_mod.mark(call(), None)
    ok(no_quote["state"] == phase47.UNMARKABLE_NO_LATER_QUOTE,
       "a contract with no later quote is UNMARKABLE, not flat")
    ok(no_quote["net_pct"] is None and no_quote["mark_price"] is None,
       "and carries no net and no price")
    ok(no_quote["reason"],
       "with a reason naming the capture rather than the call")
    ok(mark_mod.mark(call(entry_price=None), {"ts": 1200.0, "bid": 110.0})[
        "state"] == phase47.UNMARKABLE_NO_ENTRY,
       "an instant that recorded no executable entry opens no priceable call")
    no_bid = mark_mod.mark(call(), {"ts": 1200.0, "bid": None, "ask": 112.0})
    ok(no_bid["state"] == phase47.UNMARKABLE_NO_BOOK,
       "a one-sided book at the mark is UNMARKABLE_NO_TWO_SIDED_BOOK")
    ltp = mark_mod.mark(
        call(), {"ts": 1200.0, "bid": None, "ask": None, "traded": 130.0})
    ok(ltp["state"] == phase47.UNMARKABLE_NO_BOOK and ltp["net_pct"] is None,
       "and a traded print is refused as a mark — an LTP is a fill somebody "
       "else got, and marking a leg at one is the substitution every cost "
       "decision in this project was made to avoid")
    ok("traded" in str(ltp["reason"]).lower() or "print" in str(ltp["reason"]),
       "the refusal says so rather than reading as a missing quote")

    # ==================================================== staleness is reported
    fresh = mark_mod.mark(call(), {"ts": 2000.0, "bid": 110.0, "ask": 112.0},
                          now=2010.0)
    stale = mark_mod.mark(call(), {"ts": 2000.0, "bid": 110.0, "ask": 112.0},
                          now=2000.0 + phase47.STALE_MARK_SEC + 30.0)
    ok(fresh["stale_mark"] is False and fresh["mark_age_sec"] == 10.0,
       "a fresh mark reports its age")
    ok(stale["stale_mark"] is True and stale["state"] == phase47.MARKED,
       "a stale mark is still shown, and flagged, rather than hidden")
    ok(stale["net_pct"] == fresh["net_pct"],
       "the arithmetic is the same — only the confidence in it differs")

    # ==================================================== the tally
    rows = [
        mark_mod.mark(call(event_id="a"), {"ts": 1200.0, "bid": 110.0, "ask": 111.0}),
        mark_mod.mark(call(event_id="b"), {"ts": 1200.0, "bid": 95.0, "ask": 96.0}),
        mark_mod.mark(call(event_id="c"), None),
    ]
    tally = mark_mod.tally(rows)
    ok(tally["calls"] == 3 and tally["marked"] == 2 and tally["unmarkable"] == 1,
       "the tally separates calls from marked calls, so a thin capture cannot "
       "read as a quiet session")
    ok(tally["unmarkable_states"][phase47.UNMARKABLE_NO_LATER_QUOTE] == 1,
       "and attributes the unmarkable one to a named state")
    ok(tally["net_pct_total"] == 8.0 + (-7.0),
       "the running net sums only the legs that could be priced")
    ok(tally["winners"] == 1 and tally["losers"] == 1,
       "winners and losers are counted on the net, after cost")
    ok(tally["win_rate_pct"] == 50.0, "the win rate is over marked legs only")
    ok(tally["profit_factor"] == round(8.0 / 7.0, 3),
       "profit factor is gains over losses")
    ok(mark_mod.tally([rows[0]])["profit_factor"] is None,
       "an arm with no losing leg reports no ratio rather than an infinity "
       "that reads like an edge")
    ok(tally["cost_bases"] == {phase47.MEASURED_COST: 2},
       "and the tally publishes which cost basis its priced legs were charged")
    ok(tally["net_is"].startswith("SUM_OF_PER_LEG_NET_PERCENTAGES"),
       "the total is labelled a sum of per-leg percentages, because the legs "
       "are unsized and overlapping and it is not a portfolio return")
    ok(mark_mod.tally([])["net_pct_total"] is None,
       "an empty board has no net, rather than a zero that looks like a "
       "flat session")
    ok(mark_mod.drawdown([1.0, -2.0, 0.5]) == -2.0
       and mark_mod.drawdown([]) is None,
       "drawdown is the deepest peak-to-trough fall of the cumulative net, "
       "and unmeasured on an empty arm")

    # ==================================================== end to end
    ts = time.time() - 600.0
    live = live_range(ts)
    built = overlay_rows(observation(ts=ts), live)
    by_vehicle = {str(r["vehicle"]): r for r in built}
    supported = [r for r in built if r["overlay_state"] == phase46.SUPPORTED]
    supported_options = [
        r for r in supported if r["vehicle"] in (phase45.CE, phase45.PE)]
    ok(supported_options,
       "the fixture instant produces at least one SUPPORTED option row, so "
       "the board has something to admit")

    prev_dir = settings.data_dir
    prev_raw = p35store.db_path
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        raw_path = os.path.join(tmp, "raw.db")
        p35store.db_path = lambda: raw_path  # type: ignore[assignment]
        service.reset()
        p46service.reset()
        try:
            con = p46store.connect(p46store.db_path())
            p46store.insert_events(con, built)
            con.close()
            later = ts + 300.0
            raw_store(raw_path, [
                {"vehicle": v, "symbol": by_vehicle[v]["contract"],
                 "ts": later, "bid": 120.0, "ask": 121.0}
                for v in (phase45.CE, phase45.PE)
            ])

            payload = service.board(instrument="NIFTY", now=later + 30.0)
            calls = payload["calls"]
            ok(calls, "a SUPPORTED option instant appears on the board as a "
                      "paper call")
            ok(len(calls) == len(supported_options),
               "one call per admitted option instant and vehicle, not one per "
               "row in the journal — and a supported futures row is not turned "
               "into an option call")
            ok(all(c["vehicle"] in (phase45.CE, phase45.PE) for c in calls),
               "only options are opened as calls on an option call board")
            first = calls[0]
            ok(first["entry_side"] == "ASK"
               and first["entry_price"] == by_vehicle[first["vehicle"]]["entry_price"],
               "the entry is the executable ask recorded at the instant, "
               "copied rather than recomputed")
            ok(first["state"] == phase47.MARKED and first["mark_side"] == book.BID,
               "and it is marked at the bid of the later captured quote")
            ok(first["mark_price"] == 120.0 and first["mark_ts"] == later,
               "on that quote and not on a fabricated one")
            ok(first["net_pct"] is not None
               and first["net_pct"] < first["gross_pct"],
               "with the round trip charged")
            ok(payload["arms"][phase47.ARM_RESEARCH]["marked"] >= 1,
               "the research column has a running result")
            ok(phase47.ARM_PRODUCTION in payload["arms"],
               "and the production column is beside it")
            ok(payload["comparison"]["shared_legs"] >= 0
               and "THE_TWO_COLUMNS_OVERLAP" in
               payload["comparison"]["not_independent"],
               "with the overlap stated, so the two columns cannot be read as "
               "independent samples")
            ok(payload["comparison"]["verdict"] == phase47.INSUFFICIENT_SAMPLE,
               "a handful of legs is labelled an insufficient sample rather "
               "than compared")
            ok(payload["status"] == phase47.PAPER_ONLY
               and payload["order_path"] == phase47.NO_ORDER_PATH
               and payload["production_effect"] == phase47.PRODUCTION_UNCHANGED,
               "every payload carries its own paper-only chrome")
            ok(payload["definition"] == p46freeze.definition(),
               "and inherits the admission fingerprint, so tallies cannot "
               "pool across two definitions")
            ok(payload["journalled_rows_in_session"] == len(
                [r for r in built if r["vehicle"] in (phase45.CE, phase45.PE)]),
               "the count of journalled option rows is published beside the "
               "calls, so an empty board is visibly a session fact")

            # A full session is mostly refusals, and they arrive first: the
            # warm-up is at the open. Bury the admitted instant under more
            # earlier refused rows than the call cap and it must still appear —
            # the cap belongs after each arm's condition, never before it.
            refused = []
            for i in range(service.MAX_CALLS * 4 + 50):
                donor = dict(by_vehicle[phase45.CE])
                donor["overlay_id"] = f"buried-{i}"
                donor["event_id"] = f"buried-event-{i}"
                donor["decision_ts"] = float(donor["decision_ts"]) - 30.0 + i * 1e-4
                donor["overlay_state"] = phase46.COST_BLOCKED
                donor["production_signal_normalised"] = "WAIT"
                refused.append(donor)
            con = p46store.connect(p46store.db_path())
            p46store.insert_events(con, refused)
            con.close()
            service.reset()
            buried = service.board(instrument="NIFTY", now=later + 30.0)
            ok(len(buried["calls"]) == len(calls),
               "an admitted instant is still found when more earlier refused "
               "rows than the call cap sit in front of it in the session")
            ok(buried["journalled_rows_in_session"]
               > payload["journalled_rows_in_session"],
               "and the journalled row count grows with them, so the board "
               "never reports a smaller session than the journal holds")

            whole = buried["bound"][phase47.ARM_RESEARCH]
            ok(whole["truncated"] is False
               and whole["count_is"] == phase47.COUNT_IS_COMPLETE
               and whole["selected"] == whole["available"] == len(calls),
               "a board whose bound was never reached is complete, and is not "
               "hedged for no reason")

            # A read-only pass may raise the per-read bound; the board may not.
            # A diagnostic asking "is this count the session's or the limit's"
            # cannot be capped at the number it is trying to see past, and a
            # count taken at exactly the bound is a floor either way.
            ok(buried["bound"]["max"] == service.MAX_CALLS
               and service.board(instrument="NIFTY", limit=10**9,
                                 now=later + 30.0)["bound"]["limit"]
               == service.MAX_CALLS == 5000
               and service.MAX_READ > service.MAX_CALLS,
               "the live board still reads under its own 5,000-leg bound, "
               "unchanged, with the read-only ceiling declared separately")
            raised = service.arms(instrument="NIFTY", limit=50000)
            ok(raised["bound"]["limit"] == 50000
               and raised["bound"]["max"] == service.MAX_CALLS
               and raised["bound"]["read_ceiling"] == service.MAX_READ
               and service.arms(instrument="NIFTY", limit=10**9)[
                   "bound"]["limit"] == service.MAX_READ,
               "and the read-only leg reader honours a larger request up to "
               "that ceiling while publishing both bounds, so a truncated read "
               "is never mistaken for a session count")

            # CE and PE stay apart, with their own contracts
            both = {c["vehicle"]: c for c in calls}
            if len(both) == 2:
                ok(both[phase45.CE]["contract"] != both[phase45.PE]["contract"]
                   and both[phase45.CE]["strike"] != both[phase45.PE]["strike"],
                   "CE and PE are separate calls with their own contract and "
                   "strike, never collapsed")

            # a quote from before the call cannot mark it
            before_only = os.path.join(tmp, "before.db")
            p35store.db_path = lambda: before_only  # type: ignore[assignment]
            raw_store(before_only, [
                {"vehicle": phase45.CE,
                 "symbol": by_vehicle[phase45.CE]["contract"],
                 "ts": ts - 60.0, "bid": 500.0, "ask": 501.0},
            ])
            past = service.board(instrument="NIFTY", now=later)
            ok(all(c["state"] == phase47.UNMARKABLE_NO_LATER_QUOTE
                   for c in past["calls"]),
               "a quote from before the call opened is not a mark — a leg is "
               "never priced on its own past")
            # The read is bounded per contract by the *earliest* call on it, so
            # two calls on one contract share a quote lookup. A later call must
            # still refuse a quote that predates it: this is the case the SQL
            # bound cannot catch, and marking it would print a result for a leg
            # that had not been opened yet.
            shared_quote = {"ts": ts + 100.0, "bid": 300.0, "ask": 301.0}
            pair = service._mark_arm(
                [call(event_id="early", decision_ts=ts,
                      contract="C1", vehicle=phase45.CE),
                 call(event_id="late", decision_ts=ts + 200.0,
                      contract="C1", vehicle=phase45.CE)],
                {("C1", phase45.CE): shared_quote},
                now=later,
            )
            ok(pair[0]["state"] == phase47.MARKED
               and pair[1]["state"] == phase47.UNMARKABLE_NO_LATER_QUOTE,
               "two calls on one contract share one quote read, and the one "
               "taken after that quote is left unmarked rather than priced on "
               "a book from before it existed")

            # nor can one from another session
            other_session = os.path.join(tmp, "other.db")
            p35store.db_path = lambda: other_session  # type: ignore[assignment]
            raw_store(other_session, [
                {"vehicle": phase45.CE,
                 "symbol": by_vehicle[phase45.CE]["contract"],
                 "ts": ts + 3 * 86400.0, "bid": 500.0, "ask": 501.0},
            ])
            across = service.board(instrument="NIFTY", now=ts + 3 * 86400.0)
            ok(all(c["state"] == phase47.UNMARKABLE_NO_LATER_QUOTE
                   for c in across["calls"]),
               "and neither can a quote from a later session — the overnight "
               "gap is not something a paper leg lived through")

            p35store.db_path = lambda: raw_path  # type: ignore[assignment]

            # ============================================ the session journal
            first_write = service.record(now=later + 30.0)
            ok(first_write["written"] >= 1,
               "the session tally is journalled")
            again = service.record(now=later + 45.0)
            ok(again["written"] == 0 and again["duplicate"] >= 1,
               "and re-recording the same marked-through instant writes "
               "nothing, so a poll loop cannot inflate the journal")
            raw_store(raw_path, [
                {"vehicle": phase45.CE,
                 "symbol": by_vehicle[phase45.CE]["contract"],
                 "ts": later + 120.0, "bid": 130.0, "ask": 131.0},
            ])
            moved = service.record(now=later + 200.0)
            ok(moved["written"] >= 1,
               "a tally taken after the market moved is a new snapshot, "
               "because the instant it was marked through is part of its id")
            history = service.sessions(limit=10)
            ok(history["sessions"],
               "the recorded sessions are readable back")
            ok({str(r["arm"]) for r in history["sessions"]}
               <= {phase47.ARM_RESEARCH, phase47.ARM_PRODUCTION},
               "one row per arm, under the published arm names")
            ok(all(r["status"] == phase47.PAPER_ONLY
                   for r in history["sessions"]),
               "and each stored row carries its own paper-only label")
            ok(history["not_a_promotion"] == phase47.NOT_A_PROMOTION,
               "with the refusal text on the payload")

            status = service.status()
            ok(status["definition"] == p46freeze.definition()
               and status["order_path"] == phase47.NO_ORDER_PATH,
               "status names the definition it prices and the path it has not")

            # ============================================ the automatic recorder
            service.reset()
            auto = service.maybe_record(now=later + 400.0)
            ok(auto.get("skipped") is False,
               "the automatic recorder runs when the interval has elapsed")
            throttled = service.maybe_record(now=later + 401.0)
            ok(throttled.get("skipped") is True,
               "and throttles itself, so its cost is a property of the clock "
               "rather than of how densely the session was sampled")

            # ============================================ a refresh is cheap
            started = time.time()
            for _ in range(20):
                service.board(instrument="NIFTY", now=later)
            elapsed = time.time() - started
            ok(elapsed < 3.0,
               f"twenty board reads cost {elapsed:.3f}s — no historical scan "
               f"and no path rebuild runs on a refresh")

            # ============================================ empty is not zero
            empty = service.board(instrument="NO_SUCH_NAME", now=later)
            ok(not empty["calls"]
               and empty["arms"][phase47.ARM_RESEARCH]["net_pct_total"] is None,
               "an instrument with no admitted instant shows no calls and no "
               "net, rather than a flat session")
            ok(service.record(session="1999-01-01")["written"] == 0,
               "and a session with no calls journals nothing")
        finally:
            service.reset()
            p46service.reset()
            p35store.db_path = prev_raw  # type: ignore[assignment]
            settings.data_dir = prev_dir

    # ============================================ a bounded count is a floor
    # The 2026-09-17 production tally was re-recorded as exactly the recorder's
    # own ceiling and read as the session's count. A reading that stopped at its
    # limit measures the limit, so it has to say so and it has to be replaceable.
    prev_dir = settings.data_dir
    prev_raw = p35store.db_path
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        raw_path = os.path.join(tmp, "raw.db")
        p35store.db_path = lambda: raw_path  # type: ignore[assignment]
        service.reset()
        p46service.reset()
        try:
            base = time.time() - 600.0
            rows = overlay_rows(observation(ts=base), live_range(base))
            admitted = [r for r in rows
                        if r["overlay_state"] == phase46.SUPPORTED
                        and r["vehicle"] in (phase45.CE, phase45.PE)][0]
            clones = []
            for i in range(5):
                clone = dict(admitted)
                clone["overlay_id"] = f"floor-{i}"
                clone["event_id"] = f"floor-event-{i}"
                clone["decision_ts"] = float(admitted["decision_ts"]) + i
                clones.append(clone)
            con = p46store.connect(p46store.db_path())
            p46store.insert_events(con, clones)
            con.close()
            service.reset()
            held = len(clones)

            clipped = service.board(limit=2, now=base + 300.0)
            cut = clipped["bound"][phase47.ARM_RESEARCH]
            ok(len(clipped["calls"]) == 2 and cut["truncated"] is True,
               "a board read under a bound smaller than the arm holds says it "
               "was truncated")
            ok(cut["available"] == held and cut["selected"] == 2
               and cut["limit"] == 2,
               "with the bound, what the arm holds and what was read all three "
               "published, so a reader can see which of the two numbers the "
               "count is a property of")
            ok(cut["count_is"] == phase47.COUNT_IS_A_FLOOR
               and cut["reported"] == "at least 2",
               "and the count is reported as a floor, in words, never as a "
               "total")
            ok(clipped["event_counts"][phase47.ARM_RESEARCH][
                   p49events.LEG_COUNT] == 2,
               "the event grouping is taken over what was read, so its leg "
               "count cannot exceed the bound either")
            full_read = service.board(limit=held + 10, now=base + 300.0)
            whole = full_read["bound"][phase47.ARM_RESEARCH]
            ok(len(full_read["calls"]) == held
               and whole["truncated"] is False
               and whole["count_is"] == phase47.COUNT_IS_COMPLETE
               and whole["reported"] == str(held),
               "raising the limit reads the rest, and that reading is complete "
               "rather than hedged")

            floor_write = service.record(limit=2, now=base + 300.0)
            fid = str(floor_write["snapshot_ids"][phase47.ARM_RESEARCH])
            ok(floor_write["bound"][phase47.ARM_RESEARCH]["truncated"] is True,
               "recording under a binding bound returns the truncation with "
               "the counts, not only on the board")
            fuller = service.record(limit=held + 10, now=base + 300.0)
            nid = str(fuller["snapshot_ids"][phase47.ARM_RESEARCH])
            ok(nid != fid and fuller["written"] >= 1,
               "re-recording with a larger limit writes its own tally rather "
               "than being absorbed as a duplicate of the floor — being "
               "swallowed by its own id is how the truncated 8 survived its fix")
            jcon = store47.connect()
            stored = {r["snapshot_id"]: r
                      for r in store47.snapshots(jcon, limit=50)}
            jcon.close()
            ok(stored[fid]["count_is"] == phase47.COUNT_IS_A_FLOOR
               and stored[fid]["selection_bound"] == 2
               and stored[fid]["legs_available"] == held
               and stored[fid]["calls"] == 2,
               "the floor row keeps the bound it was taken under and what the "
               "arm held, so it stays readable as a floor long after the read")
            ok(stored[nid]["count_is"] == phase47.COUNT_IS_COMPLETE
               and stored[nid]["calls"] == held,
               "and the fuller row is recorded as complete, with both counts "
               "in the journal at once rather than one editing the other")
            ok(service.record(limit=2, now=base + 300.0)["written"] == 0,
               "recording the same bounded reading twice appends nothing")
        finally:
            service.reset()
            p46service.reset()
            p35store.db_path = prev_raw  # type: ignore[assignment]
            settings.data_dir = prev_dir

    # ==================================================== the store itself
    with tempfile.TemporaryDirectory() as tmp:
        con = store47.connect(os.path.join(tmp, "p47.db"))
        row = {
            "snapshot_id": store47.snapshot_id("2026-09-14", "ARM", "def", 10.0),
            "session": "2026-09-14", "arm": "ARM", "definition": "def",
            "snapshot_ts": 100.0, "marked_through_ts": 10.0, "calls": 2,
            "marked": 1, "unmarkable": 1, "status": phase47.PAPER_ONLY,
            "order_path": phase47.NO_ORDER_PATH, "version": phase47.VERSION,
        }
        ok(store47.insert_snapshots(con, [row])["written"] == 1,
           "a snapshot is appended")
        ok(store47.insert_snapshots(con, [row])["duplicate"] == 1,
           "and the same snapshot twice is absorbed, not appended")
        ok(store47.snapshot_id("s", "a", "d", 1.0)
           != store47.snapshot_id("s", "a", "d", 2.0),
           "the id changes when the instant marked through changes")
        ok(store47.snapshot_id("s", "a", "d", 1.0)
           != store47.snapshot_id("s", "a", "e", 1.0),
           "and when the definition changes, so two definitions never share "
           "a row")
        newer = {**row, "snapshot_ts": 200.0, "marked_through_ts": 20.0,
                 "snapshot_id": store47.snapshot_id(
                     "2026-09-14", "ARM", "def", 20.0)}
        store47.insert_snapshots(con, [newer])
        latest = store47.latest_per_session(con, limit=5)
        ok(len(latest) == 1 and latest[0]["marked_through_ts"] == 20.0,
           "the session view shows the newest recorded tally for each arm")
        ok(len(store47.snapshots(con, session="2026-09-14")) == 2,
           "while both snapshots remain in the journal — the record of what "
           "the board said at 14:00 survives the session ending elsewhere")
        text = source_of(store47).upper()
        ok("UPDATE SESSION_MARK" not in text and "DELETE FROM" not in text
           and "DROP TABLE" not in text,
           "the store has no UPDATE and no DELETE, so a recorded tally "
           "cannot be rewritten")
        con.close()

    # ==================================================== no hindsight
    marker_reads = names_read(mark_mod)
    leaked = sorted(set(FUTURE_FIELDS) & marker_reads)
    ok(not leaked, f"the marker reads no post-hoc outcome field (found {leaked})")
    ok(not (set(FUTURE_FIELDS) & names_read(service)),
       "and neither does the board service")
    ok("paper_outcome" not in source_of(service)
       and "paper_outcome" not in source_of(mark_mod),
       "no Phase 47 module reads the resolved outcome table")
    # Scoped to ``mark`` alone: ``tally`` legitimately reads ``net_pct`` off
    # rows ``mark`` itself produced a moment earlier, which is its own output
    # and not an inherited result.
    mark_fn = ast.parse(
        inspect.getsource(mark_mod.mark).lstrip()
    )
    mark_reads = {
        n.args[0].value for n in ast.walk(mark_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get" and n.args
        and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
    }
    ok(not (set(phase46.OUTCOME_FIELDS) & mark_reads),
       "and mark() reads none of the overlay's outcome vocabulary off a row "
       "either — it computes its own net, it never inherits one")
    ok("cost_points" in str(mark_reads) or "measured_cost_points" in mark_reads,
       "while it does read the round trip recorded at the instant, which is "
       "decision-time information and the one thing it must not model itself")
    admission = source_of(service).split("def _calls")[1].split("def _latest_quotes")[0]
    ok("SUPPORTED" in admission and "net_pct" not in admission
       and "mark_price" not in admission
       and not (set(phase46.OUTCOME_FIELDS) & fields_read(service)),
       "admission selects on the state Phase 46 recorded at the instant, and "
       "reads no price, mark or result while doing it")
    ok("app.research.phase47" not in " ".join(
        imports_of("app.research.phase45.evaluator", set())),
       "the Phase 45 admission path cannot see this phase")
    ok("phase47" not in source_of(evaluator)
       and "phase47" not in source_of(overlay),
       "and neither the evaluator nor the overlay classifier mentions it")

    # ==================================================== isolation
    for module in ("app.research.phase47", "app.research.phase47.mark",
                   "app.research.phase47.service",
                   "app.research.phase47.store",
                   "app.research.phase47.cli"):
        graph = imports_of(module, set())
        hits = sorted(
            m for m in graph
            if m in sys.modules
            and any(bad in m.lower().rsplit(".", 1)[-1] for bad in FORBIDDEN)
        )
        ok(not hits, f"{module} reaches no order/broker module (found {hits})")
    text47 = "\n".join(
        source_of(m) for m in (phase47, mark_mod, service, store47))
    for bad in ("place_order", "cancel_order", "modify_order", "requests.post",
                "httpx.post", "smart_api", "SmartConnect", "buy(", "sell("):
        ok(bad not in text47, f"no Phase 47 module mentions {bad}")
    ok("def record" in source_of(service) and "def place" not in source_of(service),
       "the service's only write is its own session journal")
    ok(source_of(mark_mod).count("open(") == 0
       and "sqlite3" not in source_of(mark_mod),
       "the marker is pure: no file, no store, no feed — which is why the "
       "smoke can price a leg without a market")

    # ==================================================== production untouched
    p17 = source_of(p17service)
    ok("phase47" not in p17 and "phase46" not in p17,
       "the Phase 17 tick path mentions neither the overlay nor this board")
    ok(p17.count("p45.observe(obs)") == 1 and p17.count("p45.") == 1,
       "and its only research coupling is still the single Phase 45 call")
    p46svc = source_of(p46service)
    ok(p46svc.count("phase47") == 1,
       "Phase 46 hands over to this phase in exactly one place")
    ok(p46svc.index("insert_events") < p46svc.index("_hand_over()"),
       "and only after its own journal write, so a Phase 47 failure cannot "
       "cost an overlay row")
    hand_over = p46svc.split("def _hand_over")[1]
    ok("try:" in hand_over and "except Exception" in hand_over,
       "the hand-over cannot raise into the writer above it")
    ok(service.maybe_record(now=0.0) is not None,
       "and the recorder returns rather than raising even with no store")

    from app.engine import decision as decision_mod
    from app.engine import signal_gate as gate_mod
    for mod, name in ((decision_mod, "app.engine.decision"),
                      (gate_mod, "app.engine.signal_gate")):
        body = source_of(mod)
        ok("phase47" not in body and "phase46" not in body
           and "phase45" not in body,
           f"{name} — production BUY/SELL/WAIT logic — imports no shadow layer")
        ok("phase47" not in " ".join(imports_of(name, set())),
           f"and nothing {name} imports reaches this board")

    # ==================================================== frozen definitions
    ok(p41freeze.fingerprint()["definition"] == FROZEN_41,
       f"Phase 41 fingerprint unchanged ({FROZEN_41})")
    ok(p42freeze.fingerprint()["definition"] == FROZEN_42,
       f"Phase 42 fingerprint unchanged ({FROZEN_42})")
    ok(p43freeze.fingerprint()["components"]["definition"] == FROZEN_43,
       f"Phase 43 definition fingerprint unchanged ({FROZEN_43})")
    ok(p44freeze.fingerprint()["definition"] == FROZEN_44,
       f"Phase 44 fingerprint unchanged ({FROZEN_44})")
    ok(p45freeze.definition() == FROZEN_45,
       f"Phase 45 fingerprint unchanged ({FROZEN_45})")
    ok(not p44_armed(), "Phase 44 is still dormant")
    ok("def definition" in source_of(service)
       and "hashlib" not in source_of(service),
       "Phase 47 mints no definition of its own — it prices what another one "
       "admitted, and inherits that fingerprint")

    # ==================================================== vocabulary
    for word in ("VERIFIED EDGE", "VERIFIED_EDGE", "WINNING", "GUARANTEED",
                 "BEST_CALL"):
        ok(word not in text47.upper(),
           f"no Phase 47 module uses the word {word}")
    ok(text47.lower().count("profitable") == 1
       and "profitable" in phase47.NOT_A_PROMOTION,
       "the only use of the word profitable is the sentence denying it")

    # ==================================================== the panel
    front = Path(__file__).resolve().parents[1] / "frontend"
    panel = front / "components" / "ResearchCallBoard.tsx"
    page = front / "app" / "page.tsx"
    api_ts = front / "lib" / "api.ts"
    if panel.exists() and api_ts.exists():
        tsx = panel.read_text()
        api = api_ts.read_text()
        ok("Array.isArray(data?.calls)" in tsx,
           "the panel guards its list read — an observational panel must not "
           "be able to throw and take the page down with it")
        fn = api.split("export async function getP47Board")[1]
        fn = fn.split("export async function")[0]
        ok("res.ok" in fn and "Array.isArray(payload.calls)" in fn,
           "and the fetch refuses a non-OK response or a payload with no "
           "calls instead of handing it to the renderer")
        ok("CALL_BOARD_UNAVAILABLE" in tsx,
           "an unreachable board reads as its own state, not as a flat "
           "session")
        ok("PAPER ONLY" in tsx and "NO ORDER PATH" in tsx,
           "the paper-only chrome is inside the panel's own border, so a "
           "screenshot of it cannot lose the label that makes it honest")
        ok("not_a_promotion" in tsx,
           "and the refusal text is rendered from the payload")
        ok(phase47.COUNT_IS_A_FLOOR in tsx and "COUNT IS A FLOOR" in tsx,
           "the panel carries the floor marker as the literal string the "
           "backend stores, so renaming it upstream shows up as a missing "
           "warning rather than as a silently complete count")
        ok("P47Bound" in api and "truncated" in api and "reported" in api,
           "and the client type has the bound on it, so a caller cannot read "
           "a count without being able to see what it was bounded by")
        ok("setData(null)" in tsx,
           "a failed refresh clears the marks rather than leaving stale ones "
           "on screen claiming to be current")
        # Comments are stripped first: the panel's own docstring says the verb
        # BUY appears nowhere, and a check that cannot tell a comment from a
        # button would fail on the sentence promising there is no button.
        code = "\n".join(
            ln for ln in tsx.splitlines()
            if not ln.lstrip().startswith(("*", "//", "/*"))
        )
        for verb in (">BUY<", "onClick", "<button", "<form", "method: \"POST\""):
            ok(verb not in code,
               f"the panel has no {verb} — it is a scoreboard, not a ticket, "
               f"and there is no control on it that could become one")
    if page.exists():
        body = page.read_text()
        signal_view = body.split("QuantumSignal")[0]
        ok("ResearchOverlayBoard" not in signal_view
           or "ResearchCallBoard" not in signal_view,
           "the research panels are not mounted in the production signal view")
        ok("ResearchCallBoard" in body,
           "the call board is mounted in the research tab")

    print()
    print(f"PHASE 47 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
