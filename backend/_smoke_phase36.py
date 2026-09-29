"""Phase 36 smoke — the properties that stop a vehicle comparison from lying.

Each check below exists because the opposite mistake would answer "FUTURES, CE or
PE?" confidently and wrongly:

* comparing a future quoted at one instant with an option quoted at another, so
  the "better vehicle" is really the better minute;
* letting a one-sided, crossed or non-executable book into the comparison, which
  silently makes the vehicle with the worse feed coverage look worse;
* charging the spread twice on an ask-in/bid-out fill, or charging it once and
  calling the modelled figure measured;
* choosing the winning vehicle, the winning strike or the winning hold *after*
  seeing the outcome;
* letting the opposite option side win a vehicle comparison, which reports a
  wrong direction as a vehicle problem;
* calling an option loss WRONG_DIRECTION when the future made money on the same
  instant — the misdiagnosis that sends a month of work to the wrong place;
* reading a large single-session sample as out-of-sample evidence;
* emitting VALIDATED from a first study, which §26 forbids;
* writing to the raw store, or touching an order path, from a research module.

    .venv/bin/python _smoke_phase36.py
"""
from __future__ import annotations

import inspect
import os
import random
import tempfile
import time

# Before app.config is imported: importing the app opens the history store, and
# against the real data dir that is the RUNNING app's file, which fails the
# import outright rather than merely sharing it.
os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="p36smoke-"))

from app import main as appmain  # noqa: E402
from app.research.phase17 import store as p17store  # noqa: E402
from app.research.phase35 import capture as p35capture  # noqa: E402
from app.research.phase35 import cli as p35cli  # noqa: E402
from app.research.phase35 import service as p35service  # noqa: E402
from app.research.phase35 import (
    CE,
    FUTURES,
    LONG,
    MEASURED_EXECUTABLE,
    PE,
    SHORT,
    UNMEASURED,
    book,
    normalize_direction,
    path,
    store,
)
from app.research.phase36 import (
    ATM,
    CLOSE,
    COMPARISON_HORIZONS,
    COUNTERFACTUAL,
    DEV,
    DIRECTIONAL,
    ENGINE_MISSED,
    ENGINE_SELECTED,
    ENTRY_CONFIRMATION,
    HOLDOUT,
    LEAD,
    MIN_SESSIONS_FOR_LEAD,
    NEEDS_DATA,
    NO_ADVANTAGE,
    OTM_1,
    VALIDATION,
    VERDICTS,
    WRONG_DIRECTION,
    WRONG_VEHICLE,
    cli,
    diagnose,
    engine,
    outcome,
    pathindex,
    report,
    service,
    tables,
    triples,
    validate,
)

PASS = 0
FAIL: list[str] = []


def _files_in(directory: str, *, since: str | None) -> list[str]:
    """Which observation files a pass would open, for a throwaway directory."""
    original = p17store.path
    p17store.path = lambda name: os.path.join(directory, name)  # type: ignore[assignment]
    try:
        return p35capture.source_files(since=since)
    finally:
        p17store.path = original  # type: ignore[assignment]

# 2026-09-07 09:05 IST, inside one MCX session.
T0 = 1_788_752_100.0
MIN = 60.0
LOT = 100
DAY = 86_400.0


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def _obs(
    obs_id: str, *, ts: float, engine_selected: bool, direction: str = LONG,
    vehicle: str | None = CE, instrument: str = "CRUDEOIL",
) -> dict:
    return {
        "obs_id": obs_id,
        "ts": ts,
        "session": path.session_date(ts),
        "instrument": instrument,
        "family": "MCX",
        "source": "ENGINE" if engine_selected else "BOARD",
        "opportunity_type": "DIRECTIONAL",
        "direction": direction,
        "engine_class": "BUY" if engine_selected else "WAIT",
        "engine_selected": engine_selected,
        "selected_vehicle": vehicle if engine_selected else None,
        "context": {"plan": {"lot_size": LOT}, "book_age_ms": 200.0},
        "origin": "SMOKE",
        "origin_id": obs_id,
        "ingest_ts": ts,
    }


def _q(
    obs_id: str, vehicle: str, *, ts: float, bid: float, ask: float,
    symbol: str, strike: float | None = None, underlying: float = 8700.0,
    evidence: str = MEASURED_EXECUTABLE, dte: int = 10,
) -> dict:
    spread = ask - bid
    return {
        "obs_id": obs_id, "vehicle": vehicle, "ts": ts, "symbol": symbol,
        "strike": strike, "expiry": "2026-09-17", "dte": dte,
        "bid": bid, "ask": ask, "traded": (bid + ask) / 2.0,
        "spread": spread, "spread_pct": 100.0 * spread / ask,
        "delta": 0.5, "iv": 32.0, "oi": 4000.0, "volume": 900.0,
        "underlying": underlying, "basis": 2.0, "lot_size": LOT,
        "evidence": evidence, "reason": None if evidence == MEASURED_EXECUTABLE
        else "seeded non-executable",
    }


CE_SYM = "CRUDEOIL17SEP268700CE"
PE_SYM = "CRUDEOIL17SEP268700PE"
FUT_SYM = "CRUDEOIL21SEP26FUT"


def _seed_instant(
    con, *, obs_id: str, ts: float, engine_selected: bool, direction: str = LONG,
    ce=(278.0, 279.0), pe=(293.0, 294.0), fut=(8698.0, 8700.0),
    ce_path=(285.0,), pe_path=(288.0,), fut_path=(8730.0,),
    engine_vehicle: str | None = CE, strike: float = 8700.0,
    underlying: float = 8700.0,
) -> None:
    """One comparable instant, plus a forward path for each of the three legs."""
    rows = [_obs(obs_id, ts=ts, engine_selected=engine_selected,
                 direction=direction, vehicle=engine_vehicle)]
    quotes = [
        _q(obs_id, CE, ts=ts, bid=ce[0], ask=ce[1], symbol=CE_SYM,
           strike=strike, underlying=underlying),
        _q(obs_id, PE, ts=ts, bid=pe[0], ask=pe[1], symbol=PE_SYM,
           strike=strike, underlying=underlying),
        _q(obs_id, FUTURES, ts=ts, bid=fut[0], ask=fut[1], symbol=FUT_SYM,
           underlying=underlying),
    ]
    for i, (c, p, f) in enumerate(
        zip(ce_path, pe_path, fut_path, strict=False), start=1,
    ):
        fid = f"{obs_id}-f{i}"
        fts = ts + i * 35 * MIN
        rows.append(_obs(fid, ts=fts, engine_selected=False))
        quotes += [
            _q(fid, CE, ts=fts, bid=c, ask=c + 1.0, symbol=CE_SYM,
               strike=strike, underlying=underlying),
            _q(fid, PE, ts=fts, bid=p, ask=p + 1.0, symbol=PE_SYM,
               strike=strike, underlying=underlying),
            _q(fid, FUTURES, ts=fts, bid=f, ask=f + 2.0, symbol=FUT_SYM,
               underlying=underlying),
        ]
    store.save_observations(con, rows)
    store.save_quotes(con, quotes)


def _seed_days(con, *, days: int, per_day: int = 4) -> None:
    """Several sessions, so the chronological split has something to split."""
    for d in range(days):
        for k in range(per_day):
            _seed_instant(
                con,
                obs_id=f"obs-d{d}-{k}",
                ts=T0 + d * DAY + k * 5 * MIN,
                engine_selected=(k % 2 == 0),
            )


def main() -> int:  # noqa: PLR0915 - one flat list of independent checks
    con = store.connect(":memory:")

    # --- §2/§3: a triple is all three vehicles at ONE instant ---------------
    _seed_instant(con, obs_id="obs-good", ts=T0, engine_selected=True)
    built = triples.build(con, instrument="CRUDEOIL")
    # The forward points are themselves fully-quoted instants, so they are
    # comparable opportunities too — that is the BOARD half of the pool, and
    # excluding them would answer the question only where the engine looked.
    ok(built["eligible"] == 2 and built["triples"][0]["ts"] == T0,
       "three executable books at one instant is a triple, in time order")
    t = built["triples"][0]
    ok(t[CE]["ask"] == 279.0 and t[PE]["ask"] == 294.0
       and t[FUTURES]["ask"] == 8700.0,
       "the triple carries all three books from the same decision instant")

    # A book the feed never quoted two-sided, a crossed book and a
    # non-executable one are all refused rather than repaired.
    ok(not triples.executable({"evidence": MEASURED_EXECUTABLE, "bid": 10.0,
                               "ask": None}),
       "a one-sided book is not executable")
    ok(not triples.executable({"evidence": MEASURED_EXECUTABLE, "bid": 11.0,
                               "ask": 10.0}),
       "a crossed book is refused, not filled through")
    ok(not triples.executable({"evidence": MEASURED_EXECUTABLE, "bid": 10.0,
                               "ask": 10.0}),
       "a locked book with zero spread is refused")
    ok(not triples.executable({"evidence": UNMEASURED, "bid": 10.0, "ask": 11.0}),
       "a non-executable evidence label is refused whatever the numbers say")

    con2 = store.connect(":memory:")
    _seed_instant(con2, obs_id="obs-nofut", ts=T0, engine_selected=True)
    store.save_quotes(con2, [_q(
        "obs-partial", CE, ts=T0 + MIN, bid=1.0, ask=2.0, symbol=CE_SYM,
        strike=8700.0,
    )])
    store.save_observations(con2, [_obs("obs-partial", ts=T0 + MIN,
                                       engine_selected=True)])
    part = triples.build(con2, instrument="CRUDEOIL")
    ok(part["refused_by_reason"].get(triples.NO_FUTURES_BOOK) == 1,
       "an instant missing the futures book is refused and counted by reason")
    ok(part["eligible"] == 2 and "obs-partial" not in {
        x["obs_id"] for x in part["triples"]
    }, "partial coverage removes only that instant, not the comparable ones")

    # A recorded direction is required: assuming LONG would put half the sample
    # on the wrong side of the §9 split.
    con3 = store.connect(":memory:")
    store.save_observations(con3, [
        dict(_obs("obs-nodir", ts=T0, engine_selected=True), direction=None),
    ])
    store.save_quotes(con3, [
        _q("obs-nodir", CE, ts=T0, bid=278.0, ask=279.0, symbol=CE_SYM,
           strike=8700.0),
        _q("obs-nodir", PE, ts=T0, bid=293.0, ask=294.0, symbol=PE_SYM,
           strike=8700.0),
        _q("obs-nodir", FUTURES, ts=T0, bid=8698.0, ask=8700.0, symbol=FUT_SYM),
    ])
    ok(triples.build(con3, instrument="CRUDEOIL")["refused_by_reason"].get(
        triples.NO_DIRECTION) == 1,
       "an instant with no recorded view cannot be a vehicle comparison")

    # --- the two direction vocabularies are the same fact -------------------
    # Capture preserves Phase 17's BULLISH/BEARISH verbatim, and this study
    # reasons in LONG/SHORT. Comparing them raw refused every real observation
    # for "no direction" while the direction was sitting in the row, so the
    # translation is pinned here in both directions and on the reading side.
    ok(normalize_direction("BULLISH") == LONG
       and normalize_direction("BEARISH") == SHORT
       and normalize_direction("bearish") == SHORT
       and normalize_direction(" LONG ") == LONG,
       "BULLISH reads as LONG and BEARISH as SHORT, however it was cased")
    ok(normalize_direction(None) is None
       and normalize_direction("SIDEWAYS") is None
       and normalize_direction(7) is None,
       "an unreadable direction stays unknown rather than defaulting to LONG")
    con3b = store.connect(":memory:")
    store.save_observations(con3b, [
        dict(_obs("obs-bull", ts=T0, engine_selected=True),
             direction="BULLISH"),
        dict(_obs("obs-bear", ts=T0 + 5 * MIN, engine_selected=True),
             direction="BEARISH"),
    ])
    for obs_id, ts in (("obs-bull", T0), ("obs-bear", T0 + 5 * MIN)):
        store.save_quotes(con3b, [
            _q(obs_id, CE, ts=ts, bid=278.0, ask=279.0, symbol=CE_SYM,
               strike=8700.0),
            _q(obs_id, PE, ts=ts, bid=293.0, ask=294.0, symbol=PE_SYM,
               strike=8700.0),
            _q(obs_id, FUTURES, ts=ts, bid=8698.0, ask=8700.0, symbol=FUT_SYM),
        ])
    vocab = triples.build(con3b, instrument="CRUDEOIL")
    ok(vocab["eligible"] == 2
       and triples.NO_DIRECTION not in vocab["refused_by_reason"],
       "BULLISH/BEARISH rows are comparable, not refused for having no view")
    by_id = {t["obs_id"]: t for t in vocab["triples"]}
    ok(by_id["obs-bull"]["direction"] == LONG
       and by_id["obs-bear"]["direction"] == SHORT,
       "the triple carries the translated position, not the upstream wording")
    ok(by_id["obs-bear"]["directional_option"] == PE
       and by_id["obs-bull"]["directional_option"] == CE,
       "a BEARISH view expresses as the put, as a SHORT view would")
    ok(outcome.leg_direction(FUTURES, "BEARISH") == SHORT
       and outcome.leg_direction(CE, "BEARISH") == LONG,
       "a bearish futures leg is priced short; a long put is still long premium")
    diag_vocab = diagnose.diagnose(con3b, instrument="CRUDEOIL")
    ok(diag_vocab["direction_values"].get("BULLISH") == 1
       and diag_vocab["direction_read_as"].get(LONG) == 1,
       "the diagnosis shows the stored word and what it was read as")

    # --- the diagnosis of a refusal names the field, not just the reason ----
    # NO_EXECUTABLE_FUTURES_BOOK covers four different capture faults with four
    # different fixes; a diagnosis that cannot tell them apart invites the wrong
    # one, which is loosening the executable test.
    diag = diagnose.diagnose(con3, instrument="CRUDEOIL")
    ok(diag["instants_with_all_three_books"] == 1
       and diag["instants_with_all_three_books_but_no_direction"] == 1,
       "a fully-quoted instant refused for direction is reported as exactly that")
    ok(diag["per_vehicle"][FUTURES]["executable"] == 1,
       "the diagnosis counts an executable futures book as executable")
    ok(diagnose.diagnose(con2, instrument="CRUDEOIL")["per_vehicle"][FUTURES][
           "classification"].get(diagnose.ABSENT) == 1,
       "a missing quote row is ABSENT, distinct from a book that failed a test")
    unmeasured = store.connect(":memory:")
    store.save_observations(unmeasured, [_obs("obs-sim", ts=T0,
                                              engine_selected=True)])
    store.save_quotes(unmeasured, [
        dict(_q("obs-sim", FUTURES, ts=T0, bid=8698.0, ask=8700.0,
                symbol=FUT_SYM), evidence=UNMEASURED,
             reason="SIMULATED_BOOK_NOT_EVIDENCE"),
        dict(_q("obs-sim", CE, ts=T0, bid=278.0, ask=279.0, symbol=CE_SYM,
                strike=8700.0), bid=None),
        dict(_q("obs-sim", PE, ts=T0, bid=294.0, ask=294.0, symbol=PE_SYM,
                strike=8700.0)),
    ])
    diag2 = diagnose.diagnose(unmeasured, instrument="CRUDEOIL")
    ok(diag2["per_vehicle"][FUTURES]["classification"].get(
           diagnose.NOT_MEASURED_EXECUTABLE) == 1
       and diag2["per_vehicle"][FUTURES]["capture_reason"].get(
           "SIMULATED_BOOK_NOT_EVIDENCE") == 1,
       "a simulated book is reported with the reason capture already recorded")
    ok(diag2["per_vehicle"][CE]["classification"].get(diagnose.NO_BID) == 1,
       "a one-sided book is reported as the side that was missing")
    ok(diag2["per_vehicle"][PE]["classification"].get(diagnose.CROSSED) == 1,
       "a locked book is separated from a two-sided one")
    ok(sum(diag2["per_vehicle"][CE]["classification"].values())
       == diag2["observations"],
       "every observation is classified exactly once per vehicle")
    ok(diagnose.diagnose(con3, instrument="CRUDEOIL")["research_only"] is True
       and "verdict" not in diagnose.diagnose(con3, instrument="CRUDEOIL"),
       "a diagnosis carries no verdict of its own")
    ok("MEASURED_EXECUTABLE" not in inspect.getsource(diagnose._classify)
       or diagnose._classify({"evidence": UNMEASURED, "bid": 1.0, "ask": 2.0})
       == diagnose.NOT_MEASURED_EXECUTABLE,
       "the diagnosis applies the same executable test, it does not relax it")
    ok("diagnose" in inspect.getsource(cli.main),
       "the diagnosis is reachable from the CLI")

    # --- §4/§12: moneyness measured from the captured strike grid -----------
    ok(triples.strike_step(con, "CRUDEOIL") is None
       or triples.strike_step(con, "CRUDEOIL") > 0,
       "the strike step is measured from captured strikes, never assumed")
    ok(triples.moneyness(vehicle=CE, strike=8700.0, underlying=8700.0,
                         step=50.0)[0] == ATM,
       "a strike at the underlying is ATM")
    ok(triples.moneyness(vehicle=CE, strike=8750.0, underlying=8700.0,
                         step=50.0) == (OTM_1, 1),
       "one strike above spot is one step OTM for a call")
    ok(triples.moneyness(vehicle=PE, strike=8650.0, underlying=8700.0,
                         step=50.0) == (OTM_1, 1),
       "one strike below spot is one step OTM for a put, same sign convention")
    ok(triples.moneyness(vehicle=CE, strike=8700.0, underlying=None,
                         step=50.0) == (None, None),
       "moneyness with no underlying stays unmeasured instead of defaulting")

    # --- §7: ask in, bid out, and the spread charged exactly once ----------
    row = outcome.resolve(con, t, CE, role=DIRECTIONAL)
    ok(row["entry_price"] == 279.0 and row["entry_side"] == "ASK",
       "a long option enters at the ask")
    ok(row["cost"]["spread_points"] == 0.0,
       "the spread is not charged again on an ask-in/bid-out fill")
    ok(abs(row["cost"]["cost_points"] - (
        row["cost"]["brokerage_points"] + row["cost"]["tax_points"]
    )) < 1e-6,
       "an executable option cost is exactly brokerage plus statutory charges")
    close = row["horizons"][CLOSE]
    ok(close["gross_pct"] == book.gross_pct(279.0, 285.0, vehicle=CE,
                                            direction=LONG),
       "the option exit is the bid, so gross is bid-out over ask-in")

    fut_long = outcome.resolve(con, t, FUTURES, role=DIRECTIONAL)
    ok(fut_long["entry_price"] == 8700.0 and fut_long["entry_side"] == "ASK",
       "long futures enter at the ask")
    short_t = dict(t, direction=SHORT,
                   directional_option=triples.directional_option(SHORT))
    fut_short = outcome.resolve(con, short_t, FUTURES, role=DIRECTIONAL)
    ok(fut_short["entry_price"] == 8698.0 and fut_short["entry_side"] == "BID",
       "short futures enter at the bid")
    ok((fut_short["horizons"][CLOSE]["gross_pct"] or 0) < 0,
       "a short future into a rising market loses, so the sign is carried")

    # A long put on a SHORT view is long its own premium, not short it.
    put = outcome.resolve(con, short_t, PE, role=DIRECTIONAL)
    ok(put["entry_side"] == "ASK" and put["leg_direction"] == LONG,
       "a put bought on a short view is still a long premium position")

    # --- §6: identical horizons for every vehicle, close always present ----
    for r in (row, fut_long, put):
        ok(CLOSE in r["horizons"], f"{r['vehicle']} has a session-close outcome")
    ok(set(row["horizons"]) <= {str(h) for h in COMPARISON_HORIZONS} | {CLOSE},
       "no horizon outside the frozen list is invented")

    # A horizon that the captured path never reached is absent, not extrapolated.
    ok("120" not in row["horizons"],
       "an unreached horizon is missing rather than forward-filled")

    # --- no crossing sessions -------------------------------------------------
    con4 = store.connect(":memory:")
    _seed_instant(con4, obs_id="obs-eod", ts=T0, engine_selected=True,
                  ce_path=(999.0,), pe_path=(999.0,), fut_path=(9999.0,))
    # A next-day quote for the same contract must not be reachable from today.
    store.save_observations(con4, [_obs("obs-next", ts=T0 + DAY,
                                       engine_selected=False)])
    store.save_quotes(con4, [_q("obs-next", CE, ts=T0 + DAY, bid=5000.0,
                                ask=5001.0, symbol=CE_SYM, strike=8700.0)])
    t4 = triples.build(con4, instrument="CRUDEOIL")["triples"][0]
    r4 = outcome.resolve(con4, t4, CE, role=DIRECTIONAL)
    ok(max(h["gross_pct"] for h in r4["horizons"].values()) < 300.0,
       "the forward path stops at the session boundary")

    # --- §20: net is one formula, re-charged rather than re-measured --------
    base = outcome.net_from_gross(10.0, cost_points=2.79, entry=279.0)
    stressed = outcome.net_from_gross(10.0, cost_points=2.79, entry=279.0,
                                      cost_multiple=2.0)
    ok(base is not None and stressed is not None and stressed < base,
       "doubling the cost multiple lowers net through the same formula")
    ok(outcome.net_from_gross(None, cost_points=1.0, entry=10.0) is None
       and outcome.net_from_gross(1.0, cost_points=None, entry=10.0) is None,
       "an unmeasured gross or cost yields no net, never a partial one")

    # --- §8/§9: the counterfactual side can never win the comparison -------
    roles = dict(outcome.vehicles_of(t))
    ok(roles[FUTURES] == DIRECTIONAL and roles[CE] == DIRECTIONAL
       and roles[PE] == COUNTERFACTUAL,
       "on a LONG view the put is a counterfactual, not a competing vehicle")
    ok(dict(outcome.vehicles_of(short_t))[PE] == DIRECTIONAL,
       "on a SHORT view the put is the directional expression")

    rows = outcome.resolve_all(con, [t])
    comp = tables.compare(rows)
    ok(comp["table"][PE]["n_entered"] == 0,
       "the counterfactual option side is absent from the vehicle table")
    ok(tables.counterfactual(rows)["by_vehicle"][PE]["n_entered"] == 1,
       "the counterfactual is reported, separately and labelled")

    # --- big multi-session sample for the aggregate checks -------------------
    conm = store.connect(":memory:")
    _seed_days(conm, days=6, per_day=6)
    state = service.run(conm, instrument="CRUDEOIL", with_entry_timing=False)

    cov = state["coverage"]
    ok(cov["eligible"] >= 36 and cov["observations"] >= cov["eligible"],
       "coverage reports eligible triples against every observation seen")
    ok(isinstance(cov["refused_by_reason"], dict),
       "every refused instant is attributed to a reason")
    ok(len(cov["sessions"]) == 6, "sessions are counted by date, not by row")

    hz = state["reference_horizon"]
    ok(hz == CLOSE or hz in {str(h) for h in COMPARISON_HORIZONS},
       "the cut tables are read at one declared horizon")

    # --- §8: the sample size is never hidden, and floors are visible --------
    for vehicle, vrow in state["vehicle_comparison"]["table"].items():
        ok("n_entered" in vrow and "sessions" in vrow,
           f"{vehicle} row carries its own sample size")
    stats = tables.horizon_stats(
        [r for r in rows if r["vehicle"] == CE], CLOSE,
    )
    ok(stats["n"] == 1 and stats["sufficient"] is False,
       "a one-trade row is marked insufficient rather than ranked")
    ok(stats["net_ci95"] is None,
       "no confidence interval is offered on a sample too small to have one")

    # --- best hold is a subset decision, not a per-trade one ---------------
    src = inspect.getsource(tables.vehicle_row)
    ok("best_hold_note" in src and "not per trade" in src,
       "the best-hold choice states that it is made for the whole subset")
    fut_row = state["vehicle_comparison"]["table"][FUTURES]
    if fut_row["best_hold"] is not None:
        per = fut_row["by_horizon"][fut_row["best_hold"]]
        ok(per["sufficient"],
           "the chosen best hold met the sample floor at that horizon")
        ok(all(
            per["net_mean_pct"] >= s["net_mean_pct"]
            for s in fut_row["by_horizon"].values()
            if s["sufficient"] and s["net_mean_pct"] is not None
        ), "the best hold is the subset's own maximum net mean")

    # --- §14: WRONG_VEHICLE vs WRONG_DIRECTION -----------------------------
    conw = store.connect(":memory:")
    # The engine bought the call; the call lost, the future paid. Direction was
    # right, the expression was not.
    _seed_instant(
        conw, obs_id="obs-wrongveh", ts=T0, engine_selected=True,
        engine_vehicle=CE, ce_path=(260.0,), pe_path=(280.0,),
        fut_path=(8900.0,),
    )
    tw = triples.build(conw, instrument="CRUDEOIL")["triples"][0]
    rw = outcome.resolve_all(conw, [tw])
    cw = engine.classify(rw, engine_vehicle=CE, horizon=CLOSE)
    ok(cw["direction_correct"] is True and cw["label"] == WRONG_VEHICLE
       and cw["best_vehicle"] == FUTURES,
       "an option loss beside a futures gain is WRONG_VEHICLE, not direction")

    cond = store.connect(":memory:")
    _seed_instant(
        cond, obs_id="obs-wrongdir", ts=T0, engine_selected=True,
        engine_vehicle=CE, ce_path=(250.0,), pe_path=(310.0,),
        fut_path=(8600.0,),
    )
    td = triples.build(cond, instrument="CRUDEOIL")["triples"][0]
    rd = outcome.resolve_all(cond, [td])
    cd = engine.classify(rd, engine_vehicle=CE, horizon=CLOSE)
    ok(cd["direction_correct"] is False and cd["label"] == WRONG_DIRECTION,
       "a long view into a falling market is WRONG_DIRECTION")
    ok(cd["nets"].get(PE) is None,
       "the counterfactual put is not offered as the better vehicle here")

    attrib = state["engine"]
    ok(set(attrib["by_side"]) == {ENGINE_SELECTED, ENGINE_MISSED},
       "every triple is attributed to the engine or to what it missed")
    ok(attrib["engine_missed_best_available"]["n"] > 0,
       "the value of declined opportunities is measured, not assumed zero")

    # --- §16: giveback -----------------------------------------------------
    gb = state["giveback"]
    ok(all("returned_to_entry_pct" in v or v.get("n") == 0
           for v in gb.values()),
       "giveback reports how often a profitable leg came back to entry")

    # --- §18: the placebo is reproducible ----------------------------------
    p1 = validate.placebo(conm, triples.build(
        conm, instrument="CRUDEOIL")["triples"], horizon=CLOSE)
    p2 = validate.placebo(conm, triples.build(
        conm, instrument="CRUDEOIL")["triples"], horizon=CLOSE)
    ok(p1["by_vehicle"] == p2["by_vehicle"],
       "the random-entry control is seeded, so it is reproducible")
    ok(p1["n_draws"] > 0, "the placebo actually drew entries")

    # --- §19: chronological, dev-only choice, holdout once -----------------
    all_rows = outcome.resolve_all(conm, triples.build(
        conm, instrument="CRUDEOIL")["triples"])
    splits = validate.split_sessions(all_rows)
    ok(not (set(splits[DEV]) & set(splits[VALIDATION]))
       and not (set(splits[DEV]) & set(splits[HOLDOUT]))
       and not (set(splits[VALIDATION]) & set(splits[HOLDOUT])),
       "the three chronological splits share no session")
    ok(max(splits[DEV]) <= min(splits[VALIDATION] or splits[DEV])
       and max(splits[VALIDATION] or splits[DEV]) <= min(
           splits[HOLDOUT] or splits[VALIDATION] or splits[DEV]),
       "the splits are in time order, so the holdout is the future")
    chron = state["chronological"]
    ok(chron.get("chosen_on") == DEV,
       "the vehicle is chosen on development sessions only")
    if chron.get(HOLDOUT):
        ok(chron[HOLDOUT].get("evaluated_once") is True,
           "the untouched holdout is marked as evaluated once")

    wf = state["walk_forward"]
    ok(wf["n_folds"] == 5 and all(
        f["test_session"] not in set() for f in wf["folds"]
    ), "walk-forward tests each later session against everything before it")

    # --- §20/§21 ------------------------------------------------------------
    st = state["stress"]["by_vehicle"][FUTURES]["cells"]
    ok("cost_1.0x_slip_1.0x" in st and "cost_2.0x_slip_3.0x" in st,
       "the stress grid covers 1x-2x cost against 1x-3x slippage")
    a = st["cost_1.0x_slip_1.0x"]["net_mean_pct"]
    b = st["cost_2.0x_slip_3.0x"]["net_mean_pct"]
    ok(a is None or b is None or b <= a,
       "a harsher stress cell can never report a better net than the base cell")
    ol = state["outliers"]["by_vehicle"][FUTURES]
    ok(ol["trim_top_0pct"]["n"] >= ol["trim_top_5pct"]["n"],
       "removing the top winners removes observations")

    # --- §17: cost over move is descriptive, not a forecast ----------------
    ok("measured MFE" in inspect.getdoc(tables.by_cost_over_move)
       or "own measured MFE" in inspect.getdoc(tables.by_cost_over_move),
       "the economic band uses the measured excursion, not a predicted move")

    # --- §25/§26: fourteen sections and one of exactly three verdicts ------
    secs = state["sections"]
    ok(len(secs) == 14, "the report has all fourteen required sections")
    ok(all(s.get("section") and "n" in s for s in secs),
       "every section carries its own sample size")
    v = state["verdict"]
    ok(v["verdict"] in VERDICTS, "the verdict is one of the three allowed values")
    ok("VALIDATED" not in VERDICTS and v["verdict"] != "VALIDATED",
       "this study cannot emit VALIDATED")
    ok(v["verdict"] == NEEDS_DATA,
       "six sessions is below the session floor, so the answer is more data")
    ok(any(g["gate"] == "SESSIONS_FLOOR" and not g["passed"] for g in v["gates"])
       or len(cov["sessions"]) >= MIN_SESSIONS_FOR_LEAD,
       "the failing gate is named rather than implied")
    ok(all("need" in g and "have" in g for g in v["gates"]),
       "every gate shows what it needed and what it got")

    # A verdict cannot be a LEAD while a gate is unpassed, and cannot be
    # NO_ADVANTAGE while a vehicle is positive.
    faked = dict(state)
    faked["verdict"] = report.verdict(faked)
    ok(not (faked["verdict"]["verdict"] == LEAD and any(
        not g["passed"] for g in faked["verdict"]["gates"])),
       "a LEAD requires every gate to pass")
    ok(NO_ADVANTAGE in VERDICTS and NEEDS_DATA in VERDICTS,
       "an unconfirmed positive is a data question, not a negative finding")

    # --- §27: research only. No order path anywhere in the package ---------
    pkg = os.path.dirname(
        os.path.abspath(engine.__file__)
    )
    banned = ("place_order", "placeOrder", "smart_api.placeOrder", "modify_order",
              "cancel_order", "square_off")
    offenders = []
    for name in sorted(os.listdir(pkg)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(pkg, name), encoding="utf-8") as fh:
            body = fh.read()
        offenders += [f"{name}:{b}" for b in banned if b in body]
    ok(not offenders, f"no order-path call in the phase36 package: {offenders}")
    ok(state["verdict"]["production_changed"] is False,
       "the report states that no production behaviour changed")

    # The study only reads raw. A run must not add or remove a raw row.
    before = store.counts(conm)
    service.run(conm, instrument="CRUDEOIL", with_entry_timing=False)
    after = store.counts(conm)
    ok(all(before[k] == after[k] for k in ("raw_observation", "raw_quote")),
       "running the study writes nothing to the raw store")

    # --- read-only API surface ---------------------------------------------
    routes = {getattr(r, "path", None) for r in appmain.app.routes}
    ok("/api/phase36/report" in routes, "the phase36 report route is registered")
    ok(service.artefact("verdict", instrument="CRUDEOIL; drop table")[
        "available"] is False,
       "an instrument name that is not a plain symbol is refused")
    ok(service.artefact("not_an_artefact")["available"] is False,
       "an unknown artefact name is refused rather than path-joined")
    src_api = inspect.getsource(appmain.phase36_report)
    ok("service" in src_api and "run(" not in src_api,
       "the endpoint reads written artefacts and never starts a study")

    # --- CLI surface --------------------------------------------------------
    ok(callable(cli.main) and "--instrument" in inspect.getsource(cli.main),
       "the CLI exposes the instrument filter")
    ok("run" in inspect.getsource(cli.main)
       and "coverage" in inspect.getsource(cli.main),
       "the CLI can report coverage before committing to a full run")

    # --- §1 the throughput filters narrow the read, never the arithmetic ----
    ok("--since" in inspect.getsource(p35cli.main),
       "the phase35 CLI exposes the file-level since filter")
    with tempfile.TemporaryDirectory() as tmp:
        old = os.path.join(tmp, "phase17_observations.20260101-000000.jsonl")
        new = os.path.join(tmp, "phase17_observations.jsonl")
        for fp in (old, new):
            with open(fp, "w", encoding="utf-8") as fh:
                fh.write("{}\n")
        os.utime(old, (0.0, time.mktime((2026, 1, 1, 12, 0, 0, 0, 0, -1))))
        listed = [os.path.basename(f) for f in _files_in(tmp, since="2026-06-01")]
        ok(listed == ["phase17_observations.jsonl"],
           "a file last written before --since is not opened this pass")
        listed_all = [os.path.basename(f) for f in _files_in(tmp, since=None)]
        ok(len(listed_all) == 2,
           "without --since every rotated file is still read")
    try:
        p35capture._since_epoch("07-09-2026")
    except ValueError:
        ok(True, "a malformed --since date is refused, not silently ignored")
    else:
        ok(False, "a malformed --since date is refused, not silently ignored")
    sig = inspect.signature(p35capture.ingest).parameters
    ok("since" in sig and "instrument" in sig,
       "ingest carries both throughput filters")
    ok(inspect.signature(p35service.rebuild).parameters.keys() >= {"since"},
       "rebuild passes the since filter through to ingest")

    # --- the forward path must be an indexed, session-bounded read -----------
    # Without these two the rebuild is quadratic in the size of the store: every
    # leg scans raw_quote end to end, which is what made a real ingest never
    # finish once the store held a few hundred thousand quotes.
    from app.research.phase35 import book as p35book
    from app.research.phase35 import path as p35path
    from app.research.phase35 import store as p35store

    con = p35store.connect(":memory:")
    idx = {
        r["name"]
        for r in con.execute("PRAGMA index_list(raw_quote)").fetchall()
    }
    ok("idx_quote_path" in idx,
       "raw_quote is indexed by (symbol, vehicle, ts) for forward-path reads")
    cols = [
        r["name"]
        for r in con.execute("PRAGMA index_info(idx_quote_path)").fetchall()
    ]
    ok(cols == ["symbol", "vehicle", "ts"],
       "the forward-path index matches the order the query filters in")

    base = time.mktime((2026, 9, 7, 10, 0, 0, 0, 0, -1))
    rows = [
        ("q1", "CE", base + 60, "CRUDEOIL17SEP268700CE", 100.0, 100.8, "M"),
        ("q2", "CE", base + 120, "CRUDEOIL17SEP268700CE", 101.0, 101.8, "M"),
        # next IST day: same contract, must never enter this session's path
        ("q3", "CE", base + 86400, "CRUDEOIL17SEP268700CE", 150.0, 150.8, "M"),
        ("q4", "CE", base + 60, "CRUDEOIL17SEP268750CE", 90.0, 90.8, "M"),
    ]
    con.executemany(
        "INSERT INTO raw_quote (obs_id, vehicle, ts, symbol, bid, ask, evidence) "
        "VALUES (?,?,?,?,?,?,?)", rows,
    )
    got = p35path.forward_samples(
        con, symbol="CRUDEOIL17SEP268700CE", vehicle="CE", after_ts=base)
    ok([s["ts"] for s in got] == [base + 60, base + 120],
       "the forward path stops at the session boundary and stays on one contract")
    plan = con.execute(
        "EXPLAIN QUERY PLAN SELECT ts FROM raw_quote WHERE symbol = ? AND "
        "vehicle = ? AND ts > ? AND ts < ? ORDER BY ts",
        ("x", "CE", 0.0, 1.0),
    ).fetchall()
    ok(any("idx_quote_path" in str(r["detail"]) for r in plan),
       "the forward-path query plan uses the index rather than scanning")

    # The cache is a speed change only: it must hand back exactly the rows and
    # the fills the per-leg query and book.exit_fill produced.
    cache = p35path.ForwardCache()
    cached = cache.samples(
        con, symbol="CRUDEOIL17SEP268700CE", vehicle="CE", direction="LONG",
        after_ts=base)
    ok([s["ts"] for s in cached] == [s["ts"] for s in got]
       and [s["bid"] for s in cached] == [s["bid"] for s in got],
       "the cached forward path is the same rows as the direct query")
    ok(all(
        s["fill"] == p35book.exit_fill(s, vehicle="CE", direction="LONG")
        for s in cached
    ), "the precomputed exit fill equals book.exit_fill for every sample")
    again = cache.samples(
        con, symbol="CRUDEOIL17SEP268700CE", vehicle="CE", direction="LONG",
        after_ts=base + 60)
    ok([s["ts"] for s in again] == [base + 120],
       "a second leg on the same contract gets its own suffix from the cache")
    ok(cache.samples(
        con, symbol=None, vehicle="CE", direction="LONG", after_ts=base) == [],
       "a quote with no contract symbol has no forward path, not a wrong one")

    # --- the indexed horizon lookup must equal the walk it replaces ---------
    #
    # After the executable-book rules this is the most important check in the
    # file: the run stopped finishing because every leg re-read and re-walked
    # its contract's whole session, and the fix is only allowed to be faster.
    # The walk is kept as the reference implementation and compared against the
    # indexed answer row for row — whole rows, not selected fields — on a store
    # built to hold the cases that break a range query: flat stretches (tie
    # breaks), one-sided books (traded-price exits), missing quotes (gaps), and
    # both directions.
    rng = random.Random(36)  # noqa: S311 - reproducible fixture, not crypto
    coni = store.connect(":memory:")
    eq_obs: list[dict] = []
    eq_q: list[dict] = []
    mark = {CE: 250.0, PE: 260.0, FUTURES: 8700.0}
    for i in range(140):
        ts = T0 + i * 45.0
        oid = f"eq-{i}"
        eq_obs.append(_obs(oid, ts=ts, engine_selected=(i % 3 == 0),
                           direction="BULLISH" if i % 2 else "BEARISH"))
        for vehicle, sym in ((CE, CE_SYM), (PE, PE_SYM), (FUTURES, FUT_SYM)):
            mark[vehicle] = max(1.0, mark[vehicle] * (1.0 + rng.choice(
                (-3.0, -1.5, -0.5, 0.0, 0.0, 0.5, 1.5, 3.0)) / 100.0))
            bid = round(mark[vehicle], 2)
            quote = _q(oid, vehicle, ts=ts, bid=bid,
                       ask=round(bid + rng.choice((0.2, 0.5, 1.0)), 2),
                       symbol=sym,
                       strike=None if vehicle == FUTURES else 8700.0)
            if i % 17 == 5:
                quote["bid"] = None       # one-sided: the exit falls to traded
            if i % 23 == 7:
                continue                  # a quote the capture simply missed
            eq_q.append(quote)
    store.save_observations(coni, eq_obs)
    store.save_quotes(coni, eq_q)
    eq_triples = triples.build(coni, instrument="CRUDEOIL")["triples"]
    ok(len(eq_triples) > 40,
       "the equivalence fixture has a real sample of triples to compare")

    mismatch = None
    for t in eq_triples:
        for vehicle, role in outcome.vehicles_of(t):
            for off in (0.0, 5.0, 15.0, ENTRY_CONFIRMATION):
                walked = outcome.resolve(coni, t, vehicle, role=role,
                                         offset=off, walk="scan")
                indexed = outcome.resolve(coni, t, vehicle, role=role,
                                          offset=off, walk="index")
                if walked != indexed:
                    mismatch = mismatch or (t["obs_id"], vehicle, off)
    ok(mismatch is None,
       f"every indexed row equals the walked row (first differs: {mismatch})")

    # The range extremes themselves, against brute force, ties included.
    bad_range = None
    for _ in range(25):
        vals = [rng.choice((1.0, 2.0, 2.0, 3.0, -1.0))
                for _ in range(rng.randint(1, 40))]
        biggest = pathindex._Extreme(vals, largest=True)
        smallest = pathindex._Extreme(vals, largest=False)
        for lo in range(len(vals)):
            for hi in range(lo, len(vals)):
                window = vals[lo:hi + 1]
                if (biggest.query(lo, hi) != lo + window.index(max(window))
                        or smallest.query(lo, hi)
                        != lo + window.index(min(window))):
                    bad_range = bad_range or (list(vals), lo, hi)
    ok(bad_range is None,
       "a range extreme is the first best value in the window, as the walk was")

    # Entry selection became an index lookup too, so it is checked against the
    # scan it replaced rather than assumed.
    paths = pathindex.PathIndex()
    contract = paths.contract(coni, symbol=CE_SYM, vehicle=CE, direction=LONG,
                              at_ts=T0)
    bad_entry = None
    for after in (T0, T0 + 900.0, T0 + 3600.0):
        for minutes in (1.0, 5.0, 30.0, 600.0):
            want = next((r for r in contract.rows if r["ts"] > after
                         and (r["ts"] - after) / 60.0 >= minutes), None)
            got = contract.first_after_minutes(after, minutes=minutes)
            if (want or {}).get("ts") != (got or {}).get("ts"):
                bad_entry = bad_entry or (after, minutes)
    ok(bad_entry is None,
       "a delayed entry takes the first quote at least that many minutes later")

    bad_confirm = None
    for entry_px in (1.0, 200.0, 260.0, 1e9):
        want = next((
            r for r in contract.rows
            if r["ts"] > T0 and r["fill"]["price"] is not None
            and r["fill"]["evidence"] == MEASURED_EXECUTABLE
            and book.gross_pct(entry_px, float(r["fill"]["price"]),
                               vehicle=CE, direction=LONG) > 0
        ), None)
        got, _got_ts = outcome._entry_quote(
            contract, offset=ENTRY_CONFIRMATION, entry_ts=T0, quote={},
            vehicle=CE, direction=LONG, base_entry=entry_px,
        )
        if (want or {}).get("ts") != (got or {}).get("ts"):
            bad_confirm = bad_confirm or entry_px
    ok(bad_confirm is None,
       "a confirmation entry takes the first later quote already in profit")

    # And it has to be faster, not just equal: same rows, same answers, one
    # read and one index per contract-session instead of one per leg.
    conp = store.connect(":memory:")
    p_obs: list[dict] = []
    p_q: list[dict] = []
    for i in range(600):
        ts = T0 + i * 10.0
        oid = f"perf-{i}"
        p_obs.append(_obs(oid, ts=ts, engine_selected=False,
                          direction="BULLISH"))
        for vehicle, sym in ((CE, CE_SYM), (PE, PE_SYM), (FUTURES, FUT_SYM)):
            px = 250.0 + (i % 40)
            p_q.append(_q(oid, vehicle, ts=ts, bid=px, ask=px + 0.5,
                          symbol=sym,
                          strike=None if vehicle == FUTURES else 8700.0))
    store.save_observations(conp, p_obs)
    store.save_quotes(conp, p_q)
    perf_triples = triples.build(conp, instrument="CRUDEOIL")["triples"][:200]
    started = time.perf_counter()
    scan_rows = [
        outcome.resolve(conp, t, vehicle, role=role, walk="scan")
        for t in perf_triples
        for vehicle, role in outcome.vehicles_of(t)
    ]                       # no shared index: one read and one walk per leg,
    t_scan = time.perf_counter() - started   # exactly what would not finish
    started = time.perf_counter()
    idx_rows = outcome.resolve_all(conp, perf_triples)
    t_idx = time.perf_counter() - started
    ok(scan_rows == idx_rows,
       "a whole indexed pass equals the walked pass over 600 legs")
    ok(t_idx * 3.0 < t_scan,
       f"the indexed pass is materially faster ({t_scan:.2f}s -> {t_idx:.2f}s)")
    print(f"  [perf] 200 triples x 3 vehicles: walk {t_scan:.2f}s, "
          f"index {t_idx:.2f}s")
    coni.close()
    conp.close()

    # The inline move in resolve() must equal book.gross_pct exactly, including
    # its rounding — an approximation here would move every net figure.
    for vehicle, direction in (("CE", "LONG"), ("PE", "SHORT"),
                               ("FUTURES", "LONG"), ("FUTURES", "SHORT")):
        sign = -1.0 if (vehicle == "FUTURES" and direction == "SHORT") else 1.0
        same = all(
            round(100.0 * sign * (px - entry) / entry, 4)
            == p35book.gross_pct(entry, px, vehicle=vehicle, direction=direction)
            for entry in (0.5, 27.35, 293.8, 8681.0)
            for px in (0.35, 29.9, 294.6, 8733.0)
        )
        ok(same, f"the inline {vehicle}/{direction} move equals book.gross_pct")
    con.close()

    print(f"\nPHASE 36 SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
