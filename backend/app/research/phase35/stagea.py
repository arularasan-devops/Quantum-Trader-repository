"""Phase 35 §1A/§14/§22 — Stage A: where to look, from five years of history.

Stage A is a *ranking of opportunity structures*, not a strategy search. §14 is
explicit that these distributions alone may not be declared a strategy, and §1A
says historical output can be ``CANDIDATE`` but never automatically
``VALIDATED``, so no path through this module can emit a validated verdict — the
label is not available to it.

Nothing new is invented here, deliberately. The opportunity structures are the
families Phase 33 already froze (which are themselves Phase 24's frozen
conditions and Phase 30's frozen patterns), the forward paths are Phase 33's
path builder, and the chronological splits and walk-forward folds are Phase 33's
``select.windows`` / ``select.folds``. §24 says not to build another indicator
library; reusing the frozen vocabulary is how that instruction is obeyed while
still answering "which structures are worth carrying into live paper".

What each structure is ranked on, per instrument and side (§14):

* movement, MFE and MAE distributions;
* time to MFE, time to T1, T1 reach rate;
* cost over risk — the ratio that decided every previous phase;
* the hold-time distribution.

And what the ranking is *for*: choosing which opportunity types Stage B spends
its live capture on. That is a research-allocation decision, which is why a
CANDIDATE here changes nothing in production and does not become a rule.
"""
from __future__ import annotations

import json
import os

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase32.reach import cost_pct
from app.research.phase33 import families, hold
from app.research.phase33 import path as p33path
from app.research.phase33 import select as p33select
from app.research.phase35 import (
    CANDIDATE,
    DESCRIBED,
    REJECTED,
    REQUIRES_MORE_DATA,
    SESSION_CLOSE,
    identity,
)

INSTRUMENTS = ("NIFTY", "CRUDEOIL")

# The horizon the ranking reads. Two hours is the longest frozen hold and it is
# the one Phase 33 found the median peak sitting well inside, so it captures the
# structure's whole useful life rather than truncating it.
RANK_HORIZON = 120

# A structure needs this many decision instants in *every* window before it is
# ranked at all. Below it the row is described and explicitly not compared.
MIN_ROWS_PER_WINDOW = 200

# T1 for Stage A is the reachability distance Phase 32 measured as the shortest
# one that pays its own toll several times over. It is a measurement level, not a
# target anybody trades, and it has to be one of Phase 33's frozen distances:
# a distance outside that grid would be an unfrozen hypothesis.
T1_LEVEL_PCT = 0.20


def _cost_array(instrument: str, p: p33path.Path) -> np.ndarray:
    """Round-trip cost per decision instant, in percent, from the frozen model."""
    return cost_pct(instrument, p.entry)


def _windows(p: p33path.Path, mask: np.ndarray) -> dict[str, np.ndarray]:
    return p33select.windows(p, mask)


def _row(p: p33path.Path, mask: np.ndarray, cost: np.ndarray) -> dict:
    """The §14 measurement block for one structure in one window."""
    econ = hold.economics(p, mask, cost, RANK_HORIZON)
    close = hold.economics(p, mask, cost, SESSION_CLOSE)
    dist = [
        row for row in hold.move_distribution(p, mask, RANK_HORIZON)
        if abs(float(row["level_pct"]) - T1_LEVEL_PCT) < 1e-9
    ]
    t1 = dist[0] if dist else {}
    give = hold.giveback(p, mask, cost)
    peak_minutes = p.peak_minute[mask]
    finite = peak_minutes[np.isfinite(peak_minutes)]
    cost_med = econ.get("cost_pct_median")
    mfe_med = econ.get("mfe_pct_median")
    return {
        "rows": econ.get("n"),
        "sessions": econ.get("sessions"),
        "gross_expectancy_pct": econ.get("gross_expectancy_pct"),
        "net_expectancy_pct": econ.get("net_expectancy_pct"),
        "profit_factor": econ.get("profit_factor"),
        "mfe_pct_median": mfe_med,
        "mae_pct_median": econ.get("mae_pct_median"),
        "time_to_mfe_min_median": (
            round(float(np.median(finite)), 2) if finite.size else None
        ),
        "t1_level_pct": T1_LEVEL_PCT,
        "t1_reach_rate": t1.get("reach_rate"),
        "time_to_t1_min_median": t1.get("minutes_p50"),
        "adverse_same_size_first_rate": t1.get("adverse_same_size_first_rate"),
        "cost_pct_median": cost_med,
        # The ratio that has decided every previous phase: what the structure
        # offers against what the round trip costs.
        "mfe_over_cost": (
            round(float(mfe_med) / float(cost_med), 3)
            if mfe_med and cost_med and float(cost_med) > 0 else None
        ),
        "giveback_share_of_mfe_median": econ.get("giveback_share_of_mfe_median"),
        "returned_to_entry_rate": give.get("returned_to_entry_after_profit_rate"),
        "turned_negative_rate": give.get("turned_net_negative_after_profit_rate"),
        "session_close_net_expectancy_pct": close.get("net_expectancy_pct"),
    }


def _status(dev: dict, val: dict, holdout: dict, folds: list[dict]) -> str:
    """CANDIDATE is the ceiling. There is no path to VALIDATED in Stage A."""
    if any(
        (w.get("rows") or 0) < MIN_ROWS_PER_WINDOW for w in (dev, val, holdout)
    ):
        return REQUIRES_MORE_DATA
    dev_net = dev.get("net_expectancy_pct")
    if dev_net is None or dev_net <= 0:
        return REJECTED
    val_net, hold_net = val.get("net_expectancy_pct"), holdout.get("net_expectancy_pct")
    if val_net is None or hold_net is None or val_net <= 0 or hold_net <= 0:
        return DESCRIBED
    positive_folds = sum(
        1 for f in folds if (f.get("net_expectancy_pct") or 0.0) > 0
    )
    if positive_folds < max(1, int(0.6 * len(folds))):
        return DESCRIBED
    return CANDIDATE


def instrument(name: str) -> dict:
    """Stage A over one instrument, both sides, every frozen structure."""
    series = p24data.load_series(name)
    if series is None:
        return {"instrument": name, "status": REQUIRES_MORE_DATA,
                "reason": "no five-year one-minute series available"}
    out: list[dict] = []
    hypotheses = 0
    for side in p33path.SIDES:
        p = p33path.build(series, side)
        cohorts = families.build(series, side)
        cost = _cost_array(name, p)
        for family, fam_mask in cohorts.masks.items():
            mask = fam_mask & p.eligible
            if not bool(mask.any()):
                continue
            hypotheses += 1
            win = _windows(p, mask)
            dev = _row(p, win[p33select.DEV], cost)
            val = _row(p, win[p33select.VAL], cost)
            hold_w = _row(p, win[p33select.HOLDOUT], cost)
            fold_rows = [
                _row(p, f, cost) for f in p33select.folds(p, mask)
            ]
            out.append({
                "instrument": name,
                "side": p33path.side_name(side),
                "structure": family,
                "opportunity_type": identity.classify_type(
                    family, direction=p33path.side_name(side)
                ),
                "development": dev,
                "validation": val,
                "holdout": hold_w,
                "walk_forward": fold_rows,
                "walk_forward_positive": sum(
                    1 for f in fold_rows if (f.get("net_expectancy_pct") or 0.0) > 0
                ),
                "walk_forward_folds": len(fold_rows),
                "status": _status(dev, val, hold_w, fold_rows),
            })
    ranked = sorted(
        out,
        key=lambda r: (
            r["status"] != CANDIDATE,
            -(r["development"].get("mfe_over_cost") or 0.0),
        ),
    )
    return {
        "instrument": name,
        "bars": len(series),
        "structures_tested": hypotheses,
        "candidates": [r for r in ranked if r["status"] == CANDIDATE],
        "ranked": ranked,
        "rank_horizon_min": RANK_HORIZON,
        "min_rows_per_window": MIN_ROWS_PER_WINDOW,
    }


def run(*, out_dir: str | None = None) -> dict:
    """Stage A over every instrument with five years of one-minute history.

    ``out_dir`` writes the full ranked table beside the summary, because a run this
    expensive should not have to be repeated to read a row that was printed once.
    """
    per = [instrument(name) for name in INSTRUMENTS]
    candidates = [c for r in per for c in r.get("candidates", [])]
    tested = sum(r.get("structures_tested") or 0 for r in per)
    payload = {
        "stage": "A_HISTORICAL_DISCOVERY",
        "instruments": per,
        "hypotheses_counted": tested,
        "candidates": len(candidates),
        "candidate_list": [
            {k: c[k] for k in ("instrument", "side", "structure", "opportunity_type")}
            for c in candidates
        ],
        "highest_possible_label": CANDIDATE,
        "note": (
            "§1A/§14: these are movement, cost and hold distributions used to "
            "choose where live paper capture is spent. A CANDIDATE is not a "
            "strategy, changes nothing in production, and is never promoted from "
            "historical data alone"
        ),
        "research_only": True,
    }
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "phase35_stagea.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
    return payload


def headline(res: dict) -> str:
    n = res.get("candidates") or 0
    tested = res.get("hypotheses_counted") or 0
    if not n:
        return (
            f"STAGE A: no opportunity structure survived chronological validation "
            f"({tested} counted)"
        )
    return (
        f"STAGE A: {n} of {tested} structures are CANDIDATE for live paper capture "
        f"(never validated from history)"
    )
