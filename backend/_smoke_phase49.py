"""Phase 49 smoke — counting once, and correcting without rewriting.

Two claims are being held to account here, and both are the kind that are easy
to make and easy to quietly break:

* **an event count is a smaller number that must mean more.** If grouping ever
  consulted a mark, an excursion or a profit-and-loss, the count of independent
  opportunities would depend on what the market did afterwards, and a sample
  size that moves with the outcome is worse than the leg count it replaced;
* **a correction must add to the record, not edit it.** The superseded tally
  keeps its wrong count, keeps its id, and is still readable. If this phase
  could UPDATE or DELETE a journalled row, the journal would stop being
  evidence of what the board actually showed.

    .venv/bin/python _smoke_phase49.py
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import tempfile
import time

from app.config import settings
from app.research import phase45, phase46, phase47, phase49
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
from app.research.phase47 import store as p47store
from app.research.phase49 import cli as cli_mod
from app.research.phase49 import events
from app.research.phase49 import service
from app.research.phase49 import store as store_mod

PASS = 0
FAIL: list[str] = []

FROZEN_41 = "34fc64a299d29760"
FROZEN_42 = "b67709af902c8bff"
FROZEN_43 = "e45c3e72ad37f864"
FROZEN_44 = "95d393256ed1f505"
FROZEN_45 = "a3b0b9b0b321ee47"

FORBIDDEN = (
    "order", "broker", "execution", "smartapi", "angel", "kite", "trade_api",
    "place_order", "autobot", "executor",
)

# Fields that only exist once the instant is over. Grouping may not read one:
# how many opportunities a session held cannot depend on what they earned.
FUTURE_FIELDS = (
    "mark_price", "mark_ts", "net_pct", "gross_pct", "mfe_pct", "mae_pct",
    "giveback_pct", "outcome", "exit_reason", "realised_pct", "t1_hit",
    "sl_hit", "state", "cost_points",
)


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def source(mod: object) -> str:
    return inspect.getsource(mod)  # type: ignore[arg-type]


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
    """One admitted observation, shaped as the board emits it."""
    base = {
        "session": "2026-09-17",
        "instrument": "SENSEX",
        "vehicle": phase45.PE,
        "contract": "SENSEX2691774500PE",
        "expiry": "2026-09-17",
        "strike": 74500,
        "direction": "LONG",
        "overlay_state": phase46.SUPPORTED,
        "decision_ts": ts,
        "overlay_id": f"o{ts}",
        "event_id": f"e{ts}",
        "obs_id": f"b{ts}",
    }
    base.update(kw)
    return base


def docstrings(tree: ast.AST) -> set[int]:
    """The id of every string constant that is only prose.

    A substring scan cannot tell an executed SQL statement from the word
    "update" in a docstring, and a check that fails on its own explanation is a
    check nobody will keep. Only executable constants are examined.
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


def main() -> int:  # noqa: C901 - one linear acceptance list, read top to bottom
    # ======================================================= the grouping rule
    nine = [leg(1000.0 + i * 8.0) for i in range(9)]
    grouped = events.group(nine)
    ok(len(grouped) == 1,
       "nine observations on one strike inside 64 seconds are one event, not "
       "nine opportunities")
    ok(grouped[0]["observation_count"] == 9,
       "and the event says how many observations it folded in, so the legs are "
       "not lost by being grouped")
    ok(grouped[0]["overlay_ids"] == [r["overlay_id"] for r in nine],
       "every folded leg id is carried on the event, so it can always be taken "
       "apart again for execution and path work")
    ok(grouped[0]["source_event_ids"] == [r["event_id"] for r in nine]
       and grouped[0]["obs_ids"] == [r["obs_id"] for r in nine],
       "the upstream ids are kept under their own name, so the event's own id "
       "never overwrites the id of a leg it folded in")
    ok(grouped[0]["event_id"] == grouped[0]["event_group_id"]
       and grouped[0]["event_id"].startswith("E")
       and grouped[0]["event_id"] not in set(grouped[0]["source_event_ids"]),
       "the event's own id is exposed as event_id — one value, distinct from "
       "any upstream event id")
    ok(events.group([leg(1000.0, research_candidate="C7")])[0]["candidate_id"]
       == "C7"
       and events.group([leg(1000.0)])[0]["candidate_id"] is None,
       "the candidate is carried as candidate_id from the journal's "
       "research_candidate, and is null when the journal held none rather "
       "than an id this phase invented")
    ok(grouped[0]["first_decision_ts"] == 1000.0
       and grouped[0]["last_decision_ts"] == 1064.0
       and grouped[0]["span_sec"] == 64.0,
       "the event spans from its first observation to its last")

    counts = events.counts(nine)
    ok(counts[events.LEG_COUNT] == 9 and counts[events.EVENT_COUNT] == 1,
       "both counts are reported from one call — a caller cannot take the "
       "event count without carrying the leg count with it")
    ok(counts["repeated_observation_legs"] == 8,
       "and the difference is named: eight of the nine legs are repeats of an "
       "opinion already counted")
    ok(counts["grouping_rule"] == phase49.GROUPING_RULE
       and counts["rule_fingerprint"] == phase49.rule_fingerprint(),
       "the rule and its fingerprint travel with the counts, so two reports "
       "taken under different rules cannot be pooled")

    # the eleven legs of 2026-09-17, exactly as the board holds them
    eleven = nine + [
        leg(2000.0, instrument="BANKNIFTY", contract="BANKNIFTY29SEP265650",
            strike=65650, overlay_id="b1", event_id="be1"),
        leg(2107.0, instrument="BANKNIFTY", contract="BANKNIFTY29SEP265650",
            strike=65650, overlay_id="b2", event_id="be2"),
    ]
    both = events.counts(eleven)
    ok(both[events.LEG_COUNT] == 11 and both[events.EVENT_COUNT] == 2,
       "the session's eleven admitted legs are two events — one SENSEX strike "
       "and one BANKNIFTY strike")

    # ------------------------------------------------- what separates an event
    ok(len(events.group([leg(1000.0), leg(1000.0 + phase49.EVENT_GAP_SEC + 1)]))
       == 2,
       "a gap longer than the rule's window starts a new event: the same strike "
       "twice in a day is two opportunities")
    ok(len(events.group([leg(1000.0), leg(1000.0 + phase49.EVENT_GAP_SEC - 1)]))
       == 1,
       "and a gap inside the window does not")
    ok(len(events.group([leg(1000.0), leg(1004.0, vehicle=phase45.CE)])) == 2,
       "CE and PE at the same instant are two events, never collapsed into one")
    ok(len(events.group([leg(1000.0), leg(1004.0, strike=74600,
                                          contract="SENSEX2691774600PE")])) == 2,
       "two strikes are two events even when the underlying read is the same")
    ok(len(events.group([leg(1000.0), leg(1004.0, direction="SHORT")])) == 2,
       "two directions on one contract are two events")
    ok(len(events.group([leg(1000.0), leg(1004.0, session="2026-09-18")])) == 2,
       "an event never spans two sessions")
    ok(len(events.group([leg(1000.0, arm=phase47.ARM_RESEARCH),
                         leg(1004.0, arm=phase47.ARM_PRODUCTION)])) == 2,
       "the two columns group separately, so one column's legs can never be "
       "folded into the other's event")
    ok(len(events.group([leg(1000.0), leg(1004.0, decision_ts=None)])) == 2,
       "an observation with no usable timestamp becomes its own event rather "
       "than being silently joined to one it may not belong to")

    # ------------------------------------------------------------ determinism
    first = events.group(eleven)
    second = events.group(list(reversed(eleven)))
    ok([e["event_group_id"] for e in first] == [e["event_group_id"] for e in second],
       "event ids do not depend on the order the rows arrive in")
    ok(events.group(eleven)[0]["event_group_id"]
       == events.group(eleven)[0]["event_group_id"],
       "and are identical on a second pass over the same rows")
    ok(events.event_id(("a", "b"), 1.0) != events.event_id(("a", "c"), 1.0)
       and events.event_id(("a", "b"), 1.0) != events.event_id(("a", "b"), 2.0),
       "a different key or a different first instant is a different event id")
    growing = events.group(nine[:3])[0]["event_group_id"]
    ok(growing == events.group(nine)[0]["event_group_id"],
       "an event's id is fixed by its first observation, so it does not change "
       "as later observations join it — two reports of one session reconcile "
       "row by row")

    # ------------------------------------------------- no future, no outcome
    marked = [dict(r, net_pct=99.0, mark_price=500.0, mfe_pct=12.0,
                   state=phase47.MARKED) for r in eleven]
    ok(events.counts(marked)[events.EVENT_COUNT]
       == events.counts(eleven)[events.EVENT_COUNT],
       "adding marks, excursions and profit to every row changes no count: "
       "grouping cannot be influenced by what the market did afterwards")
    src = source(events)
    ok(not any(f'"{field}"' in src or f"'{field}'" in src
               for field in FUTURE_FIELDS),
       "no outcome field is named anywhere in the grouping module")
    ok(all(field in phase49.EVENT_KEY or field in
           ("decision_ts", "overlay_id", "event_id", "obs_id", "overlay_state",
            "research_candidate")
           for field in _keys_read(events)),
       "the only fields read are decision-instant fields")
    ok("import sqlite3" not in src and "open(" not in src
       and "time." not in src,
       "the grouping module has no store, no file and no clock — the same rows "
       "give the same events on any machine at any later date")

    # ================================================ supersession, end to end
    prev_dir = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        service.reset()
        p47service.reset()
        try:
            definition = p47service.definition()
            old_id = p47store.snapshot_id(
                "2026-09-17", phase47.ARM_PRODUCTION, definition, None,
            )
            con = p47store.connect()
            p47store.insert_snapshots(con, [{
                "snapshot_id": old_id,
                "session": "2026-09-17",
                "arm": phase47.ARM_PRODUCTION,
                "definition": definition,
                "snapshot_ts": time.time() - 3600.0,
                "marked_through_ts": None,
                "calls": 8, "marked": 0, "unmarkable": 8, "stale_marks": 0,
                "status": phase47.PAPER_ONLY,
                "order_path": phase47.NO_ORDER_PATH,
                "version": phase47.VERSION,
            }])
            before = p47store.snapshots(con, session="2026-09-17")
            ok(len(before) == 1 and before[0]["calls"] == 8,
               "a tally recorded under the superseded selection is in the "
               "journal, reading 8")

            new_id = p47store.snapshot_id(
                "2026-09-17", phase47.ARM_PRODUCTION, definition, None,
                phase49.selection_fingerprint(),
            )
            ok(new_id != old_id,
               "a tally taken under the corrected selection gets its own id, "
               "so recording it is not swallowed as a duplicate of the wrong "
               "one — which is how the broken 8 survived its own fix")
            p47store.insert_snapshots(con, [{
                "snapshot_id": new_id,
                "session": "2026-09-17",
                "arm": phase47.ARM_PRODUCTION,
                "definition": definition,
                "snapshot_ts": time.time(),
                "marked_through_ts": None,
                "calls": 40, "marked": 0, "unmarkable": 40, "stale_marks": 0,
                "legs": 40, "events": 6,
                "grouping_rule": phase49.GROUPING_RULE,
                "status": phase47.PAPER_ONLY,
                "order_path": phase47.NO_ORDER_PATH,
                "version": phase47.VERSION,
            }])

            registry_con = store_mod.connect()
            store_mod.insert(registry_con, [{
                "supersession_id": store_mod.supersession_id(old_id, new_id),
                "superseded_snapshot_id": old_id,
                "superseding_snapshot_id": new_id,
                "session": "2026-09-17",
                "arm": phase47.ARM_PRODUCTION,
                "definition": definition,
                "superseded_calls": 8,
                "superseding_calls": 40,
                "reason": phase49.REASON_SELECTION,
                "superseded_selection": phase49.SELECTION_SUPERSEDED,
                "selection": phase49.SELECTION,
                "selection_fingerprint": phase49.selection_fingerprint(),
                "recorded_ts": time.time(),
                "version": phase49.VERSION,
            }])
            registry_con.close()
            service.reset()

            rows = service.annotate(p47store.snapshots(con, session="2026-09-17"))
            by_id = {r["snapshot_id"]: r for r in rows}
            ok(by_id[old_id]["calls"] == 8,
               "the superseded tally keeps its wrong count, exactly as it was "
               "recorded — the mistake stays on the record")
            ok(by_id[old_id]["record_status"] == phase49.SUPERSEDED,
               "and reads SUPERSEDED")
            ok(by_id[old_id]["superseded_by"] == new_id
               and by_id[old_id]["superseded_reason"] == phase49.REASON_SELECTION,
               "with the row that replaced it and the reason attached, so an "
               "old count can never be read without its correction")
            ok(by_id[new_id]["record_status"] == phase49.CURRENT
               and by_id[new_id]["calls"] == 40,
               "the corrected tally is CURRENT and reads 40")
            ok(sum(1 for r in rows
                   if r["record_status"] == phase49.CURRENT
                   and r["arm"] == phase47.ARM_PRODUCTION) == 1,
               "exactly one tally per arm is CURRENT: a session cannot have two "
               "live answers")
            ok(by_id[new_id]["legs"] == 40 and by_id[new_id]["events"] == 6,
               "and the recorded tally carries both counts, so a stored number "
               "cannot be read as a sample it never was")

            # nothing was edited to achieve any of that
            again = p47store.snapshots(con, session="2026-09-17")
            ok({r["snapshot_id"]: r["calls"] for r in again}
               == {old_id: 8, new_id: 40},
               "both rows are still in the journal with the counts they were "
               "written with: supersession added a row and edited none")
            con.close()

            p47service.reset()
            listed = p47service.sessions(limit=40)["sessions"]
            shown = {r["snapshot_id"]: r for r in listed}
            ok(old_id in shown and new_id in shown,
               "the board's own session listing shows both, so a corrected "
               "count is never displayed with the count it corrects quietly "
               "dropped by the newest-row-wins reduction")
            ok(shown[old_id]["record_status"] == phase49.SUPERSEDED
               and shown[old_id]["calls"] == 8
               and shown[new_id]["record_status"] == phase49.CURRENT,
               "OLD 8 SUPERSEDED and NEW 40 CURRENT, side by side, read off "
               "the journal rather than out of a report's prose")
            ok(service._under_current_selection(
                   {"session": "2026-09-17", "arm": phase47.ARM_PRODUCTION,
                    "definition": definition, "marked_through_ts": None},
                   new_id) is True
               and service._under_current_selection(
                   {"session": "2026-09-17", "arm": phase47.ARM_PRODUCTION,
                    "definition": definition, "marked_through_ts": None},
                   old_id) is False,
               "a tally already taken under the corrected selection is "
               "recognised as such, so a later recount does not supersede an "
               "earlier correct one with a reason about a bug")

            # the registry converges instead of stacking
            registry_con = store_mod.connect()
            repeat = store_mod.insert(registry_con, [{
                "supersession_id": store_mod.supersession_id(old_id, new_id),
                "superseded_snapshot_id": old_id,
                "superseding_snapshot_id": new_id,
                "session": "2026-09-17", "arm": phase47.ARM_PRODUCTION,
                "definition": definition, "superseded_calls": 8,
                "superseding_calls": 40, "reason": phase49.REASON_SELECTION,
                "superseded_selection": phase49.SELECTION_SUPERSEDED,
                "selection": phase49.SELECTION,
                "selection_fingerprint": phase49.selection_fingerprint(),
                "recorded_ts": time.time(), "version": phase49.VERSION,
            }])
            ok(repeat["written"] == 0 and repeat["duplicate"] == 1,
               "registering the same correction twice appends nothing")
            ok(len(store_mod.records(registry_con, session="2026-09-17")) == 1,
               "so the registry holds one correction, not a growing pile of "
               "identical ones")
            registry_con.close()

            # an untouched session is untouched
            service.reset()
            clean = service.annotate([{"snapshot_id": "unrelated", "calls": 3}])
            ok(clean[0]["record_status"] == phase49.CURRENT
               and clean[0]["superseded_by"] is None,
               "a tally nobody superseded reads CURRENT")
        finally:
            settings.data_dir = prev_dir
            service.reset()
            p47service.reset()

    # ================================================ a bounded count says so
    # The fault this section exists for: the corrected production recount came
    # back as exactly the recorder's own ceiling and was published as the
    # session's count. A number that stopped at its limit is a floor.
    ok(p47service._bound(900, 240, 240)["truncated"] is True,
       "a reading that returned exactly its bound, with more rows behind it, "
       "is truncated")
    ok(p47service._bound(900, 240, 240)["count_is"] == phase47.COUNT_IS_A_FLOOR
       and p47service._bound(900, 240, 240)["reported"] == "at least 240",
       "and is reported as a floor, in words, rather than as a total")
    ok(p47service._bound(40, 40, 240)["truncated"] is False
       and p47service._bound(40, 40, 240)["count_is"]
       == phase47.COUNT_IS_COMPLETE
       and p47service._bound(40, 40, 240)["reported"] == "40",
       "a reading that stopped because the arm ran out is complete, and is not "
       "hedged for no reason")
    ok(p47service._bound(240, 240, 240)["truncated"] is False,
       "nor is an arm that holds exactly as many legs as the bound allowed — "
       "the bound was reached, but nothing was left behind it, and hedging a "
       "count that is complete is its own dishonesty")
    ok(p47service.selection_key("fp", False, 240) == "fp"
       and p47service.selection_key("fp", True, 240) != "fp"
       and p47service.selection_key("fp", True, 240)
       != p47service.selection_key("fp", True, 60),
       "a truncated tally is a different fact from a complete one and from a "
       "differently-bounded one, so re-recording with a larger limit is not "
       "swallowed as a duplicate of the floor")
    ok(service._why({"count_is": phase47.COUNT_IS_A_FLOOR, "calls": 240},
                    {"calls": 900}) == phase49.REASON_BOUND,
       "superseding a floor is recorded as a bound that was reached, not as a "
       "wrong count")
    ok(service._why({"calls": 8}, {"calls": 40}) == phase49.REASON_SELECTION,
       "a count that moved is recorded as the selection fault it was")
    ok(service._why({"calls": 11}, {"calls": 11})
       == phase49.REASON_SELECTION_SAME_COUNT
       and "NOT_WRONG" in phase49.REASON_SELECTION_SAME_COUNT,
       "and a count that did not move is not stamped with a bug it never had — "
       "the 11 → 11 research tally was re-recorded, not corrected")
    ok(service._under_current_selection(
           {"count_is": phase47.COUNT_IS_A_FLOOR, "session": "2026-09-17",
            "arm": phase47.ARM_PRODUCTION, "definition": "d",
            "marked_through_ts": None, "selection_bound": 240}, "anything",
       ) is False,
       "a floor is never the current answer however recently it was taken, so "
       "a fuller recount can always replace it")
    ok("selection_bound" in p47store.COLUMNS
       and "legs_available" in p47store.COLUMNS
       and "count_is" in p47store.COLUMNS,
       "and the journal stores the bound, what the arm held and which of the "
       "two the count is — a stored floor stays readable as one forever after")

    # ==================================== a tally written before the bound was
    # The production 240 was written before the journal stored what bounded it,
    # so it can be read neither as a total nor as a floor. It must not sit on
    # the record as the session's count merely because a recount collides with
    # its id and is dropped as a duplicate.
    ok(p47service.selection_key("fp", False, 240, recount=True)
       != p47service.selection_key("fp", False, 240),
       "a recount of a bound-less tally gets its own selection identity, so it "
       "lands beside the row it contradicts instead of being swallowed by it")
    ok(service._why({"snapshot_id": "legacy", "calls": 240},
                    {"calls": 900}, {phase47.ARM_PRODUCTION: {"legacy"}},
                    phase47.ARM_PRODUCTION) == phase49.REASON_UNRECORDED_BOUND
       and "CANNOT_BE_TOLD_FROM_A_FLOOR"
       in phase49.REASON_UNRECORDED_BOUND,
       "and is registered for what is actually wrong with the old row — its "
       "bound was never recorded — rather than being called a floor or a "
       "wrong count, neither of which the row can support")

    prev_dir = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        service.reset()
        p47service.reset()
        try:
            admitted = []
            for i in range(3):
                admitted.append({
                    "overlay_id": f"legacy-{i}", "event_id": f"le-{i}",
                    "obs_id": f"lo-{i}", "decision_ts": 1_700_000_000.0 + i,
                    "session": "2026-09-17", "instrument": "SENSEX",
                    "vehicle": phase45.PE, "contract": "SENSEX2691774500PE",
                    "strike": 74500, "expiry": "2026-09-17",
                    "direction": "LONG",
                    "overlay_state": phase46.SUPPORTED,
                    "entry_side": "ASK", "entry_price": 224.0,
                    "bid": 223.0, "ask": 224.0,
                    "definition": p47service.definition(),
                    "classification": phase46.RESEARCH_OVERLAY,
                    "mode": phase46.SHADOW,
                    "production_effect": phase46.PRODUCTION_UNCHANGED,
                    "version": phase46.VERSION,
                })
            ocon = p46store.connect(p46store.db_path())
            p46store.insert_events(ocon, admitted)
            ocon.close()
            p46service.reset()
            p47service.reset()
            board_now = p47service.board(session="2026-09-17")
            counted = int(
                board_now["arms"][phase47.ARM_RESEARCH]["calls"] or 0)
            ok(counted == len(admitted),
               "the board counts the admitted legs the journal holds")

            definition = p47service.definition()
            legacy_id = p47store.snapshot_id(
                "2026-09-17", phase47.ARM_RESEARCH, definition, None,
                phase49.selection_fingerprint(),
            )
            jcon = p47store.connect()
            p47store.insert_snapshots(jcon, [{
                "snapshot_id": legacy_id,
                "session": "2026-09-17",
                "arm": phase47.ARM_RESEARCH,
                "definition": definition,
                "snapshot_ts": 1_700_000_000.0,
                "marked_through_ts": None,
                # one leg, no count_is: the shape of a row written before the
                # bound columns existed
                "calls": 1, "marked": 0, "unmarkable": 1, "stale_marks": 0,
                "status": phase47.PAPER_ONLY,
                "order_path": phase47.NO_ORDER_PATH,
                "version": phase47.VERSION,
            }])
            jcon.close()
            ok(service._under_current_selection(
                   {"session": "2026-09-17", "arm": phase47.ARM_RESEARCH,
                    "definition": definition, "marked_through_ts": None},
                   legacy_id) is True,
               "by its id alone that row looks like it was taken under the "
               "current selection, which is exactly why it would be skipped "
               "for ever and its disagreeing count left standing")

            done = service.supersede(session="2026-09-17")
            got = {a["arm"]: a for a in done["arms"]}
            fixed = got.get(phase47.ARM_RESEARCH, {})
            ok(fixed.get("old_calls") == 1
               and fixed.get("new_calls") == counted,
               "it is superseded anyway, because the board disagrees with it — "
               "the disagreement is the evidence, not a guess about what "
               "bounded it")
            ok(fixed.get("reason") == phase49.REASON_UNRECORDED_BOUND,
               "with the reason naming the missing bound")
            jcon = p47store.connect()
            after = {r["snapshot_id"]: r
                     for r in service.annotate(
                         p47store.snapshots(jcon, session="2026-09-17"))}
            jcon.close()
            ok(after[legacy_id]["calls"] == 1
               and after[legacy_id]["record_status"] == phase49.SUPERSEDED,
               "the old row keeps its count and reads SUPERSEDED: a count "
               "nobody can vouch for is still not edited out")
            live = [r for r in after.values()
                    if r["arm"] == phase47.ARM_RESEARCH
                    and r["record_status"] == phase49.CURRENT]
            ok(len(live) == 1 and int(live[0]["calls"]) == counted
               and str(live[0]["count_is"] or "") in (
                   phase47.COUNT_IS_COMPLETE, phase47.COUNT_IS_A_FLOOR),
               "exactly one CURRENT tally replaces it, and that one does carry "
               "which of the two its count is")
            ok(service.supersede(session="2026-09-17")["superseded"] == 0,
               "and running it again corrects nothing a second time")
        finally:
            settings.data_dir = prev_dir
            service.reset()
            p47service.reset()

    # ============================================== append-only, by inspection
    for mod in (store_mod, service, events, cli_mod):
        statements = sql_statements(mod)
        ok(not any(s.startswith(("UPDATE", "DELETE")) for s in statements),
           f"{mod.__name__} issues no UPDATE and no DELETE")
    ok(store_mod.SCHEMA.count("CREATE TABLE") == 1
       and "session_mark" not in source(store_mod),
       "the registry has its own table in its own file and does not hold the "
       "Phase 47 journal it describes — it cannot edit what it does not own")
    ok(all(s.startswith(("ALTER", "SELECT", "INSERT", "CREATE"))
           for s in sql_statements(p47store)),
       "the Phase 47 journal itself still only reads, appends and adds "
       "columns — no recorded row is ever rewritten")
    ok("ADD COLUMN" in " ".join(sql_statements(p47store)),
       "the new columns are added to an existing journal rather than requiring "
       "one to be rebuilt")

    # ------------------------------------------- both counts, never just one
    board_src = source(p47service)
    ok("event_counts" in board_src and "\"events\"" in board_src,
       "the board payload carries the events and both counts")
    cli_src = source(__import__(
        "app.research.phase47.cli", fromlist=["x"]))
    ok("LEG COUNT" in cli_src and "EVENT COUNT" in cli_src,
       "and the printed board shows both, labelled, so neither can be mistaken "
       "for the other")
    ok(cli_src.index("LEG COUNT") < cli_src.index("win rate"),
       "with the counts above the ratios that divide by them")

    # ------------------------------------------------------------ no order path
    for mod in (events, service, store_mod, cli_mod, phase49):
        text = code(mod).lower()
        ok(not any(f"import {word}" in text or f"from {word}" in text
                   for word in FORBIDDEN),
           f"{mod.__name__} imports nothing resembling an order path")
    ok("place" not in code(service).lower().replace("placeholder", ""),
       "and places nothing")
    ok(phase49.NO_ORDER_PATH in service.status()["order_path"],
       "the status says so in its own payload")
    ok(service.status()["production_effect"] == phase49.PRODUCTION_UNCHANGED,
       "and states that production is unchanged by this layer")

    # -------------------------------------------- nothing upstream moved
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
    ok("phase49" not in source(p46store),
       "Phase 46 does not know this phase exists: the dependency runs one way")
    ok(phase49.rule_fingerprint() == phase49.rule_fingerprint()
       and len(phase49.rule_fingerprint()) == 16,
       "the grouping rule has a stable fingerprint of its own, so a retuned "
       "gap would be visible as a different population rather than a longer "
       "one")

    print()
    print(f"PHASE 49 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


def _keys_read(mod: object) -> set[str]:
    """Every string key the module passes to a ``.get(...)``."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(source(mod))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            out.add(node.args[0].value)
    return out


if __name__ == "__main__":
    sys.exit(main())
