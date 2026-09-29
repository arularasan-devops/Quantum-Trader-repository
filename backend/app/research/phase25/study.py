"""Phase 25 §6 — run, confirm, stress, and refuse.

Order is enforced by the code path: the geometry sweep first, because it shows
whether buying an option at the ask and selling it at the bid inside this window
had any room at all before any rule is blamed or credited; then discovery on
development sessions; then validation and the holdout, each read once; then a
robustness grid that re-resolves *the same candidates* under worse execution.

The ceiling on any verdict here is a research lead. The window is weeks, so no
amount of internal agreement can make a survivor a validated edge, and the code
refuses to say otherwise.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import metrics
from app.research.phase25 import (
    REQUIRES_MORE_DATA,
    books,
    conditions,
    discover,
    outcomes,
    pool,
    underlying,
)

# Worsenings only. None of these is a free improvement.
COST_MULTIPLIERS = (1.0, 1.25, 1.5)
SLIPPAGE_MULTIPLIERS = (1.0, 2.0)
EXIT_DELAY_STEPS = (0, 1)

GEOMETRY_TARGETS = (1.0, 1.5, 2.5)

TIME_BUCKETS = (
    ("open_0930_1030", 570, 630),
    ("morning_1030_1200", 630, 720),
    ("midday_1200_1400", 720, 840),
    ("late_1400_close", 840, 1_440),
)


def geometry_sweep(p_by_band: dict[float, pool.Pool]) -> list[dict]:
    """The pool's own economics per stop band, with no conditions applied.

    The break-even column is the T1 rate a 1.5R target needs *before* costs; the
    achieved column is what the captured books actually produced after paying the
    spread in the prices plus brokerage, statutory charges and slippage.
    """
    rows: list[dict] = []
    for band, p in sorted(p_by_band.items()):
        o = p.out
        res = o.resolved
        n = int(res.sum())
        if n == 0:
            continue
        rows.append({
            "instrument": p.instrument,
            "stop_band_pct_of_premium": round(100.0 * band, 1),
            "t1_r": outcomes.T1_R,
            "trades": n,
            "t1_before_sl_pct": round(100.0 * float(o.t1_before_sl[res].mean()), 2),
            "breakeven_t1_pct_before_costs": round(
                100.0 / (1.0 + outcomes.T1_R), 2
            ),
            "avg_net_r": round(float(o.net_r[res].mean()), 4),
            "avg_net_rupees": round(float(o.net_rupees[res].mean()), 2),
            "cost_as_fraction_of_risk": round(
                float(np.median(o.cost_points[res]) / np.median(o.risk[res])), 3
            ),
            "median_measured_spread_pct": round(
                float(np.nanmedian(o.spread_pct[res])), 3
            ),
            "spread_as_fraction_of_risk": round(
                float(
                    np.nanmedian((p.entry_ask - p.entry_bid)[res])
                    / np.median(o.risk[res])
                ), 3
            ),
        })
    return rows


def side_breakdown(p: pool.Pool) -> dict:
    """CE versus PE on identical geometry, costs and window."""
    o = p.out
    br = discover.base_rate(o, o.resolved)
    out: dict[str, dict] = {}
    for otype in ("CE", "PE"):
        m = (p.option_type == otype) & o.resolved
        stats = metrics.summarise(o, m, br)
        stats["avg_net_rupees"] = (
            round(float(o.net_rupees[m].mean()), 2) if m.any() else None
        )
        stats["total_net_rupees"] = (
            round(float(o.net_rupees[m].sum()), 2) if m.any() else None
        )
        stats["median_hurdle_pct"] = (
            round(float(np.nanmedian(p.feat["hurdle_pct"][m])), 3) if m.any() else None
        )
        out[otype] = stats
    return out


def time_of_day(p: pool.Pool) -> dict:
    """Where in the session the captured books actually paid or cost money."""
    o = p.out
    br = discover.base_rate(o, o.resolved)
    minute = p.feat["minute_of_day"]
    out: dict[str, dict] = {}
    for name, lo, hi in TIME_BUCKETS:
        m = (minute >= lo) & (minute < hi) & o.resolved
        stats = metrics.summarise(o, m, br)
        out[name] = {
            "trades": stats.get("trades", 0),
            "t1_before_sl_pct": stats.get("t1_before_sl_pct"),
            "avg_net_r": stats.get("avg_net_r"),
            "avg_net_rupees": (
                round(float(o.net_rupees[m].mean()), 2) if m.any() else None
            ),
        }
    return out


def economics_bands(p: pool.Pool) -> list[dict]:
    """Net result by measured break-even hurdle — the one lead we already have.

    The journal study found ≤3% hurdle legs profitable and >5% legs deeply
    negative. This is the same question asked of stored books instead of a
    reconstructed journal, on entry-at-ask and exit-at-bid.
    """
    o = p.out
    res = o.resolved
    hurdle = p.feat["hurdle_pct"]
    bands = ((0.0, 3.0), (3.0, 5.0), (5.0, 10.0), (10.0, float("inf")))
    rows: list[dict] = []
    for lo, hi in bands:
        m = res & np.isfinite(hurdle) & (hurdle >= lo) & (hurdle < hi)
        n = int(m.sum())
        label = f"hurdle_{lo:g}_to_{hi:g}pct" if np.isfinite(hi) else f"hurdle_gt_{lo:g}pct"
        if n == 0:
            rows.append({"band": label, "trades": 0, "status": REQUIRES_MORE_DATA})
            continue
        rows.append({
            "band": label,
            "trades": n,
            "t1_before_sl_pct": round(100.0 * float(o.t1_before_sl[m].mean()), 2),
            "avg_net_r": round(float(o.net_r[m].mean()), 4),
            "avg_net_rupees": round(float(o.net_rupees[m].mean()), 2),
            "total_net_rupees": round(float(o.net_rupees[m].sum()), 2),
            "profit_factor": metrics.profit_factor(o.net_r[m]),
            "status": "OK" if n >= metrics.MIN_TRADES else "BELOW_MIN_SAMPLE",
        })
    return rows


def _cohort(p: pool.Pool, masks: dict, rule: dict) -> np.ndarray:
    m = p.option_type == rule["option_type"]
    for name in rule["conditions"]:
        m = m & masks[name]
    return m


def robustness(rule: dict, chain: books.Chain, series) -> list[dict]:
    """Re-resolve the same rule under worse costs, slippage and exit delay."""
    band = float(rule["stop_band_pct_of_premium"]) / 100.0
    grid: list[dict] = [{"cost_multiplier": m} for m in COST_MULTIPLIERS[1:]]
    grid += [
        {"slip_pct": outcomes.slippage_pct() * 100.0 * m}
        for m in SLIPPAGE_MULTIPLIERS[1:]
    ]
    grid += [{"exit_delay_steps": d} for d in EXIT_DELAY_STEPS[1:]]
    rows: list[dict] = []
    for kw in grid:
        p = pool.build(
            rule["instrument"], chain=chain, series=series, stop_pct=band, **kw
        )
        if p is None:
            continue
        masks = conditions.masks(p.feat, p.side)
        m = _cohort(p, masks, rule) & p.out.resolved
        stats = metrics.summarise(
            p.out, m, discover.base_rate(p.out, p.out.resolved)
        )
        rows.append({
            "variant": ", ".join(f"{k}={v}" for k, v in kw.items()),
            "trades": stats.get("trades", 0),
            "t1_before_sl_pct": stats.get("t1_before_sl_pct"),
            "avg_net_r": stats.get("avg_net_r"),
            "profit_factor": stats.get("profit_factor"),
            "survives": bool((stats.get("avg_net_r") or -1) > 0),
        })
    return rows


def selectivity(p: pool.Pool, masks: dict, rule: dict) -> dict:
    """How often the rule fires, and how often the answer is NO TRADE."""
    o = p.out
    cohort = _cohort(p, masks, rule) & o.resolved
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
    """The whole study for one instrument, or why it could not be studied.

    ``db_path`` is injectable so the study can be exercised against a
    purpose-built store instead of whatever the live engine happens to have
    captured on the machine.
    """
    inst = (instrument or "").upper()
    chain = books.load_chain(inst, db_path=db_path)
    elig = books.eligibility(chain)
    if not elig["eligible"]:
        return {"instrument": inst, "coverage": elig, "status": REQUIRES_MORE_DATA,
                "rules": [], "pool": None}
    series = underlying.load_series(inst, db_path=db_path)
    if series is None or len(series) <= pool.WARMUP_BARS:
        elig = dict(elig)
        elig["reasons"] = list(elig["reasons"]) + [
            "no captured 1-minute candles long enough to form market context, so "
            "the option books cannot be given a causal entry reason"
        ]
        elig["status"] = REQUIRES_MORE_DATA
        return {"instrument": inst, "coverage": elig, "status": REQUIRES_MORE_DATA,
                "rules": [], "pool": None}

    bands = outcomes.STOP_BANDS[1:2] if quick else outcomes.STOP_BANDS
    p_by_band: dict[float, pool.Pool] = {}
    for band in bands:
        p = pool.build(inst, chain=chain, series=series, stop_pct=band)
        if p is not None:
            p_by_band[band] = p
    if not p_by_band:
        elig = dict(elig)
        elig["reasons"] = list(elig["reasons"]) + [
            "no snapshot produced a resolvable candidate: the same contract was "
            "not quoted again inside its own session"
        ]
        return {"instrument": inst, "coverage": elig, "status": REQUIRES_MORE_DATA,
                "rules": [], "pool": None}

    base_band = outcomes.STOP_PCT if outcomes.STOP_PCT in p_by_band else (
        sorted(p_by_band)[0]
    )
    base = p_by_band[base_band]

    rules: list[dict] = []
    near: list[dict] = []
    tests = 0
    for band, p in p_by_band.items():
        s = discover.Search(p, band)
        found = s.run()
        tests += s.tests
        near.extend(s.near_misses)
        masks = s.masks
        for row in found:
            row = dict(row)
            row["dev_gate_passed"] = True
            row = s.confirm(row)
            row["selectivity"] = selectivity(p, masks, row)
            row["robustness"] = [] if quick else robustness(row, chain, series)
            rules.append(row)

    return {
        "instrument": inst,
        "coverage": {**elig, **(base.coverage or {})},
        "pool": pool.summary(base),
        "stop_band_used_for_pool_stats": round(100.0 * base_band, 1),
        "geometry_sweep": geometry_sweep(p_by_band),
        "sides": side_breakdown(base),
        "time_of_day": time_of_day(base),
        "hurdle_bands": economics_bands(base),
        "rules": rules,
        "near_misses": near[:discover.NEAR_MISS_KEEP],
        "hypotheses_evaluated": tests,
        "status": "STUDIED",
    }
