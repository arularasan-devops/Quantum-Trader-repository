"""Phase 46 smoke — the properties that keep an overlay an overlay.

Every check here exists because its absence would turn a research view into a
trading change, or has already done something like it in this project:

* an overlay module reaching an order, a broker or an execution path — walked
  transitively, because a one-level grep passes a module that imports a helper
  that imports the order path;
* the classifier reading a field that only exists after the leg is over, which
  is how an earlier ratio came to be built on a realised peak. The classifier
  is parsed for the outcome vocabulary rather than trusted;
* a stale book or a capture hole reported as a research disagreement or a cost
  refusal — two facts about the feed dressed as an opinion about an instant;
* CE and PE collapsed, or an option priced from the futures leg at the same
  instant, which deletes the only comparison the board exists to make;
* a duplicate observation appending a second overlay row, which would inflate
  every state count and rewrite what the overlay said at the time;
* the vehicle comparison naming a winner, which is a recommendation;
* Phase 41, 42, 43 or 44 moved by anything done here, or Phase 44 armed;
* the production signal path changed, or able to see this package at all.

    .venv/bin/python _smoke_phase46.py
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import tempfile
import time

from app.config import settings
from app.research import phase45, phase46
from app.research.phase17 import quality, schema
from app.research.phase17 import service as p17service
from app.research.phase41 import freeze as p41freeze
from app.research.phase42 import freeze as p42freeze
from app.research.phase43 import freeze as p43freeze
from app.research.phase44 import freeze as p44freeze
from app.research.phase44 import store as p44store
from app.research.phase44 import switch as p44switch
from app.research.phase45 import evaluator, ranges
from app.research.phase45 import freeze as p45freeze
from app.research.phase45 import service as p45service
from app.research.phase45 import store as p45store
from app.research.phase46 import compare, freeze, overlay, service
from app.research.phase46 import store as store46

PASS = 0
FAIL: list[str] = []

# Frozen before this phase existed. If one of these moves, something here has
# edited an upstream definition — which this task forbids outright.
FROZEN_41 = "34fc64a299d29760"
FROZEN_42 = "b67709af902c8bff"
FROZEN_43 = "e45c3e72ad37f864"
FROZEN_44 = "95d393256ed1f505"
FROZEN_45 = "a3b0b9b0b321ee47"

MINUTE = 60.0

# Modules that can move money, or reach something that can. Name them once.
FORBIDDEN = (
    "order", "broker", "execution", "smartapi", "angel", "kite", "trade_api",
    "place_order", "autobot", "executor",
)


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


# --------------------------------------------------------------------------
# Fixtures. Built through Phase 45's own evaluator, never by hand: an overlay
# row assembled from a literal would prove the classifier works on input the
# system never produces.
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
    lot: int | None = 50,
) -> schema.Quote:
    return schema.Quote(
        instrument=instrument, vehicle=vehicle,
        symbol=symbol or f"NIFTY26SEP{vehicle}",
        strike=None if vehicle == phase45.FUTURES else strike,
        expiry="2026-09-24", days_to_expiry=13,
        bid=bid, ask=ask, bid_size=750, ask_size=1200,
        premium=None if bid is None or ask is None else (bid + ask) / 2.0,
        lot_size=lot, delta=delta, underlying_price=24010.0,
        moneyness=schema.ATM, source="ANGELONE_WS",
        feed_age_ms=120.0, book_age_ms=140.0, data_quality=dq,
    )


def observation(
    *,
    obs_id: str = "obs-1",
    ts: float,
    instrument: str = "NIFTY",
    direction: str | None = "LONG",
    ce: schema.Quote | None = None,
    pe: schema.Quote | None = None,
    fut: schema.Quote | None = None,
    candidate_class: str = schema.BUY,
) -> schema.Observation:
    return schema.Observation(
        observation_id=obs_id, signal_ts=ts, capture_ts=ts + 0.4,
        instrument=instrument, family="INDEX", candidate_class=candidate_class,
        direction=direction, selected_vehicle=phase45.CE, session="2026-09-14",
        selected=ce if ce is not None else quote(phase45.CE),
        opposite=pe if pe is not None else quote(
            phase45.PE, delta=-0.48, symbol="NIFTY26SEPPE", strike=23900.0),
        futures=fut if fut is not None else quote(
            phase45.FUTURES, bid=24008.0, ask=24009.0, delta=1.0,
            symbol="NIFTY26SEPFUT"),
        plan=schema.Plan(lot_size=50, expected_move_points=40.0),
        context=schema.MarketContext(
            atr_points=90.0, regime="TREND", session_period="MORNING",
            market_signal="BUY",
        ),
    )


def live_range(ts: float, instrument: str = "NIFTY") -> ranges.LiveRanges:
    live = ranges.LiveRanges()
    for i in range(12, 0, -1):
        live.note(instrument, ts - i * MINUTE, 24000.0 + (i % 3) * 60.0)
    return live


def p45_rows(obs: schema.Observation, live: ranges.LiveRanges) -> list[dict]:
    return evaluator.evaluate(
        obs,
        trailing_range=live.before(obs.instrument, obs.signal_ts),
        definition=p45freeze.definition(),
        ingest_ts=time.time(),
    )


def built(obs: schema.Observation, live: ranges.LiveRanges) -> dict[str, dict]:
    fingerprint = freeze.definition()
    rows = [
        overlay.build(r, definition=fingerprint) for r in p45_rows(obs, live)
    ]
    return {str(r["vehicle"]): r for r in rows}


# --------------------------------------------------------------------------
# Static helpers
# --------------------------------------------------------------------------
def imports_of(module_name: str, seen: set[str]) -> set[str]:
    """Every ``app.*`` module reachable from one module, transitively."""
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


def names_read(module: object) -> set[str]:
    """Field names the module reads: attributes, string subscripts, ``.get``."""
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


def main() -> int:  # noqa: C901 - one flat list of properties reads better
    ts = time.time() - 120.0
    live = live_range(ts)

    # ==================================================== the overlay appears
    rows = built(observation(ts=ts), live)
    ok(set(rows) == {phase45.FUTURES, phase45.CE, phase45.PE},
       "an overlay row exists for FUTURES, CE and PE at a valid instant")
    ce = rows[phase45.CE]
    ok(ce["overlay_state"] in phase46.STATES,
       "the state is one of the eight published words")
    ok(ce["overlay_state"] not in phase46.SILENT,
       "a measured instant is not reported as silent")
    for field in ("research_candidate", "direction", "vehicle", "contract",
                  "expiry", "bid", "ask", "spread", "bid_size", "ask_size",
                  "feed_age_ms", "premium", "expected_move_points",
                  "modelled_cost_points", "measured_cost_points",
                  "expected_move_over_modelled_cost", "data_quality",
                  "overlay_state", "overlay_reason"):
        ok(field in ce, f"the row carries {field}")
    ok(ce["strike"] is not None, "an option row carries its strike")
    ok(rows[phase45.FUTURES]["strike"] is None,
       "and a futures row does not invent one")
    ok(ce["research_candidate"].endswith(p45freeze.definition()),
       "the candidate names the upstream fingerprint it stands on")
    ok(ce["definition"] == freeze.definition()
       and ce["shadow_definition"] == p45freeze.definition(),
       "both fingerprints are on the row, so nothing pools across either")

    # ============================================= nothing is recomputed here
    source = p45_rows(observation(ts=ts), live)
    by_vehicle = {str(r["vehicle"]): r for r in source}
    ok(all(
        rows[v]["expected_move_over_modelled_cost"] == by_vehicle[v]["ratio_modelled"]
        and rows[v]["modelled_cost_points"] == by_vehicle[v]["modelled_cost_points"]
        and rows[v]["bid"] == by_vehicle[v]["bid"]
        and rows[v]["ask"] == by_vehicle[v]["ask"]
        for v in rows
    ), "every number is the Phase 45 figure, copied and not recalculated")
    text = source_of(overlay)
    ok(("/" not in text.split("def build")[1].split("def research_candidate")[0]),
       "the adapter contains no division, so it cannot have its own cost model")

    # ======================================== production signal, read not written
    ok(rows[phase45.CE]["production_signal"] == by_vehicle[phase45.CE]["production_signal"],
       "the production call is carried through verbatim")
    ok(rows[phase45.CE]["production_effect"] == phase46.PRODUCTION_UNCHANGED,
       "and every row says this layer changed nothing")
    before = dict(by_vehicle[phase45.CE])
    overlay.build(by_vehicle[phase45.CE], definition=freeze.definition())
    ok(by_vehicle[phase45.CE] == before,
       "building an overlay row does not mutate the Phase 45 row it read")

    # ======================================== missing / stale / gap
    absent = built(
        observation(ts=ts, ce=quote(phase45.CE, bid=None, ask=None)), live)
    ok(absent[phase45.CE]["overlay_state"] == phase46.UNMEASURED,
       "a missing quote is UNMEASURED")
    ok(absent[phase45.CE]["overlay_reason"] is not None,
       "and names why, rather than leaving a blank cell")
    ok(absent[phase45.CE]["entry_price"] is None,
       "an unmeasurable option takes no price")
    ok(absent[phase45.FUTURES]["entry_price"] is not None
       and absent[phase45.CE]["bid"] != absent[phase45.FUTURES]["bid"],
       "and the futures book at the same instant did not stand in for it")

    stale = built(
        observation(ts=ts, ce=quote(phase45.CE, dq=quality.MISSING)), live)
    ok(stale[phase45.CE]["overlay_state"] == phase46.STALE,
       "a quote too old to fill on is STALE, not UNMEASURED")

    gapped = ranges.LiveRanges()
    for i in (9, 8, 7, 6, 5, 4, 3):
        gapped.note("NIFTY", ts - i * MINUTE, 24000.0 + i)
    gap = built(observation(ts=ts), gapped)
    ok(all(r["overlay_state"] in (phase46.CAPTURE_GAP, phase46.STALE)
           for r in gap.values()),
       "a hole between the last captured minute and the decision is a "
       "CAPTURE_GAP or a STALE bar, never a research opinion")
    ok(overlay.classify({
        "evidence": "UNMEASURED", "reason": phase45.CAPTURE_GAP,
    })["overlay_state"] == phase46.CAPTURE_GAP,
       "a capture hole named upstream is reported as CAPTURE_GAP")
    ok(all(r["overlay_state"] != phase46.COST_BLOCKED
           and r["overlay_state"] != phase46.DISAGREES for r in gap.values()),
       "a feed fact is never reported as a disagreement or a cost refusal")
    ok(overlay.classify(None)["overlay_state"] == phase46.NO_RESEARCH_EVIDENCE,
       "no research row at all is NO_RESEARCH_EVIDENCE")

    # ======================================== the negative states are reachable
    disagrees = overlay.classify({
        "evidence": evaluator.MEASURED, "reason": phase45.DIRECTION_DISAGREES,
    })
    ok(disagrees["overlay_state"] == phase46.DISAGREES,
       "a vehicle opposing the production read is DISAGREES")
    blocked = overlay.classify({
        "evidence": evaluator.MEASURED, "reason": phase45.BELOW_LIVE_GATE,
    })
    ok(blocked["overlay_state"] == phase46.COST_BLOCKED,
       "an expected move that does not clear the measured round trip is "
       "COST_BLOCKED")
    supported = overlay.classify({
        "evidence": evaluator.MEASURED, "reason": None,
        "shadow_action": phase45.SHADOW_BUY, "production_signal": schema.BUY,
    })
    ok(supported["overlay_state"] == phase46.SUPPORTED,
       "research admitting the same instant the board called is SUPPORTED")
    not_called = overlay.classify({
        "evidence": evaluator.MEASURED, "reason": None,
        "shadow_action": phase45.SHADOW_BUY, "production_signal": "WAIT",
    })
    ok(not_called["overlay_state"] == phase46.WATCH,
       "research admitting an instant the board did not call is WATCH, not "
       "a recommendation to take it")
    ok(len({
        overlay.classify(r)["overlay_state"] for r in (
            {"evidence": evaluator.MEASURED, "reason": phase45.DIRECTION_DISAGREES},
            {"evidence": evaluator.MEASURED, "reason": phase45.BELOW_LIVE_GATE},
            {"evidence": "UNMEASURED", "reason": phase45.CAPTURE_GAP},
            {"evidence": "UNMEASURED", "reason": phase45.STALE_QUOTE},
            {"evidence": "UNMEASURED", "reason": "NO_BOOK"},
        )
    }) == 5, "five distinct causes produce five distinct words")

    # ======================================== CE and PE stay apart
    ok(rows[phase45.CE]["vehicle"] != rows[phase45.PE]["vehicle"]
       and rows[phase45.CE]["contract"] != rows[phase45.PE]["contract"]
       and rows[phase45.CE]["strike"] != rows[phase45.PE]["strike"],
       "CE and PE are separate rows with their own contract and strike")
    ok(rows[phase45.CE]["overlay_id"] != rows[phase45.PE]["overlay_id"],
       "and separate journal keys, so neither can overwrite the other")
    ok(rows[phase45.PE]["premium"] == by_vehicle[phase45.PE]["last_traded_price"],
       "the put's premium is the put's own")

    # ======================================== the comparison, no winner
    cmp_all = overlay.vehicle_comparison(list(rows.values()))
    ok(sorted(cmp_all["synchronized"]) == sorted(
        [phase45.CE, phase45.FUTURES, phase45.PE]),
       "all three vehicles priced at one instant are synchronized")
    ok(cmp_all["complete"] is True, "and the instant is marked complete")
    ok({v: cmp_all["vehicles"][v]["contract"] for v in cmp_all["vehicles"]}
       == {v: rows[v]["contract"] for v in rows},
       "the comparison preserves each vehicle's exact contract")
    ok(cmp_all["vehicles"][phase45.CE]["strike"] != cmp_all["vehicles"][phase45.PE]["strike"],
       "and each option's own strike")
    partial = overlay.vehicle_comparison([
        rows[phase45.FUTURES], absent[phase45.CE], rows[phase45.PE]])
    ok(partial["complete"] is False and phase45.CE in partial["absent"],
       "an unmeasurable vehicle is named absent, not filled in")
    ok(phase45.CE not in partial["synchronized"],
       "and is not counted as synchronized")
    cmp_text = source_of(overlay).split("def vehicle_comparison")[1]
    ok("max(" not in cmp_text and "sort" not in cmp_text.replace("sorted(measured)", "")
       and "best" not in cmp_text.lower().split("no_selection")[0],
       "the comparison contains no ranking of any kind")
    ok("NOT_RANKED" in cmp_all["no_selection"],
       "and says on the payload that it selects nothing")

    # ======================================== journalled, and idempotent
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "overlay.db")
        con = store46.connect(path)
        first = store46.insert_events(con, list(rows.values()))
        ok(first["written"] == 3 and first["duplicate"] == 0,
           "three overlay rows are journalled for one instant")
        again = store46.insert_events(con, list(rows.values()))
        ok(again["written"] == 0 and again["duplicate"] == 3,
           "re-observing the same instant writes nothing and says so")
        ok(store46.counts(con)["rows"] == 3,
           "so the journal holds one row per vehicle per instant, not two")
        stored = store46.events(con, limit=10)
        ok(len(stored) == 3 and {r["vehicle"] for r in stored} == set(rows),
           "and every vehicle is readable back")
        keys = {r["overlay_state"] for r in stored}
        ok(keys <= set(phase46.STATES),
           "every journalled state is one of the published words")
        ok(all(r["mode"] == phase46.SHADOW
               and r["classification"] == phase46.RESEARCH_OVERLAY
               for r in stored),
           "each stored row carries its own SHADOW label")
        counted = store46.state_counts(con)
        ok(sum(counted.values()) == 3, "the state tally matches the journal")
        con.close()

    # the same, through the service the Phase 45 worker actually calls
    prev_dir = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        service.reset()
        try:
            out = service.record(p45_rows(observation(ts=ts), live))
            ok(out["written"] == 3, "the service journals the hand-over")
            twice = service.record(p45_rows(observation(ts=ts), live))
            ok(twice["written"] == 0 and twice["duplicate"] == 3,
               "and a replayed hand-over is absorbed, not appended")
            board = service.board(instrument="NIFTY", limit=5)
            ok(board["count"] == 1 and len(board["instants"][0]["vehicles"]) == 3,
               "the board groups one instant with its three vehicles")
            ok(board["instants"][0]["production"]["signal"]
               == by_vehicle[phase45.CE]["production_signal"],
               "and prints the production call as recorded")
            ok(board["vehicle_order"] == [phase45.FUTURES, phase45.CE, phase45.PE]
               and [r["vehicle"] for r in board["instants"][0]["vehicles"]]
               == [phase45.FUTURES, phase45.CE, phase45.PE],
               "vehicles are listed in a fixed order, not ranked by a metric")
            ok(board["status"] == phase46.PAPER_ONLY
               and board["order_path"] == phase46.NO_ORDER_PATH
               and board["production_effect"] == phase46.PRODUCTION_UNCHANGED,
               "the board payload carries its own paper-only chrome")
            ok(board["inherited"]["phase41"] == FROZEN_41
               and board["inherited"]["phase42"] == FROZEN_42,
               "and names the frozen definitions it reads read-only")
            stats = service.stats()
            ok(stats["rows_written"] == 3 and stats["duplicates"] == 3
               and stats["failures"] == 0,
               "the writer's own counters are observable")
            ok(isinstance(stats["last_latency_ms"], float)
               and stats["last_latency_ms"] < 1000.0,
               "and a hand-over costs milliseconds, not a scan")
            empty = service.board(instrument="NO_SUCH_NAME", limit=5)
            ok(empty["count"] == 0,
               "an instrument with no classified instant returns nothing "
               "rather than a fabricated row")
        finally:
            service.reset()
            settings.data_dir = prev_dir

    # ======================================== no hindsight, structurally
    classifier_reads = names_read(overlay)
    leaked = sorted(classifier_reads & set(phase46.OUTCOME_FIELDS))
    ok(not leaked, f"the classifier reads no outcome field (found {leaked})")
    ok("paper_outcome" not in source_of(overlay)
       and "paper_outcome" not in source_of(service),
       "neither the classifier nor the writer mentions the outcome table")
    ok("paper_outcome" in source_of(compare),
       "only the outcome report reads outcomes, which is what it is for")
    admission_graph = imports_of("app.research.phase46.overlay", set())
    ok("app.research.phase46.compare" not in admission_graph,
       "and the admission path cannot reach the outcome module at all")
    ok("app.research.phase46.compare" not in imports_of(
        "app.research.phase45.evaluator", set()),
       "nor can the Phase 45 evaluator")
    ok(not (names_read(service) & set(phase46.OUTCOME_FIELDS)),
       "the writer reads no outcome field either")

    # ======================================== isolation from the order path
    for module in ("app.research.phase46", "app.research.phase46.overlay",
                   "app.research.phase46.service",
                   "app.research.phase46.store",
                   "app.research.phase46.compare",
                   "app.research.phase46.freeze",
                   "app.research.phase46.cli"):
        graph = imports_of(module, set())
        # Real modules only: ``imports_of`` also records ``from x import NAME``
        # targets, and a constant called NO_ORDER_PATH is not an order path.
        hits = sorted(
            m for m in graph
            if m in sys.modules
            and any(bad in m.lower().rsplit(".", 1)[-1] for bad in FORBIDDEN)
        )
        ok(not hits, f"{module} reaches no order/broker module (found {hits})")
    text46 = "\n".join(
        source_of(m) for m in (phase46, overlay, service, store46, compare, freeze)
    )
    for bad in ("place_order", "cancel_order", "modify_order", "requests.post",
                "httpx.post", "smart_api", "SmartConnect"):
        ok(bad not in text46, f"no overlay module mentions {bad}")
    ok("def record" in source_of(service) and "def place" not in source_of(service),
       "the service's only write is its own journal")

    # ======================================== production path is untouched
    p17 = source_of(p17service)
    ok("phase46" not in p17,
       "the Phase 17 tick path does not mention Phase 46")
    ok(p17.count("p45.observe(obs)") == 1 and p17.count("p45.") == 1,
       "and its only research coupling is still the single Phase 45 call")
    p45svc = source_of(p45service)
    ok(p45svc.count("phase46") == 1,
       "Phase 45 hands over in exactly one place")
    ok(p45svc.index("insert_events") < p45svc.index("_hand_over(rows)"),
       "and only after its own journal write")
    hand_over = p45svc.split("def _hand_over")[1]
    ok("try:" in hand_over and "except Exception" in hand_over,
       "the hand-over cannot raise into the writer above it")
    ok("app.research.phase46" not in str(
        imports_of("app.research.phase45.evaluator", set())),
       "the Phase 45 admission path cannot see Phase 46")
    ok("phase46" not in source_of(evaluator),
       "and the evaluator does not mention it")

    # the production signal module itself
    from app.engine import decision as decision_mod
    from app.engine import signal_gate as gate_mod
    for mod, name in ((decision_mod, "app.engine.decision"),
                      (gate_mod, "app.engine.signal_gate")):
        body = source_of(mod)
        ok("phase46" not in body and "phase45" not in body,
           f"{name} — production BUY/SELL/WAIT logic — imports no shadow layer")
        ok("phase46" not in " ".join(imports_of(name, set())),
           f"and nothing {name} imports reaches one")

    # ======================================== frozen upstream definitions
    ok(p41freeze.fingerprint()["definition"] == FROZEN_41,
       f"Phase 41 fingerprint unchanged ({FROZEN_41})")
    ok(p42freeze.fingerprint()["definition"] == FROZEN_42,
       f"Phase 42 fingerprint unchanged ({FROZEN_42})")
    # Phase 43 publishes a composite fingerprint over five components; the one
       # frozen on the record is its definition component.
    ok(p43freeze.fingerprint()["components"]["definition"] == FROZEN_43,
       f"Phase 43 definition fingerprint unchanged ({FROZEN_43})")
    ok(p44freeze.fingerprint()["definition"] == FROZEN_44,
       f"Phase 44 fingerprint unchanged ({FROZEN_44})")
    ok(p45freeze.definition() == FROZEN_45,
       f"Phase 45 fingerprint unchanged ({FROZEN_45})")
    ok(not p44_armed(), "Phase 44 is still dormant")
    fp = freeze.fingerprint()
    ok(fp["definition"] not in
       {FROZEN_41, FROZEN_42, FROZEN_43, FROZEN_44, FROZEN_45},
       "Phase 46's own fingerprint is distinct from every upstream one")
    ok(fp["inherited"] == {
        "phase41": FROZEN_41, "phase42": FROZEN_42, "phase45": FROZEN_45},
       "and records which frozen inputs it read")
    ok(freeze.definition() == freeze.definition(),
       "the fingerprint is stable within a process")

    # ======================================== outcome comparison honesty
    report = compare.report(limit=100)
    arms = report["arms"]
    ok(set(phase46.ARMS) <= set(arms), "the report publishes both arms")
    ok(arms["not_independent"].startswith("ARM_B_IS_A_SUBSET_OF_ARM_A"),
       "and states that arm B is a subset of arm A, not a second sample")
    for key in phase46.ARMS:
        for metric in ("resolved_trades", "net_pct_total", "expectancy_net_pct",
                       "profit_factor", "win_rate_pct", "max_drawdown_net_pct",
                       "avg_cost_points", "avg_mfe_pct", "avg_mae_pct",
                       "avg_giveback_pct"):
            ok(metric in arms[key], f"{key} reports {metric}")
    ok(report["verdict"] == phase46.INSUFFICIENT_SAMPLE
       or report["resolved_legs_available"] >= phase46.MIN_RESOLVED_FOR_A_COMPARISON,
       "a thin sample is labelled insufficient rather than compared")
    ok(report["not_a_promotion"] == phase46.NOT_A_PROMOTION,
       "and promotes nothing")
    legs = [
        {"event_id": "a", "decision_ts": 1.0, "net_pct": 1.0, "cost_points": 2.0,
         "production_signal": "BUY", "vehicle": "CE",
         "engine_selected_vehicle": "CE", "session": "S1"},
        {"event_id": "b", "decision_ts": 2.0, "net_pct": -2.0, "cost_points": 2.0,
         "production_signal": "BUY", "vehicle": "CE",
         "engine_selected_vehicle": "CE", "session": "S1"},
        {"event_id": "c", "decision_ts": 3.0, "net_pct": 0.5, "cost_points": 2.0,
         "production_signal": "WAIT", "vehicle": "CE",
         "engine_selected_vehicle": "CE", "session": "S1"},
    ]
    two = compare.arms(legs, {"a": phase46.SUPPORTED, "b": phase46.COST_BLOCKED})
    ok(two[phase46.ARM_SIGNAL_ONLY]["resolved_trades"] == 2,
       "arm A takes only the instants the board called a buy")
    ok(two[phase46.ARM_SIGNAL_PLUS_OVERLAY]["resolved_trades"] == 1,
       "arm B keeps only the legs the overlay supported")
    ok(two["legs_in_a_not_in_b"] == 1
       and two["dropped_by_state"][phase46.COST_BLOCKED] == 1,
       "and the difference is attributed to a named state")
    ok(two[phase46.ARM_SIGNAL_ONLY]["profit_factor"] == 0.5,
       "profit factor is gains over losses on the arm's own legs")
    ok(two[phase46.ARM_SIGNAL_PLUS_OVERLAY]["profit_factor"] is None
       and two[phase46.ARM_SIGNAL_PLUS_OVERLAY]["profit_factor_note"] is not None,
       "an arm with no losing leg reports no ratio and says why, rather than "
       "printing an infinity that reads like an edge")
    ok(compare.drawdown([1.0, -2.0, 0.5]) == 2.0,
       "drawdown is the deepest peak-to-trough fall of the cumulative net")
    ok(compare.drawdown([]) is None,
       "and is unmeasured on an empty arm, not zero")
    ok(compare.metrics([])["sample_label"] == phase46.INSUFFICIENT_SAMPLE,
       "an empty arm is labelled insufficient")
    ok(compare.metrics(legs)["expectancy_net_pct"] is not None
       and compare.metrics([{"decision_ts": 1.0}])["expectancy_net_pct"] is None,
       "a leg with no net contributes no expectancy rather than a zero")

    # ======================================== no unsupported vocabulary
    for word in ("VERIFIED EDGE", "VERIFIED_EDGE", "WINNING", "BEST_VEHICLE",
                 "GUARANTEED"):
        ok(word not in text46.upper(),
           f"no overlay module uses the word {word}")
    # "Profitable" appears once, inside the sentence denying it — the refusal
    # has to be able to say the word in order to refuse it.
    ok(text46.lower().count("profitable") == 1
       and "profitable" in phase46.NOT_A_PROMOTION,
       "the only use of the word profitable is the sentence denying it")
    ok(phase46.NOT_A_PROMOTION in (
        service.board(limit=1)["not_a_promotion"],
        service.journal(limit=1)["not_a_promotion"],
    ), "board and journal both carry the refusal text")

    # ======================================== a refresh is cheap
    prev = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        service.reset()
        try:
            service.record(p45_rows(observation(ts=ts), live))
            started = time.time()
            for _ in range(20):
                service.board(instrument="NIFTY", limit=5)
            elapsed = time.time() - started
            ok(elapsed < 2.0,
               f"twenty board reads cost {elapsed:.3f}s — no historical scan "
               f"runs on a refresh")
            board_text = source_of(service).split("def board")[1]
            ok("phase35" not in board_text and "phase39" not in board_text
               and "ingest" not in board_text,
               "and the board reads nothing but its own journal")
        finally:
            service.reset()
            settings.data_dir = prev

    # ======================================== the switch
    ok(settings.phase46_overlay is True and service.enabled() is True,
       "the overlay records by default")
    settings.phase46_overlay = False
    try:
        ok(service.enabled() is False
           and service.record(list(rows.values()))["written"] == 0,
           "and writes nothing at all when switched off")
    finally:
        settings.phase46_overlay = True
    prev_shadow = settings.phase45_shadow
    settings.phase45_shadow = False
    try:
        ok(service.enabled() is False,
           "turning the evidence off turns its view off too")
    finally:
        settings.phase45_shadow = prev_shadow

    # ======================================== failure containment
    ok(service.record([{"vehicle": "CE"}])["written"] == 0,
       "a malformed hand-over is absorbed")
    ok(service.stats()["failures"] >= 0 and isinstance(service.stats(), dict),
       "and the writer stays readable afterwards")

    print()
    print(f"PHASE 46 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
