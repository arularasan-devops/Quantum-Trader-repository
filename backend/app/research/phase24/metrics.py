"""Phase 24 §6/§7/§10 — the statistics, including the ones that spoil a result.

Deliberately included because they are what kill most candidates:

* a Wilson interval on the T1 rate, so 95% on 20 trades is never printed next to
  90% on 2,000 as if they meant the same thing;
* a binomial p-value against the pool's own base rate, so a cohort has to beat
  the coin it was drawn from, not zero;
* outlier contribution and the same result with the top 1% and 5% of winners
  removed;
* max drawdown and the longest losing streak on the chronological equity curve.
"""
from __future__ import annotations

import math

import numpy as np

from app.research.phase24 import outcomes

# §7: nothing below this is reported as a result; it is reported as too small.
MIN_TRADES = 100


def _q(a: np.ndarray, p: float) -> float:
    return float(np.quantile(a, p)) if a.size else float("nan")


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a proportion, in percent."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = hits / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(100.0 * max(0.0, centre - half), 2),
            round(100.0 * min(1.0, centre + half), 2))


def _norm_sf(x: float) -> float:
    """Upper tail of the standard normal, via erfc — no scipy dependency."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def binomial_p(hits: int, n: int, base_rate: float) -> float:
    """One-sided p-value that the cohort's hit rate exceeds the pool base rate.

    Normal approximation: n is in the hundreds or thousands here, and an exact
    binomial tail over 100k candidates would dominate the study's runtime.
    """
    if n <= 0 or not (0.0 < base_rate < 1.0):
        return 1.0
    mu = n * base_rate
    sd = math.sqrt(n * base_rate * (1.0 - base_rate))
    if sd <= 0:
        return 1.0
    return round(_norm_sf((hits - mu - 0.5) / sd), 6)


def max_drawdown(net_r: np.ndarray) -> float:
    """Deepest peak-to-trough fall of the cumulative R curve, in R."""
    if net_r.size == 0:
        return 0.0
    eq = np.cumsum(net_r)
    peak = np.maximum.accumulate(eq)
    return round(float(np.max(peak - eq)), 3)


def longest_losing_streak(net_r: np.ndarray) -> int:
    worst = run = 0
    for v in net_r:
        if v < 0:
            run += 1
            worst = max(worst, run)
        else:
            run = 0
    return int(worst)


def profit_factor(net_r: np.ndarray) -> float | None:
    wins = float(net_r[net_r > 0].sum())
    losses = float(-net_r[net_r < 0].sum())
    if losses <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / losses, 3)


def summarise(o: outcomes.Outcomes, mask: np.ndarray, base_rate: float) -> dict:
    """Every §7 and §14 number for one cohort, in chronological order.

    ``mask`` must already exclude unresolved candidates; the caller owns that so
    a cohort's trade count always equals the number of rows behind its metrics.
    """
    n = int(mask.sum())
    if n == 0:
        return {"trades": 0, "status": "EMPTY"}
    net_r = o.net_r[mask]
    hits = int(o.t1_before_sl[mask].sum())
    lo, hi = wilson(hits, n)
    order = np.argsort(o.idx[mask], kind="stable")
    chron = net_r[order]
    top1 = max(1, int(round(0.01 * n)))
    top5 = max(1, int(round(0.05 * n)))
    ranked = np.sort(net_r)[::-1]
    total = float(net_r.sum())
    without_top1 = float(total - ranked[:top1].sum())
    without_top5 = float(total - ranked[:top5].sum())
    winners = net_r[net_r > 0]
    losers = net_r[net_r < 0]
    return {
        "trades": n,
        "t1_hits": hits,
        "sl_hits": int(o.sl_hit[mask].sum()),
        "timeouts": int((o.outcome[mask] == outcomes.TIMEOUT).sum()),
        "t1_before_sl_pct": round(100.0 * hits / n, 2),
        "t1_ci95": [lo, hi],
        "t2_before_sl_pct": round(100.0 * float(o.t2_before_sl[mask].mean()), 2),
        "t3_before_sl_pct": round(100.0 * float(o.t3_before_sl[mask].mean()), 2),
        "positive_net_pct": round(100.0 * float((net_r > 0).mean()), 2),
        "avg_net_r": round(float(net_r.mean()), 4),
        "median_net_r": round(float(np.median(net_r)), 4),
        "total_net_r": round(total, 2),
        "expectancy_r": round(float(net_r.mean()), 4),
        "profit_factor": profit_factor(net_r),
        "avg_winner_r": round(float(winners.mean()), 4) if winners.size else None,
        "avg_loser_r": round(float(losers.mean()), 4) if losers.size else None,
        "max_drawdown_r": max_drawdown(chron),
        "longest_losing_streak": longest_losing_streak(chron),
        "avg_hold_bars": round(float(o.bars_held[mask].mean()), 1),
        "median_hold_bars": round(float(np.median(o.bars_held[mask])), 1),
        "avg_mfe_r": round(float(o.mfe_r[mask].mean()), 3),
        "avg_mae_r": round(float(o.mae_r[mask].mean()), 3),
        "median_bars_to_t1": (
            round(float(np.median(o.bars_to_t1[mask][o.bars_to_t1[mask] >= 0])), 1)
            if (o.bars_to_t1[mask] >= 0).any() else None
        ),
        "median_bars_to_sl": (
            round(float(np.median(o.bars_to_sl[mask][o.bars_to_sl[mask] >= 0])), 1)
            if (o.bars_to_sl[mask] >= 0).any() else None
        ),
        "net_r_p10": round(_q(net_r, 0.10), 3),
        "net_r_p90": round(_q(net_r, 0.90), 3),
        "outlier_top1_contribution_pct": (
            round(100.0 * (total - without_top1) / abs(total), 1) if total else None
        ),
        "total_net_r_excl_top1pct": round(without_top1, 2),
        "total_net_r_excl_top5pct": round(without_top5, 2),
        "avg_net_r_excl_top5pct": round(without_top5 / max(1, n - top5), 4),
        "p_value_vs_base_rate": binomial_p(hits, n, base_rate),
        "base_rate_pct": round(100.0 * base_rate, 2),
        "sample_status": "OK" if n >= MIN_TRADES else "BELOW_MIN_SAMPLE",
    }
