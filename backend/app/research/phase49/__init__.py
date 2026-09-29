"""Phase 49 — one opportunity counted once, and a wrong tally corrected on the record.

Two defects showed up on the same board on the same day, and they have the same
shape: **a number that cannot be told apart from a different number.**

*Nine ticks are not nine opportunities.* Session 2026-09-17 admitted eleven PE
legs — nine of them on one SENSEX strike inside sixty-four seconds, two on one
BANKNIFTY strike. As a leg count that is eleven. As a count of *distinct things
the research had an opinion about* it is two. Any statistic that divides by the
first — a win rate, a profit factor, a t-statistic, a sample bar — is claiming a
sample nine times larger than the evidence, and would clear a promotion gate on
one opinion repeated. So this phase publishes both counts, always beside each
other, and the leg count is never silently used where the event count belongs.

*A tally written by a broken selection outlives the fix.* The production tally
of 8 for that session was recorded while the call query still bounded rows
before filtering them. The journal is append-only and idempotent by design, so
the wrong 8 cannot correct itself — and must not be edited, because a journal
that rewrites its own history is worth less than one that is occasionally wrong
in public. The correction is therefore **additive**: the old row is left byte
for byte as it was, a new row is recorded under the corrected selection, and an
append-only supersession registry links the two with a reason and a timestamp.
Reading derives ``SUPERSEDED`` / ``CURRENT``; nothing overwrites anything.

What this phase deliberately does not do: change the shadow definition, the
admission rule, any Phase 41–44 definition, the production signal, or anything
near an order path. Grouping cannot admit a call — it can only stop the same
call being counted twice.
"""
from __future__ import annotations

import hashlib

VERSION = "49.0"
PHASE = "PHASE49_EVENT_GROUPING_AND_TALLY_SUPERSESSION"

ARTEFACT_DIR = "phase49"
DB_NAME = "phase49_supersession.db"

CLASSIFICATION = "EVENT_GROUPING_AND_SUPERSESSION"
PAPER_ONLY = "PAPER_ONLY"
NO_ORDER_PATH = "NO_ORDER_PATH_NO_BROKER_NO_REAL_MONEY"
PRODUCTION_UNCHANGED = "PRODUCTION_SIGNAL_UNCHANGED_BY_THIS_LAYER"
APPEND_ONLY = "APPEND_ONLY_NO_UPDATE_NO_DELETE_NO_HISTORY_REWRITE"

# ------------------------------------------------------------------ the rule
#
# The grouping key is the contract the call is in, at the instant it was taken.
# Every part of it is a decision-instant field carried on the journalled row —
# no price path, no mark, no outcome, and nothing that is only knowable later.
# CE and PE are separate by construction because ``vehicle`` is in the key, and
# two strikes are two events even when the underlying read is the same one.
EVENT_KEY: tuple[str, ...] = (
    "session", "arm", "instrument", "vehicle", "contract", "expiry", "strike",
    "direction",
)

# Two observations on the same key belong to the same event while the gap
# between them is no longer than this. A gap is the only separator available
# from decision-instant information alone: the alternative — closing an event
# when the position would have been exited — reads the future to decide how to
# count the past.
#
# Chosen before looking at the eleven legs it would group, and stated as a
# parameter rather than a discovery: the nine SENSEX ticks span 64 seconds and
# the two BANKNIFTY ticks 107, so a gap anywhere from ~2 to ~10 minutes gives
# the same two events. A rule whose answer is stable across the range it could
# plausibly have taken is a rule, not a fit.
EVENT_GAP_SEC = 300.0

GROUPING_RULE = (
    "SAME_SESSION_ARM_INSTRUMENT_VEHICLE_CONTRACT_EXPIRY_STRIKE_DIRECTION_"
    "WITHIN_A_300S_GAP_IS_ONE_EVENT"
)

RULE_TEXT = (
    "Two admitted observations are the same event when they share session, "
    "arm, instrument, vehicle, contract, expiry, strike and direction, and no "
    "more than 300 seconds separates them from the previous observation in "
    "that group. Every field is read from the decision instant. No mark, "
    "excursion, giveback, exit or profit-and-loss participates, so the "
    "grouping of a session is fixed the moment its rows are journalled and "
    "cannot change when the market later moves."
)


def rule_fingerprint() -> str:
    """The grouping rule as a fingerprint, published beside every count.

    Included in the event id itself. A retuned gap or an altered key produces
    different ids, so counts taken under two rules can be seen to be two
    populations instead of being pooled into a longer one.
    """
    raw = "|".join([
        PHASE, VERSION, GROUPING_RULE, str(EVENT_GAP_SEC), *EVENT_KEY,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# The corrected call selection this phase records tallies under. Phase 47's
# original selection bounded the session's rows before filtering them by arm,
# so a full day's warm-up filled the bound and the admitted instants behind it
# were never read. Tallies taken under the two selections are not comparable
# and the fingerprint is what says so.
SELECTION = "ARM_CONDITION_APPLIED_IN_SQL_AND_BOUNDED_AFTER_IT"
SELECTION_SUPERSEDED = "ROWS_BOUNDED_BEFORE_THE_ARM_CONDITION_WAS_APPLIED"


def selection_fingerprint() -> str:
    """Identifies which call selection a recorded tally was taken under."""
    return hashlib.sha256(f"{PHASE}|{SELECTION}".encode()).hexdigest()[:16]


# Derived on read, never stored on the row it describes.
CURRENT = "CURRENT"
SUPERSEDED = "SUPERSEDED"

REASON_SELECTION = (
    "RECORDED_UNDER_A_CALL_SELECTION_THAT_BOUNDED_ROWS_BEFORE_FILTERING_"
    "THEM_BY_ARM"
)

# The same correction where the count did not move. Separate, because the
# broken selection truncated *some* arms and not others: stamping a row whose
# count was never wrong with the reason above would claim a fault the two
# numbers beside it disprove.
REASON_SELECTION_SAME_COUNT = (
    "RE_RECORDED_UNDER_THE_CORRECTED_SELECTION_WITH_AN_UNCHANGED_COUNT_"
    "THE_OLD_COUNT_WAS_NOT_WRONG"
)

# A reading that stopped at its own bound, replaced by one that saw further.
# Not a mistake in the old row — a limit in it, which is why the old row is
# published as a floor rather than as the session's count.
REASON_BOUND = (
    "RECORDED_UNDER_A_SELECTION_BOUND_THAT_WAS_REACHED_SO_ITS_COUNT_WAS_A_"
    "FLOOR_NOT_A_TOTAL"
)

# A row written before the bound was stored on it, replaced by a reading that
# disagrees with its count. The old row is not readable as a floor *or* as a
# total — what it was bounded by was never recorded — so this says exactly that
# rather than guessing which of the two it was.
REASON_UNRECORDED_BOUND = (
    "RECORDED_BEFORE_THE_SELECTION_BOUND_WAS_STORED_ON_THE_ROW_SO_ITS_COUNT_"
    "CANNOT_BE_TOLD_FROM_A_FLOOR"
)

# The case none of the four above describes, and the one that turned out to be
# the commonest: the old reading was **complete for what existed when it was
# taken**, and a later reading saw more only because the session kept writing
# rows. Nothing about the old count was wrong. Asserted only from evidence — the
# old row's own record of how many legs its arm held at the time, which has to
# equal its count, against a later reading that holds more — never from the
# counts alone, because a count that grew is exactly what a mis-selection also
# looks like.
REASON_ACCRUAL = "SUPERSEDED_BY_LATER_READING_OF_A_SESSION_STILL_ACCRUING"

NOT_A_RESULT = (
    "EVENT_COUNT_IS_NOT_A_RESULT: grouping changes how many independent "
    "opportunities the session is claimed to hold, not what any of them "
    "earned. Fewer events than legs is the honest sample, not a worse one, "
    "and no call, vehicle or definition becomes profitable by being counted "
    "correctly."
)

SUPERSESSION_IS_NOT_A_DELETION = (
    "SUPERSESSION_IS_ADDITIVE: the superseded tally is kept exactly as it was "
    "recorded, including its wrong count. Its status is derived from an "
    "append-only registry at read time. Nothing in this phase updates or "
    "deletes a journalled row."
)
