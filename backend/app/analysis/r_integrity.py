"""Reject arithmetically-true but economically meaningless R before aggregation.

Older resolutions were written before the journal refused a non-positive risk, so
the recorded ledger still holds sentinel rows such as ``realized_r =
100000000.0`` (premium 3.0, risk 0.0) alongside honest-but-explosive ones such as
``41.5R`` (premium 4.0, risk 0.2). Both are fatal to an average: a single
sentinel moves a cohort's expectancy by seven orders of magnitude, and a 0.2-point
stop under a 4-rupee premium is a rounding error being reported as an edge.

The rule is a filter, never a clamp. A clamped 20R is a number nobody measured;
an excluded row is a row with a reason and a count, which is why every caller
reports ``excluded`` and ``excluded_by_reason`` beside its cohort sizes.

Research only. Nothing here gates, sizes or suppresses a signal.
"""
from __future__ import annotations

import math

# One option tick. A stop closer than a tick cannot be respected by the book, so
# any R derived from it measures rounding, not risk.
MIN_RISK_POINTS = 0.05
# No intraday option resolution reaches this honestly; past it the denominator is
# the story, not the trade.
MAX_PLAUSIBLE_R = 20.0

NO_R = "NO_R_RECORDED"
NON_POSITIVE_RISK = "NON_POSITIVE_RISK"
SUB_TICK_RISK = "SUB_TICK_RISK"
IMPLAUSIBLE_R = "IMPLAUSIBLE_R"

R_FIELDS = ("realized_r", "mfe_r", "mae_r")


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def rejection(row: dict) -> str | None:
    """Why this resolved row must stay out of any R aggregate, or ``None``."""
    if _num(row.get("realized_r")) is None:
        return NO_R
    risk = _num(row.get("risk_points"))
    if risk is not None:
        if risk <= 0:
            return NON_POSITIVE_RISK
        if risk < MIN_RISK_POINTS:
            return SUB_TICK_RISK
    for field in R_FIELDS:
        value = _num(row.get(field))
        if value is not None and abs(value) > MAX_PLAUSIBLE_R:
            return IMPLAUSIBLE_R
    return None


def usable(row: dict) -> bool:
    return rejection(row) is None


def partition(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Split resolved rows into usable ones and a reason-counted exclusion tally."""
    kept: list[dict] = []
    excluded: dict[str, int] = {}
    for row in rows:
        why = rejection(row)
        if why is None:
            kept.append(row)
        else:
            excluded[why] = excluded.get(why, 0) + 1
    return kept, excluded


def note() -> str:
    return (f"rows with no R, a non-positive or sub-tick ({MIN_RISK_POINTS}) risk, "
            f"or any R beyond {MAX_PLAUSIBLE_R} are excluded from every average "
            f"here and counted in excluded_by_reason; they are a ledger-integrity "
            f"defect, not evidence")
