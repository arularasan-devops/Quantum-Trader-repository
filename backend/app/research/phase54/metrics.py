"""Phase 54 — the statistic set, the six readings, and the three effects.

A multi-day result can look positive for six different reasons and only two of
them are worth anything:

* **DIRECTIONAL_EDGE** — positive before cost *and* after it, and not obviously
  carried by the clock. The expansion/reclaim condition is doing the work;
* **HOLDING_PERIOD_EFFECT** — the same entries are materially better at the ten
  session cap than at the five. Then the *time*, not the entry, is what pays,
  and the entry condition is at best a way of being in the market;
* **COST_DRIVEN_APPEARANCE** — a gross edge that cost eats, or a net that only
  exists because the gate discarded the trades that would have lost;
* **RECENT_PERIOD_OVERFIT** — one year or one quarter carries the net;
* **INSUFFICIENT_SAMPLE** — too few trades for any of the above to be
  distinguishable. On a mechanism that can fire a handful of times a year this
  is the reading to expect, and it is an honest answer rather than a failure;
* **NO_EDGE** — not positive before cost either. Nothing for a longer hold or a
  cheaper broker to rescue.

``effect_source`` answers the separate question the brief asks: *if* a row is
positive, which of direction, cost efficiency and holding period produced it,
or an interaction. It is computed from three measurable comparisons — gross
expectancy, cost share of gross, and the same entries at both hold caps — and
never from a story.
"""
from __future__ import annotations

import numpy as np

from app.research.phase54 import (
    COST_DRIVEN_APPEARANCE,
    DIRECTIONAL_EDGE,
    EFFECT_COST,
    EFFECT_DIRECTIONAL,
    EFFECT_INTERACTION,
    EFFECT_NONE,
    EFFECT_TIME,
    HOLDING_PERIOD_EFFECT,
    INSUFFICIENT_SAMPLE,
    MAX_QUARTER_SHARE,
    MAX_YEAR_SHARE,
    MIN_TRADES_FOR_PROMOTION,
    NO_EDGE,
    RECENT_PERIOD_OVERFIT,
)
from app.research.phase54.stats import (
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
    "effect_reading",
    "effect_source",
    "holding_delta",
]

# A hold cap that changes net expectancy by less than this is not a holding
# period effect, it is noise with a different label. Declared here rather than
# discovered from the output.
MATERIAL_HOLD_DELTA_R = 0.10
# Cost is "decisive" when it is at least this share of the gross move.
DECISIVE_COST_SHARE = 0.25


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
        # One position at a time and no pyramiding, so every trade has its own
        # entry session by construction. Both numbers are reported anyway: a
        # reader should not have to take the invariant on trust.
        "session_count": len({r["entry_session"] for r in rows}),
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
        "average_hold_sessions": float(_arr(rows, "hold_sessions").mean()),
        "median_hold_sessions": float(np.median(_arr(rows, "hold_sessions"))),
        "median_target_distance": float(np.median(_arr(rows, "target_distance"))),
        "median_cost_points": float(np.median(cost_pts)),
        "median_cost_multiple": float(np.median(_arr(rows, "cost_multiple"))),
        "median_risk_points": float(np.median(_arr(rows, "risk_points"))),
        "median_risk_atr": float(np.median(_arr(rows, "risk_atr"))),
        "median_pullback_sessions": float(
            np.median(_arr(rows, "pullback_sessions"))
        ),
        "mfe_r_median": float(np.median(_arr(rows, "mfe_r"))),
        "mae_r_median": float(np.median(_arr(rows, "mae_r"))),
        "cost_share_of_gross_pct": (
            100.0 * float(cost_pts.sum()) / gross_abs if gross_abs > 0 else None
        ),
        "target_rate": float(np.mean([r["outcome"] == "TARGET" for r in rows])),
        "stop_rate": float(np.mean([r["outcome"] == "STOP" for r in rows])),
        "time_exit_rate": float(
            np.mean([r["outcome"] == "TIME_EXIT" for r in rows])
        ),
        "data_end_rate": float(
            np.mean([r["outcome"] == "DATA_END" for r in rows])
        ),
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
        gross_r = _arr(sel, "gross_r")
        out[label] = {
            "trade_count": len(sel),
            "net_expectancy_r": float(net_r.mean()),
            "gross_expectancy_r": float(gross_r.mean()),
            "net_total_r": float(net_r.sum()),
            "win_rate": float((net_r > 0).mean()),
        }
    return out


def holding_delta(short_hold: dict, long_hold: dict) -> dict:
    """The five-session row against the ten-session row, on the same entries.

    This is the comparison the phase exists to make, so it is a first-class
    number rather than something a reader has to compute from two tables.
    """
    if not (short_hold.get("measured") and long_hold.get("measured")):
        return {"measured": False}
    d_net = (long_hold["net_expectancy_r"] - short_hold["net_expectancy_r"])
    d_gross = (long_hold["gross_expectancy_r"] - short_hold["gross_expectancy_r"])
    return {
        "measured": True,
        "short_net_expectancy_r": short_hold["net_expectancy_r"],
        "long_net_expectancy_r": long_hold["net_expectancy_r"],
        "short_gross_expectancy_r": short_hold["gross_expectancy_r"],
        "long_gross_expectancy_r": long_hold["gross_expectancy_r"],
        "net_delta_r": d_net,
        "gross_delta_r": d_gross,
        "material": bool(abs(d_net) >= MATERIAL_HOLD_DELTA_R),
        "longer_is_better": bool(d_net > 0),
    }


def effect_reading(summary: dict, years: dict[str, dict],
                   quarters: dict[str, dict], hold: dict | None = None) -> str:
    """The six-way reading of what the numbers actually say."""
    if not summary.get("measured"):
        return NO_EDGE
    gross = summary.get("gross_expectancy_points")
    net = summary.get("net_expectancy_points")
    if gross is None or net is None:
        return NO_EDGE
    if int(summary.get("trade_count") or 0) < MIN_TRADES_FOR_PROMOTION:
        # Small sample first. A sign computed over a handful of multi-day
        # trades is arithmetic, not evidence, and labelling it DIRECTIONAL_EDGE
        # would be the single most misleading thing this report could do.
        return INSUFFICIENT_SAMPLE
    if gross <= 0:
        return NO_EDGE
    if net <= 0:
        return COST_DRIVEN_APPEARANCE
    total = summary.get("net_total_r") or 0.0
    if total > 0:
        if years and max(
            (v["net_total_r"] / total for v in years.values()), default=0.0
        ) > MAX_YEAR_SHARE:
            return RECENT_PERIOD_OVERFIT
        if quarters and max(
            (v["net_total_r"] / total for v in quarters.values()), default=0.0
        ) > MAX_QUARTER_SHARE:
            return RECENT_PERIOD_OVERFIT
    if hold and hold.get("measured") and hold.get("material") \
            and hold.get("longer_is_better") \
            and (hold["short_net_expectancy_r"] <= 0):
        # Positive only once the clock is loosened: the hold is carrying it.
        return HOLDING_PERIOD_EFFECT
    return DIRECTIONAL_EDGE


def effect_source(summary: dict, hold: dict | None = None) -> str:
    """Which of direction, cost and holding produced a positive net, if any."""
    if not summary.get("measured"):
        return EFFECT_NONE
    net = summary.get("net_expectancy_r")
    gross = summary.get("gross_expectancy_r")
    if net is None or gross is None or net <= 0:
        return EFFECT_NONE
    contributors = []
    if gross > 0:
        contributors.append(EFFECT_DIRECTIONAL)
    share = summary.get("cost_share_of_gross_pct")
    if share is not None and share < 100.0 * DECISIVE_COST_SHARE:
        contributors.append(EFFECT_COST)
    if hold and hold.get("measured") and hold.get("material") \
            and hold.get("longer_is_better"):
        contributors.append(EFFECT_TIME)
    if not contributors:
        return EFFECT_NONE
    if len(contributors) > 1:
        return EFFECT_INTERACTION
    return contributors[0]
