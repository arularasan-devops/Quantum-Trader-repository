"""Phase 29 §8 — run, confirm, stress, and refuse.

The order is enforced by the code path, and the first step matters most:

1. **the unconditional sweep runs before any discovery.** Every structure and
   geometry is priced with no conditions at all, so the report shows whether
   *selling* premium in this window had room before any rule is credited or
   blamed. If the sweep is uniformly negative, no cohort that follows is a
   finding — it is a subset of a losing pool;
2. discovery on development sessions only;
3. validation and holdout, each read once, to confirm or refuse;
4. a robustness grid that re-resolves *the same* candidates under worse costs,
   worse slippage and a delayed exit.

The ceiling on any verdict is a research lead. The window is weeks.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import metrics
from app.research.phase29 import (
    REQUIRES_MORE_DATA,
    conditions,
    discover,
    economics,
    legs,
    outcomes,
    pool,
)
from app.research.phase25 import books, underlying

# Worsenings only. None of these is a free improvement.
COST_MULTIPLIERS = (1.0, 1.25, 1.5)
SLIPPAGE_MULTIPLIERS = (1.0, 2.0)
EXIT_DELAY_STEPS = (0, 1)

TIME_BUCKETS = (
    ("open_0930_1030", 570, 630),
    ("morning_1030_1200", 630, 720),
    ("midday_1200_1400", 720, 840),
    ("late_1400_close", 840, 1_440),
)


def geometries(quick: bool = False) -> list[tuple[str, int, int]]:
    """Every (structure, short_steps, width_steps) the study prices."""
    if quick:
        return [(legs.BULL_PUT, 1, 1)]
    return [
        (structure, short, width)
        for structure in legs.STRUCTURES
        for short in legs.SHORT_STEPS
        for width in legs.WIDTH_STEPS
    ]


def stop_bands(quick: bool = False) -> tuple[float, ...]:
    return (outcomes.STOP_CREDIT_MULT,) if quick else outcomes.STOP_CREDIT_MULTIPLES


def holds(quick: bool = False) -> tuple[str, ...]:
    """The frozen hold periods. Both are intraday; nothing is held overnight."""
    if quick:
        return (outcomes.HOLD_1H,)
    return (outcomes.HOLD_1H, outcomes.HOLD_SESSION)


def unconditional(p: pool.Pool) -> dict:
    """One geometry's own economics, with no condition applied.

    ``breakeven_target_pct_before_costs`` is the hit rate the exit geometry needs
    before a rupee of friction: keeping ``take`` of the credit while risking
    ``stop - 1`` credits needs ``risk / (risk + reward)`` of the attempts. It is
    printed next to what the books actually produced, because the gap between
    those two numbers is the whole result.
    """
    o = p.out
    res = o.resolved
    n = int(res.sum())
    reward = outcomes.TAKE_1
    risk = max(p.stop_credit_mult - 1.0, 1e-9)
    row = {
        "structure": p.structure,
        "hold": p.hold,
        "underlying_view": legs.VIEW[p.structure],
        "short_steps_otm": p.short_steps,
        "width_steps": p.width_steps,
        "width_points": round(p.width_points, 2),
        "stop_credit_multiple": p.stop_credit_mult,
        "take_profit_fraction_of_credit": reward,
        "breakeven_target_pct_before_costs": round(
            100.0 * risk / (risk + reward), 2
        ),
        "trades": n,
    }
    if n == 0:
        row["status"] = REQUIRES_MORE_DATA
        return row
    row.update({
        "target_before_stop_pct": round(100.0 * float(o.t1_before_sl[res].mean()), 2),
        "avg_net_r": round(float(o.net_r[res].mean()), 4),
        "avg_return_on_defined_risk_pct": round(
            100.0 * float(o.ror_defined_risk[res].mean()), 3
        ),
        "avg_net_rupees": round(float(o.net_rupees[res].mean()), 2),
        "total_net_rupees": round(float(o.net_rupees[res].sum()), 2),
        "profit_factor": metrics.profit_factor(o.net_r[res]),
        "median_credit_points": round(float(np.median(o.credit[res])), 2),
        "median_credit_pct_of_width": round(
            float(np.median(100.0 * o.credit[res] / max(p.width_points, 1e-9))), 2
        ),
        "median_defined_loss_points": round(float(np.median(o.max_loss[res])), 2),
        "median_friction_pct_of_credit": round(
            float(np.nanmedian(o.hurdle_pct[res])), 2
        ),
        "friction_as_fraction_of_defined_risk": round(
            float(np.median(o.cost_points[res]) / max(np.median(o.max_loss[res]), 1e-9)),
            4,
        ),
        "median_structure_spread_points": round(
            float(np.nanmedian(o.spread_points[res])), 3
        ),
        "timeout_pct": round(
            100.0 * float((o.outcome[res] == outcomes.TIMEOUT).mean()), 2
        ),
        "status": "OK" if n >= metrics.MIN_TRADES else "BELOW_MIN_SAMPLE",
    })
    return row


def time_of_day(p: pool.Pool) -> dict:
    """Where in the session selling this structure paid or cost money."""
    o = p.out
    br = discover.base_rate(o, o.resolved)
    minute = p.feat["minute_of_day"]
    out: dict[str, dict] = {}
    for name, lo, hi in TIME_BUCKETS:
        m = (minute >= lo) & (minute < hi) & o.resolved
        stats = metrics.summarise(o, m, br)
        out[name] = {
            "trades": stats.get("trades", 0),
            "target_before_stop_pct": stats.get("t1_before_sl_pct"),
            "avg_net_r": stats.get("avg_net_r"),
            "avg_net_rupees": (
                round(float(o.net_rupees[m].mean()), 2) if m.any() else None
            ),
        }
    return out


def credit_bands(p: pool.Pool) -> list[dict]:
    """Net result by how much credit the structure collected against its width.

    The seller's version of the hurdle band study: a structure paid 10% of its
    width is risking nine units to make one, and this is where that shows up.
    """
    o = p.out
    res = o.resolved
    ratio = p.feat["credit_pct_of_width"]
    rows: list[dict] = []
    for lo, hi in ((0.0, 15.0), (15.0, 25.0), (25.0, 40.0), (40.0, 1e9)):
        m = res & np.isfinite(ratio) & (ratio >= lo) & (ratio < hi)
        n = int(m.sum())
        label = (
            f"credit_{lo:g}_to_{hi:g}pct_of_width" if hi < 1e9
            else f"credit_gt_{lo:g}pct_of_width"
        )
        if n == 0:
            rows.append({"band": label, "trades": 0, "status": REQUIRES_MORE_DATA})
            continue
        rows.append({
            "band": label,
            "trades": n,
            "target_before_stop_pct": round(
                100.0 * float(o.t1_before_sl[m].mean()), 2
            ),
            "avg_net_r": round(float(o.net_r[m].mean()), 4),
            "avg_return_on_defined_risk_pct": round(
                100.0 * float(o.ror_defined_risk[m].mean()), 3
            ),
            "avg_net_rupees": round(float(o.net_rupees[m].mean()), 2),
            "profit_factor": metrics.profit_factor(o.net_r[m]),
            "median_friction_pct_of_credit": round(
                float(np.nanmedian(o.hurdle_pct[m])), 2
            ),
            "status": "OK" if n >= metrics.MIN_TRADES else "BELOW_MIN_SAMPLE",
        })
    return rows


def robustness(rule: dict, prep: pool.Prepared) -> list[dict]:
    """Re-resolve the same rule under worse costs, slippage and exit delay."""
    mult = float(rule["stop_credit_multiple"])
    grid: list[dict] = [{"cost_multiplier": m} for m in COST_MULTIPLIERS[1:]]
    grid += [
        {"slip_pct": economics.slippage_pct() * 100.0 * m}
        for m in SLIPPAGE_MULTIPLIERS[1:]
    ]
    grid += [{"exit_delay_steps": d} for d in EXIT_DELAY_STEPS[1:]]
    names = tuple(rule["conditions"])
    rows: list[dict] = []
    for kw in grid:
        p = pool.resolve_prepared(prep, stop_credit_mult=mult, **kw)
        masks = conditions.masks(p.feat, p.side, p.structure)
        m = np.ones(len(p), dtype=bool)
        for name in names:
            m = m & masks[name]
        m = m & p.out.resolved
        stats = metrics.summarise(
            p.out, m, discover.base_rate(p.out, p.out.resolved)
        )
        rows.append({
            "variant": ", ".join(f"{k}={v}" for k, v in kw.items()),
            "trades": stats.get("trades", 0),
            "target_before_stop_pct": stats.get("t1_before_sl_pct"),
            "avg_net_r": stats.get("avg_net_r"),
            "profit_factor": stats.get("profit_factor"),
            "survives": bool((stats.get("avg_net_r") or -1) > 0),
        })
    return rows


def selectivity(p: pool.Pool, masks: dict, rule: dict) -> dict:
    """How often the rule fires, and how often the answer is NO TRADE."""
    o = p.out
    m = np.ones(len(p), dtype=bool)
    for name in rule["conditions"]:
        m = m & masks[name]
    cohort = m & o.resolved
    sessions = int(np.unique(p.session[o.resolved]).size) or 1
    n = int(cohort.sum())
    total = max(1, int(o.resolved.sum()))
    return {
        "trades": n,
        "sessions": sessions,
        "trades_per_session": round(n / sessions, 3),
        "pct_of_candidates": round(100.0 * n / total, 3),
        "no_trade_pct": round(100.0 - 100.0 * n / total, 3),
    }


def run_instrument(instrument: str, *, quick: bool = False,
                   db_path: str | None = None) -> dict:
    """The whole credit-spread study for one instrument, or why it could not run.

    ``db_path`` is injectable so the study can be exercised against a
    purpose-built store instead of whatever the live engine happens to have
    captured on the machine.
    """
    inst = (instrument or "").upper()
    chain = books.load_chain(inst, db_path=db_path)
    elig = books.eligibility(chain)
    if not elig["eligible"]:
        return {"instrument": inst, "coverage": elig, "status": REQUIRES_MORE_DATA,
                "rules": [], "pools": [], "unconditional": []}
    series = underlying.load_series(inst, db_path=db_path)
    if series is None or len(series) <= pool.WARMUP_BARS:
        elig = dict(elig)
        elig["reasons"] = list(elig["reasons"]) + [
            "no captured 1-minute candles long enough to form market context, so "
            "the option books cannot be given a causal entry reason"
        ]
        elig["status"] = REQUIRES_MORE_DATA
        return {"instrument": inst, "coverage": elig, "status": REQUIRES_MORE_DATA,
                "rules": [], "pools": [], "unconditional": []}

    step = legs.strike_step(chain)
    prepared: list[pool.Prepared] = []
    for hold in holds(quick):
        for structure, short, width in geometries(quick):
            prep = pool.prepare(
                inst, structure=structure, short_steps=short, width_steps=width,
                chain=chain, series=series, hold=hold,
            )
            if prep is not None and len(prep) > 0:
                prepared.append(prep)

    if not prepared:
        elig = dict(elig)
        elig["reasons"] = list(elig["reasons"]) + [
            "no snapshot produced a defined-risk structure that could be opened "
            "and closed on paired quotes: the captured ladder is not wide enough "
            "to quote a protective leg alongside the short leg"
        ]
        elig["strike_step_measured"] = round(step, 4)
        return {"instrument": inst, "coverage": elig, "status": REQUIRES_MORE_DATA,
                "rules": [], "pools": [], "unconditional": []}

    sweep: list[dict] = []
    pool_rows: list[dict] = []
    rules: list[dict] = []
    near: list[dict] = []
    tests = 0
    baseline: pool.Pool | None = None

    for prep in prepared:
        for mult in stop_bands(quick):
            p = pool.resolve_prepared(prep, stop_credit_mult=mult)
            sweep.append(unconditional(p))
            pool_rows.append(pool.summary(p))
            if baseline is None:
                baseline = p
            s = discover.Search(p)
            found = s.run()
            tests += s.tests
            near.extend(s.near_misses)
            for row in found:
                row = dict(row)
                row["dev_gate_passed"] = True
                row["take_profit_fraction"] = outcomes.TAKE_1
                res = p.out.resolved
                row["median_friction_pct_of_credit"] = (
                    round(float(np.nanmedian(p.out.hurdle_pct[res])), 2)
                    if res.any() else None
                )
                row = s.confirm(row)
                row["selectivity"] = selectivity(p, s.masks, row)
                row["robustness"] = [] if quick else robustness(row, prep)
                rules.append(row)

    assert baseline is not None
    return {
        "instrument": inst,
        "coverage": {**elig, **(baseline.coverage or {})},
        "strike_step_measured": round(step, 4),
        "geometries_priced": len(prepared),
        "unconditional": sweep,
        "pools": pool_rows,
        "time_of_day": time_of_day(baseline),
        "credit_bands": credit_bands(baseline),
        "rules": rules,
        "near_misses": sorted(
            near, key=lambda r: r["development"]["avg_net_r"], reverse=True
        )[:discover.NEAR_MISS_KEEP],
        "hypotheses_evaluated": tests,
        "status": "STUDIED",
    }
