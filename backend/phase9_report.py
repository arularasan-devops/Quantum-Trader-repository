#!/usr/bin/env python
"""Phase 9 — strike quality, entry quality, tradability and a shadow qualifier.

RESEARCH ONLY. Reads recorded history and writes report files. Phase 8's own
population builder is reused (``phase8_report._population``) so the trades analysed
here are byte-for-byte the trades Phase 8 analysed, and every path, fill, exit and
spread cost comes from Phase 7/8 — Phase 9 adds no second implementation of an R.

The questions, in the order the answers depend on each other:

  1. was a same-side strike ladder even recorded at signal time?      (gates9)
  2. what did the neighbouring strikes look like, at entry time only?  (strikes)
  3. which measurable defect was each BAD_STRIKE loss?                 (badstrike)
  4. which contracts are economically tradable after the book?         (tradability)
  5. does entry quality decide the outcome, net of the book?           (entryq)
  6. what does the confidence number cost when misread as a chance?    (calibration.error)
  7. what would a qualifier have done — in sample and on a holdout?    (shadow)
  8. what does an A+ setup look like, described rather than predicted?  (candidates)

No production behaviour is touched: BUY/WAIT/NO_TRADE, gates and their thresholds,
the confidence formula, stops, targets, R:R, the chase guard, direction logic, the
strike selector, Early Momentum/Early-Early, production exits, broker execution and
AI production behaviour are read-only inputs, and no order can be placed from here.

    .venv/bin/python phase9_report.py --instruments NIFTY,CRUDEOIL \
        --out ~/phase9_report.json --md ~/phase9_report.md --artifacts ~/phase9
"""
from __future__ import annotations

import argparse
import json
import os

from app.config import settings
from app.research.phase7 import gates, paths, policies
from app.research.phase7.dataset import REAL
from app.research.phase8 import calibration, causes, spread
from app.research.phase9 import (
    badstrike,
    candidates,
    entryq,
    gates9,
    shadow,
    strikes,
    tradability,
)
from app.research.phase9.findings9 import contamination
from phase8_report import _population, _trade_row


def _key(row: dict) -> tuple:
    return (row["instrument"], row["symbol"], row["ts_ist"])


def collect(db: str, names: list[str], horizon: int, limit: int,
            sessions_wanted: set[str]) -> dict:
    """Replay the population once and return the intermediates every study reads.

    Split out of ``build`` so a later phase can analyse *the same* trades without
    replaying them a second way. Two replays that disagree by one trade make
    every cross-phase comparison unfalsifiable, which is worse than a longer
    import chain.
    """
    per: dict[str, dict] = {}
    pairs: list[tuple] = []
    trade_rows: list[dict] = []
    signals: list[dict] = []
    signals_by_key: dict[tuple, dict] = {}
    entry_rows: dict[tuple, dict] = {}
    cause_rows: list[dict] = []

    for name in names:
        block = _population(db, name, horizon, limit, sessions_wanted)
        per[name] = block
        real = block["series"]
        for ev in block["events"]:
            fill = policies.simulate(ev, "CONTROL", "A_BASELINE")
            if not fill.entered or ev.path is None:
                continue
            sp = spread.measure(ev, fill, real)
            moved = causes.underlying_excursion(block["candles"], ev.bar, ev.side,
                                                horizon)
            leg = (real.at(fill.entry_ts) or {}).get(ev.symbol)
            row = _trade_row(ev, fill, sp, moved,
                             leg if isinstance(leg, dict) else None)
            cause_rows.append(causes.classify(ev, fill, row, sp, real,
                                              block["candles"], horizon))
            # Phase 9's own work: the ladder at the signal instant, entry-time
            # scoring first, forward outcomes second and in a separate block.
            sig = strikes.per_signal(real, ev, strikes.LADDER_STEPS, horizon)
            selected = next((r for r in sig.get("entry_time_rows", [])
                             if r.get("is_selected")), None)
            key = _key(row)
            signals.append(sig)
            signals_by_key[key] = sig
            if selected is not None:
                entry_rows[key] = selected
                # Carried onto the trade row so tradability, the qualifier and the
                # candidate vector all read the same entry-time facts.
                row["spread_share_of_risk_pct"] = selected["spread_share_of_risk_pct"]
                row["oi"] = selected["oi"]
                row["volume"] = selected["volume"]
                row["room_ratio"] = selected["room_ratio"]
                row["steps_from_atm"] = selected["steps_from_atm"]
                row["premium_band"] = selected["premium_band"]
                row["entry_time_composite"] = selected["entry_time_composite"]
            pairs.append((ev, fill))
            trade_rows.append(row)

    return {"per": per, "pairs": pairs, "trade_rows": trade_rows,
            "signals": signals, "signals_by_key": signals_by_key,
            "entry_rows": entry_rows, "cause_rows": cause_rows}


def build(db: str, names: list[str], horizon: int, limit: int,
          sessions_wanted: set[str]) -> dict:
    coll = collect(db, names, horizon, limit, sessions_wanted)
    per = coll["per"]
    pairs = coll["pairs"]
    trade_rows = coll["trade_rows"]
    signals = coll["signals"]
    signals_by_key = coll["signals_by_key"]
    entry_rows = coll["entry_rows"]
    cause_rows = coll["cause_rows"]

    sessions_ordered = sorted({r["session"] for r in trade_rows})
    n_sessions = len(sessions_ordered)
    worst_missing = max((b["candle"].get("missing_pct") or 0.0
                         for b in per.values()), default=0.0)
    atr_taint = contamination(worst_missing, atr_dependent=True)
    book_taint = contamination(worst_missing, atr_dependent=False)

    ladder_study = strikes.study(signals, n_sessions)
    trad = tradability.study(trade_rows, n_sessions, book_taint)

    bad_rows = badstrike.decompose(cause_rows, signals_by_key)
    total_losses = sum(1 for c in cause_rows if c.get("outcome") == "LOSS")
    bad_study = badstrike.study(bad_rows, total_losses, n_sessions, atr_taint)

    eq_rows = [entryq.row_of(r, ev, entry_rows.get(_key(r)))
               for r, (ev, _) in zip(trade_rows, pairs, strict=True)]
    eq_study = entryq.study(eq_rows, n_sessions, atr_taint)

    calib_error = calibration.error(pairs, n_sessions)

    for r in trade_rows:
        r["phase9_qualification"], r["phase9_qualification_trigger"] = (
            shadow.qualify_detail(r, entry_rows.get(_key(r)), trad["proposal"]))
    shadow_block = shadow.compare(trade_rows, sessions_ordered)

    cand_block = candidates.study(trade_rows, entry_rows, signals_by_key, n_sessions)

    all_cand_rows = [row for s in signals for row in s.get("entry_time_rows", [])]
    with_book = sum(1 for row in all_cand_rows if row.get("bid") is not None)
    smallest_cell = min(
        [b["primary"] for b in bad_study.get("table", []) if b["primary"]]
        + [b["n"] for b in trad["by_class"].values() if b.get("n")]
        or [0])
    p9_gates = gates9.evaluate(
        resolved=len(trade_rows), sessions=n_sessions,
        ladder_coverage_pct=ladder_study["selected_leg_in_ladder_pct"],
        median_candidates=ladder_study.get("median_candidates_per_signal"),
        book_coverage_pct=100.0 * with_book / max(1, len(all_cand_rows)),
        holdout_sessions=len(
            shadow_block["chronological_holdout"]["holdout_sessions"]),
        missing_bar_pct=worst_missing, smallest_cell=smallest_cell)

    best_chain = max((b["chain"] for b in per.values()),
                     key=lambda c: c.get(REAL, {}).get("snapshots") or 0, default={})
    p7_gates = gates.evaluate(best_chain, {"missing_pct": worst_missing},
                              len(trade_rows), 0, 0)

    return {
        "phase": 9,
        "scope": "RESEARCH ONLY — replay of recorded history. Production BUY/WAIT/"
                 "NO_TRADE, gates, thresholds, the confidence formula, stops, "
                 "targets, R:R, strike selection, direction logic, the chase guard, "
                 "Early Momentum/Early-Early, production exits, broker execution and "
                 "AI production behaviour are untouched; no order can be placed from "
                 "this report and no threshold in it is applied anywhere.",
        "reuses": "phase8_report._population for the population, phase7.paths / "
                  "policies for every path, fill, exit, MFE, MAE and chase, and "
                  "phase8.spread.measure for every cost. Phase 9 adds the strike "
                  "ladder, the BAD_STRIKE decomposition and the shadow qualifier.",
        "db": db,
        "instruments": names,
        "sessions_analysed": sessions_ordered,
        "resolved_trades": len(trade_rows),
        "phase7_gates": p7_gates,
        "phase9_gates": p9_gates,
        "data_contamination": {"atr_dependent_blocks": atr_taint,
                              "book_only_blocks": book_taint},
        "part4_5_strike_ladder": ladder_study,
        "part3_8_bad_strike": bad_study,
        "part9_12_tradability": trad,
        "part6_7_entry_quality": eq_study,
        "part11_calibration_error": calib_error,
        "part16_17_shadow_qualifier": shadow_block,
        "part18_candidates": cand_block,
        "per_instrument": {
            name: {
                "real_snapshots": b["chain"].get(REAL, {}).get("snapshots", 0),
                "strikes_per_snapshot": b["chain"].get(REAL, {}).get(
                    "strikes_per_snapshot"),
                "missing_bar_pct": b["candle"].get("missing_pct"),
                "resolved": sum(1 for r in trade_rows if r["instrument"] == name),
                "median_ladder_candidates": policies.median([
                    float(s["candidates_available"]) for s in signals
                    if s.get("instrument") == name and s.get("candidates_available")]),
                "skipped": b.get("skipped"),
            } for name, b in per.items()},
        "bad_strike_rows": bad_rows,
        "entry_quality_rows": eq_rows,
        "strike_ladder_signals": signals,
        "qualification_rows": [
            {"instrument": r["instrument"], "symbol": r["symbol"],
             "ts_ist": r["ts_ist"], "session": r["session"],
             "tradability": r.get("tradability"),
             "phase9_qualification": r["phase9_qualification"],
             "phase9_qualification_trigger": r["phase9_qualification_trigger"],
             "realised_r": r["realised_r"], "net_r": r["net_r"]}
            for r in trade_rows],
        "production_confirmation": {
            "buy_wait_no_trade_unchanged": True,
            "gates_and_thresholds_unchanged": True,
            "confidence_formula_unchanged": True,
            "stops_targets_rr_unchanged": True,
            "chase_guard_unchanged": True,
            "direction_logic_unchanged": True,
            "strike_selector_unchanged": True,
            "early_momentum_unchanged": True,
            "production_exits_unchanged": True,
            "ai_paper_only": True,
            "real_money_execution_disabled": True,
            "phase9_rule_wired_into_signal_tab": False,
            "tradability_thresholds_applied_to_production": False,
            "how_this_is_enforced": "Phase 9 lives in app/research/phase9 and is "
                                    "imported by phase9_report.py and its smoke test "
                                    "only; no production module imports it, it holds "
                                    "no order-placing code path, and _smoke_phase9 "
                                    "asserts the absence of a production import",
        },
        "limits": [
            "premiums are ~60s snapshot closes, so a candidate leg's intrabar touch "
            "is invisible and every ladder outcome is a snapshot outcome",
            "the ladder is the RECORDED ladder: an instrument whose window is 4 "
            "strikes wide cannot answer whether a better strike existed, and the "
            "LADDER_DEPTH gate is what says so",
            "exchange-to-receive latency is not recorded anywhere in this schema; "
            "chain age is the available proxy and is reported as such, never as "
            "latency",
            "room, extension and expected move derive from ATR, and this dataset's "
            "one-minute bars are incomplete, so those blocks are stamped "
            "DATA_CONTAMINATED rather than dropped",
            "the shadow qualifier is scored on the trades it was derived from unless "
            "the holdout gate passes; on this dataset it does not, so the comparison "
            "is IN_SAMPLE_ONLY and is not evidence",
            "brokerage remains the "
            f"₹{policies.COST_PER_ROUNDTRIP_OPTIONS:.0f}/round-trip assumption; it is "
            "never added into the same number as the measured spread",
            "no predicted net R is emitted anywhere: a prediction is a model, and "
            "this phase does not build one",
        ],
    }


def artifacts(report: dict) -> dict[str, object]:
    """The spec's per-topic files, derived from the single report dict.

    Derived rather than recomputed on purpose: two code paths producing two slightly
    different numbers for the same question is worse than one file too few.
    """
    return {
        "bad_strike_analysis.json": {
            "summary": report["part3_8_bad_strike"],
            "rows": report["bad_strike_rows"],
        },
        "strike_candidates.json": {
            "summary": report["part4_5_strike_ladder"],
            "signals": report["strike_ladder_signals"],
        },
        "entry_quality.json": {
            "summary": report["part6_7_entry_quality"],
            "rows": report["entry_quality_rows"],
        },
        "tradability.json": report["part9_12_tradability"],
        "confidence_calibration.json": report["part11_calibration_error"],
        "baseline_vs_phase9.json": {
            "comparison": report["part16_17_shadow_qualifier"],
            "rows": report["qualification_rows"],
        },
    }


def _n(v) -> str:
    return "—" if v is None else str(v)


def render_md(report: dict) -> str:
    lad = report["part4_5_strike_ladder"]
    bad = report["part3_8_bad_strike"]
    trad = report["part9_12_tradability"]
    eq = report["part6_7_entry_quality"]
    cal = report["part11_calibration_error"]
    sh = report["part16_17_shadow_qualifier"]
    cand = report["part18_candidates"]
    L: list[str] = []
    A = L.append

    A("# Phase 9 — strike quality, entry quality, tradability (research only)")
    A("")
    A(f"Sessions analysed: **{', '.join(report['sessions_analysed']) or 'none'}** · "
      f"resolved trades: **{report['resolved_trades']}** · instruments: "
      f"{', '.join(report['instruments'])}")
    A("")
    A("Labels are Phase 8's: `OBSERVATION` describes this sample, `HYPOTHESIS` is a "
      "relationship worth testing, `REQUIRES MORE DATA` means the cell is too small "
      "to read, `VALIDATED` needs the gates to pass **and** an unseen chronological "
      "holdout to agree. Phase 9 adds `DATA_CONTAMINATED` for blocks derived from ATR "
      "while the one-minute series is incomplete — those rows are labelled, never "
      "dropped.")
    A("")

    A("## Acceptance gates")
    A("")
    A(f"Phase 9 status: **{report['phase9_gates']['status']}** — failed: "
      f"{', '.join(report['phase9_gates']['failed']) or 'none'}")
    A("")
    A("| gate | need | have | pass | a failure forbids |")
    A("| --- | --- | --- | --- | --- |")
    for g in report["phase9_gates"]["gates"]:
        A(f"| {g['gate']} | {g['need']} | {_n(g['have'])} | "
          f"{'PASS' if g['pass'] else 'FAIL'} | {g['forbids']} |")
    A("")
    taint = report["data_contamination"]["atr_dependent_blocks"]
    A(f"ATR-derived blocks: **{taint['status']}** — {taint['reading']}")
    A("")

    A("## 1. Was BAD_STRIKE really the strike?")
    A("")
    if not bad.get("bad_strike_losses"):
        A("No loss carried BAD_STRIKE as its primary cause in this sample.")
    else:
        A(f"{bad['bad_strike_losses']} of {bad['total_losses']} losses "
          f"({bad['share_of_all_losses_pct']}%) were classified BAD_STRIKE by Phase 8. "
          f"Decomposed by the first measurable defect, in a fixed precedence:")
        A("")
        A("| sub-cause | primary | % of BAD_STRIKE | also flagged | label |")
        A("| --- | --- | --- | --- | --- |")
        for r in bad["table"]:
            if not r["primary"] and not r["flagged"]:
                continue
            A(f"| {r['sub_cause']} | {r['primary']} | {r['pct_of_bad_strike']} | "
              f"{r['flagged']} | {r['label']} |")
        A("")
        A(f"Top sub-cause: **{_n(bad['top_sub_cause'])}**. The residual "
          f"OPTION_UNDERLYING_DIVERGENCE — the only part that indicts contract "
          f"*selection* rather than execution economics, liquidity, the feed or "
          f"reachability — is **{bad['residual_divergence']} of "
          f"{bad['bad_strike_losses']} ({bad['residual_pct']}%)** of the BAD_STRIKE "
          f"group.")
        A("")
        alt = bad["entry_time_alternatives"]
        A(f"On **{alt['losses_where_nothing_scored_better_pct']}%** of these losses "
          f"nothing in the recorded ladder scored better at entry time — on those, "
          f"the selector had already picked the best available leg on these "
          f"components (median {_n(alt['median_better_scoring_legs_available'])} "
          f"better-scoring legs available across the group). {alt['reading']}")
        A("")

    A("## 2. The strike ladder at signal time")
    A("")
    A(f"{lad['signals_with_ladder']} of {lad['signals']} signals had a same-side "
      f"ladder recorded; median **{_n(lad['median_candidates_per_signal'])}** "
      f"candidates within ±{lad['ladder_steps']} steps of ATM. The chosen leg itself "
      f"was inside that window on **{lad['selected_leg_in_ladder_pct']}%** of signals "
      f"— the rest were bought further out than ±{lad['ladder_steps']} strikes and have "
      f"no comparable neighbours here.")
    A("")
    sc = lad["entry_time_scoring"]
    A(f"Something scored better than the chosen leg on "
      f"**{sc['signals_where_something_scored_better_pct']}%** of signals "
      f"(median {_n(sc['median_better_available_count'])} better-scoring legs). "
      f"Scoring is a percentile rank inside the same-instant ladder — "
      f"{sc['composite']}")
    A("")
    A("### Premium bands — what the cheap legs actually looked like at signal time")
    A("")
    A("| band | legs quoted | chosen by engine | median spread % of premium | "
      "median spread % of risk | median |delta| | median OI | no book |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for band, b in lad["by_premium_band_entry_time"].items():
        if not b.get("legs_quoted"):
            A(f"| {band} | 0 | — | — | — | — | — | — |")
            continue
        A(f"| {band} | {b['legs_quoted']} | {b['times_selected_by_engine']} | "
          f"{_n(b['median_spread_pct_of_premium'])} | "
          f"{_n(b['median_spread_share_of_risk_pct'])} | "
          f"{_n(b['median_abs_delta'])} | {_n(b['median_oi'])} | "
          f"{b['legs_with_no_book_pct']}% |")
    A("")
    A("Outcome of every ladder leg by band, replayed with the risk fraction and R:R "
      "the engine chose for the leg it actually bought:")
    A("")
    A("| band | measurable | target-before-stop | gross R | net R | PF | label |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for band, b in lad["by_premium_band_outcome"].items():
        if not b.get("measurable"):
            continue
        A(f"| {band} | {b['measurable']} | {_n(b.get('target_before_stop_pct'))}% | "
          f"{_n(b.get('gross_expectancy_r'))} | {_n(b.get('net_expectancy_r'))} | "
          f"{_n(b.get('profit_factor'))} | {b['label']} |")
    A("")
    A(f"{lad['hindsight_note']}")
    A("")

    A("## 3. Tradability — is the contract worth trading at all?")
    A("")
    A(f"Status: **PROPOSED_ONLY**. {trad['proposal'].get('warning', '')}")
    A("")
    A("| instrument | n | median spread % of risk | p90 | book wider than stop | "
      "net R | dominant proposed class |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for name, b in trad["by_instrument"].items():
        d = b["spread_share_of_risk_pct"]
        A(f"| {name} | {b['n']} | {_n(d.get('p50'))} | {_n(d.get('p90'))} | "
          f"{b['book_wider_than_stop_pct']}% | {_n(b['net_expectancy_r'])} | "
          f"{b['dominant_class']} |")
    A("")
    if trad["not_tradable_after_spread"]:
        A(f"Book wider than the stop on at least half the trades: "
          f"**{', '.join(trad['not_tradable_after_spread'])}**. {trad['reading']}")
    if trad["economically_tradable_after_spread"]:
        A(f"Net positive after the recorded book in this sample: "
          f"**{', '.join(trad['economically_tradable_after_spread'])}**.")
    A("")

    A("## 4. Entry quality against outcome")
    A("")
    A("| class | n | win rate | gross R | net R | net PF | median MFE capture | label |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for cls in eq["classes"]:
        b = eq["by_class"][cls]
        A(f"| {cls} | {b['n']} | {_n(b.get('win_rate_pct'))}% | "
          f"{_n(b.get('gross_expectancy_r'))} | {_n(b.get('net_expectancy_r'))} | "
          f"{_n(b.get('net_profit_factor'))} | "
          f"{_n(b.get('median_mfe_capture_pct'))}% | {b['label']} |")
    A("")
    um = eq["unmeasurable_entry_quality"]
    if um["n"]:
        A(f"UNKNOWN is **{um['n']} trades ({um['pct_of_resolved']}%)**, concentrated in "
          + ", ".join(f"{k} {v}" for k, v in um["by_instrument"].items())
          + f" — {um['cause']}. {um['reading']}.")
        A("")
    oc = eq["ordering_check"]
    A(f"Ordering across the measurable classes "
      f"({', '.join(oc['comparable_classes'])}): **{oc['verdict']}**"
      + ("" if not oc["improvements_against_expectation"] else
         " — improves at " + ", ".join(
             f"{b['from']}\u2192{b['to']} (+{b['gain']}R)"
             for b in oc["improvements_against_expectation"]))
      + f". {oc['reading']}")
    A("")

    A("## 5. What the confidence number costs when misread as a probability")
    A("")
    if not cal.get("measurable"):
        A(f"Not measurable: {cal.get('reason')}")
    else:
        A(f"Brier **{cal['brier_score']}** against **{cal['brier_of_base_rate_forecast']}** "
          f"for a flat forecast of the base rate ({cal['base_rate']}) — "
          f"{'better' if cal['beats_base_rate_forecast'] else 'WORSE'} than a constant "
          f"guess. Expected calibration error **{cal['expected_calibration_error']}**, "
          f"worst bucket **{cal['max_calibration_error']}**.")
        A("")
        A("| bucket | n | stated as probability | observed target-before-stop | gap |")
        A("| --- | --- | --- | --- | --- |")
        for r in cal["buckets"]:
            A(f"| {r['bucket']} | {r['n']} | {r['stated_as_probability']} | "
              f"{r['observed_target_before_stop']} | {r['gap']} |")
        A("")
        A(f"{cal['reading']}.")
    A("")

    A("## 6. Shadow qualifier vs baseline")
    A("")
    A(f"Status: **{sh['status']}** — {sh['holdout_sessions_available']} holdout "
      f"session(s) of {sh['holdout_sessions_required']} required.")
    A("")
    A(f"Qualified {sh['qualification_counts']['QUALIFIED']} of "
      f"{sh['baseline']['n']} baseline trades ({sh['qualified_pct']}%). Rejections: "
      + ", ".join(f"{k} {v}" for k, v in sh["qualification_counts"].items()
                  if k != "QUALIFIED" and v))
    A("")
    A("| label | what actually triggered it | n |")
    A("| --- | --- | --- |")
    for lbl, trigs in sh["qualification_triggers"].items():
        for trig, count in trigs.items():
            A(f"| {lbl} | {trig} | {count} |")
    A("")
    A("| population | n | win rate | gross R | net R | net PF | total net R |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for name, b in (("baseline", sh["baseline"]), ("qualified only", sh["qualified_only"])):
        A(f"| {name} | {b['n']} | {_n(b.get('win_rate_pct'))}% | "
          f"{_n(b.get('gross_expectancy_r'))} | {_n(b.get('net_expectancy_r'))} | "
          f"{_n(b.get('net_profit_factor'))} | {_n(b.get('total_net_r'))} |")
    A("")
    A(f"Missed winners **{sh['missed_winners']['n']}** "
      f"({sh['missed_winners']['total_net_r_given_up']}R given up) against avoided "
      f"losers **{sh['avoided_losers']['n']}** "
      f"({sh['avoided_losers']['total_net_r_avoided']}R). "
      f"{sh['avoided_losers']['caveat']}.")
    A("")
    A(f"Holdout: {sh['chronological_holdout']['reading']}. Label: "
      f"**{sh['label']}**.")
    A("")

    A("## 7. A+ candidate objects")
    A("")
    A(f"{cand['candidates_described']} described, {cand['shown']} shown in the JSON. "
      f"{cand['omitted_field']}")
    A("")

    A("## What this changes, and what it does not")
    A("")
    A("**Immediately actionable as observation:**")
    A("")
    if trad["not_tradable_after_spread"]:
        A(f"- the recorded book on **{', '.join(trad['not_tradable_after_spread'])}** "
          f"exceeded the intended risk on at least half their trades. No model or "
          f"signal improvement can recover that: either the risk unit is too small "
          f"for those contracts or they should not be traded with this stop "
          f"discipline. This is arithmetic, not a strategy claim.")
    A(f"- BAD_STRIKE is mostly **{_n(bad.get('top_sub_cause'))}**, and only "
      f"{bad.get('residual_pct', 0)}% of it is unexplained divergence — so the "
      f"evidence does not point at the strike selector as the thing to change.")
    A("- the ladder is measurable where it was recorded; where it was not (narrow "
      "recorded windows), widening the recorded strike window is a *recording* "
      "change with no effect on trading logic.")
    stale = sh["qualification_triggers"].get("REJECTED_DATA", {})
    stale_n = sum(v for k, v in stale.items() if k.startswith("data_flag="))
    if stale_n:
        A(f"- **{stale_n} of {sh['baseline']['n']} "
          f"({round(100.0 * stale_n / max(1, sh['baseline']['n']), 1)}%) of these "
          f"signals were already flagged stale or absent by the engine's own data "
          f"check at signal time.** That is a feed problem, not a strategy problem, "
          f"and it is upstream of every other number in this report.")
    A("")
    A("**Needs 20+ sessions and 5+ holdout sessions:**")
    A("")
    A("- every tradability threshold in this report (they are terciles of two "
      "sessions and would fit themselves);")
    A("- the shadow qualifier's improvement, which is in-sample here;")
    A("- any entry-class rejection rule;")
    A("- any per-instrument or per-expiry sub-cause ranking.")
    A("")
    A("**Should not be modelled yet:**")
    A("")
    A("- no predicted net R, and no strike-quality model: the composite in this "
      "report is equal-weighted and explicitly arbitrary, which is safer than a "
      "weighting fitted to two contaminated sessions;")
    A("- nothing ATR-derived while the one-minute series is incomplete;")
    A("- greeks-derived features until the degenerate deep-ITM legs are fixed at the "
      "recording end.")
    A("")

    A("## Production confirmation")
    A("")
    for k, v in report["production_confirmation"].items():
        if isinstance(v, bool):
            A(f"- {k.replace('_', ' ')}: **{'yes' if v else 'no'}**")
    A(f"- enforcement: {report['production_confirmation']['how_this_is_enforced']}")
    A("- no production change is authorised by this phase")
    A("")
    A("## Limits")
    A("")
    for line in report["limits"]:
        A(f"- {line}")
    A("")
    A(f"status={report['phase9_gates']['status']} "
      f"resolved={report['resolved_trades']} "
      f"sessions={len(report['sessions_analysed'])} "
      f"bad_strike_losses={bad.get('bad_strike_losses', 0)} "
      f"top_sub_cause={_n(bad.get('top_sub_cause'))} "
      f"residual_pct={bad.get('residual_pct', 0)} "
      f"shadow={sh['status']} "
      f"failed_gates={','.join(report['phase9_gates']['failed']) or 'none'}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=os.path.join(settings.data_dir, "history.db"))
    ap.add_argument("--instruments", default="")
    ap.add_argument("--session", default="",
                    help="comma-separated IST dates (YYYY-MM-DD); default all")
    ap.add_argument("--horizon", type=int, default=paths.HORIZON_BARS)
    ap.add_argument("--limit", type=int, default=0,
                    help="most recent N candles per instrument, 0 = all")
    ap.add_argument("--out", default="")
    ap.add_argument("--md", default="")
    ap.add_argument("--artifacts", default="",
                    help="directory for the per-topic JSON files")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"no history db at {args.db}")
    names = [x.strip().upper() for x in args.instruments.split(",") if x.strip()]
    if not names:
        from app.research.phase7.dataset import instruments_with_chains
        names = instruments_with_chains(args.db)[:5]
    wanted = {x.strip() for x in args.session.split(",") if x.strip()}

    report = build(args.db, names, args.horizon, args.limit, wanted)
    md = render_md(report)
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
    if args.md:
        with open(args.md, "w") as fh:
            fh.write(md + "\n")
    if args.artifacts:
        os.makedirs(args.artifacts, exist_ok=True)
        for name, payload in artifacts(report).items():
            with open(os.path.join(args.artifacts, name), "w") as fh:
                json.dump(payload, fh, indent=2, default=str)
    print(md)


if __name__ == "__main__":
    main()
