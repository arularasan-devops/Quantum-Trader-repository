"""Phase 45 smoke — the properties that keep a shadow board a shadow board.

Each check is here because its absence has already cost this project something,
or would:

* a research module reaching an order or a broker, which is how a study becomes
  a trading change by accident — the import graph is walked transitively, not
  grepped one level deep;
* an admission that reads a field which only exists after the leg is over,
  which is how Phase 42's first ratio came to be built on a realised peak;
* a missing option book filled from the futures quote captured at the same
  instant, or from an LTP, or from a midpoint — three different ways to turn an
  unmeasurable instant into a tradeable-looking one;
* CE and PE collapsed into one "option" row, which deletes the only comparison
  this phase exists to make;
* a duplicate observation appending a second journal event, which would inflate
  every count on the board and silently rewrite what an event said at the time;
* a definition change pooling with the old namespace, which is exactly the
  Phase 40 failure — two releases, two answers, nothing in the payload saying
  which was which;
* Phase 41, Phase 42 or Phase 44 moved by anything done here;
* a board that reads as a result when it is a row count.

    .venv/bin/python _smoke_phase45.py
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import tempfile
import threading
import time

from app.config import settings
from app.research import phase45
from app.research.phase17 import quality, schema
from app.research.phase17 import service as p17service
from app.research.phase35 import LOT_FROM_SPEC
from app.research.phase35 import lots as p35lots
from app.research.phase41 import freeze as p41freeze
from app.research.phase42 import freeze as p42freeze
from app.research.phase42 import LIVE_MULTIPLE
from app.research.phase44 import freeze as p44freeze
from app.research.phase44 import store as p44store
from app.research.phase44 import switch as p44switch
from app.research.phase45 import (
    evaluator,
    freeze,
    ranges,
    resolver,
    service,
    store,
)

PASS = 0
FAIL: list[str] = []

# The fingerprints as frozen before this phase existed. Phase 45 reads them; if
# any moves, something here has edited an upstream definition.
FROZEN_41 = "34fc64a299d29760"
FROZEN_42 = "b67709af902c8bff"
FROZEN_44 = "95d393256ed1f505"

MINUTE = 60.0


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def quote(
    vehicle: str,
    *,
    instrument: str = "NIFTY",
    bid: float | None = 100.0,
    ask: float | None = 101.0,
    delta: float | None = 0.5,
    dq: str = quality.EXACT,
    symbol: str | None = None,
    lot: int | None = 50,
    bid_size: int | None = 750,
    ask_size: int | None = 1200,
) -> schema.Quote:
    return schema.Quote(
        instrument=instrument, vehicle=vehicle,
        symbol=symbol or f"NIFTY26SEP{vehicle}",
        strike=None if vehicle == phase45.FUTURES else 24000.0,
        expiry="2026-09-24", days_to_expiry=13,
        bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
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
            phase45.PE, delta=-0.48, symbol="NIFTY26SEPPE"),
        futures=fut if fut is not None else quote(
            phase45.FUTURES, bid=24008.0, ask=24009.0, delta=1.0,
            symbol="NIFTY26SEPFUT", lot=50),
        plan=schema.Plan(lot_size=50, expected_move_points=40.0),
        context=schema.MarketContext(
            atr_points=90.0, regime="TREND", session_period="MORNING",
            market_signal="BUY",
        ),
    )


def wide_range_for(instrument: str, ts: float) -> ranges.LiveRanges:
    """A live range with a wide, real, pre-decision travel."""
    live = ranges.LiveRanges()
    for i in range(12, 0, -1):
        live.note(instrument, ts - i * MINUTE, 24000.0 + (i % 3) * 60.0)
    return live


def wide_range(ts: float) -> ranges.LiveRanges:
    return wide_range_for("NIFTY", ts)


def rows_for(obs: schema.Observation, live: ranges.LiveRanges) -> dict:
    out = evaluator.evaluate(
        obs,
        trailing_range=live.before(obs.instrument, obs.signal_ts),
        definition=freeze.definition(),
        ingest_ts=time.time(),
    )
    return {r["vehicle"]: r for r in out}


def imports_of(module_name: str, seen: set[str]) -> set[str]:
    """Every ``app.*`` module reachable from one module, transitively.

    Transitively because a one-level grep for "order" passes a module that
    imports a helper that imports the order path. That is the failure this
    check has to catch.
    """
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
    """Every field name the module *reads* — attributes, subscripts and ``.get``.

    A keyword argument name is deliberately not a read: costing a round trip
    with ``cost_of(..., exit_price=entry)`` prices the entry twice and looks
    nothing like consulting an exit that has not happened. Matching on raw text
    cannot tell those apart, so this walks the tree instead.
    """
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


def direct_imports(module: object) -> set[str]:
    """The module names imported by one module, one level, not transitively."""
    out: set[str] = set()
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.rsplit(".", 1)[-1] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.update(a.name for a in node.names)
            out.add((node.module or "").rsplit(".", 1)[-1])
    return out


def p44_armed() -> bool:
    """Whether the Phase 44 recorder may write, read without creating its journal.

    A missing journal is dormancy by construction — the state is DORMANT with no
    switch event — so the absence of the file is not treated as unknown.
    """
    path = p44store.db_path()
    if not os.path.exists(path):
        return False
    con = store.open_read_only(path)
    try:
        return bool(p44switch.state(con).get("may_record"))
    finally:
        con.close()


def main() -> int:
    ts = time.time() - 3600.0
    live = wide_range(ts)

    # ------------------------------------------------ upstream, untouched
    ok(p41freeze.fingerprint()["definition"] == FROZEN_41,
       "Phase 41's definition is where it was frozen")
    ok(p42freeze.fingerprint()["definition"] == FROZEN_42,
       "Phase 42's definition is where it was frozen")
    ok(p44freeze.fingerprint()["definition"] == FROZEN_44,
       "Phase 44's definition is where it was frozen")
    ok(not p44_armed(), "and Phase 44 is still dormant")
    fp = freeze.fingerprint()
    ok(fp["inherited"]["phase41"] == FROZEN_41
       and fp["inherited"]["phase42"] == FROZEN_42,
       "Phase 45 records the upstream fingerprints it borrows arithmetic from")
    ok(len(fp["definition"]) == 16 and fp["definition"] != FROZEN_42,
       "and has its own definition, in its own namespace")

    # ------------------------------------------------ no order path, at all
    reachable = set()
    for mod in ("app.research.phase45.evaluator", "app.research.phase45.service",
                "app.research.phase45.store", "app.research.phase45.resolver",
                "app.research.phase45.ranges", "app.research.phase45.freeze"):
        __import__(mod)
        imports_of(mod, reachable)
    forbidden = [
        m for m in reachable
        if any(part in m for part in ("order", "broker", "execution", "trade_api"))
    ]
    ok(not forbidden,
       f"nothing reachable from Phase 45 is an order path (found {forbidden})")
    ok(not any(m.startswith("app.services.") and "paper" not in m
               for m in reachable if "order" in m),
       "and no service-layer order module is reachable either")
    p45_src = "".join(
        inspect.getsource(m) for m in (evaluator, service, resolver, store, ranges)
    )
    for banned in ("place_order", "placeOrder", "square_off", "modify_order",
                   "cancel_order"):
        ok(banned not in p45_src, f"no call named {banned} exists in Phase 45")

    # ------------------------------------------------ no hindsight
    read = fields_read(evaluator)
    leaked = sorted(read & set(phase45.OUTCOME_FIELDS))
    ok(not leaked, f"the evaluator reads no outcome field (found {leaked})")
    ok("resolver" not in direct_imports(evaluator),
       "and does not import the module that computes outcomes")
    ok("evaluator" not in direct_imports(resolver),
       "the resolver does not import the evaluator either — one direction only")

    # ------------------------------------------------ three vehicles, never two
    by_vehicle = rows_for(observation(ts=ts), live)
    ok(sorted(by_vehicle) == sorted(phase45.VEHICLES),
       "one row per vehicle: FUTURES, CE and PE")
    ok(by_vehicle[phase45.CE]["contract"] != by_vehicle[phase45.PE]["contract"],
       "CE and PE are separate contracts, not one option row")
    ok(by_vehicle[phase45.CE]["vehicle_label"] == "CALL (CE)"
       and by_vehicle[phase45.PE]["vehicle_label"] == "PUT (PE)"
       and by_vehicle[phase45.FUTURES]["vehicle_label"] == "FUTURES",
       "and each row names its vehicle in words the board shows")
    ok(all(r["classification"] == phase45.SHADOW_SIGNAL
           and r["definition"] == fp["definition"]
           and r["version"] == phase45.VERSION
           for r in by_vehicle.values()),
       "every row carries its classification, definition and version")

    # ------------------------------------------------ executable sides only
    ce = by_vehicle[phase45.CE]
    ok(ce["entry_side"] == "ASK" and ce["entry_price"] == ce["ask"],
       "a long call enters at the ASK")
    ok(ce["bid_size"] == 750 and ce["ask_size"] == 1200,
       "and carries the resting size on each side")
    fut_long = by_vehicle[phase45.FUTURES]
    ok(fut_long["entry_side"] == "ASK" and fut_long["entry_price"] == fut_long["ask"],
       "a long futures leg enters at the ASK")
    short = rows_for(observation(ts=ts, direction="SHORT"), live)[phase45.FUTURES]
    ok(short["entry_side"] == "BID" and short["entry_price"] == short["bid"],
       "a short futures leg enters at the BID")
    ok(short["shadow_action"] in (phase45.SHADOW_SELL, phase45.SHADOW_WAIT),
       "and a short futures signal is SHADOW_SELL, not SHADOW_BUY")
    mids = [
        r for r in by_vehicle.values()
        if r["entry_price"] is not None and r["bid"] is not None
        and r["ask"] is not None
        and abs(float(r["entry_price"]) - (r["bid"] + r["ask"]) / 2.0) < 1e-9
        and r["bid"] != r["ask"]
    ]
    ok(not mids, "no row was priced at a midpoint")
    ok(all(r["last_traded_price"] != r["entry_price"]
           for r in by_vehicle.values()
           if r["entry_price"] is not None and r["last_traded_price"] is not None),
       "and none at the last traded price")

    # ------------------------------------------------ refusals, with reasons
    cases = (
        (quote(phase45.CE, bid=None), phase45.MISSING_BID, "a missing bid"),
        (quote(phase45.CE, ask=None), phase45.MISSING_ASK, "a missing ask"),
        (quote(phase45.CE, bid=101.0, ask=100.0), phase45.CROSSED_BOOK,
         "a crossed book"),
        (quote(phase45.CE, dq=quality.MISSING), phase45.STALE_QUOTE,
         "a book too old to fill on"),
        (quote(phase45.CE, delta=None), phase45.NO_DELTA, "no delta"),
    )
    for q, reason, label in cases:
        row = rows_for(observation(ts=ts, ce=q), live)[phase45.CE]
        ok(row["shadow_action"] == phase45.SHADOW_UNMEASURED,
           f"{label} produces SHADOW_UNMEASURED")
        if reason is not None:
            ok(row["reason"] == reason,
               f"and names it {reason} (got {row['reason']})")
        ok(row["reason"] in phase45.UNMEASURED_REASONS,
           f"and the reason for {label} is machine-readable")

    spec_lot = rows_for(
        observation(ts=ts, ce=quote(phase45.CE, lot=None)), live)[phase45.CE]
    ok(spec_lot["lot_size"] == p35lots.from_spec("NIFTY")
       and spec_lot["lot_source"] == LOT_FROM_SPEC,
       "a book captured without a multiplier falls back to the contract "
       "specification, labelled as such")
    nameless = "NO_SUCH_INSTRUMENT"
    unknown = observation(
        ts=ts, instrument=nameless,
        ce=quote(phase45.CE, instrument=nameless, lot=None),
        pe=quote(phase45.PE, instrument=nameless, lot=None, delta=-0.48,
                 symbol="XPE"),
        fut=quote(phase45.FUTURES, instrument=nameless, lot=None, delta=1.0,
                  bid=24008.0, ask=24009.0, symbol="XFUT"),
    )
    no_lot = rows_for(unknown, wide_range_for(nameless, ts))
    ok(all(r["shadow_action"] == phase45.SHADOW_UNMEASURED
           and r["reason"] == phase45.NO_LOT_SIZE
           for r in no_lot.values()),
       "an instrument with no known multiplier cannot be costed, so it is "
       "UNMEASURED rather than costed at one unit")

    missing_ce = rows_for(
        observation(ts=ts, ce=quote(phase45.CE, bid=None, ask=None)), live)
    ok(missing_ce[phase45.CE]["shadow_action"] == phase45.SHADOW_UNMEASURED,
       "an absent option book is UNMEASURED")
    ok(missing_ce[phase45.CE]["entry_price"] is None,
       "and takes no price at all")
    ok(missing_ce[phase45.FUTURES]["entry_price"] is not None
       and missing_ce[phase45.CE]["entry_price"]
       != missing_ce[phase45.FUTURES]["entry_price"],
       "the futures quote at the same instant did not stand in for it")

    # ------------------------------------------------ the range, before only
    cold = ranges.LiveRanges()
    cold_row = rows_for(observation(ts=ts), cold)[phase45.CE]
    ok(cold_row["shadow_action"] == phase45.SHADOW_UNMEASURED
       and cold_row["reason"] in (phase45.NO_RANGE, phase45.CAPTURE_GAP),
       "no pre-decision range is UNMEASURED, not a zero range")
    stale = ranges.LiveRanges()
    stale.note("NIFTY", ts - 4000.0, 24000.0)
    stale.note("NIFTY", ts - 3940.0, 24100.0)
    stale_row = rows_for(observation(ts=ts), stale)[phase45.CE]
    ok(stale_row["reason"] in (phase45.STALE_DECISION_BAR, phase45.CAPTURE_GAP,
                               phase45.NO_RANGE),
       "a range whose last minute predates the decision bar is refused")
    gapped = ranges.LiveRanges()
    for i in (9, 8, 7, 6, 5, 4, 3):
        gapped.note("NIFTY", ts - i * MINUTE, 24000.0 + i)
    gap_row = rows_for(observation(ts=ts), gapped)[phase45.CE]
    ok(gap_row["reason"] in (phase45.CAPTURE_GAP, phase45.STALE_DECISION_BAR),
       "a gap between the last captured minute and the decision is refused")
    band = live.before("NIFTY", ts)
    ok(band["trailing_range_points"] is not None and band["minutes"] >= 2,
       "a range built from minutes strictly before the decision is measured")

    # ------------------------------------------------ the gate, not a new one
    ok(by_vehicle[phase45.CE]["gate_multiple"] == LIVE_MULTIPLE,
       "the gate multiple is Phase 42's live multiple, not a new number")
    thin = rows_for(observation(ts=ts, ce=quote(phase45.CE, bid=1.0, ask=20.0)),
                    live)[phase45.CE]
    ok(thin["shadow_action"] in (phase45.SHADOW_WAIT, phase45.SHADOW_UNMEASURED),
       "a wide spread does not become a signal")
    if thin["shadow_action"] == phase45.SHADOW_WAIT:
        ok(thin["reason"] == phase45.BELOW_LIVE_GATE,
           "it waits because the expected move is below the cost multiple")
        ok(thin["evidence"] == "MEASURED",
           "and it is journalled as measured, so the base rate is computable")
    else:
        ok(True, "it was refused before the gate, with a data reason")
        ok(thin["reason"] in phase45.UNMEASURED_REASONS, "and named it")
    ok(by_vehicle[phase45.PE]["shadow_action"] == phase45.SHADOW_WAIT
       and by_vehicle[phase45.PE]["reason"] == phase45.DIRECTION_DISAGREES,
       "a put on a long read waits, and says the vehicle opposed the direction")
    waiting = rows_for(observation(ts=ts, candidate_class=schema.WAIT), live)
    ok(all(r["shadow_action"] != phase45.SHADOW_BUY for r in waiting.values()),
       "an instant the engine did not treat as a fresh candidate never signals")
    ok(any(r["reason"] == phase45.NOT_A_BUY_CANDIDATE for r in waiting.values()),
       "and says so")
    ok(by_vehicle[phase45.CE]["measured_cost_points"] is not None
       and by_vehicle[phase45.CE]["modelled_cost_points"] is not None
       and by_vehicle[phase45.CE]["measured_cost_points"]
       != by_vehicle[phase45.CE]["modelled_cost_points"],
       "measured and modelled cost are both recorded, and are not the same "
       "number")
    ok(by_vehicle[phase45.CE]["measured_spread_points"] == 1.0,
       "the measured cost includes the spread that was actually quoted")

    # ------------------------------------------------ the journal
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "shadow.db")
        con = store.connect(db)
        rows = list(rows_for(observation(ts=ts), live).values())
        first = store.insert_events(con, rows)
        ok(first["written"] == 3 and first["duplicate"] == 0,
           "three vehicle rows are journalled automatically")
        again = store.insert_events(con, rows)
        ok(again["written"] == 0 and again["duplicate"] == 3,
           "the same observation re-evaluated writes nothing and counts the "
           "duplicates")
        total = con.execute("SELECT COUNT(*) FROM shadow_event").fetchone()[0]
        ok(total == 3, "so the journal still holds one event per vehicle")
        mutated = [dict(r, shadow_action=phase45.SHADOW_BUY, bid=1.0) for r in rows]
        store.insert_events(con, mutated)
        kept = con.execute(
            "SELECT bid FROM shadow_event WHERE vehicle = ?", (phase45.CE,)
        ).fetchone()[0]
        ok(kept == rows_for(observation(ts=ts), live)[phase45.CE]["bid"],
           "and a later write cannot restate what an event said at the time")

        other = [dict(r, definition="0000deadbeef0000",
                      event_id=r["event_id"][:-4] + "beef") for r in rows]
        store.insert_events(con, other)
        groups = con.execute(
            "SELECT COUNT(DISTINCT definition) FROM shadow_event"
        ).fetchone()[0]
        ok(groups == 2,
           "a different definition lands in a separate fingerprint group")
        tally_a = store.tally(con, filters={"definition": fp["definition"]})
        ok(tally_a["events"] == 3,
           "and a filtered tally cannot pool the two definitions")

        # A filter naming a column that is not on the allowlist is ignored
        # rather than concatenated into the statement.
        ok(store.tally(con, filters={"instrument": "NIFTY"})["events"] >= 3
           and store.tally(con, filters={"1=1); DROP TABLE shadow_event; --": "x"}
                           )["events"] >= 3,
           "a filter outside the allowlist changes no SQL")
        ok(con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'shadow_event'"
        ).fetchone()[0] == 1, "and the table is still there")

        board_rows = store.latest_per_vehicle(con)
        ok(len({r["vehicle"] for r in board_rows}) == 3,
           "the board shows the latest row for each vehicle")
        unresolved = store.unresolved(
            con, actions=(phase45.SHADOW_BUY, phase45.SHADOW_SELL))
        ok(all(r["shadow_action"] in (phase45.SHADOW_BUY, phase45.SHADOW_SELL)
               for r in unresolved),
           "only signalled events are queued for resolution")

        # ------------------------------------------- outcomes, in their own table
        signal = next((r for r in rows if r["vehicle"] == phase45.CE
                       and r["shadow_action"] == phase45.SHADOW_BUY), None)
        ok(signal is not None, "the measured long call produced a shadow signal")
        if signal is not None:
            outcome = resolver._empty(signal, resolver.NO_PATH)
            wrote = store.insert_outcome(con, outcome)
            ok(wrote["written"] == 1, "an outcome is written for that event")
            ok(store.insert_outcome(con, outcome)["duplicate"] == 1,
               "and resolving it twice does not write twice")
            joined = store.events(con, filters={"vehicle": signal["vehicle"]})
            ok(any(r.get("outcome_status") == resolver.NO_PATH for r in joined),
               "the board can read the outcome beside the event")
            ok(con.execute(
                "SELECT COUNT(*) FROM paper_outcome").fetchone()[0] == 1,
               "outcomes live in their own table, not on the event row")
            cols = {r[1] for r in con.execute("PRAGMA table_info(shadow_event)")}
            leaked_cols = cols & set(phase45.OUTCOME_FIELDS)
            ok(not leaked_cols,
               f"and no outcome column exists on the event table {leaked_cols}")
        summary = store.tally(con)
        ok(summary["signals"] is not None and summary["unresolved"] is not None,
           "the performance tally counts signals and what is still unresolved")
        ok(summary["unmeasured"] is not None and summary["reasons"],
           "and explains the unmeasured rows by reason")
        con.close()

    # ------------------------------------- the journal lives where the data does
    with tempfile.TemporaryDirectory() as tmp:
        original = settings.data_dir
        try:
            settings.data_dir = tmp
            ok(store.db_path().startswith(tmp),
               "the journal follows the configured data directory")
        finally:
            settings.data_dir = original
    ok(os.path.isabs(store.db_path()),
       "and resolves to an absolute path from any working directory")

    # A connection is opened on the tick path and read from the event loop's
    # executor, i.e. from another thread. This failed every live write and every
    # board read while the single-threaded checks above still passed.
    with tempfile.TemporaryDirectory() as tmp:
        con = store.connect(os.path.join(tmp, "threads.db"))
        seen: list[object] = []

        def read() -> None:
            try:
                seen.append(store.events(con, limit=1))
            except Exception as exc:  # noqa: BLE001 - the failure is the finding
                seen.append(exc)

        worker = threading.Thread(target=read)
        worker.start()
        worker.join(10.0)
        ok(seen and not isinstance(seen[0], Exception),
           f"the journal is readable from another thread {seen[:1]}")
        con.close()

    # ------------------------------------------------ the live hook
    service.reset()
    ok(service.enabled(), "the hook is on by default")
    ok(service.stats()["paper_only"] is True
       and service.stats()["order_path"] is False,
       "and states on its own status that it is paper-only with no order path")
    st = service.stats()
    ok(st["evaluations"] == 0 and st["rows"] == 0,
       "the counters start empty")
    src = inspect.getsource(service.observe)
    ok("except Exception" in src,
       "the hook swallows its own failures rather than breaking a tick")
    ok("return" in inspect.getsource(service.observe)
       and "decision" not in src.replace("decision_ts", ""),
       "and returns counts, never a decision")

    p17_src = inspect.getsource(p17service)
    ok("p45.observe(obs)" in p17_src,
       "Phase 17 calls the shadow board once, by name")
    ok(p17_src.index("write_observation") < p17_src.index("p45.observe(obs)"),
       "and only after the raw observation is already persisted")
    ok(p17_src.count("p45.") == 1,
       "with exactly one coupling point into the production tick path")

    print()
    print(f"PHASE 45 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
