"""Phase 40 — favourable-first vs adverse-first, read-only, on stored paths.

Phase 39 established that CRUDEOIL's movement is large relative to its round
trip, and its §7 reconciliation showed the same legs ending *negative* at the
same horizon. Both are true: the move that covered the cost existed, and it was
not there at the end. This phase answers the one question that distinguishes the
two possible causes, and answers nothing else:

**Did the cost-clearing favourable move arrive before or after an equally large
adverse move?**

If it arrived first and was then lost, the mechanism to fix is the exit. If the
adverse move arrived first, the mechanism to fix is entry or direction. No exit
variant is tested here, no threshold is fitted, no strategy is proposed, and
nothing in production is touched.

**The race is run on executable prices, on one side of the book at both ends.**
The threshold is Phase 39's :data:`COST_CLEARING_MOVE` — ``spread + brokerage +
statutory`` — and the adverse threshold is the same distance the other way. The
origin is where the leg could have been closed at the entry instant and each
later sample is where it could have been closed then: bid-to-bid for a long,
ask-to-ask for a short future. No midpoint, LTP, nearest timestamp, interpolated
or synthetic price is read anywhere in the walk.

That origin corrects an error in the first version of this phase, and the
correction reversed its answer. The threshold already contains the quoted width,
so racing from the entry *fill* put the width inside the movement as well: a
book that never moved at all stood a full spread down and tripped the adverse
threshold on its first quote, which manufactured adverse-first legs out of flat
markets and did it worst on the widest books. Exit side to exit side charges the
width once, in the threshold, where Phase 39 put it. The money columns are still
measured from the fill, because that is the price that was paid.

**A quote is one book, so there is no intrabar ambiguity.** The walk steps
through captured quotes, not candles, so it never has to guess whether the high
or the low came first inside a bar — the classic way a first-event study
flatters itself. Where quotes are missing, the window is marked
:data:`UNCOVERED_WINDOW` and excluded from the shares rather than silently
counted as "neither reached".

**Overlapping legs are not independent observations.** Six hundred sampled
instants from one session, each walked forward thirty minutes, mostly describe
the same few price swings. Every share is therefore also computed on a
non-overlapping subsample (:func:`app.research.phase40.firstevent.independent`)
and the two are printed side by side; where they disagree, the dependent figure
is the one to distrust.

**The verdict is a comparison of measured shares, not a cut-off.** §13's label
comes from which share is larger and whether the difference survives a declared
two-sided normal test at 1.96 — no percentage is invented to define
"materially", and the sample-dependence caveat above applies to that test too.
"""
from __future__ import annotations

from app.research.phase35 import SESSION_CLOSE
from app.research.phase39 import (
    MIN_INSTANTS_FOR_LABEL,
    REFERENCE_HORIZON,
)

VERSION = "40.0"
PHASE = "PHASE40_FAVOURABLE_ADVERSE_FIRST_EVENT"

ARTEFACT_DIR = "data/phase40"
MD_NAME = "PHASE39_FAVOURABLE_ADVERSE_DIAGNOSTIC.md"
JSON_NAME = "phase39_favorable_adverse.json"

# §3. The holds the diagnostic is evaluated at, plus the session close. Declared
# here before the first run and not chosen from any result: a longer window
# cannot contain a smaller one's excursion, so a "best" hold read off these
# columns would be an artefact of the window, not a finding.
FIRST_EVENT_HORIZONS: tuple[int, ...] = (5, 10, 15, 20, 30, 45, 60, 90, 120)
CLOSE = SESSION_CLOSE

# Reused from Phase 39 rather than redeclared, so a row here can be laid beside
# a Phase 39 row without asking which hold either was measured at.
REFERENCE = REFERENCE_HORIZON

# §5 — the three outcomes of the race, and the fourth state that is not an
# outcome: a window the store could not cover cannot be scored either way.
FAVOURABLE_FIRST = "FAVOURABLE_FIRST"
ADVERSE_FIRST = "ADVERSE_FIRST"
NEITHER_REACHED = "NEITHER_REACHED"
UNCOVERED_WINDOW = "UNCOVERED_WINDOW"
FIRST_EVENTS: tuple[str, ...] = (
    FAVOURABLE_FIRST, ADVERSE_FIRST, NEITHER_REACHED, UNCOVERED_WINDOW,
)

# §7 — the split inside favourable-first, by the sign of the net at the horizon.
# The money either survived its own costs or it did not; that is a measured sign
# rather than a chosen giveback percentage.
RETAINED = "FAVOURABLE_FIRST_AND_RETAINED"
GIVEN_BACK = "FAVOURABLE_FIRST_BUT_GIVEN_BACK"

# §8 — the split inside adverse-first, by whether the favourable threshold was
# reached later in the same window.
RECOVERED = "ADVERSE_FIRST_THEN_FAVOURABLE"
STAYED_ADVERSE = "ADVERSE_FIRST_NEVER_FAVOURABLE"

# §10 — cohorts, taken from the observation's own recorded fields.
ENGINE = "ENGINE_SELECTED"
BOARD = "BOARD_ONLY"

# §13 — the four verdicts. There is no fifth, and none of them promotes,
# validates or enables anything.
EXIT_DOMINANT = "EXIT / GIVEBACK"
ENTRY_DOMINANT = "ENTRY / DIRECTION"
MIXED = "MIXED"
INSUFFICIENT = "INSUFFICIENT_DATA"
VERDICTS: tuple[str, ...] = (
    EXIT_DOMINANT, ENTRY_DOMINANT, MIXED, INSUFFICIENT,
)

# The two-sided normal deviate at which a difference between two measured shares
# is called material. Declared, conventional, and not tuned: 1.96 is the 5%
# two-sided point and was written down before the first run. It is applied to
# counts that are *not* independent, which is stated wherever it is used.
MATERIALITY_Z = 1.96

# Below this many classified legs the verdict is INSUFFICIENT_DATA. Reused from
# Phase 39's label floor so the two phases refuse to speak at the same point.
MIN_CLASSIFIED_FOR_VERDICT = MIN_INSTANTS_FOR_LABEL

UNMEASURED = "UNMEASURED"
# Why an instrument named in §9 has no numbers. Kept apart because "never
# captured" and "captured and unpriceable" call for different next steps.
NOT_IN_STORE = "NOT_IN_STORE"
NO_RACEABLE_LEG = "IN_STORE_BUT_NO_LEG_COULD_BE_RACED"

RESEARCH_ONLY = "RESEARCH_ONLY"
PAPER_ONLY = "PAPER_ONLY"
READ_ONLY = "READ_ONLY_NO_CAPTURE_NO_PRODUCTION_CHANGE"
NOT_A_STRATEGY = (
    "DIAGNOSTIC_ONLY_NOT_A_STRATEGY: this locates where a measured loss "
    "happened in time. It tests no exit rule, proposes no entry, and is not "
    "evidence that any strategy is profitable."
)
