"""Phase 31 — how far does it actually move, and when.

Phase 30 answered a *binary* question: did T1 arrive before the stop. That hides
the number a trader actually wants, which is how much a position can be expected
to give and how long it takes. This phase drops stops and targets entirely and
measures the excursion distribution instead.

Three things are kept strictly apart, because they have very different evidence
behind them:

``MEASURED_UNDERLYING``
    Forward favourable and adverse excursion of the real five-year one-minute
    series, in percent, at fixed horizons, with time-to-threshold. Real data,
    hundreds of thousands of decision instants, no strategy assumed.

``MEASURED_REALIZED``
    What the tool's own recorded calls actually returned in premium percent, from
    the paper/live journal. Real entries and exits, but a small sample, and every
    row is truncated by whatever exit rule was live at the time — it is what was
    *taken*, not what was *available*.

``ASSUMED_PREMIUM_MAP``
    The bridge from an underlying move to a premium percentage. This is a stated
    arithmetic assumption (delta and premium-to-spot ratio), never a measurement,
    because five-year two-sided option books do not exist in this store. Any
    number derived through it carries the label, and no such number is ever
    presented as a measured option result.

Nothing here touches production, the order path, or any live decision.
"""
from __future__ import annotations

MEASURED_UNDERLYING = "MEASURED_UNDERLYING"
MEASURED_REALIZED = "MEASURED_REALIZED"
ASSUMED_PREMIUM_MAP = "ASSUMED_PREMIUM_MAP"
UNMEASURED = "UNMEASURED"

BASES = (MEASURED_UNDERLYING, MEASURED_REALIZED, ASSUMED_PREMIUM_MAP, UNMEASURED)

# Horizons in minutes. One minute is included so the "does it move at all
# immediately" question is answerable, sixty because beyond an hour an intraday
# call is a different trade.
HORIZONS = (1, 3, 5, 10, 15, 30, 60)

# Favourable/adverse thresholds as a percentage of the entry price. Chosen to
# bracket the moves a premium target implies at realistic deltas, and frozen
# before any excursion is computed.
PCT_GRID = (0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.00)

# Premium targets the question is usually asked in.
PREMIUM_TARGETS_PCT = (10.0, 20.0, 30.0, 50.0, 100.0)

# Delta bands used by the stated mapping. An ATM option is ~0.5; a one-strike
# OTM weekly is nearer 0.35; deep ITM approaches 0.8.
DELTA_BANDS = (0.35, 0.50, 0.65, 0.80)

__all__ = [
    "MEASURED_UNDERLYING", "MEASURED_REALIZED", "ASSUMED_PREMIUM_MAP",
    "UNMEASURED", "BASES", "HORIZONS", "PCT_GRID", "PREMIUM_TARGETS_PCT",
    "DELTA_BANDS",
]
