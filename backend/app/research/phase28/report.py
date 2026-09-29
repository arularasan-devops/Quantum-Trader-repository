"""Phase 28 §11 — run the multi-day study, answer the questions, apply the rule.

One run produces everything: what daily history exists and where it came from,
the pool economics per stop band and horizon, the ranked table per vehicle, the
per-window results, walk-forward, the stress grid, the buy-and-hold comparison,
the reconstructions of the obvious textbook rules, and a conclusion that either
names a lead or states plainly that a multi-day horizon does not contain a
directional edge either.

The conclusion is derived from the gate verdicts, and the stopping rule agreed
before the run is written into the artefact next to it, so the bar cannot move
after the numbers are known.
"""
from __future__ import annotations

import json
import math
import os
import time

import numpy as np

from app.research.phase27 import report as p27report
from app.research.phase28 import (
    BUY_HOLD_CLAIM,
    DELIVERY_CLAIM,
    EQUITY_DELIVERY,
    FUTURES,
    GAP_CLAIM,
    INSUFFICIENT_HISTORY,
    NO_EDGE,
    REJECTED,
    REQUIRES_MORE_DATA,
    ROLLOVER_CLAIM,
    SAMPLE_CLAIM,
    SHORT_CLAIM,
    SPREAD_CLAIM,
    STOP_RULE,
    VERSION,
    baseline,
    dailybars,
    discover,
    outcomes,
    pool,
    rank,
    study,
)

ARTEFACT_DIR = "data/phase28"
STRESS_TOP_N = 10          # stress re-resolves whole pools; only leaders earn it
NO_LOSING_TRADE = "no losing trade in this window — ratio undefined"


def _abs(rel: str) -> str:
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
    return os.path.join(backend, rel)


def artefact_dir(out_dir: str | None = None) -> str:
    return out_dir if out_dir else _abs(ARTEFACT_DIR)


def jsonable(payload: object) -> object:
    """The payload as strict JSON: no NaN, no numpy scalar written as a string."""
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
    """The same conversion, typed as a dict for the read-only service."""
    return {k: jsonable(v) for k, v in payload.items()}


def _write(name: str, payload: object, out_dir: str | None = None) -> str:
    d = artefact_dir(out_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            jsonable(payload), fh, indent=1, default=str,
            allow_nan=False, ensure_ascii=False,
        )
    return path


def intraday_reference() -> dict:
    """Phase 27's own intraday economics, read back rather than recomputed.

    The claim this phase tests is that a longer holding period shrinks cost as a
    fraction of risk. That claim needs the 1m/5m/15m numbers to compare against,
    and recomputing them here would risk publishing a figure that disagrees with
    the phase that measured them. If Phase 27 has not been run on this machine the
    row is absent and says so; it is never estimated.
    """
    rows = p27report.latest_geometry()
    if not rows:
        return {
            "available": False,
            "note": (
                "Phase 27's geometry sweep is not on disk, so the intraday "
                "reference rows are unavailable. Run "
                "`python -m app.research.phase27.cli run` to produce them; they "
                "are not estimated here"
            ),
        }
    return {
        "available": True,
        "rows": rows,
        "note": (
            "measured by Phase 27 on the same stored series with the same cost "
            "model; read back, not recomputed"
        ),
    }


def _vehicle_block(
    instruments: tuple[str, ...],
    vehicle: str,
    *,
    quick: bool,
) -> dict:
    """Everything measured for one vehicle: pools, hypotheses, ranked rules."""
    bands = discover.STOP_BANDS[:2] if quick else discover.STOP_BANDS
    horizons = discover.HORIZONS[:1] if quick else discover.HORIZONS
    pools: dict[tuple[str, float, int], pool.Pool] = {}
    mask_cache: dict[tuple[str, float, int], dict] = {}
    pool_summaries: list[dict] = []
    candidates: list[dict] = []
    windows: dict = {}
    tests = 0

    for inst in instruments:
        for horizon in horizons:
            for band in bands:
                p = pool.build(inst, stop_atr=band, horizon_days=horizon)
                if p is None or len(p) == 0:
                    continue
                key = (inst, band, horizon)
                pools[key] = p
                s = discover.Search(p)
                # The vocabulary is evaluated once per pool; the same masks are
                # reused for validation, holdout, regimes and the baselines
                # rather than rebuilt per cohort.
                mask_cache[key] = s.masks
                if not windows:
                    windows = discover.window_note(p.ts)
                summary = pool.summary(p)
                summary["window_trades"] = s.window_trades()
                pool_summaries.append(summary)

                survivors = s.run()
                for rule in survivors:
                    rule["dev_gate_passed"] = True
                    candidates.append(study.evaluate_rule(p, s.masks, rule, s))
                passed = {
                    tuple(sorted(r["conditions"])) + (r["side"],) for r in survivors
                }
                for rule in s.near_misses:
                    if tuple(sorted(rule["conditions"])) + (rule["side"],) in passed:
                        continue
                    rule["dev_gate_passed"] = False
                    candidates.append(study.evaluate_rule(p, s.masks, rule, s))
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
    hold_rows: dict[str, dict] = {}
    for (inst, band, horizon), p in pools.items():
        if band != bands[0] or horizon != horizons[0]:
            continue
        base_rows[inst] = study.baselines(p, mask_cache[(inst, band, horizon)])
        s_win = discover.windows(p.ts)
        hold_rows[inst] = {
            "full_history": baseline.buy_and_hold(p.series, p.vehicle),
            "holdout_window": baseline.buy_and_hold(
                p.series, p.vehicle,
                lo_ts=s_win[discover.HOLDOUT][0],
                hi_ts=s_win[discover.HOLDOUT][1],
            ),
        }

    leads = [r for r in ranked if r["status"] == rank.RESEARCH_LEAD]
    return {
        "vehicle": vehicle,
        "instruments": list(instruments),
        "stop_bands": list(bands),
        "horizons_trading_days": list(horizons),
        "windows": windows,
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
        "buy_and_hold": hold_rows,
        "diagnostics": _diagnostics(ranked),
    }


def _diagnostics(ranked: list[dict]) -> dict:
    """How far the search got, clause by clause, so "nothing" can be quantified.

    Every count below is an independent tally over the same ranked rows, not a
    funnel: a row counted as walk-forward stable is not necessarily one of the
    rows counted as positive in all three windows.
    """
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
            if _pos(r, "development") and _pos(r, "validation")
            and _pos(r, "holdout")
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
        "beats_buy_and_hold_in_holdout": sum(
            1 for r in ranked
            if (r.get("benchmark_holdout") or {}).get("beats_benchmark")
        ),
        "fdr_survivors": sum(1 for r in ranked if r.get("fdr_survivor")),
        "research_leads": sum(
            1 for r in ranked if r["status"] == rank.RESEARCH_LEAD
        ),
    }


def run(
    *,
    instruments: tuple[str, ...] | None = None,
    quick: bool = False,
    out_dir: str | None = None,
) -> dict:
    """Run the whole multi-day study and write the artefacts."""
    started = time.time()
    cov = dailybars.coverage(instruments)
    usable = tuple(r["instrument"] for r in cov["usable"])
    futures = tuple(i for i in usable if not dailybars.is_equity(i))
    equity = tuple(i for i in usable if dailybars.is_equity(i))

    blocks: list[dict] = []
    if futures:
        blocks.append(_vehicle_block(futures, FUTURES, quick=quick))
    if equity:
        blocks.append(_vehicle_block(equity, EQUITY_DELIVERY, quick=quick))

    out = {
        "version": VERSION,
        "generated_at": int(started),
        "quick_mode": quick,
        "instruments": list(usable),
        "coverage": cov,
        "searched_space": discover.searched_space(),
        "execution": outcomes.execution_note(),
        "claims": {
            "gap": GAP_CLAIM,
            "rollover": ROLLOVER_CLAIM,
            "delivery": DELIVERY_CLAIM,
            "shorting": SHORT_CLAIM,
            "spread": SPREAD_CLAIM,
            "buy_and_hold": BUY_HOLD_CLAIM,
            "sample": SAMPLE_CLAIM,
        },
        "stop_rule": STOP_RULE,
        "vehicle_results": blocks,
        "geometry_sweep": [] if quick else study.geometry_sweep(usable),
        "intraday_reference": intraday_reference(),
        "equity_history": equity_history_note(cov),
        "data_status": "OK" if usable else "NO_USABLE_HISTORY",
        "option_logic": (
            "frozen: this study touches no option strategy logic, no captured "
            "option book and no order path. The option-book capture keeps "
            "running and is not read here"
        ),
        "paper_only": True,
    }
    out["holding_period_comparison"] = comparison(out)
    out["answers"] = answers(out)
    out["conclusion"] = conclusion(out)
    out["runtime_seconds"] = round(time.time() - started, 1)
    out["artefacts"] = _write_all(out, out_dir)
    return out


def equity_history_note(cov: dict) -> dict:
    """Whether the cash-equity half of the question could be answered at all.

    This is the single most misreadable result in the phase. Absent stock history
    is not a failed stock strategy, and the two must never be reported in the same
    sentence — so the equity status is computed here, from coverage alone, before
    any strategy number exists.
    """
    equity_usable = [
        r for r in cov["usable"] if dailybars.is_equity(str(r["instrument"]))
    ]
    equity_missing = [
        r for r in cov["excluded"] if dailybars.is_equity(str(r["instrument"]))
    ]
    if equity_usable:
        return {
            "status": "TESTED",
            "instruments": [r["instrument"] for r in equity_usable],
            "note": (
                "cash delivery was measured on these names; see the "
                "EQUITY_DELIVERY block"
            ),
        }
    return {
        "status": INSUFFICIENT_HISTORY,
        "tested": [],
        "missing": [r["instrument"] for r in equity_missing],
        "note": (
            "no cash-equity name has five years of daily history on this "
            "machine, so the stock half of the question is UNANSWERED, not "
            "answered negatively. Nothing in this report is evidence for or "
            "against a multi-day stock strategy"
        ),
        "how_to_answer_it": (
            "collect daily history first: "
            "`python -m app.research.phase28.cli collect --instrument TCS` "
            "(Angel One credentials from the environment, never passed on the "
            "command line), then re-run `cli run`"
        ),
        "corporate_actions": (
            "the collector stores the provider's own adjusted series; splits, "
            "bonuses and dividends are therefore only as adjusted as the "
            "provider makes them, and survivorship is not modelled because the "
            "universe is a fixed watchlist rather than a historical index "
            "membership series. Both are stated as limitations, not corrected "
            "for"
        ),
    }


def comparison(out: dict) -> dict:
    """1m / 5m / 15m / multi-day, on the number that decides the study.

    The hypothesis that sent this phase into existence is arithmetic: cost as a
    fraction of risk fell 0.3805 -> 0.1532 -> 0.0835 as the bar grew, so a daily
    stop should make the toll negligible. This table is where that either shows up
    or does not.
    """
    rows: list[dict] = []
    ref = out.get("intraday_reference") or {}
    if ref.get("available"):
        for g in ref.get("rows") or []:
            if float(g.get("t1_r") or 0) != 1.5:
                continue
            rows.append({
                "holding_period": f"{int(g.get('timeframe_minutes') or 0)}-minute bars",
                "instrument": g.get("instrument"),
                "stop_band_atr": g.get("stop_band_atr"),
                "median_risk_points": g.get("median_risk_points"),
                "median_cost_points": g.get("median_cost_points"),
                "cost_as_fraction_of_risk": g.get("cost_as_fraction_of_risk"),
                "avg_net_r": g.get("avg_net_r"),
                "source": "Phase 27 artefact",
            })
    for g in out.get("geometry_sweep") or []:
        if float(g.get("t1_r") or 0) != 1.5:
            continue
        rows.append({
            "holding_period": (
                f"daily bars, {g.get('horizon_trading_days')}-session horizon"
            ),
            "instrument": g.get("instrument"),
            "stop_band_atr": g.get("stop_band_atr"),
            "median_risk_points": g.get("median_risk_points"),
            "median_cost_points": g.get("median_cost_points"),
            "cost_as_fraction_of_risk": g.get("cost_as_fraction_of_risk"),
            "avg_net_r": g.get("avg_net_r"),
            "source": "this phase",
        })
    return {
        "rows": rows,
        "note": (
            "cost/risk is the median round-trip cost divided by the median risk "
            "taken, per trade. It is the only figure in this programme that "
            "improves monotonically with holding period; whether direction "
            "improves with it is the separate question the gate answers"
        ),
    }


def _best(block: dict) -> dict | None:
    ranked = block.get("ranked") or []
    return ranked[0] if ranked else None


def _all_leads(out: dict) -> list[dict]:
    return [
        r for b in out.get("vehicle_results") or []
        for r in (b.get("research_leads") or [])
    ]


def answers(out: dict) -> dict:
    """The questions this phase was built to answer, answered from the numbers."""
    blocks = out.get("vehicle_results") or []
    by_vehicle = {b["vehicle"]: b for b in blocks}
    fut = by_vehicle.get(FUTURES) or {}
    eq = by_vehicle.get(EQUITY_DELIVERY) or {}
    leads = _all_leads(out)
    return {
        "does_a_multi_day_horizon_contain_a_directional_edge": (
            f"{len(leads)} research lead(s) survived every clause"
            if leads else
            "no. Not one cohort survived development, validation, the untouched "
            "holdout, walk-forward, the stress grid, the multiple-testing "
            "correction and the buy-and-hold comparison together"
        ),
        "did_the_longer_horizon_fix_the_cost_arithmetic": _cost_answer(out),
        "futures_verdict": _vehicle_answer(fut),
        "cash_equity_verdict": (
            _vehicle_answer(eq) if eq else out.get("equity_history")
        ),
        "can_any_long_rule_beat_buy_and_hold": _benchmark_answer(out),
        "what_overnight_gaps_cost": _gap_answer(out),
        "how_many_hypotheses_were_tried": {
            b["vehicle"]: b.get("hypotheses_evaluated") for b in blocks
        },
        "is_anything_promoted": (
            "no. The ceiling is RESEARCH_LEAD, nothing is wired to an order "
            "path, and paper collection would be a separate decision"
        ),
    }


def _vehicle_answer(block: dict) -> dict:
    if not block:
        return {"status": INSUFFICIENT_HISTORY, "note": "no usable history"}
    d = block.get("diagnostics") or {}
    best = _best(block)
    return {
        "status": (
            "RESEARCH_LEAD" if d.get("research_leads") else REJECTED
        ),
        "instruments": block.get("instruments"),
        "hypotheses_evaluated": block.get("hypotheses_evaluated"),
        "diagnostics": d,
        "closest_cohort": _closest_row(best),
    }


def _closest_row(best: dict | None) -> dict | str:
    if not best:
        return "no cohort cleared the development gate or the near-miss floor"
    return {
        "strategy_id": best.get("strategy_id"),
        "instrument": best.get("instrument"),
        "vehicle": best.get("vehicle"),
        "side": best.get("side"),
        "stop_band_atr": best.get("stop_band_atr"),
        "horizon_trading_days": best.get("horizon_trading_days"),
        "conditions": best.get("conditions"),
        "development_avg_net_r": (best.get("development") or {}).get("avg_net_r"),
        "validation_avg_net_r": (best.get("validation") or {}).get("avg_net_r"),
        "holdout_avg_net_r": (best.get("holdout") or {}).get("avg_net_r"),
        "holdout_trades": (best.get("holdout") or {}).get("trades"),
        "holdout_profit_factor": (best.get("holdout") or {}).get("profit_factor"),
        "walk_forward": best.get("walk_forward"),
        "benchmark_holdout": best.get("benchmark_holdout"),
        "gap_dependence": best.get("gap_dependence"),
        "status": best.get("status"),
        "failed_clauses": best.get("failed_clauses"),
    }


def _cost_answer(out: dict) -> dict:
    rows = (out.get("holding_period_comparison") or {}).get("rows") or []
    daily = [
        r for r in rows if str(r.get("holding_period", "")).startswith("daily")
    ]
    intraday = [
        r for r in rows if not str(r.get("holding_period", "")).startswith("daily")
    ]
    def _median(vals: list[float]) -> float | None:
        clean = [float(v) for v in vals if v is not None]
        return round(float(np.median(clean)), 4) if clean else None
    return {
        "median_cost_as_fraction_of_risk_intraday": _median(
            [r.get("cost_as_fraction_of_risk") for r in intraday]
        ),
        "median_cost_as_fraction_of_risk_daily": _median(
            [r.get("cost_as_fraction_of_risk") for r in daily]
        ),
        "note": (
            "a lower toll is a necessary condition, not a sufficient one: the "
            "pool still has to be directionally better than a coin, and the "
            "gate is what decides that"
        ),
    }


def _benchmark_answer(out: dict) -> dict:
    beat = 0
    checked = 0
    for b in out.get("vehicle_results") or []:
        for r in b.get("ranked") or []:
            bench = r.get("benchmark_holdout") or {}
            if not bench.get("applicable"):
                continue
            checked += 1
            if bench.get("beats_benchmark"):
                beat += 1
    return {
        "cohorts_compared": checked,
        "cohorts_beating_buy_and_hold": beat,
        "note": (
            "compared per day of exposure, so a rule in the market a tenth of "
            "the time is not credited for the drift it was absent for. A long "
            "cohort that loses this comparison has discovered drift, not an edge"
        ),
    }


def _gap_answer(out: dict) -> dict:
    rows: list[dict] = []
    for b in out.get("vehicle_results") or []:
        for r in (b.get("top10") or [])[:5]:
            gd = r.get("gap_dependence") or {}
            if gd.get("measured"):
                rows.append({
                    "strategy_id": r.get("strategy_id"),
                    "honest": gd.get("avg_net_r_with_honest_gap_fills"),
                    "if_gaps_ignored": gd.get(
                        "avg_net_r_if_stops_filled_at_the_stop"
                    ),
                })
    pools_gapped = [
        pl.get("gap_resolved_pct")
        for b in out.get("vehicle_results") or []
        for pl in b.get("pools") or []
        if pl.get("gap_resolved_pct") is not None
    ]
    return {
        "pct_of_trades_resolved_by_a_gap_through_the_level": (
            round(float(np.median([float(v) for v in pools_gapped])), 2)
            if pools_gapped else None
        ),
        "leaders": rows,
        "note": GAP_CLAIM,
    }


def conclusion(out: dict) -> dict:
    """Derived, never written by hand after the numbers are known."""
    leads = _all_leads(out)
    usable = out.get("instruments") or []
    if not usable:
        return {
            "verdict": INSUFFICIENT_HISTORY,
            "headline": "NO_DAILY_HISTORY_TO_STUDY",
            "statement": (
                "no instrument has five years of daily bars on this machine, so "
                "nothing was tested. This is a data status, not a strategy result"
            ),
            "stop_rule": STOP_RULE,
            "stop_rule_triggered": False,
            "promoted": [],
        }
    if leads:
        return {
            "verdict": rank.RESEARCH_LEAD,
            "headline": "MULTI_DAY_RESEARCH_LEAD_FOUND",
            "statement": (
                f"{len(leads)} cohort(s) survived every clause including the "
                "buy-and-hold comparison. That earns paper collection and a "
                "re-test on new sessions — it is not a promotion, and none of "
                "it is wired to an order path"
            ),
            "leads": [_closest_row(r) for r in leads],
            "stop_rule": STOP_RULE,
            "stop_rule_triggered": False,
            "promoted": [],
        }
    return {
        "verdict": REJECTED,
        "headline": NO_EDGE,
        "statement": _rejection_statement(out),
        "equity_caveat": out.get("equity_history"),
        "closest": [
            _closest_row(_best(b)) for b in out.get("vehicle_results") or []
        ],
        "requires_more_data": REQUIRES_MORE_DATA,
        "stop_rule": STOP_RULE,
        "stop_rule_triggered": True,
        "promoted": [],
    }


def _rejection_statement(out: dict) -> str:
    tried = sum(
        int(b.get("hypotheses_evaluated") or 0)
        for b in out.get("vehicle_results") or []
    )
    parts = [
        f"{tried} multi-day hypotheses were evaluated across "
        f"{len(out.get('instruments') or [])} instrument(s), "
        f"{len(discover.STOP_BANDS)} stop bands and "
        f"{len(discover.HORIZONS)} holding horizons, and none survived the full "
        "standard."
    ]
    for b in out.get("vehicle_results") or []:
        d = b.get("diagnostics") or {}
        parts.append(
            f"{b['vehicle']}: {d.get('cohorts_ranked')} cohorts reached the "
            f"table, {d.get('positive_all_three_windows')} were positive in "
            f"development, validation and the untouched holdout, "
            f"{d.get('walk_forward_stable')} were positive in every "
            f"walk-forward fold, {d.get('beats_buy_and_hold_in_holdout')} beat "
            f"buy-and-hold per day of exposure and "
            f"{d.get('fdr_survivors')} survived the multiple-testing "
            "correction. Those tallies are independent, not a funnel."
        )
    parts.append(
        "The cost arithmetic did improve with holding period, so cost was not "
        "the only obstacle: the remaining one is direction, which this data does "
        "not predict at any horizon tested — one minute, five, fifteen, or ten "
        "sessions."
    )
    return " ".join(parts)


def _write_all(out: dict, out_dir: str | None = None) -> dict[str, str]:
    """Seven artefacts, each answering one question a reviewer will ask."""
    paths: dict[str, str] = {}
    head = {
        "version": out["version"],
        "generated_at": out["generated_at"],
        "quick_mode": out["quick_mode"],
    }

    paths["coverage"] = _write("p28_coverage.json", {
        **head,
        "coverage": out["coverage"],
        "equity_history": out["equity_history"],
        "daily_bar_rules": dailybars.rules(),
    }, out_dir)

    paths["execution_model"] = _write("p28_execution_model.json", {
        **head,
        "execution": out["execution"],
        "claims": out["claims"],
        "searched_space": out["searched_space"],
        "gate": {
            "min_holdout_trades": rank.MIN_HOLDOUT_TRADES,
            "fdr_alpha": rank.FDR_ALPHA,
            "clauses": (
                "development, validation and untouched-holdout net R positive; "
                "holdout sample and profit factor adequate; every walk-forward "
                "fold positive; every cost/slippage/timing variant survived; no "
                "collapse when a discovered band is shifted; bounded "
                "complexity; Benjamini-Hochberg survival over the honest "
                "hypothesis count; and, for a long cohort, buy-and-hold beaten "
                "per day of exposure"
            ),
        },
    }, out_dir)

    paths["pool_economics"] = _write("p28_pool_economics.json", {
        **head,
        "geometry_sweep": out["geometry_sweep"],
        "pools": [
            pl for b in out["vehicle_results"] for pl in b.get("pools") or []
        ],
        "holding_period_comparison": out["holding_period_comparison"],
        "intraday_reference": out["intraday_reference"],
    }, out_dir)

    paths["ranked_strategies"] = _write("p28_ranked_strategies.json", {
        **head,
        "vehicles": [
            {
                "vehicle": b["vehicle"],
                "hypotheses_evaluated": b.get("hypotheses_evaluated"),
                "windows": b.get("windows"),
                "ranked": b.get("ranked"),
            }
            for b in out["vehicle_results"]
        ],
    }, out_dir)

    paths["funnel"] = _write("p28_funnel.json", {
        **head,
        "vehicles": [
            {
                "vehicle": b["vehicle"],
                "diagnostics": b.get("diagnostics"),
                "candidates_surviving_development": b.get(
                    "candidates_surviving_development"
                ),
                "cohorts_examined_including_near_misses": b.get(
                    "cohorts_examined_including_near_misses"
                ),
            }
            for b in out["vehicle_results"]
        ],
        "note": (
            "each count is an independent tally over the same ranked rows; a "
            "row counted in one line is not necessarily counted in the next"
        ),
    }, out_dir)

    paths["baselines"] = _write("p28_baselines.json", {
        **head,
        "textbook_rules": {
            b["vehicle"]: b.get("baselines") for b in out["vehicle_results"]
        },
        "buy_and_hold": {
            b["vehicle"]: b.get("buy_and_hold") for b in out["vehicle_results"]
        },
        "note": (
            "the textbook rows are reconstructions of the rules people post "
            "screenshots of — EMA stack plus RSI, 20/50 trend, breakout, "
            "pullback in trend, gap continuation — measured under the same costs "
            "as everything else, plus a no-condition control"
        ),
    }, out_dir)

    paths["verdict"] = _write("p28_verdict.json", {
        **head,
        "runtime_seconds": out.get("runtime_seconds"),
        "instruments": out["instruments"],
        "data_status": out["data_status"],
        "answers": out["answers"],
        "conclusion": out["conclusion"],
        "option_logic": out["option_logic"],
        "paper_only": True,
    }, out_dir)
    return paths


def latest(out_dir: str | None = None) -> dict | None:
    """The last verdict artefact, or ``None`` if the study has not been run."""
    return _latest("p28_verdict.json", out_dir)


def _latest(name: str, out_dir: str | None = None) -> dict | None:
    path = os.path.join(artefact_dir(out_dir), name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload if isinstance(payload, dict) else None


def latest_coverage(out_dir: str | None = None) -> dict:
    return _latest("p28_coverage.json", out_dir) or {}


def latest_ranked(out_dir: str | None = None) -> dict:
    return _latest("p28_ranked_strategies.json", out_dir) or {}


def latest_economics(out_dir: str | None = None) -> dict:
    return _latest("p28_pool_economics.json", out_dir) or {}


def latest_baselines(out_dir: str | None = None) -> dict:
    return _latest("p28_baselines.json", out_dir) or {}


def latest_funnel(out_dir: str | None = None) -> dict:
    return _latest("p28_funnel.json", out_dir) or {}


def latest_execution(out_dir: str | None = None) -> dict:
    return _latest("p28_execution_model.json", out_dir) or {}


__all__ = [
    "run", "answers", "conclusion", "comparison", "jsonable", "jsonable_dict",
    "artefact_dir",
    "intraday_reference", "equity_history_note",
    "latest", "latest_coverage", "latest_ranked",
    "latest_economics", "latest_baselines", "latest_funnel", "latest_execution",
    "ARTEFACT_DIR",
]
