"""Phase 52 §14/§15 — the statistic set, and the gross-versus-net decomposition.

Two deliberate reuses from Phase 51, imported and not reimplemented: the
one-sided p-value on net R and the Benjamini-Hochberg correction. A second
implementation would eventually disagree with the first, and then two phases
would publish two different corrections of the same kind of search.

§15 is the part of this file that carries the argument. A mechanism can look
positive for four different reasons and only one of them is an edge:

* **DIRECTIONAL_EDGE** — positive before cost *and* after it. The move is real
  and it pays for the trade;
* **COST_DRIVEN_APPEARANCE** — the gross edge exists but cost eats it, or the
  net result is positive only because the cost gate threw away the trades that
  would have lost. The label is a warning that the filter, not the direction,
  is doing the work;
* **RECENT_PERIOD_OVERFIT** — the net result is carried by one year;
* **NO_EDGE** — gross is not positive either. There is nothing for a better exit
  or a cheaper broker to rescue.

The cost share is reported as a percentage of the gross edge consumed, which is
the number §15 asks for and the one that says whether this mechanism is a
trading problem or an arithmetic one.
"""
from __future__ import annotations

import numpy as np

from app.research.phase52 import (
    COST_DRIVEN_APPEARANCE,
    DIRECTIONAL_EDGE,
    MAX_YEAR_SHARE,
    NO_EDGE,
    RECENT_PERIOD_OVERFIT,
)
from app.research.phase52.stats import (
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
    "session_distribution",
    "effect_reading",
]


def _arr(rows: list[dict], key: str) -> np.ndarray:
    return np.array([float(r[key]) for r in rows], dtype=np.float64) if rows \
        else np.zeros(0, dtype=np.float64)


def describe(rows: list[dict]) -> dict:
    """§14's metric set for one cohort of resolved trades.

    ``trade_count`` and ``session_count`` are equal by construction — §11 allows
    one trade per instrument per session — so this is one of the rare research
    cohorts where the trade count is also the count of independent observations.
    """
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
        "mfe_r_median": float(np.median(_arr(rows, "mfe_r"))),
        "mae_r_median": float(np.median(_arr(rows, "mae_r"))),
        # §15's number: what share of the gross move was spent on the round trip.
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


def session_distribution(rows: list[dict]) -> dict:
    """§14's session distribution: when in the session these trades happen."""
    if not rows:
        return {"measured": False}
    mins = _arr(rows, "minutes_since_open_at_entry")
    buckets: dict[str, int] = {}
    for m in mins:
        label = f"{int(m // 60)}h-{int(m // 60) + 1}h"
        buckets[label] = buckets.get(label, 0) + 1
    return {
        "measured": True,
        "median_minutes_after_open": float(np.median(mins)),
        "earliest_minutes_after_open": float(mins.min()),
        "latest_minutes_after_open": float(mins.max()),
        "by_hour_after_open": dict(sorted(buckets.items())),
        "trades_per_session": 1.0,
        "note": (
            "one trade per instrument per session by construction, so the trade "
            "count is also the count of independent sessions"
        ),
    }


def effect_reading(summary: dict, years: dict[str, dict]) -> str:
    """§15's four-way reading of what the numbers actually say."""
    if not summary.get("measured"):
        return NO_EDGE
    gross = summary.get("gross_expectancy_points")
    net = summary.get("net_expectancy_points")
    if gross is None or net is None:
        return NO_EDGE
    if gross <= 0:
        return NO_EDGE
    if net <= 0:
        return COST_DRIVEN_APPEARANCE
    total = summary.get("net_total_r") or 0.0
    if total > 0 and years:
        worst = max(
            (v["net_total_r"] / total for v in years.values() if total > 0),
            default=0.0,
        )
        if worst > MAX_YEAR_SHARE:
            return RECENT_PERIOD_OVERFIT
    return DIRECTIONAL_EDGE
