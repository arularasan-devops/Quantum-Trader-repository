"""Phase 33 §6/§7/§8/§9 — movement, hold-time economics and giveback.

Everything here is aggregation over the arrays :mod:`path` already measured, so a
new cohort or a new horizon costs no extra pass over the bars and two cohorts can
never end up measured on different samples by accident.

Four numbers are deliberately reported side by side at every horizon, because
each one alone is the way this study could mislead:

* ``mfe`` — the best the trade ever looked. Not realisable.
* ``ret`` — what a trade closed at that horizon actually got, gross.
* ``net`` — the same after the round-trip cost, which is the only column that
  decides anything.
* ``giveback`` — ``mfe - ret``, how much of the best look was handed back by
  holding to that horizon.

A high reach rate is not profitability and a high MFE is not profit; keeping the
columns adjacent is what stops the report from implying otherwise.
"""
from __future__ import annotations

import numpy as np

from app.research.phase33 import (
    FUT_LEVELS,
    HOLD_GRID,
    MAX_HOLD,
    MFE_PERCENTILES,
    PROFITABLE_COST_MULTIPLE,
    SESSION_CLOSE,
)
from app.research.phase33.path import Path, adverse_first_rate, minutes_to, reach_rate

PCTILES = (50, 75, 90)


def _f(x: float) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 6)


def _pf(net: np.ndarray) -> float:
    wins = float(net[net > 0].sum())
    losses = float(-net[net < 0].sum())
    if losses <= 0:
        return float("inf") if wins > 0 else float("nan")
    return wins / losses


def _max_drawdown(net: np.ndarray) -> float:
    """Worst peak-to-trough of the equity curve, in percentage points.

    Rows arrive in chronological order because every cohort mask is applied to a
    time-ordered array, so this is the real sequence, not a reshuffle.
    """
    if net.size == 0:
        return float("nan")
    eq = np.cumsum(net)
    return float(np.max(np.maximum.accumulate(eq) - eq))


def _sessions(p: Path, mask: np.ndarray) -> int:
    return int(np.unique(p.session[mask]).size)


def series_at(p: Path, horizon: int | str, mask: np.ndarray) -> dict[str, np.ndarray]:
    """The favourable, adverse and close arrays of one cohort at one horizon."""
    if horizon == SESSION_CLOSE:
        return {"fav": p.sess_fav[mask], "adv": p.sess_adv[mask],
                "ret": p.sess_ret[mask]}
    h = int(horizon)
    return {"fav": p.fav[h][mask], "adv": p.adv[h][mask], "ret": p.ret[h][mask]}


def economics(
    p: Path, mask: np.ndarray, cost: np.ndarray, horizon: int | str
) -> dict:
    """§7 — one row of the hold-time economics table."""
    a = series_at(p, horizon, mask)
    c = cost[mask]
    ok = np.isfinite(a["ret"]) & np.isfinite(c)
    ret = a["ret"][ok].astype(np.float64)
    fav = a["fav"][ok].astype(np.float64)
    adv = a["adv"][ok].astype(np.float64)
    cc = c[ok].astype(np.float64)
    net = ret - cc
    give = fav - ret
    with np.errstate(invalid="ignore", divide="ignore"):
        give_frac = np.where(fav > 0, give / fav, np.nan)
    return {
        "horizon": horizon,
        "n": int(ret.size),
        "sessions": _sessions(p, mask),
        "cost_pct_median": _f(np.median(cc)) if cc.size else None,
        "gross_expectancy_pct": _f(np.mean(ret)) if ret.size else None,
        "net_expectancy_pct": _f(np.mean(net)) if net.size else None,
        "net_win_rate": _f(np.mean(net > 0)) if net.size else None,
        "profit_factor": _f(_pf(net)) if net.size else None,
        "max_drawdown_pct": _f(_max_drawdown(net)),
        "mfe_pct_median": _f(np.median(fav)) if fav.size else None,
        "mfe_pct_mean": _f(np.mean(fav)) if fav.size else None,
        "mae_pct_median": _f(np.median(adv)) if adv.size else None,
        "mae_pct_mean": _f(np.mean(adv)) if adv.size else None,
        "giveback_pct_mean": _f(np.mean(give)) if give.size else None,
        # Median, not mean: the ratio's denominator is a favourable excursion that
        # is sometimes a fraction of a tick, and a handful of those dominate a mean
        # ratio while saying nothing about a typical trade.
        "giveback_share_of_mfe_median": (
            _f(float(np.nanmedian(give_frac))) if give.size else None
        ),
    }


def hold_table(p: Path, mask: np.ndarray, cost: np.ndarray) -> list[dict]:
    """§5/§7 — every frozen horizon plus the hold to the session close."""
    rows = [economics(p, mask, cost, h) for h in HOLD_GRID]
    rows.append(economics(p, mask, cost, SESSION_CLOSE))
    return rows


def move_distribution(p: Path, mask: np.ndarray, horizon: int) -> list[dict]:
    """§6 — reach rate, timing and adverse-first for every frozen distance."""
    out: list[dict] = []
    for lvl in FUT_LEVELS:
        mins = minutes_to(p, lvl, horizon, mask)
        row = {
            "level_pct": lvl,
            "horizon": horizon,
            "reach_rate": _f(reach_rate(p, lvl, horizon, mask)),
            "adverse_same_size_first_rate":
                _f(adverse_first_rate(p, lvl, horizon, mask)),
            "n_reached": int(mins.size),
        }
        for q in PCTILES:
            row[f"minutes_p{q}"] = (
                _f(float(np.percentile(mins, q))) if mins.size else None
            )
        out.append(row)
    return out


def giveback(p: Path, mask: np.ndarray, cost: np.ndarray) -> dict:
    """§8 — peak, time to peak, retained profit, and the two failure paths.

    "Profitable at some point" means the favourable excursion cleared the row's
    own round-trip cost, not that it was merely positive: a move that never paid
    for itself was never a profit and cannot have been given back.
    """
    fav_max = p.fav[MAX_HOLD][mask].astype(np.float64)
    peak_min = p.peak_minute[mask].astype(np.float64)
    post = p.post_peak_low[mask].astype(np.float64)
    c = cost[mask].astype(np.float64)
    ok = np.isfinite(fav_max) & np.isfinite(c)
    fav_max, peak_min, post, c = fav_max[ok], peak_min[ok], post[ok], c[ok]
    profitable = fav_max >= PROFITABLE_COST_MULTIPLE * c

    # Two different questions, so two different minutes. "Fastest" is where the
    # giveback rate itself peaks; "exceeds gain" is the one a holding decision
    # turns on: the first minute from which another minute of holding gives back
    # more than it adds to what the trade keeps.
    retained: list[dict] = []
    prev_give = 0.0
    prev_ret = 0.0
    prev_h = 0
    fastest_rate = float("-inf")
    fastest_at: int | None = None
    exceeds_at: int | None = None
    for h in HOLD_GRID:
        r = p.ret[h][mask][ok].astype(np.float64)
        good = np.isfinite(r)
        give = float(np.mean(fav_max[good] - r[good])) if good.any() else float("nan")
        kept = float(np.mean(r[good])) if good.any() else float("nan")
        span = max(h - prev_h, 1)
        rate = (give - prev_give) / span
        gain_rate = (kept - prev_ret) / span
        if np.isfinite(rate) and rate > fastest_rate:
            fastest_rate, fastest_at = rate, h
        if (
            exceeds_at is None
            and np.isfinite(rate)
            and np.isfinite(gain_rate)
            and rate > gain_rate
        ):
            exceeds_at = h
        retained.append({
            "horizon": h,
            "retained_pct_mean": _f(kept) if good.any() else None,
            "giveback_vs_peak_pct_mean": _f(give),
            "giveback_rate_pct_per_min": _f(rate),
            "retained_gain_rate_pct_per_min": _f(gain_rate),
        })
        prev_give, prev_ret, prev_h = give, kept, h

    n_prof = int(profitable.sum())
    return {
        "n": int(fav_max.size),
        "sessions": _sessions(p, mask),
        "peak_minute_median": _f(float(np.median(peak_min))) if peak_min.size else None,
        "peak_minute_p75": (
            _f(float(np.percentile(peak_min, 75))) if peak_min.size else None
        ),
        "peak_mfe_pct_median": _f(float(np.median(fav_max))) if fav_max.size else None,
        "profitable_at_some_point_rate":
            _f(float(np.mean(profitable))) if fav_max.size else None,
        "n_profitable_at_some_point": n_prof,
        "returned_to_entry_after_profit_rate": (
            _f(float(np.mean(post[profitable] <= 0.0))) if n_prof else None
        ),
        "turned_net_negative_after_profit_rate": (
            _f(float(np.mean(post[profitable] <= -c[profitable]))) if n_prof else None
        ),
        "fastest_giveback_minute": fastest_at,
        "giveback_exceeds_gain_after_minute": exceeds_at,
        "retained_by_horizon": retained,
    }


def empirical_targets(
    p: Path, mask: np.ndarray, cost: np.ndarray, horizon: int
) -> list[dict]:
    """§9 — candidate targets read off the excursion distribution.

    These are percentiles of what actually happened, labelled as reach rates. A
    percentile is not a probability and this function does not turn one into a
    forecast: the target it names is only a candidate for the chronological
    selection in :mod:`select`.
    """
    fav = p.fav[horizon][mask].astype(np.float64)
    c = cost[mask].astype(np.float64)
    ok = np.isfinite(fav) & np.isfinite(c)
    fav, c = fav[ok], c[ok]
    if fav.size == 0:
        return []
    med_cost = float(np.median(c))
    out: list[dict] = []
    for q in MFE_PERCENTILES:
        # The (100-q)th percentile of the favourable excursion is the distance
        # reached by q% of the cohort.
        lvl = float(np.percentile(fav, 100 - q))
        out.append({
            "target_from_percentile": q,
            "level_pct": _f(lvl),
            "empirical_reach_rate": _f(float(np.mean(fav >= lvl))),
            "cost_multiple": _f(lvl / med_cost) if med_cost > 0 else None,
            "clears_cost": bool(lvl > med_cost),
            "basis": "EMPIRICAL_REACH_RATE",
        })
    return out
