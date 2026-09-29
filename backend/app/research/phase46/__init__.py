"""Phase 46 — the research overlay on the Option Signal Board.

**What this is.** A second column beside the production call. For every signal
the board already prints, it says what the research evidence looked like at
that same decision instant: the executable book, the round trip it would have
paid, the expected move over that cost, and one word for how the research view
stood relative to the production one. Nothing more.

**What it is not.** It is not a filter, not a second signal and not a
recommendation. The production board is unchanged: Phase 46 is downstream of a
decision that has already been taken, published and acted on by whatever the
engine does, and there is no return value from this package that any
production caller reads. :mod:`_smoke_phase46` walks the import graph and fails
if an order, a broker or an execution module is reachable from here.

**Why it computes nothing of its own.** Every number on the overlay comes from
the Phase 45 row for that instant — the same row, already journalled, not a
recomputation. A copied formula is a second formula: it agrees with its parent
until one of them is corrected. So this package classifies and records; the
arithmetic stays where it was written and where it is fingerprinted.

**Why a state and not a score.** A number invites ranking and ranking invites
promotion. The overlay publishes one of eight words, each of which names a
reason rather than a quality, and none of which says an instant was good. In
particular :data:`SUPPORTED` means the research definition would also have
admitted the instant — not that the trade made money, which is unknown at the
instant and is recorded in a different table by a different writer.

**No hindsight, structurally.** The classifier's whole input is one Phase 45
decision-instant row. Outcomes live in ``paper_outcome``, are written by the
Phase 45 resolver, and are read only by :mod:`app.research.phase46.compare`,
which nothing in the admission path imports. The smoke parses the classifier
for any mention of an outcome field, because that mistake has already been made
once in this project and the resulting table looked like an edge.
"""
from __future__ import annotations

VERSION = "46.0"
PHASE = "PHASE46_RESEARCH_OVERLAY_ON_THE_PRODUCTION_SIGNAL_BOARD"

ARTEFACT_DIR = "phase46"
DB_NAME = "phase46_overlay.db"
MD_NAME = "RESEARCH_OVERLAY.md"

# --- what this layer is, stated on every row and every payload -------------
RESEARCH_OVERLAY = "RESEARCH_OVERLAY"
SHADOW = "SHADOW"
PAPER_ONLY = "PAPER_ONLY"
NO_ORDER_PATH = "NO_ORDER_PATH_NO_BROKER_NO_REAL_MONEY"
PRODUCTION_UNCHANGED = "PRODUCTION_SIGNAL_UNCHANGED_BY_THIS_LAYER"
NOT_A_PROMOTION = (
    "RESEARCH_OVERLAY_SHADOW_PAPER_ONLY: this is what the research evidence "
    "looked like at an instant the production board already handled its own "
    "way. It changes no signal, arms no gate, filters nothing, places no "
    "order, and is not evidence that any vehicle, instant or definition is "
    "profitable."
)

# --- the eight overlay states ---------------------------------------------
# Each names why the overlay stands where it does, so a column of these is
# still usable three months later. None of them ranks an instant: a word that
# meant "good" would be a recommendation wearing a research label.
#
# SUPPORTED            measured, and the research definition would also have
#                      admitted this vehicle at this instant
# DISAGREES            measured, and the vehicle opposes the direction the
#                      production read took
# COST_BLOCKED         measured, and the expected move did not clear the round
#                      trip the book would actually have charged
# WATCH                measured, and the research definition neither supports
#                      nor contradicts the production call at this instant
# UNMEASURED           the book at this instant cannot price the vehicle
# STALE                a quote existed but was too old to price a fill with
# CAPTURE_GAP          the capture itself has a hole here
# NO_RESEARCH_EVIDENCE no research row exists for this instant at all
SUPPORTED = "SUPPORTED"
DISAGREES = "DISAGREES"
COST_BLOCKED = "COST_BLOCKED"
WATCH = "WATCH"
UNMEASURED = "UNMEASURED"
STALE = "STALE"
CAPTURE_GAP = "CAPTURE_GAP"
NO_RESEARCH_EVIDENCE = "NO_RESEARCH_EVIDENCE"
STATES: tuple[str, ...] = (
    SUPPORTED, DISAGREES, COST_BLOCKED, WATCH,
    UNMEASURED, STALE, CAPTURE_GAP, NO_RESEARCH_EVIDENCE,
)
# The states that mean "the evidence could not speak", kept as a set because
# the difference between a silent overlay and a negative one is the whole
# reason the vocabulary has eight words rather than three.
SILENT: frozenset[str] = frozenset({
    UNMEASURED, STALE, CAPTURE_GAP, NO_RESEARCH_EVIDENCE,
})

# --- why an overlay state was reached -------------------------------------
# The Phase 45 reason is carried through verbatim; these are this layer's own
# additions, for the cases Phase 45 has no opinion about.
NO_SHADOW_ROW = "NO_SHADOW_ROW_FOR_THIS_DECISION_INSTANT"
RESEARCH_WOULD_ADMIT = "RESEARCH_DEFINITION_WOULD_ALSO_ADMIT_THIS_INSTANT"
PRODUCTION_DID_NOT_CALL_IT = (
    "RESEARCH_DEFINITION_WOULD_ADMIT_BUT_PRODUCTION_DID_NOT_CALL_A_BUY"
)
NOT_A_FRESH_CANDIDATE = "PRODUCTION_INSTANT_IS_NOT_A_FRESH_BUY_CANDIDATE"
OVERLAY_REASONS: tuple[str, ...] = (
    NO_SHADOW_ROW, RESEARCH_WOULD_ADMIT, PRODUCTION_DID_NOT_CALL_IT,
    NOT_A_FRESH_CANDIDATE,
)

# --- production signal vocabulary, read and never written ------------------
# The production call as Phase 17 recorded it at the instant. Normalised for
# comparison only; the raw string is kept on the row beside it, because a
# normalisation that loses the original is a normalisation nobody can audit.
PRODUCTION_BUY = "BUY"
PRODUCTION_WAIT = "WAIT"
PRODUCTION_AVOID = "AVOID"
PRODUCTION_NONE = "NO_SIGNAL"

# --- the two arms of the outcome comparison -------------------------------
ARM_SIGNAL_ONLY = "A_CURRENT_SIGNAL_ALONE"
ARM_SIGNAL_PLUS_OVERLAY = "B_CURRENT_SIGNAL_PLUS_RESEARCH_OVERLAY"
ARMS: tuple[str, str] = (ARM_SIGNAL_ONLY, ARM_SIGNAL_PLUS_OVERLAY)

# A display label, not a gate: below this many resolved paper legs an arm's
# metrics are printed with the sample stated and no comparison is drawn. It
# promotes nothing at any sample size — there is no promotion in this phase to
# reach — and it is deliberately not one of the pre-committed research floors,
# which belong to the phases that own them.
MIN_RESOLVED_FOR_A_COMPARISON = 30
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE_FOR_A_COMPARISON"
SHARED_LEGS = (
    "ARM_B_IS_A_SUBSET_OF_ARM_A_NOT_AN_INDEPENDENT_SAMPLE: the same paper legs "
    "appear in both arms, so the difference between them is a description of "
    "which legs the overlay would have stood beside, not a test of two "
    "strategies against each other."
)

# --- fields that may never be read while classifying ----------------------
# Parsed by the smoke. Naming them here rather than in prose is what makes the
# "no hindsight" claim checkable rather than a promise.
OUTCOME_FIELDS: tuple[str, ...] = (
    "mfe_pct", "mae_pct", "giveback_pct", "peak_pct", "exit_price", "exit_ts",
    "exit_side", "net_pnl", "net_pnl_points", "net_pct", "gross_pnl",
    "gross_pct", "realized", "t1_hit", "t2_hit", "t3_hit", "hold_minutes",
    "outcome_status", "resolved_ts",
)
