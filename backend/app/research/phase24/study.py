"""Phase 24 §8/§9/§10/§12/§18 — validate, stress, compare, and refuse.

Order matters here and is enforced by the code path: discovery proposes on the
development window, validation confirms, walk-forward checks that the rule is not
a single lucky period, the holdout is read exactly once at the end, and the
robustness grid re-resolves the *same candidates* under worse execution rather
than re-searching for a rule that survives it.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import (
    RESEARCH_ONLY,
    conditions,
    discover,
    metrics,
    outcomes,
    pool,
)

# §9 grids. Every value is a worsening; none of them is a free improvement.
SPREAD_MULTIPLIERS = (1.0, 1.25, 1.5)
ENTRY_DELAY_BARS = (1, 15, 30, 60)   # the signal fires on a close; 1 = next open
EXIT_DELAY_BARS = (0, 1, 2)
WALK_FORWARD_FOLDS = 5

# §9 parameter perturbation: pullback bands are the one discovered parameter with
# an obvious neighbourhood, so a surviving rule is re-run on the neighbours.
PULLBACK_NEIGHBOURS = {
    "pullback_0_20pct": ("pullback_0_20pct", "pullback_20_40pct"),
    "pullback_20_40pct": ("pullback_0_20pct", "pullback_20_40pct", "pullback_40_60pct"),
    "pullback_40_60pct": ("pullback_20_40pct", "pullback_40_60pct", "pullback_60_100pct"),
    "pullback_60_100pct": ("pullback_40_60pct", "pullback_60_100pct"),
}


# Geometry sweep: the pool's own economics at each stop band and target multiple,
# with no entry rule at all. This is what shows whether a rule has any room to
# work in, before the search is blamed for finding nothing.
GEOMETRY_BANDS = (2.0, 3.0)
GEOMETRY_TARGETS = (1.0, 1.5, 2.0, 3.0)


def geometry_sweep(instruments: tuple[str, ...]) -> list[dict]:
    """Base-rate economics per (stop band, target) with no conditions applied."""
    rows: list[dict] = []
    for inst in instruments:
        for band in GEOMETRY_BANDS:
            for t1 in GEOMETRY_TARGETS:
                p = pool.build(inst, stop_atr=band, t1_r=t1)
                if p is None:
                    continue
                o = p.out
                res = o.resolved
                n = int(res.sum())
                if n == 0:
                    continue
                rows.append({
                    "instrument": inst,
                    "stop_band_atr": band,
                    "t1_r": t1,
                    "trades": n,
                    "t1_before_sl_pct": round(float(o.t1_before_sl[res].mean() * 100), 2),
                    "breakeven_t1_pct_before_costs": round(100.0 / (1.0 + t1), 2),
                    "avg_net_r": round(float(o.net_r[res].mean()), 4),
                    "cost_as_fraction_of_risk": round(
                        float(np.median(o.cost_points) / np.median(o.risk)), 3
                    ),
                })
    return rows


def _cohort_mask(p: pool.Pool, masks: dict, rule: dict) -> np.ndarray:
    m = p.side == rule["side_int"]
    for name in rule["conditions"]:
        m = m & masks[name]
    return m


def walk_forward(p: pool.Pool, masks: dict, rule: dict, folds: int = WALK_FORWARD_FOLDS) -> dict:
    """Equal-length chronological folds across the whole series.

    Reported per fold and as the count of folds with positive net R, because a
    rule that makes all its money in one fold is what §10 exists to catch.
    """
    o = p.out
    cohort = _cohort_mask(p, masks, rule) & o.resolved
    ts = p.ts
    lo, hi = int(ts.min()), int(ts.max()) + 1
    edges = np.linspace(lo, hi, folds + 1).astype(np.int64)
    rows = []
    for k in range(folds):
        w = (ts >= edges[k]) & (ts < edges[k + 1]) & o.resolved
        stats = metrics.summarise(o, cohort & w, discover.base_rate(o, w))
        rows.append({
            "fold": k + 1,
            "from_ts": int(edges[k]),
            "to_ts": int(edges[k + 1]),
            "trades": stats.get("trades", 0),
            "t1_before_sl_pct": stats.get("t1_before_sl_pct"),
            "avg_net_r": stats.get("avg_net_r"),
            "total_net_r": stats.get("total_net_r"),
        })
    scored = [r for r in rows if (r["trades"] or 0) > 0]
    positive = sum(1 for r in scored if (r["avg_net_r"] or 0) > 0)
    return {
        "folds": rows,
        "folds_scored": len(scored),
        "folds_positive": positive,
        "stable": bool(scored and positive == len(scored)),
    }


def cost_sensitivity(rule: dict, *, quick: bool = False) -> list[dict]:
    """Re-resolve the same rule under worse spread, entry delay and exit delay."""
    out: list[dict] = []
    grid: list[dict] = [{"spread_multiplier": m} for m in SPREAD_MULTIPLIERS]
    if not quick:
        grid += [{"entry_delay_bars": d} for d in ENTRY_DELAY_BARS[1:]]
        grid += [{"exit_delay_bars": d} for d in EXIT_DELAY_BARS[1:]]
    for kw in grid:
        p = pool.build(rule["instrument"], stop_atr=rule["stop_band_atr"], **kw)
        if p is None:
            continue
        masks = conditions.masks(p.feat, p.side)
        m = _cohort_mask(p, masks, rule) & p.out.resolved
        stats = metrics.summarise(p.out, m, discover.base_rate(p.out, p.out.resolved))
        label = ", ".join(f"{k}={v}" for k, v in kw.items())
        out.append({
            "variant": label,
            "trades": stats.get("trades", 0),
            "t1_before_sl_pct": stats.get("t1_before_sl_pct"),
            "avg_net_r": stats.get("avg_net_r"),
            "profit_factor": stats.get("profit_factor"),
            "survives": bool((stats.get("avg_net_r") or -1) > 0),
        })
    return out


def parameter_perturbation(p: pool.Pool, masks: dict, rule: dict) -> list[dict]:
    """Swap each pullback band for its neighbours; a rule should not collapse."""
    rows: list[dict] = []
    for name in rule["conditions"]:
        for neighbour in PULLBACK_NEIGHBOURS.get(name, ()):
            if neighbour == name:
                continue
            variant = dict(rule)
            variant["conditions"] = [
                neighbour if c == name else c for c in rule["conditions"]
            ]
            m = _cohort_mask(p, masks, variant) & p.out.resolved
            stats = metrics.summarise(
                p.out, m, discover.base_rate(p.out, p.out.resolved)
            )
            rows.append({
                "swap": f"{name} -> {neighbour}",
                "trades": stats.get("trades", 0),
                "avg_net_r": stats.get("avg_net_r"),
                "survives": bool((stats.get("avg_net_r") or -1) > 0),
            })
    return rows


def regimes(p: pool.Pool, masks: dict, rule: dict) -> dict:
    """§9 regime split: volatility, gap days, time of day, trend agreement."""
    o = p.out
    cohort = _cohort_mask(p, masks, rule) & o.resolved
    br = discover.base_rate(o, o.resolved)
    splits = {
        "volatility_expanding": masks["volatility_expanding"],
        "volatility_compressed": masks["volatility_compressed"],
        "gap_day": masks["gap_day_0p5pct"],
        "flat_open_day": masks["flat_open_day"],
        "trend_aligned": masks["trend_5m_and_15m_agree"],
        "trend_against": masks["trend_15m_opposes"],
    }
    for name, _lo, _hi in conditions.TIME_BUCKETS:
        splits[f"time_{name}"] = masks[f"time_{name}"]
    out: dict[str, dict] = {}
    for name, m in splits.items():
        stats = metrics.summarise(o, cohort & m, br)
        out[name] = {
            "trades": stats.get("trades", 0),
            "t1_before_sl_pct": stats.get("t1_before_sl_pct"),
            "avg_net_r": stats.get("avg_net_r"),
        }
    return out


def baselines(p: pool.Pool, masks: dict) -> dict:
    """§18 — the existing strategies' shapes and a no-edge control.

    The Pullback, Continuation and Core A+ ideas are expressed in *this* study's
    vocabulary and measured with identical geometry, costs and windows. That is
    the only way the comparison is fair; it is not the production code path, and
    it is labelled as a reconstruction rather than as those engines' own results.
    """
    o = p.out
    br = discover.base_rate(o, o.resolved)
    defs = {
        "PULLBACK_RECONSTRUCTION": (
            "trend_5m_and_15m_agree", "pullback_20_40pct", "continuation_confirmed",
        ),
        "CONTINUATION_RECONSTRUCTION": (
            "trend_5m_and_15m_agree", "momentum_agrees_0p5atr",
        ),
        "CORE_APLUS_RECONSTRUCTION": (
            "trend_5m_and_15m_agree", "pullback_20_40pct",
            "continuation_confirmed", "room_1p5atr_ahead",
        ),
        "EXPANSION_CHASE": ("candle_expansion_1p5x", "momentum_agrees_0p5atr"),
    }
    out: dict[str, dict] = {}
    for label, names in defs.items():
        for side in (outcomes.LONG, outcomes.SHORT):
            m = p.side == side
            for n in names:
                m = m & masks[n]
            stats = metrics.summarise(o, m & o.resolved, br)
            out[f"{label}_{'LONG' if side == outcomes.LONG else 'SHORT'}"] = {
                "conditions": list(names),
                "trades": stats.get("trades", 0),
                "t1_before_sl_pct": stats.get("t1_before_sl_pct"),
                "avg_net_r": stats.get("avg_net_r"),
                "profit_factor": stats.get("profit_factor"),
            }
    # No-edge control: every candidate, both sides. This is the number every
    # discovered rule is really competing with.
    whole = metrics.summarise(o, o.resolved, br)
    out["NO_EDGE_CONTROL_ALL_CANDIDATES"] = {
        "conditions": [],
        "trades": whole.get("trades", 0),
        "t1_before_sl_pct": whole.get("t1_before_sl_pct"),
        "avg_net_r": whole.get("avg_net_r"),
        "profit_factor": whole.get("profit_factor"),
    }
    return out


def trades_per_period(p: pool.Pool, masks: dict, rule: dict) -> dict:
    """§13/§19 selectivity: how often the rule actually fires."""
    o = p.out
    cohort = _cohort_mask(p, masks, rule) & o.resolved
    sessions = int(np.unique(p.session[o.resolved]).size) or 1
    n = int(cohort.sum())
    return {
        "trades": n,
        "sessions": sessions,
        "trades_per_session": round(n / sessions, 3),
        "trades_per_week": round(5.0 * n / sessions, 2),
        "pct_of_candidates": round(100.0 * n / max(1, int(o.resolved.sum())), 3),
        "no_trade_pct": round(100.0 - 100.0 * n / max(1, int(o.resolved.sum())), 3),
    }


def evaluate_rule(p: pool.Pool, masks: dict, rule: dict, s: discover.Search) -> dict:
    """Fill in validation, holdout, walk-forward, robustness and selectivity.

    The holdout window is evaluated here and nowhere else; no branch of this
    function feeds a holdout number back into a selection decision.
    """
    rule = dict(rule)
    rule["validation"] = s.evaluate(
        tuple(rule["conditions"]), rule["side_int"], s.val
    )
    rule["holdout"] = s.evaluate(
        tuple(rule["conditions"]), rule["side_int"], s.hold
    )
    rule["walk_forward"] = walk_forward(p, masks, rule)
    rule["parameter_perturbation"] = parameter_perturbation(p, masks, rule)
    rule["regimes"] = regimes(p, masks, rule)
    rule["selectivity"] = trades_per_period(p, masks, rule)
    rule["status"] = RESEARCH_ONLY
    return rule
