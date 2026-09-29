"""Phase 48 — the admission funnel: where every observation died.

Phase 46 wrote one row per decision instant per vehicle and named the reason it
could not admit that instant. Phase 47 then showed an empty research column
against 26,704 journalled option rows, which is a true statement and a useless
one: "nothing was admitted" does not say whether the feed was silent, the
definition had no opinion, or the cost gate refused a real one.

This phase is the attribution. It counts the rows at each stage of the same
question order Phase 46 asks, per session, instrument and vehicle, and reports
the distance the cost-blocked instants fell short by.

It is a **description of a journal that already exists**. It re-classifies
nothing, re-prices nothing, holds no threshold of its own, and has no path to
an order. In particular it must never become a threshold search: the distance
distribution below says how far short the refused instants were, and choosing a
multiple *because* it would have admitted more of them is fitting a gate to the
sample it is measured on — the mistake that got the Phase 44 8x arm marked
ABANDONED_ON_BASE_RATE.
"""
from __future__ import annotations

VERSION = "48.0"
PHASE = "PHASE48_ADMISSION_FUNNEL_ATTRIBUTION"

ARTEFACT_DIR = "phase48"
MD_NAME = "ADMISSION_FUNNEL.md"
JSON_NAME = "admission_funnel.json"

CLASSIFICATION = "ADMISSION_FUNNEL"
READ_ONLY = "READ_ONLY_OVER_THE_PHASE46_JOURNAL"
NO_ORDER_PATH = "NO_ORDER_PATH_NO_BROKER_NO_REAL_MONEY"
PRODUCTION_UNCHANGED = "PRODUCTION_SIGNAL_UNCHANGED_BY_THIS_LAYER"
NOT_A_THRESHOLD_SEARCH = (
    "NOT_A_THRESHOLD_SEARCH: the distance figures describe how far the refused "
    "instants fell short of the gate the engine already applies. Choosing a "
    "multiple because it would have admitted more of them fits the gate to the "
    "sample it was measured on, which is the error the 8x arm was abandoned "
    "for. No multiple here is proposed, and none is armed."
)
NOT_A_RESULT = (
    "COUNTS_ARE_NOT_A_RESULT: this is where observations stopped, not what "
    "they would have earned. A funnel that admits more instants is not a "
    "better funnel; it is a wider one."
)

# ------------------------------------------------------------------ the stages
# The order is Phase 46's own question order, and that is load-bearing: "could
# the evidence speak at all" is asked before "what did it say", so a stale book
# can never be counted as a cost refusal. A funnel in a different order would
# be a second definition of admission.
OBSERVED = "OBSERVED"
NO_EVIDENCE = "NO_RESEARCH_EVIDENCE"
NO_BOOK = "NO_TWO_SIDED_BOOK"
WARMING = "WARMING_UP_NO_DECISION_INPUTS_YET"
UNMEASURED_OTHER = "UNMEASURED_OTHER"
STALE = "STALE_BOOK"
CAPTURE_GAP = "CAPTURE_GAP"
DIRECTION = "VEHICLE_OPPOSES_THE_DIRECTION"
COST = "EXPECTED_MOVE_UNDER_THE_COST_GATE"
NOT_FRESH = "PRODUCTION_INSTANT_WAS_NOT_A_FRESH_BUY"
ADMITTED = "ADMITTED"

STAGES: tuple[str, ...] = (
    OBSERVED,
    NO_EVIDENCE,
    NO_BOOK,
    WARMING,
    UNMEASURED_OTHER,
    STALE,
    CAPTURE_GAP,
    DIRECTION,
    COST,
    NOT_FRESH,
    ADMITTED,
)

# Which stages mean "the feed could not speak" as against "the definition
# spoke and said no". They need opposite remedies: one is capture uptime, the
# other is the research itself.
FEED_STAGES: frozenset[str] = frozenset(
    {NO_EVIDENCE, NO_BOOK, WARMING, UNMEASURED_OTHER, STALE, CAPTURE_GAP})
DEFINITION_STAGES: frozenset[str] = frozenset({DIRECTION, COST, NOT_FRESH})

STAGE_NOTES: dict[str, str] = {
    NO_EVIDENCE: "no shadow row existed for the instant at all",
    NO_BOOK: "no two-sided quote for this vehicle at the instant",
    WARMING: (
        "the book was there; the definition's own inputs were not — no "
        "direction, or not enough trailing range before the instant to "
        "compute one. This clears with session history, not with capture"
    ),
    UNMEASURED_OTHER: "unmeasured for a reason that is neither of the above",
    STALE: "a quote was there and was too old to act on",
    CAPTURE_GAP: "the capture itself had a hole at the instant",
    DIRECTION: "the vehicle opposes the direction the instant was read as",
    COST: (
        "the expected move could not cover its own round trip at the "
        "multiple the engine already applies"
    ),
    NOT_FRESH: (
        "the research would have looked, but the production instant was not "
        "a fresh buy candidate, so there is nothing to agree with"
    ),
    ADMITTED: "the definition admitted the instant — capability, not profit",
}

# The distances the cost stage is reported at. Descriptive bins, deliberately
# coarse: a fine grid invites reading a threshold off the peak.
DISTANCE_BINS: tuple[float, ...] = (0.25, 0.5, 0.75, 0.9, 1.0)

MIN_ROWS_FOR_A_SHARE = 30
INSUFFICIENT = "INSUFFICIENT_ROWS_TO_QUOTE_A_SHARE"
DOMINANT_UNDECIDED = "NO_SINGLE_DOMINANT_BLOCKER"
