"""Phase 39 smoke — the properties that stop a feasibility table from lying.

A cost-to-edge table is the easiest document in this repository to misuse: it
produces one ordered list of instruments, and an ordered list of instruments is
read as a shortlist no matter what the caption says. Every check below exists
because the opposite mistake would produce a confident, wrong shortlist:

* charging fees on entry-level turnover, which understates the required move in
  the flattering direction;
* charging the spread twice, or not at all, once movement is measured on mids;
* charging futures brokerage twice, which Phase 19's decomposition invites;
* pricing a round trip with no lot size, or on a one-sided or crossed book;
* letting a simulated or fixture quote into a "measured" cost;
* forward-filling a gap in the path, so an unquoted contract looks stationary;
* counting an adverse-only leg as having offered a negative move rather than
  nothing;
* ranking on each pair's own best horizon, which is a threshold chosen with
  hindsight;
* ranking a forty-instant row beside a four-thousand-instant row;
* labelling an instrument uneconomic when what is thin is the capture;
* measuring a futures excursion on an assumed direction;
* emitting a promotion label, or the word edge, from a capability measurement;
* writing anything at all to the store it reads.

    .venv/bin/python _smoke_phase39.py
"""
from __future__ import annotations

import inspect
import json
import os
import sqlite3
import tempfile

from app.research.phase35 import (
    CE,
    FUTURES,
    LONG,
    MEASURED_EXECUTABLE,
    PE,
    SHORT,
    UNMEASURED,
    store,
)
from app.research.phase39 import (
    BARELY_PAYS,
    CANNOT_PAY,
    CAPABILITY_LABELS,
    CLEARS_GATE,
    CLOSE,
    GATE_MULTIPLE,
    INSUFFICIENT,
    JSON_NAME,
    MAX_MOVE_INSTANTS_PER_SESSION_KEY,
    MD_NAME,
    MIN_INSTANTS_FOR_LABEL,
    MIN_INSTANTS_FOR_RANK,
    PAYS,
    REFERENCE_HORIZON,
    cli,
    cost,
    feasibility,
    movement,
    report,
    service,
)

PASS = 0
FAIL: list[str] = []

T0 = 1_788_752_100.0
MIN = 60.0
LOT = 100
CE_SYM = "CRUDEOIL17SEP268700CE"
PE_SYM = "CRUDEOIL17SEP268700PE"
FUT_SYM = "CRUDEOIL21SEP26FUT"
NIF_SYM = "NIFTY25SEP2624000CE"


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def _obs(obs_id: str, *, ts: float, instrument: str, direction: str | None,
         lot_size: int | None = LOT) -> dict:
    return {
        "obs_id": obs_id,
        "ts": ts,
        "session": None,
        "instrument": instrument,
        "family": "MCX" if instrument == "CRUDEOIL" else "INDEX",
        "source": "BOARD",
        "opportunity_type": "DIRECTIONAL",
        "direction": direction,
        "engine_class": "BUY",
        "engine_selected": False,
        "selected_vehicle": None,
        "context": {"plan": {"lot_size": lot_size}, "book_age_ms": 200.0},
        "origin": "SMOKE",
        "origin_id": obs_id,
        "ingest_ts": ts,
    }


def _q(obs_id: str, vehicle: str, *, ts: float, bid: float, ask: float,
       symbol: str, lot_size: int | None = LOT,
       evidence: str = MEASURED_EXECUTABLE, reason: str | None = None) -> dict:
    spread = ask - bid
    return {
        "obs_id": obs_id, "vehicle": vehicle, "ts": ts, "symbol": symbol,
        "strike": 8700.0, "expiry": "2026-09-17", "dte": 10,
        "bid": bid, "ask": ask, "traded": (bid + ask) / 2.0,
        "spread": spread, "spread_pct": 100.0 * spread / ask,
        "delta": 0.5, "iv": 32.0, "oi": 4000.0, "volume": 900.0,
        "underlying": 8700.0, "basis": 2.0, "lot_size": lot_size,
        "evidence": evidence, "reason": reason,
    }


def _series(con, *, instrument: str, vehicle: str, symbol: str,
            mids: list[float], spread: float, direction: str | None = LONG,
            step_min: float = 5.0, tag: str = "s", lot_size: int | None = LOT,
            evidence: str = MEASURED_EXECUTABLE,
            reason: str | None = None) -> None:
    """One contract quoted every ``step_min`` minutes at the given mids."""
    rows, quotes = [], []
    for i, mid in enumerate(mids):
        obs_id = f"{tag}-{instrument}-{vehicle}-{i}"
        ts = T0 + i * step_min * MIN
        rows.append(_obs(obs_id, ts=ts, instrument=instrument,
                         direction=direction, lot_size=lot_size))
        quotes.append(_q(
            obs_id, vehicle, ts=ts, bid=mid - spread / 2.0,
            ask=mid + spread / 2.0, symbol=symbol, lot_size=lot_size,
            evidence=evidence, reason=reason,
        ))
    store.save_observations(con, rows)
    store.save_quotes(con, quotes)


def _leg(*, required: float, mfe: float, end: float | None = None,
         instrument: str = "CRUDEOIL", vehicle: str = CE,
         realised: float | None = None, spread: float | None = None,
         horizon: str = REFERENCE_HORIZON) -> dict:
    return {
        "instrument": instrument, "vehicle": vehicle, "session": "2026-09-08",
        "ts": T0, "symbol": CE_SYM, "leg_direction": LONG, "mid": 100.0,
        "entry_fill": 100.5, "required_pct": required,
        "required_points": required, "spread_pct": (
            required / 2.0 if spread is None else spread
        ),
        "priced_forward": 10,
        "horizons": {horizon: {
            "mfe_pct": mfe,
            "end_pct": mfe if end is None else end,
            "time_to_peak_min": 5.0,
            "hold_min": 30.0,
            "realised_end_pct": realised,
            "realised_mfe_pct": realised,
        }},
    }


def main() -> int:  # noqa: PLR0915 - one flat list of independent checks
    # ------------------------------------------------------------------ §1 cost
    req = cost.required_move(
        vehicle=CE, instrument="CRUDEOIL", bid=99.0, ask=101.0, lot_size=LOT,
    )
    ok(req["spread_points"] == 2.0 and req["mid"] == 100.0,
       "the spread is the quoted width and the mid is its centre")
    ok(req["required_points"] > req["spread_points"],
       "the required move is more than the spread — fees are charged too")
    ok(abs(
        req["required_points"]
        - (req["spread_points"] + req["brokerage_points"]
           + req["statutory_points"] + req["slippage_points"]
           + req["fee_residual_points"])
    ) < 1e-6,
       "required = spread + brokerage + statutory + slippage + residual, exactly")
    ok(abs(req["required_pct_of_mid"]
           - 100.0 * req["required_points"] / req["mid"]) < 1e-6,
       "the percentage form is the points form over the entry mid")

    flat = cost.fee_points(
        CE, "CRUDEOIL", ask=101.0, exit_price=101.0, lot_size=LOT,
    )
    ok(flat is not None and req["fees_points"] > flat["fees_points"],
       "fees are solved on break-even turnover, not on a flat exit — charging "
       "the flat exit would understate the requirement")

    wide = cost.required_move(
        vehicle=CE, instrument="CRUDEOIL", bid=95.0, ask=105.0, lot_size=LOT,
    )
    ok(wide["required_pct_of_mid"] > req["required_pct_of_mid"],
       "a wider book requires a larger move")
    cheap = cost.required_move(
        vehicle=CE, instrument="CRUDEOIL", bid=4.9, ask=5.1, lot_size=LOT,
    )
    ok(cheap["required_pct_of_mid"] > req["required_pct_of_mid"],
       "a flat per-order fee is a wall on a cheap contract, not a rounding error")

    fut = cost.required_move(
        vehicle=FUTURES, instrument="CRUDEOIL", bid=8698.0, ask=8700.0,
        lot_size=LOT,
    )
    ok(abs(fut["required_points"] - (
        fut["spread_points"] + fut["brokerage_points"] + fut["statutory_points"]
        + fut["slippage_points"] + fut["fee_residual_points"]
    )) < 1e-6,
       "futures brokerage is de-overlapped from the statutory line, so it is "
       "charged once")
    ok(fut["brokerage_points"] > 0 and fut["statutory_points"] > 0,
       "the futures decomposition keeps brokerage and statutory apart")

    ok(cost.required_move(
        vehicle=CE, instrument="CRUDEOIL", bid=99.0, ask=101.0, lot_size=None,
    )["required_points"] is None,
       "no lot size means no priced round trip, not a free one")
    ok(cost.required_move(
        vehicle=CE, instrument="CRUDEOIL", bid=101.0, ask=99.0, lot_size=LOT,
    )["required_points"] is None,
       "a crossed book is refused rather than repaired")

    # ------------------------------------------------ lot size, and where from
    ok(cost.resolve_lot({"instrument": "CRUDEOIL", "lot_size": 7})
       == (7, cost.LOT_FROM_QUOTE),
       "the lot the feed captured wins over any table")
    ok(cost.resolve_lot({
        "instrument": "CRUDEOIL", "lot_size": None,
        "context_json": json.dumps({"plan": {"lot_size": 9}}),
    }) == (9, cost.LOT_FROM_PLAN),
       "next comes the lot the plan carried at the decision instant — the "
       "source Phase 36 priced its triples from")
    ok(cost.resolve_lot({"instrument": "CRUDEOIL", "lot_size": None})
       == (100, cost.LOT_FROM_REGISTRY),
       "last comes the published contract lot, and a null in the feed no "
       "longer makes a knowable cost unmeasurable")
    ok(cost.resolve_lot({"instrument": "NOT_A_REAL_SYMBOL", "lot_size": None})
       == (None, None),
       "an unknown symbol resolves to nothing — get_spec would have silently "
       "charged it CRUDEOIL's lot of 100")
    ok(cost.resolve_lot({
        "instrument": "CRUDEOIL", "lot_size": 0,
        "context_json": "{not json",
    }) == (100, cost.LOT_FROM_REGISTRY),
       "a zero lot and unparseable context fall through instead of raising")
    ok(cost.required_move(
        vehicle=CE, instrument="CRUDEOIL", bid=0.0, ask=101.0, lot_size=LOT,
    )["required_points"] is None,
       "a one-sided book is refused rather than half-priced")

    # -------------------------------------------------------------- §2 sampling
    rows = [{"i": i} for i in range(1000)]
    ok(cost.stride(rows, 10)[0] == rows[0]
       and cost.stride(rows, 10)[-1]["i"] > 800
       and len(cost.stride(rows, 10)) == 10,
       "sampling is an even stride across the whole session, not its first rows")
    ok(cost.stride(rows, 10) == cost.stride(rows, 10),
       "sampling is deterministic, so a re-run reads the same instants")
    ok(cost.stride(rows[:5], 10) == rows[:5],
       "a short list is not padded or truncated")

    # ------------------------------------------------------------- §3 direction
    ok(cost.leg_direction(CE, SHORT) == LONG
       and cost.leg_direction(PE, SHORT) == LONG,
       "a bought option is long its own premium whichever way the view is put")
    ok(cost.leg_direction(FUTURES, "BEARISH") == SHORT,
       "a bearish futures leg is measured as a short")
    ok(cost.leg_direction(FUTURES, None) is None,
       "a futures leg with no recorded direction is not assumed to be long")

    # ------------------------------------------------------- §4 ratio and bands
    legs = [_leg(required=1.0, mfe=4.0), _leg(required=4.0, mfe=4.0)]
    row = feasibility.horizon_row(legs, REFERENCE_HORIZON)
    ok(row["move_over_cost"] == 2.5,
       "MOVE/COST is the median of per-instant ratios (4/1 and 4/4 -> 2.5), "
       "not the ratio of medians (4/2.5 -> 1.6)")
    ok(row["reach_rate_pct"]["1.0x"] == 100.0
       and row["reach_rate_pct"][f"{GATE_MULTIPLE}x"] == 50.0,
       "reach rates count instants that cleared a multiple of their own cost")
    ok(feasibility.horizon_row(
        [_leg(required=1.0, mfe=0.0)], REFERENCE_HORIZON,
    )["favourable_move_pct"]["median"] == 0.0,
       "a leg that never went favourable offered nothing, not a negative move")

    ok(feasibility.label_of(0.4, 500) == CANNOT_PAY
       and feasibility.label_of(1.5, 500) == BARELY_PAYS
       and feasibility.label_of(2.5, 500) == PAYS
       and feasibility.label_of(9.0, 500) == CLEARS_GATE,
       "the capability bands are the pre-declared ones")
    ok(feasibility.label_of(9.0, MIN_INSTANTS_FOR_LABEL - 1) == INSUFFICIENT,
       "a ratio below the label floor gets no label, however good it looks")
    ok(feasibility.triage_of(CANNOT_PAY) == feasibility.DEPRIORITISE
       and feasibility.triage_of(INSUFFICIENT) == feasibility.UNDECIDED
       and feasibility.triage_of(CLEARS_GATE) == feasibility.KEEP,
       "triage separates 'cannot pay' from 'not yet measured'")
    ok(all(
        "EDGE" not in label and "VALIDATED" not in label
        for label in CAPABILITY_LABELS
    ), "no capability label contains a promotion word")

    stress = feasibility.stress_rows(
        [_leg(required=1.0, mfe=4.0)], REFERENCE_HORIZON,
    )
    ok(stress[0]["move_over_cost"] == 4.0
       and stress[-1]["cost_multiple"] == 2.0
       and stress[-1]["move_over_cost"] == 2.0,
       "doubling the round trip halves MOVE/COST")

    rec = feasibility.reconciliation(
        [_leg(required=1.0, mfe=3.0, end=3.0, realised=2.0, spread=0.6),
         _leg(required=1.0, mfe=3.0, end=3.0, realised=None)],
        REFERENCE_HORIZON,
    )
    ok(rec["n"] == 1 and rec["legs_without_executable_exit"] == 1
       and abs(rec["residual_pct"]["median"] - (2.0 - 1.6)) < 1e-6,
       "the mid frame is reconciled against the executable frame, and a leg "
       "with no executable exit is counted rather than assumed to agree")

    # -------------------------------------------------- §5 no hindsight horizon
    mixed = [
        {**_leg(required=1.0, mfe=1.0),
         "horizons": {REFERENCE_HORIZON: {
             "mfe_pct": 1.0, "end_pct": 1.0, "time_to_peak_min": 1.0,
             "hold_min": 30.0, "realised_end_pct": None,
             "realised_mfe_pct": None,
         }, "120": {
             "mfe_pct": 9.0, "end_pct": 9.0, "time_to_peak_min": 100.0,
             "hold_min": 120.0, "realised_end_pct": None,
             "realised_mfe_pct": None,
         }}}
        for _ in range(MIN_INSTANTS_FOR_RANK)
    ]
    pairs = feasibility.pair_rows(mixed)
    pair = pairs["CRUDEOIL|CE"]
    ok(pair["capability"] == BARELY_PAYS,
       "the label comes from the pre-declared reference horizon")
    ok(pair["best_horizon"]["horizon"] == "120"
       and pair["best_horizon"]["used_for_ranking"] is False,
       "the best horizon is reported as a sensitivity and excluded from ranking")
    ok(feasibility.ranking(pairs)["ranked"][0]["move_over_cost"]
       == pair["reference"]["move_over_cost"],
       "the ranking uses the reference-horizon ratio, not the best one")

    thin = feasibility.pair_rows([
        _leg(required=1.0, mfe=9.0) for _ in range(MIN_INSTANTS_FOR_LABEL + 1)
    ])
    rank = feasibility.ranking(thin)
    ok(not rank["ranked"] and len(rank["measured_below_rank_floor"]) == 1,
       "a measured pair below the ranking floor is labelled but not ranked "
       "beside a deeply-captured one")
    ok(feasibility.ranking(feasibility.pair_rows(
        [_leg(required=1.0, mfe=9.0)],
    ))["below_label_floor"][0]["capability"] == INSUFFICIENT,
       "a barely-captured pair is reported as unmeasured, not as uneconomic")

    # ------------------------------------------------------ end-to-end on store
    tmp = tempfile.mkdtemp(prefix="phase39-")
    db = os.path.join(tmp, "opportunity.db")
    con = store.connect(db)

    # CRUDEOIL CE: a 0.4% book that then travels 6% — capable of paying.
    _series(con, instrument="CRUDEOIL", vehicle=CE, symbol=CE_SYM,
            mids=[100.0 + 0.4 * i for i in range(40)], spread=0.4)
    # CRUDEOIL PE: an 8% book that barely moves — cannot pay, however read.
    _series(con, instrument="CRUDEOIL", vehicle=PE, symbol=PE_SYM,
            mids=[50.0 + 0.02 * i for i in range(40)], spread=4.0, tag="pe")
    # A short futures leg: the favourable direction is down.
    _series(con, instrument="CRUDEOIL", vehicle=FUTURES, symbol=FUT_SYM,
            mids=[8700.0 - 2.0 * i for i in range(40)], spread=2.0,
            direction=SHORT, tag="fut")
    # A simulated book, which may never be priced as measured cost.
    _series(con, instrument="NIFTY", vehicle=CE, symbol=NIF_SYM,
            mids=[120.0 + i for i in range(40)], spread=0.5, tag="sim",
            evidence=UNMEASURED, reason="SIMULATED_BOOK_NOT_EVIDENCE")

    before = {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
        for t in store.RAW_TABLES
    }
    state = service.run(con)
    after = {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
        for t in store.RAW_TABLES
    }
    ok(before == after, "the study writes nothing to the store it reads")

    ok("NIFTY|CE" not in state["pairs"] and "NIFTY" not in state["instruments"],
       "a simulated book never becomes a measured cost")
    ok(any("SIMULATED" in k for k in (
        state["quote_coverage"].get("NIFTY|CE", {})
        .get("not_executable_by_reason") or {}
    )), "the refused simulated book is still reported, with its reason")

    ce_pair = state["pairs"]["CRUDEOIL|CE"]
    pe_pair = state["pairs"]["CRUDEOIL|PE"]
    ok(ce_pair["reference"]["move_over_cost"]
       > pe_pair["reference"]["move_over_cost"],
       "the tight-spread mover outranks the wide-spread drifter")
    ok(pe_pair["capability"] in (CANNOT_PAY, INSUFFICIENT),
       "an 8% round trip against a 0.04% drift cannot pay its costs")
    ok(state["pairs"]["CRUDEOIL|FUTURES"]["reference"]["favourable_move_pct"][
        "median"] > 0,
       "a short futures leg's favourable excursion is measured downwards")

    horizons = ce_pair["horizons"]
    ok(horizons["1"]["n"] == 0 and horizons["2"]["n"] == 0
       and horizons[REFERENCE_HORIZON]["n"] > 0,
       "a horizon with no quote inside it is unanswered — the 1- and 2-minute "
       "holds of a contract quoted every five minutes are absent, not answered "
       "from a later quote")
    ok(state["coverage"]["unanswered_horizons"].get("1", 0) > 0,
       "the unanswered horizons are counted in coverage, not hidden")
    ok(CLOSE in horizons and horizons[CLOSE]["n"] > 0,
       "the session-close horizon is answered from the last measured quote")

    ok(state["coverage"]["skipped_no_direction"] == 0
       and state["coverage"]["walked"] > 0,
       "coverage counts what was walked and what was skipped, per reason")
    ok(state["coverage"]["cap_per_instrument_vehicle_session"]
       == MAX_MOVE_INSTANTS_PER_SESSION_KEY,
       "the declared sampling cap is reported with the result")
    ok(set(state["cost"]) >= {"CRUDEOIL|CE", "CRUDEOIL|PE", "CRUDEOIL|FUTURES"},
       "every pair with an executable book appears in the cost table")
    ok(state["cost"]["NIFTY|CE"]["evidence"] == cost.NO_BOOK
       and state["cost"]["NIFTY|CE"]["priced"] == 0,
       "a pair with no executable book keeps its row, marked unmeasured "
       "rather than dropped — an absent instrument reads as one nobody "
       "looked at")
    ok("NIFTY|CE" in state["triage"]["undecided"],
       "a pair with no executable book is undecided, not quietly missing from "
       "the triage")
    ce_cost = state["cost"]["CRUDEOIL|CE"]
    ok(abs(
        ce_cost["required_rupees_one_lot"]["median"]
        - ce_cost["required_points"]["median"] * LOT
    ) < 1.0,
       "the rupee figure is one lot of the points figure, not a position")
    ok(set(ce_cost["components_pct_of_mid"])
       == {"spread", "brokerage", "statutory", "slippage", "residual"},
       "the cost keeps its components apart, residual included")

    # The regression that mattered on the real store: a feed that leaves
    # lot_size null on every quote row, and a plan that carries none either.
    _series(con, instrument="SILVER", vehicle=FUTURES, symbol="SILVER05DEC26FUT",
            mids=[95000.0 + 40 * i for i in range(40)], spread=60.0,
            tag="nolot", lot_size=None)
    nolot = service.run(con, instrument="SILVER")
    ok(nolot["coverage"]["walked"] > 0
       and cost.NO_LOT not in nolot["coverage"]["unwalkable_by_reason"],
       "a null lot on the quote and in the plan no longer refuses the round "
       "trip, because the contract lot is published")
    ok(nolot["coverage"]["lot_size_sources"].get(cost.LOT_FROM_REGISTRY)
       == nolot["coverage"]["walked"],
       "and every such leg says so: the lot came from the registry, not from "
       "the book")

    # A futures observation with no direction must be counted, not assumed long.
    _series(con, instrument="GOLD", vehicle=FUTURES, symbol="GOLD05OCT26FUT",
            mids=[70000.0 + 5 * i for i in range(6)], spread=10.0,
            direction=None, tag="nodir")
    nodir = service.run(con)
    ok(nodir["coverage"]["skipped_no_direction"] > 0
       and "GOLD|FUTURES" not in nodir["pairs"],
       "a futures leg with no recorded direction is skipped and counted, not "
       "measured on an assumed side")
    ok("GOLD|FUTURES" in nodir["cost"],
       "its round-trip cost is still reported — the cost is knowable, the "
       "favourable side is not")

    # ------------------------------------------------------------- §6 artefacts
    paths = service.write_artefacts(state, directory=tmp)
    ok([os.path.basename(p) for p in paths] == [MD_NAME, JSON_NAME],
       "exactly the two named artefacts are written")
    md = open(paths[0], encoding="utf-8").read()
    payload = json.loads(open(paths[1], encoding="utf-8").read())

    ok(md.startswith("# COST-TO-EDGE FEASIBILITY")
       and "NO NEW DATA WAS COLLECTED" in md
       and "IT IS NOT AN EDGE, A SIGNAL, OR A PROFITABILITY CLAIM." in md,
       "the document opens with what it is and what it is not")
    ok(f"SESSION_COUNT = {len(state['sessions'])}" in md,
       "the session count is printed, and measured rather than hardcoded")
    for banned in ("VALIDATED", "PRODUCTION_READY", "PRODUCTION READY"):
        ok(banned not in md and banned not in json.dumps(payload),
           f"the artefacts cannot say {banned}")
    ok("is not an edge" in md.lower() or "not an edge" in md.lower(),
       "the artefact carries the not-an-edge stamp")
    ok(md.rstrip().endswith(f"CURRENT ACTION: {state['action']}")
       and state["action"] in report.FOOTER_ACTIONS,
       "the document ends on the action line, with nothing after it")
    ok("MOVE/COST" in md and "median of those per-instant" in md,
       "the ranking section defines the ratio it ranks by")
    ok("ceiling" in md and "hindsight" in md,
       "the document states that the numerator is a hindsight ceiling")

    # Every table must keep its columns: an instrument|vehicle key carries the
    # column separator, and an unescaped one shifts every later column silently.
    widths, bad = [], []
    for line in md.splitlines():
        if not line.startswith("|"):
            widths = []
            continue
        cells = line.count("|") - line.count("\\|")
        if set(line.replace("|", "").replace("-", "").strip()) == set():
            widths.append(cells)
        elif widths and cells != widths[-1]:
            bad.append(line)
    ok(not bad, "every table row has as many columns as its header")
    ok("CRUDEOIL\\|CE" in md,
       "the pipe inside an instrument|vehicle key is escaped, not left to "
       "break the table")

    empty = service.run(con, instrument="DOES_NOT_EXIST")
    ok(empty["action"] == service.REQUIRES_DATA_FIX
       and not empty["pairs"],
       "no executable book is a data problem, not a finding about the market")
    ok(report.render(empty).rstrip().endswith(
        f"CURRENT ACTION: {service.REQUIRES_DATA_FIX}"),
       "the empty document still ends on its action line")

    # -------------------------------------------------------------- §7 read-only
    # Stronger than reading the source for the word UPDATE: SQLite itself is
    # asked to refuse every write, and the whole study is then run again.
    denied: list[str] = []
    writes = {
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TRIGGER,
    }

    def authorizer(action, arg1, arg2, dbname, source):  # noqa: ANN001, ANN202
        if action in writes:
            denied.append(f"{action}:{arg1}")
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    unguarded = service.run(con)
    con.set_authorizer(authorizer)
    try:
        guarded = service.run(con)
        ok(not denied and guarded["legs"] == unguarded["legs"],
           "the whole study runs with SQLite refusing every write, so it is "
           "read-only by execution and not merely by inspection")
    finally:
        con.set_authorizer(None)

    for module in (cost, movement, feasibility, report, service, cli):
        src = inspect.getsource(module)
        for banned in ("save_quotes", "save_observations", "place_order",
                       "order_path", "resolve_all"):
            ok(banned not in src,
               f"{module.__name__} does not call {banned}")
    ok("--no-artefacts" in inspect.getsource(cli),
       "the CLI can print without writing, for a read-only inspection")
    ok("--instrument" in inspect.getsource(cli)
       and "default=None" in inspect.getsource(cli),
       "the instrument flag is an optional filter; the default is every "
       "instrument captured")

    summ = service.summary(state)
    ok(summ["not_an_edge"] == state["not_an_edge"]
       and summ["action"] == state["action"]
       and summ["coverage"] == state["coverage"],
       "the CLI summary carries the coverage, the action and the caveat")
    ok("not an edge" in state["headline"].lower(),
       "the terminal headline repeats that this is capability, not an edge")

    con.close()
    print(f"\nPHASE 39 SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
