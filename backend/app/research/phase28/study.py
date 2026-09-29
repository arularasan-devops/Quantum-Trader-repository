"""Phase 28 §9 — validate, stress, benchmark and refuse, on a multi-day hold.

The order is the one the previous phases enforced and is enforced again by the
code path: discovery proposes on development bars, validation confirms, walk-
forward checks the rule is not one lucky period, the untouched holdout is read
exactly once at the end, and the robustness grid re-resolves *the same candidates*
under worse execution rather than searching for a rule that survives it.

The stress grid is where a swing study differs from an intraday one, and every
variant below exists because of a specific way this study could flatter itself:

* ``gap_fill_at_open=False`` reproduces the assumption most daily backtests make
  silently — that a stop fills at the stop price even when the market opens through
  it. Running it as a variant turns "gap risk is charged honestly" from a claim
  into a number: the difference between the two rows is what overnight risk costs;
* the entry delay is **one and two whole sessions**, because the swing equivalent
  of being 15 minutes late is being a day late, and a rule that only works if you
  are filled on the very next open is not a rule, it is a race;
* slippage is charged in points for futures and in per cent for equity, since a
  percentage of a ₹120 share and of a 24,000-point index are not the same object;
* the spread multiplier stands in for the futures spread the candle feed never
  publishes, exactly as in Phase 27.

Nothing in this module writes to a store, an order path, or the live engine.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import metrics
from app.research.phase24.outcomes import LONG
from app.research.phase28 import EQUITY_DELIVERY, RESEARCH_ONLY
from app.research.phase28 import baseline, conditions, discover, outcomes, pool
from app.research.phase28.outcomes import (
    EXIT_LABELS,
    EXIT_STOP_GAP,
    EXIT_TARGET_GAP,
    sides_for,
)

SPREAD_MULTIPLIERS = (1.0, 1.25, 1.5)
SLIPPAGE_POINTS = (2.0, 3.0)
SLIPPAGE_PCT = (0.05, 0.10)
ENTRY_DELAY_BARS = (2, 3)
EXIT_DELAY_BARS = (1,)
WALK_FORWARD_FOLDS = 5

GEOMETRY_BANDS = discover.STOP_BANDS
GEOMETRY_TARGETS = (1.0, 1.5, 2.0, 3.0)

PULLBACK_NEIGHBOURS = {
    "pullback_0_20pct": ("pullback_20_40pct",),
    "pullback_20_40pct": ("pullback_0_20pct", "pullback_40_60pct"),
    "pullback_40_60pct": ("pullback_20_40pct", "pullback_60_100pct"),
    "pullback_60_100pct": ("pullback_40_60pct",),
}


def _cohort_mask(p: pool.Pool, masks: dict, rule: dict) -> np.ndarray:
    m = p.side == rule["side_int"]
    for name in rule["conditions"]:
        m = m & masks[name]
    return m


def geometry_sweep(instruments: tuple[str, ...]) -> list[dict]:
    """Pool economics per (stop band, target, horizon) with no entry rule at all.

    This is the table the phase exists for. It answers, before any rule is
    discovered or blamed, what fraction of the risk taken a multi-day round trip
    costs — the number that was 0.3805 at one minute and 0.0835 at fifteen — and
    how far the achieved T1-before-SL rate sits from the pre-cost breakeven the
    geometry demands.
    """
    rows: list[dict] = []
    for inst in instruments:
        for horizon in discover.HORIZONS:
            for band in GEOMETRY_BANDS:
                for t1 in GEOMETRY_TARGETS:
                    p = pool.build(
                        inst, stop_atr=band, t1_r=t1, horizon_days=horizon
                    )
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
                        "instrument": p.instrument,
                        "vehicle": p.vehicle,
                        "horizon_trading_days": horizon,
                        "stop_band_atr": band,
                        "t1_r": t1,
                        "trades": n,
                        "t1_before_sl_pct": round(achieved, 2),
                        "breakeven_t1_pct_before_costs": round(breakeven, 2),
                        "gap_vs_breakeven_pct": round(achieved - breakeven, 2),
                        "avg_net_r": round(float(o.net_r[res].mean()), 4),
                        "median_risk_points": round(
                            float(np.median(o.risk[res])), 4
                        ),
                        "median_cost_points": round(
                            float(np.median(o.cost_points[res])), 4
                        ),
                        "cost_as_fraction_of_risk": round(
                            float(
                                np.median(o.cost_points[res])
                                / max(float(np.median(o.risk[res])), 1e-9)
                            ),
                            4,
                        ),
                        "gap_resolved_pct": round(
                            float(
                                np.isin(
                                    o.exit_reason[res],
                                    [EXIT_STOP_GAP, EXIT_TARGET_GAP],
                                ).mean() * 100.0
                            ),
                            2,
                        ),
                    })
    return rows


def walk_forward(
    p: pool.Pool,
    masks: dict,
    rule: dict,
    folds: int = WALK_FORWARD_FOLDS,
) -> dict:
    """Equal-length chronological folds across the whole candidate span."""
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


def stress_grid(vehicle: str, *, quick: bool = False) -> list[dict]:
    """Execution variants a leader must survive, in this vehicle's own units."""
    grid: list[dict] = [{"spread_multiplier": m} for m in SPREAD_MULTIPLIERS]
    if vehicle == EQUITY_DELIVERY:
        grid += [{"slippage_pct": s} for s in SLIPPAGE_PCT]
    else:
        grid += [{"slippage_points": s} for s in SLIPPAGE_POINTS]
    grid += [{"gap_fill_at_open": False}]
    if not quick:
        grid += [{"entry_delay_bars": d} for d in ENTRY_DELAY_BARS]
        grid += [{"exit_delay_bars": d} for d in EXIT_DELAY_BARS]
    return grid


def _variant_label(kw: dict) -> str:
    parts: list[str] = []
    for k, v in kw.items():
        if k == "gap_fill_at_open":
            parts.append(
                "gap_fill_at_open=False (the flattering assumption: stop fills at "
                "the stop price even on a gap)"
            )
        elif k == "entry_delay_bars":
            parts.append(f"entry_delay_bars={v} ({int(v) - 1} session(s) late)")
        elif k == "exit_delay_bars":
            parts.append(f"exit_delay_bars={v} ({v} session(s) late out)")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def cost_sensitivity(rule: dict, *, quick: bool = False) -> list[dict]:
    """Re-resolve the same rule under worse spread, slippage, gaps and timing."""
    out: list[dict] = []
    for kw in stress_grid(rule["vehicle"], quick=quick):
        p = pool.build(
            rule["instrument"],
            stop_atr=rule["stop_band_atr"],
            horizon_days=rule["horizon_trading_days"],
            t1_r=float(rule.get("t1_r") or outcomes.T1_R),
            **kw,
        )
        if p is None:
            continue
        masks = conditions.masks(p.feat, p.side)
        m = _cohort_mask(p, masks, rule) & p.out.resolved
        stats = metrics.summarise(
            p.out, m, discover.base_rate(p.out, p.out.resolved)
        )
        row = {
            "variant": _variant_label(kw),
            "trades": stats.get("trades", 0),
            "t1_before_sl_pct": stats.get("t1_before_sl_pct"),
            "avg_net_r": stats.get("avg_net_r"),
            "profit_factor": stats.get("profit_factor"),
            "survives": bool((stats.get("avg_net_r") or -1) > 0),
        }
        # This variant is a diagnostic, not a bar to clear: it is deliberately
        # EASIER than the baseline, so requiring survival of it would be
        # meaningless. The number that matters is how much it improves the rule.
        if "gap_fill_at_open" in kw:
            row["diagnostic_only"] = True
            row["survives"] = True
            row["note"] = (
                "gain over the honest baseline is what overnight gap risk costs "
                "this rule"
            )
        out.append(row)
    return out


def parameter_perturbation(p: pool.Pool, masks: dict, rule: dict) -> list[dict]:
    """Swap each pullback band for its neighbours; a rule should not collapse."""
    rows: list[dict] = []
    for name in rule["conditions"]:
        for neighbour in PULLBACK_NEIGHBOURS.get(name, ()):
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
    """Regime split: the 200-day side, volatility, and trend agreement."""
    o = p.out
    cohort = _cohort_mask(p, masks, rule) & o.resolved
    br = discover.base_rate(o, o.resolved)
    splits = {
        "with_200d_regime": masks["above_200d_regime"],
        "against_200d_regime": ~masks["above_200d_regime"],
        "volatility_expanding": masks["volatility_expanding"],
        "volatility_compressed": masks["volatility_compressed"],
        "trend_aligned": masks["trend_both_agree"],
        "trend_against": masks["trend_long_opposes"],
        "breakout_20d": masks["breakout_20d"],
    }
    out: dict[str, dict] = {}
    for name, m in splits.items():
        stats = metrics.summarise(o, cohort & m, br)
        out[name] = {
            "trades": stats.get("trades", 0),
            "t1_before_sl_pct": stats.get("t1_before_sl_pct"),
            "avg_net_r": stats.get("avg_net_r"),
        }
    return out


def exit_breakdown(p: pool.Pool, masks: dict, rule: dict) -> dict:
    """How this rule's trades actually ended, gaps counted separately."""
    o = p.out
    m = _cohort_mask(p, masks, rule) & o.resolved
    n = int(m.sum())
    if n == 0:
        return {"trades": 0}
    out: dict[str, object] = {"trades": n}
    for code, label in EXIT_LABELS.items():
        share = float((o.exit_reason[m] == code).mean() * 100.0)
        out[label.lower() + "_pct"] = round(share, 2)
    out["median_trading_days_held"] = float(np.median(o.bars_held[m]))
    out["median_calendar_days_held"] = float(np.median(o.calendar_days[m]))
    out["rolls_charged"] = int(o.rolls[m].sum())
    return out


def baselines(p: pool.Pool, masks: dict) -> dict:
    """The screenshot setups as daily rules, plus a no-edge control.

    Reconstructions in this study's vocabulary with identical geometry, costs and
    windows — not anyone's published results, and labelled as such. The user's own
    screenshots described an EMA 9/21 + RSI momentum entry, a 20/50 EMA crossover
    and a breakout of the prior range; these are those shapes on a daily bar, so
    "have we tested that setup" has a swing-timeframe answer too.
    """
    o = p.out
    br = discover.base_rate(o, o.resolved)
    defs = {
        "EMA_STACK_PLUS_RSI_RECONSTRUCTION": (
            "ema_stack_agrees", "rsi_agrees_50", "strong_body_agrees",
        ),
        "EMA_20_50_TREND_RECONSTRUCTION": (
            "ema_stack_agrees", "trend_long_agrees",
        ),
        "BREAKOUT_20D_RECONSTRUCTION": ("breakout_20d", "volume_expansion_1p5x"),
        "BREAKOUT_50D_RECONSTRUCTION": ("breakout_50d",),
        "PULLBACK_IN_TREND_RECONSTRUCTION": (
            "above_200d_regime", "pullback_20_40pct", "trend_long_agrees",
        ),
        "GAP_CONTINUATION_RECONSTRUCTION": (
            "gap_open_1pct_agrees", "trend_short_agrees",
        ),
    }
    out: dict[str, dict] = {}
    for label, names in defs.items():
        for side in sides_for(p.vehicle):
            m = p.side == side
            for n in names:
                m = m & masks[n]
            stats = metrics.summarise(o, m & o.resolved, br)
            key = f"{label}_{'LONG' if side == LONG else 'SHORT'}"
            out[key] = {
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
    """Selectivity: how often the rule fires, and how often it refuses."""
    o = p.out
    cohort = _cohort_mask(p, masks, rule) & o.resolved
    sessions = max(1, int(p.sessions))
    n = int(cohort.sum())
    resolved = max(1, int(o.resolved.sum()))
    return {
        "trades": n,
        "sessions": sessions,
        "trades_per_session": round(n / sessions, 4),
        "trades_per_month": round(21.0 * n / sessions, 2),
        "trades_per_year": round(250.0 * n / sessions, 2),
        "pct_of_candidates": round(100.0 * n / resolved, 3),
        "no_trade_pct": round(100.0 - 100.0 * n / resolved, 3),
    }


def evaluate_rule(
    p: pool.Pool,
    masks: dict,
    rule: dict,
    s: discover.Search,
) -> dict:
    """Fill in validation, holdout, walk-forward, robustness and the benchmark.

    The holdout window is evaluated here and nowhere else, and no branch of this
    function feeds a holdout number back into a selection decision.
    """
    rule = dict(rule)
    rule["t1_r"] = p.t1_r
    rule["validation"] = s.evaluate(
        tuple(rule["conditions"]), rule["side_int"], s.val
    )
    rule["holdout"] = s.evaluate(
        tuple(rule["conditions"]), rule["side_int"], s.hold
    )
    rule["walk_forward"] = walk_forward(p, masks, rule)
    rule["parameter_perturbation"] = parameter_perturbation(p, masks, rule)
    rule["regimes"] = regimes(p, masks, rule)
    rule["exit_breakdown"] = exit_breakdown(p, masks, rule)
    rule["selectivity"] = trades_per_period(p, masks, rule)

    hold_span = s.win[discover.HOLDOUT]
    bh = baseline.buy_and_hold(
        p.series, p.vehicle, lo_ts=hold_span[0], hi_ts=hold_span[1]
    )
    cohort = _cohort_mask(p, masks, rule)
    rule["buy_and_hold_holdout"] = bh
    rule["benchmark_holdout"] = baseline.compare(
        p.out, cohort & s.hold, rule["side_int"], bh
    )
    rule["status"] = RESEARCH_ONLY
    return rule


__all__ = [
    "geometry_sweep", "walk_forward", "stress_grid", "cost_sensitivity",
    "parameter_perturbation", "regimes", "exit_breakdown", "baselines",
    "trades_per_period", "evaluate_rule", "WALK_FORWARD_FOLDS",
]
