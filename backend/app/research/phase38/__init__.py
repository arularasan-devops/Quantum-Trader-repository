"""Phase 38 — why the first complete CRUDEOIL session lost, on every vehicle.

This is a *diagnostic*, not a study. Phase 36 already answered "which vehicle
made the most net money" with "none of them"; this package answers the only
useful follow-up question, which is *where the money went*, and it is allowed to
conclude nothing else. There is no verdict vocabulary here containing VALIDATED
or PRODUCTION_READY, so the report cannot express a promotion even by accident.

Three boundaries, stated once:

**Nothing is re-decided.** The signal, the strike rule, the target, the stop, the
exit and the order path are untouched. Every number below comes from the same
Phase 36 resolution code over the same executable books, so a figure here and a
figure in the Phase 36 report describe the same legs.

**One session is one session.** ``SESSION_COUNT`` is printed at the top of the
report and every cohort below the evidence floor prints
:data:`INSUFFICIENT_EVIDENCE` instead of a number. A cause is ranked by the
rupees attributable to it *in this session*, which is a description of one day
and not an estimate of tomorrow.

**Causes are separated, not inferred.** The six categories the task asks for are
distinguished by measured quantities only:

* the leg never had a favourable excursion at all — the move;
* it had one, but smaller than its own round-trip cost — the cost;
* it cleared its round-trip cost and still ended negative — giveback;
* a different vehicle on the same instant was positive — the vehicle;
* a later entry on the same instant was better — the entry;
* a different horizon on the same leg was better — the exit.

A leg that is negative for none of those reasons is OTHER rather than being
forced into the nearest box.
"""
from __future__ import annotations

VERSION = "38.0"
PHASE = "PHASE38_LOSS_DIAGNOSTIC"

DEFAULT_INSTRUMENT = "CRUDEOIL"

# §2 — the four categories every entered leg is placed in, at the reference
# horizon. Frozen definitions, in :mod:`app.research.phase38.sections`:
# they are mutually exclusive and cover every entered leg, so the counts add up
# to the sample and a category cannot be quietly dropped.
MOVE_TOO_SMALL = "MOVE_TOO_SMALL"
COST_DOMINATED = "COST_DOMINATED"
GIVEBACK = "MOVE_SUFFICIENT_BUT_LOST_TO_GIVEBACK"
OTHER = "OTHER"
CATEGORIES: tuple[str, ...] = (MOVE_TOO_SMALL, COST_DOMINATED, GIVEBACK, OTHER)

# §6 — how the three vehicles failed relative to each other on one instant.
FUT_PAID_OPTIONS_LOST = "FUTURES_PAID_OPTIONS_LOST"
OPTIONS_MOVED_COST_ERASED = "OPTIONS_MOVED_MORE_BUT_COST_ERASED_IT"
UNDERLYING_MOVE_INSUFFICIENT = "UNDERLYING_MOVE_ITSELF_INSUFFICIENT"
SOME_VEHICLE_PAID = "AT_LEAST_ONE_VEHICLE_PAID"
PATTERNS: tuple[str, ...] = (
    FUT_PAID_OPTIONS_LOST, OPTIONS_MOVED_COST_ERASED,
    UNDERLYING_MOVE_INSUFFICIENT, SOME_VEHICLE_PAID,
)

# §7 — entry-timing labels. TOO_LATE is deliberately unreachable from this
# data: an entry earlier than the decision instant does not exist to measure,
# and inventing one would be the first look-ahead in the whole study.
TOO_EARLY = "TOO_EARLY"
TOO_LATE = "TOO_LATE"
ENTRY_NOT_THE_PROBLEM = "NOT_THE_MAIN_PROBLEM"
NO_EARLIER_ENTRY = "TOO_LATE_IS_UNMEASURABLE_NO_PRE_DECISION_QUOTE"

# §11 — the causes this diagnostic may name, ranked by rupee impact.
CAUSE_MOVE = "MOVE_TOO_SMALL"
CAUSE_COST = "COST_TOO_HIGH"
CAUSE_ENTRY = "ENTRY_TIMING_PROBLEM"
CAUSE_VEHICLE = "WRONG_VEHICLE"
CAUSE_GIVEBACK = "GIVEBACK_PROBLEM"
CAUSE_EXIT = "EXIT_PROBLEM"
CAUSE_NONE = "INSUFFICIENT_EVIDENCE"
CAUSES: tuple[str, ...] = (
    CAUSE_MOVE, CAUSE_COST, CAUSE_ENTRY, CAUSE_VEHICLE, CAUSE_GIVEBACK,
    CAUSE_EXIT, CAUSE_NONE,
)

# §13 — the only three actions this document may end with. None of them
# promotes anything, and there is no fourth value meaning "go live".
CONTINUE_PAPER = "CONTINUE PAPER"
COLLECT_MORE = "COLLECT MORE DATA"
DATA_FIX = "REQUIRES DATA FIX"
ACTIONS: tuple[str, ...] = (CONTINUE_PAPER, COLLECT_MORE, DATA_FIX)

# A cohort smaller than this prints INSUFFICIENT_EVIDENCE rather than a mean.
# Same floor as Phase 36's per-vehicle comparison floor, on purpose: two
# different floors in two reports over the same legs is how a thin cohort
# becomes quotable by moving between documents.
MIN_COHORT = 30

# Sessions needed before this diagnostic's ranking is anything but descriptive.
MIN_SESSIONS_FOR_A_CLAIM = 20

# A leg counts as "cost could not be measured" when the round trip has no lot
# size. Above this share of the sample the diagnosis is a data problem rather
# than a market one, whatever the tables say.
UNMEASURED_COST_TOLERANCE_PCT = 5.0

# §7 — a delayed entry must beat the immediate one by more than this many
# percent of entry to be called an improvement. Set to a tenth of a typical
# crude option round trip so a rounding-level difference cannot rename the
# primary loss driver.
ENTRY_IMPROVEMENT_PCT = 0.05

# §3/§11 — likewise for the exit: a horizon must beat the reference horizon by
# more than this to be evidence of an exit problem.
EXIT_IMPROVEMENT_PCT = 0.05

INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
NO_STOP_RECORDED = "NO_STOP_DISTANCE_RECORDED_IN_A_PHASE36_TRIPLE"
UNKNOWN_RUPEES = "LOT_SIZE_UNKNOWN"

RESEARCH_ONLY = "RESEARCH_ONLY"
PAPER_ONLY = "PAPER_ONLY"

ARTEFACT_DIR = "data/phase38"
MD_NAME = "CRUDEOIL_PHASE36_LOSS_DIAGNOSTIC.md"
JSON_NAME = "CRUDEOIL_PHASE36_LOSS_DIAGNOSTIC.json"

HEADER_LINES: tuple[str, ...] = (
    "THIS IS A SESSION DIAGNOSTIC.",
    "IT IS NOT A STRATEGY VERDICT.",
)
