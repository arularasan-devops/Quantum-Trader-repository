"""Phase 11 report — family-separated validation, costed ledger, feed A/B.

RESEARCH / SHADOW / PAPER ONLY.

Run:
    .venv/bin/python phase11_report.py --db data/history.db \
        --instruments NIFTY,BANKNIFTY,SENSEX,MIDCPNIFTY,CRUDEOIL,NATURALGAS \
        --data-dir data --out phase11_report.json --md phase11_report.md

The population is Phase 9's ``collect``, unchanged, so every trade here is a trade
the earlier phases analysed. What Phase 11 adds is the instrument family on every
row and the refusal to print any number without one.

Note the ``--instruments`` default. Phase 10 defaulted to the five instruments
with the most recorded chain snapshots; MCX records chains at roughly twice the
index cadence, so that default silently produced an MCX-weighted population whose
results were then reported as the whole book. This report defaults to an explicit
family-balanced list and prints both the list and what each family contributed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.analysis import exec_funnel, feed_profile
from app.config import settings
from app.research.phase10 import freshness, probability
from app.research.phase11 import (
    aplus11,
    attribution,
    families,
    family_analysis,
    feedab,
    gates11,
    ledger,
    preview,
    score11,
    shadow11,
    tradability11,
)
from app.research.phase7 import dataset, gates, paths, policies
from app.research.phase9 import strikes
from app.research.phase9.findings9 import contamination
from phase9_report import _key, collect

QUESTIONS = (
    "did configuring the deep watchlist improve data quality?",
    "did the stale BUY rate fall?",
    "did REST pressure fall?",
    "how does the SIGNAL SCORE behave for INDEX versus MCX?",
    "is a high SIGNAL SCORE useful for INDEX options?",
    "is a high SIGNAL SCORE useful for MCX options?",
    "is the A+ shadow qualifier helpful for INDEX options?",
    "is the A+ shadow qualifier helpful for MCX options?",
    "why did the auto book take only the number of trades it took?",
    "how much of the loss is economic spread versus signal failure?",
    "which instruments are tradable on this evidence?",
    "what should stay out of deep monitoring?",
    "what remains unproven?",
    "is there enough evidence to plan production A+ integration?",
)

# The default replay population: both gated families, explicitly, so no family is
# represented only because its chains recorded faster.
DEFAULT_INSTRUMENTS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY",
                       "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER")


def build(db: str, names: list[str], horizon: int, limit: int,
          sessions_wanted: set[str], *, deep_watchlist: list[str],
          data_dir: Path | None) -> dict:
    coll = collect(db, names, horizon, limit, sessions_wanted)
    per = coll["per"]
    trade_rows = families.tag(coll["trade_rows"])
    signals = coll["signals"]
    entry_rows = coll["entry_rows"]
    pairs = coll["pairs"]

    def entry_of(row: dict) -> dict | None:
        return entry_rows.get(_key(row))

    sessions_ordered = sorted({r["session"] for r in trade_rows})
    n_sessions = len(sessions_ordered)
    worst_missing = max((b["candle"].get("missing_pct") or 0.0
                         for b in per.values()), default=0.0)
    atr_taint = contamination(worst_missing, atr_dependent=True)
    book_taint = contamination(worst_missing, atr_dependent=False)

    # ---- §3 families ---------------------------------------------------------
    fam_block = families.summary(trade_rows)

    # ---- §4 signal score, per family ----------------------------------------
    score_block = score11.study_by_family(trade_rows, n_sessions)
    predictive = {
        fam: bool(block["usefulness"].get("usable_as_ranking"))
        for fam, block in score_block["by_family"].items()}

    # ---- §4b calibration, per family — for the gates, never for a mapping ----
    prob_by_family: dict[str, dict] = {}
    for fam, rows in families.split(trade_rows).items():
        prob_by_family[fam] = (
            probability.build(rows, sessions_ordered) if rows else
            {"status": "NO_ROWS", "train_rows": 0, "holdout_rows": 0,
             "holdout_beats_constant_forecast": False,
             "reason": "no resolved trade in this family"})

    # ---- §5 A+ shadow, per family -------------------------------------------
    aplus_block = aplus11.study_by_family(
        trade_rows, sessions_ordered, entry_of,
        score_predictive=predictive, book_taint=book_taint)

    # ---- §6 costed ledger: funnel, fills, flow ------------------------------
    funnel_path = (Path(data_dir) / Path(exec_funnel.LOG_NAME)) if data_dir else None
    flow_path = (Path(data_dir) / "flow_signals.jsonl") if data_dir else None
    ledger_block = ledger.build(funnel_path=funnel_path, flow_path=flow_path,
                                db=db, rows=trade_rows,
                                sessions=sessions_ordered, entry_of=entry_of)

    # ---- §7 loss attribution ------------------------------------------------
    attribution_block = attribution.study(trade_rows, entry_of)

    # ---- §8 spread and tradability ------------------------------------------
    trad_block = tradability11.study(trade_rows, n_sessions)

    # ---- §9-11 premiums, entry quality, capture ------------------------------
    prem_block = family_analysis.premiums_by_family(signals, trade_rows, n_sessions)
    eq_block = family_analysis.entry_quality_by_family(
        trade_rows, pairs, entry_of, n_sessions, atr_taint)
    cap_block = family_analysis.capture_by_family(
        trade_rows, n_sessions, worst_missing)

    # ---- §12 shadow baseline -------------------------------------------------
    model_available = any(
        bool(b.get("holdout_beats_constant_forecast"))
        for b in prob_by_family.values())
    shadow_block = shadow11.build(
        trade_rows, model_available=model_available,
        model_status=prob_by_family[families.INDEX_OPTIONS].get("status", "UNKNOWN"))

    # ---- §2 feed before/after -----------------------------------------------
    fresh_rows = [freshness.row_of(r) for r in trade_rows]
    fresh_block = freshness.study(fresh_rows, n_sessions)
    stale_by_session: dict[str, float] = {}
    for session in sessions_ordered:
        rows = [r for r in trade_rows if r["session"] == session]
        stale = sum(1 for r in rows if r.get("data_flag") in ("STALE", "NO_DATA"))
        stale_by_session[session] = round(100.0 * stale / len(rows), 1) if rows else 0.0
    profile_path = (Path(data_dir) / Path(feed_profile.LOG_NAME)
                    if data_dir else None)
    feed_block = feedab.study(
        db, sorted({r["instrument"] for r in trade_rows}), sessions_ordered,
        profile_path, configured_deep=deep_watchlist,
        stale_rate_by_session=stale_by_session)

    # ---- §16 gates -----------------------------------------------------------
    ladder_study = strikes.study(signals, n_sessions)
    all_cand_rows = [row for s in signals for row in s.get("entry_time_rows", [])]
    with_book = sum(1 for row in all_cand_rows if row.get("bid") is not None)

    def smallest_cell(block: dict) -> int:
        cells = [c["n"] for c in ((block.get("score") or {}).get("buckets_quantile")
                                  or (block.get("score") or {}).get(
                                      "buckets_fixed_phase8") or [])
                 if c.get("n")]
        return min(cells) if cells else 0

    family_cells = {fam: smallest_cell(block)
                    for fam, block in score_block["by_family"].items()}
    gated = [prob_by_family[f] for f in gates11.GATED_FAMILIES]
    holdout_sessions = min(
        (len(b["by_family"][f]["comparison"]["chronological_holdout"]
             ["holdout_sessions"])
         for f, b in ((f, aplus_block) for f in gates11.GATED_FAMILIES)
         if b["by_family"][f].get("measurable")), default=0)
    g = gates11.evaluate(
        resolved=len(trade_rows), sessions=n_sessions,
        missing_bar_pct=worst_missing,
        book_coverage_pct=100.0 * with_book / max(1, len(all_cand_rows)),
        ladder_coverage_pct=ladder_study["selected_leg_in_ladder_pct"],
        holdout_sessions=holdout_sessions,
        smallest_cell=min(family_cells.values()) if family_cells else 0,
        calibration_train_rows=min((b.get("train_rows") or 0) for b in gated),
        calibration_holdout_rows=min((b.get("holdout_rows") or 0) for b in gated),
        sessions_at_deep_watchlist=len(feed_block["groups"][feedab.AFTER]),
        resolved_by_family=fam_block["rows_by_family"],
        smallest_family_cell=family_cells)

    # ---- §14-15 research payloads -------------------------------------------
    preview_block = preview.signal_preview(trade_rows, trad_block["by_instrument"])
    dashboard_block = preview.dashboard(
        families=fam_block, score=score_block, aplus=aplus_block,
        tradability=trad_block, attribution=attribution_block, feed=feed_block,
        funnel=ledger_block["decision_funnel"],
        flow=ledger_block["costed_flow_book"], gates=g)

    # ---- §17 answers ---------------------------------------------------------
    index_att = attribution_block["by_family"][families.INDEX_OPTIONS]
    mcx_att = attribution_block["by_family"][families.MCX_OPTIONS]
    funnel_sessions = ledger_block["decision_funnel"]["by_session"]
    recorded_funnels = [s for s, b in funnel_sessions.items() if b["recorded"]]
    answers = {
        QUESTIONS[0]: (feed_block["unmeasured_reason"]
                       or f"measured: missing bars "
                          f"{feed_block['before']['missing_bar_pct']}% before against "
                          f"{feed_block['after']['missing_bar_pct']}% after"),
        QUESTIONS[1]: (feed_block["unmeasured_reason"]
                       or f"stale BUY rate {feed_block['before']['median_stale_signal_pct']}% "
                          f"before against "
                          f"{feed_block['after']['median_stale_signal_pct']}% after"),
        QUESTIONS[2]: ("REST poll counts, rate-limit errors and reconnects are not in "
                       "the replay schema. They are recorded live by the feed profile "
                       "from this build onward, so this becomes measurable once a "
                       "session runs with the profile log present"),
        QUESTIONS[3]: score_block["comparison_note"],
        QUESTIONS[4]: score_block["answer_index"],
        QUESTIONS[5]: score_block["answer_mcx"],
        QUESTIONS[6]: aplus_block["answer_index"],
        QUESTIONS[7]: aplus_block["answer_mcx"],
        QUESTIONS[8]: (f"recorded for {len(recorded_funnels)} session(s): "
                       + "; ".join(f"{s}: {funnel_sessions[s]['binding_limit']}"
                                   for s in recorded_funnels)
                       if recorded_funnels else
                       "UNRECORDED. The execution funnel was in memory only for every "
                       "session in this dataset, so the binding limit cannot be "
                       "recovered. It is persisted from this build onward and the "
                       "next recorded session answers it"),
        QUESTIONS[9]: (
            f"INDEX_OPTIONS: {index_att['share_of_loss_economic_or_execution_pct']}% "
            f"of net loss is economic or execution against "
            f"{index_att['share_of_loss_signal_side_pct']}% signal-side; "
            f"MCX_OPTIONS: {mcx_att['share_of_loss_economic_or_execution_pct']}% "
            f"against {mcx_att['share_of_loss_signal_side_pct']}%"),
        QUESTIONS[10]: (", ".join(trad_block["tradable"]) or "none on this sample")
                       + " (research classification, applied nowhere)",
        QUESTIONS[11]: (", ".join(trad_block["untradable_on_sample"])
                        or "nothing was classified untradable on this sample")
                       + ". Excluding an instrument from deep monitoring is an "
                         "operator decision; this report does not make it",
        QUESTIONS[12]: "; ".join([
            f"{len(g['failed'])} acceptance gate(s) fail: {', '.join(g['failed'])}"
            if g["failed"] else "no acceptance gate fails",
            "no score-to-probability mapping is fitted or deployed",
            "the A+ comparison is in-sample for every family whose holdout gate "
            "fails",
            "the feed before/after has no AFTER side yet",
        ]),
        QUESTIONS[13]: (
            "no. Planning production A+ integration needs the family holdout gates "
            "to pass, and they do not: "
            f"{', '.join(g['failed'])}" if g["failed"] else
            "the gates pass on this sample. Even then Phase 11 promotes nothing — "
            "a promotion is a separate, explicit decision with its own review"),
    }

    return {
        "phase": 11,
        "scope": "RESEARCH / SHADOW / PAPER ONLY. Production BUY/WAIT/NO_TRADE, the "
                 "gates and their thresholds, the SIGNAL SCORE formula and weights, "
                 "stops, targets, R:R, the chase guard, direction logic, the strike "
                 "selector, the premium floor, Early Momentum/Early-Early, "
                 "production exits, the flow tracker and the broker order path are "
                 "untouched. No order can be placed from this report and no "
                 "threshold in it is applied anywhere.",
        "correction_this_phase_is_built_on": (
            "Phase 10's default population was the instruments with the most "
            "recorded chain snapshots, which is an MCX-weighted selection. Its "
            "pooled conclusions were therefore MCX conclusions presented as "
            "whole-book conclusions. Every table in this report is per family"),
        "db": db,
        "instruments": names,
        "sessions_analysed": sessions_ordered,
        "resolved_trades": len(trade_rows),
        "phase11_gates": g,
        "data_contamination": {"atr_dependent_blocks": atr_taint,
                               "book_only_blocks": book_taint},
        "part1_deep_watchlist": {
            "configured_deep_watchlist": deep_watchlist,
            "recommended_by_evidence": list(families.RECOMMENDED_DEEP),
            "recommendation_basis": "index options carried a payable book on this "
                                    "sample and CRUDEOIL/NATURALGAS the lowest MCX "
                                    "spread share; GOLD and SILVER are excluded from "
                                    "the recommendation on spread, not on direction",
            "chosen_by": "operator configuration (QT_DEEP_WATCHLIST). Research does "
                         "not set it: a list invented here would become the de facto "
                         "configuration without an operator ever choosing it",
            "cap": settings.max_deep_instruments,
        },
        "part2_feed_before_after": feed_block,
        "part2_signal_freshness": fresh_block,
        "part3_instrument_families": fam_block,
        "part4_signal_score_by_family": score_block,
        "part4_calibration_by_family": {
            fam: {k: v for k, v in block.items() if k != "rows"}
            for fam, block in prob_by_family.items()},
        "part5_a_plus_shadow_by_family": aplus_block,
        "part6_costed_ledger": {
            **{k: v for k, v in ledger_block.items() if k != "filled_trades"},
            "filled_trades": {
                "n": ledger_block["filled_trades"]["n"],
                "fields": ledger_block["filled_trades"]["fields"],
                "rows_written_to": "costed_flow_ledger.json",
            },
        },
        # Popped by main() before the report is written: the per-trade rows are
        # large and belong in the ledger artifact, not in the report body.
        "_filled_trade_rows": ledger_block["filled_trades"]["rows"],
        "part7_loss_attribution": attribution_block,
        "part8_spread_and_tradability": trad_block,
        "part9_premiums_by_family": prem_block,
        "part10_entry_quality_by_family": eq_block,
        "part11_profit_capture_by_family": cap_block,
        "part12_shadow_baseline": shadow_block,
        "part14_signal_preview": {k: v for k, v in preview_block.items()
                                  if k != "rows"},
        "part15_research_dashboard": dashboard_block,
        "part17_answers": answers,
        "production_confirmation": {
            "buy_wait_no_trade_unchanged": True,
            "gates_and_thresholds_unchanged": True,
            "signal_score_formula_and_weights_unchanged": True,
            "stops_targets_rr_unchanged": True,
            "chase_guard_unchanged": True,
            "direction_logic_unchanged": True,
            "strike_selector_unchanged": True,
            "premium_floor_unchanged": True,
            "early_momentum_unchanged": True,
            "production_exits_unchanged": True,
            "flow_tracker_and_flow_paper_lots_unchanged": True,
            "execution_and_broker_path_unchanged": True,
            "ai_paper_only": True,
            "real_money_execution_disabled": True,
            "phase11_wired_into_signal_tab": False,
            "calibrated_probability_deployed": False,
            "a_plus_applied_to_production": False,
            "instruments_excluded_from_production": [],
            "proposed_thresholds_applied_to_production": False,
            "what_was_added_to_production": [
                "app/analysis/exec_funnel.py now persists its refusals and periodic "
                "stage counts to JSONL (measurement only; no early return, risk, "
                "validation, execution or broker decision changed)",
                "app/analysis/feed_profile.py records what the feed cost and "
                "delivered (new module, read by nothing that trades)",
                "GET /api/feed-profile serves those records read-only",
            ],
            "how_this_is_enforced": "Phase 11 lives in app/research/phase11 and is "
                                    "imported by phase11_report.py and its smoke "
                                    "test only. _smoke_phase11 fails if any "
                                    "production module imports it",
        },
        "promotions": [],
        "limits": [
            "no session in this dataset ran with a reduced deep watchlist, so the "
            "feed before/after has a BEFORE and no AFTER and no improvement is "
            "claimed",
            "the execution funnel was in memory for every recorded session, so the "
            "binding limit on the 7-entry day is UNRECORDED rather than inferred",
            "exchange-to-receive latency, REST poll counts and rate-limit errors are "
            "not in the replay schema; chain age is reported as chain age",
            "WAIT and NO_TRADE decisions are still not recorded, so every rate here "
            "has production BUYs as its denominator",
            "the costed flow book prices legs against the nearest recorded chain "
            f"within {ledger.MAX_BOOK_AGE_SEC}s, which is a quoted book and not a "
            "fill: a real fill can be better or worse than the touch",
            "family sample sizes are small enough that per-instrument cells inside a "
            "family are descriptive only",
        ],
        "questions": list(QUESTIONS),
        "median_helper_note": policies.median.__doc__,
        "gate_pct_reference": {"max_missing_bar_pct": gates.MAX_MISSING_BAR_PCT},
    }


def _artifacts(report: dict, ledger_rows: list[dict], out_dir: Path) -> list[str]:
    """§17's named artifacts, written beside the report."""
    written: list[str] = []
    payloads = {
        "costed_flow_ledger.json": {
            "scope": "RESEARCH ONLY",
            "decision_funnel": report["part6_costed_ledger"]["decision_funnel"],
            "costed_flow_book": report["part6_costed_ledger"]["costed_flow_book"],
            "filled_trade_ledger": ledger_rows,
        },
        "instrument_family_analysis.json": {
            "families": report["part3_instrument_families"],
            "spread_and_tradability": report["part8_spread_and_tradability"],
            "premiums": report["part9_premiums_by_family"],
            "entry_quality": report["part10_entry_quality_by_family"],
            "profit_capture": report["part11_profit_capture_by_family"],
            "loss_attribution": report["part7_loss_attribution"],
        },
        "a_plus_shadow_index.json": {
            "family": families.INDEX_OPTIONS,
            "status": "SHADOW_ONLY",
            **report["part5_a_plus_shadow_by_family"]["by_family"][
                families.INDEX_OPTIONS],
        },
        "a_plus_shadow_mcx.json": {
            "family": families.MCX_OPTIONS,
            "status": "SHADOW_ONLY",
            **report["part5_a_plus_shadow_by_family"]["by_family"][
                families.MCX_OPTIONS],
        },
        "feed_before_after.json": report["part2_feed_before_after"],
        "signal_score_family_analysis.json": report["part4_signal_score_by_family"],
    }
    for name, payload in payloads.items():
        path = out_dir / name
        path.write_text(json.dumps(payload, indent=2, default=str))
        written.append(str(path))
    return written


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _rate(value: float | None) -> str:
    """A 0-1 rate as a percentage. The score blocks store fractions."""
    return "n/a" if value is None else f"{100.0 * value:.1f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def render_md(report: dict) -> str:
    g = report["phase11_gates"]
    fam = report["part3_instrument_families"]
    score = report["part4_signal_score_by_family"]
    aplus = report["part5_a_plus_shadow_by_family"]
    trad = report["part8_spread_and_tradability"]
    att = report["part7_loss_attribution"]
    feed = report["part2_feed_before_after"]
    flow = report["part6_costed_ledger"]["costed_flow_book"]
    funnel = report["part6_costed_ledger"]["decision_funnel"]

    lines: list[str] = []
    lines.append("# Phase 11 — instrument-aware validation, costed ledger, feed A/B")
    lines.append("")
    lines.append(f"**Status: {g['status']}** — "
                 f"{len(g['failed'])} gate(s) failing. Nothing is promoted.")
    lines.append("")
    lines.append(report["correction_this_phase_is_built_on"] + ".")
    lines.append("")
    lines.append(f"Sessions: {', '.join(report['sessions_analysed'])}  ")
    lines.append(f"Resolved trades: {report['resolved_trades']}  ")
    lines.append(f"Instruments replayed: {', '.join(report['instruments'])}")
    lines.append("")

    lines.append("## Population by family")
    lines.append("")
    lines.append("| family | resolved | instruments |")
    lines.append("|---|---:|---|")
    for name, n in fam["rows_by_family"].items():
        insts = ", ".join(fam["instruments_by_family"].get(name) or []) or "—"
        lines.append(f"| {name} | {n} | {insts} |")
    lines.append("")

    lines.append("## Q4-Q6 — is the SIGNAL SCORE useful, per family?")
    lines.append("")
    lines.append("| family | resolved | top readable bucket | n | target-before-stop "
                 "| net R | rank corr | usable as a ranking |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---|")
    for name, block in score["by_family"].items():
        u = block["usefulness"]
        lines.append(
            f"| {name} | {block['resolved']} | {u.get('top_readable_bucket') or '—'} "
            f"| {u.get('top_bucket_n') or 0} "
            f"| {_rate(u.get('top_bucket_target_before_stop'))} "
            f"| {_num(u.get('top_bucket_net_expectancy_r'))} "
            f"| {u.get('spearman_vs_target_before_stop')} "
            f"| {'yes' if u.get('usable_as_ranking') else 'no'} |")
    lines.append("")
    lines.append(f"- INDEX: {score['answer_index']}")
    lines.append(f"- MCX: {score['answer_mcx']}")
    lines.append("")
    lines.append(f"_{score['probability_status']}_")
    lines.append("")

    lines.append("## Q7-Q8 — A+ shadow, derived per family")
    lines.append("")
    lines.append(f"- INDEX: {aplus['answer_index']}")
    lines.append(f"- MCX: {aplus['answer_mcx']}")
    lines.append("")
    lines.append("| family | proposed spread cut (% of risk) |")
    lines.append("|---|---:|")
    for name, cut in aplus["proposed_spread_cut_by_family_pct"].items():
        lines.append(f"| {name} | {cut if cut is not None else 'n/a'} |")
    lines.append("")
    lines.append(f"_{aplus['why_split']}._ Thresholds are "
                 f"{aplus['proposed_thresholds_status']}.")
    lines.append("")

    lines.append("## Q10 — economic loss versus signal loss")
    lines.append("")
    lines.append("| family | losses | economic+execution share of net loss "
                 "| signal-side share | biggest single cause |")
    lines.append("|---|---:|---:|---:|---|")
    for name, block in att["by_family"].items():
        causes = sorted(block["by_cause"].items(),
                        key=lambda kv: -(kv[1]["n"] or 0))
        top = f"{causes[0][0]} ({causes[0][1]['n']})" if causes and causes[0][1]["n"] \
            else "—"
        lines.append(
            f"| {name} | {block['losses']} "
            f"| {_pct(block['share_of_loss_economic_or_execution_pct'])} "
            f"| {_pct(block['share_of_loss_signal_side_pct'])} | {top} |")
    lines.append("")

    lines.append("## Q11-Q12 — tradability on this sample")
    lines.append("")
    lines.append("| instrument | family | trades | median | p75 | p90 | max "
                 "| spread > risk | net R | class |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for name, block in trad["by_instrument"].items():
        lines.append(
            f"| {name} | {block['family']} | {block['trades']} "
            f"| {block['median_spread_share_of_risk_pct']} "
            f"| {block['p75_spread_share_of_risk_pct']} "
            f"| {block['p90_spread_share_of_risk_pct']} "
            f"| {block['max_spread_share_of_risk_pct']} "
            f"| {_pct(block['pct_of_trades_spread_exceeded_risk'])} "
            f"| {_num(block['net_expectancy_r'])} | {block['class']} |")
    lines.append("")
    lines.append(f"_{trad['thresholds_status']}._")
    lines.append("")

    lines.append("## Q1-Q3 — did the deep watchlist help?")
    lines.append("")
    lines.append(f"**{feed['status']}**. "
                 + (feed["unmeasured_reason"] or "before and after both recorded."))
    lines.append("")
    lines.append("| group | sessions | missing bars | median stale BUY rate |")
    lines.append("|---|---:|---:|---:|")
    for label in ("before", "after", "configuration_unrecorded"):
        block = feed[label]
        lines.append(f"| {label} | {block.get('sessions', 0)} "
                     f"| {_pct(block.get('missing_bar_pct'))} "
                     f"| {_pct(block.get('median_stale_signal_pct'))} |")
    lines.append("")

    lines.append("## Q9 — why did the book stop where it stopped?")
    lines.append("")
    lines.append("| session | funnel recorded | binding limit |")
    lines.append("|---|---|---|")
    for session, block in funnel["by_session"].items():
        lines.append(f"| {session} | {'yes' if block['recorded'] else 'no'} "
                     f"| {block['binding_limit']} |")
    lines.append("")

    lines.append("## The flow paper book, costed")
    lines.append("")
    if not flow.get("available"):
        lines.append(f"Not recomputed: {flow.get('reason')}")
    else:
        lines.append("| instrument | legs | book coverage | gross ₹ | net ₹ "
                     "| median spread (% of premium) |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for name, block in flow["by_instrument"].items():
            lines.append(
                f"| {name} | {block['legs']} "
                f"| {_pct(block['book_coverage_pct'])} "
                f"| {block['gross_rupees']} | {block['net_rupees']} "
                f"| {block['median_spread_pct_of_premium']} |")
        lines.append("")
        lines.append(f"_{flow['reading']}._")
    lines.append("")

    lines.append("## Acceptance gates")
    lines.append("")
    lines.append("| gate | need | have | pass |")
    lines.append("|---|---|---|---|")
    for gate in g["gates"]:
        lines.append(f"| {gate['gate']} | {gate['need']} | {gate['have']} "
                     f"| {'PASS' if gate['pass'] else 'FAIL'} |")
    lines.append("")

    lines.append("## Answers")
    lines.append("")
    for question, answer in report["part17_answers"].items():
        lines.append(f"- **{question}** {answer}")
    lines.append("")

    lines.append("## What this phase changed in production")
    lines.append("")
    for item in report["production_confirmation"]["what_was_added_to_production"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Everything else is confirmed unchanged, and Phase 11 promotes "
                 "nothing.")
    lines.append("")
    lines.append("## Limits")
    lines.append("")
    for item in report["limits"]:
        lines.append(f"- {item}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(settings.data_dir, "history.db"))
    ap.add_argument("--instruments", default="",
                    help="comma separated; defaults to an explicit family-balanced "
                         "list rather than to whichever names recorded most chains")
    ap.add_argument("--horizon", type=int, default=paths.HORIZON_BARS)
    ap.add_argument("--limit", type=int, default=0,
                    help="0 = every recorded signal")
    ap.add_argument("--sessions", default="")
    ap.add_argument("--deep-watchlist", default="",
                    help="operator's Tier 1 list, comma separated; reported, never "
                         "invented")
    ap.add_argument("--data-dir", default="",
                    help="directory holding exec_funnel.jsonl, feed_profiles.jsonl "
                         "and flow_signals.jsonl")
    ap.add_argument("--out", default="phase11_report.json")
    ap.add_argument("--md", default="", help="also write the markdown report here")
    ap.add_argument("--artifacts", default="",
                    help="directory for the six named artifacts; defaults to the "
                         "report's directory")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"no history db at {args.db}")
    names = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    if not names:
        recorded = set(dataset.instruments_with_chains(args.db))
        names = [n for n in DEFAULT_INSTRUMENTS if n in recorded]
        if not names:
            raise SystemExit(
                "none of the default instruments recorded a chain in this database; "
                "pass --instruments explicitly rather than letting the replay pick "
                "whichever names recorded most snapshots")
    sessions_wanted = {s.strip() for s in args.sessions.split(",") if s.strip()}
    deep = [s.strip().upper() for s in args.deep_watchlist.split(",") if s.strip()]
    data_dir = Path(args.data_dir) if args.data_dir else None

    report = build(args.db, names, args.horizon, args.limit, sessions_wanted,
                   deep_watchlist=deep, data_dir=data_dir)
    out = Path(args.out)
    ledger_rows = report.pop("_filled_trade_rows")
    art_dir = Path(args.artifacts) if args.artifacts else (out.parent or Path("."))
    art_dir.mkdir(parents=True, exist_ok=True)
    report["part6_costed_ledger"]["filled_trades"]["rows_written_to"] = str(
        art_dir / "costed_flow_ledger.json")
    written = _artifacts(report, ledger_rows, art_dir)
    out.write_text(json.dumps(report, indent=2, default=str))

    md = render_md(report)
    if args.md:
        Path(args.md).write_text(md + "\n")
    print(md)
    print()
    print(f"wrote {args.out}" + (f" and {args.md}" if args.md else ""))
    for path in written:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
