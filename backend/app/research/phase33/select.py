"""Phase 33 §16/§17/§18 — choosing a holding window, and trying to break it.

The whole point of the section is that the *best historical hold is not a
validated hold*. So the holding period is chosen on the development window only,
and everything after that is an attempt to falsify it:

* it must still be positive on validation;
* it must still be positive on the untouched holdout, which is read once;
* it must be positive in a majority of walk-forward folds;
* it must survive 1.5x and 2x costs;
* it must survive removing the top 1% and top 5% of winners, so one exceptional
  stretch cannot carry it;
* it must clear a Benjamini-Hochberg correction over **every** horizon, family,
  side and instrument that was looked at — the denominator §17 asks for.

Failing any one of those is not a smaller success. It is ``RESEARCH_LEAD`` at
best, and the report says which test failed.
"""
from __future__ import annotations

import math

import numpy as np

from app.research.phase24.discover import benjamini_hochberg
from app.research.phase33 import (
    DEV_FRACTION,
    FUT_COST_STRESS,
    HOLD_GRID,
    MIN_ROWS_PER_WINDOW,
    MIN_SESSIONS_PER_WINDOW,
    NO_HOLD_WINDOW,
    OUTLIER_TRIMS_PCT,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD_HOLD,
    SESSION_CLOSE,
    VAL_FRACTION,
    VALIDATED_HOLD,
    WALK_FORWARD_FOLDS,
)
from app.research.phase33.hold import series_at
from app.research.phase33.path import Path

DEV, VAL, HOLDOUT = "DEVELOPMENT", "VALIDATION", "HOLDOUT"
HORIZONS: tuple[int | str, ...] = tuple(HOLD_GRID) + (SESSION_CLOSE,)


def windows(p: Path, eligible: np.ndarray) -> dict[str, np.ndarray]:
    """Chronological 60/20/20 by session, never by row and never at random.

    Splitting by session rather than by row keeps a single session's rows — which
    overlap heavily, being one-minute apart — inside one window.
    """
    sessions = np.unique(p.session[eligible])
    n = sessions.size
    dev_end = int(n * DEV_FRACTION)
    val_end = int(n * (DEV_FRACTION + VAL_FRACTION))
    parts = {
        DEV: sessions[:dev_end],
        VAL: sessions[dev_end:val_end],
        HOLDOUT: sessions[val_end:],
    }
    return {
        name: eligible & np.isin(p.session, ids) for name, ids in parts.items()
    }


def folds(p: Path, eligible: np.ndarray, count: int = WALK_FORWARD_FOLDS) -> list[np.ndarray]:
    """Equal-session chronological folds for the walk-forward check."""
    sessions = np.unique(p.session[eligible])
    if sessions.size < count:
        return []
    chunks = np.array_split(sessions, count)
    return [eligible & np.isin(p.session, ids) for ids in chunks]


def _net(p: Path, mask: np.ndarray, cost: np.ndarray, horizon: int | str,
         cost_mult: float = 1.0, trim_pct: float = 0.0) -> np.ndarray:
    """Net percentage returns of one cohort at one horizon.

    ``trim_pct`` removes the best rows, not the largest by absolute size: §17 asks
    whether the result was created by a handful of exceptional *winners*.
    """
    a = series_at(p, horizon, mask)
    c = cost[mask]
    ok = np.isfinite(a["ret"]) & np.isfinite(c)
    net = a["ret"][ok].astype(np.float64) - cost_mult * c[ok].astype(np.float64)
    if trim_pct > 0 and net.size:
        keep = net.size - int(math.ceil(net.size * trim_pct / 100.0))
        if keep <= 0:
            return net[:0]
        cut = np.sort(net)[keep - 1]
        # Ties at the cut are kept, so the trim never removes more than asked.
        net = net[net <= cut]
    return net


def _t_p_value(net: np.ndarray) -> float:
    """One-sided p-value for "mean net > 0", normal approximation.

    The sample sizes here are in the thousands, where the normal approximation and
    a t-distribution agree to more decimals than this study reports.
    """
    if net.size < 30:
        return 1.0
    sd = float(np.std(net, ddof=1))
    if sd <= 0:
        return 0.0 if float(np.mean(net)) > 0 else 1.0
    z = float(np.mean(net)) / (sd / math.sqrt(net.size))
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _stat(net: np.ndarray) -> dict:
    if net.size == 0:
        return {"n": 0, "net_expectancy_pct": None}
    return {
        "n": int(net.size),
        "net_expectancy_pct": round(float(np.mean(net)), 6),
        "net_win_rate": round(float(np.mean(net > 0)), 6),
    }


def evaluate(
    p: Path, mask: np.ndarray, cost: np.ndarray, family: str
) -> dict:
    """Select a holding window on development data, then try to break it."""
    win = windows(p, mask)
    sizes = {k: int(v.sum()) for k, v in win.items()}
    sess = {k: int(np.unique(p.session[v]).size) for k, v in win.items()}
    out: dict = {
        "family": family,
        "instrument": p.instrument,
        "side": "LONG" if p.side > 0 else "SHORT",
        "window_rows": sizes,
        "window_sessions": sess,
        "horizons_tested": len(HORIZONS),
        "development": [],
    }
    too_small = [
        k for k in (DEV, VAL, HOLDOUT)
        if sizes[k] < MIN_ROWS_PER_WINDOW or sess[k] < MIN_SESSIONS_PER_WINDOW
    ]
    if too_small:
        out["status"] = REQUIRES_MORE_DATA
        out["reason"] = (
            f"{', '.join(too_small)} below the floor of {MIN_ROWS_PER_WINDOW} rows "
            f"and {MIN_SESSIONS_PER_WINDOW} sessions, so no holding window is "
            "selected for this cohort"
        )
        return out

    best: tuple[float, int | str] | None = None
    for h in HORIZONS:
        st = _stat(_net(p, win[DEV], cost, h))
        st["horizon"] = h
        out["development"].append(st)
        exp = st.get("net_expectancy_pct")
        if exp is not None and (best is None or exp > best[0]):
            best = (exp, h)

    if best is None or best[0] <= 0:
        out["status"] = NO_HOLD_WINDOW
        out["reason"] = (
            "no holding horizon had positive net expectancy on development data, "
            "so nothing was carried to validation or the holdout"
        )
        out["best_development_net_pct"] = None if best is None else best[0]
        out["selected_horizon"] = None
        return out

    h = best[1]
    out["selected_horizon"] = h
    out["selection_basis"] = "BEST_DEVELOPMENT_NET_EXPECTANCY"
    out["validation"] = _stat(_net(p, win[VAL], cost, h))
    hold_net = _net(p, win[HOLDOUT], cost, h)
    out["holdout"] = _stat(hold_net)
    out["holdout_p_value"] = round(_t_p_value(hold_net), 8)

    fold_rows = []
    for i, fm in enumerate(folds(p, mask)):
        st = _stat(_net(p, fm, cost, h))
        st["fold"] = i + 1
        fold_rows.append(st)
    out["walk_forward"] = fold_rows
    positive = sum(
        1 for r in fold_rows
        if (r.get("net_expectancy_pct") or 0.0) > 0
    )
    out["walk_forward_positive"] = positive
    out["walk_forward_majority"] = bool(fold_rows) and positive * 2 > len(fold_rows)

    out["cost_stress"] = [
        {"cost_multiple": m, **_stat(_net(p, win[HOLDOUT], cost, h, cost_mult=m))}
        for m in FUT_COST_STRESS
    ]
    out["outlier_trims"] = [
        {"top_winners_removed_pct": t,
         **_stat(_net(p, win[HOLDOUT], cost, h, trim_pct=t))}
        for t in OUTLIER_TRIMS_PCT
    ]

    failures: list[str] = []
    if (out["validation"].get("net_expectancy_pct") or 0.0) <= 0:
        failures.append("validation net expectancy was not positive")
    if (out["holdout"].get("net_expectancy_pct") or 0.0) <= 0:
        failures.append("untouched holdout net expectancy was not positive")
    if not out["walk_forward_majority"]:
        failures.append(
            f"only {positive} of {len(fold_rows)} walk-forward folds were positive"
        )
    for row in out["cost_stress"]:
        if (row.get("net_expectancy_pct") or 0.0) <= 0:
            failures.append(
                f"turns negative at {row['cost_multiple']}x costs"
            )
            break
    for row in out["outlier_trims"]:
        if (row.get("net_expectancy_pct") or 0.0) <= 0:
            failures.append(
                "depends on outliers: negative with the top "
                f"{row['top_winners_removed_pct']}% of winners removed"
            )
            break
    out["failures"] = failures
    out["status"] = RESEARCH_LEAD_HOLD if not failures else NO_HOLD_WINDOW
    out["reason"] = (
        "positive on development, validation and the untouched holdout, and "
        "survived walk-forward, cost stress and outlier removal; still awaiting "
        "the multiple-testing correction"
        if not failures else "; ".join(failures)
    )
    return out


def apply_correction(rows: list[dict], alpha: float = 0.05) -> dict:
    """§17 — the correction over every hypothesis, and the final verdict.

    The denominator is every horizon of every family, side and instrument that
    was evaluated, not only the ones that made it to the holdout: counting a
    hypothesis only when it looks promising is exactly the search this section
    forbids.
    """
    hypotheses = sum(int(r.get("horizons_tested") or 0) for r in rows)
    leads = [r for r in rows if r.get("status") == RESEARCH_LEAD_HOLD]
    p_values = [float(r.get("holdout_p_value") or 1.0) for r in leads]
    # BH is applied over the full denominator by padding the untested hypotheses
    # with p = 1.0, so a lead cannot be helped by the fact that other cohorts were
    # never carried this far.
    padded = p_values + [1.0] * max(hypotheses - len(p_values), 0)
    flags = benjamini_hochberg(padded, alpha=alpha) if padded else []
    survivors: list[dict] = []
    for r, keep in zip(leads, flags[:len(leads)]):
        r["multiple_testing_survived"] = bool(keep)
        if keep:
            r["status"] = VALIDATED_HOLD
            survivors.append(r)
        else:
            r["status"] = RESEARCH_LEAD_HOLD
            r["reason"] = (
                "survived every economic test but not the Benjamini-Hochberg "
                f"correction over {hypotheses} counted hypotheses"
            )
    if survivors:
        verdict = VALIDATED_HOLD
    elif leads:
        verdict = RESEARCH_LEAD_HOLD
    elif any(r.get("status") == REQUIRES_MORE_DATA for r in rows) and not any(
        r.get("status") == NO_HOLD_WINDOW for r in rows
    ):
        verdict = REQUIRES_MORE_DATA
    else:
        verdict = NO_HOLD_WINDOW
    return {
        "hypotheses_counted": hypotheses,
        "alpha": alpha,
        "validated": len(survivors),
        "research_leads": len(leads) - len(survivors),
        "verdict": verdict,
    }
