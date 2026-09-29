"""Phase 51 §8/§10/§12 — the gates a candidate has to survive.

The objective is §12's: **positive net expectancy after costs**, not a high win
rate. A rule that wins three times in ten and pays four to one is admissible; a
rule that wins nine times in ten and loses money is not, and the ranking here
cannot be fooled by the second because it never reads the win rate.

Four gates, in the order that kills the most candidates first:

1. **sample** — §10's floors. Fewer than the declared trades or sessions and the
   result is not measured, whatever its sign;
2. **concentration** — if the best single trade is more than a third of the net,
   the rule made its money once. §10 asks for that rejection explicitly;
3. **stability** — positive in at least three separate years, and surviving 1.5x
   and 2x cost. A rule that dies at 1.5x cost was never an edge, it was a cost
   assumption;
4. **multiplicity** — Benjamini-Hochberg over the **entire** trial count,
   including every hypothesis discarded before the ranking. The denominator is
   the number generated, not the number that looked interesting.

The p-value is one-sided on the mean net R, from a t statistic. Net R per trade
is not normal — it is a bounded, skewed, three-outcome distribution — so the
p-value is an ordering statistic and a multiplicity budget, not a probability
anybody should quote. That caveat is a constant in the artefact rather than a
remark here, because it travels with the number.
"""
from __future__ import annotations

import math

import numpy as np

from app.research.phase51 import (
    COST_STRESS_MULTIPLES,
    FDR_ALPHA,
    MAX_DRAWDOWN_R,
    MAX_TOP_TRADE_SHARE,
    MIN_SESSIONS,
    MIN_TRADES,
    MIN_YEARS_POSITIVE,
)

P_VALUE_IS_ORDERING_ONLY = (
    "THE_P_VALUE_COMES_FROM_A_T_STATISTIC_ON_NET_R_WHICH_IS_A_BOUNDED_SKEWED_"
    "THREE_OUTCOME_DISTRIBUTION_SO_IT_IS_USED_TO_ORDER_CANDIDATES_AND_TO_SIZE_"
    "THE_MULTIPLICITY_BUDGET_AND_IS_NOT_A_PROBABILITY_TO_BE_QUOTED"
)

STABILITY_PASS = "STABLE"
STABILITY_CONCENTRATED = "ONE_TRADE_CARRIES_IT"
STABILITY_ONE_PERIOD = "ONE_PERIOD_CARRIES_IT"
STABILITY_COST_FRAGILE = "DIES_UNDER_COST_STRESS"
STABILITY_DRAWDOWN = "DRAWDOWN_BEYOND_THE_DECLARED_BOUND"
STABILITY_UNMEASURED = "SAMPLE_BELOW_THE_DECLARED_FLOOR"


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def one_sided_p(x: np.ndarray) -> float:
    """P(mean this positive by chance), one-sided, from a t statistic.

    A normal tail on the t statistic: with the trade counts this engine requires
    (a hundred minimum) the difference from the exact t distribution is far
    smaller than the error from the non-normality of net R, which no tail
    correction can fix.
    """
    x = x[np.isfinite(x)]
    n = x.size
    if n < 3:
        return 1.0
    sd = float(np.std(x, ddof=1))
    if sd <= 0.0:
        return 0.0 if float(np.mean(x)) > 0 else 1.0
    t = float(np.mean(x)) / (sd / math.sqrt(n))
    return max(0.0, min(1.0, 1.0 - _normal_cdf(t)))


def benjamini_hochberg(p_values: list[float], *, tests: int,
                       alpha: float = FDR_ALPHA) -> list[bool]:
    """BH over ``tests`` hypotheses, which may exceed the p-values supplied.

    ``tests`` is the number of hypotheses the engine *evaluated*, not the number
    it chose to report. Correcting against the survivors is how a data-mined
    fluke passes: every candidate discarded along the way was a chance to find
    this one, and the correction has to know that.
    """
    m = max(int(tests), len(p_values))
    if m == 0 or not p_values:
        return [False] * len(p_values)
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    out = [False] * len(p_values)
    largest = -1
    for rank, i in enumerate(order, start=1):
        if p_values[i] <= alpha * rank / m:
            largest = rank
    for rank, i in enumerate(order, start=1):
        if rank <= largest:
            out[i] = True
    return out


def max_drawdown_r(net_r: np.ndarray) -> float:
    """Peak-to-trough of the cumulative net-R curve, in R."""
    if net_r.size == 0:
        return 0.0
    curve = np.cumsum(net_r)
    peak = np.maximum.accumulate(curve)
    return float(np.max(peak - curve)) if curve.size else 0.0


def describe(net_r: np.ndarray, net_points: np.ndarray, sessions: np.ndarray,
             years: np.ndarray, mfe_r: np.ndarray, mae_r: np.ndarray,
             cost_points: np.ndarray) -> dict:
    """§10's metric set for one cohort of resolved trades."""
    n = int(net_r.size)
    if n == 0:
        return {"trade_count": 0, "session_count": 0, "net_expectancy_r": None,
                "measured": False}
    wins = net_r > 0
    losses = net_r < 0
    gross_win = float(net_r[wins].sum()) if wins.any() else 0.0
    gross_loss = float(-net_r[losses].sum()) if losses.any() else 0.0
    total = float(net_r.sum())
    best = float(net_r.max())
    return {
        "measured": True,
        "trade_count": n,
        "session_count": int(np.unique(sessions).size),
        "year_count": int(np.unique(years).size),
        "win_rate": float(wins.mean()),
        "net_expectancy_r": float(net_r.mean()),
        "net_expectancy_points": float(net_points.mean()),
        "net_total_r": total,
        "net_total_points": float(net_points.sum()),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0
                         else (None if gross_win <= 0 else float("inf")),
        "average_winner_r": float(net_r[wins].mean()) if wins.any() else None,
        "average_loser_r": float(net_r[losses].mean()) if losses.any() else None,
        "max_drawdown_r": max_drawdown_r(net_r),
        "mfe_r_median": float(np.median(mfe_r)) if mfe_r.size else None,
        "mae_r_median": float(np.median(mae_r)) if mae_r.size else None,
        "cost_points_per_trade": float(np.mean(cost_points)),
        "cost_share_of_gross": (
            float(np.sum(cost_points) / np.sum(np.abs(net_points + cost_points)))
            if float(np.sum(np.abs(net_points + cost_points))) > 0 else None
        ),
        # §10's concentration test: what share of the whole result is one trade.
        "top_trade_share": (best / total) if total > 0 else None,
        "p_value_one_sided": one_sided_p(net_r),
    }


def per_year(net_r: np.ndarray, years: np.ndarray) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for y in np.unique(years):
        sel = years == y
        out[str(int(y))] = {
            "trade_count": int(sel.sum()),
            "net_expectancy_r": float(net_r[sel].mean()),
            "net_total_r": float(net_r[sel].sum()),
        }
    return out


def per_regime(net_r: np.ndarray, regime: np.ndarray) -> dict[str, dict]:
    """Result by volatility regime, so a one-regime rule is visible as one."""
    out: dict[str, dict] = {}
    for label in ("QUIET", "NORMAL", "VOLATILE", "UNKNOWN"):
        sel = regime == label
        if not sel.any():
            continue
        out[label] = {
            "trade_count": int(sel.sum()),
            "net_expectancy_r": float(net_r[sel].mean()),
        }
    return out


def regime_labels(volatility_ratio: np.ndarray) -> np.ndarray:
    """Fast-ATR-over-slow-ATR banded at fixed cuts, declared not fitted."""
    out = np.full(volatility_ratio.size, "UNKNOWN", dtype=object)
    finite = np.isfinite(volatility_ratio)
    out[finite & (volatility_ratio <= 0.8)] = "QUIET"
    out[finite & (volatility_ratio > 0.8) & (volatility_ratio <= 1.25)] = "NORMAL"
    out[finite & (volatility_ratio > 1.25)] = "VOLATILE"
    return out


def stability_status(summary: dict, years: dict[str, dict],
                     cost_stress: dict[str, dict]) -> str:
    """§10's rejections, in the order they should be applied.

    Sample first, because an unmeasured rule has no other property worth
    reading; then the one-trade and one-period tests; then cost fragility.
    """
    if not summary.get("measured"):
        return STABILITY_UNMEASURED
    if (summary["trade_count"] < MIN_TRADES
            or summary["session_count"] < MIN_SESSIONS):
        return STABILITY_UNMEASURED
    share = summary.get("top_trade_share")
    if share is not None and share > MAX_TOP_TRADE_SHARE:
        return STABILITY_CONCENTRATED
    positive_years = sum(1 for v in years.values() if v["net_expectancy_r"] > 0)
    if positive_years < MIN_YEARS_POSITIVE:
        return STABILITY_ONE_PERIOD
    if summary["max_drawdown_r"] > MAX_DRAWDOWN_R:
        return STABILITY_DRAWDOWN
    for mult, row in cost_stress.items():
        if float(mult) <= 1.0:
            continue
        exp = row.get("net_expectancy_r")
        if exp is None or exp <= 0:
            return STABILITY_COST_FRAGILE
    return STABILITY_PASS


def cost_stress_multiples() -> tuple[float, ...]:
    return COST_STRESS_MULTIPLES
