"""Phase 36 — CRUDEOIL vehicle selection, on real executable books only.

One question, and it is not a new strategy: *when a genuine opportunity occurs,
which vehicle actually makes the most NET money — FUTURES, CE, PE, or standing
aside?* Nothing here predicts anything. The signal, the gate, the strike rule,
the target, the stop, the exit and the order path are untouched; this package
reads what was already captured and prices the same instant three ways.

Why CRUDEOIL first, and why that is not cherry-picking: it is the only
instrument whose capture currently holds *simultaneous* CE, PE and FUTURES
two-sided books in quantity (~8,700 of each in one session, spreads 0.4-1.1 on
premium and 1-3 on the future). A vehicle comparison needs all three quoted at
the same instant, and NIFTY does not yet have that. The framework is
instrument-agnostic on purpose so the same command answers NIFTY when NIFTY's
triples exist — but §"Do not generalize from CrudeOil without evidence" is
enforced by the report refusing to speak about any instrument it did not measure.

Three rules inherited from Phase 35 and re-stated because this phase is where
they would be most tempting to bend:

**A vehicle is comparable only at the same timestamp.** A triple is built from
one decision instant. Comparing the future at 10:04 with the call at 10:09 would
be comparing two different opportunities and calling one of them a mistake.

**The winning vehicle may never be chosen with hindsight.** The vehicle a rule
selects must be decidable from the entry instant alone. Picking per-observation
whichever vehicle turned out best produces a beautiful table that no one can
trade, which is exactly what §3 and §22 forbid.

**Spread is charged once.** On executable fills it is already inside ask-in and
bid-out; only a traded-price fill has a spread modelled on top, and then the row
is labelled so. This is enforced in :mod:`app.research.phase35.book`, which this
phase reuses rather than reimplements — a second cost model would eventually
disagree with the first, and the disagreement would look like a finding.

What this phase can conclude is bounded by §24: the first success condition is
that the comparison is possible without contamination, not that anything is
profitable. A single session cannot produce an out-of-sample vehicle rule, and
the verdict vocabulary has no VALIDATED value to give it.
"""
from __future__ import annotations

from app.research.phase35 import (
    HORIZONS,
    SESSION_CLOSE,
)

VERSION = "36.0"

# The instrument this study was commissioned for. Not a restriction in code —
# every function takes the instrument as an argument — but the default, so a
# run that forgets the flag measures the thing that has the evidence.
DEFAULT_INSTRUMENT = "CRUDEOIL"

# §6 — identical holding horizons for every vehicle, reused from Phase 35 rather
# than redeclared, so a Phase 36 table can be laid beside a Phase 35 one.
COMPARISON_HORIZONS: tuple[int, ...] = HORIZONS
CLOSE = SESSION_CLOSE

# ---------------------------------------------------------------------------
# §3/§9 — how a vehicle relates to the opportunity's direction.
#
# DIRECTIONAL is the expression a system would actually take: long futures or a
# long call on a LONG view, short futures or a long put on a SHORT view.
# COUNTERFACTUAL is the other option side, priced at the same instant and
# reported separately (§9). It is never allowed to win the comparison, because
# "the put would have paid on a long signal" is a statement about the direction
# being wrong, not about vehicle selection.
# ---------------------------------------------------------------------------
DIRECTIONAL = "DIRECTIONAL"
COUNTERFACTUAL = "COUNTERFACTUAL"
ROLES: tuple[str, ...] = (DIRECTIONAL, COUNTERFACTUAL)

# §12 — moneyness buckets, by strikes away from the captured ATM. Frozen before
# any outcome was looked at. "steps" are strike intervals, not rupees, so the
# bucketing survives an instrument with a different strike grid.
ATM = "ATM"
OTM_1 = "OTM_1"
OTM_2 = "OTM_2"
OTM_DEEP = "OTM_DEEP"
ITM = "ITM"
MONEYNESS: tuple[str, ...] = (ITM, ATM, OTM_1, OTM_2, OTM_DEEP)

# §10 — premium bands, exactly as the task states them. Deliberately finer than
# Phase 35's three bands because Phase 34 found the cost problem concentrated
# below ₹25 and the task asks for the ₹25-50 and ₹50-100 split explicitly.
PREMIUM_BANDS: tuple[tuple[str, float, float], ...] = (
    ("LT_25", 0.0, 25.0),
    ("25_50", 25.0, 50.0),
    ("50_100", 50.0, 100.0),
    ("100_250", 100.0, 250.0),
    ("GT_250", 250.0, float("inf")),
)

# §11 — DTE bands.
DTE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("EXPIRY_DAY", 0, 0),
    ("DTE_1", 1, 1),
    ("DTE_2_3", 2, 3),
    ("DTE_4_7", 4, 7),
    ("DTE_GT_7", 8, 10_000),
)

# §17 — cost as a fraction of the expected move. The bands are the task's, and
# the "expected move" is the leg's own measured MFE at the horizon rather than a
# forecast: a predicted expected move would smuggle a model into a diagnostic.
COST_MOVE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("LT_25PCT", 0.0, 25.0),
    ("25_50PCT", 25.0, 50.0),
    ("50_75PCT", 50.0, 75.0),
    ("75_100PCT", 75.0, 100.0),
    ("GE_100PCT", 100.0, float("inf")),
)

# §13 — time-of-day buckets in IST minutes-from-midnight. MCX runs 09:00-23:30,
# NFO 09:15-15:30; the buckets are defined as fractions of the *instrument's
# own* captured session in :mod:`app.research.phase36.tables`, so one hard-coded
# clock cannot mislabel the other exchange's afternoon.
TOD_OPENING = "OPENING"
TOD_MORNING = "MORNING"
TOD_MIDDAY = "MIDDAY"
TOD_AFTERNOON = "AFTERNOON"
TOD_LATE = "LATE"
TIME_OF_DAY: tuple[str, ...] = (
    TOD_OPENING, TOD_MORNING, TOD_MIDDAY, TOD_AFTERNOON, TOD_LATE,
)
# The opening bucket is a fixed wall-clock length, not a fraction: "the first
# thirty minutes" is a claim about auction mechanics and does not stretch just
# because MCX trades for fourteen hours.
OPENING_MINUTES = 30.0

# §15 — entry-timing counterfactuals, in minutes after the decision instant.
# CONFIRMATION is not a time: it is "the first later quote at which the leg was
# not already adverse", which is the closest thing to "after confirmation" that
# can be measured without inventing a confirmation rule.
ENTRY_OFFSETS_MIN: tuple[float, ...] = (0.0, 1.0, 2.0)
ENTRY_CONFIRMATION = "AFTER_CONFIRMATION"

# §14 — engine attribution. Narrower than Phase 35's taxonomy on purpose: this
# phase only separates the vehicle question from the direction question.
ENGINE_SELECTED = "ENGINE_SELECTED"
ENGINE_MISSED = "ENGINE_MISSED"
WRONG_VEHICLE = "WRONG_VEHICLE"
WRONG_DIRECTION = "WRONG_DIRECTION"
VEHICLE_OK = "VEHICLE_CHOICE_OK"
NO_VEHICLE_PROFITABLE = "NO_VEHICLE_PROFITABLE"
INSUFFICIENT = "INSUFFICIENT_DATA"

# §20 — stress grid. Cost multiples apply to the whole round trip; slippage is
# added as extra points on top, in ticks, because on an executable fill the
# measured spread is already paid and the honest stress is "what if I did worse
# than the quoted side".
COST_MULTIPLES: tuple[float, ...] = (1.0, 1.5, 2.0)
SLIPPAGE_MULTIPLES: tuple[float, ...] = (1.0, 2.0, 3.0)

# §21 — outlier removal.
OUTLIER_TRIMS_PCT: tuple[float, ...] = (0.0, 1.0, 5.0)

# §19 — chronological split, by session date. Never by row index: rows are not
# evenly spread through a session and an index split would put the same minute
# on both sides of the boundary.
DEV_FRACTION = 0.6
VALIDATION_FRACTION = 0.2
# The remainder is the untouched holdout, evaluated once.
DEV = "DEVELOPMENT"
VALIDATION = "VALIDATION"
HOLDOUT = "UNTOUCHED_HOLDOUT"
WALK_FORWARD = "WALK_FORWARD"
SPLITS: tuple[str, ...] = (DEV, VALIDATION, HOLDOUT)

# §16/§8 — evidence floors for this study. A vehicle row below the floor is
# reported with its sample and refused a comparison verdict rather than ranked.
MIN_TRIPLES_PER_VEHICLE = 30      # to print a comparison row at all
MIN_TRIPLES_FOR_LEAD = 100        # to call anything a research lead
MIN_SESSIONS_FOR_LEAD = 5         # a one-session gap is not a lead
MIN_HOLDOUT_TRIPLES = 30

# §26 — the only three things this study may conclude. There is deliberately no
# VALIDATED member: §26 forbids it from a first study, so the vocabulary cannot
# express it.
LEAD = "VEHICLE_SELECTION_RESEARCH_LEAD"
NEEDS_DATA = "VEHICLE_SELECTION_REQUIRES_MORE_DATA"
NO_ADVANTAGE = "NO_VEHICLE_ADVANTAGE_FOUND"
VERDICTS: tuple[str, ...] = (LEAD, NEEDS_DATA, NO_ADVANTAGE)

# §18 — the placebo. Seeded so the control is reproducible: an unseeded placebo
# that happens to look bad is not evidence that the signal is good.
PLACEBO_SEED = 36_000
PLACEBO_DRAWS_PER_SESSION = 200
PLACEBO = "RANDOM_ENTRY_PLACEBO"

RESEARCH_ONLY = "RESEARCH_ONLY"
PAPER_ONLY = "PAPER_ONLY"
ARTEFACT_DIR = "data/phase36"
