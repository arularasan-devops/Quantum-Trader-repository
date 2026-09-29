"""Phase 28 §7 — buy-and-hold, the benchmark a long swing rule has to beat.

This module exists because of one specific way a multi-day study can lie to its
author. A long-only rule on an instrument that rose over the window will show a
positive net R that it did not earn: it collected drift. Every intraday phase in
this programme was immune to that by construction — a position that is flat at
15:30 cannot collect a multi-year trend — and this one is not.

So the passive alternative is measured on the same series, in the same window,
paying real costs, and reduced to a single comparable number: **points earned per
day of exposure**. A cohort's number is its net points divided by the trading days
it actually held a position, which is the like-for-like question: if I am going to
tie up capital and attention for a day, does the rule pay me more for that day than
simply holding does?

Two honest limits on the comparison, both reported rather than buried:

* buy-and-hold is exposed every day, and a selective rule is exposed on a fraction
  of them. Per-day-of-exposure is the fair ratio, and it flatters neither side;
* for a SHORT cohort the passive alternative is not buy-and-hold, it is holding
  nothing. Its bar is therefore zero, which is already enforced by requiring a
  positive net R, and the buy-and-hold clause is not applied to it.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24.data import Series
from app.research.phase24.outcomes import LONG
from app.research.phase28 import costs as cost_model
from app.research.phase28 import outcomes


def _max_drawdown_pct(close: np.ndarray) -> float:
    """Peak-to-trough fall in per cent, on closes."""
    if close.size == 0:
        return 0.0
    peak = np.maximum.accumulate(close)
    dd = (close - peak) / np.maximum(peak, 1e-9)
    return round(float(dd.min() * 100.0), 2)


def buy_and_hold(
    series: Series,
    vehicle: str,
    *,
    lo_ts: int | None = None,
    hi_ts: int | None = None,
) -> dict:
    """Hold from the first open in the window to the last close, costs charged."""
    ts = series.ts
    m = np.ones(ts.size, dtype=bool)
    if lo_ts is not None:
        m &= ts >= int(lo_ts)
    if hi_ts is not None:
        m &= ts < int(hi_ts)
    if int(m.sum()) < 2:
        return {
            "available": False,
            "note": "fewer than two daily bars in this window",
        }
    idx = np.flatnonzero(m)
    first, last = int(idx[0]), int(idx[-1])
    entry = float(series.open[first])
    exit_price = float(series.close[last])
    calendar_days = max(1.0, (float(ts[last]) - float(ts[first])) / 86_400.0)
    trading_days = int(idx.size)
    charge = cost_model.round_trip(
        series.instrument,
        vehicle,
        entry=entry,
        exit_price=exit_price,
        calendar_days_held=calendar_days,
    )
    cost_points = float(charge.get("cost_points") or 0.0)
    gross = exit_price - entry
    net = gross - cost_points
    years = calendar_days / 365.25
    cagr = (
        round((((exit_price / entry) ** (1.0 / years)) - 1.0) * 100.0, 2)
        if years > 0 and entry > 0 and exit_price > 0 else None
    )
    return {
        "available": True,
        "instrument": series.instrument,
        "vehicle": vehicle,
        "entry": round(entry, 4),
        "exit": round(exit_price, 4),
        "trading_days": trading_days,
        "calendar_days": round(calendar_days, 1),
        "gross_points": round(gross, 4),
        "cost_points": round(cost_points, 4),
        "rolls_charged": int(charge.get("rolls") or 0),
        "net_points": round(net, 4),
        "net_return_pct": round(net / entry * 100.0, 2),
        "cagr_pct": cagr,
        "max_drawdown_pct": _max_drawdown_pct(series.close[m]),
        "net_points_per_trading_day": round(net / trading_days, 6),
        "note": (
            "one round trip charged over the whole window, plus futures rolls "
            "where applicable. This is the alternative to trading, not a strategy"
        ),
    }


def cohort_exposure(o: outcomes.SwingOutcomes, mask: np.ndarray) -> dict:
    """A cohort's net points per day of exposure, and how exposed it was."""
    m = np.asarray(mask, dtype=bool) & o.resolved
    n = int(m.sum())
    if n == 0:
        return {"trades": 0, "exposed_trading_days": 0,
                "net_points_per_exposed_day": None}
    days = int(o.bars_held[m].sum())
    net = float(o.net_points[m].sum())
    return {
        "trades": n,
        "exposed_trading_days": days,
        "total_net_points": round(net, 4),
        "net_points_per_exposed_day": (
            round(net / days, 6) if days > 0 else None
        ),
        "median_trading_days_held": float(np.median(o.bars_held[m])),
    }


def compare(
    o: outcomes.SwingOutcomes,
    mask: np.ndarray,
    side_int: int,
    bh: dict,
) -> dict:
    """Does this cohort pay more per exposed day than holding the instrument?"""
    exposure = cohort_exposure(o, mask)
    if side_int != LONG:
        return {
            **exposure,
            "benchmark": "FLAT (holding nothing)",
            "benchmark_points_per_day": 0.0,
            "beats_benchmark": bool(
                (exposure.get("net_points_per_exposed_day") or 0.0) > 0.0
            ),
            "applicable": True,
            "note": (
                "a short cohort's passive alternative is holding nothing, so its "
                "bar is zero rather than buy-and-hold"
            ),
        }
    if not bh.get("available"):
        return {
            **exposure,
            "benchmark": "BUY_AND_HOLD",
            "benchmark_points_per_day": None,
            "beats_benchmark": None,
            "applicable": False,
            "note": "buy-and-hold could not be measured in this window",
        }
    per_day = exposure.get("net_points_per_exposed_day")
    bench = float(bh["net_points_per_trading_day"])
    return {
        **exposure,
        "benchmark": "BUY_AND_HOLD",
        "benchmark_points_per_day": bench,
        "ratio_vs_benchmark": (
            round(per_day / bench, 3)
            if per_day is not None and bench not in (0.0, None) else None
        ),
        "beats_benchmark": (
            None if per_day is None else bool(per_day > bench)
        ),
        "applicable": True,
        "note": (
            "points per day of exposure, both sides paying real costs. A long rule "
            "that earns less per exposed day than simply holding is collecting "
            "drift with extra steps"
        ),
    }


__all__ = ["buy_and_hold", "cohort_exposure", "compare"]
