"""Market-wide parallel opportunity discovery (RESEARCH ONLY, PAPER ONLY).

This package replaces a serial habit, not a piece of code. Everything before it
asked *does this one mechanism work on this one instrument*, waited for the
answer, and only then wrote the next question. Here many pre-registered
candidates observe the same sessions at once, cheap historical filters remove
the hopeless ones before they consume live capture, and the survivors are
ranked across whatever the market actually offers on the day.

Four things are deliberately harder here than they were serially, and each one
is a place where a wide search quietly manufactures an edge:

**Multiplicity is the main enemy.** Testing one candidate at the 5% level is a
5% chance of a false positive. Testing four hundred is a near-certainty of
twenty of them, which is more than the number of real edges this market is
likely to contain. So the count of hypotheses is carried in the report, the
Benjamini-Hochberg cut is applied over the whole family rather than per
candidate, and a candidate that clears the raw bar but not the corrected one is
labelled :data:`UNCORRECTED_ONLY`. A wide funnel without this is a machine for
producing plausible nonsense.

**A wide universe is mostly unmeasurable, and must say so.** Five years of
one-minute history exist for NIFTY and CRUDEOIL. For the rest of the requested
universe there is nothing to screen on, so a candidate scoped to it returns
:data:`NO_HISTORY` — not a neutral result, not a small sample, an absence. The
same holds for execution: candle history carries no bid or ask, so anything
needing an executable price on it is :data:`UNMEASURED`. The funnel reports what
it could not look at with the same prominence as what it could.

**Fast elimination must be mechanical, or it becomes selection.** Killing a
candidate early is right when it is structurally broken — unexecutable spread,
no measurable opportunities, look-ahead, catastrophic after costs. Killing one
because it is *down* after fifteen trades discards the unlucky along with the
bad, and does it in favour of whatever started well. So the early-kill rules in
:mod:`app.research.opportunity.screen` are pre-registered and none of them reads
"currently losing".

**Parallel does not mean interacting.** Candidates observe the same
observations and never each other: no candidate's admission may depend on
another's state, its outcome, or the current champion. Otherwise the whole set
becomes one adaptive strategy fitted in flight, with none of the evidence a
single frozen definition would have.

Nothing here writes an order, touches the production signal path, or changes
phases 41 to 44. The strongest verdict available is
:data:`PROMOTED_TO_PRODUCTION_PAPER`, which is still paper.
"""
from __future__ import annotations

VERSION = "OPPORTUNITY_V1"
SCHEMA_VERSION = "opportunity.v1"

# ---------------------------------------------------------------------------
# §3 candidate lifecycle. One status at a time, and the transitions are checked
# in the registry rather than trusted to the caller.
# ---------------------------------------------------------------------------
DISCOVERY = "DISCOVERY"
HISTORICAL_TESTING = "HISTORICAL_TESTING"
HISTORICAL_REJECTED = "HISTORICAL_REJECTED"
SHADOW = "SHADOW"
PAPER = "PAPER"
HOLDOUT = "HOLDOUT"
PROMOTED_TO_PRODUCTION_PAPER = "PROMOTED_TO_PRODUCTION_PAPER"
REJECTED = "REJECTED"
DORMANT = "DORMANT"

STATUSES: tuple[str, ...] = (
    DISCOVERY, HISTORICAL_TESTING, HISTORICAL_REJECTED, SHADOW, PAPER,
    HOLDOUT, PROMOTED_TO_PRODUCTION_PAPER, REJECTED, DORMANT,
)

# Statuses at which the definition is immutable (§3). A candidate that has begun
# observing live sessions cannot have its entry rule edited: the sessions were
# measured under the old rule, and silently re-labelling them is the single
# easiest way to invent a result. An edit becomes a new candidate.
FROZEN_AT: frozenset[str] = frozenset({
    SHADOW, PAPER, HOLDOUT, PROMOTED_TO_PRODUCTION_PAPER,
})

# Legal transitions. DORMANT and REJECTED are terminal for the fingerprint;
# re-testing a rejected idea means generating it again, which gives it a new
# identity and an honest second hypothesis count.
TRANSITIONS: dict[str, frozenset[str]] = {
    DISCOVERY: frozenset({HISTORICAL_TESTING, DORMANT, REJECTED}),
    HISTORICAL_TESTING: frozenset({SHADOW, HISTORICAL_REJECTED, DORMANT}),
    HISTORICAL_REJECTED: frozenset({DORMANT}),
    SHADOW: frozenset({PAPER, REJECTED, DORMANT}),
    PAPER: frozenset({HOLDOUT, REJECTED, DORMANT}),
    HOLDOUT: frozenset({PROMOTED_TO_PRODUCTION_PAPER, REJECTED, DORMANT}),
    PROMOTED_TO_PRODUCTION_PAPER: frozenset({REJECTED, DORMANT}),
    REJECTED: frozenset(),
    DORMANT: frozenset({DISCOVERY}),
}

# ---------------------------------------------------------------------------
# §4 mechanism families. Finite and economically distinct, because the
# alternative — free composition of indicators — is a search space large enough
# to fit anything, and a result fitted to a space that large is not evidence.
# ---------------------------------------------------------------------------
FAMILY_MOMENTUM = "A_DIRECTIONAL_MOMENTUM"
FAMILY_COST = "B_OPPORTUNITY_VS_COST"
FAMILY_REGIME = "C_REGIME_STATE"
FAMILY_RELATIVE = "D_RELATIVE_VALUE"
FAMILY_VEHICLE = "E_VEHICLE_SELECTION"
FAMILY_OPTION = "F_OPTION_STRUCTURE"
FAMILY_CAPTURE = "G_PROFIT_CAPTURE"

FAMILIES: tuple[str, ...] = (
    FAMILY_MOMENTUM, FAMILY_COST, FAMILY_REGIME, FAMILY_RELATIVE,
    FAMILY_VEHICLE, FAMILY_OPTION, FAMILY_CAPTURE,
)

# ---------------------------------------------------------------------------
# Vehicle and scope vocabulary.
# ---------------------------------------------------------------------------
FUTURES = "FUTURES"
CE = "CE"
PE = "PE"
UNDERLYING = "UNDERLYING"
VEHICLES: tuple[str, ...] = (FUTURES, CE, PE, UNDERLYING)

# ---------------------------------------------------------------------------
# §19 evidence state of a single evaluation. Continuous with Phase 35 so rows
# from both can be read without a translation table.
# ---------------------------------------------------------------------------
MEASURED = "MEASURED"
UNMEASURED = "UNMEASURED"
CAPTURE_GAP = "CAPTURE_GAP"
STALE = "STALE"
OTHER = "OTHER"
EVIDENCE_STATES: tuple[str, ...] = (MEASURED, UNMEASURED, CAPTURE_GAP, STALE, OTHER)

# Why a screening or evaluation produced no number. An absence with a reason is
# evidence; an absence rendered as a zero is a lie with a decimal point.
NO_HISTORY = "NO_HISTORY"
NO_EXECUTABLE_PRICE = "NO_EXECUTABLE_PRICE"
NO_OPPORTUNITIES = "NO_MEASURABLE_OPPORTUNITIES"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"

# ---------------------------------------------------------------------------
# §5 pre-registered early-kill reasons. Fixed here, before any candidate is
# scored, and every one of them is decidable without knowing whether the
# candidate is currently up or down.
# ---------------------------------------------------------------------------
KILL_NEGATIVE_IN_TRAIN = "NEGATIVE_NET_IN_THE_TRAINING_PERIOD"
KILL_CATASTROPHIC = "CATASTROPHICALLY_NEGATIVE_AFTER_COSTS"
KILL_UNSTABLE = "UNSTABLE_ACROSS_THE_TRAINING_SUB_PERIODS"
KILL_ONE_SESSION = "ENTIRELY_DEPENDENT_ON_ONE_SESSION"
KILL_OUTLIER = "DEPENDENT_ON_A_SINGLE_OUTLIER_TRADE"
KILL_NOT_EXECUTABLE = "NOT_EXECUTABLE_WITH_THE_AVAILABLE_INFORMATION"
KILL_LOOK_AHEAD = "LOOK_AHEAD_DETECTED_IN_THE_ADMISSION_LOGIC"
KILL_TOO_FEW = "TOO_FEW_MEASURABLE_OPPORTUNITIES_TO_JUDGE"

KILL_REASONS: tuple[str, ...] = (
    KILL_NEGATIVE_IN_TRAIN, KILL_CATASTROPHIC, KILL_UNSTABLE,
    KILL_ONE_SESSION, KILL_OUTLIER, KILL_NOT_EXECUTABLE,
    KILL_LOOK_AHEAD, KILL_TOO_FEW,
)

# A candidate is never killed for being down after a handful of trades. Stated
# as a constant so the smoke can assert the screen has no such rule.
NEVER_A_KILL_REASON = "CURRENTLY_LOSING_ON_A_SMALL_SAMPLE"

# ---------------------------------------------------------------------------
# §5/§6 screening thresholds, declared before the sweep.
# ---------------------------------------------------------------------------
MIN_TRAIN_TRADES = 30          # below this the train period cannot reject anything
MIN_SCREEN_SESSIONS = 20       # distinct sessions a screen needs to say anything
CATASTROPHIC_NET_PCT = -0.50   # mean net per trade, in percent of entry
MAX_ONE_SESSION_SHARE = 0.60   # of total net coming from a single session
MAX_ONE_TRADE_SHARE = 0.50     # of total net coming from a single trade
FDR_Q = 0.10                   # Benjamini-Hochberg false-discovery rate

# Chronological split (§5). Fractions of the available span, never of the trade
# list: splitting on trade count moves the boundary when a candidate trades more
# often, and then two candidates are judged on different years.
TRAIN_FRACTION = 0.60
VALIDATION_FRACTION = 0.20
HOLDOUT_FRACTION = 0.20

# ---------------------------------------------------------------------------
# §15 milestones. Descriptive labels, not verdicts: the point of naming them is
# that a reader sees "10 resolved trades" and the word DESCRIPTIVE in the same
# breath, instead of reading an early positive number as a finding.
# ---------------------------------------------------------------------------
MILESTONES: tuple[tuple[str, int, str], ...] = (
    ("EARLY", 10, "DESCRIPTIVE_ONLY_NOT_EVIDENCE_OF_ANYTHING"),
    ("INITIAL", 25, "DESCRIPTIVE_ONLY_TOO_FEW_TO_SEPARATE_SKILL_FROM_NOISE"),
    ("INTERMEDIATE", 50, "INDICATIVE_ONLY_STILL_BELOW_THE_VALIDATION_FLOOR"),
    ("VALIDATION", 100, "ELIGIBLE_FOR_A_VALIDATION_VERDICT_IF_THE_GATES_PASS"),
)
MILESTONE_NOT_REACHED = "BELOW_THE_FIRST_MILESTONE"

# ---------------------------------------------------------------------------
# §21 promotion gate to production paper. Every one of these must hold; the
# absence of any is NO_CANDIDATE, which is the expected state.
# ---------------------------------------------------------------------------
PROMOTION_MIN_TRADES = 100
PROMOTION_MIN_SESSIONS = 20
PROMOTION_MIN_NET_PCT = 0.0        # net after realistic costs, strictly above
PROMOTION_MIN_PF = 1.2
PROMOTION_MAX_DD_PCT = 25.0
PROMOTION_COST_STRESS_MULT = 1.5   # net must survive costs 1.5x the modelled ones
NO_CANDIDATE = "NO_CANDIDATE"

GATE_NAMES: tuple[str, ...] = (
    "FROZEN_FINGERPRINT", "RESOLVED_TRADES", "SESSIONS", "NET_AFTER_COSTS",
    "PROFIT_FACTOR", "DRAWDOWN", "HOLDOUT_POSITIVE", "COST_STRESS",
    "NO_SINGLE_OUTLIER", "NO_LOOK_AHEAD", "DATA_COVERAGE",
)

# §9 champion/challenger. A challenger needs its own full sample; it does not
# inherit the champion's, and a short profitable run does not displace anything.
CHALLENGER_MIN_TRADES = 50
CHALLENGER_MIN_SESSIONS = 15
CHAMPION = "CHAMPION"
CHALLENGER = "CHALLENGER"
NO_CHAMPION = "NO_CHAMPION_YET"

# §20 loss containment for paper simulation. Limits on the simulation, so a
# runaway candidate cannot dominate the journal, and the numbers a later live
# design would inherit are already declared.
MAX_HOLD_MINUTES = 240
DAILY_CANDIDATE_LOSS_PCT = 3.0
DAILY_AGGREGATE_LOSS_PCT = 10.0
MAX_CONCURRENT_PAPER_LEGS = 20

# §22 live readiness. The strongest value is a recommendation to hold a review,
# never an authorisation, and nothing in this package can raise it further.
NOT_READY = "NOT_READY"
PAPER_ONLY = "PAPER_ONLY"
SHADOW_VALIDATED = "SHADOW_VALIDATED"
READY_FOR_CONTROLLED_LIVE_REVIEW = "READY_FOR_CONTROLLED_LIVE_REVIEW"
READINESS_STATES: tuple[str, ...] = (
    NOT_READY, PAPER_ONLY, SHADOW_VALIDATED, READY_FOR_CONTROLLED_LIVE_REVIEW,
)

# §6 significance labels, so a corrected and an uncorrected survivor can never
# be confused for each other in a table.
SURVIVES_CORRECTED = "SURVIVES_MULTIPLICITY_CORRECTION"
UNCORRECTED_ONLY = "CLEARS_THE_RAW_BAR_ONLY_NOT_AFTER_CORRECTION"
NOT_SIGNIFICANT = "NOT_SIGNIFICANT"

# §11 the market-wide result vocabulary. NO_TRADE is the normal answer and is
# ranked alongside the others rather than hidden when the list is empty.
OPPORTUNITY = "OPPORTUNITY"
WATCH = "WATCH"
REJECT = "REJECT"
NO_TRADE_ANYWHERE = "NO_TRADE_ANYWHERE"

# Files. All JSONL and all append-only: a candidate's history is the point.
DATA_SUBDIR = "opportunity"
CANDIDATE_FILE = "candidates.jsonl"
JOURNAL_FILE = "paper_journal.jsonl"
SHADOW_FILE = "shadow_observations.jsonl"
SCREEN_FILE = "screen_results.jsonl"

STANDING_LIMITS: tuple[str, ...] = (
    "research and paper only — no order path, no broker call, no real money",
    "historical candles carry no bid or ask, so every execution figure on them "
    "is UNMEASURED and no spread, fill or midpoint is ever inferred",
    "five years of 1-minute history exist for NIFTY and CRUDEOIL only; every "
    "other instrument screens as NO_HISTORY until a dataset is imported",
    "the hypothesis count is carried in every screening report and the "
    "false-discovery cut is applied across the whole family, not per candidate",
    "an early milestone is descriptive: 10 or 25 resolved trades is not "
    "evidence that anything is profitable",
    "a candidate is never killed for being down on a small sample; the "
    "early-kill reasons are pre-registered and structural",
    "no candidate's decision may depend on another candidate, on the champion, "
    "or on any future price, path, spread or outcome",
    "the strongest verdict available here is production paper, which is still "
    "paper; a live pilot needs separate explicit authorisation",
)
