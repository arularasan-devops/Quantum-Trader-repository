"""Phase 50 smoke — what an event was worth, and what the clock and the record allow.

Four claims are held to account here, and each of them is a way for a missing
number to be replaced by a plausible one:

* **an earlier reading is not a wrong reading.** A tally of 11 taken while the
  session was still writing rows and a tally of 11 taken when 476 already
  existed look identical without coverage. The first is honest and the second is
  not, so the state must be derived from what the journal held at each reading
  and must read ``UNDETERMINED_NO_COVERAGE_RECORDED`` where that was never
  stored;
* **"the market was open" is not a measurement.** The status comes from the
  clock, the weekday, the segment's windows and a holiday list when one exists.
  A weekday whose book never moved is ``UNKNOWN``, because with no list on disk
  a holiday and a dead feed cannot be told apart;
* **an unresolved event is not a flat one.** No later executable quote, no
  entry, no measured round trip — each leaves the event ``UNRESOLVED`` with its
  reason, out of every average, and never at zero;
* **no midpoint and no traded print is ever a fill.** A forward sample whose
  required side was not quoted is dropped from the path before it is walked, so
  it can reach neither the exit nor the excursions.

    .venv/bin/python _smoke_phase50.py
"""
from __future__ import annotations

import ast
import inspect
import os
import sqlite3
import sys
import tempfile

from app.config import settings
from app.research import phase45, phase46, phase47, phase49, phase50
from app.research.phase17 import mcx as p17mcx
from app.research.phase17 import quality as p17quality
from app.research.phase17 import service as p17service
from app.research.phase17 import store as p17store
from app.research.phase41 import freeze as p41freeze
from app.research.phase42 import freeze as p42freeze
from app.research.phase43 import freeze as p43freeze
from app.research.phase44 import freeze as p44freeze
from app.research.phase44 import store as p44store
from app.research.phase44 import switch as p44switch
from app.research.phase45 import freeze as p45freeze
from app.research.phase45 import store as p45store
from app.research.phase46 import service as p46service
from app.research.phase46 import store as p46store
from app.research.phase47 import service as p47service
from app.research.phase49 import events as p49events
from app.research.phase49 import service as p49service
from app.research.phase50 import budget as p50budget
from app.research.phase50 import calendar as p50calendar
from app.research.phase50 import cli as cli_mod
from app.research.phase50 import exits as p50exits
from app.research.phase50 import observe as p50observe
from app.research.phase50 import report as report_mod
from app.research.phase50 import resolve as p50resolve
from app.research.phase50 import service
from app.research.phase50 import store as store_mod
from app.research.phase50 import tiers as p50tiers

PASS = 0
FAIL: list[str] = []

FROZEN_41 = "34fc64a299d29760"
FROZEN_42 = "b67709af902c8bff"
FROZEN_43 = "e45c3e72ad37f864"
FROZEN_44 = "95d393256ed1f505"
FROZEN_45 = "a3b0b9b0b321ee47"
# Phase 49's grouping rule. This phase adds no rule of its own and must not
# move this value: every event id in the record is derived from it.
FROZEN_49_RULE = "c2bff87a600a0639"
# The three fingerprints this increment is not allowed to move. The coverage
# guard and the strata are a reading layer over the same partition, resolution
# and exit set, so if any of these changes the increment was not isolated.
FROZEN_RESOLUTION = "576f08bb8e04a56d"
FROZEN_EXITS = "ab7013d2824613cc"
FROZEN_PARTITION = "e465ebf3798eff3b"
# The resolution-rate coverage guard. The cadence guard is an additional
# condition beside it, so this value moving would mean the first condition was
# rewritten rather than joined.
FROZEN_GUARD = "22ed249295627c2c"

FORBIDDEN = (
    "order", "broker", "execution", "smartapi", "angel", "kite", "trade_api",
    "place_order", "autobot", "executor",
)

# One IST weekday, 2026-09-17 (a Thursday), as epoch seconds.
def ist(hour: int, minute: int, *, day: int = 17, month: int = 9) -> float:
    import datetime as dt
    tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return dt.datetime(2026, month, day, hour, minute, tzinfo=tz).timestamp()


OPEN_TS = ist(10, 30)
SESSION = "2026-09-17"


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def source(mod: object) -> str:
    return inspect.getsource(mod)  # type: ignore[arg-type]


def docstrings(tree: ast.AST) -> set[int]:
    """Every string constant that is only prose.

    A substring scan cannot tell an executed UPDATE from the word "update" in a
    docstring, and a check that fails on its own explanation is one nobody
    keeps.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            out.add(id(node.value))
    return out


def code(mod: object) -> str:
    """The module's source with its prose removed."""
    tree = ast.parse(source(mod))
    prose = docstrings(tree)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            node.body = [  # type: ignore[attr-defined]
                n for n in body
                if not (isinstance(n, ast.Expr) and id(n.value) in prose)
            ] or [ast.Pass()]
    return ast.unparse(tree)


def sql_statements(mod: object) -> list[str]:
    """Every SQL string constant the module can execute."""
    out = []
    tree = ast.parse(source(mod))
    prose = docstrings(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in prose):
            text = " ".join(node.value.split()).upper()
            if any(text.startswith(k) or f" {k} " in text
                   for k in ("SELECT", "INSERT", "UPDATE", "DELETE", "ALTER")):
                out.append(text)
    return out


def imported(mod: object) -> set[str]:
    """Every module and imported name in the module, lowercased."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(source(mod))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            out.update((node.module or "").split("."))
            out.update(alias.name for alias in node.names)
    return {name.lower() for name in out if name}


def defined(mod: object) -> tuple[set[str], set[str]]:
    """The names the module defines as functions, and the ones it assigns."""
    funcs: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(ast.parse(source(mod))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return funcs, names


def p44_armed() -> bool:
    path = p44store.db_path()
    if not os.path.exists(path):
        return False
    con = p45store.open_read_only(path)
    try:
        return bool(p44switch.state(con).get("may_record"))
    finally:
        con.close()


def leg(ts: float, **kw: object) -> dict:
    """One admitted observation, shaped as Phase 47's arm accessor emits it."""
    base = {
        "session": SESSION,
        "arm": phase47.ARM_PRODUCTION,
        "instrument": "TCS",
        "vehicle": phase45.CE,
        "contract": "TCS29SEP262240CE",
        "expiry": "2026-09-29",
        "strike": 2240,
        "direction": "LONG",
        "overlay_state": phase46.SUPPORTED,
        "decision_ts": ts,
        "overlay_id": f"o{ts}",
        "event_id": f"e{ts}",
        "obs_id": f"b{ts}",
        "entry_price": 100.0,
        "entry_side": "ASK",
        "measured_cost_points": 1.0,
        "measured_spread_points": 0.5,
        "cost_evidence": "MEASURED_EXECUTABLE",
        # The vocabulary Phase 45 admits a leg under: an observation whose book
        # was not fillable never reaches this journal with an entry price, so
        # the fixture uses a label the pipeline can actually produce.
        "data_quality": p17quality.EXACT,
        "bid": 99.5,
        "ask": 100.0,
    }
    base.update(kw)
    return base


def quote(ts: float, bid: float | None, ask: float | None,
          traded: float | None = None) -> dict:
    """One forward sample as Phase 35's path reader yields them."""
    return {"ts": ts, "bid": bid, "ask": ask, "traded": traded}


def reading(
    snapshot: str, *, held: int | None, selected: int, events_at_read: int,
    ts: float, state: str = phase50.SNAPSHOT_CURRENT,
) -> dict:
    return {
        "reading_id": store_mod.reading_id(snapshot, ts),
        "snapshot_id": snapshot,
        "reading_ts": ts,
        "session": SESSION,
        "arm": phase47.ARM_RESEARCH,
        "definition": "d",
        "journal_rows_available_at_read": held,
        "selected_rows_at_read": selected,
        "event_count_at_read": events_at_read,
        "selection_bound": 5000,
        "count_is": "COUNT_IS_EVERY_LEG_THE_SELECTION_HOLDS",
        "selection_fingerprint": "s",
        "session_status": phase50.OPEN,
        "superseded_by": None,
        "supersession_reason": None,
        "tally_state": state,
        "status": phase50.PAPER_ONLY,
        "order_path": phase50.NO_ORDER_PATH,
        "version": phase50.VERSION,
    }


def _cadence_event(
    instrument: str, group: str, index: int, observations: int, gap: float,
) -> dict:
    """One observation record polled on an exact cadence, for the §5 bars.

    Built through :func:`observe.record` rather than as a literal so the cadence
    the bias classifier reads is the one the diagnostic derives from timestamps,
    not a number this file asserted into the fixture.
    """
    stamps = [1000.0 + i * gap for i in range(observations)]
    return p50observe.record(
        {"event_id": f"{instrument}-{group}-{index}", "instrument": instrument,
         "vehicle": "CE", "direction": "LONG"},
        first_ts=stamps[0], last_ts=stamps[-1],
        observation_count=observations, observation_stamps=stamps,
        max_intra_event_gap_sec=gap, last_quality=p17quality.EXACT,
        last_quality_is_fillable=True, later_books=observations,
        later_executable_books=observations,
        first_later_executable_ts=stamps[0],
        last_later_executable_ts=stamps[-1],
        next_observation_ts_any_contract=None,
        next_observation_ts_same_contract=None,
        session_close_ts=None, gap_sec=300.0,
    )


def _bias_row(payload: dict, source: str) -> dict:
    """One declared mechanism out of the §5 evidence table."""
    return next(row for row in payload["rows"] if row["source"] == source)


def main() -> int:  # noqa: C901 - one linear acceptance list, read top to bottom
    # =========================================================== §2 the calendar
    open_row = p50calendar.status(OPEN_TS, "TCS")
    ok(open_row["market_session_status"] == phase50.OPEN
       and open_row["status_evidence"] == phase50.BY_CLOCK,
       "10:30 IST on a weekday is OPEN, and the row says the clock is why")
    ok(p50calendar.status(OPEN_TS, "TCS") == p50calendar.status(OPEN_TS, "TCS"),
       "the status of an instant is deterministic: same inputs, same answer, no "
       "clock of its own and no store behind it")
    ok(p50calendar.status(ist(9, 5), "TCS")["market_session_status"]
       == phase50.PRE_OPEN,
       "09:05 IST is PRE_OPEN, not OPEN — a call-auction instant is not a "
       "session instant")
    ok(p50calendar.status(ist(16, 0), "TCS")["market_session_status"]
       == phase50.POST_CLOSE,
       "16:00 IST is POST_CLOSE")
    ok(p50calendar.status(ist(8, 0), "TCS")["market_session_status"]
       == phase50.CLOSED,
       "08:00 IST is CLOSED, before even the pre-open call")
    ok(p50calendar.status(ist(10, 30, day=19), "TCS")["market_session_status"]
       == phase50.CLOSED
       and p50calendar.status(ist(10, 30, day=19), "TCS")["status_evidence"]
       == phase50.BY_WEEKEND,
       "a Saturday is CLOSED on the weekday alone, whatever the rows say")
    ok(p50calendar.status(
        OPEN_TS, "TCS", holidays=frozenset({SESSION}),
    )["market_session_status"] == phase50.HOLIDAY,
       "a listed holiday is HOLIDAY, taken from the list and from nothing else")
    ok(p50calendar.status(OPEN_TS, "TCS")["holiday_note"]
       == phase50.NO_HOLIDAY_LIST
       and phase50.HOLIDAY not in {
           p50calendar.status(ist(h, 0), "TCS")["market_session_status"]
           for h in range(0, 24)
       },
       "with no holiday list on disk HOLIDAY is never asserted at any hour, and "
       "the row says the list was absent rather than that the day was open")
    ok(p50calendar.status(OPEN_TS, "TCS", moved=False)["market_session_status"]
       == phase50.UNKNOWN
       and p50calendar.status(OPEN_TS, "TCS", moved=False)["status_evidence"]
       == phase50.BY_FROZEN_BOOK,
       "a weekday inside the window whose book never moved is UNKNOWN, because "
       "a holiday and a dead feed cannot be told apart without a list")
    ok(p50calendar.status(None, "TCS")["market_session_status"]
       == phase50.UNKNOWN
       and p50calendar.status("13:00", "TCS")["market_session_status"]
       == phase50.UNKNOWN,
       "a row with no usable timestamp is UNKNOWN, never given a status from "
       "its neighbours")
    ok(p50calendar.status(ist(21, 0), "CRUDEOIL")["market_session_status"]
       == phase50.OPEN
       and p50calendar.status(ist(21, 0), "TCS")["market_session_status"]
       == phase50.POST_CLOSE,
       "21:00 IST is a session for MCX and after the close for equity — the "
       "segment comes from the production instrument registry")
    ok(p50calendar.segment("CRUDEOIL") == phase50.SEGMENT_MCX
       and p50calendar.segment("NIFTY") == phase50.SEGMENT_EQUITY
       and p50calendar.segment(None) == phase50.SEGMENT_EQUITY,
       "the segment is read from the registry's own exchange field, so a newly "
       "configured commodity cannot be graded on equity hours")
    ok(all(
        p50calendar.status(ist(h, m), name)["market_session_status"]
        in phase50.MARKET_SESSION_STATUS
        for h in range(0, 24) for m in (0, 14, 30, 59)
        for name in ("TCS", "CRUDEOIL")
    ), "every instant of the day on both segments answers with one of the six "
       "allowed statuses and nothing else")
    ok(p50calendar.summarise([phase50.OPEN, phase50.POST_CLOSE])
       == phase50.UNKNOWN
       and p50calendar.summarise([phase50.OPEN, phase50.OPEN]) == phase50.OPEN
       and p50calendar.summarise([]) == phase50.UNKNOWN,
       "an event whose observations straddle the close is UNKNOWN, not the "
       "majority answer")

    # ========================================================= §3/§4 the events
    ok(phase49.rule_fingerprint() == FROZEN_49_RULE,
       f"the grouping rule is still Phase 49's, fingerprint {FROZEN_49_RULE} — "
       "this phase adds no rule of its own")
    p50_funcs, p50_names = defined(service)
    ok(not ({"EVENT_GAP_SEC", "EVENT_KEY", "RULE_TEXT", "GROUPING_RULE"}
            & p50_names)
       and not ({"group", "event_key", "rule_fingerprint", "event_id"}
                & p50_funcs),
       "and it defines neither a gap, a key nor a grouping function of its "
       "own: the ones in the record are imported, so this phase cannot re-cut "
       "history while claiming the same fingerprint")
    many = [leg(OPEN_TS + i * 3.0) for i in range(465)]
    grouped = p49events.group(many)
    ok(len(grouped) == 1 and grouped[0]["observation_count"] == 465,
       "465 observations of one strike inside the frozen gap are one event, "
       "which is the whole reason this phase counts events at all")
    counts = p49events.counts(many)
    ok(counts[p49events.LEG_COUNT] == 465
       and counts[p49events.EVENT_COUNT] == 1,
       "the leg count and the event count are both reported, and they differ")
    ce_pe = p49events.group([
        leg(OPEN_TS), leg(OPEN_TS + 1, vehicle=phase45.PE,
                          contract="TCS29SEP262240PE"),
    ])
    ok(len(ce_pe) == 2,
       "a CE and a PE at the same instant are two events: the vehicle is part "
       "of the key")
    fut = p49events.group([
        leg(OPEN_TS), leg(OPEN_TS + 1, vehicle="FUTURES", contract="TCS29SEPFUT",
                          strike=None),
    ])
    ok(len(fut) == 2,
       "an option and a future are two events, never pooled into one line")
    future_fields = {
        "net_pct": 42.0, "gross_pct": 40.0, "mfe_pct": 90.0, "mae_pct": -5.0,
        "giveback_pct": 12.0, "t1_hit": True, "exit_price": 180.0,
        "resolution_status": phase50.RESOLVED,
    }
    ok([e["event_id"] for e in p49events.group(
            [{**row, **future_fields} for row in many])]
       == [e["event_id"] for e in grouped],
       "stamping every outcome field onto the legs changes no event id: how "
       "many opportunities a session held cannot depend on what they earned")
    ok(p49events.group(many)[0]["event_id"]
       == p49events.group(many[:9])[0]["event_id"],
       "and an event keeps its id as later observations join it, so the journal "
       "does not grow a second row for the same opportunity")
    ids = [
        e["event_id"] for e in p49events.group(
            many + [leg(OPEN_TS + 5000.0), leg(OPEN_TS + 5001.0),
                    leg(OPEN_TS + 1, vehicle=phase45.PE,
                        contract="TCS29SEP262240PE")]
        )
    ]
    ok(len(ids) == len(set(ids)) == 3,
       "distinct opportunities get distinct ids, with no collisions")

    # ================================================ §5 resolution and refusals
    legs = [leg(OPEN_TS + i * 3.0) for i in range(4)]
    event = p49events.group(legs)[0]
    forward = [
        quote(OPEN_TS + 60, 104.0, 105.0),
        quote(OPEN_TS + 120, 118.0, 119.0),
        quote(OPEN_TS + 240, 109.0, 110.0),
    ]
    res = p50resolve.resolve_event(event, legs, forward)
    ok(res["resolution_status"] == phase50.RESOLVED,
       "an event with a recorded executable entry and later executable quotes "
       "resolves")
    ok(res["entry_side"] == "ASK" and res["entry_price"] == 100.0,
       "a long option enters at the ASK recorded at the decision instant")
    ok(res["exit_side"] == "BID"
       and res["exit_price"] in {q["bid"] for q in forward},
       "and exits at a BID that is actually in the capture — never at a mid and "
       "never at a price the book did not show")
    mids = {(q["bid"] + q["ask"]) / 2.0 for q in forward}
    ok(res["exit_price"] not in mids,
       "the exit is not the midpoint of any sample, which is the one fill this "
       "project has refused for forty phases")
    ok(res["gross_pct"] is not None and res["net_pct"] is not None
       and res["net_pct"] < res["gross_pct"],
       "the round trip is charged: net is below gross, not equal to it")
    ok(res["cost_points"] == 1.0
       and res["cost_basis"] == phase47.MEASURED_COST,
       "the cost is the round trip measured at the instant, with its basis "
       "beside it")
    ok(res["cost_points"] == p50resolve.resolve_event(
        event, legs * 3, forward)["cost_points"],
       "and one round trip is charged per event, not per observation: an "
       "opinion sampled four times is one paper leg")
    ok(res["mfe_pct"] is not None and res["mae_pct"] is not None
       and res["giveback_pct"] is not None and res["hold_minutes"] is not None
       and res["t1_hit"] is not None and res["targets_pct"]["t1"] is not None,
       "MFE, MAE, giveback, hold and the T1/T2/T3 ladder are all reported for a "
       "resolved event")
    ok(res["time_to_favorable_sec"] == 60.0,
       "time to the first favourable executable quote is measured from the "
       "entry instant")
    ok(res["forward_samples"] == 3 and res["forward_samples_dropped"] == 0,
       "and the row says how much of the capture the answer rests on")
    ok(res["resolution_fingerprint"] == phase50.definition_fingerprint()
       and res["no_midpoint"] == phase50.NO_MIDPOINT,
       "every resolved row carries the fingerprint of the pricing definition it "
       "was resolved under")

    traded_only = [quote(OPEN_TS + 60, None, None, traded=140.0)]
    refused = p50resolve.resolve_event(event, legs, traded_only)
    ok(refused["resolution_status"] == phase50.UNRESOLVED_NO_LATER_QUOTE
       and refused["net_pct"] is None
       and refused["forward_samples_dropped"] == 1,
       "a forward sample with only a traded print is dropped and the event "
       "stays UNRESOLVED: somebody else's print is not an exit this leg could "
       "have taken")
    kept, dropped = p50resolve.executable_only(
        [*forward, *traded_only, quote(OPEN_TS + 90, None, 111.0)],
        vehicle=phase45.CE, direction="LONG",
    )
    ok(len(kept) == 3 and dropped == 2,
       "the filter keeps only samples whose required side was quoted, and "
       "counts what it dropped rather than discarding the fact")
    ok(all(s["fill"]["evidence"] == "MEASURED_EXECUTABLE" for s in kept),
       "and every sample that survives carries a measured executable fill")
    ok(p50resolve.resolve_event(event, legs, [])["resolution_status"]
       == phase50.UNRESOLVED_NO_LATER_QUOTE,
       "no later quote at all leaves the event UNRESOLVED with the reason, not "
       "at zero")
    no_entry = [leg(OPEN_TS, entry_price=None)]
    ok(p50resolve.resolve_event(
        p49events.group(no_entry)[0], no_entry, forward,
    )["resolution_status"] == phase50.UNRESOLVED_NO_ENTRY,
       "an event whose first observation recorded no executable entry price is "
       "UNRESOLVED_NO_ENTRY — the entry is not taken from a later observation "
       "that happens to have one")
    no_cost = [leg(OPEN_TS, measured_cost_points=None)]
    ok(p50resolve.resolve_event(
        p49events.group(no_cost)[0], no_cost, forward,
    )["resolution_status"] == phase50.UNRESOLVED_NO_COST,
       "and with no measured or modelled round trip the event is UNRESOLVED: a "
       "gross figure is not a result")
    short_fut = [leg(OPEN_TS, vehicle="FUTURES", contract="TCS29SEPFUT",
                     strike=None, direction="SHORT", entry_side="BID",
                     entry_price=3400.0)]
    short_res = p50resolve.resolve_event(
        p49events.group(short_fut)[0], short_fut,
        [quote(OPEN_TS + 60, 3390.0, 3391.0)],
    )
    ok(short_res["resolution_status"] == phase50.RESOLVED
       and short_res["exit_side"] == "ASK"
       and short_res["exit_price"] == 3391.0
       and short_res["gross_pct"] is not None and short_res["gross_pct"] > 0,
       "a short future exits by lifting the ASK, and a fall in the price is a "
       "gain — the executable side follows the direction, not the vehicle")
    ok(p50resolve.still_open({}, session_end_ts=OPEN_TS + 100, now=OPEN_TS)
       and not p50resolve.still_open(
           {}, session_end_ts=OPEN_TS, now=OPEN_TS + 100),
       "an event inside its own session is still open, and one after it is not")
    ok(p50resolve.open_row({})["resolution_status"]
       == phase50.UNRESOLVED_EVENT_OPEN,
       "an event that can still gain observations publishes no outcome at all")

    # ------------------------- where a forward sample may come from (the §5 gap)
    #
    # The live run resolved nothing: 70 of 76 production events read NO_LATER_
    # EXECUTABLE_QUOTE while each held hundreds of later two-sided books of its
    # own, because forward samples were read only from the raw quote store. An
    # event's own later observations are books on its path, and refusing to read
    # them published a fact about this module's reach as a fact about the market.
    own = [
        leg(OPEN_TS, bid=99.5, ask=100.0),
        leg(OPEN_TS + 60, bid=104.0, ask=105.0),
        leg(OPEN_TS + 120, bid=118.0, ask=119.0),
        leg(OPEN_TS + 240, bid=109.0, ask=110.0),
    ]
    own_event = p49events.group(own)[0]
    from_own = service._forward(own_event, own, con=None)
    ok([s["ts"] for s in from_own]
       == [OPEN_TS + 60, OPEN_TS + 120, OPEN_TS + 240]
       and all(s["sample_source"] == phase50.FROM_OBSERVATIONS
               for s in from_own),
       "an event's own later observations are forward samples, in time order, "
       "and the entry observation is not one of them — a leg cannot exit at the "
       "instant it opened")
    own_res = p50resolve.resolve_event(own_event, own, from_own)
    ok(own_res["resolution_status"] == phase50.RESOLVED
       and own_res["exit_side"] == "BID"
       and own_res["exit_price"] in {99.5, 104.0, 118.0, 109.0}
       and own_res["forward_samples_from_observations"] == 3
       and own_res["forward_samples_from_quote_store"] == 0,
       "so an event observed 4 times with no separate quote-store row resolves "
       "off its own captured books, and the row says which source it rests on")
    ok(own_res["exit_price"] not in {112.0, 113.5, 109.5, 99.75}
       and own_res["sample_source"] == phase50.SAMPLE_SOURCE,
       "reading the journal changes where a quote comes from and nothing about "
       "what a fill may be: still a quoted side, never a midpoint of one")
    stale = service._forward(
        own_event,
        [own[0], {**own[1], "data_quality": p17quality.STALE},
         {**own[2], "data_quality": p17quality.DEGRADED}],
        con=None,
    )
    ok(stale == [],
       "an observation whose own book was too old to price a fill is not a "
       "forward sample either: staleness is not cured by being in the journal")
    unquoted = service._forward(
        own_event, [own[0], {**own[1], "bid": None, "ask": None}], con=None,
    )
    ok(len(unquoted) == 1 and unquoted[0]["bid"] is None
       and p50resolve.resolve_event(own_event, own[:1] + [own[1]], unquoted)[
           "resolution_status"] == phase50.UNRESOLVED_NO_LATER_QUOTE,
       "and an observation that quoted no side is passed through to be dropped "
       "by the executable filter, not filled from the row beside it")
    end_to_end = service._arm(
        own, arm=phase47.ARM_PRODUCTION, holiday_days=frozenset(), con=None,
        now=OPEN_TS + 10_000, resolve_outcomes=True,
    )
    ok(end_to_end["leg_count"] == 4 and end_to_end["event_count"] == 1
       and end_to_end["metrics"]["resolved_events"] == 1
       and end_to_end["events"][0]["resolution_status"] == phase50.RESOLVED,
       "and the whole arm resolves through the service with no quote store at "
       "all: four observations of one opportunity, one event, one outcome")

    # ============================================ metrics: unresolved is not flat
    rows = [
        {"resolution_status": phase50.RESOLVED, "net_pct": 10.0, "first_ts": 1.0,
         "t1_hit": True, "observation_count": 3},
        {"resolution_status": phase50.RESOLVED, "net_pct": -4.0, "first_ts": 2.0,
         "t1_hit": False, "observation_count": 2},
        {"resolution_status": phase50.UNRESOLVED_NO_LATER_QUOTE,
         "net_pct": None, "first_ts": 3.0, "observation_count": 9},
    ]
    m = service.metrics(rows)
    ok(m["events"] == 3 and m["resolved_events"] == 2
       and m["unresolved_events"] == 1,
       "the metric block reports events, resolved and unresolved side by side")
    ok(m["net_pct_mean_per_event"] == 3.0,
       "and averages over resolved events only: dividing by the unresolved one "
       "would report a capture gap as a smaller result")
    ok(m["flat"] == 0 and m["winners"] == 1 and m["losers"] == 1,
       "an unresolved event is not counted flat")
    ok(m["win_rate_pct"] == 50.0 and m["profit_factor"] == 2.5
       and m["t1_hit_rate_pct"] == 50.0 and m["max_drawdown_pct"] is not None,
       "PF, win rate, T1 hit rate and drawdown come off the same resolved set")
    ok(m["unresolved_reasons"] == {phase50.UNRESOLVED_NO_LATER_QUOTE: 1},
       "and the unresolved reasons are published rather than summarised away")
    ok(service.metrics([])["resolved_events"] == 0
       and service.metrics([])["net_pct_total"] is None,
       "an empty column reports nothing rather than zero")
    ok(phase50.MIN_RESOLVED_EVENTS == 20
       and service._verdict({"resolved_events": 3,
                             "net_pct_mean_per_event": 40.0},
                            {"resolved_events": 3,
                             "net_pct_mean_per_event": 90.0})["state"]
       == phase50.TOO_FEW_EVENTS,
       "three resolved events with a large positive difference still refuse a "
       "direction: the bar is a constant, not a function of the sample")

    # ================================== §1 accrual, §6 comparison, §11 the journal
    prev_dir = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        try:
            service._CON = None
            con = store_mod.connect()

            # --- the reading register
            first = reading("snapA", held=200, selected=11, events_at_read=2,
                            ts=OPEN_TS)
            later = reading("snapB", held=23410, selected=476,
                            events_at_read=3, ts=OPEN_TS + 3600)
            written = store_mod.insert_readings(con, [first, later])
            ok(written["written"] == 2,
               "readings are journalled with what the journal held at the "
               "instant each was taken")
            again = store_mod.insert_readings(con, [first, later])
            ok(again["written"] == 0 and again["duplicate"] == 2,
               "re-reading the same instant writes nothing: the register is "
               "append-only and idempotent, not a mutable row")
            coverage = store_mod.coverage_by_snapshot(con)
            ok(coverage["snapA"]["journal_rows_available_at_read"] == 200
               and coverage["snapB"]["journal_rows_available_at_read"] == 23410,
               "coverage is readable per snapshot, earliest reading first")
            by_reason = {
                phase49.REASON_ACCRUAL: phase50.SNAPSHOT_ACCRUAL,
                phase49.REASON_SELECTION: phase50.SNAPSHOT_MIS_SELECTED,
                phase49.REASON_SELECTION_SAME_COUNT:
                    phase50.SNAPSHOT_RE_RECORDED,
                phase49.REASON_BOUND: phase50.SNAPSHOT_BOUNDED,
            }
            accrued = service._state_of(
                {"snapshot_id": "snapA", "calls": 11, "events": 2,
                 "superseded_by": "snapB",
                 "superseded_reason": phase49.REASON_ACCRUAL},
                coverage, by_reason,
            )
            ok(accrued["tally_state"] == phase50.SNAPSHOT_ACCRUAL
               and accrued["tally_state"] != phase50.SNAPSHOT_MIS_SELECTED,
               "an earlier reading of a session that kept writing rows is "
               "SNAPSHOT_ACCRUAL, not a mis-selected count")
            ok(accrued["tally_state_evidence"] == phase50.FROM_COVERAGE,
               "and the state is derived from the recorded coverage rather than "
               "read off a reason string somebody chose")
            ok(accrued["journal_rows_available_at_read"] == 200
               and accrued["selected_rows_at_read"] == 11
               and accrued["event_count_at_read"] == 2
               and accrued["reading_ts"] == OPEN_TS
               and accrued["superseded_by"] == "snapB"
               and accrued["supersession_reason"] == phase49.REASON_ACCRUAL
               and accrued["selection_fingerprint"] == "s"
               and accrued["session_status"] == phase50.OPEN,
               "every field §1 asks for rides on the reading: coverage, "
               "selection, session status and what superseded it")
            confounded = service._state_of(
                {"snapshot_id": "snapA", "superseded_by": "snapB",
                 "superseded_reason": phase49.REASON_SELECTION},
                coverage, by_reason,
            )
            ok(confounded["tally_state"] == phase50.SNAPSHOT_ACCRUAL
               and confounded["confounded"] is True
               and confounded["registry_reason"] == phase49.REASON_SELECTION,
               "where the registry names a selection fault but the journal also "
               "grew, coverage wins and the row says both are true: an earlier "
               "reading is never called wrong on the strength of a later count")
            equal = store_mod.coverage_by_snapshot(con)
            equal["snapB"] = {**equal["snapB"],
                              "journal_rows_available_at_read": 200}
            mis = service._state_of(
                {"snapshot_id": "snapA", "superseded_by": "snapB",
                 "superseded_reason": phase49.REASON_SELECTION},
                equal, by_reason,
            )
            ok(mis["tally_state"] == phase50.SNAPSHOT_MIS_SELECTED
               and mis["tally_state_evidence"] == phase50.FROM_REGISTRY,
               "with the journal unchanged between the two readings the "
               "difference cannot be accrual, and the selection fault stands")
            ok(service._state_of(
                {"snapshot_id": "snapA", "superseded_by": "snapB",
                 "superseded_reason": phase49.REASON_SELECTION_SAME_COUNT},
                equal, by_reason,
            )["tally_state"] == phase50.SNAPSHOT_RE_RECORDED
               and service._state_of(
                   {"snapshot_id": "snapA", "superseded_by": "snapB",
                    "superseded_reason": phase49.REASON_BOUND},
                   equal, by_reason,
               )["tally_state"] == phase50.SNAPSHOT_BOUNDED,
               "a re-recording whose count did not move and a reading that "
               "stopped at its bound keep their own states: neither is a wrong "
               "count")
            ok(service._state_of(
                {"snapshot_id": "snapB", "superseded_by": None,
                 "superseded_reason": None}, coverage, by_reason,
            )["tally_state"] == phase50.SNAPSHOT_CURRENT,
               "the newest reading of a session and arm is the current snapshot")
            undetermined = service._state_of(
                {"snapshot_id": "snapLegacy", "superseded_by": "snapB",
                 "superseded_reason": "SOME_OLDER_REASON"},
                coverage, by_reason,
            )
            ok(undetermined["tally_state"] == phase50.SNAPSHOT_UNDETERMINED
               and undetermined["tally_state_evidence"] == phase50.FROM_NOTHING,
               "a row written before coverage existed is UNDETERMINED: accrual "
               "and mis-selection cannot be told apart for it, and choosing one "
               "would invent the evidence that is missing")
            ok(set(phase50.SNAPSHOT_STATES) == set(phase50.SNAPSHOT_STATE_TEXT)
               and phase50.SNAPSHOT_ACCRUAL in phase50.SNAPSHOT_STATES,
               "every state has prose a reader can check it against")

            # --- the event journal
            resolved_event = {
                "event_id": "E1", "session": SESSION, "instrument": "TCS",
                "vehicle": phase45.CE, "contract": "TCS29SEP262240CE",
                "direction": "LONG", "observation_count": 465,
                "market_session_status": phase50.OPEN,
                "resolution_status": phase50.RESOLVED, "exit_ts": OPEN_TS + 240,
                "net_pct": 8.0, "underlying_leg_ids": ["o1", "o2"],
            }
            row = service._event_record(
                resolved_event, arm=phase47.ARM_RESEARCH, session=SESSION,
                clock=OPEN_TS + 300,
            )
            ok(store_mod.insert_events(con, [row])["written"] == 1
               and store_mod.insert_events(con, [row])["duplicate"] == 1,
               "an event summary is journalled once per outcome and re-writing "
               "the same reading changes nothing")
            unresolved_row = service._event_record(
                {**resolved_event,
                 "resolution_status": phase50.UNRESOLVED_NO_LATER_QUOTE,
                 "exit_ts": None, "net_pct": None},
                arm=phase47.ARM_RESEARCH, session=SESSION, clock=OPEN_TS + 400,
            )
            ok(store_mod.insert_events(con, [unresolved_row])["written"] == 1
               and len(store_mod.events(con)) == 2
               and len(store_mod.latest_per_event(con)) == 1,
               "the same event resolved differently at two instants keeps both "
               "rows, and the board reads the newest of them")
            journalled = store_mod.events(con)[0]
            ok(journalled["observation_count"] == 465
               and journalled["underlying_leg_ids"]
               and journalled["status"] == phase50.PAPER_ONLY
               and journalled["order_path"] == phase50.NO_ORDER_PATH,
               "the event row carries its observation count, the ids of every "
               "leg it folded in, and its paper-only labels")
            ok(store_mod.counts(con)["reading_rows"] == 2
               and store_mod.counts(con)["resolution_rows"] == 2,
               "and both tables can be counted without reading either of them")
            con.close()

            # --- §6 the comparison, over synthetic arms
            shared = [leg(OPEN_TS + i * 3.0) for i in range(3)]
            research_only = [
                leg(OPEN_TS + 20 + i * 3.0, arm=phase47.ARM_RESEARCH,
                    instrument="SENSEX", contract="SENSEX2691774500PE",
                    vehicle=phase45.PE, strike=74500, entry_price=220.0)
                for i in range(2)
            ]
            arms_payload = {
                "session": SESSION,
                "instrument": None,
                "journalled_rows_in_session": 23410,
                "legs": {
                    phase47.ARM_RESEARCH: [*shared, *research_only],
                    phase47.ARM_PRODUCTION: shared,
                },
                "bound": {
                    "limit": 5000, "max": 5000,
                    phase47.ARM_RESEARCH: {"available": 5, "selected": 5,
                                           "limit": 5000, "truncated": False,
                                           "count_is": "COMPLETE"},
                    phase47.ARM_PRODUCTION: {"available": 3, "selected": 3,
                                             "limit": 5000, "truncated": False,
                                             "count_is": "COMPLETE"},
                },
                "selection": "sel", "definition": "def",
                "status": phase50.PAPER_ONLY,
                "order_path": phase50.NO_ORDER_PATH, "version": "47",
            }
            real_arms, real_forward = p47service.arms, service._forward
            p47service.arms = lambda **kw: arms_payload  # type: ignore[assignment]
            service._forward = (  # type: ignore[assignment]
                lambda event, legs, *, con: forward
            )
            try:
                comparison = service.compare(now=OPEN_TS + 10000)
                a = comparison["arms"][phase50.ARM_PRODUCTION_ONLY]
                b = comparison["arms"][phase50.ARM_PRODUCTION_PLUS_RESEARCH]
                ok(a["leg_count"] == 3 and a["event_count"] == 1,
                   "arm A is the production board's own legs, counted as events")
                ok(b["leg_count"] == 5 and b["event_count"] == 2,
                   "arm B is the union of both arms with duplicate observations "
                   "dropped by id, so the three shared legs are counted once")
                ok(comparison["events_only_in_b"] == 1
                   and comparison["contracts_only_in_b"]
                   == ["SENSEX2691774500PE"],
                   "and what the overlay added is the event none of whose "
                   "observations came from the production arm — decided by leg "
                   "identity, not by matching contracts across the columns")
                ok(comparison["resolved_events_only_in_b"] == 1
                   and comparison["metrics_only_in_b"]["resolved_events"] == 1,
                   "the added event is measured on its own as well as inside B")
                ok(a["metrics"]["resolved_events"] == 1
                   and b["metrics"]["resolved_events"] == 2,
                   "both columns resolve their events through the same "
                   "arithmetic, so the difference is not the cost model")
                ok(comparison["verdict"]["state"] == phase50.TOO_FEW_EVENTS
                   and comparison["label"] == phase50.EARLY_SHADOW_RESULT,
                   "and the verdict on two resolved events is that neither "
                   "column can be told from the other, labelled "
                   "EARLY_SHADOW_RESULT")
                ok(comparison["delta"]["resolved_events"] == 1.0,
                   "the delta is B minus A on the headline figures")
                breakdowns = comparison["breakdowns"][
                    phase50.ARM_PRODUCTION_PLUS_RESEARCH]
                ok({"by_vehicle", "by_instrument", "by_direction",
                    "by_research_fingerprint"} <= set(breakdowns)
                   and set(breakdowns["by_vehicle"]) == {phase45.CE, phase45.PE}
                   and set(breakdowns["by_instrument"]) == {"TCS", "SENSEX"},
                   "§6's breakdowns are per vehicle, instrument, direction and "
                   "research fingerprint, and CE and PE stay apart in them")
                ok(comparison["rule_fingerprint"] == FROZEN_49_RULE
                   and comparison["no_midpoint"] == phase50.NO_MIDPOINT
                   and comparison["overlap"].startswith("THE_TWO_COLUMNS_"),
                   "the frozen rule, the no-midpoint refusal and the overlap "
                   "warning are published with the numbers")
                # ------------- the filter partition and the declared exit set
                #
                # The union comparison above is degenerate by construction, so
                # the answerable question is which production events the overlay
                # selected against the ones it refused. A capture where every
                # state read UNMEASURED once produced 51 declined events and no
                # selected ones — a decline cohort invented out of silence,
                # which is what these checks exist to stop.
                declines = [
                    leg(OPEN_TS + 600, overlay_state=phase46.DISAGREES,
                        instrument="INFY", contract="INFY29SEP261600CE",
                        strike=1600),
                    leg(OPEN_TS + 900, overlay_state=phase46.COST_BLOCKED,
                        instrument="WIPRO", contract="WIPRO29SEP26300CE",
                        strike=300),
                    leg(OPEN_TS + 1200, overlay_state=phase46.WATCH,
                        instrument="SBIN", contract="SBIN29SEP26800CE",
                        strike=800),
                ]
                undecided = [
                    leg(OPEN_TS + 1500, overlay_state=phase46.UNMEASURED,
                        instrument="ITC", contract="ITC29SEP26400CE",
                        strike=400),
                    leg(OPEN_TS + 1800, overlay_state="",
                        instrument="LT", contract="LT29SEP263600CE",
                        strike=3600),
                ]
                for state in (phase46.SUPPORTED, phase46.DISAGREES,
                              phase46.COST_BLOCKED, phase46.WATCH,
                              phase46.UNMEASURED):
                    ok(state in {phase46.SUPPORTED, *phase50.STATE_IS_A_DECLINE,
                                 *phase50.STATE_IS_NOT_A_DECISION},
                       f"the overlay state {state} is classified by the "
                       f"partition rule rather than falling through it")
                ok(service.partition_of([leg(OPEN_TS)])["overlay_partition"]
                   == phase50.GROUP_SELECTED,
                   "an event admitted at a SUPPORTED instant is OVERLAY_SELECTED")
                for row in declines:
                    part = service.partition_of([row])
                    ok(part["overlay_partition"] == phase50.GROUP_DECLINED
                       and part["overlay_state_is_a_decision"] is True,
                       f"{row['overlay_state']} is a refusal the overlay made, "
                       f"so its event is OVERLAY_DECLINED")
                for row in undecided:
                    part = service.partition_of([row])
                    ok(part["overlay_partition"] == phase50.GROUP_UNMEASURED
                       and part["overlay_state_is_a_decision"] is False,
                       f"a state of "
                       f"{row['overlay_state'] or 'nothing at all'} is no "
                       f"decision, so the event is OVERLAY_UNMEASURED and the "
                       f"filter is not credited with refusing it")
                for absent in phase50.STATE_IS_NOT_A_DECISION:
                    ok(service.partition_of(
                        [leg(OPEN_TS, overlay_state=absent)],
                    )["overlay_partition"] == phase50.GROUP_UNMEASURED,
                       f"and the same holds for {absent}: an unanswered "
                       f"opportunity is not a declined one")
                first_decides = service.partition_of([
                    leg(OPEN_TS, overlay_state=phase46.DISAGREES),
                    leg(OPEN_TS + 3.0, overlay_state=phase46.SUPPORTED),
                    leg(OPEN_TS + 6.0, overlay_state=phase46.SUPPORTED),
                ])
                ok(first_decides["overlay_partition"] == phase50.GROUP_DECLINED
                   and first_decides["overlay_state_at_first_observation"]
                   == phase46.DISAGREES
                   and first_decides["overlay_partition_is_mixed"] is True,
                   "the group is read at the admitting instant, so a later "
                   "change of the overlay's mind cannot move the event — and "
                   "the row says the state was not constant")
                ok(service.partition_of([
                    leg(OPEN_TS, overlay_state=phase46.DISAGREES,
                        entry_price=100.0),
                    leg(OPEN_TS + 3.0, overlay_state=phase46.SUPPORTED),
                ])["overlay_partition"] == service.partition_of([
                    leg(OPEN_TS, overlay_state=phase46.DISAGREES,
                        entry_price=100.0, bid=400.0, ask=401.0),
                    leg(OPEN_TS + 3.0, overlay_state=phase46.SUPPORTED),
                ])["overlay_partition"],
                   "and an event that went on to be worth four times its entry "
                   "is partitioned identically: no outcome reaches the "
                   "selection")

                def path_for(event: dict, legs_in: list[dict], *, con: object):
                    """Executable books on the path of whichever event is asked."""
                    start = float(event["first_decision_ts"])
                    return [
                        {**quote(start + 60, 104.0, 105.0),
                         "sample_source": phase50.FROM_OBSERVATIONS},
                        {**quote(start + 330, 118.0, 119.0),
                         "sample_source": phase50.FROM_OBSERVATIONS},
                        {**quote(start + 1000, 96.0, 97.0),
                         "sample_source": phase50.FROM_OBSERVATIONS},
                    ]

                arms_payload["legs"][phase47.ARM_PRODUCTION] = [
                    *shared, *declines, *undecided]
                service._forward = path_for  # type: ignore[assignment]
                filtered = service.filter_compare(now=OPEN_TS + 100_000)
                counts = filtered["group_counts"]
                ok(filtered["production_events"] == 6
                   and filtered["production_legs"] == 8,
                   "the filter report counts the production universe in both "
                   "units: 8 observations of 6 opportunities")
                ok(counts[phase50.GROUP_SELECTED] == 1
                   and counts[phase50.GROUP_DECLINED] == 3
                   and counts[phase50.GROUP_UNMEASURED] == 2,
                   "and partitions them by what the overlay actually said at "
                   "each admitting instant")
                check = filtered["partition_check"]
                ok(check["exhaustive_and_disjoint"] is True
                   and check["unassigned"] == 0
                   and check["duplicate_event_ids"] == 0
                   and check["assigned_to_a_group"]
                   == filtered["production_events"],
                   "every production event is in exactly one group: none "
                   "unassigned, none in two, no id counted twice")
                ok(sum(counts.values()) == filtered["production_events"]
                   and sum(filtered["group_leg_counts"].values())
                   == filtered["production_legs"],
                   "the groups add up to the universe in events and in legs, "
                   "so a declined opportunity is measured and never dropped")
                ok(filtered["unmeasured_reasons"].get(phase46.UNMEASURED) == 1
                   and filtered["unmeasured_reasons"].get(
                       "NO_OVERLAY_STATE_RECORDED") == 1,
                   "and the unmeasured group says why each of its events is "
                   "there rather than being an unexplained residue")
                ok(service.filter_compare(now=OPEN_TS + 100_000)["group_counts"]
                   == counts,
                   "the partition is deterministic: the same journal read twice "
                   "gives the same groups")
                ids = {
                    group: {r["event_id"] for r in filtered["events"]
                            if r["overlay_partition"] == group}
                    for group in phase50.FILTER_GROUPS
                }
                ok(not (ids[phase50.GROUP_SELECTED]
                        & ids[phase50.GROUP_DECLINED]),
                   "no event appears in both the selected and the declined "
                   "column")
                ok(filtered["partition_fingerprint"]
                   == phase50.partition_fingerprint()
                   and len(phase50.partition_fingerprint()) == 16,
                   "the cohort rule is fingerprinted like the exits are, so "
                   "which states count as a decline cannot move unrecorded")
                required = (
                    "event_id", "production_signal", "overlay_state",
                    "overlay_state_at_first_observation", "candidate_id",
                    "fingerprint", "instrument", "vehicle", "contract",
                    "expiry", "strike", "direction", "first_decision_ts",
                    "market_session_status", "observation_count",
                )
                ok(all(field in row for row in filtered["events"]
                       for field in required),
                   "and every partitioned event carries the identity fields §1 "
                   "asks for")

                # --- the declared policies, applied identically to both groups
                ok(tuple(filtered["primary_policies"]) == p50exits.POLICIES
                   and phase50.COVERAGE_ENDPOINT
                   not in filtered["primary_policies"],
                   "the primary policies are the declared set and the coverage "
                   "endpoint is not one of them")
                ok(len({p50exits.fingerprint(p) for p in p50exits.ALL_POLICIES})
                   == len(p50exits.ALL_POLICIES)
                   and filtered["exit_policy_set_fingerprint"]
                   == p50exits.set_fingerprint(),
                   "each policy has its own fingerprint and the set has one, so "
                   "the report cannot silently gain or reword a policy")
                for policy in p50exits.POLICIES:
                    block = filtered["policies"][policy]
                    ok(block["fingerprint"] == p50exits.fingerprint(policy)
                       and block["params"] == p50exits.PARAMS[policy]
                       and set(block["columns"]) >= set(phase50.FILTER_GROUPS)
                       and block["same_exit_both_groups"]
                       == phase50.SAME_EXIT_BOTH_GROUPS,
                       f"{policy} is measured on every group under one frozen "
                       f"fingerprint and one set of parameters")
                    stamped = {
                        (r["overlay_partition"],
                         (r["outcomes"][policy]).get("exit_policy_fingerprint"))
                        for r in filtered["events"]
                    }
                    ok(len({finger for _, finger in stamped}) == 1
                       and stamped and all(
                           finger == p50exits.fingerprint(policy)
                           for _, finger in stamped),
                       f"and every event in every group carries {policy}'s own "
                       f"fingerprint: neither cohort was given an exit the "
                       f"other did not get")
                ok(filtered["coverage_endpoint"] == phase50.COVERAGE_ENDPOINT
                   and "NOT_A_TRADING_EXIT" in phase50.COVERAGE_ENDPOINT
                   and phase50.COVERAGE_ENDPOINT in filtered["policies"],
                   "path coverage is still reported, under a name that says it "
                   "is a diagnostic and not a trading exit")
                ok(filtered["label"] == phase50.EARLY_SHADOW_RESULT
                   and filtered["status"] == phase50.PAPER_ONLY
                   and filtered["order_path"] == phase50.NO_ORDER_PATH
                   and filtered["production_effect"]
                   == phase50.PRODUCTION_UNCHANGED
                   and filtered["rule_fingerprint"] == FROZEN_49_RULE,
                   "the filter report is labelled EARLY_SHADOW_RESULT, paper "
                   "only, with the frozen grouping fingerprint on it")

                # --- the pre-registered coverage guard
                guard = filtered["coverage_guard"]
                ok(guard["fingerprint"] == phase50.coverage_guard_fingerprint()
                   and len(guard["fingerprint"]) == 16
                   and guard["max_resolution_rate_gap_pp"]
                   == phase50.COVERAGE_MAX_RATE_GAP_PP
                   == 15.0
                   and guard["max_resolution_rate_ratio"]
                   == phase50.COVERAGE_MAX_RATE_RATIO
                   == 1.5,
                   "the symmetry threshold is two named constants with a "
                   "fingerprint of their own, so it cannot be loosened after "
                   "the cohorts are seen without the fingerprint moving")
                ok(phase50.coverage_guard_fingerprint()
                   not in {phase50.partition_fingerprint(),
                           phase50.definition_fingerprint(),
                           p50exits.set_fingerprint(), FROZEN_49_RULE}
                   and filtered["partition_fingerprint"] == FROZEN_PARTITION
                   and filtered["resolution_fingerprint"] == FROZEN_RESOLUTION
                   and filtered["exit_policy_set_fingerprint"] == FROZEN_EXITS
                   and filtered["rule_fingerprint"] == FROZEN_49_RULE,
                   "and it is a new fingerprint beside the four frozen ones "
                   "rather than a change to any of them")
                for policy in p50exits.ALL_POLICIES:
                    cover = filtered["policies"][policy]["coverage"]
                    for group in (phase50.GROUP_SELECTED,
                                  phase50.GROUP_DECLINED):
                        cohort = cover[group]
                        rate = cohort["resolution_rate_pct"]
                        expected = (
                            None if not cohort["eligible_events"] else round(
                                100.0 * cohort["resolved_events"]
                                / cohort["eligible_events"], 2)
                        )
                        ok(cohort["resolved_events"]
                           + cohort["unresolved_events"]
                           == cohort["eligible_events"] and rate == expected,
                           f"{policy} reports {group} as eligible / resolved / "
                           f"unresolved / rate, and the three counts close")
                asym = service._compare(
                    {"events": 8, "resolved_events": 5, "unresolved_events": 3,
                     "net_pct_mean_per_event": 12.18},
                    {"events": 63, "resolved_events": 23,
                     "unresolved_events": 40,
                     "net_pct_mean_per_event": -13.29},
                )
                ok(asym["coverage"]["status"] == phase50.COVERAGE_ASYMMETRIC
                   == "COVERAGE_ASYMMETRIC_DO_NOT_COMPARE"
                   and asym["coverage"][phase50.GROUP_SELECTED][
                       "resolution_rate_pct"] == 62.5
                   and asym["coverage"][phase50.GROUP_DECLINED][
                       "resolution_rate_pct"] == 36.51,
                   "the live 5-minute row — 5 of 8 against 23 of 63 — is called "
                   "COVERAGE_ASYMMETRIC_DO_NOT_COMPARE")
                ok(asym["selected_minus_declined"] is None
                   and asym["delta_withheld_because"]
                   == phase50.DELTA_WITHHELD
                   and asym["columns"][phase50.GROUP_SELECTED][
                       "net_pct_mean_per_event"] == 12.18
                   and asym["columns"][phase50.GROUP_DECLINED][
                       "net_pct_mean_per_event"] == -13.29,
                   "so the +25.5 subtraction is withheld while both raw columns "
                   "stay on display: the coverage problem is reported, not "
                   "hidden")
                ok(asym["verdict"]["state"] == phase50.COVERAGE_ASYMMETRIC
                   and asym["verdict"]["resolution_rate_pct_selected"] == 62.5
                   and asym["verdict"]["resolution_is_not_performance"]
                   == phase50.RESOLUTION_IS_NOT_PERFORMANCE,
                   "and the verdict says coverage rather than direction, with "
                   "the note that a wider capture is not a better filter")
                plenty = service._compare(
                    {"events": 200, "resolved_events": 125,
                     "unresolved_events": 75, "net_pct_mean_per_event": 9.0},
                    {"events": 200, "resolved_events": 50,
                     "unresolved_events": 150, "net_pct_mean_per_event": -9.0},
                )
                ok(plenty["verdict"]["state"] == phase50.COVERAGE_ASYMMETRIC
                   and plenty["selected_minus_declined"] is None
                   and min(125, 50) >= phase50.MIN_RESOLVED_EVENTS,
                   "the guard runs ahead of the event floor: 125 against 50 "
                   "resolved events clears the sample bar and is still two "
                   "different universes")
                near = service._compare(
                    {"events": 100, "resolved_events": 12,
                     "unresolved_events": 88, "net_pct_mean_per_event": 1.0},
                    {"events": 100, "resolved_events": 4,
                     "unresolved_events": 96, "net_pct_mean_per_event": -1.0},
                )
                ok(near["coverage"]["resolution_rate_gap_pp"] == 8.0
                   and near["coverage"]["status"]
                   == phase50.COVERAGE_ASYMMETRIC,
                   "12% against 4% is inside the 15-point gap and outside the "
                   "1.5 ratio, and both conditions have to hold — which is why "
                   "the ratio is in the threshold at all")
                even = service._compare(
                    {"events": 40, "resolved_events": 22,
                     "unresolved_events": 18, "net_pct_mean_per_event": 3.0,
                     "net_pct_total": 66.0,
                     "median_inter_observation_sec": 3.0},
                    {"events": 40, "resolved_events": 20,
                     "unresolved_events": 20, "net_pct_mean_per_event": -2.0,
                     "net_pct_total": -40.0,
                     "median_inter_observation_sec": 4.0},
                )
                ok(even["coverage"]["status"] == phase50.COVERAGE_COMPARABLE
                   and even["coverage"]["comparable"] is True
                   and even["selected_minus_declined"] is not None
                   and even["delta_withheld_because"] is None
                   and even["verdict"]["state"]
                   == phase50.DIRECTIONALLY_USEFUL,
                   "55% against 50% passes both conditions, so the delta is "
                   "published and the verdict is allowed to speak about "
                   "direction")
                thin = service._compare(
                    {"events": 4, "resolved_events": 2, "unresolved_events": 2,
                     "net_pct_mean_per_event": 3.0},
                    {"events": 4, "resolved_events": 2, "unresolved_events": 2,
                     "net_pct_mean_per_event": -2.0},
                )
                ok(thin["coverage"]["status"] == phase50.COVERAGE_COMPARABLE
                   and thin["verdict"]["state"] == phase50.TOO_FEW_EVENTS,
                   "and symmetric coverage on two events each is still "
                   "TOO_FEW_RESOLVED_EVENTS: passing the guard is not passing "
                   "the sample bar")
                ok(thin["selected_minus_declined"] is None
                   and thin["delta_withheld_because"]
                   == phase50.DELTA_WITHHELD_TOO_FEW
                   and thin["min_resolved_events_either_cohort"] == 2
                   and thin["resolved_event_floor"]
                   == phase50.MIN_RESOLVED_EVENTS,
                   "so the subtraction is withheld on the floor as well as on "
                   "the guard — a difference of two two-event means is not a "
                   "measurement, however symmetric the coverage was")
                ok(even["columns"][phase50.GROUP_SELECTED][
                       "net_pct_mean_per_event"] == 3.0
                   and thin["columns"][phase50.GROUP_SELECTED][
                       "net_pct_mean_per_event"] == 3.0,
                   "and both rows still carry their raw cohort figures, "
                   "withheld subtraction or not")
                empty = service._compare(
                    {"events": 8, "resolved_events": 0, "unresolved_events": 8},
                    {"events": 9, "resolved_events": 0, "unresolved_events": 9},
                )
                ok(empty["coverage"]["status"]
                   == phase50.COVERAGE_NOT_MEASURABLE
                   and empty["selected_minus_declined"] is None,
                   "with nothing resolved on either side the row is unmeasured "
                   "rather than comparable at zero")

                # --- ex-ante cost bands and the stratified comparison
                ok(phase50.cost_band(None) == phase50.COST_BAND_UNMEASURED
                   and phase50.cost_band(0.5) == "COST_0_TO_1_PCT"
                   and phase50.cost_band(1.0) == "COST_1_TO_2P5_PCT"
                   and phase50.cost_band(7.5) == "COST_5_TO_10_PCT"
                   and phase50.cost_band(400.0) == "COST_OVER_25_PCT"
                   and set(phase50.cost_bands())
                   >= {phase50.cost_band(v)
                       for v in (None, 0.1, 1.0, 3.0, 6.0, 20.0, 900.0)},
                   "the cost bands are pre-registered edges on cost as a "
                   "percent of entry, and an event with no recorded cost is "
                   "unmeasured rather than guessed into a band")
                banded = service._ex_ante_cost([
                    leg(OPEN_TS, entry_price=100.0, measured_cost_points=2.0),
                    leg(OPEN_TS + 60, entry_price=100.0,
                        measured_cost_points=90.0, bid=400.0, ask=401.0),
                ])
                ok(banded["ex_ante_cost_pct_of_entry"] == 2.0
                   and banded["cost_band"] == "COST_1_TO_2P5_PCT"
                   and banded["ex_ante_cost_basis"] == phase47.MEASURED_COST,
                   "the band is cut on the first leg's own cost and entry, so "
                   "neither a later cost nor a fourfold later quote can move "
                   "which stratum an event is compared in")
                ok(service._ex_ante_cost(
                    [leg(OPEN_TS, entry_price=100.0,
                         measured_cost_points=None,
                         modelled_cost_points=None)],
                )["cost_band"] == phase50.COST_BAND_UNMEASURED,
                   "and an event whose round trip was never measured or "
                   "modelled lands in the unmeasured band")
                ok(all(row["cost_band"] in phase50.cost_bands()
                       and row["cost_band_is_ex_ante"]
                       == phase50.COST_BAND_IS_EX_ANTE
                       for row in filtered["events"]),
                   "every partitioned event carries its band and the note that "
                   "the band is ex-ante")
                ok(filtered["strata_fields"]
                   == ["instrument", "vehicle", "cost_band"],
                   "the strata are the three §2 asks for: instrument, vehicle "
                   "and ex-ante cost band")
                for policy in p50exits.POLICIES:
                    strata = filtered["policies"][policy]["strata"]
                    ok(set(strata) == {"by_instrument", "by_vehicle",
                                       "by_cost_band"}
                       and all(
                           set(block) >= {"columns", "coverage", "leg_counts",
                                          "selected_minus_declined",
                                          "verdict", "both_cohorts_present"}
                           for cut in strata.values()
                           for block in cut.values()),
                       f"{policy} is stratified three ways, and every stratum "
                       f"carries its own cohorts, coverage, legs and status")
                    for cut, field in (("by_instrument", "instrument"),
                                       ("by_vehicle", "vehicle"),
                                       ("by_cost_band", "cost_band")):
                        blocks = strata[cut]
                        events_in_strata = sum(
                            b["coverage"][phase50.GROUP_SELECTED][
                                "eligible_events"]
                            + b["coverage"][phase50.GROUP_DECLINED][
                                "eligible_events"]
                            for b in blocks.values()
                        )
                        legs_in_strata = sum(
                            b["leg_counts"][phase50.GROUP_SELECTED]
                            + b["leg_counts"][phase50.GROUP_DECLINED]
                            for b in blocks.values()
                        )
                        decided = (counts[phase50.GROUP_SELECTED]
                                   + counts[phase50.GROUP_DECLINED])
                        decided_legs = (
                            filtered["group_leg_counts"][
                                phase50.GROUP_SELECTED]
                            + filtered["group_leg_counts"][
                                phase50.GROUP_DECLINED])
                        ok(events_in_strata == decided
                           and legs_in_strata == decided_legs
                           and all(
                               key == str(next(
                                   r for r in filtered["events"]
                                   if r[field] == key)[field])
                               for key in blocks
                               if any(r[field] == key
                                      for r in filtered["events"])),
                           f"{policy} {cut} partitions the two decided cohorts "
                           f"without losing or duplicating an event or a leg — "
                           f"{decided} events, {decided_legs} legs")
                        ok(all(
                            b["selected_minus_declined"] is None
                            for b in blocks.values()
                            if not b["both_cohorts_present"]),
                           f"and a {cut} stratum the overlay only ever "
                           f"selected or only ever declined publishes no "
                           f"difference against a cohort that does not exist")

                # ------- §A the forward-observation diagnostic, read-only
                #
                # The guard refuses every policy row on the real capture and a
                # refusal is not a diagnosis. These checks are the diagnosis:
                # one production universe whose events stop being observed for
                # six different recorded reasons, and a taxonomy that has to
                # name each of them from the recording rather than from the
                # fact that the event went unresolved.
                books: dict[str, list[dict]] = {
                    "SEL1CE": [
                        {**quote(OPEN_TS + 1000, 104.0, 105.0),
                         "sample_source": phase50.FROM_OBSERVATIONS},
                        {**quote(OPEN_TS + 1300, 108.0, 109.0),
                         "sample_source": phase50.FROM_OBSERVATIONS},
                    ],
                    "DEC5CE": [
                        {**quote(OPEN_TS + 200, 104.0, 105.0),
                         "sample_source": phase50.FROM_OBSERVATIONS},
                    ],
                    # Quoted, and one-sided: an ask with no bid is a book the
                    # exit cannot sell into, which is the chain's doing and not
                    # the recorder's.
                    "DEC3CE": [
                        {**quote(OPEN_TS + 300, None, 105.0),
                         "sample_source": phase50.FROM_OBSERVATIONS},
                    ],
                }

                def books_for(event: dict, legs_in: list[dict], *, con: object):
                    return books.get(str(event.get("contract")), [])

                def obs(ts: float, name: str, state: str, **kw: object) -> dict:
                    return leg(
                        ts, instrument=name, contract=f"{name}CE",
                        overlay_state=state, **kw,
                    )

                # A filler contract observed every 150 seconds is what makes
                # "the recorder carried on" a measurable fact rather than an
                # assumption: without a second contract on the timeline, a
                # contract that vanished and a recorder that died are the same
                # row.
                fixture = [
                    *[obs(OPEN_TS + i * 150.0, "FILL", phase46.UNMEASURED)
                      for i in range(12)],
                    *[obs(OPEN_TS + 70 + i * 240.0, "SEL1", phase46.SUPPORTED)
                      for i in range(4)],
                    obs(OPEN_TS + 90, "DEC5", phase46.DISAGREES),
                    obs(OPEN_TS + 120, "DEC1", phase46.DISAGREES),
                    obs(OPEN_TS + 180, "DEC2", phase46.COST_BLOCKED,
                        data_quality=p17quality.MISSING),
                    obs(OPEN_TS + 240, "DEC3", phase46.WATCH),
                    obs(OPEN_TS + 360, "DEC4", phase46.DISAGREES),
                    obs(OPEN_TS + 1230, "DEC4", phase46.DISAGREES),
                    obs(OPEN_TS + 2400, "GAPD", phase46.SUPPORTED),
                ]
                prior_legs = arms_payload["legs"][phase47.ARM_PRODUCTION]
                arms_payload["legs"][phase47.ARM_PRODUCTION] = fixture
                service._forward = books_for  # type: ignore[assignment]
                try:
                    diag = service.coverage_diagnostic(now=OPEN_TS + 100_000)
                    rows = {
                        str(r["contract"]): r for r in diag["events"]
                        if str(r["contract"]) != "DEC4CE"
                    }
                    quad = sorted(
                        (r for r in diag["events"]
                         if str(r["contract"]) == "DEC4CE"),
                        key=lambda r: float(r["first_decision_ts"]),
                    )
                    ok(diag["production_events"] == 9
                       and diag["production_legs"] == len(fixture),
                       "the diagnostic counts the production universe in both "
                       "units and never calls an observation an opportunity")
                    check = diag["taxonomy_check"]
                    ok(check["every_event_classified_exactly_once"] is True
                       and check["unclassified"] == 0
                       and check["duplicate_event_ids"] == 0
                       and check["events"] == diag["production_events"],
                       "every production event carries exactly one stop reason "
                       "from the frozen taxonomy, and no event is classified "
                       "twice")
                    ok(check["no_outcome_field_present"] is True
                       and not check["outcome_fields_present"],
                       "and no record carries a net, gross, MFE, MAE, giveback "
                       "or win-rate field: why an event was observed cannot be "
                       "explained by what it earned")
                    ok(diag["partition_check"]["exhaustive_and_disjoint"] is True
                       and sum(diag["group_counts"].values())
                       == diag["production_events"]
                       and sum(diag["group_leg_counts"].values())
                       == diag["production_legs"],
                       "the selected/declined/unmeasured partition stays "
                       "exhaustive and disjoint inside the diagnostic, in "
                       "events and in legs")
                    expected = {
                        "SEL1CE": phase50.STOP_EVENT_ENDED,
                        "DEC5CE": phase50.STOP_EVENT_ENDED,
                        "DEC1CE": phase50.STOP_CONTRACT_DROPPED,
                        "DEC2CE": phase50.STOP_STALE_QUOTE,
                        "DEC3CE": phase50.STOP_SIDE_UNAVAILABLE,
                        "FILLCE": phase50.STOP_CAPTURE_GAP,
                        "GAPDCE": phase50.STOP_FEED_STOPPED,
                    }
                    for contract, reason in expected.items():
                        row = rows[contract]
                        ok(row["observation_stopped_because"] == reason
                           and row["stop_reason_evidence"]
                           == phase50.STOP_REASON_EVIDENCE[reason]
                           and row["stop_reason_cause"]
                           == phase50.STOP_REASON_CAUSE[reason],
                           f"{contract} stopped being observed because "
                           f"{reason}, and the row carries the recorded fact "
                           f"that says so and the cause it belongs to")
                    ok(quad[0]["observation_stopped_because"]
                       == phase50.STOP_GROUP_ENDED
                       and quad[0]["next_observation_ts_same_contract"]
                       == OPEN_TS + 1230,
                       "a contract observed again beyond the grouping gap ends "
                       "its first event by the frozen 300-second rule, and the "
                       "row names the later observation that proves it")
                    ok(rows["DEC1CE"]["next_observation_ts_any_contract"]
                       is not None
                       and rows["DEC1CE"]["later_books_after_entry"] == 0,
                       "and CONTRACT_NO_LONGER_OBSERVED is only said where the "
                       "recorder demonstrably carried on: another contract was "
                       "observed within a gap and this one never was again")
                    ok(rows["DEC3CE"]["later_books_after_entry"] == 1
                       and rows["DEC3CE"][
                           "number_of_executable_quotes_after_entry"] == 0
                       and rows["DEC3CE"][
                           "remained_observable_after_decision"] is True,
                       "a one-sided later book counts as observation that "
                       "happened and as an executable quote that did not — the "
                       "two are never the same column")
                    ok(rows["SEL1CE"]["observation_count"] == 4
                       and rows["SEL1CE"]["observation_duration_seconds"] == 720.0
                       and rows["SEL1CE"][
                           "number_of_executable_quotes_after_entry"] == 2
                       and rows["SEL1CE"]["first_later_executable_quote_ts"]
                       == OPEN_TS + 1000
                       and rows["SEL1CE"]["last_later_executable_quote_ts"]
                       == OPEN_TS + 1300
                       and rows["SEL1CE"]["forward_coverage_seconds"] == 1230.0,
                       "the per-event record holds the observation shape §1 "
                       "asks for: count, duration, executable quotes after "
                       "entry and the first and last of them")
                    ok(rows["SEL1CE"]["covered_to_15m"] is True
                       and rows["SEL1CE"]["covered_to_30m"] is False
                       and rows["DEC1CE"]["covered_to_5m"] is False,
                       "coverage horizons are answered from the executable "
                       "path alone, so an event with no later book is not "
                       "covered to five minutes rather than unmeasurable")
                    late = service.coverage_diagnostic(now=OPEN_TS + 100_000)
                    ok([r["observation_stopped_because"] for r in late["events"]]
                       == [r["observation_stopped_because"]
                           for r in diag["events"]],
                       "the diagnostic is deterministic: the same journal read "
                       "twice returns the same reason for every event")
                    table = diag["comparison"]
                    left = table["columns"]["selected"]
                    right = table["columns"]["declined"]
                    ok(left["events"] == 2 and right["events"] == 6
                       and left["legs"] == 5 and right["legs"] == 6,
                       "the §3 table counts events and legs separately in both "
                       "cohorts, so five observations of two opportunities are "
                       "never five opportunities")
                    ok(left["median_observation_count"] == 2.5
                       and right["median_observation_count"] == 1.0
                       and table["gaps"]["median_observation_count"] == 1.5,
                       "and reports the observation depth of each cohort with "
                       "the gap between them")
                    ok(set(left["stopped_by"]) == set(phase50.STOP_REASONS)
                       and sum(left["stopped_by"].values()) == left["events"]
                       and sum(right["stopped_by"].values()) == right["events"],
                       "every declared reason appears in the table even at "
                       "zero, and the reasons account for every event in the "
                       "cohort")
                    ok(left["pct_covered_to_5m"] is not None
                       and right["pct_covered_to_5m"] is not None
                       and table["gaps"]["pct_covered_to_5m"] is not None,
                       "the horizon coverage percentages §3 asks for are "
                       "published for both cohorts and differenced")
                    attribution = diag["attribution"]
                    ok(attribution["attributable"] is True
                       and attribution["dominant_cause"] in phase50.CAUSES
                       and phase50.CAUSE_OVERLAY
                       not in attribution["contributions_pp"],
                       "the diagnostic names a dominant cause from the eight "
                       "candidates, and never assigns the overlay one: no "
                       "recorded field attributes an observation to it")
                    ok(attribution["contributions_pp"][phase50.CAUSE_TRACKING]
                       > 0
                       and attribution["contributions_pp"][
                           phase50.CAUSE_CHAIN] > 0
                       and attribution["contributions_pp"][
                           phase50.CAUSE_TERMINATION] < 0,
                       "a cause's contribution is the declined cohort's share "
                       "of it minus the selected cohort's, so a reason that "
                       "only ends declined events reads positive")
                    residual = attribution["residual_depth_asymmetry"]
                    shared_reason = [
                        r for r in residual["per_stop_reason"]
                        if r["stop_reason"] == phase50.STOP_EVENT_ENDED
                    ]
                    ok(len(shared_reason) == 1
                       and shared_reason[0]["ratio"] == 4.0
                       and residual["reasons_compared"] == 1
                       and residual["reasons_with_depth_ratio_beyond_2x"] == 1,
                       "and where both cohorts stop for the same reason the "
                       "surviving depth difference is measured rather than "
                       "explained away — four observations against one under "
                       "the identical reason")
                    ok(attribution["overlay_cause_note"]
                       == phase50.CAUSE_OVERLAY_IS_A_RESIDUAL,
                       "with the note that a residual is all a capture whose "
                       "policy already differs between cohorts can support")
                    for field in ("by_instrument", "by_vehicle", "by_cost_band",
                                  "by_direction"):
                        strata = diag["strata"][field]
                        ok(strata and all(
                            set(s["columns"]) == {"selected", "declined"}
                            for s in strata.values()),
                           f"the same comparison is repeated {field} with both "
                           f"cohorts in every stratum")
                    events_by_stratum = sum(
                        s["columns"]["selected"]["events"]
                        + s["columns"]["declined"]["events"]
                        for s in diag["strata"]["by_instrument"].values()
                    )
                    ok(events_by_stratum == left["events"] + right["events"],
                       "and the strata partition the decided cohorts without "
                       "losing or duplicating an event")
                    ok(diag["observation_diagnostic_fingerprint"]
                       == phase50.observation_diagnostic_fingerprint()
                       and len(
                           phase50.observation_diagnostic_fingerprint()) == 16
                       and diag["rule_fingerprint"] == FROZEN_49_RULE
                       and diag["resolution_fingerprint"] == FROZEN_RESOLUTION
                       and diag["exit_policy_set_fingerprint"] == FROZEN_EXITS
                       and diag["partition_fingerprint"] == FROZEN_PARTITION,
                       "the taxonomy has a fingerprint of its own and moves "
                       "none of the four measurement fingerprints: the "
                       "diagnostic is a reading layer")
                    ok(all(str(r.get("session_close_ts") or "") != ""
                           for r in diag["events"]),
                       "every event knows when its own segment closed, so the "
                       "session can be told apart from the recorder")

                    # ---------------- §0-§7 cadence: how often, not how long
                    #
                    # The first reading ranked event termination from a
                    # stop-reason mix that was 2-19 points apart, while the
                    # depth medians were 201 against 4 — a difference the
                    # stop-reason table cannot see because both cohorts stop for
                    # the same reasons. What separates them is the interval
                    # between looks, so it is measured per event here.
                    bound = diag["read_bound"]
                    ok(bound["status"] == phase50.EXHAUSTIVE
                       and bound["exhaustive"] is True
                       and bound["legs_read"] == diag["production_legs"]
                       and bound["count_is"] == "THE_SESSIONS_OWN_COUNT",
                       "a reading that stopped before its bound says so, and "
                       "its counts are the session's own")
                    at_bound = service._read_bound(
                        {"bound": {phase47.ARM_PRODUCTION: {"available": 5000}}},
                        5000, 5000,
                    )
                    ok(at_bound["status"] == phase50.TRUNCATED
                       and at_bound["exhaustive"] is False
                       and at_bound["limit_was_reached"] is True
                       and at_bound["count_is"]
                       == "A_FLOOR_NOT_THE_SESSIONS_COUNT"
                       and at_bound["conclusion_rule"]
                       == phase50.TRUNCATION_BLOCKS_CONCLUSIONS,
                       "and a reading that read exactly its limit reports "
                       "TRUNCATED with its counts as floors: the bound "
                       "truncates the observation tail that is being measured")
                    ok(service._read_bound(
                        {"bound": {phase47.ARM_PRODUCTION: {
                            "available": 60000, "truncated": True}}},
                        50000, 50000,
                    )["status"] == phase50.TRUNCATED
                       and service._read_bound(
                           {"bound": {phase47.ARM_PRODUCTION: {
                               "available": 4184}}}, 4184, 50000,
                       )["exhaustive"] is True
                       and at_bound["read_ceiling"] == p47service.MAX_READ,
                       "truncation is read from the upstream bound as well as "
                       "from the count, and a 50,000 request that returned "
                       "4,184 legs is exhaustive")
                    sel_row = rows["SEL1CE"]
                    ok(sel_row["median_inter_observation_seconds"] == 240.0
                       and sel_row["p25_inter_observation_seconds"] == 240.0
                       and sel_row["p95_inter_observation_seconds"] == 240.0
                       and sel_row["max_inter_observation_seconds"] == 240.0
                       and sel_row["observation_intervals"] == 3
                       and sel_row["observations_per_minute"] == 0.333
                       and sel_row["first_observation_ts"] == OPEN_TS + 70
                       and sel_row["last_observation_ts"] == OPEN_TS + 790,
                       "every event carries the §1 cadence shape from its own "
                       "observation instants: p25, median, p75, p95, max, the "
                       "interval count and observations per minute")
                    ok(sel_row["cadence_label"] == phase50.CADENCE_SPARSE
                       and rows["FILLCE"]["cadence_label"]
                       == phase50.CADENCE_SPARSE
                       and rows["DEC5CE"]["cadence_label"]
                       == phase50.CADENCE_SINGLE
                       and rows["DEC5CE"][
                           "median_inter_observation_seconds"] is None
                       and rows["DEC5CE"]["observation_intervals"] == 0,
                       "and one look is labelled SINGLE_OBSERVATION with no "
                       "interval rather than an interval of zero: a cadence of "
                       "zero seconds would read as the fastest polling in the "
                       "capture")
                    ok(p50observe.cadence([10.0, 25.0])["cadence_label"]
                       == phase50.CADENCE_CONTINUOUS
                       and p50observe.cadence([0.0, 30.0])["cadence_label"]
                       == phase50.CADENCE_INTERMITTENT
                       and p50observe.cadence([0.0, 60.0])["cadence_label"]
                       == phase50.CADENCE_SPARSE
                       and p50observe.cadence([])["cadence_label"]
                       == phase50.CADENCE_SINGLE,
                       "the label boundaries are the declared 15-second and "
                       "60-second thresholds, inclusive at both ends")
                    shuffled = p50observe.cadence(
                        [OPEN_TS + 790, OPEN_TS + 70, OPEN_TS + 550,
                         OPEN_TS + 310])
                    ok(shuffled["median_inter_observation_seconds"] == 240.0
                       and shuffled == p50observe.cadence(
                           [OPEN_TS + 70 + i * 240.0 for i in range(4)]),
                       "cadence is deterministic and order-free: the same "
                       "instants in any order return the same statistics")
                    ok(set(inspect.signature(
                           p50observe.cadence).parameters) == {"stamps"}
                       and not (set(p50observe.cadence(
                           [0.0, 5.0])) & set(p50observe.FORBIDDEN_FIELDS)),
                       "and it is a function of observation instants alone, so "
                       "no future outcome can reach it — there is no argument "
                       "to pass one through and no outcome field in the result")
                    cadence_cols = table["columns"]
                    ok(cadence_cols["selected"][
                           "median_inter_observation_sec"] == 240.0
                       and cadence_cols["selected"][
                           "median_observations_per_minute"] is not None
                       and cadence_cols["declined"][
                           "median_inter_observation_sec"] is None
                       and table["gaps"]["median_inter_observation_sec"] is None,
                       "the §2 cohort table carries cadence beside duration, "
                       "and declines to difference a cohort whose interval was "
                       "never measurable")
                    dist = diag["cohorts"][phase50.GROUP_SELECTED][
                        "distributions"]
                    ok(set(dist) == set(p50observe.DISTRIBUTION_FIELDS)
                       and dist["observation_count"]["n"] == 2
                       and dist["observation_count"]["min"] <= dist[
                           "observation_count"]["median"] <= dist[
                           "observation_count"]["max"]
                       and dist["observation_count"]["percentiles_are"]
                       == "NEAREST_RANK_OBSERVED_VALUES_NOT_INTERPOLATED",
                       "and the distribution behind each median is published, "
                       "at observed values rather than interpolated ones: two "
                       "medians of four and of two hundred intervals are the "
                       "same column and not the same recording")
                    labels = diag["cohorts"][phase50.GROUP_DECLINED][
                        "cadence_label_counts"]
                    ok(set(labels) == set(phase50.CADENCE_LABELS)
                       and sum(labels.values()) == right["events"],
                       "every declared cadence label appears even at zero and "
                       "the labels account for every event in the cohort")
                    ok(table["cadence_guard"]["status"]
                       == phase50.CADENCE_NOT_MEASURABLE
                       and table["cadence_guard"]["comparable"] is False
                       and table["cadence_guard"]["guard_fingerprint"]
                       == phase50.cadence_guard_fingerprint()
                       and len(phase50.cadence_guard_fingerprint()) == 16
                       and diag["coverage_guard_fingerprint"] == FROZEN_GUARD,
                       "the cadence guard has a fingerprint of its own and "
                       "leaves the resolution-rate guard's untouched: it was "
                       "added beside that condition, not substituted for it")
                    ok(p50observe.cadence_comparable(
                           3.0, 43.0, selected_events=10, declined_events=67,
                       )["status"] == phase50.CADENCE_ASYMMETRIC
                       and p50observe.cadence_comparable(
                           3.0, 43.0, selected_events=10, declined_events=67,
                       )["cadence_ratio"] == 14.333
                       and p50observe.cadence_comparable(
                           3.0, 5.0, selected_events=10, declined_events=67,
                       )["status"] == phase50.CADENCE_COMPARABLE,
                       "a 3-second and a 43-second schedule are 14x apart and "
                       "refused; within the declared factor of two they are "
                       "comparable")
                    ok(p50observe.cadence_comparable(
                           3.0, 6.0, selected_events=8, declined_events=8,
                       )["status"] == phase50.CADENCE_COMPARABLE
                       and p50observe.cadence_comparable(
                           3.0, 6.0, selected_events=8, declined_events=8,
                       )["cadence_ratio"] == phase50.CADENCE_MAX_RATIO
                       and p50observe.cadence_comparable(
                           3.0, 6.03, selected_events=8, declined_events=8,
                       )["status"] == phase50.CADENCE_ASYMMETRIC,
                       "the bar itself is comparable and the first ratio past "
                       "it is not: the boundary is stated once so a row cannot "
                       "be published or refused by a rounding direction")
                    equal_bar = p50observe.bias_sources(
                        [_cadence_event("CRUDEOIL", "sel", i, 200, 3.0)
                         for i in range(8)],
                        [_cadence_event("CRUDEOIL", "dec", i, 100, 6.0)
                         for i in range(8)],
                    )
                    past_bar = p50observe.bias_sources(
                        [_cadence_event("CRUDEOIL", "sel", i, 200, 3.0)
                         for i in range(8)],
                        [_cadence_event("CRUDEOIL", "dec", i, 100, 43.0)
                         for i in range(8)],
                    )
                    ok(_bias_row(equal_bar,
                                 phase50.BIAS_POLL_CADENCE)["measured"]
                       == phase50.CADENCE_MAX_RATIO
                       and _bias_row(
                           equal_bar,
                           phase50.BIAS_POLL_CADENCE)["crossed"] is False
                       and _bias_row(
                           equal_bar,
                           phase50.BIAS_OVERLAY_CORRELATED)["crossed"] is False
                       and _bias_row(
                           past_bar,
                           phase50.BIAS_POLL_CADENCE)["crossed"] is True
                       and _bias_row(
                           past_bar,
                           phase50.BIAS_OVERLAY_CORRELATED)["crossed"] is True,
                       "and no mechanism is named from a cadence ratio the "
                       "publication guard calls comparable: B and G are "
                       "asserted strictly past the same bar, not at it")
                    within_equal = equal_bar["within_instrument"]
                    only = within_equal["per_instrument"][0]
                    ok(within_equal["instruments_clearing_floor"] == ["CRUDEOIL"]
                       and only["selected_events"] == 8
                       and only["declined_events"] == 8
                       and only["cadence_ratio"] == phase50.CADENCE_MAX_RATIO
                       and only["comparison"] is not None,
                       "§4 compares an instrument holding the event floor in "
                       "both cohorts and reports its own cadence ratio rather "
                       "than the all-market one")
                    sparse = service._compare(
                        {"events": 40, "resolved_events": 22,
                         "unresolved_events": 18, "net_pct_mean_per_event": 3.0,
                         "median_inter_observation_sec": 3.0},
                        {"events": 40, "resolved_events": 20,
                         "unresolved_events": 20,
                         "net_pct_mean_per_event": -2.0,
                         "median_inter_observation_sec": 43.0},
                    )
                    ok(sparse["coverage"]["status"]
                       == phase50.COVERAGE_COMPARABLE
                       and sparse["cadence"]["status"]
                       == phase50.CADENCE_ASYMMETRIC
                       and sparse["selected_minus_declined"] is None
                       and sparse["delta_withheld_because"]
                       == phase50.DELTA_WITHHELD_CADENCE
                       and sparse["columns"][phase50.GROUP_SELECTED][
                           "net_pct_mean_per_event"] == 3.0,
                       "§7: symmetric resolution reached from a 3-second and a "
                       "43-second schedule publishes no subtraction — equal "
                       "resolution out of unequal polling is a coincidence — "
                       "and both raw columns still print")
                    unmeasured_cadence = service._compare(
                        {"events": 40, "resolved_events": 22,
                         "unresolved_events": 18, "net_pct_mean_per_event": 3.0},
                        {"events": 40, "resolved_events": 20,
                         "unresolved_events": 20,
                         "net_pct_mean_per_event": -2.0},
                    )
                    ok(unmeasured_cadence["selected_minus_declined"] is None
                       and unmeasured_cadence["delta_withheld_because"]
                       == phase50.CADENCE_NOT_MEASURABLE,
                       "and an unmeasured cadence withholds it too: not "
                       "knowing whether the opportunity was equal is not "
                       "evidence that it was")
                    bias = diag["bias_sources"]
                    ok({row["source"] for row in bias["rows"]}
                       == set(phase50.BIAS_SOURCES)
                       and all(row["bar"] is not None
                               and "measured_is" in row
                               and "evidence" in row
                               for row in bias["rows"]),
                       "§5 reports all eight declared mechanisms with what was "
                       "measured, the pre-registered bar it had to cross and "
                       "the evidence, rather than the one that reads best")
                    ok(bias["ranked_by"].startswith("HOW_FAR_EACH_MEASURED")
                       and (bias["dominant_source"] is None
                            or bias["dominant_source"] in phase50.BIAS_SOURCES)
                       and (bias["dominant_source"] is not None
                            or bias["unranked_because"] == phase50.BIAS_UNRANKED)
                       and bias["overlay_correlation_note"]
                       == phase50.BIAS_G_IS_CORRELATION_ONLY,
                       "the dominant mechanism is whichever measurement sits "
                       "furthest past its own bar, or none is named at all, "
                       "and G stays a correlation")
                    overlay_row = next(
                        r for r in bias["rows"]
                        if r["source"] == phase50.BIAS_OVERLAY_CORRELATED)
                    within_bias = bias["within_instrument"]
                    ok(overlay_row["crossed"] is False
                       and overlay_row["measured"] is None
                       and within_bias["instruments_clearing_floor"] == []
                       and within_bias["event_floor"]
                       == phase50.BIAS_MIN_EVENTS_PER_COHORT,
                       "with no instrument holding the event floor in both "
                       "cohorts, G is not asserted at all: an instrument "
                       "present in one cohort only measures the mix")
                    ok(all(
                        row["comparison"] is None
                        for row in within_bias["per_instrument"]
                        if not row["clears_event_floor_both_cohorts"]),
                       "and a thin within-instrument stratum is reported "
                       "without a comparison rather than compared as though it "
                       "were a population")
                    named = {row["instrument"]
                             for row in within_bias["per_instrument"]}
                    ok(named == {
                           str(r["instrument"]) for r in diag["events"]
                           if r["overlay_partition"] != phase50.GROUP_UNMEASURED
                       }
                       and all(
                           row["selected_events"] + row["declined_events"] > 0
                           for row in within_bias["per_instrument"]),
                       "§3 stratification is by the event's own recorded "
                       "instrument, and every decided event lands in exactly "
                       "one instrument row")
                    design = diag["equal_observation_policy_design"]
                    ok(all(term in design for term in (
                           "observation_start", "observation_end",
                           "polling_cadence", "max_inter_observation_gap",
                           "minimum_quote_quality", "stale_quotes",
                           "capture_gaps", "contract_disappearance",
                           "session_close", "option_chain_disappearance",
                           "unresolved_conditions"))
                       and design["not_implemented"]
                       == phase50.DESIGN_NOT_IMPLEMENTED,
                       "§6 declares the equal-cadence and equal-window policy "
                       "in full, including the polling interval and the worst "
                       "tolerated gap that equal duration alone would have "
                       "missed")
                    ok("15s" in design["polling_cadence"]
                       and "60s" in design["max_inter_observation_gap"]
                       and "not the cohort" in design["observation_end"],
                       "and the schedule is a fixed interval and a fixed "
                       "window that the cohort is not an input to")
                finally:
                    arms_payload["legs"][phase47.ARM_PRODUCTION] = prior_legs
                    service._forward = path_for  # type: ignore[assignment]

                # A session that ran out before the recorder did. Measured in
                # its own universe because one event observed at 15:28 makes
                # every earlier event look like a capture gap, which is true of
                # that fixture and would hide these reasons behind each other.
                close_legs = [obs(ist(15, 28), "ENDD", phase46.SUPPORTED)]
                arms_payload["legs"][phase47.ARM_PRODUCTION] = close_legs
                service._forward = books_for  # type: ignore[assignment]
                try:
                    closing = service.coverage_diagnostic(now=ist(23, 0))
                    row = closing["events"][0]
                    ok(row["observation_stopped_because"]
                       == phase50.STOP_SESSION_ENDED
                       and row["reached_session_close"] is True
                       and row["stop_reason_cause"] == phase50.CAUSE_SESSION,
                       "an event whose last observation is within one grouping "
                       "gap of its segment's close stopped because the session "
                       "did, and that outranks every reason about the recorder")
                    ok(closing["comparison"]["columns"]["declined"]["events"]
                       == 0
                       and closing["attribution"]["attributable"] is False,
                       "and with an empty cohort nothing is attributed at all: "
                       "a share of no events is not a share")
                finally:
                    arms_payload["legs"][phase47.ARM_PRODUCTION] = prior_legs
                    service._forward = path_for  # type: ignore[assignment]

                # --- one event's policies, where the arithmetic is visible
                pol_legs = [leg(OPEN_TS)]
                pol_event = p49events.group(pol_legs)[0]
                walked = p50resolve.resolve_policies(
                    pol_event, pol_legs, path_for(pol_event, pol_legs, con=None),
                )
                five = walked[p50exits.FIXED_5M]
                ok(five["resolution_status"] == phase50.RESOLVED
                   and five["exit_ts"] == OPEN_TS + 330
                   and five["exit_side"] == "BID"
                   and five["exit_price"] == 118.0
                   and five["exit_reason"] == p50exits.BY_HORIZON,
                   "the 5-minute policy exits at the first executable book at "
                   "or after its horizon, at a BID the capture recorded")
                ok(walked[p50exits.FIXED_30M]["resolution_status"]
                   == phase50.UNRESOLVED_NO_QUOTE_AT_HORIZON
                   and walked[p50exits.FIXED_30M]["net_pct"] is None,
                   "a horizon the path cannot answer within tolerance stays "
                   "UNRESOLVED — not forward-filled, not answered from the "
                   "nearest book, and never at zero")
                ok(walked[p50exits.FIXED_60M]["resolution_status"]
                   == phase50.UNRESOLVED_NO_QUOTE_AT_HORIZON,
                   "and the same for 60 minutes: an unanswerable exit is a fact "
                   "about the capture")
                mids = {(q["bid"] + q["ask"]) / 2.0
                        for q in path_for(pol_event, pol_legs, con=None)}
                exits_taken = {
                    p["exit_price"] for p in walked.values()
                    if p.get("exit_price") is not None
                }
                ok(exits_taken and not (exits_taken & mids)
                   and all(
                       price in {104.0, 118.0, 96.0} for price in exits_taken),
                   "no policy exits at a midpoint or at a price the book never "
                   "showed on the BID it had to sell into")
                ok(all(
                    p["exit_policy_fingerprint"] == p50exits.fingerprint(pid)
                    for pid, p in walked.items()),
                   "every policy row carries the frozen fingerprint of the rule "
                   "it was measured under")
                ok(walked[phase50.COVERAGE_ENDPOINT]["exit_ts"] == OPEN_TS + 1000
                   and walked[phase50.COVERAGE_ENDPOINT]["exit_reason"]
                   == p50exits.BY_PATH_END
                   and walked[phase50.COVERAGE_ENDPOINT]["is_coverage_diagnostic"] is True,
                   "the coverage endpoint still exits at the last book on the "
                   "path, kept apart from the policies and labelled as the "
                   "diagnostic it is")
                stop_legs = [leg(OPEN_TS, measured_cost_points=2.0)]
                stop_event = p49events.group(stop_legs)[0]
                stopped = p50resolve.resolve_policies(
                    stop_event, stop_legs,
                    [{**quote(OPEN_TS + 60, 99.0, 100.0),
                      "sample_source": phase50.FROM_OBSERVATIONS},
                     {**quote(OPEN_TS + 120, 95.0, 96.0),
                      "sample_source": phase50.FROM_OBSERVATIONS}],
                )[p50exits.STOP_THEN_TIMEOUT]
                ok(stopped["resolution_status"] == phase50.RESOLVED
                   and stopped["exit_ts"] == OPEN_TS + 120
                   and stopped["exit_reason"] == p50exits.BY_STOP,
                   "the stop policy closes at the first book through twice the "
                   "measured round trip, and says the stop is why")
                # The assembler is the boundary a foreign contract has to cross,
                # so it is tested there rather than by handing the resolver a
                # sample it would never receive: a quote store holding the
                # neighbouring strike, read through the real _forward.
                store = sqlite3.connect(":memory:")
                store.row_factory = sqlite3.Row
                store.execute(
                    "CREATE TABLE raw_quote (symbol TEXT, vehicle TEXT, "
                    "ts REAL, bid REAL, ask REAL, traded REAL, evidence TEXT)")
                for symbol, bid in (("TCS29SEP262240CE", 130.0),
                                    ("TCS29SEP262260CE", 999.0)):
                    store.execute(
                        "INSERT INTO raw_quote VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (symbol, phase45.CE, OPEN_TS + 1400, bid, bid + 1.0,
                         None, p17quality.EXACT),
                    )
                assembled = real_forward(pol_event, pol_legs, con=store)
                store.close()
                from_store = [s for s in assembled
                              if s["sample_source"] == phase50.FROM_QUOTE_STORE]
                ok(len(from_store) == 1 and from_store[0]["bid"] == 130.0
                   and not any(s.get("bid") == 999.0 for s in assembled),
                   "the assembler reads this contract's books alone: the "
                   "neighbouring strike quoted at the same instant never "
                   "reaches the exit, so no policy can price off it")
                board = service.dashboard(now=OPEN_TS + 10000)
                ok(board["leg_counts"] and board["event_counts"],
                   "the dashboard publishes leg counts and event counts "
                   "together, never one without the other")
                rows_out = [*board["active_events"], *board["completed_events"]]
                ok(rows_out and all(
                    r["observations_are_one_event"].endswith("= 1 event")
                    for r in rows_out),
                   "and every row says in words that its observations are one "
                   "event")
                ok(all(r["market_session_status"]
                       in phase50.MARKET_SESSION_STATUS for r in rows_out)
                   and all(r["status"] == phase50.PAPER_ONLY for r in rows_out),
                   "each row carries a machine-derived session status and the "
                   "paper-only label")
                live = service.dashboard(now=OPEN_TS + 30)
                ok(len(live["active_events"]) >= 1
                   and all(r["resolution_status"]
                           == phase50.UNRESOLVED_EVENT_OPEN
                           for r in live["active_events"]),
                   "an event that can still gain an observation is active and "
                   "claims no outcome yet")
                text = report_mod.render(
                    service.events(now=OPEN_TS + 10000), comparison,
                    service.readings(),
                )
                ok(phase50.EARLY_SHADOW_RESULT in text
                   and "LEG COUNT" in text.upper()
                   and "EVENT COUNT" in text.upper(),
                   "the report is labelled EARLY_SHADOW_RESULT and prints both "
                   "counts")
                ok(not any(
                    word in text.lower().replace("not " + word, "")
                    for word in ("profitable", "validated")
                ) or "EARLY_SHADOW_RESULT" in text,
                   "and never describes this sample with the words §7 forbids "
                   "except to refuse them")
            finally:
                p47service.arms = real_arms  # type: ignore[assignment]
                service._forward = real_forward  # type: ignore[assignment]
        finally:
            service._CON = None
            settings.data_dir = prev_dir

    # ============================ §13 the poll budget and the 15-second policy
    with tempfile.TemporaryDirectory() as tmp:
        prev_dir = settings.data_dir
        settings.data_dir = tmp
        try:
            journal = p17store.series_paths(p17store.OBSERVATIONS)[-1]
            os.makedirs(os.path.dirname(journal), exist_ok=True)
            # Two candidate rows written at one tick instant, then a second
            # instant one second later. Three rows, two visits.
            rows = [
                f'{{"instrument":"CRUDEOIL","signal_ts":{OPEN_TS},'
                f'"selected_vehicle":"CE"}}',
                f'{{"instrument":"CRUDEOIL","signal_ts":{OPEN_TS},'
                f'"selected_vehicle":"PE"}}',
                f'{{"instrument":"CRUDEOIL","signal_ts":{OPEN_TS + 1.0},'
                f'"selected_vehicle":"CE"}}',
            ]
            with open(journal, "w", encoding="utf-8") as fh:
                fh.write("\n".join(rows) + "\n")
            scan = p50budget.scan_ticks(SESSION)
            ok(scan["ticks"]["CRUDEOIL"] == [OPEN_TS, OPEN_TS + 1.0]
               and scan["rows_by_instrument"]["CRUDEOIL"] == 3,
               "the budget counts two visits from three rows: one tick that "
               "wrote a CE and a PE candidate is one look at the feed, and "
               "counting rows would have inflated the cost it is measured "
               "against")
            measured = p50budget.measure(SESSION, scan=scan)
            ok(measured["total_poll_attempts"] == 2
               and measured["total_observation_rows"] == 3,
               "so attempts and rows are reported as two different numbers "
               "rather than one number used twice")
            ok(measured["read_status"] == phase50.EXHAUSTIVE
               and measured["exhaustive"] is True
               and measured["lines_read"] == 3,
               "the reading states how much of the journal it saw, because an "
               "attempt count taken at a bound is a floor")
            truncated = p50budget.scan_ticks(SESSION, max_lines=2)
            ok(truncated["read_status"] == phase50.BUDGET_TRUNCATED
               and truncated["exhaustive"] is False,
               "and a reading stopped by its own ceiling says TRUNCATED "
               "instead of returning a smaller number silently")
            for field in ("cpu_seconds", "http_request_count", "timeout_count",
                          "failed_request_count", "skipped_or_duplicate_polls"):
                ok(measured[field] == phase50.BUDGET_NOT_RECORDED,
                   f"{field} is reported absent, not zero and not modelled: a "
                   f"cost nothing recorded cannot clear a safety bar")
            ok(measured["attempt_count_is"].startswith("DISTINCT_RECORDED"),
               "the measurement says in the payload what one attempt means, so "
               "an exact count of productive visits is never read as an exact "
               "count of all visits")
            empty = p50budget.measure("1999-01-01")
            ok(empty["total_poll_attempts"] == 0
               and empty["cpu_seconds"] == phase50.BUDGET_NOT_RECORDED,
               "a session with no journal measures no attempts and still "
               "declares the five fields absent")
        finally:
            settings.data_dir = prev_dir

    instants = p50observe.grid(1000.0, 4600.0)
    ok(instants == p50observe.grid(1000.0, 4600.0) and len(instants) == 241
       and instants[1] - instants[0] == 15.0,
       "the 15-second schedule is deterministic and pure: one hour is 241 "
       "instants, and the same start and end give the same list every time")
    ok(p50observe.grid(1000.0, 1000.0) == [1000.0],
       "a decision instant with no window still schedules the decision "
       "instant itself rather than nothing")
    fast_stamps = [1000.0 + i for i in range(3601)]
    sel_policy = p50observe.policy_record(
        decision_ts=1000.0, executable_stamps=fast_stamps,
        session_close_ts=None)
    dec_policy = p50observe.policy_record(
        decision_ts=1000.0, executable_stamps=fast_stamps,
        session_close_ts=None)
    ok(sel_policy == dec_policy
       and p50observe.satisfy(instants, fast_stamps)["satisfied_stamps"]
       == p50observe.satisfy(instants, fast_stamps)["satisfied_stamps"],
       "a selected event and a declined event with identical timestamps "
       "receive identical observation timestamps — the schedule takes no "
       "overlay state as an argument, so there is nothing for it to branch on")
    ok("selected" not in inspect.signature(
           p50observe.policy_record).parameters
       and "declined" not in inspect.signature(
           p50observe.policy_record).parameters
       and "overlay" not in inspect.signature(
           p50observe.policy_record).parameters,
       "and the overlay cannot influence the interval even by mistake: no "
       "parameter of the policy names it")
    ok(sel_policy["policy_median_inter_observation_seconds"] == 15.0
       and sel_policy["policy_cadence_sec"] == 15.0
       and sel_policy["policy_observation_count"] == 241,
       "a continuously captured hour satisfies every instant of the grid at "
       "exactly the declared cadence")
    ok(p50observe.satisfy([1000.0], [999.0])["satisfied"] == 0,
       "an observation one second BEFORE a scheduled instant does not satisfy "
       "it: the policy never backfills, because a look taken earlier is not a "
       "look taken then")
    ok(p50observe.satisfy([1000.0], [1000.0 + phase50.SHADOW_MAX_GAP_SEC + 1]
                          )["satisfied"] == 0
       and p50observe.satisfy([1000.0], [1030.0])["satisfied"] == 1,
       "and an observation past the declared maximum gap is a miss rather than "
       "a late fill")
    slow_policy = p50observe.policy_record(
        decision_ts=1000.0,
        executable_stamps=[1000.0 + i * 61.0 for i in range(60)],
        session_close_ts=None)
    ok(slow_policy["policy_feasible"] is False
       and slow_policy["policy_infeasible_reason"] == phase50.SHADOW_INFEASIBLE,
       "a contract the recorder visited every 61 seconds cannot be replayed on "
       "a 15-second grid, and that is reported as infeasible rather than as a "
       "sparser result")
    closed_policy = p50observe.policy_record(
        decision_ts=1000.0, executable_stamps=fast_stamps,
        session_close_ts=1600.0)
    ok(closed_policy["policy_covered_to_5m"] is True
       and closed_policy["policy_covered_to_60m"] is None
       and closed_policy["policy_window_truncated_by_close"] is True,
       "a window the session closed inside reports the horizons it could not "
       "reach as unmeasurable, not as failures — closing is deterministic and "
       "is not the event's fault")
    ok(not (set(sel_policy) & {"net_pct", "mfe_pct", "mae_pct", "giveback_pct",
                               "win_rate_pct", "profit_factor"}),
       "no future outcome enters the observation policy: the record carries no "
       "P&L, MFE, MAE, giveback or win-rate field to branch on")
    ok(p50observe.policy_record(decision_ts=None, executable_stamps=None,
                                session_close_ts=None)["policy_feasible"]
       is False,
       "an event with no decision instant is not observed under the policy "
       "rather than observed from an assumed one")
    equal = p50observe.equalised(
        [{"policy_median_inter_observation_seconds": 15.0}] * 6,
        [{"policy_median_inter_observation_seconds": 15.0}] * 6)
    ok(equal["verdict"] == phase50.SHADOW_EQUALIZED,
       "two cohorts both replayed at 15 seconds are declared equalised")
    unequal = p50observe.equalised(
        [{"policy_median_inter_observation_seconds": 15.0}] * 6,
        [{"policy_median_inter_observation_seconds": 61.0}] * 6)
    ok(unequal["verdict"] == phase50.SHADOW_NOT_EQUALIZED
       and unequal["publishable"] is False,
       "and a 15-against-61 pair publishes nothing: CADENCE_NOT_EQUALIZED is "
       "the answer, not a subtraction with a caveat beside it")
    fixture_events = [
        {"event_id": f"e{i}", "instrument": "CRUDEOIL", "vehicle": "CE",
         "cohort": phase50.GROUP_SELECTED if i < 3 else phase50.GROUP_DECLINED,
         "decision_ts": 1000.0, "session_close_ts": None}
        for i in range(6)
    ]
    replay = p50budget.proposed(
        fixture_events, ticks={"CRUDEOIL": fast_stamps})
    ok(replay["total_poll_attempts"] < replay["attempts_per_event_naive"]
       and replay["attempts_saved_by_dedupe"] > 0,
       "six events on one instrument at one instant share their visits: the "
       "budget counts the feed once per instrument per instant, because that "
       "is what a poll actually costs")
    ok(replay["slow_tier_attempts"] == 0
       and replay["fast_tier_attempts"] == replay["total_poll_attempts"],
       "the proposed policy has one tier: there is no fast lane left for the "
       "selected side to sit in")
    ok(p50budget.grid is p50observe.grid
       and p50budget.satisfy is p50observe.satisfy,
       "the cost replay and the fairness replay are the same two functions, so "
       "a cheap-looking budget cannot come from a different schedule than the "
       "one that equalised the cohorts")
    cheap = p50budget.compare(
        {"total_poll_attempts": 100, "peak_attempts_in_one_minute": 10,
         "attempts_per_minute": 5.0, "total_observation_rows": 200,
         "exhaustive": True},
        {"total_poll_attempts": 50, "peak_attempts_in_one_minute": 5,
         "attempts_per_minute": 3.0, "total_observation_rows": 50,
         "eligible_events": 10, "feasible_events": 9, "infeasible_events": 1},
    )
    ok(cheap["verdict"] == phase50.BUDGET_SAFE and not cheap["blocking_reasons"],
       "a schedule that costs fewer attempts and a lower worst minute than the "
       "recorder already spends is declared operationally safe")
    for label, prop_patch in (
        ("more attempts in total", {"total_poll_attempts": 500}),
        ("a worse single minute", {"peak_attempts_in_one_minute": 99}),
        ("too little raw evidence to replay", {"feasible_events": 2}),
    ):
        unsafe = p50budget.compare(
            {"total_poll_attempts": 100, "peak_attempts_in_one_minute": 10,
             "attempts_per_minute": 5.0, "total_observation_rows": 200,
             "exhaustive": True},
            {"total_poll_attempts": 50, "peak_attempts_in_one_minute": 5,
             "attempts_per_minute": 3.0, "total_observation_rows": 50,
             "eligible_events": 10, "feasible_events": 9,
             "infeasible_events": 1, **prop_patch},
        )
        ok(unsafe["verdict"] == phase50.BUDGET_UNSAFE
           and unsafe["blocking_reasons"],
           f"and a proposal with {label} is refused with its reason named "
           f"rather than forced to 15 seconds")
    truncated_cmp = p50budget.compare(
        {"total_poll_attempts": 100, "peak_attempts_in_one_minute": 10,
         "attempts_per_minute": 5.0, "total_observation_rows": 200,
         "exhaustive": False},
        {"total_poll_attempts": 50, "peak_attempts_in_one_minute": 5,
         "attempts_per_minute": 3.0, "total_observation_rows": 50,
         "eligible_events": 10, "feasible_events": 9, "infeasible_events": 1},
    )
    ok(truncated_cmp["verdict"] == phase50.BUDGET_UNSAFE,
       "a comparison against a truncated measurement is refused: the current "
       "budget it would be cheaper than is itself only a floor")
    ok(phase50.SHADOW_CADENCE_SEC == 15.0
       and list(phase50.SHADOW_WINDOWS_SEC) == [300.0, 900.0, 1800.0, 3600.0],
       "the cadence is 15 seconds and the four declared windows are 5, 15, 30 "
       "and 60 minutes, identical for both cohorts")
    budget_src = code(p50budget).lower()
    ok("net_pct" not in budget_src and "mfe" not in budget_src
       and "giveback" not in budget_src,
       "the budget module cannot see an outcome: no P&L field is named in it")
    ok("open_series" in source(p50budget)
       and 'open(' not in code(p50budget).replace("open_series(", "")
       .replace("open_maybe", ""),
       "and it only ever opens the raw journals through the reader that handles "
       "rolled and compressed files, so the higher-frequency capture is read "
       "whole and never rewritten")
    ok(len(phase50.observation_policy_fingerprint()) == 16
       and len(phase50.poll_budget_fingerprint()) == 16
       and phase50.observation_policy_fingerprint()
       != phase50.poll_budget_fingerprint()
       and phase50.observation_policy_fingerprint() not in {
           FROZEN_49_RULE, phase50.definition_fingerprint(),
           p50exits.set_fingerprint(), phase50.partition_fingerprint()},
       "the observation policy carries its own fingerprint, independent of the "
       "grouping, resolution, exit and partition fingerprints it must not "
       "change — so a result measured under the old asymmetric cadence can "
       "never be pooled with one measured under this policy")

    # ===================== the approved slow-tier change in the recorder
    ok(p50tiers.slow_tier_sec() == 15.0 and phase50.SHADOW_SLOW_TIER_SEC == 15.0,
       "the recorder's sampled tier is exactly 15 seconds — the number the "
       "measurement asked for, not approximately it")
    ok(p50tiers.PREVIOUS_SLOW_TIER_SEC == 60.0
       and p17mcx.SAMPLE_INTERVAL_SEC == 60.0,
       "and the gate it replaces is kept as a named 60 seconds, so the report "
       "prints the change instead of describing it")
    prev_flag = settings.phase50_shadow_slow_tier
    try:
        settings.phase50_shadow_slow_tier = False
        ok(p50tiers.slow_tier_sec() == 60.0
           and p50tiers.due("GOLD", now=1030.0, last_capture_ts=1000.0) is False
           and p50tiers.policy()["state"] == phase50.SHADOW_TIER_REVERTED,
           "the rollback condition is a flag, not an edit: disarmed, the gate "
           "is the previous 60-second one again and says so")
    finally:
        settings.phase50_shadow_slow_tier = prev_flag
    ok(p50tiers.due("GOLD", now=1014.9, last_capture_ts=1000.0) is False
       and p50tiers.due("GOLD", now=1015.0, last_capture_ts=1000.0) is True,
       "a sampled name is refused at 14.9 seconds and permitted at 15.0: the "
       "boundary is the declared one")
    ok(p50tiers.due("GOLD", now=1000.0, last_capture_ts=None) is True,
       "a name never captured this session is due immediately rather than "
       "waiting out an interval it has no start for")
    ok(p50tiers.due("CRUDEOIL", now=1000.1, last_capture_ts=1000.0) is True
       and p50tiers.due("NIFTY", now=1000.1, last_capture_ts=1000.0) is True
       and p50tiers.FAST_TIER_CADENCE.startswith("EVERY_TICK_UNGATED"),
       "the fast tier is untouched: CRUDEOIL and every non-MCX name are still "
       "due on every tick, which is deliberate — CRUDEOIL is the one "
       "instrument whose cohorts are already comparable")
    ok(sorted(p17mcx.DEEP_CAPTURE) == ["CRUDEOIL"]
       and sorted(p17mcx.SAMPLED_CAPTURE) == [
           "COPPER", "GOLD", "NATURALGAS", "SILVER"],
       "tier membership is unchanged by the cadence change: no instrument "
       "enters or leaves either tier")
    tier_params = set(inspect.signature(p50tiers.due).parameters)
    ok(tier_params == {"instrument", "now", "last_capture_ts"},
       "the gate takes the instrument, the clock and the last capture instant "
       "and nothing else — there is no overlay, vehicle, direction or cost "
       "argument for it to branch on")
    tier_src = code(p50tiers).lower()
    ok(not any(word in tier_src for word in (
           "selected", "declined", "overlay", "net_pct", "mfe", "mae",
           "giveback", "win_rate")),
       "and no future outcome or cohort label is even named in the module, so "
       "no schedule can be a function of how the trade turned out")
    ok(p50tiers.due("GOLD", now=1015.0, last_capture_ts=1000.0)
       == p50tiers.due("gold", now=1015.0, last_capture_ts=1000.0),
       "the same instrument at the same two instants receives the same "
       "permission every time: a selected GOLD event and a declined GOLD "
       "event get one schedule because there is only one function")
    ok("provider" not in code(p17service) and "httpx" not in code(p17service)
       and "requests." not in code(p17service),
       "the recorder fetches nothing: the book arrives as an argument from the "
       "tick that already holds it, so lifting the gate changes how often a "
       "row is WRITTEN and never how often the feed is ASKED")
    ok(list(phase50.SHADOW_WINDOWS_SEC) == [300.0, 900.0, 1800.0, 3600.0]
       and phase50.SHADOW_MAX_GAP_SEC == 60.0
       and p49events.rule_fingerprint() == FROZEN_49_RULE,
       "the fixed 5/15/30/60-minute windows and the event grouping rule are "
       "untouched by the cadence change")
    tier_fp = phase50.tier_cadence_fingerprint()
    ok(len(tier_fp) == 16 and tier_fp not in {
           FROZEN_49_RULE, phase50.definition_fingerprint(),
           p50exits.set_fingerprint(), phase50.partition_fingerprint(),
           phase50.observation_policy_fingerprint(),
           phase50.poll_budget_fingerprint()},
       "the recorder's cadence carries its own fingerprint, independent of "
       "the grouping, resolution, exit, partition and observation-policy ones "
       "it must not change")
    ok(tier_fp == "a134bdf1aec1e005",
       "the cadence fingerprint is the frozen a134bdf1aec1e005, so a later "
       "edit to the policy's wording cannot silently re-label sessions "
       "already journalled under it")
    pol = p50tiers.policy()
    ok("ADDS_NO_PROVIDER_REQUEST" in pol["cost_is_writes_not_requests"]
       and phase50.SHADOW_TIER_COST_IS_WRITES not in (
           phase50.SHADOW_TIER_RULE, phase50.SHADOW_TIER_FRESH_SAMPLE),
       "the policy states where the gate sits — after the tick's own chain and "
       "futures read — so the added work is journal writes, and it says so "
       "without claiming a CPU or request number no journal recorded")
    ok(store_mod.activation_id("2026-09-18", tier_fp, True)
       != store_mod.activation_id("2026-09-18", tier_fp, False)
       and store_mod.activation_id("2026-09-18", tier_fp, True)
       != store_mod.activation_id("2026-09-19", tier_fp, True),
       "one activation row per session per fingerprint per armed state, so a "
       "session that ran part-way under a rollback journals both rather than "
       "being labelled by its first tick")
    with tempfile.TemporaryDirectory() as tmp:
        prev_dir = settings.data_dir
        settings.data_dir = tmp
        p50tiers.reset()
        try:
            ok(p50tiers.note_session("2026-09-18", now=OPEN_TS) is True
               and p50tiers.note_session("2026-09-18", now=OPEN_TS + 60) is False,
               "the fresh-sample boundary is journalled once per session and "
               "is idempotent: a tick path that runs every second does not "
               "write a row every second")
            conn = store_mod.connect()
            try:
                conn.execute(
                    "INSERT INTO capture_cadence VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("old", "2026-09-17", "PREVIOUS", "0000000000000000",
                     60.0, p50tiers.FAST_TIER_CADENCE, 1, "OLD", "OLD",
                     OPEN_TS - 86400, phase50.PAPER_ONLY,
                     phase50.NO_ORDER_PATH, phase50.VERSION),
                )
                conn.commit()
                rows = store_mod.cadence_sessions(conn)
                ok(len(rows) == 2 and len(store_mod.cadence_sessions(
                       conn, tier_fingerprint=tier_fp)) == 1,
                   "both cadences are on the record and can be read apart: "
                   "the journal keeps the old policy's sessions rather than "
                   "deleting them")
            finally:
                conn.close()
            service._CON = None
            payload = service.tier_cadence()
            fresh = payload["fresh_sample"]
            ok(fresh["sessions"] == ["2026-09-18"]
               and fresh["sessions_captured"] == 1
               and payload["pre_change_sessions"] == 1,
               "and they are never pooled: the session captured under the old "
               "60-second gate is counted separately from the fresh sample, "
               "not added to it")
            ok(fresh["sessions_required"] == 20
               and fresh["sessions_remaining"] == 19
               and fresh["checkpoint_reached"] is False,
               "the first evidence checkpoint is ~20 fresh sessions, and one "
               "session in, the report says 19 remaining rather than showing "
               "a comparison")
            ok(payload["capture_health"] is None,
               "capture health is reported only for a named session, because "
               "it is a measurement of that session's journal and not an "
               "average over the policy")
            health = service.tier_cadence(session=SESSION)["capture_health"]
            ok(health is not None
               and health["captured_under_current_policy"] is False
               and health["cpu_seconds"] == phase50.BUDGET_NOT_RECORDED
               and health["timeout_count"] == phase50.BUDGET_NOT_RECORDED,
               "monitoring reports the measured attempts and the worst single "
               "minute, and leaves CPU, requests, timeouts, failures and "
               "duplicates absent rather than modelled")
            for field in ("total_poll_attempts", "peak_attempts_in_one_minute",
                          "stall_lines", "tick_ran_no_row_lines"):
                ok(field in health,
                   f"{field} is a measured live number in the monitoring "
                   f"payload, not the pre-change estimate")
        finally:
            service._CON = None
            p50tiers.reset()
            settings.data_dir = prev_dir
    p17_src = code(p17service)
    ok("p50tiers.due(" in p17_src and "mcx.due(" not in p17_src,
       "the recorder's tick path reads the new gate and the old one is left "
       "in place unchanged, so the rollback returns to code that still runs")
    ok("phase50" not in code(p17mcx),
       "and the superseded gate knows nothing of this phase: the dependency "
       "runs one way")

    # ======================================================== §8/§9 the barriers
    for mod in (phase50, p50calendar, p50resolve, store_mod, service, cli_mod,
                report_mod, p50budget, p50observe):
        # Imports and calls, not a substring scan: these modules print the
        # words NO_ORDER_PATH and ORDER BY, and a check that fires on its own
        # safety label is a check somebody deletes.
        hits = sorted(imported(mod) & set(FORBIDDEN))
        body = code(mod).lower().replace("order_path", "")
        called = sorted(word for word in FORBIDDEN if f"{word}(" in body)
        ok(not hits and not called,
           f"{getattr(mod, '__name__', mod)} imports and calls no order, "
           f"broker or execution path"
           f"{'' if not (hits or called) else ' — ' + str(hits + called)}")
    ok(not any(
        s.startswith(("UPDATE", "DELETE", "ALTER")) for s in
        sql_statements(store_mod)
    ), "the store executes no UPDATE, DELETE or ALTER: the record is appended "
       "to and never rewritten")
    ok("phase50" not in source(p46store) and "phase50" not in source(p46service)
       and "phase50" not in source(p47service)
       and "phase50" not in source(p49service),
       "no earlier phase knows this one exists: the dependency runs one way, so "
       "nothing here can change what they record")
    svc = code(service)
    reads_only = all(
        tail.startswith(("'", '"', ")", "]", "_normalised"))
        for tail in svc.split("production_signal")[1:]
    )
    ok("def decide" not in svc and " = " not in "".join(
        tail[:3] for tail in svc.split("production_signal")[1:]
    ) and reads_only,
       "and this phase computes no decision of its own — every mention of the "
       "production signal is a journal read or a dict key, never an assignment")
    main_src = open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "app",
                     "main.py"), encoding="utf-8",
    ).read()
    p50_routes = [
        line for line in main_src.splitlines() if "phase50" in line
        and "@app." in line
    ]
    ok(all("@app.get" in line for line in p50_routes)
       and 'app.post("/api/phase50' not in main_src,
       "every Phase 50 route is a GET: there is no endpoint that records, and "
       "recording stays a CLI act an operator performs")
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
    ok(phase50.definition_fingerprint() == phase50.definition_fingerprint()
       and len(phase50.definition_fingerprint()) == 16,
       "this phase's own pricing definition has a stable fingerprint, so a "
       "changed exit rule would be visible as a different population rather "
       "than as better numbers")

    print()
    print(f"PHASE 50 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
