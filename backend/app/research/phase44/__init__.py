"""Phase 44 — the Phase 43 nearest miss, pre-registered and recorded, not run.

Phase 43 measured 42 hypotheses on five years of one-minute candles and produced
no lead. One shape was interesting and short of evidence:
``A1_EXPECTED_MOVE_OVER_COST_8X_CRUDEOIL`` — the ordinary trend-and-momentum
direction rule, taken only when the trailing ATR is at least eight times the
*modelled* round-trip cost. It held +227,009 NET in the untouched holdout at
PF 1.202, on 238 trades across 53 sessions, against declared floors of 100
trades and 60 sessions. It was labelled REQUIRES_MORE_DATA and it stays that
way. This phase does not promote it, does not trade it and does not measure
whether it works.

**What this phase is.** The rule written down and fingerprinted *before* any
live evidence exists, plus a journal that records what the rule would have seen
at each decision instant. Writing the definition after seeing the live data is
the failure this project has already made twice, and a frozen hash is the only
defence that does not rely on memory.

**Why there is no live hook.** The Phase 17 capture already records both sides
of the book at the decision instant, and Phase 35's raw store is never
overwritten. So the journal is built from raw *after the close* and sees exactly
what a tick-time hook would have seen, with no code at all on the path that runs
while the market is open. Track A capture cannot be disturbed by a phase that
never executes during a session.

**The switch is off, and off is the default in code.** :mod:`switch` refuses to
record until an operator arms it explicitly, and the armed state is bound to the
definition hash: change the rule and the arm lapses, because rows recorded under
two definitions are not one sample. Nothing here reads or writes a setting the
live engine uses.

**The experiment, stated before it is run.** Phase 43's ratio divides by a
*modelled* cost that cannot see a spread, because five-year candles have no
book. The live ratio must divide by the *measured* round trip, spread included.
The measured cost is the larger of the two, so the measured ratio is the
smaller, and the 8x arm will fire less often live than it did historically —
possibly never. Every journal row therefore carries both costs and both ratios
side by side. If the two diverge enough that the arm stops firing, that is the
finding, and it should be visible within days rather than after
:data:`TARGET_SESSIONS` sessions.

**What this phase cannot conclude.** Anything about profit. It records decision
instants; it resolves no outcomes and reports no returns. A later phase may
judge the arm once :data:`TARGET_TRADES` executable decisions exist, and that
phase will be pre-registered too.
"""
from __future__ import annotations

from app.research.phase43 import mechanisms as p43mech
# Every field the capture must carry for a row to be judgeable later, imported
# from Phase 43's registry so the list a future validation is held to is the
# list Phase 43 published rather than a convenient subset of it.
from app.research.phase43.registry import REQUIRED_LIVE_FIELDS

VERSION = "44.0"
PHASE = "PHASE44_HISTORICAL_LEAD_SHADOW_DORMANT"

ARTEFACT_DIR = "data/phase44"
DB_NAME = "phase44_shadow.db"
JSON_NAME = "phase44_shadow.json"
MD_NAME = "PHASE44_SHADOW.md"

RESEARCH_ONLY = "RESEARCH_ONLY"
RECORD_ONLY = "RECORD_ONLY_NO_GATE_CHANGE_NO_ORDER_PATH"
NOT_A_PROMOTION = (
    "a journal row is not a trade, a trade is not a validation, and this arm "
    "was REQUIRES_MORE_DATA in Phase 43 and still is"
)

# --- the frozen arm --------------------------------------------------------
# The candidate is named as Phase 43 named it, so the two cannot drift apart in
# conversation. The conditions and the ratio are *imported* from Phase 43 rather
# than restated: a copied rule is a second rule, and it would go on agreeing
# with its parent while the two diverged.
SOURCE_PHASE = "PHASE43"
SOURCE_CANDIDATE = "A1_EXPECTED_MOVE_OVER_COST_8X_CRUDEOIL"
INSTRUMENT = "CRUDEOIL"
VEHICLE = "FUTURES"
BASE_CONDITIONS: tuple[str, ...] = tuple(p43mech.BASE)
RATIO_KEY = "expected_move_over_cost"
THRESHOLD = 8.0

# Geometry, carried from Phase 43 unchanged so a later resolution phase cannot
# quietly grade the arm on easier targets than it was declared on.
STOP_ATR = 2.0
T1_R = 1.5

# --- what counts as enough, declared now -----------------------------------
# Phase 43's own estimate: 400 executable decisions, which at its historical
# 5.80 admissions per session is about 69 sessions. Both are floors and both
# assume every session captures two sides at the decision instant; five of
# Phase 42's eight did not, so the calendar cost is likely larger.
TARGET_TRADES = 400
TARGET_SESSIONS = 69
HISTORICAL_ADMITS_PER_SESSION = 5.80

# --- switch states ---------------------------------------------------------
DORMANT = "DORMANT"
ARMED = "ARMED"
# Recording is refused for one of these reasons, and the refusal is the default.
REFUSED_DORMANT = "REFUSED: the recorder has never been armed"
REFUSED_DEFINITION_CHANGED = (
    "REFUSED: the rule was armed under a different definition hash, so new rows "
    "would pool with rows measured under another rule"
)

# --- per-row evidence ------------------------------------------------------
MEASURED = "MEASURED_EXECUTABLE"
# The spread was quoted and measured, but the lot came from the contract
# specification because the capture did not record one. A futures lot is a
# specification and not an observation, and the modelled cost already reads it
# from the same place — refusing it here while using it there would throw away
# a session whose book *was* captured. It is a separate label rather than the
# same one so a later phase can require the stronger evidence if it wants to.
SPEC_LOT = "MEASURED_SPREAD_SPEC_LOT"
UNMEASURED = "UNMEASURED"
NO_DIRECTION = "NO_DIRECTION_AT_DECISION"
NO_BOOK = "NO_TWO_SIDED_BOOK_AT_DECISION_INSTANT"
NO_LOT_SIZE = "NO_LOT_SIZE"
# The captured contract is not the one the spec lot describes (a mini contract
# trades a different lot), so the fallback is refused rather than mispriced.
CONTRACT_MISMATCH = "CAPTURED_CONTRACT_IS_NOT_THE_SPEC_CONTRACT"
NO_BARS = "NOT_ENOUGH_COMPLETED_MINUTES_BEFORE_THE_DECISION"
# A bar is built only from the minutes the capture actually observed, so a
# capture gap puts two distant minutes side by side and the true range charges
# the whole jump to one bar. That inflates the trailing ATR, which is the
# numerator of this arm's ratio — meaning a thinly captured session would admit
# far more readily than a well captured one, and the arm would fire exactly
# where the evidence is worst. Both are refusals rather than corrections: a gap
# cannot be interpolated away without inventing the prices that were not seen.
GAPPY_WINDOW = "CAPTURE_GAP_INSIDE_THE_TRAILING_WINDOW"
STALE_WINDOW = "LAST_COMPLETED_MINUTE_IS_NOT_ADJACENT_TO_THE_DECISION"
NO_ATR = "NO_TRAILING_ATR"
NO_COST = "NO_ROUND_TRIP_COST"

# The fewest completed minutes before a decision for the trailing ATR to mean
# anything. Phase 24 computes ATR over 14 bars and the trend terms need 15, so a
# decision earlier than this in a session is recorded UNMEASURED rather than
# scored off a partly-formed window.
MIN_BARS_BEFORE = 16

__all__ = ["REQUIRED_LIVE_FIELDS"]
