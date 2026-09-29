"""Phase 39 — cost-to-edge feasibility, read-only, over the captured store.

One question, and it is narrower than every phase before it: **can this
instrument, expressed through this vehicle, move far enough in a session to pay
for its own execution?** Nothing here predicts, selects, ranks or recommends a
trade. There is no signal, no threshold fitted to an outcome, no strategy and no
promotion vocabulary. The output is a triage list: which instrument/vehicle
pairs are *arithmetically capable* of covering their round trip, and therefore
worth spending further paper sessions on.

Why the question is worth its own phase. Phase 36 answered "which vehicle made
the most net money on CRUDEOIL" with "none of them, on one session", and the
observation floor puts a real answer ~60 sessions away *per instrument*. That is
a schedule, not a plan. This phase answers a strictly easier question that needs
no further capture at all, because the required move is decided by the cost
model and the movement distribution is already on disk.

Four rules, and they are what keep this honest.

**The threshold is arithmetic, never fitted.** The required move is
``spread + brokerage + statutory charges`` on a break-even round trip, computed
from the instrument's own measured book. No outcome is consulted to choose it,
so :data:`MOVE_OVER_COST` cannot be inflated by picking a favourable cut. The
reference horizon is likewise pre-declared here (:data:`REFERENCE_HORIZON`) and
is the one Phase 36 already used; the per-instrument best horizon is reported but
explicitly excluded from ranking and labelling, because choosing it per row from
the results is exactly the look-ahead §"do not use future outcomes" forbids.

**The movement side is a ceiling, not an expectation.** Favourable excursion
(MFE) is the best exit available *with hindsight*. So a ratio above 1 means only
that a perfect exit could have paid the costs, while a ratio below 1 means even a
perfect exit could not. The two conclusions are therefore not symmetric, and the
report says so in the one place a reader might forget it: a
:data:`CANNOT_PAY_ITS_COSTS` row is a strong negative finding, and a
:data:`CLEARS_THE_EXISTING_3X_GATE` row is only permission to keep measuring.

**Mid is a yardstick, never a fill.** Movement is measured mid-to-mid and the
whole spread is then charged as cost. That is the only decomposition in which
"the market moved X" and "execution cost Y" are separate numbers rather than one
netted figure — and because the spread is charged in full, it cannot flatter
anything. No paper fill anywhere in this package is priced at the mid; §10's
reconciliation prices the same legs the Phase 36 way (ask-in, bid-out) and
reports the residual between the two frames instead of asserting they agree.

**Capability is not edge.** The labels below describe cost coverage only. Every
artefact carries :data:`NOT_AN_EDGE`, and the vocabulary contains no VALIDATED
and no PRODUCTION_READY value, so the report cannot express a promotion even by
accident.
"""
from __future__ import annotations

from app.research.phase35 import HORIZONS, SESSION_CLOSE

VERSION = "39.0"
PHASE = "PHASE39_COST_TO_EDGE_FEASIBILITY"

ARTEFACT_DIR = "data/phase39"
MD_NAME = "COST_TO_EDGE_FEASIBILITY.md"
JSON_NAME = "COST_TO_EDGE_FEASIBILITY.json"

# The horizons movement is measured at. Reused from Phase 35 rather than
# redeclared so a row here can be laid beside a Phase 35/36/38 row.
FEASIBILITY_HORIZONS: tuple[int, ...] = HORIZONS
CLOSE = SESSION_CLOSE

# Pre-declared, before any ratio was computed, and equal to the horizon Phase 36
# already reports at. Ranking and labelling happen here and nowhere else. The
# best horizon per instrument is still reported — as a sensitivity, flagged
# ``used_for_ranking: false`` — because a ratio that only exists at one hold is
# a fact a reader should see, not one the ranking should exploit.
REFERENCE_HORIZON = "30"

# The multiple the live flow gate already requires of an expected move against
# its round trip. Not chosen here: it is imported as an existing frozen number
# so this report describes that gate rather than inventing a competing one.
GATE_MULTIPLE = 3.0

# Reach rates are reported at these multiples of the required move. Descriptive
# only — no multiple is selected as "the" threshold by anything downstream.
REACH_MULTIPLES: tuple[float, ...] = (1.0, 2.0, GATE_MULTIPLE)

# §9 — what the ranking looks like when the round trip costs more than measured.
# Slippage on top of an executable side, expressed as a multiple of the whole
# round trip, matching Phase 36's stress grid.
STRESS_COST_MULTIPLES: tuple[float, ...] = (1.0, 1.5, 2.0)

# ---------------------------------------------------------------------------
# Capability labels. Bands are on MOVE/COST at the reference horizon and were
# written down before the first run. They describe cost coverage and nothing
# else: none of them is an edge, a signal, a recommendation or a size.
# ---------------------------------------------------------------------------
CANNOT_PAY = "CANNOT_PAY_ITS_COSTS"
BARELY_PAYS = "BARELY_PAYS_ITS_COSTS"
PAYS = "PAYS_ITS_COSTS"
CLEARS_GATE = "CLEARS_THE_EXISTING_3X_GATE"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
UNMEASURED = "UNMEASURED_NO_EXECUTABLE_BOOK"
CAPABILITY_LABELS: tuple[str, ...] = (
    CANNOT_PAY, BARELY_PAYS, PAYS, CLEARS_GATE, INSUFFICIENT, UNMEASURED,
)
# (label, lower bound inclusive, upper bound exclusive)
CAPABILITY_BANDS: tuple[tuple[str, float, float], ...] = (
    (CANNOT_PAY, 0.0, 1.0),
    (BARELY_PAYS, 1.0, 2.0),
    (PAYS, 2.0, GATE_MULTIPLE),
    (CLEARS_GATE, GATE_MULTIPLE, float("inf")),
)

# Sample floors. Below the first, a pair gets no label at all; below the second
# it is measured and printed but kept out of the ranking, because a ratio from
# forty instants of one session is a description of forty instants.
MIN_INSTANTS_FOR_LABEL = 30
MIN_INSTANTS_FOR_RANK = 100

# Throughput bounds, deterministic and declared. Sampling is by fixed stride
# over time-ordered rows — never random, never "the liquid part of the day" —
# so a re-run over the same store returns the same rows, and a sampled table can
# be checked against the full-coverage counts printed beside it.
MAX_COST_SAMPLES_PER_KEY = 5000
MAX_MOVE_INSTANTS_PER_SESSION_KEY = 200

# The statutory charge is a percentage of turnover, so the cost of a round trip
# depends slightly on the exit price, which depends on the required move. Solved
# by iteration rather than ignored: charging at entry-level turnover understates
# the requirement for a winning exit, which is the direction that flatters.
COST_FIXED_POINT_ITERATIONS = 3

# Stamped on every artefact and every payload.
RESEARCH_ONLY = "RESEARCH_ONLY"
PAPER_ONLY = "PAPER_ONLY"
READ_ONLY = "READ_ONLY_NO_CAPTURE_NO_PRODUCTION_CHANGE"
NOT_AN_EDGE = (
    "CAPABILITY_ONLY_NOT_AN_EDGE: this measures whether typical movement "
    "covers execution cost. It is not a signal, a forecast, or evidence that "
    "any strategy is profitable."
)
