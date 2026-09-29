"""Phase 27 §4 — run both timeframes, answer the questions, apply the stop rule.

One run produces everything: the resampling counts, the pool economics per
timeframe, the ranked table, the per-window results, walk-forward, the robustness
grid, the baseline reconstructions, the 1-minute reference row read back from
Phase 24, and a conclusion that either names a lead or states plainly that the
environment shows no robust intraday directional edge.

The conclusion is not written by hand after the fact. It is derived from the gate
verdicts, and the stopping rule the operator set *before* the run is carried in
the artefact next to it, so nobody — including the author of this file — can move
the bar after seeing the numbers.
"""
from __future__ import annotations

import json
import math
import os
import time

import numpy as np

from app.research.phase24 import report as p24report
from app.research.phase27 import (
    REJECTED,
    REQUIRES_MORE_DATA,
    RESAMPLE_CLAIM,
    SPREAD_CLAIM,
    STOP_RULE,
    VALIDATED,
    VERSION,
    bars,
    conditions,
    discover,
    outcomes_note,
    pool,
    rank,
    study,
)

ARTEFACT_DIR = "data/phase27"
STRESS_TOP_N = 10          # cost stress re-resolves pools; only leaders earn it
NO_LOSING_TRADE = "no losing trade in this window — ratio undefined"
NO_EDGE = "NO_ROBUST_INTRADAY_DIRECTIONAL_EDGE_FOUND"


def _abs(rel: str) -> str:
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
    return os.path.join(backend, rel)


def artefact_dir(out_dir: str | None = None) -> str:
    return out_dir if out_dir else _abs(ARTEFACT_DIR)


def jsonable(payload: object) -> object:
    """The payload as strict JSON: no NaN, no numpy types written as strings.

    The metrics come back as numpy scalars and arrays. Left alone they are written
    through ``default=str``, which turns a number into ``"1.5"`` and quietly makes
    every artefact unreadable by anything that expects a number.
    """
    if isinstance(payload, dict):
        return {k: jsonable(v) for k, v in payload.items()}
    if isinstance(payload, np.ndarray):
        return [jsonable(v) for v in payload.tolist()]
    if isinstance(payload, (list, tuple)):
        return [jsonable(v) for v in payload]
    if isinstance(payload, np.bool_):
        return bool(payload)
    if isinstance(payload, np.integer):
        return int(payload)
    if isinstance(payload, np.floating):
        payload = float(payload)
    if isinstance(payload, float) and not math.isfinite(payload):
        return NO_LOSING_TRADE if payload > 0 else None
    return payload


def jsonable_dict(payload: dict) -> dict:
    return {k: jsonable(v) for k, v in payload.items()}


def _write(name: str, payload: object, out_dir: str | None = None) -> str:
    d = artefact_dir(out_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(jsonable(payload), fh, indent=1, default=str, allow_nan=False)
    return path


def one_minute_reference() -> dict:
    """Phase 24's own 1-minute economics, read back rather than recomputed.

    Recomputing the 1-minute pool here would take longer than the rest of the
    study and would risk reporting a number that disagrees with the phase that
    published it. If Phase 24 has not been run on this machine the row is absent
    and says so — it is never replaced with an assumption.
    """
    rows = p24report.latest_geometry()
    if not rows:
        return {
            "available": False,
            "note": (
                "Phase 24's geometry sweep is not on disk, so the 1-minute "
                "reference row is unavailable. Run "
                "`python -m app.research.phase24.cli run` to produce it; it is "
                "not estimated here"
            ),
        }
    return {
        "available": True,
        "timeframe_minutes": 1,
        "rows": rows,
        "note": (
            "measured by Phase 24 on the same stored 1-minute series with the "
            "same cost model; read back, not recomputed"
        ),
    }


def _timeframe_block(
    instruments: tuple[str, ...],
    timeframe: int,
    *,
    quick: bool,
) -> dict:
    """Everything measured at one timeframe: pools, hypotheses, ranked rules."""
    bands = discover.STOP_BANDS[:2] if quick else discover.STOP_BANDS
    pools: dict[tuple[str, float], pool.Pool] = {}
    mask_cache: dict[tuple[str, float], dict] = {}
    pool_summaries: list[dict] = []
    candidates: list[dict] = []
    windows: dict = {}
    tests = 0

    for inst in instruments:
        for band in bands:
            p = pool.build(inst, timeframe, stop_atr=band)
            if p is None:
                continue
            key = (inst, band)
            pools[key] = p
            s = discover.Search(p, band)
            # The search evaluates the vocabulary once; the same masks are reused
            # for validation, holdout, regimes and baselines rather than rebuilt.
            mask_cache[key] = s.masks
            if not windows:
                windows = discover.window_note(p.ts)
            summary = pool.summary(p)
            summary["stop_band_atr"] = band
            summary["median_cost_points_per_round_trip"] = round(
                float(np.median(p.out.cost_points)) if len(p) else 0.0, 3
            )
            summary["cost_as_fraction_of_risk"] = round(
                float(np.median(p.out.cost_points) / np.median(p.out.risk))
                if len(p) else 0.0, 4
            )
            summary["window_trades"] = s.window_trades()
            pool_summaries.append(summary)

            survivors = s.run()
            for rule in survivors:
                rule["t1_r"] = outcomes_note.T1_R
                rule["dev_gate_passed"] = True
                candidates.append(study.evaluate_rule(p, mask_cache[key], rule, s))
            passed = {
                tuple(sorted(r["conditions"])) + (r["side"],) for r in survivors
            }
            for rule in s.near_misses:
                if tuple(sorted(rule["conditions"])) + (rule["side"],) in passed:
                    continue
                rule["t1_r"] = outcomes_note.T1_R
                rule["dev_gate_passed"] = False
                candidates.append(study.evaluate_rule(p, mask_cache[key], rule, s))
            tests += s.tests

    ranked = rank.rank(candidates, tests=tests)
    for r in ranked[:STRESS_TOP_N]:
        r["cost_sensitivity"] = study.cost_sensitivity(r, quick=quick)
    for r in ranked:
        if "cost_sensitivity" not in r:
            r["cost_sensitivity"] = []
            r["cost_stress"] = "NOT_RUN_NOT_A_LEADER"
    ranked = rank.rank(ranked, tests=tests)

    base_rows: dict[str, dict] = {}
    for (inst, band), p in pools.items():
        if band == bands[0]:
            base_rows[inst] = study.baselines(p, mask_cache[(inst, band)])

    leads = [r for r in ranked if r["status"] == rank.RESEARCH_LEAD]
    return {
        "timeframe_minutes": timeframe,
        "stop_bands": list(bands),
        "windows": windows,
        "condition_windows": conditions.window_labels(timeframe),
        "pools": pool_summaries,
        "hypotheses_evaluated": tests,
        "candidates_surviving_development": sum(
            1 for r in candidates if r.get("dev_gate_passed")
        ),
        "cohorts_examined_including_near_misses": len(candidates),
        "ranked": ranked,
        "top10": ranked[:10],
        "research_leads": leads,
        "baselines": base_rows,
        "diagnostics": _diagnostics(ranked),
    }


def _diagnostics(ranked: list[dict]) -> dict:
    """How far the search got, clause by clause, so "nothing" can be quantified."""
    def _pos(row: dict, window: str) -> bool:
        return float((row.get(window) or {}).get("avg_net_r") or -1.0) > 0.0

    scored = [r for r in ranked if (r.get("holdout") or {}).get("trades", 0) > 0]
    return {
        "cohorts_ranked": len(ranked),
        "positive_development": sum(1 for r in ranked if _pos(r, "development")),
        "positive_validation": sum(1 for r in ranked if _pos(r, "validation")),
        "positive_holdout": sum(1 for r in ranked if _pos(r, "holdout")),
        "positive_all_three_windows": sum(
            1 for r in ranked
            if _pos(r, "development") and _pos(r, "validation") and _pos(r, "holdout")
        ),
        "holdout_sample_adequate": sum(
            1 for r in scored
            if (r["holdout"].get("trades") or 0) >= rank.MIN_HOLDOUT_TRADES
        ),
        "walk_forward_stable": sum(
            1 for r in ranked if (r.get("walk_forward") or {}).get("stable")
        ),
        "survives_every_stress_variant": sum(
            1 for r in ranked
            if r.get("cost_sensitivity")
            and all(v.get("survives") for v in r["cost_sensitivity"])
        ),
        "fdr_survivors": sum(1 for r in ranked if r.get("fdr_survivor")),
        "research_leads": sum(
            1 for r in ranked if r["status"] == rank.RESEARCH_LEAD
        ),
    }


def run(
    *,
    instruments: tuple[str, ...] | None = None,
    timeframes: tuple[int, ...] = bars.TIMEFRAMES,
    quick: bool = False,
    out_dir: str | None = None,
) -> dict:
    """Run the whole higher-timeframe study and write the artefacts."""
    started = time.time()
    cov = bars.coverage()
    usable = tuple(r["instrument"] for r in cov["usable"])
    if instruments:
        usable = tuple(i for i in usable if i in instruments)

    blocks: list[dict] = []
    for tf in timeframes:
        if not usable:
            break
        blocks.append(_timeframe_block(usable, int(tf), quick=quick))

    out = {
        "version": VERSION,
        "generated_at": int(started),
        "runtime_seconds": round(time.time() - started, 1),
        "quick_mode": quick,
        "instruments": list(usable),
        "timeframes": [int(t) for t in timeframes],
        "coverage": cov,
        "resample_claim": RESAMPLE_CLAIM,
        "spread_claim": SPREAD_CLAIM,
        "stop_rule": STOP_RULE,
        "execution": outcomes_note.execution_note(),
        "timeframe_results": blocks,
        "geometry_sweep": (
            [] if quick else study.geometry_sweep(usable, tuple(int(t) for t in timeframes))
        ),
        "one_minute_reference": one_minute_reference(),
        "data_status": "OK" if usable else "NO_USABLE_HISTORY",
        "option_logic": (
            "frozen: this study touches no option strategy logic, no captured "
            "option book and no order path. The option-book capture keeps running "
            "and is not read here"
        ),
        "paper_only": True,
    }
    out["comparison"] = comparison(out)
    out["answers"] = answers(out)
    out["conclusion"] = conclusion(out)
    out["artefacts"] = _write_all(out, out_dir)
    return out


def comparison(out: dict) -> dict:
    """1-minute vs 5-minute vs 15-minute, on the numbers that decide the study."""
    rows: list[dict] = []
    ref = out.get("one_minute_reference") or {}
    if ref.get("available"):
        for g in ref.get("rows") or []:
            if float(g.get("t1_r") or 0) != 1.5:
                continue
            rows.append({
                "timeframe_minutes": 1,
                "instrument": g.get("instrument"),
                "stop_band_atr": g.get("stop_band_atr"),
                "trades": g.get("trades"),
                "t1_before_sl_pct": g.get("t1_before_sl_pct"),
                "breakeven_t1_pct_before_costs": g.get(
                    "breakeven_t1_pct_before_costs"
                ),
                "avg_net_r": g.get("avg_net_r"),
                "cost_as_fraction_of_risk": g.get("cost_as_fraction_of_risk"),
                "source": "PHASE24_GEOMETRY_SWEEP",
            })
    for block in out.get("timeframe_results") or []:
        for p in block.get("pools") or []:
            rows.append({
                "timeframe_minutes": block["timeframe_minutes"],
                "instrument": p.get("instrument"),
                "stop_band_atr": p.get("stop_band_atr"),
                "trades": p.get("resolved"),
                "t1_before_sl_pct": p.get("base_t1_before_sl_pct"),
                "breakeven_t1_pct_before_costs": round(100.0 / (1.0 + 1.5), 2),
                "avg_net_r": p.get("base_avg_net_r"),
                "cost_as_fraction_of_risk": p.get("cost_as_fraction_of_risk"),
                "median_risk_points": p.get("median_atr_points"),
                "source": "PHASE27_POOL",
            })
    return {
        "rows": rows,
        "note": (
            "unconditional pool economics: no entry rule is applied, so this table "
            "shows what the geometry costs at each bar size before any search is "
            "credited or blamed. A 1.5R target needs 40% of trades to reach T1 "
            "before the stop just to break even before costs"
        ),
    }


def _best(block: dict) -> dict | None:
    ranked = block.get("ranked") or []
    return ranked[0] if ranked else None


def answers(out: dict) -> dict:
    """The questions this branch was commissioned to answer, one line each."""
    per_tf: dict[str, dict] = {}
    for block in out.get("timeframe_results") or []:
        tf = block["timeframe_minutes"]
        d = block["diagnostics"]
        best = _best(block)
        per_tf[f"{tf}m"] = {
            "any_candidate_surviving_all_gates": d["research_leads"] > 0,
            "hypotheses_evaluated": block["hypotheses_evaluated"],
            "cohorts_ranked": d["cohorts_ranked"],
            "cleared_development": block["candidates_surviving_development"],
            "positive_validation": d["positive_validation"],
            "positive_untouched_holdout": d["positive_holdout"],
            "positive_in_all_three_windows": d["positive_all_three_windows"],
            "walk_forward_stable": d["walk_forward_stable"],
            "survives_every_stress_variant": d["survives_every_stress_variant"],
            "fdr_survivors": d["fdr_survivors"],
            "strongest_candidate": (
                {
                    "strategy_id": best["strategy_id"],
                    "instrument": best["instrument"],
                    "side": best["side"],
                    "stop_band_atr": best["stop_band_atr"],
                    "conditions": best["conditions"],
                    "development_avg_net_r": (best.get("development") or {}).get(
                        "avg_net_r"
                    ),
                    "validation_avg_net_r": (best.get("validation") or {}).get(
                        "avg_net_r"
                    ),
                    "holdout_avg_net_r": (best.get("holdout") or {}).get("avg_net_r"),
                    "holdout_trades": (best.get("holdout") or {}).get("trades"),
                    "holdout_profit_factor": (best.get("holdout") or {}).get(
                        "profit_factor"
                    ),
                    "status": best["status"],
                    "why_it_failed": best["failed_clauses"],
                }
                if best else "no cohort was large enough to rank"
            ),
        }
    leads = _all_leads(out)
    return {
        "per_timeframe": per_tf,
        "either_timeframe_has_a_lead": bool(leads),
        "does_a_higher_timeframe_fix_the_cost_arithmetic": _cost_answer(out),
        "does_a_higher_timeframe_fix_the_direction_problem": _direction_answer(out),
        "option_strategy_logic_touched": False,
        "option_capture_touched": False,
        "anything_ready_for_paper_trading": bool(leads),
        "stop_rule": STOP_RULE,
        "stop_rule_triggered": not leads,
    }


def _all_leads(out: dict) -> list[dict]:
    leads: list[dict] = []
    for block in out.get("timeframe_results") or []:
        leads.extend(block.get("research_leads") or [])
    return leads


def _cost_answer(out: dict) -> dict:
    """Did the bar size actually reduce cost as a fraction of risk? By how much?"""
    by_tf: dict[int, list[float]] = {}
    for row in (out.get("comparison") or {}).get("rows") or []:
        frac = row.get("cost_as_fraction_of_risk")
        if isinstance(frac, (int, float)):
            by_tf.setdefault(int(row["timeframe_minutes"]), []).append(float(frac))
    medians = {
        tf: round(float(np.median(v)), 4) for tf, v in sorted(by_tf.items()) if v
    }
    return {
        "median_cost_as_fraction_of_risk_by_timeframe": medians,
        "answer": (
            "yes — the charges are a materially smaller fraction of the risk taken "
            "at a larger bar size"
            if len(medians) > 1
            and min(medians.values()) < 0.5 * max(medians.values())
            else "not materially, on these numbers"
        ),
        "note": (
            "this is the one thing a higher timeframe was expected to fix, and it "
            "is measured rather than argued. Fixing the cost fraction is necessary, "
            "not sufficient: the direction still has to be there"
        ),
    }


def _direction_answer(out: dict) -> dict:
    """Whether the pool's own hit rate moved off the pre-cost breakeven."""
    rows: list[dict] = []
    for block in out.get("timeframe_results") or []:
        for p in block.get("pools") or []:
            achieved = p.get("base_t1_before_sl_pct")
            if not isinstance(achieved, (int, float)):
                continue
            rows.append({
                "timeframe_minutes": block["timeframe_minutes"],
                "instrument": p.get("instrument"),
                "stop_band_atr": p.get("stop_band_atr"),
                "achieved_t1_pct": achieved,
                "breakeven_t1_pct": 40.0,
                "gap_pct": round(float(achieved) - 40.0, 2),
            })
    worst = min((r["gap_pct"] for r in rows), default=None)
    best = max((r["gap_pct"] for r in rows), default=None)
    if best is None:
        answer = "not measured: no pool resolved any candidate"
    elif best <= 0.0:
        answer = (
            f"no — the best unconditional pool still reaches T1 before the stop "
            f"{abs(best):.2f} points BELOW the {40.0:.0f}% a 1.5R target needs "
            "before costs, so the larger bar changed the cost arithmetic and not "
            "the direction. A rule has to supply the whole difference"
        )
    elif best < 5.0:
        answer = (
            f"marginally — the best unconditional pool is {best:.2f} points above "
            "the pre-cost breakeven, which one spread erases. Treat it as no"
        )
    else:
        answer = (
            f"at least one pool's unconditional hit rate is {best:.2f} points above "
            "the pre-cost breakeven, which is worth reading closely"
        )
    return {
        "rows": rows,
        "best_gap_vs_breakeven_pct": best,
        "worst_gap_vs_breakeven_pct": worst,
        "answer": answer,
    }


def conclusion(out: dict) -> dict:
    """The verdict, derived from the gate rather than written afterwards."""
    if out.get("data_status") != "OK":
        return {
            "verdict": REQUIRES_MORE_DATA,
            "headline": (
                "the study did not run: no instrument on this machine has a "
                "five-year 1-minute series to aggregate, so nothing was searched. "
                "This is not a negative result about any strategy"
            ),
            "stop_rule": STOP_RULE,
            "stop_rule_triggered": False,
            "paper_only": True,
        }
    leads = _all_leads(out)
    if leads:
        best = max(leads, key=lambda r: r["score"])
        return {
            "verdict": VALIDATED,
            "headline": (
                f"{best['strategy_id']} cleared every clause at "
                f"{best['timeframe_minutes']}-minute bars"
            ),
            "research_label": best["status"],
            "leads": [
                {
                    "strategy_id": r["strategy_id"],
                    "timeframe_minutes": r["timeframe_minutes"],
                    "instrument": r["instrument"],
                    "side": r["side"],
                    "conditions": r["conditions"],
                    "holdout": r["holdout"],
                }
                for r in leads
            ],
            "next_step": (
                "a lead is an argument for forward paper collection at this "
                "timeframe, not a promotion. Nothing is wired to the live engine "
                "and no order path is changed by this study"
            ),
            "stop_rule": STOP_RULE,
            "stop_rule_triggered": False,
            "paper_only": True,
        }
    closest = _closest(out)
    return {
        "verdict": REJECTED,
        "headline": NO_EDGE,
        "statement": _rejection_statement(out),
        "how_far_it_got": {
            f"{b['timeframe_minutes']}m": b["diagnostics"]
            for b in out.get("timeframe_results") or []
        },
        "closest_candidate": closest,
        "stop_rule": STOP_RULE,
        "stop_rule_triggered": True,
        "what_this_does_not_say": [
            "it does not say the market has no edge; it says this data and this "
            "execution model do not contain one that survives out of sample",
            "it does not say the loss-avoidance findings are wrong — the measured "
            "hurdle and spread vetoes from Phase 25/26 are unaffected by this run",
            "it does not close the option-book capture, which keeps running and "
            "keeps making the earlier leads answerable with more sessions",
        ],
        "paper_only": True,
    }


def _rejection_statement(out: dict) -> str:
    """Say how the search failed, counted, rather than asserting it found nothing.

    "Nothing survived" and "nothing was ever positive" are different claims, and
    only one of them is usually true. The clause that actually stopped each
    timeframe is named from its own diagnostics. Each count is over the ranked
    cohorts and is independent of the others — they are not a nested funnel, so
    they are worded as separate tallies rather than "of those".
    """
    parts: list[str] = []
    for block in out.get("timeframe_results") or []:
        d = block["diagnostics"]
        parts.append(
            f"at {block['timeframe_minutes']}-minute bars "
            f"{block['hypotheses_evaluated']} hypotheses were evaluated and "
            f"{block['candidates_surviving_development']} cleared development; of "
            f"the {d.get('cohorts_ranked', len(block.get('ranked') or []))} "
            f"cohorts that reached the table, "
            f"{d['positive_all_three_windows']} were positive on development, the "
            f"validation year and the untouched holdout at once, "
            f"{d['holdout_sample_adequate']} had an adequate holdout sample, "
            f"{d['walk_forward_stable']} were positive in every "
            f"walk-forward fold, {d['survives_every_stress_variant']} survived "
            f"every cost/slippage/timing variant and "
            f"{d['fdr_survivors']} survived the multiple-testing correction — "
            f"leaving {d['research_leads']} that cleared every clause at once"
        )
    return (
        "; ".join(parts)
        + ". On the pre-committed stopping rule, the evidence says this data and "
        "execution environment does not contain a robust intraday directional edge "
        "that survives out of sample, and the search stops here"
    )


def _closest(out: dict) -> dict | list | str:
    """The best-scoring cohort on each timeframe, with its exact failure reasons."""
    rows: list[dict] = []
    for block in out.get("timeframe_results") or []:
        best = _best(block)
        if not best:
            continue
        rows.append({
            "timeframe_minutes": block["timeframe_minutes"],
            "strategy_id": best["strategy_id"],
            "instrument": best["instrument"],
            "side": best["side"],
            "stop_band_atr": best["stop_band_atr"],
            "conditions": best["conditions"],
            "complexity": best["complexity"],
            "development": best.get("development"),
            "validation": best.get("validation"),
            "holdout": best.get("holdout"),
            "walk_forward": {
                "folds_positive": (best.get("walk_forward") or {}).get(
                    "folds_positive"
                ),
                "folds_scored": (best.get("walk_forward") or {}).get("folds_scored"),
            },
            "cost_sensitivity": best.get("cost_sensitivity"),
            "fdr_survivor": best.get("fdr_survivor"),
            "status": best["status"],
            "why_it_failed": best["failed_clauses"],
        })
    return rows or "no cohort on either timeframe was large enough to rank"


def _write_all(out: dict, out_dir: str | None = None) -> dict[str, str]:
    """One artefact per deliverable, all strict JSON."""
    ranked_all: list[dict] = []
    for block in out["timeframe_results"]:
        ranked_all.extend(block["ranked"])
    return {
        "study_report": _write("p27_study_report.json", {
            "version": out["version"],
            "generated_at": out["generated_at"],
            "runtime_seconds": out["runtime_seconds"],
            "quick_mode": out["quick_mode"],
            "instruments": out["instruments"],
            "timeframes": out["timeframes"],
            "coverage": out["coverage"],
            "resample_claim": out["resample_claim"],
            "spread_claim": out["spread_claim"],
            "stop_rule": out["stop_rule"],
            "execution": out["execution"],
            "option_logic": out["option_logic"],
            "per_timeframe": [
                {
                    "timeframe_minutes": b["timeframe_minutes"],
                    "windows": b["windows"],
                    "condition_windows": b["condition_windows"],
                    "pools": b["pools"],
                    "hypotheses_evaluated": b["hypotheses_evaluated"],
                    "candidates_surviving_development": b[
                        "candidates_surviving_development"
                    ],
                    "cohorts_examined_including_near_misses": b[
                        "cohorts_examined_including_near_misses"
                    ],
                    "diagnostics": b["diagnostics"],
                }
                for b in out["timeframe_results"]
            ],
            "comparison": out["comparison"],
            "one_minute_reference": out["one_minute_reference"],
            "answers": out["answers"],
            "conclusion": out["conclusion"],
            "paper_only": True,
        }, out_dir),
        "ranked_table": _write("p27_ranked_strategies.json", ranked_all, out_dir),
        "top10": _write("p27_top10.json", [
            {"timeframe_minutes": b["timeframe_minutes"], "top10": b["top10"]}
            for b in out["timeframe_results"]
        ], out_dir),
        "walk_forward": _write("p27_walk_forward.json", [
            {
                "strategy_id": r["strategy_id"],
                "timeframe_minutes": r["timeframe_minutes"],
                "walk_forward": r["walk_forward"],
            }
            for r in ranked_all
        ], out_dir),
        "holdout": _write("p27_final_holdout.json", [
            {
                "strategy_id": r["strategy_id"],
                "timeframe_minutes": r["timeframe_minutes"],
                "development": r["development"],
                "validation": r["validation"],
                "holdout": r["holdout"],
            }
            for r in ranked_all
        ], out_dir),
        "robustness": _write("p27_robustness.json", [
            {
                "strategy_id": r["strategy_id"],
                "timeframe_minutes": r["timeframe_minutes"],
                "cost_sensitivity": r.get("cost_sensitivity"),
                "parameter_perturbation": r.get("parameter_perturbation"),
                "regimes": r.get("regimes"),
            }
            for r in ranked_all
        ], out_dir),
        "geometry_sweep": _write(
            "p27_geometry_sweep.json", out["geometry_sweep"], out_dir
        ),
        "comparison": _write("p27_timeframe_comparison.json", out["comparison"], out_dir),
        "baselines": _write("p27_baseline_comparison.json", [
            {"timeframe_minutes": b["timeframe_minutes"], "baselines": b["baselines"]}
            for b in out["timeframe_results"]
        ], out_dir),
        "rejected_log": _write("p27_rejected.json", [
            {
                "strategy_id": r["strategy_id"],
                "timeframe_minutes": r["timeframe_minutes"],
                "instrument": r["instrument"],
                "side": r["side"],
                "stop_band_atr": r["stop_band_atr"],
                "conditions": r["conditions"],
                "status": r["status"],
                "gate_verdict": r["gate_verdict"],
                "failed_clauses": r["failed_clauses"],
            }
            for r in ranked_all if r["status"] != rank.RESEARCH_LEAD
        ], out_dir),
        "leads": _write("p27_research_leads.json", _all_leads(out) or {
            "leads": [],
            "note": (
                "no cohort on either timeframe cleared the gate, so no lead is "
                "published and no fingerprint is offered for live matching"
            ),
        }, out_dir),
    }


def latest(out_dir: str | None = None) -> dict | None:
    """The last written study report, for the read-only API."""
    path = os.path.join(artefact_dir(out_dir), "p27_study_report.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _latest_list(name: str, out_dir: str | None = None) -> list[dict]:
    path = os.path.join(artefact_dir(out_dir), name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return rows if isinstance(rows, list) else []


def latest_ranked(out_dir: str | None = None) -> list[dict]:
    return _latest_list("p27_ranked_strategies.json", out_dir)


def latest_comparison(out_dir: str | None = None) -> dict:
    path = os.path.join(artefact_dir(out_dir), "p27_timeframe_comparison.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload if isinstance(payload, dict) else {}


def latest_geometry(out_dir: str | None = None) -> list[dict]:
    return _latest_list("p27_geometry_sweep.json", out_dir)


__all__ = [
    "run", "latest", "latest_ranked", "latest_comparison", "latest_geometry",
    "answers", "conclusion", "comparison", "one_minute_reference", "jsonable",
    "jsonable_dict", "artefact_dir", "NO_EDGE",
]
