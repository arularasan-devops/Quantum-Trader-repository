"""Phase 52 statistics and eligibility — deliberately duplicated, not imported.

Phase 51 carries the same three helpers, and importing them from there would be
shorter. It is not done, for one reason: Phase 51's own suite asserts that no
module outside Phase 51 imports Phase 51, which is what guarantees a research
engine can never be reached from anything that trades. Borrowing forty lines of
arithmetic would have cost that guarantee, so the arithmetic is restated here and
Phase 51 stays hermetic.

The duplication is bounded to pure functions of their arguments — a normal tail,
a Benjamini-Hochberg pass and a drawdown walk. None of them reads a file, a
config or a clock, so the two copies cannot drift in behaviour without one of
the two suites failing.
"""
from __future__ import annotations

import math

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase52 import (
    FDR_ALPHA,
    HISTORICAL_CANDLE_DATA,
    MIN_BARS,
    MIN_SESSIONS,
    NOT_EXECUTABLE_BOOK,
)
from app.research.phase52 import levels as lv_mod


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def one_sided_p(x: np.ndarray) -> float:
    """P(a mean this positive arose by chance), one-sided, from a t statistic.

    A normal tail on the t statistic. At the trade counts this mechanism
    produces the normal tail is optimistic against the exact t distribution, so
    the p-value printed is the *kinder* of the two — which matters only because
    nothing passed even so.
    """
    x = x[np.isfinite(x)]
    n = x.size
    if n < 3:
        return 1.0
    sd = float(np.std(x, ddof=1))
    if sd <= 0.0:
        return 0.0 if float(np.mean(x)) > 0 else 1.0
    t = float(np.mean(x)) / (sd / math.sqrt(n))
    return max(0.0, min(1.0, 1.0 - _normal_cdf(t)))


def benjamini_hochberg(p_values: list[float], *, tests: int,
                       alpha: float = FDR_ALPHA) -> list[bool]:
    """BH over ``tests`` hypotheses, which may exceed the p-values supplied.

    ``tests`` is the number of hypotheses actually evaluated, not the number
    worth reporting. Correcting against the survivors is how a data-mined fluke
    passes: every parameterization discarded along the way was another chance to
    find this one, and the correction has to know that.
    """
    m = max(int(tests), len(p_values))
    if m == 0 or not p_values:
        return [False] * len(p_values)
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    out = [False] * len(p_values)
    largest = -1
    for rank, i in enumerate(order, start=1):
        if p_values[i] <= alpha * rank / m:
            largest = rank
    for rank, i in enumerate(order, start=1):
        if rank <= largest:
            out[i] = True
    return out


def max_drawdown_r(net_r: np.ndarray) -> float:
    """Peak-to-trough of the cumulative net-R curve, in R."""
    if net_r.size == 0:
        return 0.0
    curve = np.cumsum(net_r)
    peak = np.maximum.accumulate(curve)
    return float(np.max(peak - curve)) if curve.size else 0.0


def eligibility(instrument: str) -> dict:
    """Is there enough 1-minute history to run this mechanism on this name."""
    series = p24data.load_series(instrument)
    if series is None or len(series) == 0:
        return {
            "instrument": instrument.upper(),
            "eligible": False,
            "bars": 0,
            "sessions": 0,
            "source": HISTORICAL_CANDLE_DATA,
            "reason": "no 1-minute series on disk, shipped or imported",
            "execution_note": NOT_EXECUTABLE_BOOK,
        }
    sessions = int(np.unique(lv_mod.sessions_of(series.ts)).size)
    bars = len(series)
    eligible = bars >= MIN_BARS and sessions >= MIN_SESSIONS
    return {
        "instrument": instrument.upper(),
        "eligible": eligible,
        "bars": bars,
        "sessions": sessions,
        "first_ts": int(series.ts[0]),
        "last_ts": int(series.ts[-1]),
        "source": HISTORICAL_CANDLE_DATA,
        "reason": None if eligible else (
            f"{bars:,} bars over {sessions:,} sessions is below the declared "
            f"floor of {MIN_BARS:,} bars and {MIN_SESSIONS:,} sessions, and a "
            f"chronological 60/20/20 split of less than that cannot be graded"
        ),
        "execution_note": NOT_EXECUTABLE_BOOK,
    }
