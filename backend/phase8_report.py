#!/usr/bin/env python
"""Phase 8 — calibration, spread cost, loss attribution, expiry + profit capture.

RESEARCH ONLY. Reads recorded history and writes two report files. Every path and
policy number comes from the Phase 7 pipeline, so a Phase 8 table cannot disagree
with the Phase 7 table it came from. Phase 8 adds only the questions Phase 7 could
not answer:

  1. does the displayed confidence rank outcomes?            (calibration)
  2. what does the real recorded bid/ask cost?               (spread)
  3. which single cause best explains each loss?             (causes)
  4. is expiry day materially different?                    (expiry)
  5. which exit captures more of the move, net of costs?     (capture)
  6. hold or protect, judged only on past information?       (continuation)
  7. what did the chain say about the contract bought?       (contract)

No production behaviour is touched: BUY/WAIT/NO_TRADE, gates, thresholds, stops,
targets, R:R, strike selection, direction logic, the chase guard, Early
Momentum/Early-Early, production exits, broker execution and AI production
behaviour are read-only inputs, and no order can be placed from this file.

    .venv/bin/python phase8_report.py --instruments CRUDEOIL,NIFTY \
        --session 2026-08-18 --out ~/phase8_report.json --md ~/phase8_report.md
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3

from app.config import settings
from app.market import tick_quality
from app.research.phase7 import gates, paths, policies, quality
from app.research.phase7.dataset import (
    REAL,
    ist,
    load_candles,
    load_chains,
    session_of,
)
from app.research.phase8 import (
    calibration,
    capture,
    causes,
    contract,
    continuation,
    gates8,
    spread,
)
from app.research.phase8 import expiry as expiry_mod


def _trade_row(ev, fill, sp: dict, moved: dict, leg: dict | None) -> dict:
    """The per-trade record, assembled from Phase 7 objects plus Phase 8 descriptors.

    Deliberately thin: it re-labels values that ``paths``/``policies`` already
    computed and derives nothing of its own except the cost-adjusted R, which uses
    Phase 7's own cost assumption.
    """
    path = ev.path
    stop_then_target = (path.first_target_ts is not None
                        and path.first_stop_ts is not None
                        and path.first_target_ts > path.first_stop_ts)
    target_first = (path.first_target_ts is not None
                    and (path.first_stop_ts is None
                         or path.first_target_ts < path.first_stop_ts))
    gross_r = fill.r
    cost_r = policies.COST_PER_ROUNDTRIP_OPTIONS / max(
        0.01, (fill.entry - fill.stop)) if fill.entry else 0.0
    exp = ev.expiry
    return {
        "symbol": ev.symbol,
        "instrument": ev.instrument,
        "side": ev.side,
        "ts_ist": ist(ev.ts),
        "session": session_of(ev.ts),
        "expiry": exp.get("expiry"),
        "days_to_expiry": exp.get("days_to_expiry"),
        "minutes_to_expiry": exp.get("minutes_to_expiry"),
        "expiry_class": exp.get("expiry_class"),
        "venue": exp.get("venue"),
        "regime": ev.regime,
        "confidence": ev.confidence,
        "strike": ev.strike,
        "entry": round(fill.entry, 2),
        "stop": round(fill.stop, 2),
        "target1": round(ev.target1, 2),
        "exit": round(fill.exit, 2),
        "exit_reason": fill.exit_reason,
        "held_min": fill.held_min,
        "realised_r": round(gross_r, 3),
        "net_r": sp.get("net_r"),
        "spread_status": sp.get("spread_status"),
        "spread_cost_r": sp.get("total_spread_cost_r"),
        "mfe_r": round(fill.mfe_r, 3),
        "mae_r": round(fill.mae_r, 3),
        "min_to_mfe": path.as_dict().get("min_to_mfe"),
        "min_to_mae": path.as_dict().get("min_to_mae"),
        "capture": paths.mfe_capture(gross_r, fill.mfe_r),
        "target_before_stop_hit": target_first,
        "target_after_stop": stop_then_target,
        "entry_quality": ev.chase.get("class"),
        "chase_class": ev.chase.get("class"),
        "entry_improvement_pct": ev.chase.get("improvement_available_pct"),
        "premium_expansion_pct": (
            None if not ev.chase.get("peak_in_horizon") else round(
                100.0 * (ev.chase["peak_in_horizon"] - ev.entry) / ev.entry, 2)),
        "underlying_favourable": moved.get("favourable"),
        "underlying_adverse": moved.get("adverse"),
        "chain_age_sec": ev.chain_age_sec,
        "data_flag": ev.data_flag,
        "leg_delta": ev.leg_delta,
        "atr": round(ev.atr, 3),
        "cost_adjusted_r": round(gross_r - cost_r, 3),
        "contract": contract.describe(leg, ev.spot, ev.strike, ev.side),
    }


def _population(db: str, instrument: str, horizon: int, limit: int,
                sessions_wanted: set[str]) -> dict:
    """Reconstructed production BUYs for one instrument, with their Phase 7 fills."""
    series = load_chains(db, instrument)
    candles = load_candles(db, instrument, limit)
    real = series[REAL]
    chain = quality.chain_audit(series)
    candle = quality.candle_audit(candles)
    if not len(real) or not candles:
        return {"instrument": instrument, "events": [], "chain": chain,
                "candle": candle, "series": real, "candles": candles,
                "skipped": "no REAL_BROKER chain or no candles"}

    events, recon = paths.reconstruct(instrument, candles, real,
                                      tick_quality.FRESH_MS, horizon)
    if sessions_wanted:
        events = [ev for ev in events if session_of(ev.ts) in sessions_wanted]
    for ev in events:
        ev.chase = paths.measure_chase(ev, real)
    cuts = paths.chase_thresholds(events)
    for ev in events:
        ev.chase["class"] = paths.classify_chase(
            ev.chase.get("consumed_frac"), cuts.get("cuts"))
        # Phase 8 annotations carried on the Phase 7 event, so every downstream
        # module sees the same expiry classification.
        ev.expiry = expiry_mod.describe(instrument, ev.symbol, ev.ts)
        ev.expiry_class = ev.expiry["expiry_class"]
    return {"instrument": instrument, "events": events, "chain": chain,
            "candle": candle, "series": real, "candles": candles,
            "reconstruction": recon}


def _paper_book(research_db: str | None) -> dict:
    """Part L — the live AI paper book, reported as it is, never invented."""
    if not research_db or not os.path.exists(research_db):
        return {"available": False,
                "reason": "no research.db supplied (--research-db)"}
    try:
        db = sqlite3.connect(f"file:{research_db}?mode=ro", uri=True)
        try:
            n = db.execute("SELECT COUNT(*) FROM ai_paper_trades").fetchone()[0]
            resolved = db.execute(
                "SELECT COUNT(*) FROM ai_paper_trades WHERE exit_premium IS NOT NULL"
            ).fetchone()[0]
        finally:
            db.close()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"unreadable: {exc.__class__.__name__}"}
    return {
        "available": True,
        "paper_trades": n,
        "resolved_paper_trades": resolved,
        "reading": ("the live paper book is empty, so the ledger below is the replay "
                    "ledger and is NOT evidence of live paper performance"
                    if not n else
                    "live paper trades exist; they are still not comparable with the "
                    "replay ledger, which applies no live cooldowns or governor"),
    }


def _paper_ledger(rows: list[dict], spread_rows: list[dict]) -> list[dict]:
    """Part L's per-trade ledger for the replayed baseline policy.

    Called a ledger and not a result: it is the reconstruction, so it has no live
    cooldowns, no concurrency limit and no risk governor. Every field the spec asks
    for is present, and the ones that are assumptions say so in the report's limits.
    """
    out = []
    for r, sp in zip(rows, spread_rows, strict=True):
        out.append({
            "instrument": r["instrument"],
            "symbol": r["symbol"],
            "direction": r["side"],
            "strike": r["strike"],
            "expiry": r["expiry"],
            "expiry_class": r["expiry_class"],
            "entry_policy": "CONTROL",
            "exit_policy": "A_BASELINE",
            "entry": r["entry"],
            "exit": r["exit"],
            "exit_reason": r["exit_reason"],
            "entry_bid": sp.get("entry_bid"),
            "entry_ask": sp.get("entry_ask"),
            "exit_bid": sp.get("exit_bid"),
            "exit_ask": sp.get("exit_ask"),
            "spread_status": sp.get("spread_status"),
            "slippage_spread_cost": sp.get("total_spread_cost"),
            "fees_assumed": policies.COST_PER_ROUNDTRIP_OPTIONS,
            "mfe_r": r["mfe_r"],
            "mae_r": r["mae_r"],
            "mfe_capture_pct": r["capture"].get("capture_pct"),
            "giveback_r": r["capture"].get("giveback_r"),
            "realised_r": r["realised_r"],
            "net_r": r["net_r"],
            "cost_adjusted_r": r["cost_adjusted_r"],
        })
    return out


def build(db: str, names: list[str], horizon: int, limit: int,
          sessions_wanted: set[str], research_db: str | None = None) -> dict:
    per: dict[str, dict] = {}
    pairs: list[tuple] = []          # (event, fill) across instruments
    events_all: list = []
    series_by_instrument: dict = {}
    trade_rows: list[dict] = []
    spread_rows: list[dict] = []
    cause_rows: list[dict] = []
    cont_rows: list[dict] = []

    for name in names:
        block = _population(db, name, horizon, limit, sessions_wanted)
        per[name] = block
        real = block["series"]
        series_by_instrument[name] = real
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
            cause = causes.classify(ev, fill, row, sp, real, block["candles"],
                                    horizon)
            pairs.append((ev, fill))
            events_all.append(ev)
            trade_rows.append(row)
            spread_rows.append(sp)
            cause_rows.append(cause)
            cont_rows.extend(continuation.observations(ev, fill))

    real_sessions = sorted({r["session"] for r in trade_rows})
    n_sessions = len(real_sessions)

    best_chain = max(
        (b["chain"] for b in per.values()),
        key=lambda c: c.get(REAL, {}).get("snapshots") or 0, default={})
    worst_missing = max((b["candle"].get("missing_pct") or 0.0
                         for b in per.values()), default=0.0)
    gate_block = gates.evaluate(best_chain, {"missing_pct": worst_missing},
                                len(trade_rows), 0, 0)

    calib = calibration.study(pairs, n_sessions)
    calib_splits = calibration.splits(pairs, n_sessions)
    spr = spread.study(spread_rows, n_sessions)
    evidence = causes.evidence_table(cause_rows, n_sessions)
    causes_by_expiry = causes.by_expiry(cause_rows, n_sessions)
    exp_study = expiry_mod.study(trade_rows, n_sessions)
    cap = capture.run(events_all, series_by_instrument, n_sessions)
    cont = continuation.study(cont_rows, n_sessions)
    contracts = contract.study(trade_rows, n_sessions)

    expiry_trades = sum(1 for r in trade_rows
                        if r["expiry_class"] == expiry_mod.EXPIRY_DAY)
    measured = sum(1 for r in spread_rows if r.get("measured"))
    classified = sum(1 for r in trade_rows
                     if r["expiry_class"] != expiry_mod.UNKNOWN)
    smallest_cell = min(
        (b["n"] for b in exp_study["by_class"].values() if b.get("n")), default=0)
    p8_gates = gates8.evaluate(
        resolved=len(trade_rows), sessions=n_sessions,
        expiry_sessions=exp_study["classification"]["instrument_expiry_sessions"],
        expiry_trades=expiry_trades,
        spread_coverage_pct=100.0 * measured / max(1, len(spread_rows)),
        expiry_coverage_pct=100.0 * classified / max(1, len(trade_rows)),
        smallest_cell=smallest_cell)

    candidates = {
        "overall": capture.best_by_net(cap["policies"]),
        **{f"expiry_class:{k}": capture.best_by_net(v)
           for k, v in cap["by_expiry_class"].items()},
    }

    return {
        "phase": 8,
        "scope": "RESEARCH ONLY — replay of recorded history. Production BUY/WAIT/"
                 "NO_TRADE, gates, thresholds, stops, targets, R:R, strike "
                 "selection, direction logic, chase guard, Early Momentum/"
                 "Early-Early, production exits, broker execution and AI production "
                 "behaviour are untouched; no order can be placed from this report.",
        "reuses": "app.research.phase7.paths / policies for every path, MFE, MAE, "
                  "chase, fill and exit — Phase 8 adds no second implementation. The "
                  "extra fixed levels and trail widths are new names in the existing "
                  "Phase 7 policy grammar, executed by the Phase 7 code.",
        "db": db,
        "instruments": names,
        "sessions_analysed": real_sessions,
        "resolved_trades": len(trade_rows),
        "phase7_gates": gate_block,
        "phase8_gates": p8_gates,
        "part4_5_j_confidence_calibration": calib,
        "part_j_calibration_splits": calib_splits,
        "part2_8_h_spread_cost": {k: v for k, v in spr.items() if k != "trades"},
        "part3_11_k_attribution": evidence,
        "part_k_attribution_by_expiry": causes_by_expiry,
        "part_a_b_expiry": exp_study,
        "part_d_e_f_g_profit_capture": cap,
        "part_c_p_continuation": cont,
        "part_i_contract_quality": contracts,
        "part_l_paper": {
            "live_book": _paper_book(research_db),
            "replay_ledger_rows": len(trade_rows),
        },
        "policy_candidates": candidates,
        "per_instrument": {
            name: {
                "reconstruction": b.get("reconstruction"),
                "skipped": b.get("skipped"),
                "real_snapshots": b["chain"].get(REAL, {}).get("snapshots", 0),
                "chain_cadence_sec": b["chain"].get(REAL, {}).get("cadence_sec"),
                "missing_bar_pct": b["candle"].get("missing_pct"),
                "resolved": sum(1 for r in trade_rows if r["instrument"] == name),
            } for name, b in per.items()},
        "trades": trade_rows,
        "spread_trades": spr["trades"],
        "cause_trades": cause_rows,
        "continuation_observations": len(cont_rows),
        "paper_ledger": _paper_ledger(trade_rows, spread_rows),
        "production_confirmation": {
            "buy_logic_unchanged": True,
            "production_exits_unchanged": True,
            "production_stops_unchanged": True,
            "production_targets_unchanged": True,
            "strike_selection_unchanged": True,
            "gates_and_thresholds_unchanged": True,
            "chase_guard_unchanged": True,
            "ai_paper_only": True,
            "real_money_execution_disabled": True,
            "expiry_rule_wired_into_production": False,
            "how_this_is_enforced": "Phase 8 lives in app/research/phase8 and is "
                                    "imported by phase8_report.py only; no "
                                    "production module imports it, and it holds no "
                                    "order-placing code path",
        },
        "limits": [
            "premiums are ~60s snapshot closes: a stop or target touch between two "
            "snapshots is invisible, so MFE/MAE are snapshot extremes",
            "spread is measured from the recorded book; brokerage is still the "
            f"₹{policies.COST_PER_ROUNDTRIP_OPTIONS:.0f}/round trip assumption and "
            "the two are never added into one number",
            "the reconstruction replays the production engine on stored bars; live "
            "cooldowns, concurrency and the risk governor are not applied, so it "
            "produces MORE BUYs than the live funnel did — the paper ledger is a "
            "replay ledger, not live paper performance",
            "greeks are taken as recorded; deep-ITM legs carry degenerate iv/delta "
            "in this dataset and are excluded from greek aggregates, so "
            "delta-derived quantities (room, mirror pricing) are weaker on them",
            "contract expiry is parsed from the tradingsymbol because the recorded "
            "leg has no expiry field; unrecognised grammars are EXPIRY_UNKNOWN and "
            "never guessed",
            "the continuation table is in-sample conditional frequency with no "
            "holdout: it is not a probability and cannot be used as one",
        ],
    }


def _by_instrument_rows(report: dict) -> list[dict]:
    out = []
    for inst, block in report["per_instrument"].items():
        rows = [r for r in report["trades"] if r["instrument"] == inst]
        if not rows:
            out.append({"instrument": inst, "n": 0})
            continue
        rs = [r["realised_r"] for r in rows]
        wins = [r for r in rs if r > 0]
        bad = -sum(r for r in rs if r <= 0)
        out.append({
            "instrument": inst,
            "n": len(rows),
            "win_rate_pct": round(100.0 * len(wins) / len(rs), 1),
            "expectancy_r": round(sum(rs) / len(rs), 3),
            "profit_factor": round(sum(wins) / bad, 3) if bad > 0 else None,
            "median_mfe_r": policies.median([r["mfe_r"] for r in rows]),
            "median_mae_r": policies.median([r["mae_r"] for r in rows]),
            "missing_bar_pct": block.get("missing_bar_pct"),
        })
    return out


def _n(v) -> str:
    return "—" if v is None else str(v)


def render_md(report: dict) -> str:
    calib = report["part4_5_j_confidence_calibration"]
    spr = report["part2_8_h_spread_cost"]
    ev = report["part3_11_k_attribution"]
    exp = report["part_a_b_expiry"]
    cap = report["part_d_e_f_g_profit_capture"]
    cont = report["part_c_p_continuation"]
    con = report["part_i_contract_quality"]
    L: list[str] = []
    A = L.append

    A("# Phase 8 — calibration, costs, attribution, expiry & profit capture "
      "(research only)")
    A("")
    A(f"Sessions analysed: **{', '.join(report['sessions_analysed']) or 'none'}** · "
      f"resolved trades: **{report['resolved_trades']}** · instruments: "
      f"{', '.join(report['instruments'])}")
    A("")
    A("Evidence labels: `OBSERVATION` describes this sample; `HYPOTHESIS` is a "
      "relationship worth testing; `REQUIRES MORE DATA` means the cell is too small "
      "to read; `VALIDATED` requires the gates to pass **and** a chronological "
      "holdout to agree, so it cannot be earned from one dataset and appears nowhere "
      "below. Nothing here is promoted to a trading rule.")
    A("")

    A("## Acceptance gates")
    A("")
    A(f"Phase 8 status: **{report['phase8_gates']['status']}** — failed: "
      f"{', '.join(report['phase8_gates']['failed']) or 'none'}")
    A("")
    A("| gate | need | have | pass | a failure forbids |")
    A("| --- | --- | --- | --- | --- |")
    for g in report["phase8_gates"]["gates"]:
        A(f"| {g['gate']} | {g['need']} | {g['have']} | "
          f"{'PASS' if g['pass'] else 'FAIL'} | {g['forbids']} |")
    A("")
    A("Phase 7 gates carried over: " + ", ".join(
        f"{g['gate']} {'PASS' if g['pass'] else 'FAIL'} ({_n(g['have'])})"
        for g in report["phase7_gates"]["gates"]))
    A("")

    # --- confidence -------------------------------------------------------
    A("## Confidence calibration — is the displayed number meaningful?")
    A("")
    A(f"Population: {calib['population']} · resolved {calib['resolved']}")
    A("")
    A("| bucket | n | predicted | actual target-before-stop % | expectancy R | PF | "
      "median MFE R | median MAE R | label |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in calib["buckets"]:
        if not r["n"]:
            A(f"| {r['bucket']} | 0 | — | — | — | — | — | — | REQUIRES MORE DATA |")
            continue
        A(f"| {r['bucket']} | {r['n']} | {r['predicted_confidence_mean']} | "
          f"{r['actual_target_before_stop_pct']} | {r['expectancy_r']} | "
          f"{_n(r['profit_factor'])} | {_n(r['median_mfe_r'])} | "
          f"{_n(r['median_mae_r'])} | {r['label']} |")
    A("")
    mono = calib["monotonicity"]
    A(f"**Monotonicity verdict: {mono['verdict']}** — calibration_failure = "
      f"`{mono['calibration_failure']}`"
      + ("  → the confidence system is marked **NON-MONOTONIC / MIS-CALIBRATED** "
         "on this sample" if mono["calibration_failure"] else ""))
    A("")
    A(f"- {mono['reading']}")
    if mono["hit_rate_inversions"]:
        A("- hit-rate inversions (higher bucket did worse): " + ", ".join(
            f"{i['from']}→{i['to']} −{i['drop']}pp"
            for i in mono["hit_rate_inversions"]))
    if mono["expectancy_inversions"]:
        A("- expectancy inversions: " + ", ".join(
            f"{i['from']}→{i['to']} −{i['drop']}R"
            for i in mono["expectancy_inversions"]))
    A(f"- Spearman(confidence, realised R) = "
      f"{_n(mono['spearman_confidence_vs_realised_r'])} · buckets with 10+ trades: "
      f"{mono['buckets_with_10_plus']}")
    A(f"- label: **{calib['label']}** — {calib['label_note']}")
    A(f"- {calib['guarantee']}")
    A("")
    A("Splits (expiry / instrument / direction), monotonicity verdict per cell:")
    A("")
    A("| split | cell | resolved | buckets with 10+ | verdict | Spearman |")
    A("| --- | --- | --- | --- | --- | --- |")
    for split, cells in report["part_j_calibration_splits"].items():
        for cell, b in cells.items():
            m = b["monotonicity"]
            A(f"| {split} | {cell} | {b['resolved']} | {m['buckets_with_10_plus']} | "
              f"{m['verdict']} | {_n(m['spearman_confidence_vs_realised_r'])} |")
    A("")

    # --- spread -----------------------------------------------------------
    A("## Spread cost — how much expectancy is lost to the real book?")
    A("")
    A(f"Model: {spr['model']}. A leg without a usable two-sided quote at both ends is "
      "`SPREAD_UNKNOWN` and is excluded, never assigned a fabricated spread.")
    A("")
    A("| group | trades | measured | median entry spread % | median cost R | "
      "spread % of risk | gross expectancy R | net expectancy R | flipped to loss | "
      "label |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")

    def spread_line(name: str, b: dict) -> str:
        if not b.get("measured"):
            return (f"| {name} | {b.get('n', 0)} | 0 | — | — | — | — | — | — | "
                    f"{b.get('label', '')} |")
        return (f"| {name} | {b['n']} | {b['measured']} | "
                f"{_n(b['median_entry_spread_pct'])} | "
                f"{_n(b['median_spread_cost_r'])} | "
                f"{_n(b['median_spread_share_of_risk_pct'])} | "
                f"{b['gross_expectancy_r']} | {b['net_expectancy_r']} | "
                f"{b['flipped_to_loss_by_spread']} | {b['label']} |")

    A(spread_line("ALL", spr["overall"]))
    for name, b in spr["by_side"].items():
        A(spread_line(f"side {name}", b))
    for name, b in spr["by_instrument"].items():
        A(spread_line(name, b))
    A("")

    # --- expiry -----------------------------------------------------------
    A("## Expiry analysis — is expiry day materially different?")
    A("")
    cls = exp["classification"]
    A(f"Classification: {cls['rule']} · coverage {cls['coverage_pct']}% · "
      f"instrument-sessions on expiry day: {cls['instrument_expiry_sessions']}")
    A("")
    A("| class | n | sessions | target-before-stop % | expectancy R | PF | "
      "median MFE R | median MAE R | MFE capture % | give-back R | min to MFE | "
      "min to MAE | held min | spread cost R | premium expansion % | label |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
      "--- | --- | --- | --- |")
    for name, b in exp["by_class"].items():
        if not b.get("n"):
            continue
        A(f"| {name} | {b['n']} | {b['sessions']} | {b['target_before_stop_pct']} | "
          f"{b['expectancy_r']} | {_n(b['profit_factor'])} | {_n(b['median_mfe_r'])} | "
          f"{_n(b['median_mae_r'])} | {_n(b['median_mfe_capture_pct'])} | "
          f"{_n(b['median_giveback_r'])} | {_n(b['median_min_to_mfe'])} | "
          f"{_n(b['median_min_to_mae'])} | {_n(b['median_held_min'])} | "
          f"{_n(b['median_spread_cost_r'])} | "
          f"{_n(b['median_premium_expansion_pct'])} | {b['label']} |")
    A("")
    A("Per instrument (never pooled — the same date is expiry for one instrument and "
      "not another):")
    A("")
    A("| instrument | venue | expiries seen | class | n | expectancy R | "
      "MFE capture % | give-back R | label |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for inst, blk in exp["by_instrument"].items():
        for name, b in blk["by_class"].items():
            if not b.get("n"):
                continue
            A(f"| {inst} | {blk['venue']} | {', '.join(blk['expiries_seen']) or '—'} | "
              f"{name} | {b['n']} | {b['expectancy_r']} | "
              f"{_n(b['median_mfe_capture_pct'])} | {_n(b['median_giveback_r'])} | "
              f"{b['label']} |")
    A("")
    A(f"- {exp['warning']}")
    A("")

    # --- capture ----------------------------------------------------------
    A("## Profit capture and give-back — which exit keeps more, net of costs?")
    A("")
    A(f"{cap['ranking_caveat']}. A policy is also charged with its premature exits: "
      "the share of trades it left while the premium later exceeded its exit price by "
      "0.25R.")
    A("")
    A("| policy | n | gross exp R | **net exp R** | net PF | win % | "
      "median MFE capture % | median give-back R | stopped % | target % | "
      "premature exit % | median held min | label |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name in capture.POLICIES:
        b = cap["policies"].get(name, {})
        if not b.get("n"):
            continue
        A(f"| {name} | {b['n']} | {b['gross_expectancy_r']} | "
          f"**{_n(b['net_expectancy_r'])}** | {_n(b['net_profit_factor'])} | "
          f"{b['win_rate_pct']} | {_n(b['median_mfe_capture_pct'])} | "
          f"{_n(b['median_giveback_r'])} | {b['stopped_pct']} | {b['target_pct']} | "
          f"{b['premature_exit_pct']} | {_n(b['median_held_min'])} | {b['label']} |")
    A("")
    A("Best net policy per slice — **candidates only**, within-sample:")
    A("")
    A("| slice | policy | n | net exp R | baseline net exp R | margin R | "
      "MFE capture % | premature % |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for slice_name, c in report["policy_candidates"].items():
        if not c:
            continue
        A(f"| {slice_name} | {c['policy']} | {c['n']} | {c['net_expectancy_r']} | "
          f"{_n(c['baseline_net_expectancy_r'])} | {_n(c['margin_over_baseline_r'])} | "
          f"{_n(c['median_mfe_capture_pct'])} | {c['premature_exit_pct']} |")
    A("")
    A("### Entry interaction — does a better entry change which exit wins?")
    A("")
    A("| entry quality | policy | n | net exp R | MFE capture % | give-back R |")
    A("| --- | --- | --- | --- | --- | --- |")
    for qual, by_policy in cap["by_entry_quality"].items():
        for name in ("A_BASELINE", "B_FIXED_3PCT", "C_TRAIL_30", "F_HYBRID_HALF"):
            b = by_policy.get(name, {})
            if not b.get("n"):
                continue
            A(f"| {qual} | {name} | {b['n']} | {_n(b['net_expectancy_r'])} | "
              f"{_n(b['median_mfe_capture_pct'])} | {_n(b['median_giveback_r'])} |")
    A("")

    # --- continuation -----------------------------------------------------
    A("## Hold or protect? (continuation, past-information only)")
    A("")
    A(f"{cont['definitions']['features']}")
    A("")
    A(f"**{cont['definitions']['warning']}**")
    A("")
    o = cont["overall"]
    A(f"Observations: {o.get('n_observations', 0)} in-profit quotes · continued "
      f"{_n(o.get('continued_pct'))}% · reversed to break-even "
      f"{_n(o.get('reversed_to_breakeven_pct'))}%")
    A("")
    A("| state (extension · momentum · peak) | observations | continued % | "
      "reversed % | median remaining MFE R | label |")
    A("| --- | --- | --- | --- | --- | --- |")
    for state, b in cont["by_state"].items():
        if not b.get("n"):
            continue
        A(f"| {state} | {b['n_observations']} | {b['continued_pct']} | "
          f"{b['reversed_to_breakeven_pct']} | {_n(b['median_remaining_mfe_r'])} | "
          f"{b['label']} |")
    A("")
    A(f"- separation: {cont['separation']['reading']}")
    A("")

    # --- attribution ------------------------------------------------------
    A("## Loss attribution — what actually causes losses?")
    A("")
    A(f"Resolved {ev['resolved']} · wins {ev['wins']} · losses {ev['losses']} · "
      f"top loss cause: **{ev['top_loss_cause']}**")
    A("")
    A("Priority order (first match wins, so this order *is* the definition of "
      "primary, and it is a research classification rule with no production "
      "counterpart): " + " → ".join(ev["ordering_is_the_definition"]))
    A("")
    A("| cause | loss (primary) | % of losses | win (primary) | loss flagged | "
      "win flagged | flag loss rate % | label |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in ev["table"]:
        A(f"| {r['cause']} | {r['loss_primary']} | {r['loss_primary_pct']} | "
          f"{r['win_primary']} | {r['loss_flagged']} | {r['win_flagged']} | "
          f"{_n(r['flag_loss_rate_pct'])} | {r['label']} |")
    A("")
    A(f"- {ev['reading']}")
    A(f"- label: **{ev['label']}** — {ev['label_note']}")
    A("")
    byx = report["part_k_attribution_by_expiry"]
    A("Does the cause change on expiry day? top cause per class: " + ", ".join(
        f"{k} → {_n(v)}" for k, v in byx["top_cause_by_class"].items()))
    A("")
    A(f"- {byx['reading']}")
    A("")

    # --- contract ---------------------------------------------------------
    A("## Contract / strike quality (descriptive only)")
    A("")
    A(f"Legs recorded {con['legs_recorded']} · usable greeks "
      f"{con['legs_with_usable_greeks']} · degenerate greek legs "
      f"{con['degenerate_greek_legs']}")
    A("")
    A("| delta band | n | expectancy R | target-before-stop % | label |")
    A("| --- | --- | --- | --- | --- |")
    for band, b in con["outcome_by_delta_band"].items():
        A(f"| {band} | {b['n']} | {b['expectancy_r']} | "
          f"{b['target_before_stop_pct']} | {b['label']} |")
    A("")
    A(f"- {con['caveat']}")
    A("")

    # --- paper ------------------------------------------------------------
    A("## Paper results")
    A("")
    live = report["part_l_paper"]["live_book"]
    if live.get("available"):
        A(f"- live AI paper book: **{live['paper_trades']} trades** "
          f"({live['resolved_paper_trades']} resolved) — {live['reading']}")
    else:
        A(f"- live AI paper book not read: {live['reason']}")
    A(f"- replay ledger: {report['part_l_paper']['replay_ledger_rows']} trades with "
      "entry/exit, both books, spread cost, assumed fees, MFE, MAE, capture, "
      "give-back, realised and net R (full rows in the JSON under `paper_ledger`)")
    A("- the replay ledger is not live paper performance: no cooldowns, no "
      "concurrency limit, no risk governor")
    A("")

    A("## By instrument (context, not a ranking)")
    A("")
    A("| instrument | n | win % | expectancy R | PF | median MFE R | median MAE R | "
      "missing bars % |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in _by_instrument_rows(report):
        if not r["n"]:
            A(f"| {r['instrument']} | 0 | — | — | — | — | — | — |")
            continue
        A(f"| {r['instrument']} | {r['n']} | {r['win_rate_pct']} | "
          f"{r['expectancy_r']} | {_n(r['profit_factor'])} | "
          f"{_n(r['median_mfe_r'])} | {_n(r['median_mae_r'])} | "
          f"{_n(r['missing_bar_pct'])} |")
    A("")

    A("## Limits carried into every number above")
    A("")
    for lim in report["limits"]:
        A(f"- {lim}")
    A("")

    # --- the question -----------------------------------------------------
    A("## The question this phase was set")
    A("")
    A("> On expiry day, can the system identify when a profitable option move still "
      "has enough continuation probability to hold, and when the probability of "
      "reversal is high enough that profit should be protected?")
    A("")
    sep = cont["separation"]
    exp_day = exp["by_class"].get("EXPIRY_DAY", {})
    if not exp_day.get("n"):
        A("**Not on this data — and the reason is not subtle: there are no resolved "
          "trades classified EXPIRY_DAY in this sample.** The expiry-specific half of "
          "the question is unanswerable here, not answered negatively.")
    elif sep.get("states_with_20_plus_observations", 0) < 2:
        A(f"**Not yet.** {exp_day['n']} expiry-day trades produced too few in-profit "
          "observations for the hold/protect states to separate measurably, so the "
          "system cannot currently tell 'still running' from 'about to give it back' "
          "on evidence — only on assumption.")
    else:
        A(f"**Partially, as a candidate.** Continuation frequency varies by "
          f"{_n(sep['continuation_spread_pp'])}pp across states carrying 20+ "
          "observations, which means the states are not interchangeable on this "
          "sample. That is a candidate signal to test over many sessions, not a "
          "probability, and not a rule: one session cannot establish it.")
    A("")
    A("What would answer it: 20+ sessions including 4+ expiry sessions per "
      "instrument, then the same table computed on the earlier sessions and checked "
      "on the later ones. Until the check exists, no state may be used to hold or "
      "protect a live trade.")
    A("")

    A("## What this changes, and what it does not")
    A("")
    A("**Immediately actionable as an observation (no code change, no rule change):**")
    A("")
    if mono["calibration_failure"]:
        A("- The displayed confidence did not rank outcomes in this sample. Until it "
          "does, read it as a description of how many gates passed, not as a "
          "probability of profit.")
    else:
        A("- Confidence ordering held in this sample; that is not yet evidence it "
          "generalises, so it changes nothing on its own.")
    A(f"- Spread is real and measurable: the book takes a median "
      f"{_n(spr['overall'].get('median_spread_share_of_risk_pct'))}% of the risk unit "
      "per round trip on the measured legs, and it is wildly uneven between "
      "instruments — that unevenness is visible on the board before you trade.")
    A(f"- Losses concentrate in **{ev['top_loss_cause']}**. Whether that is a "
      "signal-side or execution-side cause decides which half of the system is worth "
      "working on next.")
    A("")
    A("**Needs 20+ sessions (and 4+ expiry sessions) before it can support any "
      "change:**")
    A("")
    A("- every policy ranking above, including the candidates table — a within-sample "
      "best is the single easiest way to overfit one day;")
    A("- any expiry-specific exit behaviour, and any claim that expiry is different;")
    A("- any recalibration of the displayed confidence;")
    A("- the continuation states as a hold/protect decision.")
    A("")
    A("**Should not be modelled yet:**")
    A("")
    A("- no AI model on this sample: per-cell counts are in single digits and the "
      "missing one-minute bar rate is far above the 5% gate, so ATR/VWAP/regime "
      "features are contaminated;")
    A("- greeks-derived features need the degenerate-leg problem fixed at the "
      "recording end first;")
    A("- no feature may be called predictive from one day, however large its "
      "apparent effect.")
    A("")

    A("## Production confirmation")
    A("")
    for k, v in report["production_confirmation"].items():
        if isinstance(v, bool):
            A(f"- {k.replace('_', ' ')}: **{'yes' if v else 'no'}**")
    A(f"- enforcement: {report['production_confirmation']['how_this_is_enforced']}")
    A("- no production change is authorised by this phase")
    A("")
    A(f"status={report['phase8_gates']['status']} "
      f"resolved={report['resolved_trades']} "
      f"sessions={len(report['sessions_analysed'])} "
      f"expiry_day_trades={exp['by_class'].get('EXPIRY_DAY', {}).get('n', 0)} "
      f"calibration_failure={mono['calibration_failure']} "
      f"top_loss_cause={ev['top_loss_cause']} "
      f"failed_gates={','.join(report['phase8_gates']['failed']) or 'none'}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=os.path.join(settings.data_dir, "history.db"))
    ap.add_argument("--research-db", default="",
                    help="research.db, read only for the live AI paper book count")
    ap.add_argument("--instruments", default="")
    ap.add_argument("--session", default="",
                    help="comma-separated IST dates (YYYY-MM-DD); default all")
    ap.add_argument("--horizon", type=int, default=paths.HORIZON_BARS)
    ap.add_argument("--limit", type=int, default=0,
                    help="most recent N candles per instrument, 0 = all")
    ap.add_argument("--out", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"no history db at {args.db}")
    names = [x.strip().upper() for x in args.instruments.split(",") if x.strip()]
    if not names:
        from app.research.phase7.dataset import instruments_with_chains
        names = instruments_with_chains(args.db)[:5]
    wanted = {x.strip() for x in args.session.split(",") if x.strip()}

    report = build(args.db, names, args.horizon, args.limit, wanted,
                   args.research_db or None)
    md = render_md(report)
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
    if args.md:
        with open(args.md, "w") as fh:
            fh.write(md + "\n")
    print(md)


if __name__ == "__main__":
    main()
