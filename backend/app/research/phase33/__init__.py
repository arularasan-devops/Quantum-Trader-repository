"""Phase 33 — how far a signal moves, when it peaks, and how long to hold.

Phase 31 measured excursion. Phase 32 asked whether a target can sit where the
excursion actually arrives. This phase asks the question that sits between them
and is the one a trader actually acts on: **after entry, when is the trade at its
best, and when does holding start giving it back?**

Everything below is frozen here, in code, before any outcome is computed: the
hold-time grid, the move levels, the giveback definitions, the chronological
split, the rule that picks a holding window (best *net* expectancy on the
development window only) and the minimum sample a window needs before it is
allowed to decide anything.

Three separations the report keeps visible at all times, because collapsing any
of them is how a study like this lies:

``MEASURED``
    Real bars or real two-sided books. Nothing derived.
``COUNTERFACTUAL``
    What would have been best in hindsight (best vehicle, best exit minute).
    Useful as a diagnosis of what was left on the table; never a validated rule.
``REQUIRES_MORE_DATA``
    The sample is too small to state a preference. Reported as a count, never as
    a ``BEST``.

And three distinctions §23 insists on, kept as separate columns rather than one
headline: a high reach rate is not profitability, a high MFE is not realisable
profit, and the best historical hold is not a validated hold.

Research only. No signal, entry, strike, target, stop, exit, hold timer,
averaging, sizing or order-path change.
"""
from __future__ import annotations

MEASURED = "MEASURED"
COUNTERFACTUAL = "COUNTERFACTUAL"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
UNMEASURED = "UNMEASURED"

# Holding horizons in minutes, §5. ``SESSION_CLOSE`` is handled separately
# because its length differs per row and it must never be averaged in with a
# fixed horizon.
HOLD_GRID = (1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120)
SESSION_CLOSE = "SESSION_CLOSE"
MAX_HOLD = max(HOLD_GRID)

# Favourable distances measured on futures/underlying, in percent, §6.
FUT_LEVELS = (0.10, 0.20, 0.30, 0.50, 0.75, 1.00, 1.50, 2.00)

# Favourable distances measured on option premium, in percent, §6. Only ever
# applied to real two-sided books; never derived from an underlying move.
OPT_LEVELS = (1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0)

# Percentiles of the favourable excursion distribution that become candidate
# targets, §9. They are reach rates, not probabilities, until calibrated.
MFE_PERCENTILES = (50, 60, 70, 75, 80, 90)
EMPIRICAL_REACH_RATE = "EMPIRICAL_REACH_RATE"

# Giveback, §8: a trade counts as "profitable at some point" once its favourable
# excursion clears this multiple of its own round-trip cost. Using a cost
# multiple rather than a flat percentage stops a move that never paid for itself
# from being called a profit that was given back.
PROFITABLE_COST_MULTIPLE = 1.0

# Chronological split, §16. No random sampling anywhere.
DEV_FRACTION = 0.60
VAL_FRACTION = 0.20
# The remaining 0.20 is the untouched holdout.
WALK_FORWARD_FOLDS = 5

# A window with fewer resolved rows than this cannot select or confirm a holding
# period; it reports the count instead of a verdict, §19.
MIN_ROWS_PER_WINDOW = 500
MIN_SESSIONS_PER_WINDOW = 20
# A family below this many rows is described but never ranked.
MIN_ROWS_PER_FAMILY = 200

# Cost stress, §18.
FUT_COST_STRESS = (1.0, 1.5, 2.0)
OPT_SPREAD_STRESS = (1.0, 1.25, 1.50)

# Outlier removal, §17.
OUTLIER_TRIMS_PCT = (0.0, 1.0, 5.0)

# Chronological history needed before a 60/20/20 split means anything. Same
# numbers as Phase 32, so an instrument cannot change tier between phases.
MIN_BARS_FOR_SPLIT = 100_000
MIN_SESSIONS_FOR_SPLIT = 750

# Real two-sided option observations needed before a premium distribution is
# reported at all. Same bar as Phase 31 used to refuse one.
MIN_OPTION_INSTANTS = 500

VALIDATED_HOLD = "VALIDATED_HOLD_WINDOW_FOUND"
RESEARCH_LEAD_HOLD = "RESEARCH_LEAD_HOLD_WINDOW"
NO_HOLD_WINDOW = "NO_REPEATABLE_HOLD_WINDOW_FOUND"

VERDICTS = (VALIDATED_HOLD, RESEARCH_LEAD_HOLD, REQUIRES_MORE_DATA, NO_HOLD_WINDOW)

SCOPE = "RESEARCH_ONLY"
