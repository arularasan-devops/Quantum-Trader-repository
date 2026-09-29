"""Phase 32 — choosing a T1 on the development window and paying for it later.

The configuration grid, the selection rule and the bar a candidate has to clear
are all frozen in ``__init__``. What this module adds is the discipline around
them:

* the split is chronological, 60/20/20, and the holdout is evaluated **once**,
  after the choice has been made on the development window alone;
* the choice is made on net expectancy per unit of risk (net percentage divided
  by the stop distance), not on win rate. A 90%-win configuration that risks
  twice what it makes is worse than the coin flip it looks better than;
* every configuration examined is counted, and the development p-value has to
  clear a Bonferroni threshold for the whole count, so a configuration cannot
  qualify simply because sixty-two others were tried;
* cost is charged through the shared model, both ways, and the candidate has to
  survive 1.5x and 2x of it;
* the top 1% and top 5% of winners are removed to check that the result is not
  one exceptional run.
"""
from __future__ import annotations

import math

import numpy as np

from app.research.phase32 import (
    CAPS,
    COST_STRESS,
    DEV_FRACTION,
    MIN_TRADES_PER_WINDOW,
    NO_REACHABLE_T1,
    REJECTED,
    RESEARCH_LEAD,
    SL_MULTIPLES,
    T1_GRID,
    VAL_FRACTION,
    VALIDATED,
)
from app.research.phase32 import reach as reachmod

DEV, VAL, HOLDOUT = "DEV", "VAL", "HOLDOUT"
WINDOWS = (DEV, VAL, HOLDOUT)


def configs() -> list[tuple[float, float, int]]:
    """Every (T1, stop, cap) the study will look at. Counted, not searched."""
    out = []
    for t1 in T1_GRID:
        for m in SL_MULTIPLES:
            for cap in CAPS:
                out.append((t1, round(t1 * m, 6), cap))
    return out


def windows(r: reachmod.Reach) -> dict[str, np.ndarray]:
    """Chronological masks. Split on *sessions*, so no session straddles two windows."""
    elig = r.eligible
    sess = r.session
    ids = np.unique(sess[elig]) if elig.any() else np.unique(sess)
    n = ids.size
    d_end = int(n * DEV_FRACTION)
    v_end = int(n * (DEV_FRACTION + VAL_FRACTION))
    parts = {
        DEV: set(ids[:d_end].tolist()),
        VAL: set(ids[d_end:v_end].tolist()),
        HOLDOUT: set(ids[v_end:].tolist()),
    }
    out = {}
    for name, keep in parts.items():
        sel = np.isin(sess, list(keep)) if keep else np.zeros_like(elig)
        out[name] = elig & sel
    return out


def _p_one_sided(mean: float, sd: float, n: int) -> float | None:
    """Probability of a mean this positive under a zero-mean null.

    Normal approximation to the t statistic. At the sample sizes here (hundreds of
    thousands of instants) the difference from the exact t distribution is far
    smaller than the assumptions already made about execution.
    """
    if n < 2 or sd <= 0:
        return None
    z = mean / (sd / math.sqrt(n))
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _drawdown(net: np.ndarray) -> float:
    """Worst peak-to-trough fall of the cumulative net percentage, in order."""
    if net.size == 0:
        return 0.0
    eq = np.cumsum(net)
    peak = np.maximum.accumulate(eq)
    return float(np.min(eq - peak))


def evaluate(
    r: reachmod.Reach,
    cost: np.ndarray,
    t1_pct: float,
    sl_pct: float,
    cap: int,
    mask: np.ndarray,
    *,
    cost_multiplier: float = 1.0,
    drop_top_pct: float = 0.0,
) -> dict:
    """One configuration on one window, costs charged."""
    code, gross = reachmod.resolve(r, t1_pct, sl_pct, cap, mask)
    c = cost[mask] * float(cost_multiplier)
    net = gross - c
    keep = np.isfinite(net)
    net, gross, code = net[keep], gross[keep], code[keep]
    if drop_top_pct > 0.0 and net.size:
        cut = float(np.percentile(net, 100.0 - drop_top_pct))
        net = net[net <= cut]
    n = int(net.size)
    if n == 0:
        return {"trades": 0}
    mean = float(net.mean())
    sd = float(net.std(ddof=1)) if n > 1 else 0.0
    wins = net > 0
    gross_win = float(net[wins].sum())
    gross_loss = float(-net[~wins].sum())
    return {
        "trades": n,
        "t1_first_pct": round(100.0 * float(np.mean(code == reachmod.T1_FIRST)), 3),
        "sl_first_pct": round(100.0 * float(np.mean(code == reachmod.SL_FIRST)), 3),
        "timeout_pct": round(100.0 * float(np.mean(code == reachmod.TIMEOUT)), 3),
        "net_expectancy_pct": round(mean, 6),
        "net_expectancy_r": round(mean / float(sl_pct), 6),
        "gross_expectancy_pct": round(float(gross.mean()), 6),
        "cost_pct_median": round(float(np.median(c[np.isfinite(c)])), 6)
        if np.isfinite(c).any() else None,
        "win_rate_pct": round(100.0 * float(wins.mean()), 3),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "max_drawdown_pct": round(_drawdown(net), 4),
        "p_value_one_sided": _p_one_sided(mean, sd, n),
    }


def study_instrument(r: reachmod.Reach, cost: np.ndarray) -> dict:
    """Every configuration on every window, then one choice made on DEV alone."""
    w = windows(r)
    grid = configs()
    rows = []
    for t1, sl, cap in grid:
        row = {"t1_pct": t1, "sl_pct": sl, "cap_min": cap}
        for name in WINDOWS:
            row[name] = evaluate(r, cost, t1, sl, cap, w[name])
        rows.append(row)

    eligible_rows = [
        row for row in rows
        if row[DEV].get("trades", 0) >= MIN_TRADES_PER_WINDOW
        and (row[DEV].get("net_expectancy_r") or -1.0) > 0
    ]
    out = {
        "instrument": r.instrument,
        "side": reachmod.side_name(r.side),
        "hypotheses": len(grid),
        "window_trades": {name: int(w[name].sum()) for name in WINDOWS},
        "configs": rows,
    }
    if not eligible_rows:
        out["verdict"] = NO_REACHABLE_T1
        out["reason"] = (
            "no frozen T1/stop/cap configuration had a positive net expectancy on "
            "the development window, so nothing was carried to the holdout"
        )
        out["selected"] = None
        return out

    # The selection rule, applied to the development window only.
    best = max(eligible_rows, key=lambda row: row[DEV]["net_expectancy_r"])
    sel = {
        "t1_pct": best["t1_pct"],
        "sl_pct": best["sl_pct"],
        "cap_min": best["cap_min"],
        "chosen_on": DEV,
        "dev": best[DEV],
        "val": best[VAL],
        "holdout": best[HOLDOUT],
        "minutes_to_t1_median": None,
    }
    mins = reachmod.minutes_to_t1(r, best["t1_pct"], best["cap_min"], w[HOLDOUT])
    if mins.size:
        sel["minutes_to_t1_median"] = round(float(np.median(mins)), 1)
        sel["minutes_to_t1_p75"] = round(float(np.percentile(mins, 75)), 1)

    stress = {}
    for mult in COST_STRESS:
        stress[f"cost_{mult}x"] = evaluate(
            r, cost, best["t1_pct"], best["sl_pct"], best["cap_min"],
            w[HOLDOUT], cost_multiplier=mult,
        )
    sel["holdout_cost_stress"] = stress
    sel["holdout_outliers_removed"] = {
        f"top_{p}pct_removed": evaluate(
            r, cost, best["t1_pct"], best["sl_pct"], best["cap_min"],
            w[HOLDOUT], drop_top_pct=p,
        )
        for p in (1.0, 5.0)
    }
    out["selected"] = sel
    return out


def verdict(sel: dict, hypotheses_total: int) -> tuple[str, list[str]]:
    """One status, and every reason it is not the better one."""
    reasons: list[str] = []
    dev = sel["dev"].get("net_expectancy_r") or -1.0
    val = sel["val"].get("net_expectancy_r") or -1.0
    hold = sel["holdout"].get("net_expectancy_r") or -1.0
    if dev <= 0:
        reasons.append("development expectancy is not positive")
    if val <= 0:
        reasons.append("validation expectancy is not positive")
    if hold <= 0:
        reasons.append("untouched holdout expectancy is not positive")
    if sel["holdout"].get("trades", 0) < MIN_TRADES_PER_WINDOW:
        reasons.append("the holdout has too few resolved trades to confirm anything")
    stressed = sel["holdout_cost_stress"].get("cost_2.0x", {})
    if (stressed.get("net_expectancy_r") or -1.0) <= 0:
        reasons.append("it does not survive twice the modelled cost")
    trimmed = sel["holdout_outliers_removed"].get("top_5.0pct_removed", {})
    if (trimmed.get("net_expectancy_r") or -1.0) <= 0:
        reasons.append("the holdout result depends on its top 5% of trades")
    p = sel["dev"].get("p_value_one_sided")
    threshold = 0.05 / max(1, hypotheses_total)
    if p is None or p >= threshold:
        reasons.append(
            f"the development p-value does not clear the Bonferroni threshold "
            f"{threshold:.2e} for {hypotheses_total} counted hypotheses"
        )
    if not reasons:
        return VALIDATED, []
    if dev > 0 and val > 0:
        return RESEARCH_LEAD, reasons
    return REJECTED, reasons


def describe_reach(
    r: reachmod.Reach, cost: np.ndarray, mask: np.ndarray | None = None
) -> dict:
    """Descriptive reach rates and the cost hurdle each distance has to clear.

    Used for two things: a series too short to split — where no T1 is selected and
    no verdict is claimed, because on a few weeks of candles the best-looking
    distance is a property of those weeks — and the cost-hurdle table of a
    five-year instrument, which states how far a target has to be before the round
    trip is payable at all.
    """
    m = r.eligible if mask is None else (r.eligible & mask)
    total = int(m.sum())
    med_cost = (
        round(float(np.nanmedian(cost[m])), 6)
        if total and np.isfinite(cost[m]).any() else None
    )
    rows = []
    for t1 in T1_GRID:
        ff = r.first_fav[r.index_of(t1)][m].astype(np.int32)
        fa = r.first_adv[r.index_of(t1)][m].astype(np.int32)
        row = {
            "t1_pct": t1,
            "clears_round_trip_cost": None if med_cost is None
            else bool(t1 > med_cost),
            "t1_in_multiples_of_cost": None if not med_cost
            else round(t1 / med_cost, 2),
        }
        cap_max = max(CAPS)
        in_f = (ff > 0) & (ff <= cap_max)
        in_a = (fa > 0) & (fa <= cap_max)
        # An equal adverse move arriving first is what a stop at this distance
        # would have paid for; it is reported beside the reach rate so a high
        # reach rate is never read on its own.
        row[f"adverse_same_size_first_within_{cap_max}m_pct"] = (
            round(100.0 * float(np.sum(in_a & (~in_f | (fa < ff)))) / total, 3)
            if total else None
        )
        for cap in CAPS:
            row[f"reached_within_{cap}m_pct"] = (
                round(100.0 * float(np.sum((ff > 0) & (ff <= cap))) / total, 3)
                if total else None
            )
        hit = ff[(ff > 0) & (ff <= max(CAPS))]
        row["minutes_to_reach_median"] = (
            round(float(np.median(hit)), 1) if hit.size else None
        )
        rows.append(row)
    return {
        "instrument": r.instrument,
        "side": reachmod.side_name(r.side),
        "decision_instants": total,
        "sessions": int(np.unique(r.session[m]).size) if total else 0,
        "round_trip_cost_pct_median": med_cost,
        "reach": rows,
    }
