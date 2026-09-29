"""Phase 24 §14/§19/§20 — run the study and write the artefacts.

Everything the specification asks for is produced from one run: the ranked table,
the top ten, the walk-forward and holdout results, the robustness grid, the
fingerprints, the rejected log, the coverage statement and direct answers to the
fifteen questions. If nothing survives, the report says so and names the closest
candidate together with the exact clause it failed.
"""
from __future__ import annotations

import gzip
import json
import os
import time

import numpy as np

from app.research.phase24 import (
    INSUFFICIENT_HISTORY,
    REQUIRES_MORE_DATA,
    VALIDATED,
    VERSION,
    conditions,
    data,
    discover,
    metrics,
    outcomes,
    pool,
    rank,
    study,
)

ARTEFACT_DIR = "data/phase24"
STRESS_TOP_N = 10          # cost stress re-resolves pools; only the top rules get it


def _abs(rel: str) -> str:
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
    return os.path.join(backend, rel)


def _write(name: str, payload: object) -> str:
    d = _abs(ARTEFACT_DIR)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, default=str)
    return path


def write_candidate_rows(
    p: pool.Pool,
    masks: dict,
    band: float,
    ranked: list[dict],
) -> dict:
    """Every candidate row for one pool, including the ones no rule ever selected.

    A summary table cannot be audited, so the individual opportunities are written
    out: entry, geometry, path, cost and net result per row, the conditions that
    were true at its entry timestamp, and which of the leading rules selected it.
    Rows with an empty ``selected_by`` are the rejected opportunities — they are
    the NO TRADE side of the study and are kept, not filtered away.
    """
    o = p.out
    names = sorted(masks)
    picks: list[tuple[str, np.ndarray]] = []
    for r in ranked[:STRESS_TOP_N]:
        if r["instrument"] != p.instrument or float(r["stop_band_atr"]) != float(band):
            continue
        m = p.side == (outcomes.LONG if r["side"] == "LONG" else outcomes.SHORT)
        for c in r["conditions"]:
            m = m & masks[c]
        picks.append((r["strategy_id"], m))

    name = f"p24_candidates_{p.instrument}_{band:g}atr.jsonl.gz"
    d = _abs(ARTEFACT_DIR)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for k in range(len(p)):
            fh.write(json.dumps({
                "instrument": p.instrument,
                "ts": int(p.ts[k]),
                "session": int(p.session[k]),
                "stop_band_atr": band,
                "side": "LONG" if p.side[k] == outcomes.LONG else "SHORT",
                "entry": round(float(o.entry[k]), 2),
                "stop": round(float(o.stop[k]), 2),
                "risk_points": round(float(o.risk[k]), 2),
                "t1": round(float(o.t1[k]), 2),
                "t2": round(float(o.t2[k]), 2),
                "t3": round(float(o.t3[k]), 2),
                "outcome": str(o.outcome[k]),
                "resolved": bool(o.resolved[k]),
                "t1_before_sl": bool(o.t1_before_sl[k]),
                "t2_before_sl": bool(o.t2_before_sl[k]),
                "t3_before_sl": bool(o.t3_before_sl[k]),
                "mfe_r": round(float(o.mfe_r[k]), 3),
                "mae_r": round(float(o.mae_r[k]), 3),
                "bars_to_t1": int(o.bars_to_t1[k]),
                "bars_to_sl": int(o.bars_to_sl[k]),
                "bars_held": int(o.bars_held[k]),
                "exit": round(float(o.exit_price[k]), 2),
                "cost_points": round(float(o.cost_points[k]), 3),
                "net_points": round(float(o.net_points[k]), 2),
                "net_r": round(float(o.net_r[k]), 4),
                "conditions_true_at_entry": [c for c in names if masks[c][k]],
                "selected_by": [sid for sid, m in picks if m[k]],
            }) + "\n")
    return {
        "path": path,
        "rows": int(len(p)),
        "note": "one row per candidate opportunity, selected or refused",
    }


def run(*, instruments: tuple[str, ...] | None = None, quick: bool = False) -> dict:
    """Run the whole study. Returns the summary that the API and CLI both serve."""
    started = time.time()
    cov = data.coverage()
    usable = tuple(r["instrument"] for r in cov["usable"])
    if instruments:
        usable = tuple(i for i in usable if i in instruments)

    pools: dict[tuple[str, float], pool.Pool] = {}
    mask_cache: dict[tuple[str, float], dict] = {}
    pool_summaries: list[dict] = []
    candidates: list[dict] = []
    tests = 0

    bands = (discover.STOP_BANDS[:2] if quick else discover.STOP_BANDS)
    for inst in usable:
        for band in bands:
            p = pool.build(inst, stop_atr=band)
            if p is None:
                continue
            key = (inst, band)
            pools[key] = p
            mask_cache[key] = conditions.masks(p.feat, p.side)
            s = discover.Search(p, band)
            s.masks = mask_cache[key]
            summary = pool.summary(p)
            summary["stop_band_atr"] = band
            summary["median_cost_points_per_round_trip"] = round(
                float(np.median(p.out.cost_points)) if len(p) else 0.0, 3
            )
            summary["cost_as_fraction_of_risk"] = round(
                float(np.median(p.out.cost_points) / np.median(p.out.risk))
                if len(p) else 0.0, 3
            )
            pool_summaries.append(summary)
            survivors = s.run()
            for rule in survivors:
                rule["t1_r"] = outcomes.T1_R
                rule["dev_gate_passed"] = True
                candidates.append(study.evaluate_rule(p, mask_cache[key], rule, s))
            # The best cohorts that failed the development gate are carried too, so
            # "nothing survived" can be quantified instead of asserted.
            passed = {tuple(sorted(r["conditions"])) + (r["side"],) for r in survivors}
            for rule in s.near_misses:
                if tuple(sorted(rule["conditions"])) + (rule["side"],) in passed:
                    continue
                rule["t1_r"] = outcomes.T1_R
                rule["dev_gate_passed"] = False
                candidates.append(study.evaluate_rule(p, mask_cache[key], rule, s))
            tests += s.tests

    ranked = rank.rank(candidates)
    # Cost/entry/exit stress is expensive, so only the leaders earn it — and a
    # rule cannot be VALIDATED without it, so the gate is re-run afterwards.
    for r in ranked[:STRESS_TOP_N]:
        r["cost_sensitivity"] = study.cost_sensitivity(r, quick=quick)
    for r in ranked:
        if "cost_sensitivity" not in r:
            r["cost_sensitivity"] = []
            r["cost_stress"] = "NOT_RUN_NOT_A_LEADER"
    ranked = rank.rank(ranked)

    # The candidate dataset is written at the frozen geometry only; the wider stop
    # bands are research variants, re-derivable from the CLI.
    candidate_files: list[dict] = []
    if not quick:
        for inst in usable:
            key = (inst, bands[0])
            if key in pools:
                candidate_files.append(
                    write_candidate_rows(pools[key], mask_cache[key], bands[0], ranked)
                )

    # Scanned once: on a store with a long capture this walks every snapshot's
    # payload, and both the coverage block and the vehicle note need the answer.
    books = data.option_book_coverage()
    validated = [r for r in ranked if r["status"] == VALIDATED]
    base_rows: dict[str, dict] = {}
    for (inst, band), p in pools.items():
        if band == bands[0]:
            base_rows[inst] = study.baselines(p, mask_cache[(inst, band)])

    out = {
        "version": VERSION,
        "generated_at": int(started),
        "runtime_seconds": round(time.time() - started, 1),
        "quick_mode": quick,
        "coverage": cov,
        "option_books": books,
        "windows": _window_note(pools),
        "pools": pool_summaries,
        "candidate_files": candidate_files,
        "hypotheses_evaluated": tests,
        "candidates_surviving_development": sum(
            1 for r in candidates if r.get("dev_gate_passed")
        ),
        "cohorts_examined_including_near_misses": len(candidates),
        "ranked": ranked,
        "top10": ranked[:10],
        "validated": validated,
        "rejected": [r for r in ranked if r["status"] != VALIDATED],
        "baselines": base_rows,
        "geometry_sweep": ([] if quick else study.geometry_sweep(usable)),
        "vehicles": _vehicle_note(books),
        "data_status": ("OK" if usable else "NO_USABLE_HISTORY"),
        "answers": answers(ranked, pool_summaries, cov, base_rows),
        "conclusion": conclusion(ranked, ran_on_data=bool(pools)),
    }
    out["artefacts"] = _write_all(out)
    return out


def _window_note(pools: dict) -> dict:
    for p in pools.values():
        w = discover.windows(p.ts)
        return {
            k: {"from_ts": v[0], "to_ts": v[1],
                "from": time.strftime("%Y-%m-%d", time.gmtime(v[0])),
                "to": time.strftime("%Y-%m-%d", time.gmtime(v[1]))}
            for k, v in w.items()
        }
    return {}


def _vehicle_note(out_books: dict | None = None) -> dict:
    """§12/§16 — what each vehicle could honestly be measured on.

    The CE/PE note is derived from the option store on this machine rather than
    fixed in the source: a capture that has accumulated real two-sided books can
    support an option study over its own window, and saying otherwise would be
    wrong on exactly the installations that have done the capturing.
    """
    books = out_books if out_books is not None else data.option_book_coverage()
    two = int(books.get("snapshots_with_two_sided_book") or 0)
    ce_note = (
        "historical discovery needs entry at ASK and exit at BID; the stored "
        "chain history has no usable two-sided book, so no CE result is "
        "produced rather than a modelled one"
    ) if two == 0 else (
        f"the five-year series carries no option history at all, so CE is not "
        f"discovered here. This store does hold {two:,} snapshots with a real "
        f"two-sided book: enough to price entry-at-ask and exit-at-bid over the "
        f"captured window, which is a separate and shorter study than this one"
    )
    return {
        "FUTURES": {
            "status": "MEASURED_WITH_MODELLED_SPREAD",
            "note": (
                "resolved on 1-minute OHLC with brokerage, statutory charges and "
                "configured slippage charged both ways; no two-sided futures book "
                "exists historically, so the spread is unmeasured and every net "
                "figure is optimistic by one spread"
            ),
        },
        "CE": {"status": REQUIRES_MORE_DATA, "note": ce_note},
        "PE": {"status": REQUIRES_MORE_DATA, "note": "same as CE"},
        "NO_TRADE": {
            "status": "FIRST_CLASS_OUTCOME",
            "note": "every candidate not selected by a rule is a NO TRADE, and the "
                    "study is scored on selectivity rather than frequency",
        },
    }


def answers(ranked: list[dict], pools: list[dict], cov: dict, base_rows: dict) -> dict:
    """§19 — the fifteen questions, answered from this run's numbers only."""
    best = ranked[0] if ranked else None
    validated = [r for r in ranked if r["status"] == VALIDATED]
    thin = [r["instrument"] for r in cov.get("excluded", [])]

    def _cond(r: dict | None) -> str:
        return " AND ".join(r["conditions"]) if r else "none — no rule cleared development"

    return {
        "1_highest_t1_before_sl_conditions": (
            _cond(max(ranked, key=lambda r: r["development"]["t1_before_sl_pct"]))
            if ranked else "no cohort beat its pool base rate by the required margin"
        ),
        "2_highest_net_expectancy_conditions": _cond(best),
        "3_instruments_with_repeatable_edge": [
            r["instrument"] for r in validated
        ] or "none — no instrument produced a rule that passed the holdout",
        "4_instruments_with_no_reliable_edge": sorted(
            {p["instrument"] for p in pools}
            - {r["instrument"] for r in validated}
        ),
        "5_nature_of_edge": _edge_nature(ranked),
        "6_survives_bid_ask_and_slippage": _stress_answer(ranked),
        "7_survives_walk_forward": (
            any(r["walk_forward"]["stable"] for r in ranked) if ranked else False
        ),
        "8_survives_final_holdout": bool(validated),
        "9_trades_per_day_week": (
            best["selectivity"] if best else "not applicable — no rule"
        ),
        "10_no_trade_pct": (
            best["selectivity"]["no_trade_pct"] if best else 100.0
        ),
        "11_best_ce_setup": REQUIRES_MORE_DATA,
        "12_best_pe_setup": REQUIRES_MORE_DATA,
        "13_best_futures_setup": (
            {"strategy_id": best["strategy_id"], "conditions": best["conditions"],
             "status": best["status"]} if best else "none"
        ),
        "14_anything_ready_for_paper": bool(validated),
        "15_single_strongest_strategy": (
            {"strategy_id": best["strategy_id"], "score": best["score"],
             "status": best["status"], "failed_clauses": best["failed_clauses"]}
            if best else "none"
        ),
        "instruments_excluded_for_insufficient_history": thin,
        "excluded_status": INSUFFICIENT_HISTORY,
        "cost_wall_note": _cost_wall(pools),
    }


def _edge_nature(ranked: list[dict]) -> str:
    """Which feature families the surviving rules actually lean on."""
    if not ranked:
        return "no edge was isolated; the pool's own base rate was not beaten"
    families = {
        "directional": ("trend", "ema", "prev_day", "opening_range", "vwap_side"),
        "momentum": ("momentum", "accelerating"),
        "volatility": ("volatility", "candle_expansion", "expansion_leg"),
        "pullback": ("pullback", "continuation", "exhaustion"),
        "location": ("room", "vwap_1atr", "stretched"),
        "time": ("time_",),
        "gap": ("gap", "flat_open"),
    }
    hits: dict[str, int] = {k: 0 for k in families}
    for r in ranked[:10]:
        for c in r["conditions"]:
            for fam, keys in families.items():
                if any(k in c for k in keys):
                    hits[fam] += 1
    present = [k for k, v in sorted(hits.items(), key=lambda kv: -kv[1]) if v]
    return " + ".join(present) if present else "unclassified"


def _stress_answer(ranked: list[dict]) -> dict:
    for r in ranked:
        if r.get("cost_sensitivity"):
            return {
                "strategy_id": r["strategy_id"],
                "variants": r["cost_sensitivity"],
                "survives_all": all(v["survives"] for v in r["cost_sensitivity"]),
            }
    return {"survives_all": False, "note": "no rule reached the stress grid"}


def _cost_wall(pools: list[dict]) -> dict:
    """The single most important number in this study, stated plainly."""
    return {
        "note": (
            "round-trip futures cost as a fraction of the risk taken, per stop band. "
            "At a 1-ATR stop on 1-minute bars the charges alone consume most of one R, "
            "so the geometry — not the entry rule — decides whether anything can win."
        ),
        "by_pool": [
            {
                "instrument": p["instrument"],
                "stop_band_atr": p.get("stop_band_atr"),
                "cost_as_fraction_of_risk": p.get("cost_as_fraction_of_risk"),
                "base_t1_before_sl_pct": p.get("base_t1_before_sl_pct"),
                "base_avg_net_r": p.get("base_avg_net_r"),
            }
            for p in pools
        ],
    }


def conclusion(ranked: list[dict], *, ran_on_data: bool = True) -> dict:
    """The §21 final block, in the requested shape.

    ``ran_on_data`` separates the two very different ways this can come back
    empty: a search that examined the history and found nothing, and a machine
    that has no five-year history to search. Reporting the second as the first
    would read as a negative result about the market.
    """
    if not ran_on_data:
        return {
            "BEST_VALIDATED_STRATEGY": "NONE",
            "EXPECTED_T1_BEFORE_SL": None,
            "NET_EXPECTANCY_R": None,
            "PROFIT_FACTOR": None,
            "FINAL_HOLDOUT": None,
            "TRADES_PER_WEEK": None,
            "STATUS": REQUIRES_MORE_DATA,
            "CLOSEST_CANDIDATE": (
                "the study did not run: no instrument on this machine has a "
                "five-year 1-minute series, so nothing was searched. This is not "
                "a negative result about any strategy — build the history first."
            ),
        }
    validated = [r for r in ranked if r["status"] == VALIDATED]
    if validated:
        b = validated[0]
        return {
            "BEST_VALIDATED_STRATEGY": b["strategy_id"],
            "CONDITIONS": b["conditions"],
            "EXPECTED_T1_BEFORE_SL": b["holdout"].get("t1_before_sl_pct"),
            "NET_EXPECTANCY_R": b["holdout"].get("avg_net_r"),
            "PROFIT_FACTOR": b["holdout"].get("profit_factor"),
            "FINAL_HOLDOUT": b["holdout"],
            "TRADES_PER_WEEK": b["selectivity"].get("trades_per_week"),
            "STATUS": VALIDATED,
        }
    closest = ranked[0] if ranked else None
    return {
        "BEST_VALIDATED_STRATEGY": "NONE",
        "EXPECTED_T1_BEFORE_SL": None,
        "NET_EXPECTANCY_R": None,
        "PROFIT_FACTOR": None,
        "FINAL_HOLDOUT": None,
        "TRADES_PER_WEEK": None,
        "STATUS": (closest or {}).get("status", REQUIRES_MORE_DATA),
        "CLOSEST_CANDIDATE": (
            {
                "strategy_id": closest["strategy_id"],
                "instrument": closest["instrument"],
                "side": closest["side"],
                "stop_band_atr": closest["stop_band_atr"],
                "conditions": closest["conditions"],
                "development": closest["development"],
                "validation": closest["validation"],
                "holdout": closest["holdout"],
                "why_it_failed": closest["failed_clauses"],
            }
            if closest else "no candidate cleared the development gate"
        ),
    }


def _write_all(out: dict) -> dict[str, str]:
    """§20 deliverables, one file each."""
    return {
        "discovery_report": _write("p24_discovery_report.json", {
            "version": out["version"],
            "generated_at": out["generated_at"],
            "coverage": out["coverage"],
            "option_books": out["option_books"],
            "windows": out["windows"],
            "pools": out["pools"],
            "hypotheses_evaluated": out["hypotheses_evaluated"],
            "answers": out["answers"],
            "conclusion": out["conclusion"],
        }),
        "candidate_dataset": _write("p24_candidate_pool.json", {
            "pool_summaries": out["pools"],
            "per_candidate_files": out.get("candidate_files", []),
        }),
        "ranked_table": _write("p24_ranked_strategies.json", out["ranked"]),
        "top10": _write("p24_top10.json", out["top10"]),
        "walk_forward": _write("p24_walk_forward.json", [
            {"strategy_id": r["strategy_id"], "walk_forward": r["walk_forward"]}
            for r in out["ranked"]
        ]),
        "holdout": _write("p24_final_holdout.json", [
            {"strategy_id": r["strategy_id"], "holdout": r["holdout"],
             "validation": r["validation"]}
            for r in out["ranked"]
        ]),
        "robustness": _write("p24_robustness.json", [
            {"strategy_id": r["strategy_id"],
             "cost_sensitivity": r.get("cost_sensitivity"),
             "parameter_perturbation": r.get("parameter_perturbation"),
             "regimes": r.get("regimes")}
            for r in out["ranked"]
        ]),
        "fingerprints": _write("p24_fingerprints.json", [
            r["fingerprint"] for r in out["ranked"] if r["status"] == VALIDATED
        ] or {"fingerprints": [], "note": "no strategy was VALIDATED, so no "
              "fingerprint is published for live matching"}),
        "rejected_log": _write("p24_rejected.json", [
            {"strategy_id": r["strategy_id"], "instrument": r["instrument"],
             "side": r["side"], "stop_band_atr": r["stop_band_atr"],
             "conditions": r["conditions"], "status": r["status"],
             "failed_clauses": r["failed_clauses"]}
            for r in out["ranked"] if r["status"] != VALIDATED
        ]),
        "baselines": _write("p24_baseline_comparison.json", out["baselines"]),
        "geometry_sweep": _write("p24_geometry_sweep.json", out["geometry_sweep"]),
        "vehicles": _write("p24_vehicles.json", out["vehicles"]),
    }


def latest() -> dict | None:
    """The last written report, for the read-only API."""
    path = os.path.join(_abs(ARTEFACT_DIR), "p24_discovery_report.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def latest_geometry() -> list[dict]:
    """The unconditional stop/target economics, so a reader can see whether the
    failure is in the search or already in the geometry."""
    path = os.path.join(_abs(ARTEFACT_DIR), "p24_geometry_sweep.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return rows if isinstance(rows, list) else []


def latest_ranked() -> list[dict]:
    path = os.path.join(_abs(ARTEFACT_DIR), "p24_ranked_strategies.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return rows if isinstance(rows, list) else []


__all__ = [
    "run", "latest", "latest_ranked", "latest_geometry", "write_candidate_rows",
    "answers", "conclusion", "metrics",
]
