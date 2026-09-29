"""Phase 32 — reachable T1, every instrument, and what it implies for a premium.

Phase 31 measured how far price travels after a decision and how long it takes.
This phase asks the next question: **can a T1 be placed where the measurement
says it is actually reached, often enough and fast enough to pay its own cost** —
and it asks it for every instrument that has data, not only the two with five
years.

Everything below is frozen here, in code, before any outcome is computed:

* the T1 grid, the stop multiples, the holding caps;
* the chronological 60/20/20 split;
* the rule that picks a T1 (best net expectancy on the *development* window
  only, never on the holdout);
* the minimum sample a window must have before it is allowed to decide anything;
* the tie rule: when one minute contains both T1 and the stop, the **stop** wins.

Three labels separate what is measured from what is not:

``MEASURED_UNDERLYING``
    Real one-minute bars. Index or futures movement, never a premium.
``DERIVED_PREMIUM``
    A premium percentage obtained from a measured underlying move by stated
    arithmetic (delta and premium/strike). An optimistic bound: theta, IV change
    and the spread all make the real premium outcome worse.
``REQUIRES_MORE_DATA``
    The instrument has candles, but not enough history to be split
    chronologically, so no T1 is selected for it and no verdict is claimed.

Research only. No gate, entry, stop, target, sizing or order-path change.
"""
from __future__ import annotations

MEASURED_UNDERLYING = "MEASURED_UNDERLYING"
DERIVED_PREMIUM = "DERIVED_PREMIUM"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"

BASES = (MEASURED_UNDERLYING, DERIVED_PREMIUM, REQUIRES_MORE_DATA)

# Candidate T1 distances, in percent of price. Same grid as Phase 31 so the two
# studies quote the same numbers, and small enough to be counted honestly.
T1_GRID = (0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.00)

# The stop is a multiple of the chosen T1, not an independent search dimension.
SL_MULTIPLES = (1.0, 1.5, 2.0)

# Holding caps in minutes. Unresolved at the cap is a real outcome: the position
# is closed at that bar's close, not held until it happens to work.
CAPS = (15, 30, 60)

# Chronological split. No random sampling anywhere.
DEV_FRACTION = 0.60
VAL_FRACTION = 0.20
# The remaining 0.20 is the untouched holdout.

# A window with fewer resolved trades than this cannot select or confirm a
# configuration; it reports the count instead of a verdict.
MIN_TRADES_PER_WINDOW = 500

# Cost stress multipliers applied to the round-trip cost.
COST_STRESS = (1.0, 1.5, 2.0)

# A distance is only worth attempting if the move pays its round trip several
# times over. Same number as ``settings.flow_cost_multiple``, which production
# already uses to refuse a leg whose expected move is inside its own cost.
MIN_COST_MULTIPLE = 3.0

# Delta bands for the premium translation, matching Phase 31.
DELTA_BANDS = (0.35, 0.50, 0.65, 0.80)

# Premium gain the translation answers for.
PREMIUM_TARGETS_PCT = (10.0, 20.0, 30.0, 50.0, 100.0)

VALIDATED = "VALIDATED"
RESEARCH_LEAD = "RESEARCH_LEAD"
REJECTED = "REJECTED"

NO_REACHABLE_T1 = "NO_REACHABLE_T1_FOUND"

# Chronological history needed before a 60/20/20 split means anything.
MIN_BARS_FOR_SPLIT = 100_000
MIN_SESSIONS_FOR_SPLIT = 750
