"""Phase 42 smoke — the properties that stop an entry filter from lying.

This phase exists because of a mistake made in this project: a band table built
on the realised peak was read as an entry filter and nearly wired into the gate.
The checks below are mostly about making that class of error loud rather than
silent:

* the ratio reading a field that only exists after the leg is over;
* the trailing range including the decision's own minute, so the move being
  forecast contributes to its own forecast;
* an unmeasurable leg being counted as refused, which measures a gate the
  engine does not run;
* the typical range moving with how densely a minute was sampled;
* a fingerprint that ignores function bodies, or moves on a comment;
* a fingerprint that restates a threshold rather than reading it;
* choosing the multiple on the sessions it is then judged on;
* a holdout of one or two sessions being reported as a holdout;
* publishing a label below the declared session floor, which happened: the
  floor was declared and fingerprinted and never applied to the verdict;
* cutting where the cost basis changed, so one half of the holdout was costed
  by a modelled spread and the other from a measured lot;
* calling a loss-reducing filter profitable;
* claiming a filter beat the live multiple without comparing them on the same
  sessions;
* the best hindsight horizon being presented as an exit rule;
* writing anything at all to the store it reads.

    .venv/bin/python _smoke_phase42.py
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sqlite3
import sys
import tempfile

from app.research.phase35 import (
    CE,
    FUTURES,
    LONG,
    MEASURED_EXECUTABLE,
    SESSION_CLOSE,
    store,
)
from app.research.phase42 import (
    BASIS_CHANGED,
    BASIS_CONSISTENT,
    BETTER_THAN_LIVE,
    EX_ANTE_MEASURED,
    EX_ANTE_UNMEASURED,
    INSUFFICIENT,
    LIVE_MULTIPLE,
    LOSS_REDUCTION_ONLY,
    MIN_SESSIONS,
    MIN_SESSIONS_FOR_SPLIT,
    NO_BETTER_THAN_LIVE,
    NO_COST,
    NO_DELTA,
    NO_RANGE,
    POSITIVE_IN_HOLDOUT,
    RANGE_FROM_UNDERLYING,
    THRESHOLDS,
    arms,
    cli,
    exante,
    freeze,
    giveback,
    report,
    validate,
)

LOT = 100
MIN = 60.0
T0 = 1_760_000_000.0

PASS = 0
FAIL: list[str] = []


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def _obs(obs_id: str, *, ts: float, session: str, instrument: str) -> dict:
    return {
        "obs_id": obs_id, "ts": ts, "session": session,
        "instrument": instrument, "family": "MCX", "source": "BOARD",
        "opportunity_type": "DIRECTIONAL", "direction": LONG,
        "engine_class": "BUY", "engine_selected": False,
        "selected_vehicle": None,
        "context": {"plan": {"lot_size": LOT}}, "origin": "SMOKE",
        "origin_id": obs_id, "ingest_ts": ts,
    }


def _quote(obs_id: str, *, ts: float, vehicle: str = CE, underlying: float,
           delta: float | None = 0.5, traded: float = 100.0) -> dict:
    return {
        "obs_id": obs_id, "vehicle": vehicle, "ts": ts, "symbol": "SMK",
        "strike": 8700.0, "expiry": "2026-09-17", "dte": 10,
        "bid": 99.8, "ask": 100.2, "traded": traded, "spread": 0.4,
        "spread_pct": 0.4, "delta": delta, "iv": 30.0, "oi": 100.0,
        "volume": 100.0, "underlying": underlying, "basis": 0.0,
        "lot_size": LOT, "evidence": MEASURED_EXECUTABLE, "reason": None,
    }


def _leg(leg_id: str, obs_id: str, *, ts: float, net: float,
         cost: float = 1.0, vehicle: str = CE, instrument: str = "CRUDEOIL",
         book: str = "FULL_MARKET_PAPER",
         cost_evidence: str = MEASURED_EXECUTABLE) -> dict:
    return {
        "leg_id": leg_id, "obs_id": obs_id, "book": book, "vehicle": vehicle,
        "instrument": instrument, "direction": LONG, "entry_ts": ts,
        "entry_price": 100.0, "entry_side": "ASK", "lot_size": LOT,
        "lot_source": "QUOTE_AT_DECISION_INSTANT", "cost_points": cost,
        "cost_evidence": cost_evidence, "exit_ts": ts + 600,
        "exit_price": 100.0 + net, "exit_side": "BID",
        "exit_reason": SESSION_CLOSE, "gross_pct": net + 1.0, "net_pct": net,
        "resolved": 1, "evidence": MEASURED_EXECUTABLE, "reason": None,
    }


# Four cost groups against a fixed expected move of 0.5 x 4.0 = 2.0 points, so
# the legs land at ratios 4 / 2 / 1 / 0.5 and the arms actually differ. The net
# rises with the ratio, which is the shape the phase is built to detect — and
# detecting it in a fixture proves only that the measurement works, not that
# the shape is in anyone's data.
_GROUPS: tuple[tuple[float, float], ...] = (
    (0.5, 1.0), (1.0, 0.5), (2.0, -1.0), (4.0, -3.0),
)


def _seed(con, sessions: list[str], *, per_session: int = 160,
          underlying_step: float = 4.0,
          cost_evidence: dict[str, str] | None = None) -> None:
    """A store with real minute series, so the range is measured not asserted.

    Each session gets a rising underlying at one observation per minute; the
    legs are hung on the later minutes so a trailing window exists before each.
    """
    for s, session in enumerate(sessions):
        base = T0 + s * 86_400
        obs, quotes, legs = [], [], []
        for i in range(per_session + 6):
            obs_id = f"{session}-{i}"
            ts = base + i * MIN
            obs.append(_obs(obs_id, ts=ts, session=session,
                            instrument="CRUDEOIL"))
            quotes.append(_quote(obs_id, ts=ts,
                                 underlying=8700.0 + i * underlying_step))
        for i in range(per_session):
            k = i + 6
            cost, net = _GROUPS[i % len(_GROUPS)]
            legs.append(_leg(
                f"{session}-L{i}", f"{session}-{k}", ts=base + k * MIN,
                net=net, cost=cost,
                cost_evidence=(cost_evidence or {}).get(
                    session, MEASURED_EXECUTABLE)))
        store.save_observations(con, obs)
        store.save_quotes(con, quotes)
        store.save_legs(con, legs)


def main() -> int:  # noqa: PLR0915 - one flat list of independent checks
    # ------------------------------------------------------- look-ahead guard
    src = inspect.getsource(exante)
    tree = ast.parse(src)
    declared_names = set()
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "OUTCOME_FIELDS"):
            continue
        for leaf in ast.walk(node):
            if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                declared_names.add(leaf.value)
    leaked = sorted({
        leaf.value
        for leaf in ast.walk(tree)
        if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str)
        and leaf.value in set(exante.OUTCOME_FIELDS)
    } - declared_names)
    attribute_leak = sorted({
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in exante.OUTCOME_FIELDS
    })
    ok(not leaked and not attribute_leak,
       "no function that forms the ex-ante ratio names an outcome field: the "
       "parser, not a promise, is what stops the peak from re-entering the "
       f"entry decision (found {leaked + attribute_leak})")
    ok("peak_pct" in exante.OUTCOME_FIELDS and "net_pct" in exante.OUTCOME_FIELDS,
       "the forbidden list names the fields that carried the look-ahead the "
       "first time, so the guard covers the mistake that was actually made")

    # -------------------------------------------------------- trailing range
    minutes = [T0 + i * MIN for i in range(10)]
    prices = [100.0 + i for i in range(10)]
    series = exante.TrailingRange(minutes, prices, RANGE_FROM_UNDERLYING)
    span, _ = series.before(minutes[5] + 30.0)
    ok(span == 1.0,
       "the typical minute move is measured from the observations before the "
       "decision and reads the move that was actually there")
    jump = exante.TrailingRange(
        minutes, [100.0, 101.0, 102.0, 103.0, 104.0, 999.0, 999.0, 999.0,
                  999.0, 999.0], RANGE_FROM_UNDERLYING)
    span_excl, _ = jump.before(minutes[5] + 59.0)
    ok(span_excl == 1.0,
       "a move inside the decision's own minute cannot enter the forecast of "
       "that move: the window stops at the start of the minute, not at the tick")
    short, reason = exante.TrailingRange(
        minutes[:2], prices[:2], RANGE_FROM_UNDERLYING).before(minutes[1])
    ok(short is None and reason == NO_RANGE,
       "too few minutes yields no range and says so, rather than a typical "
       "move computed from one observation")
    flat, flat_reason = exante.TrailingRange(
        minutes, [100.0] * 10, RANGE_FROM_UNDERLYING).before(minutes[9])
    ok(flat is None and flat_reason == NO_RANGE,
       "an instrument that did not move has no measurable typical move, and "
       "is refused rather than given a range of zero that would make every "
       "ratio zero and every arm refuse")

    # ------------------------------------------------------------- the ratio
    measured = exante.ratio(delta=0.5, trailing_range_points=8.0,
                            cost_points=1.0, entry_price=100.0)
    ok(measured["evidence"] == EX_ANTE_MEASURED and measured["ratio"] == 4.0
       and measured["expected_move_points"] == 4.0,
       "the ratio is the gate's own arithmetic: |delta| x typical move over "
       "the leg's round-trip cost")
    blank = exante.ratio(delta=None, trailing_range_points=None,
                         cost_points=None, entry_price=None)
    ok(blank["evidence"] == EX_ANTE_UNMEASURED and blank["ratio"] is None
       and blank["expected_move_points"] is None,
       "an unmeasurable leg carries no ratio at all, so no arm can silently "
       "treat a missing input as a zero and refuse the leg on arithmetic that "
       "was never done")
    ok(exante.ratio(delta=None, trailing_range_points=8.0, cost_points=1.0,
                    entry_price=100.0)["reason"] == NO_DELTA
       and exante.ratio(delta=0.5, trailing_range_points=None, cost_points=1.0,
                        entry_price=100.0)["reason"] == NO_RANGE
       and exante.ratio(delta=0.5, trailing_range_points=8.0, cost_points=0.0,
                        entry_price=100.0)["reason"] == NO_COST,
       "each missing input is named, so the unmeasured pool can be read as a "
       "coverage problem rather than as a result")
    ok(exante.leg_delta(FUTURES, None) == 1.0
       and exante.leg_delta(CE, None) is None
       and exante.leg_delta(CE, -0.4) == 0.4,
       "a futures leg is one-for-one by definition and needs no captured "
       "delta; an option leg without one cannot be forecast; a short delta is "
       "a magnitude")
    ok(exante.admits(None, 3.0) is None and exante.admits(3.0, 3.0) is True
       and exante.admits(2.99, 3.0) is False,
       "an unmeasurable leg is neither admitted nor refused: the live gate "
       "does not refuse on economics it could not compute, and folding it "
       "into either pool would measure a different gate")

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "p42.db")
        con = store.connect(db)
        sessions = [f"2026-09-{d:02d}" for d in (1, 2, 3, 4, 7, 8)]
        _seed(con, sessions)

        # ----------------------------------------------------------- the arms
        one = arms.one_session(con, sessions[0])
        ok(one.measured.legs > 0,
           "legs in a seeded session are measurable, so the sweep is testing "
           "the arithmetic rather than an empty pool")
        ok(one.range_sources.get(RANGE_FROM_UNDERLYING, 0) > 0,
           "the range came from the underlying carried on the option quote, "
           "and the payload records which source answered")
        for row in one.arm_rows():
            admitted = row["measured_only"]["legs"]
            refused = row["refused"]["legs"]
            ok(admitted + refused == one.measured.legs,
               f"at {row['multiple']}x every measured leg lands in exactly one "
               "pool: nothing is dropped or double counted")
            ok(row["as_implemented"]["legs"]
               == admitted + one.unmeasured.legs,
               f"at {row['multiple']}x the as-implemented pool is the admitted "
               "legs plus the unmeasurable ones, which is what the gate does")
        counts = [
            r["measured_only"]["legs"] for r in one.arm_rows()
        ]
        ok(all(a >= b for a, b in zip(counts, counts[1:])),
           "a higher multiple never admits more legs than a lower one — the "
           "arms are nested, and a non-monotone count would mean the ratio is "
           "not what is being thresholded")

        results = arms.sweep(con)
        ok([r.session for r in results] == sessions,
           "sessions are swept in date order, because a threshold chosen on "
           "later days and tested on earlier ones is not a forward test")
        pool = arms.pooled(results)
        ok(pool.measured.legs == sum(r.measured.legs for r in results),
           "pooling adds the sessions and invents nothing")

        # ------------------------------------------------ choice and holdout
        picked = validate.choose(results)
        ok(picked["multiple"] in THRESHOLDS or picked["reason"] == INSUFFICIENT,
           "the chooser returns a declared multiple or refuses; it cannot "
           "invent a threshold that was never pre-registered")
        split = validate.split(results)
        ok(set(split["dev_sessions"]).isdisjoint(split["holdout_sessions"]),
           "no session both chooses the multiple and tests it")
        ok(split["dev_sessions"] + split["holdout_sessions"] == sessions
           and max(split["dev_sessions"]) < min(split["holdout_sessions"]),
           "the split is chronological and whole-session: the holdout is the "
           "later days, entire")
        ok(validate.split(results[:2])["status"] == INSUFFICIENT,
           f"fewer than {MIN_SESSIONS_FOR_SPLIT} sessions is refused as a "
           "holdout instead of being reported as one")
        hollow = [arms.SessionResult(f"2026-01-0{i}") for i in range(1, 6)]
        hollow.extend(results)
        hollow_split = validate.split(hollow)
        ok(hollow_split["dev_sessions"] == split["dev_sessions"]
           and hollow_split["holdout_sessions"] == split["holdout_sessions"]
           and hollow_split["sessions_swept"] == len(hollow),
           "sessions that graded no leg are not cut on: five empty days in "
           "front of the evidence would otherwise move the cut and leave the "
           "choosing half with nothing in it, which reads as a holdout and "
           "tests nothing")
        ok(validate.split(hollow[:5] + results[:1])["status"] == INSUFFICIENT,
           "and the floor counts only the sessions that carry evidence, so "
           "one gradeable day behind five empty ones is refused as a holdout")
        forward = validate.walk_forward(results)
        ok(all(step["tested_on"] not in step["chose_on_sessions"]
               for step in forward["steps"]),
           "each walk-forward step is scored on a session that took no part "
           "in choosing the multiple")
        ok(forward["choice_stability_pct"] is not None,
           "the walk-forward reports how often the choice repeated, because a "
           "multiple that keeps moving is noise being fitted, not a finding")

        verdict = validate.verdict(split, forward)
        labels = set(verdict["verdict"].split(" / "))
        ok(labels <= {POSITIVE_IN_HOLDOUT, LOSS_REDUCTION_ONLY,
                      BETTER_THAN_LIVE, NO_BETTER_THAN_LIVE, INSUFFICIENT},
           "the verdict is one of the declared labels")
        ok(not (verdict.get("holdout_mean_net_pct") is not None
                and verdict["holdout_mean_net_pct"] < 0
                and POSITIVE_IN_HOLDOUT in labels),
           "a filter whose holdout mean is still negative is labelled loss "
           "reduction, never positive — cutting a loss is not a profit")
        ok(not (BETTER_THAN_LIVE in labels
                and verdict.get("live_holdout_mean_net_pct") is not None
                and verdict["holdout_mean_net_pct"]
                <= verdict["live_holdout_mean_net_pct"]),
           "'better than live' is only said when the chosen arm actually beat "
           f"{LIVE_MULTIPLE}x on the same holdout sessions")

        negative = validate.verdict(
            {"status": "MEASURED",
             "chosen_on_dev": {"multiple": 5.0},
             "holdout_chosen": {"legs": 500, "mean_net_pct": -1.2},
             "holdout_live_multiple": {"legs": 900, "mean_net_pct": -2.5}},
            {"choice_stability_pct": 100.0})
        ok(LOSS_REDUCTION_ONLY in negative["verdict"]
           and BETTER_THAN_LIVE in negative["verdict"],
           "an arm that loses less than the live gate is both: better than "
           "live and still losing money, and the label says both rather than "
           "letting the first one imply a profit")

        # ------------------------------------- the pre-committed session floor
        ok(MIN_SESSIONS > MIN_SESSIONS_FOR_SPLIT,
           "the floor a verdict needs is higher than the floor a split needs: "
           "four sessions can be cut in two, and four sessions are not "
           "evidence about a gate")
        floor_held = validate.verdict(split, forward)
        ok(floor_held["verdict"] == INSUFFICIENT
           and floor_held["provisional_label"]
           and floor_held["holdout_mean_net_pct"] is not None
           and any(str(MIN_SESSIONS) in reason
                   for reason in floor_held["withheld_because"]),
           f"{len(sessions)} sessions produce a split and still no label: the "
           f"declared floor of {MIN_SESSIONS} sessions was fingerprinted and "
           "then never applied here, which published a verdict on four days, "
           "and the arithmetic is now returned as description instead")
        above_floor = dict(split)
        above_floor["sessions_carrying_evidence"] = MIN_SESSIONS
        ok(validate.verdict(above_floor, forward)["verdict"] != INSUFFICIENT,
           "and the floor is the only thing withholding it: the same split at "
           "the floor is labelled, so the gate is a session count and not a "
           "blanket refusal")

        # ------------------------------------------- one cost basis, or none
        basis = split["cost_basis_across_the_cut"]
        dev_measured = sum(r.measured.legs for r in results
                           if r.session in set(split["dev_sessions"]))
        ok(basis["status"] == BASIS_CONSISTENT
           and sum(basis["dev"].values()) == dev_measured
           and set(basis["holdout"]) == {MEASURED_EXECUTABLE},
           "the split records how the round trip was established on each side "
           "of the cut, leg for leg with the measurable pool")
        mixed_db = os.path.join(tmp, "p42_mixed.db")
        mixed_con = store.connect(mixed_db)
        _seed(mixed_con, sessions, cost_evidence={
            s: "SPREAD_MODELLED" for s in sessions[:3]})
        mixed = validate.split(arms.sweep(mixed_con))
        mixed_basis = mixed["cost_basis_across_the_cut"]
        ok(mixed_basis["status"] == BASIS_CHANGED
           and mixed_basis["dev_dominant"] == "SPREAD_MODELLED"
           and mixed_basis["holdout_dominant"] == MEASURED_EXECUTABLE,
           "a store whose cost basis changed part-way through is caught: the "
           "ratio divides by the round trip, so choosing an arm on modelled "
           "spread and scoring it on a measured one compares two quantities")
        mixed_above = dict(mixed)
        mixed_above["sessions_carrying_evidence"] = MIN_SESSIONS
        mixed_verdict = validate.verdict(
            mixed_above, validate.walk_forward(arms.sweep(mixed_con)))
        ok(mixed_verdict["verdict"] == INSUFFICIENT
           and any("SPREAD_MODELLED" in reason
                   for reason in mixed_verdict["withheld_because"]),
           "and even above the session floor that split carries no label, "
           "naming both bases rather than reporting the difference between "
           "them as the effect of the multiple")
        mixed_con.close()

        # --------------------------------------------------------- the freeze
        first = freeze.fingerprint()
        ok(first == freeze.fingerprint(),
           "hashing the same definition twice gives the same hash")
        ok(len(first["definition"]) == 16 and set(first["components"]) == {
            "ratio", "pools", "choice", "giveback", "declared"},
           "the definition is one hash over named components, so a move is "
           "diagnosable to the part that moved")
        ok(first["declared"]["thresholds"] == list(THRESHOLDS)
           and first["declared"]["live_multiple"] == LIVE_MULTIPLE,
           "the declared numbers are read from the phase module, not restated "
           "here — a second copy would agree with the hash while drifting")
        ok(any("exante" in name for name in first["covers"]["ratio"])
           and any("validate" in name for name in first["covers"]["choice"]),
           "the hash records which callables it covered, so a component "
           "silently ceasing to cover a module is visible")

        def _doc_a(x: int) -> int:
            """One sentence."""
            return x + 1

        def _doc_b(x: int) -> int:
            """A different sentence entirely."""
            # and a comment that did not exist
            return x + 1

        def _body(x: int) -> int:
            """One sentence."""
            return x + 2

        from app.research import fingerprint as fp
        ok(fp.behaviour(_doc_a) == fp.behaviour(_doc_b),
           "a rewritten docstring or a new comment does not move the hash, so "
           "a reader has no reason to start ignoring it")
        ok(fp.behaviour(_doc_a) != fp.behaviour(_body),
           "a changed function body does move the hash — the failure that "
           "produced two different Phase 40 answers under one definition")

        # ------------------------------------------------------ the giveback
        paths, attribs = [], []
        for i in range(4):
            leg_id = f"{sessions[0]}-L{i}"
            attribs.append({
                "leg_id": leg_id, "primary_cause": "GIVEBACK",
                "channel": "GIVEBACK", "gross_pct": 5.0, "spread_pct": 0.4,
                "brokerage_pct": 0.1, "statutory_pct": 0.1,
                "slippage_pct": 0.0, "net_pct": -1.0, "peak_pct": 6.0,
                "time_to_peak_min": 12.0, "giveback_pct": 7.0,
                "evidence": MEASURED_EXECUTABLE, "note": None,
                "time_resolution": "MINUTE",
            })
            for horizon, net in (("5", 1.0), ("30", 4.0),
                                 (SESSION_CLOSE, -1.0)):
                paths.append({
                    "leg_id": leg_id, "horizon": horizon,
                    "gross_pct": net + 1.0, "net_pct": net, "mfe_pct": 6.0,
                    "mae_pct": -1.0, "t1": 1, "t2": 0, "t3": 0,
                    "giveback": 6.0 - net, "evidence": MEASURED_EXECUTABLE,
                })
        store.save_attributions(con, attribs)
        store.save_leg_paths(con, paths)
        decomposed = giveback.decompose(con)
        rows = decomposed["by_channel"]["GIVEBACK"]["horizons"]
        ok([r["horizon"] for r in rows] == ["5", "30", SESSION_CLOSE],
           "horizons are reported in the order they happen, not in the order "
           "SQLite sorts their names")
        ok(decomposed["by_channel"]["GIVEBACK"][
               "legs_where_some_horizon_beat_the_close"] == 4,
           "the decomposition finds that every seeded leg was worth more "
           "before the close than at it, which is the shape the channel name "
           "claims and is measured rather than assumed")
        best = decomposed["by_channel"]["GIVEBACK"]["best_horizon_in_hindsight"]
        ok(sum(b["legs"] for b in best) == 4 and best[0]["horizon"] == "30",
           "the per-leg best horizon is counted once per leg")
        ok("HINDSIGHT" in decomposed["hindsight_note"],
           "the best-horizon table carries its own warning: it is chosen after "
           "the path is known and is not an exit rule")
        close = next(r for r in rows if r["horizon"] == SESSION_CLOSE)
        early = next(r for r in rows if r["horizon"] == "30")
        ok(close["legs_that_reached_profit"] == 4
           and close["returned_to_entry_or_worse_pct"] == 100.0
           and close["turned_negative_pct"] == 100.0
           and early["turned_negative_pct"] == 0.0,
           "the horizon rows say how many legs reached profit and how many "
           "handed all of it back, which is the question the channel name "
           "raises and the mean net alone cannot answer")
        ok(close["mean_retained_share_of_peak_pct"] == round(-100.0 / 6, 2)
           and early["mean_retained_share_of_peak_pct"] == round(400.0 / 6, 2),
           "the retained share is the horizon's net over its own peak, so a "
           "leg that gave everything back reads at or below zero")
        ok(close["t1_pct"] == 100.0 and close["t2_pct"] == 0.0
           and close["legs_with_milestones"] == 4,
           "the target milestones stored on the path are carried through, so "
           "a horizon that reached T1 and gave it back is visible as such")
        peak = decomposed["by_channel"]["GIVEBACK"]["peak"]
        ok(peak["legs"] == 4 and peak["mean_peak_pct"] == 6.0
           and peak["mean_time_to_peak_min"] == 12.0
           and peak["legs_with_a_coarse_peak_time"] == 4,
           "peak, giveback and time-to-peak come from the attribution row, "
           "and the legs whose peak time is only known coarsely are counted "
           "rather than averaged in silently")
        ok("RETAINED_IS_NET_OVER_A_GROSS_PEAK" in decomposed["retained_note"],
           "the retained share discloses that its numerator is charged the "
           "round trip and its denominator is not")

        # ---------------------------------------------------------- the report
        payload = report.run(con, with_giveback=True)
        ok(payload["frozen_definition"] == first["definition"],
           "every figure is stamped with the definition that produced it")
        ok(payload["mode"] == ["RESEARCH_ONLY", "PAPER_ONLY",
                               "RECORD_ONLY_NO_GATE_CHANGE_NO_ORDER_PATH"],
           "the payload states that nothing here touches the gate or the "
           "order path")
        ok(str(len(THRESHOLDS)) in payload["arms_tested_note"],
           "the multiple-testing note counts the arms that were actually "
           "measured, so the reader can discount the best of them")
        text = report.render(payload)
        ok("DESCRIPTIVE, NOT A TEST" in text and "<- live" in text,
           "the printed table marks the pooled figures as descriptive and "
           "shows where the live multiple sits among the arms")
        ok("PER SESSION — WHERE THE EVIDENCE IS" in text
           and all(str(r["measured"]["legs"]) in text
                   for r in payload["per_session"]),
           "the printed answer says per session how many legs were measurable, "
           "because a holdout whose arms read exactly like the pooled ones is "
           "a holdout holding all the evidence and that has to be visible")
        blind = dict(payload)
        blind["chronological_split"] = {
            "status": "MEASURED",
            "dev_sessions": ["A"],
            "holdout_sessions": ["B"],
            "chosen_on_dev": {"multiple": None, "reason": INSUFFICIENT},
            "holdout_all_arms": [{"multiple": m, "legs": 0,
                                  "mean_net_pct": None, "win_pct": None,
                                  "profit_factor": None} for m in THRESHOLDS],
            "holdout_chosen": None,
            "holdout_live_multiple": None,
            "live_multiple": 3.0,
        }
        blind_text = report.render(blind)
        ok("NO ARM MET THE LEG FLOOR THERE" in blind_text
           and "none of them tests anything" in blind_text
           and "Nonex" not in blind_text,
           "when no arm could be chosen on the choosing half the report says "
           "so instead of printing a missing multiple, and refuses to let the "
           "tested half's own arms be read as a test")
        ok(payload["sessions_floor"] > payload["sessions_measured"]
           or payload["verdict"]["verdict"] != INSUFFICIENT,
           "the session floor is reported next to the sessions measured, so a "
           "verdict below the floor cannot be quoted without the caveat")
        ok(all(r["cost_bases"] for r in payload["per_session"]
               if r["measured"]["legs"])
           and MEASURED_EXECUTABLE in text
           and "cost basis" in text,
           "the per-session table says how each session's legs were costed, "
           "because a cut that falls where the costing changed is otherwise "
           "indistinguishable from a cut that found something")
        ok("reported as description because" in text
           and str(MIN_SESSIONS) in text,
           "and the printed answer gives the holdout arithmetic under the "
           "reasons it is not a test, rather than as a verdict")

        # ------------------------------------------------- read-only in fact
        counts_before = {
            t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("raw_observation", "raw_quote", "paper_leg",
                      "leg_path", "leg_attribution")
        }
        ro = store.connect(db)

        def deny(action, *_args):
            return sqlite3.SQLITE_DENY if action in (
                sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE,
            ) else sqlite3.SQLITE_OK

        ro.set_authorizer(deny)
        try:
            again = report.run(ro, with_giveback=True)
            ok(again["verdict"] == payload["verdict"],
               "the whole study runs again with SQLite refusing every write "
               "and reaches the same verdict — read-only is enforced by "
               "execution, not by intention")
        except sqlite3.DatabaseError as exc:  # pragma: no cover - failure path
            ok(False, f"the study attempted a write: {exc}")
        finally:
            ro.set_authorizer(None)
            ro.close()
        counts_after = {
            t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in counts_before
        }
        ok(counts_before == counts_after,
           "not one row changed in the store the study read")
        con.close()

        # ------------------------------------------------------------ the CLI
        artefacts = os.path.join(tmp, "artefacts")
        os.makedirs(artefacts, exist_ok=True)
        payload_path = os.path.join(artefacts, "phase42.json")
        with open(payload_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
        with open(payload_path, encoding="utf-8") as handle:
            reloaded = json.load(handle)
        ok(reloaded["frozen_definition"] == payload["frozen_definition"],
           "the payload is JSON-serialisable, so an artefact can be compared "
           "with a later one rather than re-derived from a printout")
        ok(cli.main(["frozen"]) == 0,
           "the CLI can print the frozen definition without opening the store")

    print()
    print(f"PHASE 42 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
