"""Phase 26 §3 — the measured tradability advisory.

The most reliable finding in this project is not a signal, it is an arithmetic
one: some vehicles cannot be bought profitably at any hit rate, and the book says
so before entry. On the captured window ZINC's measured spread alone was ~0.97x
the risk of a 30%-of-premium stop, and the >10% break-even hurdle band held ~93%
of the whole loss. Neither of those needs a forecast.

This module turns that into a table with one row per instrument, computed from
stored books under the baseline geometry:

* **spread as a fraction of risk** — how much of the stop the round trip eats
  before the underlying does anything;
* **median break-even hurdle** — the same measured number the Phase 23 shadow
  brackets at 3% and 5%;
* what the instrument actually produced, so a reader can check the verdict
  against the outcome instead of trusting the rule;
* a verdict of ``TRADABLE_ECONOMICS`` / ``MARGINAL_ECONOMICS`` /
  ``AVOID_ECONOMICS`` from thresholds fixed **here, before any result is read**.

Two things this is not. It is not a live refusal: nothing in Phase 26 is wired to
a gate, a signal or the order path, and the report says what a rule would have
cost as well as saved. And it is not a strategy: refusing a bad vehicle removes
losses, it does not create an edge, and every band on the captured window was
negative — including the cheapest one.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import metrics
from app.research.phase26 import AVOID, MARGINAL, TRADABLE

# Thresholds fixed before results. The hurdle brackets deliberately match the
# 3% / 5% brackets the Phase 23 live shadow runs, so the two can be read against
# each other; this module does not decide any live threshold.
HURDLE_CHEAP_PCT = 3.0
HURDLE_RICH_PCT = 5.0
SPREAD_RISK_OK = 0.10
SPREAD_RISK_BAD = 0.25

TIME_BUCKETS = (
    ("open_0930_1030", 570, 630),
    ("morning_1030_1200", 630, 720),
    ("midday_1200_1400", 720, 840),
    ("late_1400_close", 840, 1_440),
)


def verdict(spread_over_risk: float, median_hurdle_pct: float) -> str:
    """The economics verdict for one vehicle, from measured numbers only."""
    if (
        not np.isfinite(spread_over_risk)
        or not np.isfinite(median_hurdle_pct)
    ):
        return MARGINAL
    if spread_over_risk >= SPREAD_RISK_BAD or median_hurdle_pct > HURDLE_RICH_PCT:
        return AVOID
    if spread_over_risk <= SPREAD_RISK_OK and median_hurdle_pct <= HURDLE_CHEAP_PCT:
        return TRADABLE
    return MARGINAL


def _band_masks(hurdle: np.ndarray) -> list[tuple[str, np.ndarray]]:
    return [
        ("hurdle_le_3pct", np.isfinite(hurdle) & (hurdle <= HURDLE_CHEAP_PCT)),
        (
            "hurdle_3_to_5pct",
            np.isfinite(hurdle)
            & (hurdle > HURDLE_CHEAP_PCT)
            & (hurdle <= HURDLE_RICH_PCT),
        ),
        ("hurdle_gt_5pct", np.isfinite(hurdle) & (hurdle > HURDLE_RICH_PCT)),
    ]


def row(c, o) -> dict:
    """One instrument's advisory row under the baseline geometry."""
    res = o.resolved
    n = int(res.sum())
    hurdle = c.feat["hurdle_pct"]
    spread_pts = c.entry_ask - c.entry_bid
    spread_over_risk = (
        float(np.nanmedian(spread_pts[res]) / np.median(o.risk[res]))
        if n else float("nan")
    )
    med_hurdle = float(np.nanmedian(hurdle[res])) if n else float("nan")
    out = {
        "instrument": c.instrument,
        "trades": n,
        "median_measured_spread_pct": (
            round(float(np.nanmedian(o.spread_pct[res])), 3) if n else None
        ),
        "spread_as_fraction_of_risk": (
            round(spread_over_risk, 3) if np.isfinite(spread_over_risk) else None
        ),
        "median_break_even_hurdle_pct": (
            round(med_hurdle, 3) if np.isfinite(med_hurdle) else None
        ),
        "all_costs_as_fraction_of_risk": (
            round(float(np.median(o.cost_points[res]) / np.median(o.risk[res])), 3)
            if n else None
        ),
        "median_premium_ask": (
            round(float(np.nanmedian(c.entry_ask[res])), 2) if n else None
        ),
        "measured_t1_before_sl_pct": (
            round(100.0 * float(o.t1_before_sl[res].mean()), 2) if n else None
        ),
        "measured_avg_net_r": (
            round(float(o.net_r[res].mean()), 4) if n else None
        ),
        "measured_total_net_rupees": (
            round(float(o.net_rupees[res].sum()), 2) if n else None
        ),
        "verdict": verdict(spread_over_risk, med_hurdle),
        "advisory_only": True,
    }
    if not n:
        return out

    bands = []
    for name, m in _band_masks(hurdle):
        mm = m & res
        k = int(mm.sum())
        bands.append({
            "band": name,
            "trades": k,
            "avg_net_r": round(float(o.net_r[mm].mean()), 4) if k else None,
            "total_net_rupees": round(float(o.net_rupees[mm].sum()), 2) if k else None,
            "profit_factor": metrics.profit_factor(o.net_r[mm]) if k else None,
        })
    out["hurdle_bands"] = bands

    minute = c.feat["minute_of_day"]
    hours = []
    for name, lo, hi in TIME_BUCKETS:
        mm = res & (minute >= lo) & (minute < hi)
        k = int(mm.sum())
        hours.append({
            "bucket": name,
            "trades": k,
            "t1_before_sl_pct": (
                round(100.0 * float(o.t1_before_sl[mm].mean()), 2) if k else None
            ),
            "avg_net_r": round(float(o.net_r[mm].mean()), 4) if k else None,
            "total_net_rupees": round(float(o.net_rupees[mm].sum()), 2) if k else None,
        })
    out["time_of_day"] = hours

    # What a refusal would have done on this window, both directions, so the
    # decision is not sold on the saving alone.
    rich = np.isfinite(hurdle) & (hurdle > HURDLE_RICH_PCT) & res
    late = res & (minute >= 840)
    out["counterfactual_if_refused"] = {
        "hurdle_gt_5pct": _counterfactual(o, rich),
        "after_1400": _counterfactual(o, late),
        "note": (
            "retrospective on this study's research candidates, which buy every "
            "eligible ATM leg on a fixed stride. It is not a forecast of what "
            "the same refusal would do to the live selected book, where the "
            "trade population is different"
        ),
    }
    return out


def _counterfactual(o, mask: np.ndarray) -> dict:
    """What refusing ``mask`` would have removed — losses and winners both."""
    k = int(mask.sum())
    if not k:
        return {"trades_refused": 0}
    net = o.net_rupees[mask]
    winners = net[net > 0]
    losers = net[net < 0]
    return {
        "trades_refused": k,
        "net_rupees_not_taken": round(float(net.sum()), 2),
        "losses_avoided_rupees": round(float(losers.sum()), 2),
        "winners_given_up_rupees": round(float(winners.sum()), 2),
        "winners_given_up_count": int(winners.size),
        "avg_net_r_refused": round(float(o.net_r[mask].mean()), 4),
    }


def table(rows: list[dict]) -> dict:
    """The advisory table plus the counts a reader needs to judge it."""
    by = {v: [r["instrument"] for r in rows if r["verdict"] == v]
          for v in (TRADABLE, MARGINAL, AVOID)}
    scored = [r for r in rows if r.get("trades")]
    saved = sum(
        (r.get("counterfactual_if_refused", {}).get("hurdle_gt_5pct", {})
         .get("losses_avoided_rupees") or 0.0)
        for r in scored
    )
    given_up = sum(
        (r.get("counterfactual_if_refused", {}).get("hurdle_gt_5pct", {})
         .get("winners_given_up_rupees") or 0.0)
        for r in scored
    )
    return {
        "rows": sorted(rows, key=lambda r: (r["verdict"], r["instrument"])),
        "counts": {k: len(v) for k, v in by.items()},
        "instruments_by_verdict": by,
        "thresholds": {
            "hurdle_cheap_pct": HURDLE_CHEAP_PCT,
            "hurdle_rich_pct": HURDLE_RICH_PCT,
            "spread_over_risk_ok": SPREAD_RISK_OK,
            "spread_over_risk_bad": SPREAD_RISK_BAD,
            "fixed_before_results": True,
        },
        "refusing_hurdle_gt_5pct_on_this_window": {
            "losses_avoided_rupees": round(float(saved), 2),
            "winners_given_up_rupees": round(float(given_up), 2),
            "net_rupees_not_taken": round(float(saved + given_up), 2),
        },
    }
