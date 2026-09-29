"""Phase 38 smoke — the properties that stop a loss diagnostic from lying.

A post-mortem is easier to get wrong than a study, because every number in it is
already known to be bad and nobody argues with a plausible explanation of a
loss. Each check below exists because the opposite mistake would explain this
session confidently and wrongly:

* attributing a loss to cost when the market never moved in the leg's favour at
  all, which sends the next month of work at the cost model;
* attributing it to the move when the excursion cleared the round trip and the
  hold gave it back;
* calling it WRONG_VEHICLE when every vehicle on that instant lost;
* calling the entry too early on a difference smaller than rounding;
* naming the best-of-twelve horizon an exit;
* charging the spread twice in the waterfall, so the arithmetic no longer adds
  up to the net that Phase 36 reported;
* filling a rupee total with zero for legs whose lot size was never captured,
  which quietly makes the loss look smaller;
* reading a three-leg DTE cohort as a finding;
* emitting VALIDATED or PRODUCTION_READY from a one-session document;
* dropping the required header or footer, which is what stops this file being
  quoted as a verdict.

    .venv/bin/python _smoke_phase38.py
"""
from __future__ import annotations

import inspect
import json
import os
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
from app.research.phase36 import outcome as p36outcome
from app.research.phase36 import tables as p36tables
from app.research.phase36 import triples as p36triples
from app.research.phase38 import (
    ACTIONS,
    CAUSE_COST,
    CAUSE_GIVEBACK,
    CAUSE_MOVE,
    CAUSE_VEHICLE,
    COLLECT_MORE,
    COST_DOMINATED,
    DATA_FIX,
    ENTRY_NOT_THE_PROBLEM,
    GIVEBACK,
    HEADER_LINES,
    INSUFFICIENT_EVIDENCE,
    JSON_NAME,
    MD_NAME,
    MIN_COHORT,
    MOVE_TOO_SMALL,
    OTHER,
    SOME_VEHICLE_PAID,
    TOO_EARLY,
    UNDERLYING_MOVE_INSUFFICIENT,
    cli,
    diagnosis,
    money,
    report,
    sections,
    service,
)

PASS = 0
FAIL: list[str] = []

T0 = 1_788_752_100.0
MIN = 60.0
LOT = 100
DAY = 86_400.0
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


def _obs(obs_id: str, *, ts: float, direction: str = LONG,
         lot_size: int | None = LOT) -> dict:
    return {
        "obs_id": obs_id,
        "ts": ts,
        "session": None,
        "instrument": "CRUDEOIL",
        "family": "MCX",
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
       symbol: str, strike: float | None = None, underlying: float = 8700.0,
       lot_size: int | None = LOT, dte: int = 10) -> dict:
    spread = ask - bid
    return {
        "obs_id": obs_id, "vehicle": vehicle, "ts": ts, "symbol": symbol,
        "strike": strike, "expiry": "2026-09-17", "dte": dte,
        "bid": bid, "ask": ask, "traded": (bid + ask) / 2.0,
        "spread": spread, "spread_pct": 100.0 * spread / ask,
        "delta": 0.5, "iv": 32.0, "oi": 4000.0, "volume": 900.0,
        "underlying": underlying, "basis": 2.0, "lot_size": lot_size,
        "evidence": MEASURED_EXECUTABLE, "reason": None,
    }


def _seed(con, *, obs_id: str, ts: float, direction: str = LONG,
          ce=(278.0, 279.0), pe=(293.0, 294.0), fut=(8698.0, 8700.0),
          ce_path=(285.0,), pe_path=(288.0,), fut_path=(8730.0,),
          lot_size: int | None = LOT) -> None:
    rows = [_obs(obs_id, ts=ts, direction=direction, lot_size=lot_size)]
    quotes = [
        _q(obs_id, CE, ts=ts, bid=ce[0], ask=ce[1], symbol=CE_SYM,
           strike=8700.0, lot_size=lot_size),
        _q(obs_id, PE, ts=ts, bid=pe[0], ask=pe[1], symbol=PE_SYM,
           strike=8700.0, lot_size=lot_size),
        _q(obs_id, FUTURES, ts=ts, bid=fut[0], ask=fut[1], symbol=FUT_SYM,
           lot_size=lot_size),
    ]
    for i, (c, p, f) in enumerate(
        zip(ce_path, pe_path, fut_path, strict=False), start=1,
    ):
        fid = f"{obs_id}-f{i}"
        fts = ts + i * 35 * MIN
        rows.append(_obs(fid, ts=fts, direction=direction, lot_size=lot_size))
        quotes += [
            _q(fid, CE, ts=fts, bid=c, ask=c + 1.0, symbol=CE_SYM,
               strike=8700.0, lot_size=lot_size),
            _q(fid, PE, ts=fts, bid=p, ask=p + 1.0, symbol=PE_SYM,
               strike=8700.0, lot_size=lot_size),
            _q(fid, FUTURES, ts=fts, bid=f, ask=f + 2.0, symbol=FUT_SYM,
               lot_size=lot_size),
        ]
    store.save_observations(con, rows)
    store.save_quotes(con, quotes)


def _resolved(con):
    triples = p36triples.build(con, instrument="CRUDEOIL")["triples"]
    rows = p36outcome.resolve_all(con, triples)
    horizon = "30"
    return rows, horizon


def main() -> int:  # noqa: PLR0915 - one flat list of independent checks
    # ---------------------------------------------------------------- §2 rule
    # The category is the whole diagnostic: get it wrong and every downstream
    # ranking blames the wrong thing. Each branch is pinned on a leg built to
    # sit in exactly one of them.
    ok(sections.categorise({
        "net_rupees": 400.0, "mfe_pct": 2.0, "cost_pct": 0.5,
    }) == OTHER, "a leg that made money is not a loss category")
    ok(sections.categorise({
        "net_rupees": -100.0, "mfe_pct": 0.0, "cost_pct": 0.6,
    }) == MOVE_TOO_SMALL,
       "no favourable excursion at all is MOVE_TOO_SMALL, never a cost problem")
    ok(sections.categorise({
        "net_rupees": -100.0, "mfe_pct": 0.3, "cost_pct": 0.9,
    }) == COST_DOMINATED,
       "an excursion smaller than the leg's own round trip is COST_DOMINATED")
    ok(sections.categorise({
        "net_rupees": -100.0, "mfe_pct": 3.0, "cost_pct": 0.9,
    }) == GIVEBACK,
       "an excursion that cleared the round trip and still lost is giveback")
    ok(sections.categorise({
        "net_rupees": -100.0, "mfe_pct": None, "cost_pct": None,
    }) == MOVE_TOO_SMALL,
       "an unmeasured excursion is not silently promoted to a cost finding")
    ok(sections.categorise({
        "net_rupees": -100.0, "mfe_pct": 0.9, "cost_pct": 0.9,
    }) == GIVEBACK,
       "an excursion exactly equal to the round trip is not COST_DOMINATED")

    # ------------------------------------------------------- rupee conversion
    row = {"lot_size": LOT, "entry_price": 279.0}
    ok(money.rupees(1.5, row) == 150.0, "points times lot size is the rupees")
    ok(money.rupees(1.5, {"lot_size": None, "entry_price": 279.0}) is None,
       "a leg with no captured lot size converts to None, never to zero")
    ok(money.pct_to_rupees(1.0, row) == 279.0,
       "one percent of a 279 premium on one lot is 279 rupees")
    ok(money.pct_to_rupees(1.0, {"lot_size": LOT, "entry_price": 0.0}) is None,
       "a zero entry cannot be converted rather than dividing by it")

    # ------------------------------------------------------- the whole diagnostic
    con = store.connect(":memory:")
    for k in range(40):
        _seed(con, obs_id=f"win-{k}", ts=T0 + k * 5 * MIN,
              fut_path=(8760.0,), ce_path=(320.0,), pe_path=(250.0,))
    state = service.run(con, instrument="CRUDEOIL")

    ok(state["session_count"] == 1 and len(state["sessions"]) == 1,
       "one seeded session is reported as one session, not as a sample")
    ok(state["coverage"]["eligible"] > 0, "the fixture produced triples")
    pnl = state["vehicle_pnl"]
    ok(set(pnl) == {FUTURES, CE, PE}, "§1 reports all three vehicles")
    for v in (FUTURES, CE, PE):
        body = pnl[v]
        ok(abs(
            (body["spread_rupees"] + body["brokerage_rupees"]
             + body["statutory_rupees"] + body["slippage_rupees"])
            - body["cost_rupees"]
        ) < 1.0,
           f"§1 {v}: the itemised charges add up to the cost line")
        ok(abs(
            body["gross_rupees"] - body["cost_rupees"] - body["net_rupees"]
        ) < 1.0, f"§1 {v}: GROSS - COSTS = NET, with nothing unexplained")
    ok(all(p["net_r"] is None and p["net_r_reason"] for p in pnl.values()),
       "net R is refused with a reason rather than invented from no stop")

    # Spread is inside the executable fills, so charging it again would show up
    # here as a second spread line. Phase 36 passes zero; this must not add one.
    ok(pnl[FUTURES]["spread_rupees"] == 0.0
       and pnl[CE]["spread_rupees"] == 0.0,
       "the spread already paid by ask-in/bid-out is not charged a second time")

    # --------------------------------------------------------------- §2 totals
    mvc = state["move_vs_cost"]
    ok(sum(b["n"] for b in mvc["by_category"].values()) == mvc["legs"],
       "§2 categories partition the sample: the counts add up to the legs")
    ok(set(mvc["by_category"]) <= {MOVE_TOO_SMALL, COST_DOMINATED, GIVEBACK,
                                   OTHER},
       "§2 emits no category outside the four the task defines")

    # --------------------------------------------------------------- §3 holds
    hold = state["hold_time"]
    ok(all(
        set(hold[v]["by_horizon"]) == set(p36tables.HORIZON_KEYS)
        for v in (FUTURES, CE, PE)
    ), "§3 reports all twelve horizons plus session close for every vehicle")
    ok(all("not a validated exit" in hold[v]["note"] for v in hold),
       "§3 refuses to call the best-of-twelve horizon an exit")

    # --------------------------------------------------------------- §4 money
    gb = state["giveback"]["by_vehicle"]
    ok(all(
        b["money_available_rupees"] >= b["money_captured_rupees"]
        or b["losing_trades"] == 0
        for b in gb.values()
    ), "§4: money captured never exceeds the money the market offered")
    ok(all(
        b["money_given_back_rupees"] == round(
            b["money_available_rupees"] - b["money_captured_rupees"], 2,
        ) for b in gb.values()
    ), "§4 giveback is available minus captured, not a separate estimate")
    ok(all(
        b["losing_trades"]
        == b["losing_trades_that_went_green"] + b["never_went_green_n"]
        for b in gb.values()
    ), "§4 splits the losers into those that went green and those that did not")

    # ------------------------------------------------------------- §5 CE/PE
    cp = state["ce_vs_pe"]
    ok(cp["pairs"] > 0, "§5 found same-timestamp CE/PE pairs")
    for key, body in cp["wins"].items():
        ok(body[CE] + body[PE] + body["TIE"] + body["UNMEASURED"]
           == cp["pairs"],
           f"§5 {key}: every pair is scored exactly once, unmeasurable "
           "included, so a blank row cannot read as a dead heat")
    ok("neither side is called superior" in cp["note"],
       "§5 states that one session cannot rank the two sides")

    # ------------------------------------------------------------- §7 entry
    ent = state["entry_timing"]
    ok(set(ent["by_offset"]) >= {"0.0", "1.0", "2.0"},
       "§7 compares the decision instant with the 1- and 2-minute delays")
    ok(ent["label"] in (TOO_EARLY, ENTRY_NOT_THE_PROBLEM),
       "§7 emits a measurable label")
    ok("UNMEASURABLE" in ent["too_late_note"],
       "§7 says TOO_LATE cannot be measured rather than never mentioning it")

    # -------------------------------------------------------------- §10 waterfall
    wf = state["waterfall"]
    ok(wf["order"] == ["TOTAL GROSS OPPORTUNITY", "SPREAD", "BROKERAGE",
                       "STATUTORY", "SLIPPAGE", "GIVEBACK", "FINAL NET"],
       "§10 has exactly the required steps, in the required order")
    ok([s["step"] for s in wf["steps"]] == wf["order"],
       "§10 prints the steps in that order, not merely declares it")
    ok(wf["closes"] and abs(wf["residual_rupees"]) < 0.05,
       "§10 arithmetic closes: offered - each cost - giveback = final net")
    total_net = round(sum(
        pnl[v]["net_rupees"] for v in (FUTURES, CE, PE)
    ), 2)
    final = report.final_net(state)
    ok(abs(final - total_net) < 1.0,
       "§10 final net equals the sum of the §1 per-vehicle nets")

    # -------------------------------------------------------------- §11 ranking
    ranked = state["ranked_causes"]
    realised = [r for r in ranked if r["kind"] == "REALISED_LOSS"]
    ok({r["cause"] for r in realised} == {CAUSE_MOVE, CAUSE_COST,
                                          CAUSE_GIVEBACK},
       "§11 realised causes are exactly the three that decompose the net")
    ok(abs(sum(r["rupees"] for r in realised) - (
        total_net - _other_net(mvc)
    )) < 1.0,
       "§11 the three realised causes sum to the net of the losing legs")
    impacts = [r.get("impact_rupees") or 0.0 for r in ranked]
    ok(impacts == sorted(impacts, reverse=True),
       "§11 is sorted by rupee impact, not by how fixable a cause is")
    ok(all(r["descriptive_only"] for r in ranked),
       "with one session every ranked cause is marked descriptive only")

    # A counterfactual must not be counted as realised money: it is measured on
    # the same legs and adding it to the waterfall would double-count.
    ok(all(r["kind"] == "COUNTERFACTUAL" for r in ranked
           if r["cause"] == CAUSE_VEHICLE),
       "WRONG_VEHICLE is an opportunity, not part of the realised loss")

    # ------------------------------------------------------------- the footer
    md = report.render(state)
    lines = md.splitlines()
    ok(lines[0] == "SESSION_COUNT = 1", "the report begins with SESSION_COUNT")
    ok(lines[1] == "" and lines[2] == HEADER_LINES[0]
       and lines[3] == HEADER_LINES[1],
       "the two required header lines follow verbatim")
    tail = [ln for ln in lines if ln.startswith((
        "PRIMARY LOSS DRIVER:", "SECONDARY LOSS DRIVER:", "CURRENT ACTION:",
    ))]
    ok(len(tail) == 3 and tail[0].startswith("PRIMARY LOSS DRIVER:")
       and tail[1].startswith("SECONDARY LOSS DRIVER:")
       and tail[2].startswith("CURRENT ACTION:"),
       "the three required footer lines are present, in order")
    ok(lines[-3:] == tail,
       "the document ends with those three lines and nothing after them")
    action = state["action"]["action"]
    ok(action in ACTIONS, "CURRENT ACTION is one of the three allowed values")
    ok(tail[2] == f"CURRENT ACTION: {action}",
       "the footer prints the action the payload carries")
    ok("VALIDATED" not in md and "PRODUCTION_READY" not in md,
       "the document cannot express a promotion")
    ok(action == COLLECT_MORE,
       "one session yields COLLECT MORE DATA, whatever the tables say")

    # Every markdown section the task asks for is present and numbered.
    for heading in (
        "## 1. Vehicle P&L decomposition", "## 2. Move vs cost",
        "## 3. Hold time", "## 4. Giveback", "## 5. CE vs PE",
        "## 6. Futures vs options", "## 7. Entry timing",
        "## 8. Premium bands", "## 9. DTE and moneyness",
        "## 10. Money-loss waterfall", "## 11. Final diagnosis",
        "## 12. What this cannot say",
    ):
        ok(heading in md, f"the report contains {heading!r}")

    # ------------------------------------------------- thin cohorts say so
    dm = state["dte_and_moneyness"]
    thin = [
        s for body in dm["dte"].values()
        for s in body["by_vehicle"].values()
        if (s["trades"] or 0) < MIN_COHORT
    ]
    ok(thin and all(s["insufficient"] == INSUFFICIENT_EVIDENCE for s in thin),
       "a cohort below the floor prints INSUFFICIENT_EVIDENCE, not a mean")
    ok(all(
        (s["insufficient"] is None) == ((s["trades"] or 0) >= MIN_COHORT)
        for body in dm["moneyness"].values()
        for s in body["by_vehicle"].values()
    ), "the sample flag is the floor, applied identically to every cohort")

    # ----------------------------------- a loss with no favourable move at all
    # Nothing in this fixture ever moves the right way, so the diagnosis must be
    # the move — attributing it to cost would be the classic misread.
    con_flat = store.connect(":memory:")
    for k in range(40):
        _seed(con_flat, obs_id=f"flat-{k}", ts=T0 + k * 5 * MIN,
              fut_path=(8660.0,), ce_path=(240.0,), pe_path=(240.0,))
    flat = service.run(con_flat, instrument="CRUDEOIL",
                       with_entry_timing=False)
    cats = flat["move_vs_cost"]["by_category"]
    ok((cats.get(MOVE_TOO_SMALL) or {}).get("n", 0) > 0
       and (cats.get(COST_DOMINATED) or {}).get("n", 0) == 0,
       "a session that never moved favourably is MOVE_TOO_SMALL, not cost")
    ok(flat["diagnosis"]["primary"] == CAUSE_MOVE,
       "the primary driver of an adverse session is the move")
    ok(flat["futures_vs_options"]["patterns"][UNDERLYING_MOVE_INSUFFICIENT] > 0,
       "§6 recognises that the underlying move itself was insufficient")
    ok(flat["vehicle_opportunity"]["improvement_rupees"] == 0.0,
       "when every vehicle lost, no vehicle switch is credited with rupees")
    ok(flat["diagnosis"]["primary"] != CAUSE_VEHICLE,
       "WRONG_VEHICLE is not named when nothing else was profitable")
    con_flat.close()

    # ------------------------------- a loss where another vehicle did pay
    con_mixed = store.connect(":memory:")
    for k in range(40):
        # SHORT view: the future is priced short and loses as price rises,
        # while the long put on the same instant gains.
        _seed(con_mixed, obs_id=f"mix-{k}", ts=T0 + k * 5 * MIN,
              direction=SHORT, fut_path=(8740.0,), ce_path=(240.0,),
              pe_path=(360.0,))
    mixed = service.run(con_mixed, instrument="CRUDEOIL",
                        with_entry_timing=False)
    ok(mixed["futures_vs_options"]["patterns"][SOME_VEHICLE_PAID] > 0,
       "§6 counts an instant where at least one vehicle paid")
    ok(mixed["vehicle_opportunity"]["improvement_rupees"] > 0,
       "a vehicle switch is credited only when another vehicle was positive")
    con_mixed.close()

    # ------------------------------------------- legs with no lot size
    con_nolot = store.connect(":memory:")
    for k in range(40):
        _seed(con_nolot, obs_id=f"nolot-{k}", ts=T0 + k * 5 * MIN,
              lot_size=None)
    nolot = service.run(con_nolot, instrument="CRUDEOIL",
                        with_entry_timing=False)
    ok(nolot["waterfall"]["n_legs"] == 0,
       "a leg with no captured lot size contributes to no rupee total")
    ok(nolot["action"]["action"] == DATA_FIX,
       "when the legs cannot be costed the action is REQUIRES DATA FIX, not a "
       "market explanation")
    ok("VALIDATED" not in report.render(nolot),
       "the no-cost path still cannot promote anything")
    con_nolot.close()

    # ------------------------------------------------------ artefacts on disk
    with tempfile.TemporaryDirectory() as tmp:
        original = service.artefact_dir
        service.artefact_dir = lambda: tmp  # type: ignore[assignment]
        try:
            written = service.write_artefacts(state)
        finally:
            service.artefact_dir = original  # type: ignore[assignment]
        names = {os.path.basename(p) for p in written}
        ok(names == {MD_NAME, JSON_NAME},
           "exactly the two artefacts §13 names are written")
        with open(os.path.join(tmp, JSON_NAME), encoding="utf-8") as fh:
            payload = json.load(fh)
        ok(payload["session_count"] == 1
           and payload["diagnosis"]["primary"] == state["diagnosis"]["primary"],
           "the json artefact carries the same diagnosis as the document")
        for key in ("vehicle_pnl", "move_vs_cost", "hold_time", "giveback",
                    "ce_vs_pe", "futures_vs_options", "entry_timing",
                    "premium_bands", "dte_and_moneyness", "waterfall",
                    "ranked_causes", "action"):
            ok(key in payload, f"the json artefact contains {key}")
        blob = json.dumps(payload)
        ok("VALIDATED" not in blob and "PRODUCTION_READY" not in blob,
           "no promotion label appears anywhere in the payload")

    # ------------------------------------------------- read-only by construction
    for module in (service, sections, diagnosis, money, report, cli):
        src = inspect.getsource(module)
        ok("save_observations" not in src and "save_quotes" not in src
           and "INSERT" not in src.upper().replace("INSERT INTO SQLITE", ""),
           f"{module.__name__} never writes to the research store")
        ok("place_order" not in src and "order_path" not in src,
           f"{module.__name__} has no order path")

    ok("--no-artefacts" in inspect.getsource(cli),
       "the CLI can print without writing, for a read-only inspection")

    # The summary the CLI prints must not omit the action or the drivers.
    summ = service.summary(state)
    ok(summ["current_action"] == action
       and summ["primary_loss_driver"] == state["diagnosis"]["primary"]
       and summ["session_count"] == 1,
       "the CLI summary carries the session count, drivers and action")
    ok(report.headline(state).count("NOT A STRATEGY VERDICT") == 1,
       "the terminal headline repeats that this is not a verdict")

    con.close()
    print(f"\nPHASE 38 SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


def _other_net(mvc: dict) -> float:
    body = (mvc.get("by_category") or {}).get(OTHER) or {}
    return float(body.get("net_rupees") or 0.0)


if __name__ == "__main__":
    raise SystemExit(main())
