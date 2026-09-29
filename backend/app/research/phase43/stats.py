"""Phase 43 §2 — one metric set, used by every family.

Written once and shared so a single-instrument candidate and a pair candidate
are never compared through two different definitions of profit factor. Every
statistic here is computed from realised per-trade arrays; nothing in this module
knows what produced them.

Deliberately included because they are what kill most candidates: gross next to
net, so the reader can see how much of the result the charges ate; drawdown on
the chronological curve rather than on the sorted one; the same total with the
best single session removed; and MFE/MAE reported descriptively, never used to
select.
"""
from __future__ import annotations

import math

import numpy as np

EMPTY = "EMPTY"


def _f(x: float) -> float | None:
    return None if not math.isfinite(x) else round(float(x), 4)


def profit_factor(net: np.ndarray) -> float | None:
    """Gross win over gross loss. ``None`` when there is no loss to divide by,
    because ``inf`` printed in a ranking table reads as a strong result when it
    actually means "too few trades to have lost yet"."""
    wins = float(net[net > 0].sum())
    losses = float(-net[net < 0].sum())
    if losses <= 0:
        return None
    return round(wins / losses, 3)


def max_drawdown(net_chronological: np.ndarray) -> float:
    """Deepest peak-to-trough fall of the cumulative curve, in the input's units.

    The curve starts at zero rather than at the first trade, so a cohort that
    loses before it ever wins reports that loss as drawdown. Starting at the
    first trade hides exactly the sequence a reader cares about.
    """
    if net_chronological.size == 0:
        return 0.0
    eq = np.concatenate((np.zeros(1), np.cumsum(net_chronological)))
    peak = np.maximum.accumulate(eq)
    return round(float(np.max(peak - eq)), 2)


def session_concentration(net: np.ndarray, session: np.ndarray) -> dict:
    """How much of a positive total comes from its single best session.

    A mechanism whose whole result is one day is not a mechanism, and this is the
    number that says so. Reported as a share of the positive total; when the
    total is not positive there is nothing to concentrate and the share is None.
    """
    if net.size == 0:
        return {"best_session_share_pct": None, "total_excl_best_session": None,
                "sessions": 0}
    total = float(net.sum())
    uniq = np.unique(session)
    per = np.array([float(net[session == s].sum()) for s in uniq])
    best = float(per.max()) if per.size else 0.0
    return {
        "sessions": int(uniq.size),
        "best_session_net": round(best, 2),
        "total_excl_best_session": round(total - best, 2),
        "best_session_share_pct": (
            round(100.0 * best / total, 1) if total > 0 else None
        ),
    }


def summarise(
    *,
    gross: np.ndarray,
    net: np.ndarray,
    cost: np.ndarray,
    mfe: np.ndarray,
    mae: np.ndarray,
    win: np.ndarray,
    session: np.ndarray,
    order: np.ndarray,
) -> dict:
    """The full requested metric set for one cohort.

    ``order`` is the chronological ordering of the cohort's trades; drawdown and
    the losing streak are computed on it rather than on whatever order the mask
    happened to produce.
    """
    n = int(net.size)
    if n == 0:
        return {"trades": 0, "sessions": 0, "status": EMPTY}
    chron = net[order]
    ranked = np.sort(net)[::-1]
    top1 = max(1, int(round(0.01 * n)))
    total = float(net.sum())
    streak = worst = 0
    for v in chron:
        streak = streak + 1 if v < 0 else 0
        worst = max(worst, streak)
    conc = session_concentration(net, session)
    return {
        "trades": n,
        "sessions": conc["sessions"],
        "gross_total": round(float(gross.sum()), 2),
        "net_total": round(total, 2),
        "cost_total": round(float(cost.sum()), 2),
        "gross_expectancy": _f(float(gross.mean())),
        "expectancy": _f(float(net.mean())),
        "median_net": _f(float(np.median(net))),
        "win_rate_pct": round(100.0 * float(np.asarray(win, dtype=bool).mean()), 2),
        "positive_net_pct": round(100.0 * float((net > 0).mean()), 2),
        "profit_factor": profit_factor(net),
        "max_drawdown": max_drawdown(chron),
        "longest_losing_streak": int(worst),
        "avg_mfe": _f(float(np.mean(mfe))),
        "avg_mae": _f(float(np.mean(mae))),
        "net_total_excl_top1pct": round(total - float(ranked[:top1].sum()), 2),
        "best_session_net": conc["best_session_net"],
        "best_session_share_pct": conc["best_session_share_pct"],
        "net_total_excl_best_session": conc["total_excl_best_session"],
        "status": "OK",
    }


def wilson_low(hits: int, n: int, z: float = 1.96) -> float | None:
    """Lower bound of the 95% Wilson interval on a win rate, in percent."""
    if n <= 0:
        return None
    p = hits / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(100.0 * max(0.0, centre - half), 2)


def t_statistic(net: np.ndarray) -> float | None:
    """One-sample t against zero mean, for the FDR step.

    A t rather than a win-rate binomial because the question a lead has to answer
    is whether its *expectancy* differs from zero, and a mechanism can win often
    and still lose money.
    """
    n = int(net.size)
    if n < 2:
        return None
    sd = float(net.std(ddof=1))
    if sd <= 0:
        return None
    return round(float(net.mean()) / (sd / math.sqrt(n)), 4)


def p_value_one_sided(t: float | None, n: int) -> float:
    """Upper-tail normal approximation to the t p-value.

    Normal rather than exact: every cohort that reaches the trade floor has n in
    the hundreds, where the difference is smaller than the rounding this report
    prints, and it avoids a scipy dependency the project does not have.
    """
    if t is None or n < 2:
        return 1.0
    return round(0.5 * math.erfc(float(t) / math.sqrt(2.0)), 6)
