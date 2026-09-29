"""Phase 47 — the research call board: the overlay's own calls, marked to market.

Phase 46 says what the research evidence looked like at a decision instant.
This phase answers the next question, which is the only one worth asking of a
research call: **would it have made money?** It takes every instant the research
admitted, treats it as a paper leg opened at the executable price recorded then,
and marks it against the latest measured quote for that same contract — so the
answer on screen is a number that moves during the session rather than a claim
that has to wait for a study.

Three properties make it evidence rather than a demo.

**The mark is measured, never modelled.** A long option is marked at the BID and
a short futures leg at the ASK, through Phase 35's ``exit_fill``, on quotes that
were captured at the time. Nothing here interpolates, forward-fills or reaches
across a session boundary, and a contract with no later quote is reported
``UNMARKABLE`` with its reason instead of being marked at its own entry — which
would print a flat leg and dilute the tally with a fiction.

**Cost is charged once, on the way in.** The round trip carried on the Phase 46
row is subtracted from the gross, so the net on this board is what the leg would
be worth after brokerage, spread, taxes and slippage. A board that showed gross
would be showing the one number the research already knows is not survivable.

**No hindsight and no order path.** Admission happened at the instant, in Phase
45/46, from information available then; this phase only prices what was already
admitted. It has no broker import, no order call, and no return value any
production caller reads. A call on this board is an entry in a paper journal and
nothing else.

The production arm is marked by the same function on the same quotes, so the two
columns differ in which legs they contain and in nothing else.
"""
from __future__ import annotations

VERSION = "47.0"
PHASE = "PHASE47_RESEARCH_CALL_BOARD_PAPER_MARKED_TO_MARKET"

ARTEFACT_DIR = "phase47"
DB_NAME = "phase47_calls.db"
MD_NAME = "RESEARCH_CALL_BOARD.md"

# Safety vocabulary, repeated on every payload and every artefact. The overlay
# taught the lesson: a screen showing running P&L is read as a recommendation
# unless it says otherwise in its own labels.
RESEARCH_CALL_BOARD = "RESEARCH_CALL_BOARD"
PAPER_ONLY = "PAPER_ONLY"
NO_ORDER_PATH = "NO_ORDER_PATH_NO_BROKER_NO_REAL_MONEY"
PRODUCTION_UNCHANGED = "PRODUCTION_SIGNAL_UNCHANGED_BY_THIS_LAYER"
NOT_A_RECOMMENDATION = "NOT_A_RECOMMENDATION_NOT_A_SIGNAL_NOT_A_FILTER"

# What a call on this board can be. A leg that cannot be priced is a state, not
# an absence: silently dropping it would make the tally look complete.
MARKED = "MARKED"

# A call's lifecycle, which is about its session and not about its price: a call
# in the running session is OPEN and its mark will move, one in a finished
# session is RESOLVED and its last mark is the one it ended on.
OPEN = "OPEN"
RESOLVED = "RESOLVED"
UNMARKABLE_NO_LATER_QUOTE = "UNMARKABLE_NO_LATER_QUOTE_FOR_THIS_CONTRACT"
UNMARKABLE_NO_ENTRY = "UNMARKABLE_NO_EXECUTABLE_ENTRY_RECORDED"
UNMARKABLE_NO_BOOK = "UNMARKABLE_NO_TWO_SIDED_BOOK_AT_THE_MARK"
UNMARKABLE_NO_COST = "UNMARKABLE_NO_ROUND_TRIP_RECORDED_FOR_THIS_LEG"
NO_CALLS_YET = "NO_RESEARCH_CALL_CAPTURED_IN_THIS_SESSION"

# Whether a count is the session's or the reader's bound. A selection that
# stopped at its own ceiling is a floor, and reporting it as a total is the
# fault the superseded 2026-09-17 tally was recorded with.
COUNT_IS_COMPLETE = "COUNT_IS_EVERY_LEG_THE_SELECTION_HOLDS"
COUNT_IS_A_FLOOR = (
    "COUNT_STOPPED_AT_THE_SELECTION_BOUND_SO_IT_IS_A_FLOOR_NOT_A_TOTAL"
)

# A tally written before the bound was stored on the row carries no way to tell
# a complete count from a floor. Recounting one needs its own selection identity
# or it collides with the very row it disagrees with and is dropped as a
# duplicate — which is how a count equal to the recorder's ceiling stayed on the
# record as though it were the session's.
RECOUNT = "RECOUNT_OF_A_TALLY_WHOSE_BOUND_WAS_NEVER_RECORDED"

# Which round trip a priced leg was charged. Published per leg and counted per
# column, because Phase 42's holdout was ruined by exactly this distinction
# going unrecorded across a chronological cut: a ratio whose denominator
# changed basis is two measurements wearing one name.
MEASURED_COST = "MEASURED_EXECUTABLE_ROUND_TRIP"
MODELLED_COST = "MODELLED_ROUND_TRIP_NO_MEASURED_BOOK_AT_THE_INSTANT"
NO_COST = "NO_ROUND_TRIP_RECORDED"

CALL_STATES: tuple[str, ...] = (
    MARKED,
    RESOLVED,
    UNMARKABLE_NO_LATER_QUOTE,
    UNMARKABLE_NO_ENTRY,
    UNMARKABLE_NO_BOOK,
    UNMARKABLE_NO_COST,
)

# The two columns. Named so neither can be mistaken for a strategy: one is the
# production board's own selected leg, the other is the research's admitted leg.
ARM_PRODUCTION = "PRODUCTION_BOARD_SELECTED_LEG"
ARM_RESEARCH = "RESEARCH_ADMITTED_LEG"

# A mark older than this is still shown, with its age, and flagged: a stale mark
# is a statement about the capture, and hiding it would make the session tally
# look fresher than the feed.
STALE_MARK_SEC = 300.0

# Below this many marked calls the session tally is printed with its sample and
# no comparison is drawn between the columns. A display rule — there is no
# promotion in this phase to gate.
MIN_MARKED_FOR_A_COMPARISON = 20
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE_FOR_A_COMPARISON"
COLUMNS_REPORTED = "COLUMNS_REPORTED_NO_CONCLUSION_DRAWN"

NOT_A_PROMOTION = (
    "RESEARCH_CALL_BOARD_PAPER_ONLY: these are paper legs opened at recorded "
    "executable prices and marked against measured quotes. A positive running "
    "net on this board is one sample of one definition over the sessions "
    "captured so far. It promotes nothing, arms no gate, changes no signal, "
    "places no order, and is not evidence that any call, vehicle or definition "
    "is profitable."
)
