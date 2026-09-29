"""The statistics the funnel depends on, kept in one place.

Two of these decide whether a wide scan produces evidence or a list of lucky
accidents, so they are written explicitly rather than pulled from a library the
reader would have to go and check:

:func:`p_value` — a one-sample test that the mean net return per trade is zero,
using the t statistic with a normal tail. The normal approximation is honest at
the sample sizes that matter here (the screen needs 30 trades before it says
anything) and slightly *conservative* nowhere — so the correction below is the
thing doing the real work, not this.

:func:`benjamini_hochberg` — the false-discovery cut across the whole family of
candidates. Testing 400 candidates at 5% yields about 20 that look significant
with no edge at all, which is more than this market is likely to hand out for
real. The cut is applied over every candidate scored in the cycle, not over the
survivors, because correcting only the survivors is not a correction.

No scipy: adding a dependency for two functions would be a supply-chain
decision made for convenience.
"""
from __future__ import annotations

import math


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def median(xs: list[float]) -> float | None:
    """The middle value, or ``None`` when there is nothing to take a middle of.

    ``None`` rather than zero: an empty period has no median, and a zero in a
    hold-time column would read as a trade that closed instantly.
    """
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    if len(s) % 2:
        return float(s[mid])
    return (float(s[mid - 1]) + float(s[mid])) * 0.5


def stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def profit_factor(xs: list[float]) -> float | None:
    """Gross win over gross loss. ``None`` when there is no loss to divide by.

    Returning ``None`` rather than infinity matters: a candidate with four
    trades and no loser has a profit factor that is undefined, not excellent,
    and an infinity sorts to the top of every ranking.
    """
    wins = sum(x for x in xs if x > 0)
    losses = -sum(x for x in xs if x < 0)
    if losses <= 0:
        return None
    return wins / losses


def max_drawdown(xs: list[float]) -> float:
    """Worst peak-to-trough of the cumulative net series, in the same units."""
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for x in xs:
        equity += x
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return abs(worst)


def win_rate(xs: list[float]) -> float | None:
    if not xs:
        return None
    return 100.0 * sum(1 for x in xs if x > 0) / len(xs)


def _normal_sf(z: float) -> float:
    """Upper tail of the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def p_value(xs: list[float]) -> float | None:
    """Two-sided p for mean(xs) == 0. ``None`` when the sample cannot support one."""
    n = len(xs)
    if n < 5:
        return None
    sd = stdev(xs)
    if sd <= 0:
        return None
    t = mean(xs) / (sd / math.sqrt(n))
    return min(1.0, 2.0 * _normal_sf(abs(t)))


def benjamini_hochberg(pairs: list[tuple[str, float]], q: float) -> dict:
    """Which candidates survive a false-discovery rate of ``q``.

    ``pairs`` is ``(candidate_id, p_value)`` for **every** candidate that was
    scored, including the losers. The threshold is the largest p at rank k with
    ``p <= k*q/m``; everything at or below it survives.
    """
    scored = [(cid, p) for cid, p in pairs if p is not None]
    m = len(scored)
    if m == 0:
        return {"m": 0, "q": q, "threshold": None, "survivors": [],
                "reason": "no candidate produced a testable sample"}
    ordered = sorted(scored, key=lambda kv: kv[1])
    threshold = None
    cut_rank = 0
    for k, (_cid, p) in enumerate(ordered, start=1):
        if p <= (k * q) / m:
            threshold = p
            cut_rank = k
    survivors = [cid for cid, _p in ordered[:cut_rank]]
    return {
        "m": m,
        "q": q,
        "threshold": threshold,
        "cut_rank": cut_rank,
        "survivors": survivors,
        "expected_false_positives_without_correction": round(0.05 * m, 1),
        "note": (
            "m counts every candidate scored this cycle, not the ones that "
            "looked good. Correcting only the survivors is not a correction."
        ),
    }


def stability(period_means: list[float | None]) -> dict:
    """Do the sub-periods agree on the sign, and how far apart are they?

    Sign agreement rather than a variance test: with three sub-periods a
    variance test has no power, while a candidate that is positive in one third
    and negative in the other two is telling a reader something plain.
    """
    seen = [m for m in period_means if m is not None]
    if len(seen) < 2:
        return {"periods": len(seen), "agree": None,
                "reason": "fewer than two sub-periods produced trades"}
    positive = sum(1 for m in seen if m > 0)
    return {
        "periods": len(seen),
        "positive_periods": positive,
        "agree": positive == len(seen) or positive == 0,
        "all_positive": positive == len(seen),
        "spread": max(seen) - min(seen),
        "means": seen,
    }
