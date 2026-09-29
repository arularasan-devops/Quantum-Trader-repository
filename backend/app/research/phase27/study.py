"""Phase 27 §3 — validate, stress, compare and refuse, at 5 and 15 minutes.

The order is the same one Phase 24 enforced and is enforced again by the code
path: discovery proposes on development, validation confirms, walk-forward checks
the rule is not one lucky period, the untouched holdout is read exactly once at
the end, and the robustness grid re-resolves *the same candidates* under worse
execution rather than searching for a rule that happens to survive it.

Two things are deliberately expressed in clock time rather than in bars, because
a grid stated in bars would silently be four times harsher at 5 minutes than at
15 and the two studies would stop being comparable:

* the entry-delay grid is "act 15 / 30 / 60 minutes late", converted to bars;
* the exit-delay grid is one and two bars, which is the same question — how much
  does it cost to be one bar slow — and is reported in minutes.

The slippage grid matters more here than anywhere else in the project. The
futures feed publishes candles, so the spread is unmeasured; the honest way to
say that is to re-run every leader at two and three points per side and require
it to survive, rather than to print one optimistic number.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import RESEARCH_ONLY, metrics, outcomes
from app.research.phase24 import conditions as p24conditions
from app.research.phase27 import conditions, discover, pool

SPREAD_MULTIPLIERS = (1.0, 1.25, 1.5)
SLIPPAGE_POINTS = (2.0, 3.0)          # baseline is settings.futures_slippage_points
ENTRY_DELAY_MINUTES = (15, 30, 60)
EXIT_DELAY_BARS = (1, 2)
WALK_FORWARD_FOLDS = 5

PULLBACK_NEIGHBOURS = {
    "pullback_0_20pct": ("pullback_0_20pct", "pullback_20_40pct"),
    "pullback_20_40pct": ("pullback_0_20pct", "pullback_20_40pct", "pullback_40_60pct"),
    "pullback_40_60pct": (
        "pullback_20_40pct", "pullback_40_60pct", "pullback_60_100pct",
    ),
    "pullback_60_100pct": ("pullback_40_60pct", "pullback_60_100pct"),
}

GEOMETRY_BANDS = (1.0, 2.0, 3.0)
GEOMETRY_TARGETS = (1.0, 1.5, 2.0, 3.0)


def geometry_sweep(
    instruments: tuple[str, ...],
    timeframes: tuple[int, ...],
) -> list[dict]:
    """Pool economics per (timeframe, stop band, target) with no entry rule at all.

    This is the table the whole phase exists for: it says, before any rule is
    discovered or blamed, what fraction of the risk taken the round trip costs at
    each bar size, and how far the achieved T1-before-SL rate sits from the
    pre-cost breakeven the geometry demands.
    """
    rows: list[dict] = []
    for inst in instruments:
        for tf in timeframes:
            for band in GEOMETRY_BANDS:
                for t1 in GEOMETRY_TARGETS:
                    p = pool.build(inst, tf, stop_atr=band, t1_r=t1)
                    if p is None:
                        continue
                    o = p.out
                    res = o.resolved
                    n = int(res.sum())
                    if n == 0:
                        continue
                    achieved = float(o.t1_before_sl[res].mean() * 100.0)
                    breakeven = 100.0 / (1.0 + t1)
                    rows.append({
                        "instrument": inst,
                        "timeframe_minutes": tf,
                        "stop_band_atr": band,
                        "t1_r": t1,
                        "trades": n,
                        "t1_before_sl_pct": round(achieved, 2),
                        "breakeven_t1_pct_before_costs": round(breakeven, 2),
                        "gap_vs_breakeven_pct": round(achieved - breakeven, 2),
                        "avg_net_r": round(float(o.net_r[res].mean()), 4),
                        "median_risk_points": round(float(np.median(o.risk)), 3),
                        "median_cost_points": round(float(np.median(o.cost_points)), 3),
                        "cost_as_fraction_of_risk": round(
                            float(np.median(o.cost_points) / np.median(o.risk)), 4
                        ),
                    })
    return rows


def _cohort_mask(p: pool.Pool, masks: dict, rule: dict) -> np.ndarray:
    m = p.side == rule["side_int"]
    for name in rule["conditions"]:
        m = m & masks[name]
    return m


def walk_forward(
    p: pool.Pool,
    masks: dict,
    rule: dict,
    folds: int = WALK_FORWARD_FOLDS,
) -> dict:
    """Equal-length chronological folds across the whole series."""
    o = p.out
    cohort = _cohort_mask(p, masks, rule) & o.resolved
    ts = p.ts
    lo, hi = int(ts.min()), int(ts.max()) + 1
    edges = np.linspace(lo, hi, folds + 1).astype(np.int64)
    rows: list[dict] = []
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


def stress_grid(timeframe: int, *, quick: bool = False) -> list[dict]:
    """The execution variants a leader must survive, in this timeframe's bars."""
    tf = int(timeframe)
    grid: list[dict] = [{"spread_multiplier": m} for m in SPREAD_MULTIPLIERS]
    grid += [{"slippage_points": s} for s in SLIPPAGE_POINTS]
    if not quick:
        grid += [
            {"entry_delay_bars": max(1, m // tf)} for m in ENTRY_DELAY_MINUTES
        ]
        grid += [{"exit_delay_bars": d} for d in EXIT_DELAY_BARS]
    return grid


def cost_sensitivity(rule: dict, *, quick: bool = False) -> list[dict]:
    """Re-resolve the same rule under worse spread, slippage and timing."""
    tf = int(rule["timeframe_minutes"])
    out: list[dict] = []
    for kw in stress_grid(tf, quick=quick):
        p = pool.build(rule["instrument"], tf, stop_atr=rule["stop_band_atr"], **kw)
        if p is None:
            continue
        masks = conditions.masks(p.feat, p.side, tf)
        m = _cohort_mask(p, masks, rule) & p.out.resolved
        stats = metrics.summarise(p.out, m, discover.base_rate(p.out, p.out.resolved))
        label = ", ".join(f"{k}={v}" for k, v in kw.items())
        if "entry_delay_bars" in kw:
            label += f" ({int(kw['entry_delay_bars']) * tf} minutes late)"
        if "exit_delay_bars" in kw:
            label += f" ({int(kw['exit_delay_bars']) * tf} minutes late)"
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
    """Regime split: volatility, gap days, time of day, trend agreement."""
    o = p.out
    cohort = _cohort_mask(p, masks, rule) & o.resolved
    br = discover.base_rate(o, o.resolved)
    splits = {
        "volatility_expanding": masks["volatility_expanding"],
        "volatility_compressed": masks["volatility_compressed"],
        "gap_day": masks["gap_day_0p5pct"],
        "flat_open_day": masks["flat_open_day"],
        "trend_aligned": masks["trend_5bar_and_15bar_agree"],
        "trend_against": masks["trend_15bar_opposes"],
    }
    for name, _lo, _hi in p24conditions.TIME_BUCKETS:
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
    """The existing strategy shapes and a no-edge control, at this timeframe.

    Reconstructions in this study's vocabulary with identical geometry, costs and
    windows — not the production engines' own results, and labelled as such. The
    no-edge control is every candidate on both sides: that is the number a
    discovered rule is really competing with.
    """
    o = p.out
    br = discover.base_rate(o, o.resolved)
    defs = {
        "PULLBACK_RECONSTRUCTION": (
            "trend_5bar_and_15bar_agree", "pullback_20_40pct",
            "continuation_confirmed",
        ),
        "CONTINUATION_RECONSTRUCTION": (
            "trend_5bar_and_15bar_agree", "momentum_agrees_0p5atr",
        ),
        "CORE_APLUS_RECONSTRUCTION": (
            "trend_5bar_and_15bar_agree", "pullback_20_40pct",
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
    """Selectivity: how often the rule actually fires, and how often it refuses."""
    o = p.out
    cohort = _cohort_mask(p, masks, rule) & o.resolved
    sessions = int(np.unique(p.session[o.resolved]).size) or 1
    n = int(cohort.sum())
    resolved = max(1, int(o.resolved.sum()))
    return {
        "trades": n,
        "sessions": sessions,
        "trades_per_session": round(n / sessions, 3),
        "trades_per_week": round(5.0 * n / sessions, 2),
        "pct_of_candidates": round(100.0 * n / resolved, 3),
        "no_trade_pct": round(100.0 - 100.0 * n / resolved, 3),
    }


def evaluate_rule(
    p: pool.Pool,
    masks: dict,
    rule: dict,
    s: discover.Search,
) -> dict:
    """Fill in validation, holdout, walk-forward, robustness and selectivity.

    The holdout window is evaluated here and nowhere else, and no branch of this
    function feeds a holdout number back into a selection decision.
    """
    rule = dict(rule)
    rule["timeframe_minutes"] = int(p.timeframe)
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
