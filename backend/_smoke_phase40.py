"""Phase 40 smoke — the properties that stop a first-event study from lying.

A favourable-first count is the easiest measurement in this repository to
produce by accident, because every plausible-looking bug points the answer the
same way. Each check below exists because the opposite mistake would produce a
confident, wrong verdict:

* thresholds of unequal size, which decides the race before it is run;
* a favourable threshold on the executable exit price against an adverse
  threshold on the mid, which is the same bias hidden in a frame change;
* a threshold read off the outcome distribution rather than the cost model;
* an unquoted window counted as "neither reached", turning missing data into
  evidence that the market stood still;
* a later quote answering an earlier horizon, which is look-ahead that always
  points where the price went;
* a first event that changes when the same leg is scored at a longer hold;
* a peak treated as an achievable exit;
* a giveback split on an invented percentage rather than the sign of the net;
* an adverse-first leg silently stopped out, which is an exit test in disguise;
* a vehicle comparison built from three separately-sampled populations;
* a verdict from a handful of overlapping legs of one session;
* writing anything at all to the store it reads.

    .venv/bin/python _smoke_phase40.py
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
    store,
)
from app.research.phase39 import cost as p39cost
from app.research.phase39 import movement as p39movement
from app.research.phase40 import (
    ADVERSE_FIRST,
    BOARD,
    CLOSE,
    ENGINE,
    ENTRY_DOMINANT,
    EXIT_DOMINANT,
    FAVOURABLE_FIRST,
    GIVEN_BACK,
    INSUFFICIENT,
    JSON_NAME,
    MATERIALITY_Z,
    MD_NAME,
    MIN_CLASSIFIED_FOR_VERDICT,
    MIXED,
    NEITHER_REACHED,
    RECOVERED,
    REFERENCE,
    RETAINED,
    STAYED_ADVERSE,
    UNCOVERED_WINDOW,
    UNMEASURED,
    VERDICTS,
    cli,
    firstevent,
    metrics,
    report,
    service,
    verdict,
)

PASS = 0
FAIL: list[str] = []

T0 = 1_788_752_100.0
MIN = 60.0
LOT = 100
CE_SYM = "CRUDEOIL17SEP268700CE"
PE_SYM = "CRUDEOIL17SEP268700PE"
FUT_SYM = "CRUDEOIL21SEP26FUT"


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def _obs(obs_id: str, *, ts: float, instrument: str, direction: str | None,
         source: str = "BOARD", engine_selected: bool = False) -> dict:
    return {
        "obs_id": obs_id,
        "ts": ts,
        "session": None,
        "instrument": instrument,
        "family": "MCX",
        "source": source,
        "opportunity_type": "DIRECTIONAL",
        "direction": direction,
        "engine_class": "BUY",
        "engine_selected": engine_selected,
        "selected_vehicle": None,
        "context": {"plan": {"lot_size": LOT}, "book_age_ms": 200.0},
        "origin": "SMOKE",
        "origin_id": obs_id,
        "ingest_ts": ts,
    }


def _q(obs_id: str, vehicle: str, *, ts: float, bid: float, ask: float,
       symbol: str) -> dict:
    spread = ask - bid
    return {
        "obs_id": obs_id, "vehicle": vehicle, "ts": ts, "symbol": symbol,
        "strike": 8700.0, "expiry": "2026-09-17", "dte": 10,
        "bid": bid, "ask": ask, "traded": (bid + ask) / 2.0,
        "spread": spread, "spread_pct": 100.0 * spread / ask,
        "delta": 0.5, "iv": 32.0, "oi": 4000.0, "volume": 900.0,
        "underlying": 8700.0, "basis": 2.0, "lot_size": LOT,
        "evidence": MEASURED_EXECUTABLE, "reason": None,
    }


def _series(con, *, instrument: str, vehicle: str, symbol: str,
            mids: list[float], spread: float, direction: str | None = LONG,
            step_min: float = 1.0, tag: str = "s",
            source: str = "BOARD", engine_selected: bool = False) -> None:
    rows, quotes = [], []
    for i, mid in enumerate(mids):
        obs_id = f"{tag}-{instrument}-{vehicle}-{i}"
        ts = T0 + i * step_min * MIN
        rows.append(_obs(obs_id, ts=ts, instrument=instrument,
                         direction=direction, source=source,
                         engine_selected=engine_selected))
        quotes.append(_q(obs_id, vehicle, ts=ts, bid=mid - spread / 2.0,
                         ask=mid + spread / 2.0, symbol=symbol))
    store.save_observations(con, rows)
    store.save_quotes(con, quotes)


def _contract(mids: list[float], *, spread: float = 0.4,
              step_min: float = 1.0, vehicle: str = CE,
              direction: str = LONG,
              spreads: list[float] | None = None,
              ) -> p39movement._ContractSession:
    widths = spreads or [spread] * len(mids)
    rows = [
        {"ts": T0 + i * step_min * MIN, "bid": m - widths[i] / 2.0,
         "ask": m + widths[i] / 2.0, "traded": m,
         "evidence": MEASURED_EXECUTABLE}
        for i, m in enumerate(mids)
    ]
    return p39movement._ContractSession(
        rows, vehicle=vehicle, direction=direction,
    )


def _leg(*, mids: list[float], threshold: float = 1.0, spread: float = 0.4,
         vehicle: str = CE, direction: str = LONG, step_min: float = 1.0,
         instrument: str = "CRUDEOIL", cohort: str = BOARD,
         symbol: str = CE_SYM, ts: float = T0, session: str = "2026-09-08",
         obs_id: str = "o1", spreads: list[float] | None = None) -> dict:
    """One raced leg, built through the real walk so nothing is hand-written."""
    entry_mid = mids[0]
    contract = _contract(mids, spread=spread, step_min=step_min,
                         vehicle=vehicle, direction=direction,
                         spreads=spreads)
    short = vehicle == FUTURES and direction == SHORT
    entry_fill = entry_mid + (-spread / 2.0 if short else spread / 2.0)
    # The origin of the race is the *exit* side of the entry book — bid for a
    # long, ask for a short future — exactly as book.exit_fill would give it.
    entry_exit_px = entry_mid + (spread / 2.0 if short else -spread / 2.0)
    raced = firstevent.walk(
        contract, entry_ts=T0 - MIN, entry_mid=entry_mid,
        entry_fill=entry_fill, entry_exit_px=entry_exit_px, vehicle=vehicle,
        direction=direction,
        threshold_points=threshold * entry_mid / 100.0,
        threshold_pct=threshold,
    )
    fees = p39cost.fee_points(
        vehicle, instrument, ask=entry_fill, exit_price=entry_fill,
        lot_size=LOT,
    )
    return {
        "obs_id": obs_id, "instrument": instrument, "vehicle": vehicle,
        "cohort": cohort, "source": "BOARD", "session": session, "ts": ts,
        "symbol": symbol, "leg_direction": direction, "lot_size": LOT,
        "lot_source": "LOT_FROM_QUOTE", "entry_mid": entry_mid,
        "entry_fill": entry_fill, "required_pct": threshold,
        "required_points": threshold * entry_mid / 100.0,
        "spread_pct": 100.0 * spread / entry_mid,
        "fees_points": 0.0 if fees is None else fees["fees_points"],
        **{k: v for k, v in raced.items() if k != "reason"},
    }


def main() -> int:  # noqa: PLR0915 - one flat list of independent checks
    # ------------------------------------------------- §4 predeclared symmetry
    up = _leg(mids=[100.0, 100.5, 101.5, 101.0], threshold=1.0)
    down = _leg(mids=[100.0, 99.5, 98.5, 99.0], threshold=1.0)
    ok(up["threshold_pct"] == down["threshold_pct"] == 1.0,
       "the favourable and adverse thresholds are the same distance, so the "
       "race is not decided by the geometry")
    ok(up["horizons"][REFERENCE]["event"] == FAVOURABLE_FIRST
       and down["horizons"][REFERENCE]["event"] == ADVERSE_FIRST,
       "a mirror-image path gives the mirror-image first event")

    # The frame the race is run in decides the answer, so it is asserted here
    # rather than left to the docstring. Both ends are the exit side, so a mid
    # that moves while the side that could have been hit does not — a widening
    # book — is not a threshold reached.
    widening = _leg(mids=[100.0, 101.4], threshold=1.0, spread=0.4,
                    spreads=[0.4, 3.2])
    ok(widening["horizons"]["5"]["event"] != FAVOURABLE_FIRST,
       "a favourable mid move that the exit side did not follow, because the "
       "book widened, is not reached: the race is run on the exit side")

    # The regression that changed this phase's answer. The threshold already
    # contains the quoted width (Phase 39: spread + fees), so racing from the
    # entry fill charged the spread to the favourable side twice and gave it to
    # the adverse side free — a market that never moved tripped adverse-first
    # on its very first quote. Exit-side to exit-side, a flat market scores
    # zero however wide the book is.
    for width in (0.4, 2.0, 8.0):
        flat = _leg(mids=[100.0] * 40, threshold=1.0, spread=width)
        ok(flat["horizons"][REFERENCE]["event"] == NEITHER_REACHED
           and flat["horizons"][REFERENCE]["mae_points"] == 0.0
           and flat["horizons"][REFERENCE]["mfe_points"] == 0.0,
           f"a market that did not move scores zero on a {width}-wide book, "
           "neither favourable nor adverse: the spread is charged once, in "
           "the threshold, and never inside the movement as well")
    ok("contract.mid" not in inspect.getsource(firstevent.walk),
       "the walk never reads a mid")
    ok("entry_exit_px" in inspect.getsource(firstevent.walk)
       and "exit_fill" in inspect.getsource(firstevent.legs),
       "the origin of the race is Phase 35's own exit side, so entry and exit "
       "cannot disagree about which side of the book a direction trades")

    src = inspect.getsource(firstevent.walk)
    ok("required_pct_of_mid" not in src and "mfe" not in src.split("def walk")[0],
       "the walk is handed a threshold; it does not choose one")
    ok("required_pct_of_mid" in inspect.getsource(firstevent.legs),
       "the threshold comes from Phase 39's own break-even arithmetic")

    # A threshold is set from the entry book only. Doubling the *later* path
    # cannot change it, which is what "not fitted to outcomes" means.
    calm = _leg(mids=[100.0, 100.2, 100.1], threshold=1.0)
    wild = _leg(mids=[100.0, 130.0, 70.0], threshold=1.0)
    ok(calm["threshold_pct"] == wild["threshold_pct"],
       "the threshold is unchanged by what the path did afterwards")

    # ---------------------------------------------------- §5 the race per horizon
    late = _leg(
        mids=[100.0] * 9 + [101.5] + [100.0] * 30, threshold=1.0,
        step_min=1.0,
    )
    ok(late["horizons"]["5"]["event"] == NEITHER_REACHED,
       "a threshold crossed at minute nine is not reached at the 5-minute hold")
    ok(late["horizons"]["10"]["event"] == FAVOURABLE_FIRST,
       "the same crossing is reached once the hold contains it")
    ok(late["horizons"]["5"]["time_to_favourable_min"] is None,
       "a crossing outside the window is not reported inside it — answering "
       "an early horizon from a later quote is look-ahead")

    both = _leg(mids=[100.0, 101.5, 97.0], threshold=1.0)
    ok(both["horizons"]["5"]["event"] == FAVOURABLE_FIRST,
       "when both thresholds are crossed, the first one wins")
    ok(both["first_favourable_min"] < both["first_adverse_min"],
       "the recorded crossing times keep the order they happened in")
    reverse = _leg(mids=[100.0, 97.0, 103.0], threshold=1.0)
    ok(reverse["horizons"]["5"]["event"] == ADVERSE_FIRST,
       "the same two crossings the other way round give the other answer")
    ok(reverse["horizons"]["5"]["favourable_after_adverse_min"] == 1.0,
       "an adverse-first leg records how long the favourable move took to "
       "arrive afterwards")

    flat = _leg(mids=[100.0] + [100.1] * 200, threshold=1.0)
    ok(flat["horizons"]["30"]["event"] == NEITHER_REACHED,
       "a path that crosses nothing inside a covered window is NEITHER, not "
       "an outcome")
    ok(flat["horizons"][CLOSE]["event"] == NEITHER_REACHED,
       "the session close is answered by the last quote, which is what a "
       "close is")

    short_path = _leg(mids=[100.0, 100.1, 100.2], threshold=1.0)
    ok(short_path["horizons"]["30"]["event"] == UNCOVERED_WINDOW,
       "a 30-minute hold on a 2-minute path is uncovered, not 'the market did "
       "not move'")
    ok(short_path["horizons"]["30"]["covered"] is False
       and short_path["horizons"][CLOSE]["covered"] is True,
       "coverage is per horizon, because the store's reach is per horizon")

    # A quote is one mid, so no leg can be both events at the same instant.
    for leg in (up, down, both, reverse, late, flat, short_path):
        for snap in leg["horizons"].values():
            ok(snap["event"] in (FAVOURABLE_FIRST, ADVERSE_FIRST,
                                 NEITHER_REACHED, UNCOVERED_WINDOW),
               "every horizon carries exactly one of the four states")
            break

    # Once an event has happened it cannot un-happen at a longer hold.
    events = [late["horizons"][h]["event"] for h in ("10", "15", "20", "30")]
    ok(set(events) == {FAVOURABLE_FIRST},
       "a first event, once inside the window, is the same at every longer "
       "hold — a first event that changed with the hold would not be one")

    # --------------------------------------------- short futures direction
    fut_short = _leg(
        mids=[8700.0, 8600.0], threshold=0.5, spread=2.0, vehicle=FUTURES,
        direction=SHORT,
    )
    ok(fut_short["horizons"]["5"]["event"] == FAVOURABLE_FIRST,
       "a falling price is the favourable direction for a short futures leg")
    fut_long = _leg(
        mids=[8700.0, 8600.0], threshold=0.5, spread=2.0, vehicle=FUTURES,
        direction=LONG,
    )
    ok(fut_long["horizons"]["5"]["event"] == ADVERSE_FIRST,
       "the same fall is the adverse direction for a long one")
    pe_leg = _leg(mids=[50.0, 55.0], threshold=1.0, vehicle=PE,
                  direction=SHORT, symbol=PE_SYM)
    ok(pe_leg["horizons"]["5"]["event"] == FAVOURABLE_FIRST,
       "a long PE profits when its own premium rises, whatever the view was "
       "called")

    # ------------------------------------------------------- §6 the metrics
    pool = (
        [_leg(mids=[100.0, 101.5] + [100.0] * 40, threshold=1.0,
              obs_id=f"f{i}") for i in range(6)]
        + [_leg(mids=[100.0, 97.0] + [97.0] * 40, threshold=1.0,
                obs_id=f"a{i}") for i in range(3)]
        + [_leg(mids=[100.0] + [100.1] * 41, threshold=1.0,
                obs_id=f"n{i}") for i in range(1)]
    )
    row = metrics.horizon_row(pool, horizon=REFERENCE)
    ok(row["classified"] == 10 and row["counts"][UNCOVERED_WINDOW] == 0,
       "the classified count is the legs the store could follow to the hold")
    ok(abs(
        (row["favourable_first_pct"] or 0) + (row["adverse_first_pct"] or 0)
        + (row["neither_pct"] or 0) - 100.0,
    ) < 1e-9,
       "the three shares are shares of the classified legs and sum to 100")
    ok(row["uncovered_pct"] == 0.0,
       "the uncovered share is reported against all legs, not hidden")
    mixed_pool = [*pool, _leg(mids=[100.0, 100.1], threshold=1.0,
                              obs_id="short")]
    mrow = metrics.horizon_row(mixed_pool, horizon=REFERENCE)
    ok(mrow["classified"] == 10 and mrow["n"] == 11
       and mrow["favourable_first_pct"] == row["favourable_first_pct"],
       "an uncovered leg changes no share — it is counted, printed and left "
       "out of the denominator")
    ok(row["median_time_to_favourable_min"] == 2.0
       and row["median_time_to_adverse_min"] == 2.0,
       "the event times are medians of the legs where that event happened, "
       "measured from the entry instant and not from the first quote after it")
    ok(row["mae_pct_median"] <= 0.0 <= row["mfe_pct_median"],
       "MFE is non-negative and MAE non-positive: a leg that only went "
       "against the view offered nothing rather than offering a loss")
    ok(row["cost_pct_median"] == 1.0,
       "the cost column is the same required move the thresholds were set from")
    ok(row["net_pct_median"] is not None
       and row["net_pct_median"] < row["gross_pct_median"],
       "net is below gross, because the round trip is charged on top of a "
       "gross that already crossed the spread")
    for key in ("profit_factor", "win_pct", "p75_time_to_favourable_min",
                "p75_time_to_adverse_min", "cost_over_move_median"):
        ok(key in row, f"§6 requires {key} and the row carries it")

    ok(metrics._profit_factor([1.0, 2.0]) is None,
       "a profit factor with no losses is unanswered, not infinite")
    ok(metrics._profit_factor([2.0, -1.0]) == 2.0,
       "a profit factor is gross profit over gross loss")
    ok(metrics.horizon_row([], horizon=REFERENCE)["favourable_first_pct"]
       is None,
       "an empty cohort reports nothing rather than zero percent")

    # ------------------------------------------------------ §7 giveback split
    kept = _leg(mids=[100.0, 104.0] + [103.5] * 40, threshold=1.0)
    lost = _leg(mids=[100.0, 104.0] + [96.0] * 40, threshold=1.0)
    give = metrics.giveback_row([kept, lost], horizon=REFERENCE)
    ok(give["favourable_first"] == 2 and give[RETAINED] == 1
       and give[GIVEN_BACK] == 1,
       "the split is by the sign of the net at the hold — above its own round "
       "trip or below it")
    ok(give["given_back_pct"] == 50.0 and give["retained_pct"] == 50.0,
       "the two shares are shares of the graded favourable-first legs")
    ok(give["peak_pct_median"] > 0
       and give["given_back_fraction_of_mfe_pct_median"] is not None,
       "the fraction of the peak lost is reported as a distribution beside "
       "the split, not as the split")
    ok(give["turned_negative"] == 1 and give["returned_to_entry"] == 1,
       "returned-to-entry and turned-negative are counted separately, "
       "because one is about the market and the other about the money")
    only_kept = metrics.giveback_row([kept], horizon=REFERENCE)
    ok(only_kept[GIVEN_BACK] == 0 and only_kept["given_back_pct"] == 0.0,
       "a cohort that kept everything reports a zero giveback, not a missing "
       "one")
    ok(metrics.giveback_row([down], horizon=REFERENCE)["favourable_first"] == 0,
       "an adverse-first leg never appears in the giveback table")

    peak_net = firstevent.leg_net_pct(
        lost, lost["horizons"][REFERENCE], at_peak=True,
    )
    end_net = firstevent.leg_net_pct(lost, lost["horizons"][REFERENCE])
    ok(peak_net > end_net,
       "the net at the peak is priced at the best quoted exit side and the "
       "net at the hold at the last one; the difference is the giveback")
    ok("achievable exit" in inspect.getsource(metrics).lower()
       and "ceiling" in inspect.getsource(metrics).lower(),
       "the peak is documented as a ceiling rather than an achievable exit")

    # --------------------------------------------- §8 adverse-first recovery
    recovered = _leg(mids=[100.0, 97.0, 104.0] + [104.0] * 40, threshold=1.0)
    stayed = _leg(mids=[100.0, 97.0] + [96.0] * 40, threshold=1.0)
    adv = metrics.adverse_row([recovered, stayed], horizon=REFERENCE)
    ok(adv["adverse_first"] == 2 and adv[RECOVERED] == 1
       and adv[STAYED_ADVERSE] == 1,
       "adverse-first legs are split by whether the favourable threshold "
       "arrived later, with no interpretation attached to either")
    ok(adv["median_wait_to_favourable_min"] == 1.0,
       "the wait from the adverse threshold to the favourable one is measured")
    ok(adv["mfe_before_adverse_pct_median"] == 0.0,
       "what the leg offered before the adverse move is measured separately, "
       "so 'it was never in profit' and 'it gave up a profit' stay distinct")
    ok(adv["mae_pct_median"] < 0 and adv["net_pct_median"] is not None,
       "the adverse table carries the depth of the adverse move and the final "
       "outcome")
    ok(recovered["horizons"][REFERENCE]["end_pct"] > 0,
       "no stop is applied: a leg past the adverse threshold is followed to "
       "the hold exactly as one that never crossed it")
    printed = report._waterfall(
        {"waterfall": metrics.waterfall([recovered, stayed],
                                        horizon=REFERENCE)},
    )
    ok(any("no stop is applied" in line.lower() for line in printed),
       "the report says in words that no stop was applied")

    # ---------------------------------------------------------- §12 waterfall
    water = metrics.waterfall([kept, lost, recovered], horizon=REFERENCE)
    fav_branch = water["favourable_first"]
    ok(fav_branch["giveback_pct"] >= 0
       and fav_branch["cost_pct"] == 1.0
       and fav_branch["final_net_pct"] is not None,
       "the favourable branch quantifies offered, peak, giveback, cost and net")
    ok(fav_branch["peak_rupees_one_lot"] is not None,
       "the branch is also in rupees for one lot, which is a per-lot median "
       "and not a book total — a total would need a sizing view")
    ok(water["adverse_first"]["adverse_move_pct"] < 0,
       "the adverse branch carries the measured adverse move")

    # -------------------------------------------------- §13 the decision rule
    exit_pool = (
        [_leg(mids=[100.0, 104.0] + [96.0] * 40, threshold=1.0,
              obs_id=f"g{i}") for i in range(40)]
        + [_leg(mids=[100.0, 97.0] + [96.0] * 40, threshold=1.0,
                obs_id=f"b{i}") for i in range(5)]
    )
    ex = verdict.classify(exit_pool, horizon=REFERENCE)
    ok(ex["verdict"] == EXIT_DOMINANT,
       "money that arrives first and is then lost is EXIT / GIVEBACK")
    ok(ex["race_test"]["material"] and ex["giveback_test"]["material"],
       "both halves of the exit verdict are required: arriving first, and "
       "then not being kept")

    keep_pool = [
        _leg(mids=[100.0, 104.0] + [103.5] * 40, threshold=1.0,
             obs_id=f"k{i}") for i in range(40)
    ]
    kp = verdict.classify(keep_pool, horizon=REFERENCE)
    ok(kp["verdict"] == MIXED,
       "money that arrives first and is kept is not a leak, so the verdict is "
       "not EXIT even though favourable-first dominates")

    entry_pool = (
        [_leg(mids=[100.0, 97.0] + [96.0] * 40, threshold=1.0,
              obs_id=f"e{i}") for i in range(40)]
        + [_leg(mids=[100.0, 104.0] + [96.0] * 40, threshold=1.0,
                obs_id=f"x{i}") for i in range(5)]
    )
    en = verdict.classify(entry_pool, horizon=REFERENCE)
    ok(en["verdict"] == ENTRY_DOMINANT,
       "a loss that arrives before the money is ENTRY / DIRECTION")

    tie_pool = (
        [_leg(mids=[100.0, 104.0] + [96.0] * 40, threshold=1.0,
              obs_id=f"t{i}") for i in range(20)]
        + [_leg(mids=[100.0, 97.0] + [96.0] * 40, threshold=1.0,
                obs_id=f"u{i}") for i in range(19)]
    )
    tie = verdict.classify(tie_pool, horizon=REFERENCE)
    ok(tie["verdict"] == MIXED and not tie["race_test"]["material"],
       "a difference inside the declared deviate is MIXED, not the larger "
       "share rounded up to a finding")
    ok(tie["race_test"]["z_threshold"] == MATERIALITY_Z,
       "the deviate the verdict turns on is printed with the verdict")

    thin = verdict.classify(
        [_leg(mids=[100.0, 104.0] + [96.0] * 40, threshold=1.0,
              obs_id=f"s{i}") for i in range(MIN_CLASSIFIED_FOR_VERDICT - 1)],
        horizon=REFERENCE,
    )
    ok(thin["verdict"] == INSUFFICIENT,
       "below Phase 39's own label floor the verdict is INSUFFICIENT_DATA, "
       "not the direction the few legs happened to point")
    uncovered_pool = [
        _leg(mids=[100.0, 100.1], threshold=1.0, obs_id=f"c{i}")
        for i in range(80)
    ]
    unc = verdict.classify(uncovered_pool, horizon=REFERENCE)
    ok(unc["verdict"] == INSUFFICIENT,
       "when more windows are uncovered than classified, the store is what "
       "was measured, not the market")

    # A race decided before the quotes ran out is still a race — but its net at
    # that horizon is not a thirty-minute net, and must not be counted as one.
    early = [
        _leg(mids=[100.0, 104.0], threshold=1.0, obs_id=f"y{i}")
        for i in range(6)
    ]
    erow = metrics.horizon_row(early, horizon=REFERENCE)
    ok(erow["favourable_first_pct"] == 100.0,
       "an event that happened at minute two is first at every longer hold: "
       "nothing arriving later can precede it")
    ok(erow["truncated_economics"] == 6 and erow["priced_economics"] == 0
       and erow["net_pct_median"] is None,
       "but the money at that hold is unmeasured, not the two-minute figure "
       "printed under a thirty-minute heading")
    ok(metrics.giveback_row(early, horizon=REFERENCE)["favourable_first"] == 0
       and metrics.giveback_row(early, horizon=CLOSE)["favourable_first"] == 6,
       "the giveback table drops those legs too, and keeps them at the close, "
       "where the last quote is the answer by definition")
    for v in (ex, kp, en, tie, thin, unc):
        ok(v["verdict"] in VERDICTS, "every verdict is one of the declared four")
        ok(v["because"] and any(ch.isdigit() for ch in v["because"]),
           "every verdict shows the measurement it came from")

    ok(verdict.sign_test(0, 0)["z"] is None,
       "a sign test with nothing in it has no deviate rather than a zero one")
    ok(verdict.sign_test(10, 0)["z"] > verdict.sign_test(6, 4)["z"],
       "a one-sided split gives a larger deviate than a near-even one")

    # ------------------------------------------------ overlapping legs are not
    #                                                  independent observations
    dense = [
        _leg(mids=[100.0, 104.0] + [96.0] * 40, threshold=1.0,
             obs_id=f"d{i}", ts=T0 + i * MIN)
        for i in range(40)
    ]
    indep = firstevent.independent(dense, horizon=REFERENCE)
    ok(0 < len(indep) < len(dense),
       "the non-overlapping subsample is smaller than the pool, because "
       "overlapping windows describe the same price swing many times")
    gaps = sorted(x["ts"] for x in indep)
    ok(all((b - a) / 60.0 >= float(REFERENCE)
           for a, b in zip(gaps, gaps[1:], strict=False)),
       "the kept legs are at least one horizon apart")
    ok(firstevent.independent(dense, horizon=CLOSE).__len__() == 1,
       "at the session close one window covers the day, so the subsample is "
       "one leg per contract per session")
    ok("outcome can influence" in inspect.getsource(firstevent.independent),
       "the subsample is chosen on timestamps alone, so no outcome can "
       "influence who survives the cut")

    # ------------------------------------------------------- §11 same instant
    shared = "obs-shared"
    trio = [
        _leg(mids=[100.0, 104.0] + [96.0] * 40, threshold=1.0, vehicle=CE,
             obs_id=shared, symbol=CE_SYM),
        _leg(mids=[50.0, 46.0] + [46.0] * 40, threshold=1.0, vehicle=PE,
             obs_id=shared, symbol=PE_SYM),
        _leg(mids=[8700.0, 8750.0] + [8740.0] * 40, threshold=0.2,
             spread=2.0, vehicle=FUTURES, obs_id=shared, symbol=FUT_SYM),
    ]
    orphan = _leg(mids=[100.0, 104.0] + [96.0] * 40, threshold=1.0,
                  vehicle=CE, obs_id="obs-alone")
    vc = metrics.vehicle_comparison([*trio, orphan], horizon=REFERENCE)
    ok(vc["instants_with_all_vehicles"] == 1 and vc["instants_seen"] == 2,
       "the comparison uses instants where all three vehicles were quoted, "
       "not three separately-sampled populations")
    ok(set(vc["vehicles"]) == {CE, PE, FUTURES}
       and all(r["n"] == 1 for r in vc["vehicles"].values()),
       "each vehicle contributes the same instants as the others")
    ok("no vehicle is promoted" in report.render(_payload_stub()).lower(),
       "the vehicle table says no vehicle is promoted by it")

    # ------------------------------------------------------ end-to-end on store
    tmp = tempfile.mkdtemp(prefix="phase40-")
    db = os.path.join(tmp, "opportunity.db")
    con = store.connect(db)

    # CE: rises past its threshold, then collapses — favourable first, lost.
    _series(con, instrument="CRUDEOIL", vehicle=CE, symbol=CE_SYM,
            mids=[100.0 + 4.0 * min(i, 3) - 0.35 * max(0, i - 3)
                  for i in range(90)], spread=0.4, tag="ce")
    # PE: falls first — adverse first. Engine-selected, so §10 has two cohorts.
    _series(con, instrument="CRUDEOIL", vehicle=PE, symbol=PE_SYM,
            mids=[50.0 - 0.4 * i for i in range(90)], spread=0.4, tag="pe",
            source="ENGINE", engine_selected=True)
    # FUTURES on a short view: a falling price is favourable.
    _series(con, instrument="CRUDEOIL", vehicle=FUTURES, symbol=FUT_SYM,
            mids=[8700.0 - 6.0 * i for i in range(90)], spread=2.0,
            direction=SHORT, tag="fut")

    before = {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
        for t in store.RAW_TABLES
    }
    payload = service.run(con)
    after = {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
        for t in store.RAW_TABLES
    }
    ok(before == after, "the diagnostic writes nothing to the store it reads")

    ok(payload["legs"] > 0 and "CRUDEOIL" in payload["instruments"],
       "the run measures the store it was given")
    ok(list(payload["instruments"])[0] == "CRUDEOIL",
       "CRUDEOIL is answered first, as §9 requires")
    crude = payload["instruments"]["CRUDEOIL"]
    ok(set(crude["vehicles"]) == {CE, PE, FUTURES},
       "the three vehicles are measured separately and never combined")
    for vehicle, block in crude["vehicles"].items():
        holds = [r["horizon"] for r in block["horizons"]]
        ok(holds == list(firstevent.HORIZON_KEYS),
           f"{vehicle} is evaluated at every declared hold plus the close")
        ok(len(block["giveback"]) == len(holds) == len(block["adverse_first"]),
           f"{vehicle} carries a giveback and an adverse row per hold")
    ok(crude["vehicles"][CE]["verdict"]["favourable_first_pct"] is not None,
       "a rising-then-collapsing CE book is measured as favourable-first")
    ok(crude["vehicles"][PE]["verdict"]["adverse_first_pct"] is not None,
       "a falling PE book is measured as adverse-first")
    ok(crude["thresholds"][CE]["cost_clearing_move_pct_median"] > 0
       and crude["thresholds"][CE]["adverse_move_pct_median"] < 0
       and abs(crude["thresholds"][CE]["adverse_move_pct_median"])
       == crude["thresholds"][CE]["cost_clearing_move_pct_median"],
       "the reported thresholds are equal and opposite, as applied")

    named = {u["instrument"]: u for u in payload["unmeasured_instruments"]}
    ok("SENSEX" in named and named["SENSEX"]["reason"] == "NOT_IN_STORE"
       and named["SENSEX"]["verdict"] == INSUFFICIENT,
       "an instrument §9 named and the capture never held keeps a row saying "
       "so: absent is not the same finding as uneconomic")
    ok("CRUDEOIL" not in named,
       "an instrument that was measured is not also listed as unmeasured")
    ok("SENSEX" in report.render(payload),
       "the named-but-unmeasured rows reach the report, so a missing "
       "instrument cannot read as one examined and found wanting")

    cohorts = crude["cohorts"]
    split_ok = (cohorts[ENGINE]["n"] > 0 and cohorts[BOARD]["n"] > 0
                and cohorts["comparable"] is True)
    ok(split_ok,
       "engine-selected and board-only legs are split by the field the "
       "observation recorded, not inferred")
    if not split_ok:
        # A cohort the fixture built and the run did not see is a fact about
        # this environment, so print what it refused rather than leaving the
        # next assertion to fail on a missing key.
        print("  DIAGNOSIS engine n=%s board n=%s per-vehicle=%s refusals=%s"
              % (cohorts[ENGINE]["n"], cohorts[BOARD]["n"],
                 {k: b["n"] for k, b in crude["vehicles"].items()},
                 payload["coverage"]["unraceable_by_reason"]))
    ok(cohorts[ENGINE]["verdict"]["verdict"] in VERDICTS
       and cohorts[BOARD]["verdict"]["verdict"] in VERDICTS,
       "each cohort gets its own verdict rather than a shared one — an empty "
       "cohort carries INSUFFICIENT_DATA rather than no verdict at all")

    # The split must survive an upstream projection that stops carrying the
    # field: Phase 40 reads engine_selected from raw_observation itself, so a
    # store where the engine selected something can never read as a store
    # where it selected nothing.
    stripped = firstevent.legs(con)
    ok({leg["cohort"] for leg in stripped["legs"]} == {ENGINE, BOARD},
       "the cohorts are read from the observations for the instants raced, "
       "not from another phase's column list")
    ok(firstevent.selections(con, ["not-an-obs-id"]) == {},
       "an id the store does not hold yields nothing rather than a default "
       "cohort")

    empty_cohort = service._cohorts(dense)
    ok(empty_cohort[ENGINE]["status"] == UNMEASURED
       and empty_cohort["comparable"] is False,
       "a store with only one cohort reports the other UNMEASURED rather than "
       "answering the engine-versus-board question")

    ok(payload["primary"]["verdict"] in VERDICTS
       and payload["primary_non_overlapping"]["verdict"] in VERDICTS,
       "the primary verdict is computed twice — on all legs and on "
       "non-overlapping windows only")
    ok(len(payload["cross_instrument"]) == 1
       and payload["cross_instrument"][0]["instrument"] == "CRUDEOIL",
       "the cross-instrument table lists what the store held, and nothing it "
       "did not")

    # ---------------------------------------------------------- §15 artefacts
    paths = report.write(payload, root=tmp)
    md = open(paths["md"], encoding="utf-8").read()  # noqa: SIM115, PTH123
    body = json.loads(open(paths["json"], encoding="utf-8").read())  # noqa: SIM115, PTH123
    ok(paths["md"].endswith(MD_NAME) and paths["json"].endswith(JSON_NAME),
       "the two artefacts have the names the task asked for")
    ok(body["primary"]["verdict"] == payload["primary"]["verdict"],
       "the json is the payload, so it cannot disagree with the markdown")
    for section in ("Executive summary", "Cross-instrument comparison",
                    "CRUDEOIL", "ENGINE_SELECTED vs BOARD_ONLY",
                    "vehicle comparison", "giveback", "adverse-first",
                    "money flow", "What this cannot say"):
        ok(section.lower() in md.lower(), f"the report has its {section} section")
    for vehicle in (CE, PE, FUTURES):
        ok(f"CRUDEOIL {vehicle}" in md,
           f"the report has a {vehicle} section of its own")
    ok("FAVOURABLE MOVE" in md and "GIVEBACK" in md and "FINAL NET" in md
       and "ADVERSE MOVE" in md,
       "both money-flow waterfalls are printed")
    ok("NO EXIT, ENTRY, STOP, TARGET, SIZING OR ORDER PATH WAS TESTED OR "
       "CHANGED." in md,
       "the document states on its first screen that no mechanism was touched")
    for banned in ("VALIDATED", "PRODUCTION_READY", "EDGE FOUND",
                   "RECOMMEND BUYING", "GO LIVE"):
        ok(banned not in md.upper(), f"the report cannot express {banned}")
    ok("not evidence that any strategy is profitable" in md.lower(),
       "the only appearance of profit in the document is its disclaimer")
    ok("no vehicle is promoted" in md.lower()
       and "promoted" not in md.lower().replace("no vehicle is promoted", ""),
       "the only appearance of promotion in the document is its denial")
    # Every markdown table is rectangular: a header, a rule of the same width,
    # and body rows of that width. A ragged table is how a pipe in a value
    # silently shifts a column.
    ragged = 0
    width: int | None = None
    for line in md.splitlines():
        if not line.startswith("|"):
            width = None
            continue
        cells = line.count("|")
        if width is None:
            width = cells
        elif cells != width:
            ragged += 1
    ok(ragged == 0, "every markdown table is rectangular")

    ok("min" in md and "%" in md, "times are in minutes and moves in percent")

    # ------------------------------------------------------------- §16 answer
    text = cli.answer(payload)
    lines = text.splitlines()
    ok(lines[0] == "PRIMARY PROBLEM:" and lines[1] in VERDICTS,
       "the answer opens with the verdict, in the requested format")
    ok("EVIDENCE:" in lines and "NEXT ACTION:" in lines,
       "the answer carries its three headings in order")
    ok(lines.index("EVIDENCE:") < lines.index("NEXT ACTION:"),
       "the evidence precedes the action")
    ok(any(ch.isdigit() for ch in text.split("EVIDENCE:")[1]),
       "the evidence section contains measurements")
    ok(len(text.split("NEXT ACTION:")[1].strip()) > 0,
       "there is exactly one next action and it is not empty")
    for label, expect in (
        (EXIT_DOMINANT, "exit study"),
        (ENTRY_DOMINANT, "entry timing"),
        (INSUFFICIENT, "keep capturing"),
        (MIXED, "more sessions"),
    ):
        stub = {"primary": {**payload["primary"], "verdict": label}}
        ok(expect in cli.recommendation(stub).lower(),
           f"a {label} verdict recommends {expect} and not a change of "
           f"mechanism")
    ok("holdout" in cli.recommendation(
        {"primary": {**payload["primary"], "verdict": EXIT_DOMINANT}},
    ).lower(),
       "even the exit recommendation asks for a study with a holdout, not a "
       "switch")

    summ = cli.summary(payload)
    ok(summ["coverage"] == payload["coverage"]
       and summ["not_a_strategy"] == payload["not_a_strategy"],
       "the CLI summary carries the coverage and the caveat")

    # -------------------------------------------------------------- read-only
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
           "the whole diagnostic runs with SQLite refusing every write, so it "
           "is read-only by execution and not merely by inspection")
    finally:
        con.set_authorizer(None)

    for module in (firstevent, metrics, verdict, service, report, cli):
        source_text = inspect.getsource(module)
        for banned in ("save_quotes", "save_observations", "place_order",
                       "order_path", "resolve_all"):
            ok(banned not in source_text,
               f"{module.__name__} does not call {banned}")
    ok("--no-artefacts" in inspect.getsource(cli),
       "the CLI can print without writing, for a read-only inspection")
    ok("--instrument" in inspect.getsource(cli),
       "the instrument flag is an optional filter, not a scope")

    # Phase 35/36/39 are untouched: this phase only reads their functions.
    for module in (firstevent, metrics, verdict, service):
        text_src = inspect.getsource(module)
        ok("phase36" not in text_src or "import" in text_src,
           f"{module.__name__} reuses phase modules rather than editing them")

    con.close()
    print(f"\nPHASE 40 SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


def _payload_stub() -> dict:
    """A minimal payload, so the report can be rendered without a store."""
    legs = [
        _leg(mids=[100.0, 104.0] + [96.0] * 40, threshold=1.0,
             obs_id=f"p{i}") for i in range(2)
    ]
    return {
        "phase": "PHASE40_FAVOURABLE_ADVERSE_FIRST_EVENT",
        "version": "40.0",
        "mode": [], "not_a_strategy": "", "instrument_filter": None,
        "reference_horizon": REFERENCE,
        "horizons": list(firstevent.HORIZON_KEYS),
        "sessions": ["2026-09-08"], "legs": len(legs), "coverage": {},
        "instruments": {
            "CRUDEOIL": {
                "n": len(legs), "sessions": ["2026-09-08"],
                "thresholds": service._thresholds(legs),
                "vehicles": {CE: service._vehicle_block(legs)},
                "cohorts": service._cohorts(legs),
                "vehicle_comparison": metrics.vehicle_comparison(
                    legs, horizon=REFERENCE,
                ),
                "verdict": verdict.classify(legs, horizon=REFERENCE),
            },
        },
        "primary": verdict.classify(legs, horizon=REFERENCE),
        "primary_non_overlapping": verdict.classify(legs, horizon=REFERENCE),
        "cross_instrument": [],
        "unmeasured_instruments": [{
            "instrument": "SENSEX", "status": UNMEASURED,
            "reason": "NOT_IN_STORE", "verdict": INSUFFICIENT,
        }],
    }


if __name__ == "__main__":
    raise SystemExit(main())
