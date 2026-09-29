"""Phase 34 §2 orchestration — does an adaptive exit beat a clock, robustly?

Every (instrument, family, side, exit arm) pair is one counted hypothesis. Each is
graded chronologically on development / validation / untouched holdout, then on five
walk-forward folds, then under 1.5x and 2x cost, then with the top 1% and top 5% of
winners removed. A candidate has to survive all of it, and the multiple-testing
denominator is the full count, not the survivors.

The stopping rule is pre-committed: if nothing survives, the answer is
``NO_ADAPTIVE_EXIT_EDGE_FOUND`` and no exit is wired into production.
"""
from __future__ import annotations

import json
import os

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase31.excursion import LONG, SHORT
from app.research.phase34 import adaptive, signals

DEV_FRACTION = 0.60
VAL_FRACTION = 0.20
WALK_FORWARD_FOLDS = 5
MIN_TRADES = 200
COST_STRESS = (1.0, 1.5, 2.0)
OUTLIER_TRIMS_PCT = (0.0, 1.0, 5.0)

VALIDATED = "VALIDATED_ADAPTIVE_EXIT"
RESEARCH_LEAD = "RESEARCH_LEAD_ADAPTIVE_EXIT"
NO_EDGE = "NO_ADAPTIVE_EXIT_EDGE_FOUND"
THIN = "REQUIRES_MORE_DATA"

ARTEFACT_DIR = os.path.join("data", "phase34")


def _stats(x: np.ndarray) -> dict:
    x = x[np.isfinite(x)]
    if not len(x):
        return {"n": 0, "net_expectancy_pct": None, "win_rate": None, "pf": None,
                "max_drawdown_pct": None}
    wins = x[x > 0]
    losses = x[x < 0]
    equity = np.cumsum(x)
    peak = np.maximum.accumulate(equity)
    return {
        "n": int(len(x)),
        "net_expectancy_pct": round(float(np.mean(x)), 6),
        "win_rate": round(float(len(wins) / len(x)), 4),
        "pf": (round(float(wins.sum() / abs(losses.sum())), 4)
               if len(losses) and losses.sum() != 0 else None),
        "max_drawdown_pct": round(float(np.max(peak - equity)), 6),
        "median_pct": round(float(np.median(x)), 6),
    }


def _trim(x: np.ndarray, pct: float) -> np.ndarray:
    """Remove the top ``pct`` percent of winners — outlier dependence check."""
    if pct <= 0 or not len(x):
        return x
    k = int(np.ceil(len(x) * pct / 100.0))
    if k <= 0:
        return x
    order = np.argsort(x)
    keep = order[: max(len(x) - k, 0)]
    return x[np.sort(keep)]


def _splits(n: int) -> tuple[slice, slice, slice]:
    dev = int(n * DEV_FRACTION)
    val = int(n * (DEV_FRACTION + VAL_FRACTION))
    return slice(0, dev), slice(dev, val), slice(val, n)


def grade(res: dict) -> dict:
    """Chronological grading of one exit arm's trade stream."""
    x = res["net_pct"]
    if res["n"] < MIN_TRADES:
        return {"status": THIN, "n": res["n"]}
    dev_s, val_s, hold_s = _splits(len(x))
    dev, val, hold = _stats(x[dev_s]), _stats(x[val_s]), _stats(x[hold_s])
    folds = np.array_split(x, WALK_FORWARD_FOLDS)
    fold_means = [
        round(float(np.nanmean(f)), 6) if len(f) else None for f in folds
    ]
    positive_folds = sum(1 for m in fold_means if m is not None and m > 0)
    stress = {
        f"{m:g}x": _stats(x + (res["cost_pct_median"] or 0.0) * (1.0 - m))
        for m in COST_STRESS
    }
    trims = {f"top_{p:g}pct_removed": _stats(_trim(x, p)) for p in OUTLIER_TRIMS_PCT}

    survives = (
        dev["net_expectancy_pct"] is not None and dev["net_expectancy_pct"] > 0
        and val["net_expectancy_pct"] is not None and val["net_expectancy_pct"] > 0
        and hold["net_expectancy_pct"] is not None and hold["net_expectancy_pct"] > 0
        and positive_folds > WALK_FORWARD_FOLDS // 2
        and all(v["net_expectancy_pct"] is not None and v["net_expectancy_pct"] > 0
                for v in stress.values())
        and all(v["net_expectancy_pct"] is not None and v["net_expectancy_pct"] > 0
                for v in trims.values())
    )
    lead = (
        not survives
        and dev["net_expectancy_pct"] is not None and dev["net_expectancy_pct"] > 0
        and val["net_expectancy_pct"] is not None and val["net_expectancy_pct"] > 0
    )
    return {
        "status": VALIDATED if survives else (RESEARCH_LEAD if lead else "REJECTED"),
        "n": res["n"],
        "development": dev,
        "validation": val,
        "untouched_holdout": hold,
        "walk_forward_fold_means": fold_means,
        "walk_forward_positive": positive_folds,
        "cost_stress": stress,
        "outlier_trims": trims,
        "median_hold_minutes": int(np.median(res["hold_minutes"]))
        if len(res["hold_minutes"]) else None,
        "mean_peak_pct": round(float(np.nanmean(res["peak_pct"])), 6)
        if len(res["peak_pct"]) else None,
        "exit_reason_mix": {
            r: round(res["reasons"].count(r) / max(len(res["reasons"]), 1), 4)
            for r in sorted(set(res["reasons"]))
        },
    }


def run(instruments: tuple[str, ...] = ("NIFTY", "CRUDEOIL"),
        *, out_dir: str | None = None, progress: bool = True) -> dict:
    rows: list[dict] = []
    hypotheses = 0
    for inst in instruments:
        s = p24data.load_series(inst)
        if s is None:
            rows.append({"instrument": inst, "status": THIN,
                         "reason": "NO_FIVE_YEAR_SERIES"})
            continue
        cost = adaptive.costs_for(inst, s)
        cost_median = float(np.nanmedian(cost)) if np.isfinite(cost).any() else 0.0
        for family in signals.FAMILIES:
            sign = signals.sign_of(s, family)
            for side in (LONG, SHORT):
                ent = signals.entries(sign, side)
                for kind, param in adaptive.arms():
                    hypotheses += 1
                    res = adaptive.resolve(
                        s, side, ent, kind=kind, param=param, cost=cost,
                        flip_sign=sign,
                    )
                    res["cost_pct_median"] = cost_median
                    row = {
                        "instrument": inst,
                        "family": family,
                        "side": "LONG" if side == LONG else "SHORT",
                        "exit_kind": kind,
                        "exit_param": param,
                        "cost_pct_median": round(cost_median, 6),
                    }
                    row.update(grade(res))
                    rows.append(row)
                    if progress:
                        print(
                            f"  {inst} {family} {row['side']} {kind}:{param:g} "
                            f"n={row.get('n')} status={row['status']}",
                            flush=True,
                        )
    validated = [r for r in rows if r.get("status") == VALIDATED]
    leads = [r for r in rows if r.get("status") == RESEARCH_LEAD]
    out = {
        "hypotheses_counted": hypotheses,
        "validated": validated,
        "research_leads": leads,
        "rows": rows,
        "verdict": VALIDATED if validated else (RESEARCH_LEAD if leads else NO_EDGE),
        "production_changed": False,
    }
    target = out_dir or p24data._resolve(ARTEFACT_DIR)
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "p34_adaptive_exit.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    return out


def best_by_holdout(rows: list[dict], key: str = "untouched_holdout") -> list[dict]:
    """Rank arms by untouched-holdout expectancy. Ranking is not validation."""
    graded = [r for r in rows if isinstance(r.get(key), dict)
              and r[key].get("net_expectancy_pct") is not None]
    return sorted(graded, key=lambda r: r[key]["net_expectancy_pct"], reverse=True)


__all__ = ["run", "grade", "best_by_holdout", "NO_EDGE", "VALIDATED", "RESEARCH_LEAD",
           "SHORT"]
