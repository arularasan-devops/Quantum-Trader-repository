"""Phase 10 report — feed quality, score calibration, A+ validation. RESEARCH ONLY.

Run:
    .venv/bin/python phase10_report.py --db data/history.db --out phase10_report.json

Nothing this script produces can reach production. It reuses Phase 9's population
verbatim (``phase9_report.collect``) so that every trade analysed here is the same
trade Phase 9 analysed — a second replay that disagreed by one trade would make the
cross-phase comparison meaningless — and adds only measurement on top.

The report answers §16's questions in order and is written so a failing answer is
as legible as a passing one. Several answers on this dataset are "cannot be
answered", and those are the important ones: they say which session of recording
would convert them into a measurement.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.config import settings
from app.research.phase10 import (
    aplus,
    capacity,
    capture10,
    export,
    freshness,
    gates10,
    paperbook,
    premiums,
    probability,
    score,
)
from app.research.phase7 import dataset, gates, paths, policies
from app.research.phase9 import candidates, entryq, strikes, tradability
from app.research.phase9.findings9 import contamination
from phase8_report import REAL
from phase9_report import _key, collect

QUESTIONS = (
    "did the deep/broad tier split improve bar completeness and stale rate?",
    "what is the measured bar completeness per instrument?",
    "what share of production BUYs fired on stale data?",
    "does the SIGNAL SCORE order outcomes?",
    "can the SIGNAL SCORE be mapped to an honest probability?",
    "does A+ filtering beat baseline on sessions it never saw?",
    "which instruments are habitually untradable on spread?",
    "are cheap-premium options being excluded, and should they be?",
    "how much of the favourable excursion is kept?",
    "is the shadow paper book complete without a validated model?",
    "what is promoted to production by this phase?",
)


def recorded_instrument_count(db: str) -> int:
    """How many instruments the recorder actually collected candles for.

    The load that produced this dataset's missing bars is the recorder's whole
    watchlist, which is larger than the set any one replay analyses.
    """
    conn = dataset.connect(db)
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT instrument) FROM candles").fetchone()
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else 0


def build(db: str, names: list[str], horizon: int, limit: int,
          sessions_wanted: set[str], *, deep_watchlist: list[str],
          data_dir: Path | None) -> dict:
    coll = collect(db, names, horizon, limit, sessions_wanted)
    per = coll["per"]
    trade_rows = coll["trade_rows"]
    signals = coll["signals"]
    signals_by_key = coll["signals_by_key"]
    entry_rows = coll["entry_rows"]
    pairs = coll["pairs"]

    sessions_ordered = sorted({r["session"] for r in trade_rows})
    n_sessions = len(sessions_ordered)
    worst_missing = max((b["candle"].get("missing_pct") or 0.0
                         for b in per.values()), default=0.0)
    atr_taint = contamination(worst_missing, atr_dependent=True)
    book_taint = contamination(worst_missing, atr_dependent=False)

    # ---- §3 data quality, measured from the replayed window -------------------
    dq = {}
    for name, b in per.items():
        candle = b["candle"]
        rows = [r for r in trade_rows if r["instrument"] == name]
        stale = sum(1 for r in rows if r.get("data_flag") in ("STALE", "NO_DATA"))
        dq[name] = {
            "bars_expected_in_sessions": candle.get("expected_bars_in_sessions"),
            "bars_present": candle.get("bars"),
            "missing_bars": candle.get("missing_bars"),
            "missing_bar_pct": candle.get("missing_pct"),
            "duplicate_bars": candle.get("duplicate_timestamps"),
            "out_of_order_bars": candle.get("out_of_order"),
            "largest_gaps_bars": candle.get("largest_gaps_bars"),
            "chain_snapshots": b["chain"].get(REAL, {}).get("snapshots", 0),
            "resolved_trades": len(rows),
            "stale_decisions": stale,
            "stale_pct": round(100.0 * stale / len(rows), 1) if rows else None,
            "median_chain_age_sec": policies.median(
                [r["chain_age_sec"] for r in rows if r.get("chain_age_sec") is not None]),
            "meets_completeness_gate": bool(
                (candle.get("missing_pct") or 100.0) <= gates.MAX_MISSING_BAR_PCT),
            "tier_when_recorded": "DEEP (every session in this dataset ran the full "
                                  "watchlist, so no instrument was BROAD)",
        }

    # ---- §4 freshness --------------------------------------------------------
    fresh_rows = [freshness.row_of(r) for r in trade_rows]
    fresh_block = freshness.study(fresh_rows, n_sessions)

    # ---- §5-6 signal score ---------------------------------------------------
    score_block = score.study(trade_rows, n_sessions)

    # ---- §7 calibrated probability ------------------------------------------
    prob_block = probability.build(trade_rows, sessions_ordered)

    # ---- §8-9 A+ shadow ------------------------------------------------------
    trad = tradability.study(trade_rows, n_sessions, book_taint)
    inst_classes = aplus.instrument_tradability(trade_rows)
    predictive = bool(score_block.get("is_predictive"))
    for r in trade_rows:
        r["phase10_qualification"], r["phase10_qualification_trigger"] = (
            aplus.qualify_detail(r, entry_rows.get(_key(r)), trad["proposal"],
                                 instrument_classes=inst_classes,
                                 score_component_enabled=predictive,
                                 min_signal_score=None))
    aplus_block = aplus.compare(trade_rows, sessions_ordered,
                                score_is_predictive=predictive)

    # ---- §10-11 spread and cheap premiums -----------------------------------
    prem_block = premiums.study(signals, trade_rows, n_sessions)

    # ---- §12 profit capture --------------------------------------------------
    cap_block = capture10.study(trade_rows, n_sessions, worst_missing)

    # ---- §13 shadow paper book ----------------------------------------------
    # A mapping that was fitted but lost to a constant base-rate forecast on the
    # unseen block is not a model the book may lean on. Only a mapping that beat
    # the constant on the holdout counts as available here; anything else is
    # NO_VALIDATED_MODEL, which empties the probability column and nothing else.
    model_available = bool(prob_block.get("holdout_beats_constant_forecast"))
    book_block = paperbook.book(
        trade_rows,
        model_status=(prob_block["status"] if model_available
                      else paperbook.NO_VALIDATED_MODEL))

    # ---- §14 export ----------------------------------------------------------
    export_block = ({"planned": False,
                     "reason": "--data-dir not supplied, so the nightly export was "
                               "described but not planned against a real directory",
                     "members_specified": [*export.JSONL_MEMBERS,
                                           *export.JSON_MEMBERS]}
                    if data_dir is None else
                    {"planned": True, **export.plan(data_dir)})

    # ---- §15 capacity --------------------------------------------------------
    # The before-session's cost is set by the watchlist the *recorder* ran, not by
    # the handful of instruments this replay analyses: using len(per) here would
    # understate the load whose effects — missing bars, stale ticks — are the whole
    # point of the comparison.
    recorded_watchlist = recorded_instrument_count(db) or len(per)
    before = capacity.snapshot(
        label=capacity.BEFORE,
        instruments_deep=recorded_watchlist,
        instruments_broad=0,
        warm_calls_per_cycle=recorded_watchlist,
        chain_calls_per_cycle=recorded_watchlist,
        missing_bar_pct=round(worst_missing, 2),
        stale_signal_pct=fresh_block["overall"].get("stale_pct"))
    cap_compare = capacity.compare(before, None,
                                   deep_count=len(deep_watchlist) or 8)

    # ---- gates ---------------------------------------------------------------
    ladder_study = strikes.study(signals, n_sessions)
    all_cand_rows = [row for s in signals for row in s.get("entry_time_rows", [])]
    with_book = sum(1 for row in all_cand_rows if row.get("bid") is not None)
    cells = [c["n"] for c in (score_block.get("buckets_quantile")
                              or score_block.get("buckets_fixed_phase8") or [])
             if c.get("n")]
    smallest_cell = min(cells) if cells else 0
    hold = aplus_block["chronological_holdout"]
    g = gates10.evaluate(
        resolved=len(trade_rows), sessions=n_sessions,
        missing_bar_pct=worst_missing,
        book_coverage_pct=100.0 * with_book / max(1, len(all_cand_rows)),
        ladder_coverage_pct=ladder_study["selected_leg_in_ladder_pct"],
        holdout_sessions=len(hold["holdout_sessions"]),
        smallest_cell=smallest_cell,
        calibration_train_rows=prob_block["train_rows"],
        calibration_holdout_rows=prob_block["holdout_rows"],
        sessions_at_deep_watchlist=0)

    eq_rows = [entryq.row_of(r, ev, entry_rows.get(_key(r)))
               for r, (ev, _) in zip(trade_rows, pairs, strict=True)]

    answers = {
        QUESTIONS[0]: cap_compare["verdict"],
        QUESTIONS[1]: (
            f"worst instrument {worst_missing:.1f}% of one-minute bars missing "
            f"against a {gates.MAX_MISSING_BAR_PCT}% gate; "
            f"{sum(1 for v in dq.values() if v['meets_completeness_gate'])} of "
            f"{len(dq)} instrument(s) meet it"),
        QUESTIONS[2]: fresh_block["reading"],
        QUESTIONS[3]: score_block.get("answer"),
        QUESTIONS[4]: prob_block.get("reason") or prob_block.get("reading"),
        QUESTIONS[5]: aplus_block["answer_to_does_a_plus_beat_baseline"],
        QUESTIONS[6]: ", ".join(
            f"{k}: {v['class'] or 'unmeasured'} "
            f"(median {v['median_spread_share_of_risk_pct']}% of risk, "
            f"{v['trades']} trades)" for k, v in inst_classes.items()) or "none",
        QUESTIONS[7]: prem_block["cheap_premium_answer"],
        QUESTIONS[8]: cap_block["diagnosis"],
        QUESTIONS[9]: book_block["independence"],
        QUESTIONS[10]: "nothing. No gate, threshold, formula, selector, exit or "
                       "execution path is changed by Phase 10, and every threshold "
                       "it derived is stamped PROPOSED_ONLY",
    }

    return {
        "phase": 10,
        "scope": "RESEARCH / SHADOW / PAPER ONLY — replay of recorded history plus "
                 "live observability. Production BUY/WAIT/NO_TRADE, the gates and "
                 "their thresholds, the confidence formula, stops, targets, R:R, the "
                 "chase guard, direction logic, the strike selector, the premium "
                 "floor, Early Momentum/Early-Early, production exits and the broker "
                 "order path are untouched. No order can be placed from this report "
                 "and no threshold in it is applied anywhere.",
        "reuses": "phase9_report.collect for the population (the same trades Phase 9 "
                  "analysed, not a second replay), phase7.paths/policies for every "
                  "path, fill, exit, MFE and MAE, phase8.spread.measure for every "
                  "cost, and phase9.shadow.metrics for every outcome block.",
        "db": db,
        "instruments": names,
        "sessions_analysed": sessions_ordered,
        "resolved_trades": len(trade_rows),
        "phase10_gates": g,
        "data_contamination": {"atr_dependent_blocks": atr_taint,
                               "book_only_blocks": book_taint},
        "part1_2_tier_architecture": {
            "configured_deep_watchlist": deep_watchlist,
            "chosen_by": "operator configuration (QT_DEEP_WATCHLIST). Phase 10 does "
                         "not choose the deep instruments: §2 reserves that decision, "
                         "and a list invented in research would become the de facto "
                         "configuration",
            "instruments_recorded_in_this_dataset": len(per),
            "tier_split_active_during_recording": False,
            "mechanism": "app/market/tiers.py routes feed capture only. No gate, "
                         "threshold, signal, strike or order path reads an "
                         "instrument's tier",
        },
        "part3_data_quality": {
            "per_instrument": dq,
            "gate_pct": gates.MAX_MISSING_BAR_PCT,
            "worst_missing_bar_pct": round(worst_missing, 2),
            "instruments_meeting_gate": sum(
                1 for v in dq.values() if v["meets_completeness_gate"]),
            "live_endpoint": "GET /api/data-quality-summary (production "
                             "observability; contaminated instruments are listed, "
                             "never filtered out)",
            "contaminated_rows_hidden": False,
        },
        "part4_signal_freshness": fresh_block,
        "part5_6_signal_score": score_block,
        "part7_calibrated_probability": prob_block,
        "part8_9_a_plus_shadow": aplus_block,
        "part8_instrument_tradability": inst_classes,
        "part10_11_spread_and_cheap_premium": prem_block,
        "part10_tradability_classes": trad,
        "part12_profit_capture": cap_block,
        "part13_shadow_paper_book": {k: v for k, v in book_block.items()
                                     if k != "book"},
        "part14_daily_export": export_block,
        "part15_capacity": cap_compare,
        "part16_answers": answers,
        "entry_quality_rows_count": len(eq_rows),
        "candidate_summary": candidates.study(trade_rows, entry_rows, signals_by_key,
                                              n_sessions),
        "production_confirmation": {
            "buy_wait_no_trade_unchanged": True,
            "gates_and_thresholds_unchanged": True,
            "confidence_formula_unchanged": True,
            "signal_score_display_unchanged_in_production": True,
            "stops_targets_rr_unchanged": True,
            "chase_guard_unchanged": True,
            "direction_logic_unchanged": True,
            "strike_selector_unchanged": True,
            "premium_floor_unchanged": True,
            "early_momentum_unchanged": True,
            "production_exits_unchanged": True,
            "execution_and_broker_path_unchanged": True,
            "ai_paper_only": True,
            "real_money_execution_disabled": True,
            "phase10_wired_into_signal_tab": False,
            "calibrated_probability_deployed": False,
            "a_plus_applied_to_production": False,
            "proposed_thresholds_applied_to_production": False,
            "how_this_is_enforced": "Phase 10 lives in app/research/phase10 and is "
                                    "imported by phase10_report.py and its smoke test "
                                    "only. It holds no order-placing code path, and "
                                    "_smoke_phase10 fails if any production module "
                                    "imports it",
        },
        "promotions": [],
        "limits": [
            "every session in this dataset ran the full watchlist, so the tier "
            "split's before exists and its after does not: no completeness, stale "
            "rate, latency or capacity improvement is claimed",
            "exchange-to-receive latency is not in the recorded schema; chain age is "
            "the proxy and is reported as chain age, never as latency",
            "WAIT and NO_TRADE decisions are not recorded, so the freshness audit's "
            "denominator is production BUYs only and a fresh-WAIT rate is not "
            "computed",
            "room, extension and expected move derive from ATR and the one-minute "
            "bars are incomplete, so those blocks are stamped DATA_CONTAMINATED "
            "rather than dropped",
            "the A+ qualifier is scored on trades it was derived from unless the "
            "holdout gate passes; on this dataset it does not",
            "premiums are ~60s snapshot closes, so intrabar touches on candidate "
            "legs are invisible",
            "brokerage remains the "
            f"₹{policies.COST_PER_ROUNDTRIP_OPTIONS:.0f}/round-trip assumption and is "
            "never added into the same number as the measured spread",
        ],
        "questions": list(QUESTIONS),
    }


def _n(v: object) -> str:
    """A cell that says nothing when the value is unmeasured."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, dict):
        return ", ".join(f"{k}={_n(x)}" for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return ", ".join(_n(x) for x in v) or "—"
    return str(v)


def render_md(r: dict) -> str:
    """The operator-facing report. Every number carries the label that limits it."""
    L: list[str] = []
    g = r["phase10_gates"]
    L.append("# Phase 10 — watchlist tiering, data quality, score calibration, "
             "A+ shadow (research only)")
    L.append("")
    L.append(f"Sessions analysed: **{', '.join(r['sessions_analysed'])}** · resolved "
             f"trades: **{r['resolved_trades']}** · instruments: "
             f"{', '.join(r['instruments'])}")
    L.append("")
    L.append(f"Status: **{g['status']}** — failed gates: "
             f"{', '.join(g['failed']) or 'none'}. Permitted labels: "
             f"{', '.join(g['permitted_labels'])}.")
    L.append("")
    L.append("## Acceptance gates")
    L.append("")
    L.append("| gate | need | have | pass | a failure forbids |")
    L.append("| --- | --- | --- | --- | --- |")
    for row in g["gates"]:
        L.append(f"| {row['gate']} | {row['need']} | {_n(row['have'])} | "
                 f"{'PASS' if row['pass'] else 'FAIL'} | {row['forbids']} |")
    L.append("")

    tier = r["part1_2_tier_architecture"]
    L.append("## 1-2. Watchlist tiers")
    L.append("")
    L.append(f"Configured deep watchlist: "
             f"**{', '.join(tier['configured_deep_watchlist']) or 'not configured'}** "
             f"— {tier['chosen_by']}.")
    L.append("")
    L.append(f"Tier split active while this dataset was recorded: "
             f"**{_n(tier['tier_split_active_during_recording'])}**. "
             f"{tier['mechanism']}.")
    L.append("")

    dq = r["part3_data_quality"]
    L.append("## 3. Data quality per instrument")
    L.append("")
    L.append("| instrument | bars expected | present | missing % | dup | out of order "
             "| chain snapshots | trades | stale % | median chain age s | meets "
             f"{dq['gate_pct']}% gate |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, v in dq["per_instrument"].items():
        L.append(f"| {name} | {_n(v['bars_expected_in_sessions'])} | "
                 f"{_n(v['bars_present'])} | "
                 f"{_n(v['missing_bar_pct'])} | {_n(v['duplicate_bars'])} | "
                 f"{_n(v['out_of_order_bars'])} | {_n(v['chain_snapshots'])} | "
                 f"{_n(v['resolved_trades'])} | {_n(v['stale_pct'])} | "
                 f"{_n(v['median_chain_age_sec'])} | "
                 f"{_n(v['meets_completeness_gate'])} |")
    L.append("")
    L.append(f"{dq['instruments_meeting_gate']} of {len(dq['per_instrument'])} "
             f"instrument(s) meet the completeness gate; worst is "
             f"**{dq['worst_missing_bar_pct']}%** missing. Live equivalent: "
             f"{dq['live_endpoint']}.")
    L.append("")

    fr = r["part4_signal_freshness"]
    L.append("## 4. Signal freshness at decision time")
    L.append("")
    L.append(f"{fr['decisions_audited']} decisions audited. {fr['denominator']}.")
    L.append("")
    L.append("| data state | n | target-before-stop % | gross R | net R | median "
             "chain age s |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for state, v in fr["by_state"].items():
        L.append(f"| {state} | {v['n']} | {_n(v['target_before_stop_pct'])} | "
                 f"{_n(v['gross_expectancy_r'])} | {_n(v['net_expectancy_r'])} | "
                 f"{_n(v['median_chain_age_sec'])} |")
    L.append("")
    L.append(f"Fresh BUYs **{_n(fr['fresh_buy_pct'])}%**, stale BUYs "
             f"**{_n(fr['stale_buy_pct'])}%**. {fr['reading']}. {fr['guarantee']}.")
    L.append("")

    sc = r["part5_6_signal_score"]
    L.append(f"## 5-6. {sc['label_used']} against outcome")
    L.append("")
    L.append(f"*{sc['tooltip']}* {sc['naming_note']}.")
    L.append("")
    L.append("| bucket | n | mean score | stated as probability | observed "
             "target-before-stop | gross R | net R | net PF | label |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for b in sc.get("buckets_quantile") or sc.get("buckets_fixed_phase8") or []:
        L.append(f"| {b['bucket']} | {b['n']} | {_n(b['mean_signal_score'])} | "
                 f"{_n(b['stated_as_probability'])} | "
                 f"{_n(b['observed_target_before_stop'])} | "
                 f"{_n(b['gross_expectancy_r'])} | {_n(b['net_expectancy_r'])} | "
                 f"{_n(b['net_profit_factor'])} | {b['label']} |")
    L.append("")
    L.append(f"{sc['bucketing_note']}.")
    L.append("")
    mono = sc["monotonicity"]
    L.append(f"Ordering: **{mono['verdict']}**. {mono['diagnosis']}.")
    L.append("")
    br = sc["brier"]
    L.append(f"Brier **{_n(br.get('brier_score'))}** against "
             f"**{_n(br.get('brier_of_constant_base_rate_forecast'))}** for a flat "
             f"forecast of the base rate; expected calibration error "
             f"**{_n(sc['expected_calibration_error'])}**, worst bucket "
             f"**{_n(sc['max_calibration_error'])}**. Rank "
             f"correlation with net R "
             f"**{_n(sc['spearman_score_vs_net_r'])}**.")
    L.append("")
    L.append(f"Answer: {sc['answer']}. {sc['guarantee']}.")
    L.append("")

    pb = r["part7_calibrated_probability"]
    L.append("## 7. Calibrated probability")
    L.append("")
    L.append(f"Status: **{pb['status']}**. Chronological split — train "
             f"{', '.join(pb['split'].get('train_sessions', []))} ({pb['train_rows']} "
             f"rows), dev {', '.join(pb['split'].get('dev_sessions', []))} "
             f"({pb['dev_rows']}), holdout "
             f"{', '.join(pb['split'].get('holdout_sessions', []))} "
             f"({pb['holdout_rows']}).")
    L.append("")
    L.append(f"{pb.get('reason') or pb.get('reading')}.")
    L.append("")
    L.append(f"{pb['guarantee']}.")
    L.append("")

    ap_ = r["part8_9_a_plus_shadow"]
    L.append("## 8-9. A+ shadow qualifier vs baseline")
    L.append("")
    L.append(f"Status: **{ap_['status']}**, label **{ap_['label']}**. "
             f"{ap_['population']}.")
    L.append("")
    L.append("| population | n | target-before-stop % | gross R | net R | net PF | "
             "total net R |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for label, v in (("baseline", ap_["baseline"]),
                     ("A+ qualified only", ap_["a_plus_only"])):
        L.append(f"| {label} | {v['n']} | {_n(v.get('target_before_stop_pct'))} | "
                 f"{_n(v.get('gross_expectancy_r'))} | "
                 f"{_n(v.get('net_expectancy_r'))} | "
                 f"{_n(v.get('net_profit_factor'))} | {_n(v.get('total_net_r'))} |")
    L.append("")
    L.append("| label | n |")
    L.append("| --- | --- |")
    for k, v in ap_["qualification_counts"].items():
        L.append(f"| {k} | {v} |")
    L.append("")
    L.append(f"Missed winners **{ap_['missed_winners']['n']}** against avoided losers "
             f"**{ap_['avoided_losers']['n']}** — {ap_['avoided_losers']['caveat']}.")
    L.append("")
    L.append(f"Answer: {ap_['answer_to_does_a_plus_beat_baseline']}. Proposed "
             f"thresholds: **{ap_['proposed_thresholds_status']}**.")
    L.append("")

    L.append("## 10. Instrument tradability on spread")
    L.append("")
    L.append("| instrument | trades | median spread as % of intended risk | "
             "judgeable | class |")
    L.append("| --- | --- | --- | --- | --- |")
    for name, v in r["part8_instrument_tradability"].items():
        L.append(f"| {name} | {v['trades']} | "
                 f"{_n(v['median_spread_share_of_risk_pct'])} | "
                 f"{_n(v['judgeable'])} | "
                 f"{v['class'] or 'unmeasured'} |")
    L.append("")

    pr = r["part10_11_spread_and_cheap_premium"]
    L.append("## 11. Premium bands")
    L.append("")
    L.append("| band | legs available at signal time | selected | median premium | "
             "median spread as % of risk | trades | target-before-stop % | gross R | "
             "net R | label |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for band in pr["band_order"]:
        e = pr["by_premium_band_entry_time"].get(band, {})
        o = pr["by_premium_band_outcome"].get(band, {})
        L.append(f"| {band} | {_n(e.get('legs_available'))} | "
                 f"{_n(e.get('selected'))} | {_n(e.get('median_premium'))} | "
                 f"{_n(e.get('median_spread_share_of_risk_pct'))} | "
                 f"{_n(o.get('trades'))} | "
                 f"{_n(o.get('target_before_stop_pct'))} | "
                 f"{_n(o.get('gross_expectancy_r'))} | "
                 f"{_n(o.get('net_expectancy_r'))} | "
                 f"{o.get('label') or e.get('label') or '—'} |")
    L.append("")
    L.append(f"Cheap premiums: {pr['cheap_premium_answer']}. Premium floor: "
             f"**{pr['premium_floor_status']}**.")
    L.append("")

    cp = r["part12_profit_capture"]
    L.append("## 12. How much of the favourable excursion is kept")
    L.append("")
    ov = cp["overall"]
    L.append(f"Median MFE **{_n(ov.get('median_mfe_r'))}R**, median MAE "
             f"**{_n(ov.get('median_mae_r'))}R**, median MFE capture "
             f"**{_n(ov.get('median_mfe_capture_pct'))}%**.")
    L.append("")
    L.append("| give-back band | n | median MFE R | median capture % | median "
             "give-back R | median minutes to MFE | net R | label |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for band, v in cp["by_giveback_band"].items():
        L.append(f"| {band} | {v['n']} | {_n(v.get('median_mfe_r'))} | "
                 f"{_n(v.get('median_mfe_capture_pct'))} | "
                 f"{_n(v.get('median_giveback_r'))} | "
                 f"{_n(v.get('median_min_to_mfe'))} | "
                 f"{_n(v.get('net_expectancy_r'))} | {v.get('label', '—')} |")
    L.append("")
    L.append(f"{cp['diagnosis']}. {cp['hindsight_warning']}. ATR-derived rows: "
             f"**{cp['atr_contamination']['status']}**. {cp['guarantee']}.")
    L.append("")

    bk = r["part13_shadow_paper_book"]
    L.append("## 13. Shadow paper book")
    L.append("")
    L.append(f"{bk['rows_recorded']} rows recorded, model status "
             f"**{bk['model_status']}**, invented probabilities "
             f"**{bk['invented_probabilities']}**. {bk['independence']}. "
             f"{bk['guarantee']}.")
    L.append("")

    ex = r["part14_daily_export"]
    L.append("## 14. Nightly export")
    L.append("")
    if ex.get("planned"):
        present = [m["member"] for m in ex["members"] if m["present"]]
        L.append(f"Planned against `{ex['data_dir']}` as "
                 f"`qt_daily_YYYY-MM-DD.zip`: **{ex['included_count']}** member(s) "
                 f"would be included, **{len(present)}** present on disk, "
                 f"**{len(ex['missing'])}** missing "
                 f"({', '.join(ex['missing']) or 'none'}). A missing member is "
                 f"reported as missing and never substituted.")
        L.append("")
        L.append(f"Refused name patterns: {', '.join(ex['forbidden_patterns'])}. "
                 f"{ex['secret_exclusion']}.")
    else:
        L.append(f"Not planned against a directory: {ex['reason']}. Members "
                 f"specified: {', '.join(ex['members_specified'])}.")
    L.append("")

    cc = r["part15_capacity"]
    L.append("## 15. Capacity")
    L.append("")
    L.append(f"Status: **{cc['status']}**. {cc['verdict']}.")
    L.append("")
    L.append("| metric | before | after |")
    L.append("| --- | --- | --- |")
    after = (cc.get("after") or {}).get("metrics") or {}
    for metric, v in cc["before"]["metrics"].items():
        L.append(f"| {metric} | {_n(v)} | {_n(after.get(metric))} |")
    L.append("")
    est = cc["estimate"]
    L.append(f"Arithmetic estimate only: {est['reasoning']}. It does not predict "
             f"{', '.join(est['does_not_predict'])} — {est['why_not']}.")
    L.append("")

    L.append("## Answers")
    L.append("")
    for q, a in r["part16_answers"].items():
        L.append(f"- **{q}** {a}")
    L.append("")

    L.append("## Production confirmation")
    L.append("")
    for k, v in r["production_confirmation"].items():
        if isinstance(v, bool):
            L.append(f"- {k.replace('_', ' ')}: **{'yes' if v else 'no'}**")
    L.append(f"- enforcement: {r['production_confirmation']['how_this_is_enforced']}")
    L.append(f"- promotions: **{r['promotions'] or 'none'}**")
    L.append("")

    L.append("## Limits")
    L.append("")
    for lim in r["limits"]:
        L.append(f"- {lim}")
    L.append("")
    L.append(f"status={g['status']} resolved={r['resolved_trades']} "
             f"sessions={len(r['sessions_analysed'])} "
             f"score_predictive={_n(sc['is_predictive'])} "
             f"probability={pb['status']} a_plus={ap_['status']} "
             f"capacity={cc['status']} "
             f"failed_gates={','.join(g['failed']) or 'none'}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(settings.data_dir, "history.db"))
    ap.add_argument("--instruments", default="")
    # Phase 9's defaults exactly. A different horizon or limit here would replay a
    # different population and every Phase 9 -> Phase 10 comparison would silently
    # stop being a comparison.
    ap.add_argument("--horizon", type=int, default=paths.HORIZON_BARS)
    ap.add_argument("--limit", type=int, default=0,
                    help="0 = every recorded signal")
    ap.add_argument("--sessions", default="")
    ap.add_argument("--deep-watchlist", default="",
                    help="operator's Tier 1 list, comma separated; reported, never "
                         "invented")
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--out", default="phase10_report.json")
    ap.add_argument("--md", default="", help="also write the markdown report here")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"no history db at {args.db}")
    names = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    if not names:
        # Phase 9's default, including its first-five truncation. Widening the
        # default would replay a larger population than Phase 9 reported and the
        # two phases' numbers would no longer be about the same trades; pass
        # --instruments to widen deliberately.
        names = dataset.instruments_with_chains(args.db)[:5]
    sessions_wanted = {s.strip() for s in args.sessions.split(",") if s.strip()}
    deep = [s.strip().upper() for s in args.deep_watchlist.split(",") if s.strip()]
    data_dir = Path(args.data_dir) if args.data_dir else None

    report = build(args.db, names, args.horizon, args.limit, sessions_wanted,
                   deep_watchlist=deep, data_dir=data_dir)
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    md = render_md(report)
    if args.md:
        Path(args.md).write_text(md + "\n")
    print(md)
    print()
    print(f"wrote {args.out}"
          + (f" and {args.md}" if args.md else ""))


if __name__ == "__main__":
    main()
