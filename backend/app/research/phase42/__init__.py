"""Phase 42 — is the live entry-economics gate set at the right multiple?

**Why this phase exists, and what it is correcting.** Phase 35 §19 reported that
legs whose room was at least five times their round-trip cost returned +2.96%
against −2.27% for the 3-5x band, over 143,491 legs, monotone across four bands.
That was read here as evidence that the live gate — which refuses a fresh Flow
BUY unless the expected move is at least :data:`LIVE_MULTIPLE` times the leg's
own round trip — is set one band too loose. It is not evidence of that, and the
reason is the whole point of this phase.

§19 computes room as ``peak_pct / cost_pct``, and ``peak_pct`` is the *realised*
maximum of the leg's forward path: the best the market went after the entry was
taken. So "legs whose room was 5x their cost made money" says little more than
"legs that ran a long way in your favour made money". At the decision instant
that number does not exist. A gate cannot be set on it, and comparing it with
the live gate compares two different quantities — the live gate's numerator is a
*forecast* (the option's delta times the recent typical one-minute range of the
underlying), and §19's is an *outcome*.

**What this phase measures instead.** The same ratio the live gate actually
computes, reconstructed on the stored evidence for legs that were already
resolved, and then swept across candidate multiples. Every input to it is read
strictly at or before the decision instant, and that is enforced rather than
intended: :mod:`app.research.phase42.exante` is walked by the smoke for any
reference to an outcome field, so the class of mistake §19's table led to cannot
be repeated here silently.

**What it can and cannot conclude.** It can say whether a different multiple
would have admitted a better pool than :data:`LIVE_MULTIPLE` did, on days it was
not chosen on. It cannot say a multiple is profitable: a filter selects from the
pool the engine already produces, and every band of that pool measured so far is
negative after cost. Reducing a loss is not an edge, and this phase labels the
difference (:data:`LOSS_REDUCTION_ONLY`).

**Nothing here touches the order path.** No signal, entry, exit, target, stop,
strike, sizing or broker call is changed, and no threshold found here is wired
to anything. The live gate keeps the value in ``settings.flow_cost_multiple``
until a change survives on sessions it was not chosen on, and the decision to
make that change is not this phase's to take.
"""
from __future__ import annotations

from app.config import settings
from app.research.phase35 import (
    GENERAL_MIN_SESSIONS,
    GENERAL_MIN_TRADES,
    REQUIRES_MORE_DATA,
)

VERSION = "42.0"
PHASE = "PHASE42_EX_ANTE_ECONOMICS_GATE_SHADOW"

ARTEFACT_DIR = "data/phase42"
JSON_NAME = "phase42_gate_shadow.json"
MD_NAME = "EX_ANTE_GATE_SHADOW.md"
# The definition ledger, append-only and outside the raw store, for the same
# reason Phase 41 keeps one: a study that writes into the evidence it reads is
# not a study.
LEDGER_NAME = "definition_ledger.jsonl"

RESEARCH_ONLY = "RESEARCH_ONLY"
PAPER_ONLY = "PAPER_ONLY"
RECORD_ONLY = "RECORD_ONLY_NO_GATE_CHANGE_NO_ORDER_PATH"

# --- the frozen definition -------------------------------------------------
# The multiples swept. Declared before measuring, and the live value is read
# from settings rather than restated here: a copy of a threshold is a second
# threshold, and it would go on agreeing with the gate while the two drifted.
THRESHOLDS: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0)
LIVE_MULTIPLE: float = float(settings.flow_cost_multiple)
# How many minute-to-minute moves of the underlying the typical range is taken
# over, and the fewest that may stand in for it. The window is the live gate's
# own (``flow_expected_move_candles``); the floor is this phase's, because a
# median of one change is not a typical range and admitting it would let the
# first minutes of a session decide an arm.
RANGE_WINDOW: int = int(settings.flow_expected_move_candles)
MIN_RANGE_SAMPLES = 3
# A futures leg has no delta in the store because it needs none: the contract
# moves point for point with what it is written on.
FUTURES_DELTA = 1.0

# Floors, read from Phase 35 so one bar governs both phases.
MIN_ARM_LEGS = GENERAL_MIN_TRADES
MIN_SESSIONS = GENERAL_MIN_SESSIONS
# The fewest sessions that make a chronological split a split at all. Below it
# the holdout is not a holdout, and the phase says so instead of reporting one.
MIN_SESSIONS_FOR_SPLIT = 4
# A session counts toward that floor only if it carries this many legs whose
# economics could be measured. Counting swept sessions instead was wrong and
# hid it: a store whose first five sessions captured one side of the book has
# eight sessions and three days of evidence, and cutting it at 60% of eight put
# every measurable leg on the tested side while the choosing side was empty.
MIN_SESSION_LEGS = GENERAL_MIN_TRADES
# Where the chronological cut falls. Sessions are whole: a session is never
# divided between the side a threshold is chosen on and the side it is tested
# on, because both halves of one day share the same regime.
DEV_SHARE = 0.6

# --- how a leg's ratio was established -------------------------------------
EX_ANTE_MEASURED = "EX_ANTE_MEASURED"
# The gate's own stance on missing evidence, kept deliberately: a leg whose
# economics cannot be measured is NOT refused by the live gate, it is reported
# as unmeasured. An arm that refused them would be a different gate.
EX_ANTE_UNMEASURED = "EX_ANTE_UNMEASURED"
NO_DELTA = "NO_DELTA_AT_DECISION_INSTANT"
NO_RANGE = "NO_TRAILING_RANGE_BEFORE_DECISION_INSTANT"
NO_COST = "NO_MEASURED_ROUND_TRIP_COST"

# Where the trailing range came from. Neither is the live gate's input exactly,
# and the difference is disclosed rather than smoothed over: the live gate takes
# the median *high-low span* of captured one-minute futures bars, while the
# store keeps point observations, so this reconstructs the median absolute
# change between consecutive minutes. The two agree in scale, not in value.
RANGE_FROM_UNDERLYING = "UNDERLYING_AT_OPTION_QUOTE_MINUTE_CHANGE"
RANGE_FROM_FUTURES = "FUTURES_TRADED_PRICE_MINUTE_CHANGE"
RANGE_PROXY = (
    "TRAILING_RANGE_IS_A_PROXY: the live gate medians the high-low span of "
    "captured one-minute bars; the raw store keeps point observations, so this "
    "medians the absolute change between consecutive minutes instead. It is "
    "the same quantity in scale and construction but not the same number, so "
    "an arm's multiple is comparable across arms and only approximately "
    "comparable with the live setting."
)

# --- how the legs on each side of the cut were costed -----------------------
# The ratio divides by the leg's round-trip cost, so a change in how that cost
# was established changes the ratio without changing this phase's definition.
# It happened on the first store that reached four gradeable sessions: the
# earlier sessions were costed by a modelled spread, the later ones from a
# measured lot, and the chronological cut fell exactly between the two. Choosing
# an arm on one cost basis and scoring it on another is not a holdout, so the
# composition of each half is reported and a cut that separates them is refused
# as a test rather than labelled.
UNKNOWN_BASIS = "COST_BASIS_NOT_RECORDED"
BASIS_CONSISTENT = "COST_BASIS_CONSISTENT_ACROSS_THE_CUT"
BASIS_CHANGED = "COST_BASIS_CHANGED_ACROSS_THE_CUT"
BASIS_NOTE = (
    "COST_BASIS_CHANGED_ACROSS_THE_CUT: the legs the multiple was chosen on "
    "and the legs it was scored on had their round trip established in "
    "different ways ({dev} vs {holdout}). The ratio divides by that cost, so "
    "the two halves are not measuring the same quantity and the difference "
    "between them is not attributable to the multiple."
)

# --- what an arm concluded --------------------------------------------------
# A filter chooses from the pool the engine produced. It cannot manufacture a
# winner, so these labels distinguish "lost less" from "made money", which is
# the distinction every earlier band table in this project blurred.
LOSS_REDUCTION_ONLY = "LOSS_REDUCTION_ONLY_STILL_NEGATIVE_AFTER_COST"
POSITIVE_IN_HOLDOUT = "POSITIVE_AFTER_COST_IN_HOLDOUT"
NO_BETTER_THAN_LIVE = "NO_BETTER_THAN_THE_LIVE_MULTIPLE"
BETTER_THAN_LIVE = "BETTER_THAN_THE_LIVE_MULTIPLE_IN_HOLDOUT"
INSUFFICIENT = REQUIRES_MORE_DATA

# What this phase refuses to become.
NOT_A_PROMOTION = (
    "RECORD_ONLY_NOT_A_PROMOTION: this sweeps a gate the engine already "
    "applies across other values of its own multiple. It changes no gate, "
    "proposes no entry, tests no exit, and is not evidence that any strategy "
    "or any multiple is profitable."
)
# The multiple-testing note. Seven arms are measured, so the best of them is the
# maximum of seven correlated statistics and is biased upward by selection even
# when nothing separates them.
ARMS_TESTED_NOTE = (
    "SELECTION_BIAS_ACROSS_ARMS: {n} multiples are measured on the same legs, "
    "so the best arm in a sample is the maximum of {n} correlated figures and "
    "is optimistic by construction. Only the chronological holdout figure, "
    "where the arm was chosen without seeing those sessions, is read as a test."
)
