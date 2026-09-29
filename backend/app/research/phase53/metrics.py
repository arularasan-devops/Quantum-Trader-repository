"""Phase 53 — the statistic set, and the five readings of what it means.

A mechanism can look positive for five different reasons and only one of them is
an edge:

* **DIRECTIONAL_EDGE** — positive before cost *and* after it. The move is real
  and it pays for the trade;
* **COST_DRIVEN_APPEARANCE** — the gross edge exists but cost eats it, or the
  net is positive only because the cost gate discarded the trades that would
  have lost. Either way the filter, not the direction, is doing the work;
* **RECENT_PERIOD_OVERFIT** — the net is carried by one year or one quarter;
* **INSUFFICIENT_SAMPLE** — too few trades for any of the above to be
  distinguishable. This reading did not exist in Phase 52 and it is the honest
  label for a row whose sign is real arithmetic over a handful of trades;
* **NO_EDGE** — gross is not positive either. Nothing for a better exit or a
  cheaper broker to rescue.

Two differences from Phase 52's metric set, both forced by the mechanism:

* trade count and session count are **not** equal, because a session may
  contribute one long and one short. Both are reported and neither is inferred
  from the other;
* the target distance is ``target_r x risk`` rather than a fixed price level, so
  the median target distance moves with the stop buffer and is reported per
  variant rather than per instrument.
"""
from __future__ import annotations

import numpy as np

from app.research.phase53 import (
    COST_DRIVEN_APPEARANCE,
    DIRECTIONAL_EDGE,
    INSUFFICIENT_SAMPLE,
    MAX_QUARTER_SHARE,
    MAX_YEAR_SHARE,
    MIN_TRADES_VALIDATION,
    NO_EDGE,
    RECENT_PERIOD_OVERFIT,
)
from app.research.phase53.stats import (
    benjamini_hochberg,
    max_drawdown_r,
    one_sided_p,
)

__all__ = [
    "benjamini_hochberg",
    "one_sided_p",
    "max_drawdown_r",
    "describe",
    "per_period",
    "per_direction",
    "session_distribution",
    "effect_reading",
]


def _arr(rows: list[dict], key: str) -> np.ndarray:
    return np.array([float(r[key]) for r in rows], dtype=np.float64) if rows \
        else np.zeros(0, dtype=np.float64)


def describe(rows: list[dict]) -> dict:
    """The full metric set for one cohort of resolved trades."""
    n = len(rows)
    if n == 0:
        return {"measured": False, "trade_count": 0, "session_count": 0,
                "net_expectancy_r": None, "net_expectancy_points": None,
                "profit_factor": None}
    net_r = _arr(rows, "net_r")
    net_pts = _arr(rows, "net_points")
    gross_pts = _arr(rows, "gross_points")
    cost_pts = _arr(rows, "cost_points")
    wins = net_r > 0
    losses = net_r < 0
    gross_win = float(net_r[wins].sum()) if wins.any() else 0.0
    gross_loss = float(-net_r[losses].sum()) if losses.any() else 0.0
    total_r = float(net_r.sum())
    gross_abs = float(np.abs(gross_pts).sum())
    return {
        "measured": True,
        "trade_count": n,
        "session_count": len({r["session"] for r in rows}),
        "long_count": int(sum(1 for r in rows if r["side"] > 0)),
        "short_count": int(sum(1 for r in rows if r["side"] < 0)),
        "win_rate": float(wins.mean()),
        "average_winner_r": float(net_r[wins].mean()) if wins.any() else None,
        "average_loser_r": float(net_r[losses].mean()) if losses.any() else None,
        "net_expectancy_r": float(net_r.mean()),
        "net_expectancy_points": float(net_pts.mean()),
        "gross_expectancy_points": float(gross_pts.mean()),
        "gross_expectancy_r": float(_arr(rows, "gross_r").mean()),
        "net_total_points": float(net_pts.sum()),
        "net_total_r": total_r,
        "net_total_rupees": float(_arr(rows, "net_rupees").sum()),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0
                         else (None if gross_win <= 0 else float("inf")),
        "max_drawdown_r": max_drawdown_r(net_r),
        "largest_winner_r": float(net_r.max()),
        "largest_loser_r": float(net_r.min()),
        "average_hold_minutes": float(_arr(rows, "hold_minutes").mean()),
        "median_hold_minutes": float(np.median(_arr(rows, "hold_minutes"))),
        "median_target_distance": float(np.median(_arr(rows, "target_distance"))),
        "median_cost_points": float(np.median(cost_pts)),
        "median_cost_multiple": float(np.median(_arr(rows, "cost_multiple"))),
        "median_risk_points": float(np.median(_arr(rows, "risk_points"))),
        "median_stop_atr": float(np.median(_arr(rows, "stop_atr_needed"))),
        "median_retest_delay_minutes": float(
            np.median(_arr(rows, "retest_delay_minutes"))
        ),
        "median_confirm_delay_minutes": float(
            np.median(_arr(rows, "confirm_delay_minutes"))
        ),
        "mfe_r_median": float(np.median(_arr(rows, "mfe_r"))),
        "mae_r_median": float(np.median(_arr(rows, "mae_r"))),
        "cost_share_of_gross_pct": (
            100.0 * float(cost_pts.sum()) / gross_abs if gross_abs > 0 else None
        ),
        "target_rate": float(np.mean([r["outcome"] == "TARGET" for r in rows])),
        "stop_rate": float(np.mean([r["outcome"] == "STOP" for r in rows])),
        "time_exit_rate": float(np.mean([r["outcome"] == "TIME_EXIT" for r in rows])),
        "top_trade_share": (float(net_r.max()) / total_r) if total_r > 0 else None,
        "p_value_one_sided": one_sided_p(net_r),
    }


def per_period(rows: list[dict], key: str) -> dict[str, dict]:
    """Result grouped by a period key (``year`` or ``quarter``), in order."""
    out: dict[str, dict] = {}
    for label in sorted({str(r[key]) for r in rows}):
        sel = [r for r in rows if str(r[key]) == label]
        net_r = _arr(sel, "net_r")
        out[label] = {
            "trade_count": len(sel),
            "net_expectancy_r": float(net_r.mean()),
            "net_total_r": float(net_r.sum()),
            "win_rate": float((net_r > 0).mean()),
        }
    return out


def per_direction(rows: list[dict]) -> dict[str, dict]:
    """Long and short reported apart: one side can carry a pooled result."""
    out: dict[str, dict] = {}
    for label in sorted({str(r["direction"]) for r in rows}):
        sel = [r for r in rows if str(r["direction"]) == label]
        net_r = _arr(sel, "net_r")
        out[label] = {
            "trade_count": len(sel),
            "net_expectancy_r": float(net_r.mean()),
            "net_total_r": float(net_r.sum()),
            "win_rate": float((net_r > 0).mean()),
        }
    return out


def session_distribution(rows: list[dict]) -> dict:
    """When in the session these trades happen, and how many per session."""
    if not rows:
        return {"measured": False}
    mins = _arr(rows, "minutes_since_open_at_entry")
    buckets: dict[str, int] = {}
    for m in mins:
        label = f"{int(m // 60)}h-{int(m // 60) + 1}h"
        buckets[label] = buckets.get(label, 0) + 1
    sessions = len({r["session"] for r in rows})
    return {
        "measured": True,
        "median_minutes_after_open": float(np.median(mins)),
        "earliest_minutes_after_open": float(mins.min()),
        "latest_minutes_after_open": float(mins.max()),
        "by_hour_after_open": dict(sorted(buckets.items())),
        "session_count": sessions,
        "trades_per_session": len(rows) / float(sessions) if sessions else 0.0,
        "note": (
            "at most one long and one short per session, so trades per session "
            "lies between 1 and 2 and the session count is the count of "
            "independent days rather than of trades"
        ),
    }


def effect_reading(summary: dict, years: dict[str, dict],
                   quarters: dict[str, dict]) -> str:
    """The five-way reading of what the numbers actually say."""
    if not summary.get("measured"):
        return NO_EDGE
    gross = summary.get("gross_expectancy_points")
    net = summary.get("net_expectancy_points")
    if gross is None or net is None:
        return NO_EDGE
    if int(summary.get("trade_count") or 0) < MIN_TRADES_VALIDATION:
        # Small-sample first: a sign computed over a handful of trades is
        # arithmetic, not evidence, and labelling it DIRECTIONAL_EDGE would be
        # the single most misleading thing this report could do.
        return INSUFFICIENT_SAMPLE
    if gross <= 0:
        return NO_EDGE
    if net <= 0:
        return COST_DRIVEN_APPEARANCE
    total = summary.get("net_total_r") or 0.0
    if total > 0:
        if years:
            worst_year = max(
                (v["net_total_r"] / total for v in years.values()), default=0.0
            )
            if worst_year > MAX_YEAR_SHARE:
                return RECENT_PERIOD_OVERFIT
        if quarters:
            worst_quarter = max(
                (v["net_total_r"] / total for v in quarters.values()), default=0.0
            )
            if worst_quarter > MAX_QUARTER_SHARE:
                return RECENT_PERIOD_OVERFIT
    return DIRECTIONAL_EDGE
