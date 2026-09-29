"""Phase 50 — what the live shadow events were actually worth, and when the clock said so.

Three faults on the same board, all of them about a number that cannot be told
apart from a different number.

*An earlier reading is not a wrong reading.* The research arm read 11 legs at one
moment and 476 at a later one on the same session. The registry could only say
"superseded", and the reason it chose implied the first count was mis-selected.
It was not: the journal was still accruing, and 11 was the honest total of what
existed when it was read. So a reading now records **what the journal held at the
instant it was taken** — and supersession distinguishes a mis-selected count from
an earlier honest snapshot of an accruing session from the current one. Where a
row was written before those coverage fields existed, its state is
``UNDETERMINED_NO_COVERAGE_RECORDED`` rather than a guess.

*"The market was open" is not evidence.* Every event now carries a
machine-derived ``MARKET_SESSION_STATUS`` from the clock, the weekday, the
segment's own windows and — where it is available — the exchange holiday list.
A weekday inside the session window whose book never moved reads ``UNKNOWN``
rather than ``OPEN``, because with no holiday list on disk this process cannot
tell a holiday from a dead feed, and claiming either would be an assumption
wearing a status field.

*An observation is not a trade.* 465 quotes of one TCS strike are one
opportunity. Events come from Phase 49's frozen 300-second rule, unchanged and
with the same fingerprint — this phase adds no grouping rule of its own — and
both counts are always published together.

What resolution is allowed to use: the recorded executable entry (ASK for a long
option), later **measured executable** books for the same contract in the same
session (BID out) — the event's own later observations as well as the raw quote
store, both being two-sided books recorded at instants — and the round trip
already measured at the instant. Not a midpoint, not a traded print, not a quote
from another session, and not a book too stale to price a fill. Where no later
executable book exists the event stays ``UNRESOLVED`` and contributes to
nothing — an unresolved event is not a flat one.

Everything here is read-only over other phases' journals plus its own
append-only event journal. It admits nothing, promotes nothing, changes no
production decision, and imports no order, broker or execution module.
"""
from __future__ import annotations

import hashlib

VERSION = "50.0"
PHASE = "PHASE50_EVENT_RESOLUTION_AND_SESSION_STATUS"

ARTEFACT_DIR = "phase50"
DB_NAME = "phase50_events.db"
REPORT_NAME = "SHADOW_EVENT_BOARD.md"
HOLIDAY_FILE = "exchange_holidays.json"

CLASSIFICATION = "SHADOW_EVENT_MEASUREMENT"
PAPER_ONLY = "PAPER_ONLY"
NO_ORDER_PATH = "NO_ORDER_PATH_NO_BROKER_NO_REAL_MONEY"
PRODUCTION_UNCHANGED = "PRODUCTION_SIGNAL_UNCHANGED_BY_THIS_LAYER"
APPEND_ONLY = "APPEND_ONLY_NO_UPDATE_NO_DELETE_NO_HISTORY_REWRITE"

# --------------------------------------------------------- market session status
#
# Machine-derived, from the clock and the calendar. Never from a description of
# the day: "the market was open" is a claim, and a status field that can be
# filled in by agreement is not a measurement.
OPEN = "OPEN"
PRE_OPEN = "PRE_OPEN"
POST_CLOSE = "POST_CLOSE"
CLOSED = "CLOSED"
HOLIDAY = "HOLIDAY"
UNKNOWN = "UNKNOWN"

MARKET_SESSION_STATUS: tuple[str, ...] = (
    OPEN, PRE_OPEN, POST_CLOSE, CLOSED, HOLIDAY, UNKNOWN,
)

# How the status was reached, carried beside it. A status derived from a holiday
# list and one derived from a weekday assumption are not the same fact.
BY_CALENDAR = "EXCHANGE_HOLIDAY_LIST_ON_DISK"
BY_WEEKEND = "SATURDAY_OR_SUNDAY_BOTH_EXCHANGES_SHUT"
BY_CLOCK = "SEGMENT_SESSION_WINDOW_IN_IST_ON_A_WEEKDAY"
BY_NO_TIMESTAMP = "THE_ROW_CARRIED_NO_USABLE_TIMESTAMP"
BY_FROZEN_BOOK = (
    "WEEKDAY_INSIDE_THE_SESSION_WINDOW_BUT_THE_BOOK_NEVER_MOVED_AND_NO_"
    "HOLIDAY_LIST_IS_ON_DISK_SO_A_HOLIDAY_AND_A_DEAD_FEED_CANNOT_BE_TOLD_APART"
)
NO_HOLIDAY_LIST = (
    "NO_EXCHANGE_HOLIDAY_LIST_IS_ON_DISK_SO_HOLIDAY_IS_NEVER_ASSERTED_ONLY_"
    "WEEKENDS_AND_SESSION_WINDOWS_ARE_MACHINE_KNOWN"
)

# Segment session windows in IST minutes-from-midnight. Equity and index
# derivatives quote 09:15–15:30 with a pre-open call auction from 09:00; MCX
# runs one long evening session. Kept as data rather than as branches so a
# reader can check the numbers against the exchange circular.
EQUITY_PRE_OPEN_MIN = 9 * 60
EQUITY_OPEN_MIN = 9 * 60 + 15
EQUITY_CLOSE_MIN = 15 * 60 + 30
MCX_PRE_OPEN_MIN = 8 * 60 + 55
MCX_OPEN_MIN = 9 * 60
MCX_CLOSE_MIN = 23 * 60 + 30

SEGMENT_EQUITY = "NSE_BSE_EQUITY_AND_INDEX_DERIVATIVES"
SEGMENT_MCX = "MCX_COMMODITY_DERIVATIVES"

# The registry's own exchange code for the commodity segment. Compared against
# ``InstrumentSpec.exchange`` so a newly configured commodity is graded on
# commodity hours without this module keeping a second list of names.
EXCHANGE_MCX = "MCX"

# ------------------------------------------------------------- snapshot accrual
#
# The three cases §1 asks for, plus the one the record forces: a row written
# before coverage was stored cannot be sorted into any of them, and saying which
# it was would be inventing the evidence that is missing.
SNAPSHOT_MIS_SELECTED = "MIS_SELECTED_INCORRECT_READING"
SNAPSHOT_ACCRUAL = "SNAPSHOT_ACCRUAL"
SNAPSHOT_CURRENT = "CURRENT_SESSION_SNAPSHOT"
SNAPSHOT_UNDETERMINED = "UNDETERMINED_NO_COVERAGE_RECORDED"

# Two more the registry already distinguishes, kept rather than folded into the
# three above: a reading that stopped at its own bound was weaker than the one
# that replaced it but not wrong, and a re-recording whose count did not move
# was not wrong either. Calling either of them MIS_SELECTED would put a fault on
# the record that the two counts beside it disprove.
SNAPSHOT_BOUNDED = "EARLIER_BOUNDED_READING_WHOSE_COUNT_WAS_A_FLOOR_NOT_A_TOTAL"
SNAPSHOT_RE_RECORDED = (
    "RE_RECORDED_UNDER_THE_CORRECTED_SELECTION_WITH_AN_UNCHANGED_COUNT"
)

SNAPSHOT_STATES: tuple[str, ...] = (
    SNAPSHOT_MIS_SELECTED, SNAPSHOT_ACCRUAL, SNAPSHOT_CURRENT,
    SNAPSHOT_BOUNDED, SNAPSHOT_RE_RECORDED, SNAPSHOT_UNDETERMINED,
)

# How a state was reached. A state read off the registry's own reason and one
# re-derived from recorded coverage are not the same evidence.
FROM_REGISTRY = "THE_REGISTRY_REASON_THE_SUPERSESSION_WAS_WRITTEN_WITH"
FROM_COVERAGE = "DERIVED_FROM_WHAT_THE_JOURNAL_HELD_WHEN_EACH_READING_WAS_TAKEN"
FROM_NOTHING = "NO_COVERAGE_WAS_RECORDED_FOR_THIS_ROW_SO_NOTHING_IS_ASSERTED"
NOT_SUPERSEDED = "NO_REGISTRY_ROW_NAMES_THIS_SNAPSHOT"

SNAPSHOT_STATE_TEXT: dict[str, str] = {
    SNAPSHOT_MIS_SELECTED: (
        "The reading disagreed with what the journal already held when it was "
        "taken. Its count was wrong at its own instant."
    ),
    SNAPSHOT_ACCRUAL: (
        "EARLIER_HONEST_SNAPSHOT_OF_AN_ACCRUING_SESSION: the reading counted "
        "everything the journal held when it was taken, and was overtaken "
        "because the session kept writing rows. Not a wrong count."
    ),
    SNAPSHOT_CURRENT: (
        "The newest reading of this session and arm under the current "
        "selection. Still provisional while the session accrues."
    ),
    SNAPSHOT_BOUNDED: (
        "The reading stopped at its own selection bound, so its count was a "
        "floor. Weaker than the reading that replaced it, not wrong."
    ),
    SNAPSHOT_RE_RECORDED: (
        "Re-recorded under the corrected selection and returned the same "
        "count. The old count was not wrong."
    ),
    SNAPSHOT_UNDETERMINED: (
        "Written before a reading recorded what the journal held at the time, "
        "so accrual and mis-selection cannot be told apart for this row."
    ),
}

# ------------------------------------------------------------------- resolution
RESOLVED = "RESOLVED"
UNRESOLVED_NO_LATER_QUOTE = (
    "UNRESOLVED_NO_LATER_EXECUTABLE_QUOTE_FOR_THIS_CONTRACT_IN_THIS_SESSION"
)
UNRESOLVED_NO_ENTRY = "UNRESOLVED_NO_EXECUTABLE_ENTRY_PRICE_WAS_RECORDED"
UNRESOLVED_NO_COST = "UNRESOLVED_NO_ROUND_TRIP_WAS_MEASURED_SO_NET_IS_UNKNOWN"
UNRESOLVED_EVENT_OPEN = "UNRESOLVED_THE_EVENT_IS_STILL_ACCRUING_OBSERVATIONS"

RESOLUTION_STATES: tuple[str, ...] = (
    RESOLVED, UNRESOLVED_NO_LATER_QUOTE, UNRESOLVED_NO_ENTRY,
    UNRESOLVED_NO_COST, UNRESOLVED_EVENT_OPEN,
)

NO_MIDPOINT = (
    "NO_MIDPOINT_AND_NO_TRADED_PRINT_IS_EVER_AN_EXIT: a long option enters at "
    "the ASK and exits at the BID, a long future enters at the ASK and exits "
    "at the BID, a short future the reverse. A sample whose required side was "
    "not quoted is dropped from the path rather than filled at the mid or at "
    "somebody else's print."
)

ENTRY_RULE = "LONG_OPTION_ENTERS_AT_THE_ASK_RECORDED_AT_THE_DECISION_INSTANT"
EXIT_RULE = "EXIT_AT_THE_LATEST_MEASURED_EXECUTABLE_BID_IN_THE_SAME_SESSION"

# Where a forward sample may come from, and the reason both sources are read.
#
# An event of 630 observations on one contract holds 629 later two-sided books of
# its own, each recorded at a decision instant by the same capture that priced
# the entry. Reading only a separate quote store reported "no later executable
# quote" for events whose later quotes it had already loaded — a statement about
# where this module looked, published as a statement about the capture. Both
# sources are measured quotes at recorded instants; neither is a midpoint, a
# print or an inference, and a sample is admitted from an observation only when
# that observation's own book was fresh enough to price a fill by the same bar
# the entry was priced under.
SAMPLE_SOURCE = (
    "FORWARD_SAMPLES_ARE_THE_EVENTS_OWN_LATER_OBSERVED_BOOKS_UNIONED_WITH_THE_"
    "RAW_QUOTE_STORE_FOR_THAT_CONTRACT_IN_THAT_SESSION"
)
SAMPLE_FRESHNESS = (
    "AN_OBSERVED_BOOK_IS_A_FORWARD_SAMPLE_ONLY_AT_THE_SAME_DATA_QUALITY_BAR_"
    "THAT_PRICES_AN_ENTRY_FILL"
)
FROM_OBSERVATIONS = "THE_EVENTS_OWN_LATER_OBSERVATIONS"
FROM_QUOTE_STORE = "THE_RAW_QUOTE_STORE_AFTER_THE_LAST_OBSERVATION"

# --------------------------------------------------------------- the comparison
ARM_PRODUCTION_ONLY = "A_CURRENT_PRODUCTION_SIGNAL"
ARM_PRODUCTION_PLUS_RESEARCH = "B_CURRENT_PRODUCTION_SIGNAL_PLUS_RESEARCH_OVERLAY"
COMPARISON_ARMS: tuple[str, ...] = (
    ARM_PRODUCTION_ONLY, ARM_PRODUCTION_PLUS_RESEARCH,
)

# The label the report carries until a promotion gate says otherwise. Not a
# hedge: with three research events and twenty-nine production ones, every
# statistic below is one draw, and the words this project refuses to print on
# one draw are listed so a reader can check the report for them.
EARLY_SHADOW_RESULT = "EARLY_SHADOW_RESULT"
FORBIDDEN_WORDS: tuple[str, ...] = ("profitable", "validated", "best", "edge")

DIRECTIONALLY_USEFUL = "DIRECTIONALLY_USEFUL_ON_THIS_SAMPLE_NOT_A_PROMOTION"
DIRECTIONALLY_UNHELPFUL = "NOT_DIRECTIONALLY_USEFUL_ON_THIS_SAMPLE"
TOO_FEW_EVENTS = "TOO_FEW_RESOLVED_EVENTS_TO_SAY_EITHER_WAY"

# Below this many resolved events per arm the comparison reports its inputs and
# refuses a direction. Stated as a constant so the bar cannot be read off the
# sample it is applied to.
MIN_RESOLVED_EVENTS = 20

NOT_A_PROMOTION = (
    "EARLY_SHADOW_RESULT: this compares two paper columns over the events one "
    "capture holds. It arms no gate, promotes no candidate, changes no signal, "
    "places no order, and is not a claim that either column is profitable — "
    "which on this many independent events could not be measured either way."
)

OVERLAP = (
    "THE_TWO_COLUMNS_OVERLAP: arm B is arm A plus the events the research "
    "overlay admitted, so wherever both admitted the same opportunity the same "
    "paper event is in both totals. The difference between the columns is what "
    "the overlay added, not one column tested against the other."
)

# ------------------------------------------------- the overlay is a filter, not an addition
#
# On the first market-open capture every one of the overlay's 1,528 legs was
# already a production leg, so the union column equalled the production column
# on every metric and the difference was zero by construction, not by
# measurement. A ∪ overlay cannot differ from A while overlay ⊆ A, so that
# comparison is arithmetically incapable of answering whether the overlay helps
# and is no longer published as a performance comparison.
#
# What the overlay actually does on this capture is *decline* production
# opportunities. So the production event universe is partitioned: the events
# whose admitting instant the overlay supported, and the events it did not. Both
# groups are then measured under the same exit policy, which is the only form of
# the question a filter can answer.
DEGENERATE = (
    "THE_OVERLAY_IS_A_SUBSET_OF_PRODUCTION_ON_THIS_CAPTURE_SO_PRODUCTION_"
    "VERSUS_PRODUCTION_PLUS_OVERLAY_IS_DEGENERATE_AND_IS_NOT_A_PERFORMANCE_"
    "COMPARISON"
)

GROUP_SELECTED = "OVERLAY_SELECTED"
GROUP_DECLINED = "OVERLAY_DECLINED"
GROUP_UNMEASURED = "OVERLAY_UNMEASURED"
FILTER_GROUPS: tuple[str, ...] = (
    GROUP_SELECTED, GROUP_DECLINED, GROUP_UNMEASURED,
)

# The partition is read off the overlay state the capture recorded **at the
# event's first observation** — the instant the opportunity was admitted and the
# only instant a decision could have used. Later observations of the same event
# may change state as the book moves; counting those would let information from
# after the entry decide which group the entry belongs to, which is the
# hindsight §3 forbids. How many of an event's observations were supported is
# published beside the group so a mixed event is visible rather than hidden.
#
# Three groups, not two, because the overlay states divide three ways and not
# two. SUPPORTED is the overlay taking the opportunity. DISAGREES, COST_BLOCKED
# and WATCH are the overlay looking at the opportunity and refusing it — those
# are its declines, and they are what a filter has to be judged against.
# UNMEASURED, STALE, CAPTURE_GAP and NO_RESEARCH_EVIDENCE are not decisions at
# all: the overlay had nothing to say, usually because the capture had nothing to
# say to it. Calling those declines would credit the filter with refusing trades
# it was never asked about, which on a capture where every state is UNMEASURED
# would manufacture a complete decline cohort out of a recording gap.
STATE_IS_A_DECLINE: tuple[str, ...] = ("DISAGREES", "COST_BLOCKED", "WATCH")
STATE_IS_NOT_A_DECISION: tuple[str, ...] = (
    "UNMEASURED", "STALE", "CAPTURE_GAP", "NO_RESEARCH_EVIDENCE",
)
PARTITION_RULE = (
    "A_PRODUCTION_EVENT_IS_OVERLAY_SELECTED_WHEN_THE_OVERLAY_STATE_RECORDED_AT_"
    "ITS_FIRST_OBSERVATION_IS_SUPPORTED_OVERLAY_DECLINED_WHEN_THAT_STATE_IS_A_"
    "REFUSAL_THE_OVERLAY_ACTUALLY_MADE_AND_OVERLAY_UNMEASURED_WHEN_NO_STATE_WAS_"
    "RECORDED_OR_THE_RECORDED_STATE_IS_NOT_A_DECISION"
)
PARTITION_IS_EXHAUSTIVE = (
    "EVERY_PRODUCTION_EVENT_IS_IN_EXACTLY_ONE_GROUP_AND_A_DECLINED_EVENT_IS_"
    "MEASURED_NOT_DROPPED"
)
UNMEASURED_IS_NOT_A_DECLINE = (
    "AN_EVENT_THE_OVERLAY_HAD_NO_STATE_FOR_IS_UNMEASURED_NOT_DECLINED_BECAUSE_A_"
    "FILTER_CANNOT_BE_CREDITED_WITH_REFUSING_WHAT_IT_WAS_NEVER_ASKED_ABOUT"
)

# ---------------------------------------------------------------- exit policies
#
# The observed-path endpoint — exit at the last executable book on the path — is
# where a leg *stopped being watched*, not where a decision closed it. Hold
# times under it ran from 0 to 1,702 seconds on one capture purely by coverage,
# so its net figure grades the recorder, not a strategy. It is kept as a coverage
# diagnostic and is no longer the primary outcome.
#
# Each policy below is written down with its parameters and fingerprinted before
# any outcome is measured, and every policy is applied to the selected and the
# declined group identically.
COVERAGE_ENDPOINT = "OBSERVED_PATH_END_COVERAGE_DIAGNOSTIC_NOT_A_TRADING_EXIT"
COVERAGE_NOT_AN_EXIT = (
    "THE_LAST_OBSERVED_BOOK_IS_WHERE_THE_CAPTURE_STOPPED_LOOKING_NOT_WHERE_A_"
    "DECISION_CLOSED_THE_LEG_SO_IT_IS_REPORTED_AS_COVERAGE_AND_NEVER_AS_THE_"
    "PRIMARY_NET"
)

# A horizon is answered by the first executable book at or after it, and only
# while that book is within one grouping gap of the horizon. Beyond that the
# horizon is unanswered: filling "15 minutes" from a book twenty minutes late is
# the forward-fill this project refuses everywhere else.
HORIZON_TOLERANCE_SEC = 300.0
MAX_HOLD_SEC = 3600.0
TRAIL_GIVEBACK_FRACTION_OF_PEAK = 0.5
STOP_COST_MULTIPLE = 2.0

NO_HINDSIGHT = (
    "NO_EXIT_POLICY_READS_FUTURE_MFE_MAE_GIVEBACK_PNL_SPREAD_OR_FINAL_OUTCOME_"
    "EACH_WALKS_THE_BOOKS_IN_TIME_ORDER_AND_STOPS_AT_THE_FIRST_ONE_THAT_"
    "SATISFIES_ITS_OWN_PRE_REGISTERED_CONDITION"
)
SAME_EXIT_BOTH_GROUPS = (
    "THE_SELECTED_AND_DECLINED_GROUPS_ARE_MEASURED_UNDER_THE_SAME_POLICY_AND_"
    "THE_SAME_FINGERPRINT_IN_EVERY_ROW_OF_EVERY_TABLE"
)

UNRESOLVED_NO_QUOTE_AT_HORIZON = (
    "UNRESOLVED_NO_EXECUTABLE_BOOK_WITHIN_TOLERANCE_OF_THIS_POLICYS_EXIT_INSTANT"
)
UNRESOLVED_POLICY_NEVER_TRIGGERED = (
    "UNRESOLVED_THE_POLICY_NEVER_TRIGGERED_AND_THE_PATH_ENDED_BEFORE_ITS_TIMEOUT"
)


# ------------------------------------------------------------ coverage symmetry
#
# The first capture measured 5 of 8 selected events and 23 of 63 declined ones
# under the five-minute policy, and the table published +25.5 net per event off
# that. Those are not the same universe: which events resolve under a policy is
# decided by how long the capture kept watching the contract, so a difference
# between a 62% cohort and a 37% cohort mixes the overlay's judgement with the
# recorder's uptime and cannot be attributed to either.
#
# Resolution rate is therefore reported as its own quantity and never read as
# performance, and the delta column is withheld unless both cohorts were
# resolved at comparable rates. The threshold is written here, before the
# measurement, as two constants: an absolute gap in percentage points and a
# ratio, both of which must hold. The ratio catches the small-rate case a gap
# alone lets through — 4% against 12% is 8 points apart and three times as
# covered.
COVERAGE_MAX_RATE_GAP_PP = 15.0
COVERAGE_MAX_RATE_RATIO = 1.5
COVERAGE_COMPARABLE = "COVERAGE_COMPARABLE"
COVERAGE_ASYMMETRIC = "COVERAGE_ASYMMETRIC_DO_NOT_COMPARE"
COVERAGE_NOT_MEASURABLE = "COVERAGE_UNMEASURED_NO_RESOLVED_EVENTS_TO_COMPARE"
COVERAGE_GUARD_RULE = (
    "TWO_COHORTS_ARE_COMPARABLE_ONLY_WHEN_THEIR_RESOLUTION_RATES_DIFFER_BY_NO_"
    "MORE_THAN_15_PERCENTAGE_POINTS_AND_BY_NO_MORE_THAN_A_FACTOR_OF_1_5_"
    "OTHERWISE_THE_RAW_NUMBERS_ARE_SHOWN_AND_THE_DELTA_IS_WITHHELD"
)
RESOLUTION_IS_NOT_PERFORMANCE = (
    "RESOLUTION_RATE_IS_HOW_OFTEN_THE_CAPTURE_HELD_A_LATER_EXECUTABLE_BOOK_AND_"
    "TRADING_PERFORMANCE_IS_WHAT_THE_PRICED_EVENTS_EARNED_THEY_ARE_DIFFERENT_"
    "QUANTITIES_AND_NEITHER_IS_EVIDENCE_FOR_THE_OTHER"
)
DELTA_WITHHELD = (
    "SEL_MINUS_DEC_IS_NOT_PUBLISHED_FOR_THIS_ROW_BECAUSE_THE_TWO_COHORTS_WERE_"
    "NOT_RESOLVED_AT_COMPARABLE_RATES_AND_THE_DIFFERENCE_WOULD_CARRY_THE_"
    "CAPTURES_UPTIME_INSIDE_IT"
)
DELTA_WITHHELD_TOO_FEW = (
    "SEL_MINUS_DEC_IS_NOT_PUBLISHED_FOR_THIS_ROW_BECAUSE_ONE_OR_BOTH_COHORTS_"
    "RESOLVED_FEWER_THAN_THE_REQUIRED_EVENTS_AND_A_DIFFERENCE_OF_TWO_SMALL_"
    "MEANS_IS_NOT_A_MEASUREMENT_OF_ANYTHING"
)
# The third refusal, added beside the two above rather than replacing either:
# cohorts can resolve at comparable rates and still have been looked at on
# different schedules, and the schedule is the opportunity to be resolved.
DELTA_WITHHELD_CADENCE = (
    "SEL_MINUS_DEC_IS_NOT_PUBLISHED_FOR_THIS_ROW_BECAUSE_THE_TWO_COHORTS_WERE_"
    "OBSERVED_AT_MATERIALLY_DIFFERENT_POLLING_CADENCES_AND_OBSERVATION_"
    "OPPORTUNITY_IS_NOT_A_TRADING_RESULT"
)

# ------------------------------------------------------------------ cost bands
#
# `avg_cost_points` 0.73 selected against 296.9 declined is not a finding: one of
# the decline states *is* COST_BLOCKED, so the overlay declining expensive
# contracts is the definition of the filter rather than evidence about its
# judgement. The question that is not circular is whether, inside one instrument,
# one vehicle and one comparable cost band, the events it kept beat the events it
# refused.
#
# The band is cut on the round-trip cost as a percentage of the entry premium —
# measured or modelled at the admitting instant, so known before any outcome
# exists. Points alone would not do: 170 points is most of a NIFTY option's
# premium and a rounding error on a SILVER contract, and a band that mixes them
# is not a comparable stratum. Nothing about MFE, MAE, giveback, P&L or the final
# outcome enters the band.
COST_BAND_BASIS = "ROUND_TRIP_COST_AS_PERCENT_OF_ENTRY_PREMIUM_AT_THE_ENTRY_INSTANT"
COST_BAND_IS_EX_ANTE = (
    "THE_BAND_IS_CUT_ON_A_QUANTITY_KNOWN_AT_THE_DECISION_INSTANT_AND_NEVER_ON_"
    "FUTURE_PNL_MFE_MAE_GIVEBACK_OR_FINAL_OUTCOME"
)
COST_BAND_EDGES_PCT: tuple[float, ...] = (1.0, 2.5, 5.0, 10.0, 25.0)
COST_BAND_UNMEASURED = "COST_BAND_UNMEASURED_NO_EX_ANTE_COST_RECORDED"


def cost_band(cost_pct_of_entry: float | None) -> str:
    """Which pre-registered cost band an entry falls in, by its ex-ante cost."""
    if cost_pct_of_entry is None:
        return COST_BAND_UNMEASURED
    value = float(cost_pct_of_entry)
    low = 0.0
    for edge in COST_BAND_EDGES_PCT:
        if value < edge:
            return f"COST_{_band_label(low)}_TO_{_band_label(edge)}_PCT"
        low = edge
    return f"COST_OVER_{_band_label(COST_BAND_EDGES_PCT[-1])}_PCT"


def _band_label(value: float) -> str:
    text = f"{value:g}".replace(".", "P")
    return text


def cost_bands() -> tuple[str, ...]:
    """Every band label the cutter can return, in order, for a stable report."""
    labels: list[str] = []
    low = 0.0
    for edge in COST_BAND_EDGES_PCT:
        labels.append(f"COST_{_band_label(low)}_TO_{_band_label(edge)}_PCT")
        low = edge
    labels.append(f"COST_OVER_{_band_label(COST_BAND_EDGES_PCT[-1])}_PCT")
    labels.append(COST_BAND_UNMEASURED)
    return tuple(labels)


# ------------------------------------------- forward-observation diagnostic (§A)
#
# The guard refused every policy row on the first market-open capture: the
# selected cohort resolved at 60-90% and the declined cohort at 3-36%, so no
# delta could be published for any of them. That refusal is correct and it is
# also a dead end — more sessions of the same recording accumulate more of an
# incomparable pair rather than converging on an answer.
#
# So before changing any capture behaviour, the asymmetry itself is measured.
# This block is a **read-only diagnostic about data coverage**. It asks, for
# every production event, how long the capture kept looking at that contract and
# what the recorded data says stopped it. It reads no P&L, MFE, MAE, giveback or
# final outcome, and it produces no trading conclusion: an event that lost money
# and an event that made money are the same event to it.
#
# The reason an observation stopped is asserted only from a recorded fact, and
# each reason names the fact that asserts it. "Unresolved" is not a reason —
# it is the consequence of one, and it was the only thing the board could say
# before this diagnostic existed.
OBSERVATION_DIAGNOSTIC = "FORWARD_OBSERVATION_COVERAGE_DIAGNOSTIC_READ_ONLY"
DIAGNOSTIC_IS_READ_ONLY = (
    "THIS_DIAGNOSTIC_READS_JOURNALS_AND_WRITES_NOTHING_IT_CHANGES_NO_CAPTURE_NO_"
    "SIGNAL_NO_FILTER_NO_ORDER_PATH_AND_NO_FINGERPRINT"
)
DIAGNOSTIC_HAS_NO_OUTCOME = (
    "NO_PNL_MFE_MAE_GIVEBACK_WIN_RATE_OR_FINAL_OUTCOME_ENTERS_THIS_DIAGNOSTIC_"
    "WHY_AN_EVENT_WAS_OBSERVED_CANNOT_BE_EXPLAINED_BY_WHAT_IT_EARNED"
)

STOP_STALE_QUOTE = "STALE_QUOTE"
STOP_SIDE_UNAVAILABLE = "OPTION_SIDE_UNAVAILABLE"
STOP_SESSION_ENDED = "SESSION_ENDED"
STOP_FEED_STOPPED = "DATA_FEED_STOPPED"
STOP_CAPTURE_GAP = "CAPTURE_GAP"
STOP_GROUP_ENDED = "EVENT_GROUP_ENDED"
STOP_CONTRACT_DROPPED = "CONTRACT_NO_LONGER_OBSERVED"
STOP_EVENT_ENDED = "EVENT_ENDED_NATURALLY"
STOP_NO_LATER_QUOTE = "NO_LATER_QUOTE"
STOP_OTHER = "OTHER"

# Order matters and is part of the definition: the reasons are tested in this
# sequence and the first one whose evidence is present is the answer, so a row
# has exactly one reason and two readers get the same one. Stronger evidence
# comes first — a recorder that went silent is a fact about the recorder whether
# or not the contract was ever quoted again.
STOP_REASONS: tuple[str, ...] = (
    STOP_STALE_QUOTE,
    STOP_SIDE_UNAVAILABLE,
    STOP_SESSION_ENDED,
    STOP_FEED_STOPPED,
    STOP_CAPTURE_GAP,
    STOP_GROUP_ENDED,
    STOP_CONTRACT_DROPPED,
    STOP_EVENT_ENDED,
    STOP_NO_LATER_QUOTE,
    STOP_OTHER,
)

STOP_REASON_EVIDENCE: dict[str, str] = {
    STOP_STALE_QUOTE: (
        "the event's last observation recorded a data quality below the bar "
        "that prices a fill, so the capture was still writing rows for this "
        "contract but they were no longer executable books"
    ),
    STOP_SIDE_UNAVAILABLE: (
        "later books for this contract were recorded and none of them quoted "
        "the side the exit needs, so the chain was present and one-sided"
    ),
    STOP_SESSION_ENDED: (
        "the last observation is within one grouping gap of this segment's own "
        "close minute, so the session ran out before the capture did"
    ),
    STOP_FEED_STOPPED: (
        "no observation of any contract was recorded after this event's last "
        "one while the session was still open, so the recorder stopped rather "
        "than this contract"
    ),
    STOP_CAPTURE_GAP: (
        "the next observation of any contract is more than one grouping gap "
        "after this event's last one, so the recorder was silent across a hole "
        "and resumed afterwards"
    ),
    STOP_GROUP_ENDED: (
        "this contract was observed again later in the session, but more than "
        "one grouping gap later, so the frozen 300-second rule closed this "
        "event and gave those books to a different event"
    ),
    STOP_CONTRACT_DROPPED: (
        "the recorder kept observing other contracts without a gap and never "
        "observed this one again, so the contract left the observed set while "
        "the capture continued"
    ),
    STOP_EVENT_ENDED: (
        "the event held later executable books of its own and none of the "
        "above applies, so its path ended where the opportunity did"
    ),
    STOP_NO_LATER_QUOTE: (
        "no later book of any kind exists for this contract and no stronger "
        "evidence about why is recorded — absence, stated as absence"
    ),
    STOP_OTHER: (
        "the recorded fields cannot support any of the reasons above, most "
        "often a row with no usable timestamp"
    ),
}

# The eight candidate sources of the asymmetry, named as §4 names them. Each stop
# reason belongs to exactly one, so a distribution of reasons becomes a
# distribution of causes without a judgement call in between.
CAUSE_GROUPING = "A_EVENT_GROUPING"
CAUSE_OVERLAY = "B_SHADOW_OVERLAY_SELECTION"
CAUSE_CAPTURE = "C_QUOTE_CAPTURE"
CAUSE_TRACKING = "D_CONTRACT_TRACKING"
CAUSE_CHAIN = "E_OPTION_CHAIN_AVAILABILITY"
CAUSE_TERMINATION = "F_EVENT_TERMINATION"
CAUSE_SESSION = "G_SESSION_BOUNDARY"
CAUSE_OTHER = "H_OTHER"

CAUSES: tuple[str, ...] = (
    CAUSE_GROUPING, CAUSE_OVERLAY, CAUSE_CAPTURE, CAUSE_TRACKING,
    CAUSE_CHAIN, CAUSE_TERMINATION, CAUSE_SESSION, CAUSE_OTHER,
)

STOP_REASON_CAUSE: dict[str, str] = {
    STOP_STALE_QUOTE: CAUSE_CAPTURE,
    STOP_SIDE_UNAVAILABLE: CAUSE_CHAIN,
    STOP_SESSION_ENDED: CAUSE_SESSION,
    STOP_FEED_STOPPED: CAUSE_CAPTURE,
    STOP_CAPTURE_GAP: CAUSE_CAPTURE,
    STOP_GROUP_ENDED: CAUSE_GROUPING,
    STOP_CONTRACT_DROPPED: CAUSE_TRACKING,
    STOP_EVENT_ENDED: CAUSE_TERMINATION,
    STOP_NO_LATER_QUOTE: CAUSE_CAPTURE,
    STOP_OTHER: CAUSE_OTHER,
}

# ``CAUSE_OVERLAY`` is deliberately not reachable from a stop reason. No recorded
# field says "the recorder watched this event because the overlay supported it",
# so that cause can only be inferred from a residual: an asymmetry in observation
# depth that survives inside every stop reason. It is reported as a residual with
# that caveat rather than assigned, because the honest answer to "did the shadow
# layer bias the capture" is not obtainable from a capture whose policy already
# differs between the cohorts.
CAUSE_OVERLAY_IS_A_RESIDUAL = (
    "NO_RECORDED_FIELD_ATTRIBUTES_AN_OBSERVATION_TO_THE_OVERLAY_STATE_SO_THIS_"
    "CAUSE_IS_ONLY_EVER_REPORTED_AS_A_RESIDUAL_DEPTH_ASYMMETRY_THAT_SURVIVES_"
    "INSIDE_EVERY_STOP_REASON_AND_NEVER_ASSIGNED_FROM_A_STOP_REASON"
)

# The horizons the coverage percentages are cut at, in seconds. They are the
# declared exit policies' own horizons, so "% with 15m coverage" answers "could
# the 15-minute policy have been measured on this event at all".
COVERAGE_HORIZONS_SEC: tuple[float, ...] = (300.0, 900.0, 1800.0, 3600.0)

# How close to its segment's close minute an event's last observation must be
# before the session, rather than the recorder, is called the reason it stopped.
# One grouping gap, so the same 300 seconds that defines an event defines this.
SESSION_CLOSE_TOLERANCE_SEC = 300.0

# ---------------------------------------------------------------------------
# Poll cadence. The coverage table said the two cohorts were observed for
# different *durations*; it could not say they were observed at different
# *rates*, and a cohort polled every 43 seconds cannot resolve a 5-minute exit
# on the same terms as one polled every 3 seconds however long the window is.
#
# These boundaries are pre-registered for the same reason the coverage guard's
# are. A "sparse" threshold chosen after seeing which cohort it lands on
# measures the reader and not the recording. Continuous means a median interval
# inside a quarter-minute — a recorder that is following the contract. Sparse
# means a minute or worse — a recorder that is sampling it. Between the two the
# label is intermittent rather than a coin toss.
CADENCE_CONTINUOUS_MAX_SEC = 15.0
CADENCE_SPARSE_MIN_SEC = 60.0
CADENCE_CONTINUOUS = "CONTINUOUS"
CADENCE_INTERMITTENT = "INTERMITTENT"
CADENCE_SPARSE = "SPARSE"
CADENCE_SINGLE = "SINGLE_OBSERVATION_NO_INTERVAL_MEASURABLE"
CADENCE_LABELS: tuple[str, ...] = (
    CADENCE_CONTINUOUS, CADENCE_INTERMITTENT, CADENCE_SPARSE, CADENCE_SINGLE,
)
CADENCE_RULE = (
    "AN_EVENTS_CADENCE_IS_THE_MEDIAN_INTERVAL_BETWEEN_ITS_OWN_CONSECUTIVE_"
    "OBSERVATIONS_CONTINUOUS_AT_OR_UNDER_15S_SPARSE_AT_OR_OVER_60S_"
    "INTERMITTENT_BETWEEN_AND_UNMEASURABLE_ON_A_SINGLE_OBSERVATION"
)

# The cadence half of the fair-comparison requirement. Two cohorts whose median
# polling interval differs by more than a factor of two did not get the same
# chance to be resolved, so a P&L subtraction between them is withheld even when
# the resolution-rate guard passes: equal resolution rates reached by unequal
# observation opportunity is a coincidence, not a comparison.
CADENCE_MAX_RATIO = 2.0
CADENCE_COMPARABLE = "CADENCE_COMPARABLE"
CADENCE_ASYMMETRIC = "CADENCE_ASYMMETRIC_DO_NOT_COMPARE"
CADENCE_NOT_MEASURABLE = "CADENCE_UNMEASURED_NO_INTERVAL_IN_ONE_COHORT"
CADENCE_GUARD_RULE = (
    "TWO_COHORTS_ARE_COMPARABLE_ONLY_WHEN_THEIR_MEDIAN_INTER_OBSERVATION_"
    "INTERVALS_DIFFER_BY_NO_MORE_THAN_A_FACTOR_OF_2_OTHERWISE_THE_RAW_NUMBERS_"
    "ARE_SHOWN_AND_THE_DELTA_IS_WITHHELD_BECAUSE_OBSERVATION_OPPORTUNITY_"
    "DIFFERED_MATERIALLY"
)
CADENCE_IS_NOT_PERFORMANCE = (
    "HOW_OFTEN_AN_EVENT_WAS_LOOKED_AT_IS_A_PROPERTY_OF_THE_RECORDER_NOT_OF_THE_"
    "OPPORTUNITY_AND_IS_NEVER_A_TRADING_QUANTITY"
)

# ---------------------------------------------------------------------------
# The second-level question: given the cadence measurement, what produced the
# coverage asymmetry. Distinct from CAUSES above, which ranks *stop reasons*;
# these rank mechanisms, and the first of them — instrument mix — is invisible
# to a stop-reason table because it is a property of which contracts each cohort
# happens to contain.
BIAS_INSTRUMENT_MIX = "A_INSTRUMENT_MIX"
BIAS_POLL_CADENCE = "B_POLL_CADENCE"
BIAS_EVENT_TERMINATION = "C_EVENT_TERMINATION"
BIAS_QUOTE_AVAILABILITY = "D_QUOTE_AVAILABILITY"
BIAS_CONTRACT_TRACKING = "E_CONTRACT_TRACKING"
BIAS_SESSION_BOUNDARY = "F_SESSION_BOUNDARY"
BIAS_OVERLAY_CORRELATED = "G_OVERLAY_SELECTION_CORRELATED_WITH_POLLING"
BIAS_OTHER = "H_OTHER"
BIAS_SOURCES: tuple[str, ...] = (
    BIAS_INSTRUMENT_MIX, BIAS_POLL_CADENCE, BIAS_EVENT_TERMINATION,
    BIAS_QUOTE_AVAILABILITY, BIAS_CONTRACT_TRACKING, BIAS_SESSION_BOUNDARY,
    BIAS_OVERLAY_CORRELATED, BIAS_OTHER,
)

# The stop-reason causes each mechanism is asserted from. Grouping belongs to
# termination and not to cadence on purpose: the frozen 300-second rule ending
# an event is a termination fact, and *why* the contract went quiet long enough
# for the rule to fire is the separate cadence measurement, so folding one into
# the other would count the same evidence twice.
BIAS_SOURCE_FROM_CAUSE: dict[str, str] = {
    CAUSE_GROUPING: BIAS_EVENT_TERMINATION,
    CAUSE_TERMINATION: BIAS_EVENT_TERMINATION,
    CAUSE_CAPTURE: BIAS_QUOTE_AVAILABILITY,
    CAUSE_CHAIN: BIAS_QUOTE_AVAILABILITY,
    CAUSE_TRACKING: BIAS_CONTRACT_TRACKING,
    CAUSE_SESSION: BIAS_SESSION_BOUNDARY,
    CAUSE_OTHER: BIAS_OTHER,
}

# Pre-registered evidence bars. A mechanism is named only when its measured
# quantity crosses its bar; nothing is named because it is plausible.
BIAS_MIN_STOP_SHARE_GAP_PP = 10.0
BIAS_MIN_DEPTH_RATIO = 2.0
BIAS_MIN_CADENCE_RATIO = 2.0
BIAS_MIN_EVENTS_PER_COHORT = 5
BIAS_G_IS_CORRELATION_ONLY = (
    "OVERLAY_SELECTION_CORRELATED_WITH_POLLING_IS_ASSERTED_ONLY_AS_A_"
    "CORRELATION_THAT_SURVIVES_WITHIN_A_SINGLE_INSTRUMENT_NO_RECORDED_FIELD_"
    "SAYS_THE_OVERLAY_STATE_CAUSED_THE_RECORDER_TO_KEEP_LOOKING_AND_A_CAPTURE_"
    "WHOSE_POLICY_ALREADY_DIFFERS_BETWEEN_COHORTS_CANNOT_SEPARATE_THE_TWO"
)
BIAS_UNRANKED = "NO_MECHANISM_CROSSED_ITS_PRE_REGISTERED_EVIDENCE_BAR"

# ------------------------------------------------------- how much was read
#
# Every quantity in the coverage diagnostic is a length or a density of
# observation, and a bounded read removes the tail of exactly those. So the
# reading states whether it saw the journal or its own ceiling *before* any depth
# figure is interpreted: the first run read 5,000 legs under a 5,000-leg bound,
# and a 201-against-4 depth median taken there cannot be told apart from the
# shape of the limit.
TRUNCATED = "TRUNCATED"
EXHAUSTIVE = "EXHAUSTIVE"
BUDGET_TRUNCATED = "BUDGET_READING_TRUNCATED_ATTEMPT_COUNTS_ARE_FLOORS"
TRUNCATION_BLOCKS_CONCLUSIONS = (
    "A_READING_THAT_REACHED_ITS_BOUND_PUBLISHES_COUNTS_AS_FLOORS_AND_NO_FINAL_"
    "CADENCE_OR_COVERAGE_CONCLUSION_BECAUSE_THE_BOUND_TRUNCATES_THE_TAIL_THAT_"
    "IS_BEING_MEASURED"
)

# ------------------------------------------- the real poll budget (Step 1)
#
# What a poll costs is a *counted* fact or it is not reported. The estimate this
# section replaces — "the budget is roughly the cohort's observations divided by
# its duration" — multiplied two medians over events that were never concurrent
# and would have justified a cadence either way, which is exactly the kind of
# arithmetic that made the first coverage reading wrong.
#
# So each field below is one of three things and says which: MEASURED from a
# recorded timestamp, DERIVED arithmetic over measured values, or NOT_RECORDED.
# Nothing is inferred into existence: this process journals observation rows and
# tick liveness, not HTTP calls, so request/timeout/failure/CPU counts are
# reported absent rather than modelled from row counts.
BUDGET_MEASURED = "MEASURED_FROM_A_RECORDED_TIMESTAMP_IN_A_JOURNAL"
BUDGET_DERIVED = "DERIVED_ARITHMETIC_OVER_MEASURED_VALUES_ONLY"
BUDGET_NOT_RECORDED = (
    "NOT_RECORDED_BY_ANY_JOURNAL_ON_THIS_PATH_REPORTED_ABSENT_RATHER_THAN_"
    "ESTIMATED_BECAUSE_A_MODELLED_COST_CANNOT_CLEAR_A_SAFETY_BAR"
)
BUDGET_RULE = (
    "ONE_POLL_ATTEMPT_IS_ONE_DISTINCT_RECORDED_TICK_INSTANT_FOR_ONE_INSTRUMENT_"
    "COUNTED_FROM_THE_OBSERVATION_JOURNALS_OWN_TIMESTAMPS_NEVER_FROM_A_COHORT_"
    "MEDIAN_AND_NEVER_FROM_A_ROW_COUNT_DIVIDED_BY_A_DURATION"
)
BUDGET_CURRENT = "CURRENT_POLICY"
BUDGET_PROPOSED = "PROPOSED_15S_SHADOW_POLICY"

# The tier split, named from the measurement rather than from configuration. The
# feed router's DEEP/BROAD lists say which names were *meant* to be polled
# often; what the journal shows is which names *were*. Where they disagree the
# journal wins, because the budget is spent by the recorder and not by the list.
BUDGET_TIER_FAST = "FAST_TIER_MEASURED_MEDIAN_TICK_INTERVAL_UNDER_THE_POLICY_CADENCE"
BUDGET_TIER_SLOW = "SLOW_TIER_MEASURED_MEDIAN_TICK_INTERVAL_AT_OR_OVER_THE_POLICY_CADENCE"
BUDGET_TIER_UNMEASURED = "TIER_UNMEASURED_ONE_TICK_INSTANT_GIVES_NO_INTERVAL"

# The safety verdict. Deliberately two-sided: a proposal cheaper than what runs
# today is safe on this evidence, a proposal dearer than it is not approved here
# whatever its research value, and a proposal whose *feasibility* depends on a
# production capture change is neither — it is a redesign, because layering a
# 15-second observation policy over a feed that polls that name once a minute
# cannot produce 15-second observations without changing the feed.
BUDGET_SAFE = "OPERATIONALLY_SAFE_PROPOSED_ATTEMPTS_DO_NOT_EXCEED_MEASURED_CURRENT"
BUDGET_UNSAFE = "CADENCE_POLICY_REQUIRES_REDESIGN"
BUDGET_SAFE_RULE = (
    "THE_PROPOSED_SCHEDULE_IS_SAFE_ONLY_WHEN_ITS_TOTAL_ATTEMPTS_AND_ITS_WORST_"
    "SINGLE_MINUTE_BOTH_SIT_AT_OR_UNDER_THE_MEASURED_CURRENT_ONES_A_RESEARCH_"
    "COMPARISON_IS_NEVER_A_REASON_TO_SPEND_MORE_FEED_THAN_PRODUCTION_ALREADY_DOES"
)

# ------------------------------------- the shadow observation policy (Step 2)
#
# 15 seconds, one schedule, both cohorts. This is a READ-ONLY observation policy:
# it selects which of the already-recorded observations a comparison may look at,
# and it does not ask the recorder for anything. That is what makes it
# implementable without touching production capture — and also what bounds it,
# because an instrument the recorder visited once a minute has no 15-second
# evidence to select and is reported as such instead of being padded.
SHADOW_CADENCE_SEC = 15.0
SHADOW_MAX_GAP_SEC = 60.0
SHADOW_WINDOWS_SEC: tuple[float, ...] = (300.0, 900.0, 1800.0, 3600.0)
SHADOW_POLICY = "PHASE50_SHADOW_OBSERVATION_CADENCE_15S"
SHADOW_POLICY_RULE = (
    "EVERY_ELIGIBLE_EVENT_IS_OBSERVED_ON_THE_SAME_GRID_OF_DECISION_INSTANT_PLUS_"
    "15_SECOND_MULTIPLES_UNTIL_THE_DECLARED_WINDOW_OR_ITS_OWN_SESSION_CLOSE_AND_"
    "THE_OVERLAY_STATE_INSTRUMENT_VEHICLE_AND_COST_STATE_ARE_NOT_INPUTS_TO_THE_"
    "SCHEDULE"
)
SHADOW_IS_READ_ONLY = (
    "THIS_POLICY_SELECTS_FROM_ALREADY_RECORDED_OBSERVATIONS_AND_REQUESTS_NO_"
    "POLL_THE_RAW_HIGHER_FREQUENCY_CAPTURE_IS_UNCHANGED_UNDOWNSAMPLED_AND_"
    "REMAINS_THE_AUDIT_RECORD"
)
SHADOW_NO_BRANCH = (
    "THE_SCHEDULE_IS_A_FUNCTION_OF_THE_DECISION_TIMESTAMP_THE_CADENCE_AND_THE_"
    "WINDOW_ONLY_TWO_EVENTS_DECIDED_AT_THE_SAME_INSTANT_RECEIVE_IDENTICAL_"
    "OBSERVATION_TIMESTAMPS_WHATEVER_THE_OVERLAY_DID_WITH_THEM"
)

# A grid instant is satisfied by the first recorded observation at or after it
# and no later than the tolerated gap. Never the nearest earlier one: reaching
# backwards for a look that happened before the instant is the same substitution
# the pricing rules already refuse, one axis over.
SHADOW_GRID_FILLED = "GRID_INSTANT_SATISFIED_BY_A_RECORDED_OBSERVATION_AT_OR_AFTER_IT"
SHADOW_GRID_MISSED = "GRID_INSTANT_HAD_NO_RECORDED_OBSERVATION_WITHIN_THE_TOLERATED_GAP"
SHADOW_NO_BACKFILL = (
    "A_GRID_INSTANT_IS_NEVER_SATISFIED_BY_AN_EARLIER_OBSERVATION_BY_AN_"
    "INTERPOLATION_BY_A_MIDPOINT_BY_AN_LTP_OR_BY_A_NEIGHBOURING_STRIKE_OR_"
    "CONTRACT_AN_UNSATISFIED_INSTANT_STAYS_UNSATISFIED"
)
SHADOW_INFEASIBLE = (
    "CADENCE_NOT_EQUALIZED_UNDERLYING_CAPTURE_COARSER_THAN_THE_POLICY_CADENCE"
)
SHADOW_EQUALIZED = "CADENCE_EQUALIZED"
SHADOW_NOT_EQUALIZED = "CADENCE_NOT_EQUALIZED"
SHADOW_EQUALIZED_RULE = (
    "BOTH_COHORTS_MEDIAN_POLICY_INTER_OBSERVATION_INTERVAL_MUST_EQUAL_THE_"
    "DECLARED_CADENCE_WITHIN_ONE_CADENCE_STEP_OTHERWISE_CADENCE_NOT_EQUALIZED_"
    "IS_REPORTED_AND_NO_PERFORMANCE_COMPARISON_IS_PUBLISHED"
)
SHADOW_POOLING_FORBIDDEN = (
    "RESULTS_MEASURED_UNDER_THE_OLD_ASYMMETRIC_CADENCE_ARE_NEVER_POOLED_WITH_"
    "RESULTS_MEASURED_UNDER_THIS_POLICY_THE_POLICY_FINGERPRINT_SEPARATES_THEM"
)

# ------------------------- the shadow recorder's slow tier (approved increment)
#
# The observation policy above selects from what was recorded. This is the one
# thing that changes what gets recorded — and only in the research recorder,
# which is the path behind ``settings.phase17_capture`` that writes the Phase 46
# observation journals the diagnostics read. The production scanner, the signal,
# the broker and the order path are not in it.
#
# What it does: the four sampled MCX books were gated to one visit a minute to
# stop a day of capture reaching 784 MB. Measured over 2026-09-17 that gate,
# plus the feed's own arrival rate, left 47 of 86 events unable to answer a
# 15-second grid at all. The gate moves to 15 seconds. The fast tier is NOT
# touched: it already visits faster than any 15-second policy needs, and
# lowering it would cost coverage on the one instrument — CRUDEOIL — where
# selected and declined are already comparable.
#
# What it cannot do, and must not be reported as doing: an instrument whose
# median gap is 87 seconds because the feed only quotes it that often is not
# fixed by a gate that permits more. A permission to visit is not a quote. So
# lifting this tier equalises the *recorder's* contribution to the asymmetry and
# nothing else, and the all-market cadence stays unequal while the fast tier
# runs at about a second.
SHADOW_SLOW_TIER_SEC = 15.0
SHADOW_TIER_POLICY = "PHASE50_SHADOW_SLOW_TIER_15S"
SHADOW_TIER_RULE = (
    "THE_RESEARCH_RECORDERS_SAMPLED_TIER_MAY_VISIT_ONCE_EVERY_15_SECONDS_"
    "INSTEAD_OF_ONCE_EVERY_60_THE_MEMBERSHIP_OF_THE_TIER_IS_UNCHANGED_AND_THE_"
    "FAST_TIER_KEEPS_ITS_EXISTING_EVERY_TICK_CADENCE"
)
SHADOW_TIER_FAST_UNCHANGED = (
    "THE_FAST_TIER_IS_NOT_REDUCED_BY_THIS_INCREMENT_IT_ALREADY_VISITS_FINER_"
    "THAN_THE_POLICY_CADENCE_AND_LOWERING_IT_WOULD_SPEND_COVERAGE_ON_THE_ONLY_"
    "INSTRUMENT_WHOSE_COHORTS_ARE_ALREADY_COMPARABLE"
)
SHADOW_TIER_NO_BRANCH = (
    "THE_TIER_GATE_TAKES_AN_INSTRUMENT_A_CLOCK_AND_THE_LAST_CAPTURE_INSTANT_"
    "ONLY_THE_OVERLAY_STATE_THE_VEHICLE_THE_DIRECTION_THE_COST_STATE_AND_EVERY_"
    "FUTURE_OUTCOME_ARE_NOT_INPUTS_SO_A_SELECTED_AND_A_DECLINED_EVENT_ON_ONE_"
    "INSTRUMENT_ARE_VISITED_ON_THE_SAME_SCHEDULE"
)
SHADOW_TIER_NOT_EQUAL_MARKET = (
    "ALL_MARKET_CADENCE_IS_NOT_EQUAL_BECAUSE_THE_FAST_TIER_STILL_RUNS_AT_ABOUT_"
    "ONE_SECOND_A_LIFTED_SLOW_TIER_IS_NOT_A_GROUND_FOR_LABELLING_THE_MARKET_"
    "CADENCE_EQUAL_OR_FOR_PUBLISHING_AN_ALL_MARKET_SELECTED_VERSUS_DECLINED_ROW"
)
SHADOW_TIER_GATE_IS_NOT_A_QUOTE = (
    "A_PERMISSION_TO_VISIT_IS_NOT_A_QUOTE_AN_INSTRUMENT_THE_FEED_ONLY_PRICES_"
    "EVERY_87_SECONDS_STAYS_COARSER_THAN_THE_POLICY_AND_IS_REPORTED_INFEASIBLE_"
    "RATHER_THAN_COUNTED_AS_EQUALIZED"
)
# Deliberately NOT part of the tier fingerprint: it describes where the gate
# sits in the tick, which is a fact about the existing recorder rather than a
# term of the policy being frozen.
SHADOW_TIER_COST_IS_WRITES = (
    "THE_GATE_SITS_AFTER_THE_LIVE_TICKS_OWN_CHAIN_AND_FUTURES_READ_WHICH_HAPPENS_"
    "ONCE_A_TICK_WHETHER_OR_NOT_THE_RECORDER_IS_PERMITTED_SO_LIFTING_IT_ADDS_"
    "OBSERVATION_BUILD_AND_JOURNAL_WRITE_WORK_AND_ADDS_NO_PROVIDER_REQUEST_THE_"
    "CPU_AND_REQUEST_COUNTERS_REMAIN_UNRECORDED_SO_THIS_IS_A_STATEMENT_ABOUT_"
    "WHERE_THE_GATE_SITS_AND_NOT_A_MEASURED_RESOURCE_CLAIM"
)
SHADOW_TIER_ROLLBACK = (
    "SET_QT_PHASE50_SHADOW_SLOW_TIER_TO_FALSE_TO_RETURN_THE_SAMPLED_TIER_TO_ITS_"
    "PREVIOUS_60_SECOND_GATE_WITHOUT_A_CODE_CHANGE_PRODUCTION_CAPTURE_IS_"
    "UNAFFECTED_EITHER_WAY"
)
# The fresh-sample rule. The experiment starts at its activation instant, and a
# session captured before it is diagnostic evidence rather than equal-policy
# evidence. Sessions are counted, never pooled across the boundary.
SHADOW_TIER_SESSIONS_REQUIRED = 20
SHADOW_TIER_FRESH_SAMPLE = (
    "THE_EQUAL_POLICY_SAMPLE_BEGINS_AT_THE_ACTIVATION_INSTANT_JOURNALLED_WITH_"
    "THIS_FINGERPRINT_SESSIONS_CAPTURED_BEFORE_IT_ARE_NEVER_POOLED_INTO_THE_"
    "PRIMARY_COMPARISON_AND_ABOUT_20_FRESH_SESSIONS_ARE_THE_FIRST_CHECKPOINT"
)
SHADOW_TIER_ACTIVE = "SLOW_TIER_15S_ACTIVE"
SHADOW_TIER_REVERTED = "SLOW_TIER_REVERTED_TO_THE_PREVIOUS_60_SECOND_GATE"

# ------------------------------- the equal-cadence, equal-window design (§6)
#
# A specification, deliberately inert: nothing in this package reads it to decide
# what to poll, and the live scheduler is not touched by this increment. It is
# here rather than in a document so the diagnostic that motivates it and the
# design it motivates cannot drift apart, and so the eventual implementation has
# a frozen text to be diffed against.
#
# The correction it carries over the first version: equal *duration* is not
# enough. Two cohorts watched for the same hour at 3s and at 43s were not given
# the same chance to resolve, so cadence and the worst tolerated gap are declared
# here as first-class terms.
DESIGN_NOT_IMPLEMENTED = (
    "THIS_IS_A_SPECIFICATION_ONLY_NO_CAPTURE_SCHEDULER_POLLING_INTERVAL_OR_"
    "PRODUCTION_PATH_IS_CHANGED_BY_THIS_INCREMENT"
)
EQUAL_OBSERVATION_DESIGN: dict[str, str] = {
    "goal": (
        "EVERY_ELIGIBLE_PRODUCTION_EVENT_RECEIVES_THE_SAME_OBSERVATION_SCHEDULE_"
        "AFTER_ITS_DECISION_AND_THE_OVERLAY_STATE_IS_NOT_AN_INPUT_TO_THAT_"
        "SCHEDULE"
    ),
    "observation_start": (
        "the event's first decision instant, the same field the partition is "
        "read from, so the window opens before the overlay state is known to "
        "the scheduler rather than after it"
    ),
    "observation_end": (
        "min(start + 3600s, the instrument's own session close). Nothing "
        "extends it: not a pending exit, not an unresolved policy, and above "
        "all not the cohort"
    ),
    "polling_cadence": (
        "one observation attempt every 15s for every event in the window, the "
        "same interval for both cohorts. 15s is the existing continuous "
        "threshold, not a new number: it is the coarsest cadence this "
        "diagnostic already calls continuous, so the policy equalises at the "
        "bar it is measured against"
    ),
    "max_inter_observation_gap": (
        "60s. A window where the recorder actually managed one look per minute "
        "is honoured as observed; beyond that the event's own record is marked "
        "as a cadence breach for that interval. The breach is recorded, the "
        "window is not shortened, and the event is never dropped for it"
    ),
    "minimum_quote_quality": (
        "the same executable bar the resolution already uses — both sides "
        "present and fillable, long entered at ASK and exited at BID, short "
        "futures entered at BID and exited at ASK. No midpoint, no traded "
        "print, no interpolation, no substitute strike, contract or session"
    ),
    "stale_quotes": (
        "recorded as observations with their measured quality and never used as "
        "a fill. A stale look still proves the recorder was there, which is "
        "what the cadence measurement needs and what the price model must "
        "refuse"
    ),
    "capture_gaps": (
        "recorded with their measured span and counted against the window's "
        "coverage fraction. A gap never shortens the window and never ends the "
        "event: the whole defect being corrected is a window whose length was a "
        "property of how well the capture happened to be running"
    ),
    "contract_disappearance": (
        "the same contract keeps being polled for the rest of the window and "
        "the absence is recorded per attempt, so a vanished contract appears as "
        "a coverage fraction rather than as a short event. No substitution"
    ),
    "session_close": (
        "the window is truncated at the instrument's close with "
        "UNRESOLVED_WINDOW_TRUNCATED_BY_CLOSE recorded, and never carried into "
        "the next session"
    ),
    "option_chain_disappearance": (
        "treated as contract disappearance for the leg being observed and "
        "recorded as OPTION_SIDE_UNAVAILABLE per attempt. The event is not "
        "re-struck onto whatever the chain does offer"
    ),
    "out_of_window_at_decision": (
        "an event decided inside the last 300s of its session cannot receive "
        "the minimum measurable window, and is marked "
        "OUT_OF_WINDOW_AT_DECISION and excluded from BOTH cohorts. Excluding it "
        "from one only would rebuild the asymmetry by another route"
    ),
    "unresolved_conditions": (
        "UNRESOLVED_NO_EXECUTABLE_ENTRY, UNRESOLVED_NO_MEASURED_COST, "
        "UNRESOLVED_NO_QUOTE_AT_HORIZON, UNRESOLVED_WINDOW_TRUNCATED_BY_CLOSE, "
        "UNRESOLVED_CONTRACT_NOT_QUOTED_IN_WINDOW, OUT_OF_WINDOW_AT_DECISION — "
        "each a recorded fact about the window, never a zero and never a flat "
        "outcome"
    ),
    "cost": (
        "polling declined events on the selected cohort's cadence multiplies "
        "the shadow poll budget by roughly the declined-to-selected ratio. If "
        "the feed budget cannot carry it the fallback is a random sample of "
        "eligible events at the full cadence, with the sampling recorded — "
        "never a longer window or a finer cadence for the selected cohort"
    ),
    "not_implemented": DESIGN_NOT_IMPLEMENTED,
}


def cadence_guard_fingerprint() -> str:
    """The cadence bars, frozen before any interval was read.

    Its own fingerprint rather than a change to the coverage guard's: the
    resolution-rate thresholds behind ``22ed249295627c2c`` are untouched, and a
    reader must be able to see that the cadence requirement was added beside
    them rather than substituted for them.
    """
    raw = "|".join([
        CADENCE_RULE,
        CADENCE_GUARD_RULE,
        f"{CADENCE_CONTINUOUS_MAX_SEC:g}",
        f"{CADENCE_SPARSE_MIN_SEC:g}",
        f"{CADENCE_MAX_RATIO:g}",
        ",".join(CADENCE_LABELS),
        ",".join(BIAS_SOURCES),
        ",".join(f"{cause}={BIAS_SOURCE_FROM_CAUSE[cause]}"
                 for cause in sorted(BIAS_SOURCE_FROM_CAUSE)),
        f"{BIAS_MIN_STOP_SHARE_GAP_PP:g}",
        f"{BIAS_MIN_DEPTH_RATIO:g}",
        f"{BIAS_MIN_CADENCE_RATIO:g}",
        f"{BIAS_MIN_EVENTS_PER_COHORT:d}",
        BIAS_G_IS_CORRELATION_ONLY,
        CADENCE_IS_NOT_PERFORMANCE,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def observation_policy_fingerprint() -> str:
    """The 15-second shadow observation policy, frozen before it selects a row.

    Independent of the four measurement fingerprints and of the cadence guard's,
    for the reason §12 gives: a result measured on the old asymmetric recording
    and a result measured on this grid answer different questions, and pooling
    them would hide the equalisation inside the average it was meant to fix. A
    reader can tell which policy produced a row by this hash alone.
    """
    raw = "|".join([
        SHADOW_POLICY,
        SHADOW_POLICY_RULE,
        f"{SHADOW_CADENCE_SEC:g}",
        f"{SHADOW_MAX_GAP_SEC:g}",
        ",".join(f"{w:g}" for w in SHADOW_WINDOWS_SEC),
        SHADOW_GRID_FILLED,
        SHADOW_GRID_MISSED,
        SHADOW_NO_BACKFILL,
        SHADOW_NO_BRANCH,
        SHADOW_IS_READ_ONLY,
        SHADOW_EQUALIZED_RULE,
        SHADOW_POOLING_FORBIDDEN,
        SHADOW_INFEASIBLE,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def tier_cadence_fingerprint() -> str:
    """The recorder's tier cadence, frozen and journalled at activation.

    Separate from :func:`observation_policy_fingerprint`: that one says which
    already-recorded observations a comparison may read, this one says how often
    the research recorder was permitted to look. A session captured under the
    60-second gate and a session captured under the 15-second gate carry
    different hashes here, which is what stops the fresh sample being quietly
    diluted by the old one.
    """
    raw = "|".join([
        SHADOW_TIER_POLICY,
        SHADOW_TIER_RULE,
        f"{SHADOW_SLOW_TIER_SEC:g}",
        SHADOW_TIER_FAST_UNCHANGED,
        SHADOW_TIER_NO_BRANCH,
        SHADOW_TIER_NOT_EQUAL_MARKET,
        SHADOW_TIER_GATE_IS_NOT_A_QUOTE,
        SHADOW_TIER_ROLLBACK,
        SHADOW_TIER_FRESH_SAMPLE,
        f"{SHADOW_TIER_SESSIONS_REQUIRED:d}",
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def poll_budget_fingerprint() -> str:
    """What counts as one poll attempt, frozen before the journal is counted.

    Here because the definition decides the answer: attempts counted per
    observation ROW rather than per tick INSTANT inflate the current budget by
    however many candidates a tick happened to hold, which would make any
    proposal look cheap.
    """
    raw = "|".join([
        BUDGET_RULE, BUDGET_MEASURED, BUDGET_DERIVED, BUDGET_NOT_RECORDED,
        BUDGET_TIER_FAST, BUDGET_TIER_SLOW, BUDGET_TIER_UNMEASURED,
        BUDGET_SAFE_RULE, BUDGET_SAFE, BUDGET_UNSAFE,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def observation_diagnostic_fingerprint() -> str:
    """The taxonomy and its test order, frozen before it is applied.

    Separate from the four measurement fingerprints on purpose: this diagnostic
    grades the recording, not a trade, and it must be possible to add a stop
    reason later without touching grouping, resolution, exits or the partition.
    """
    raw = "|".join([
        OBSERVATION_DIAGNOSTIC,
        ",".join(STOP_REASONS),
        ",".join(f"{reason}={STOP_REASON_CAUSE[reason]}"
                 for reason in STOP_REASONS),
        ",".join(f"{h:g}" for h in COVERAGE_HORIZONS_SEC),
        f"{SESSION_CLOSE_TOLERANCE_SEC:g}",
        CAUSE_OVERLAY_IS_A_RESIDUAL,
        DIAGNOSTIC_HAS_NO_OUTCOME,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def coverage_guard_fingerprint() -> str:
    """The symmetry threshold, frozen before it is applied to any cohort.

    A guard whose threshold can be loosened after seeing the cohorts is not a
    guard, so both constants and the cost-band edges they are reported beside
    are inside this fingerprint.
    """
    raw = "|".join([
        COVERAGE_GUARD_RULE,
        f"{COVERAGE_MAX_RATE_GAP_PP:g}",
        f"{COVERAGE_MAX_RATE_RATIO:g}",
        COST_BAND_BASIS,
        ",".join(f"{edge:g}" for edge in COST_BAND_EDGES_PCT),
        RESOLUTION_IS_NOT_PERFORMANCE,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def definition_fingerprint() -> str:
    """This phase's own measurement definition — resolution, not grouping.

    Grouping keeps Phase 49's fingerprint untouched. What is fingerprinted here
    is how a resolved event is priced: the sides, the ladder and the refusal to
    fill anywhere but an executable side.
    """
    raw = "|".join([
        PHASE, VERSION, ENTRY_RULE, EXIT_RULE, NO_MIDPOINT,
        SAMPLE_SOURCE, SAMPLE_FRESHNESS,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def partition_fingerprint() -> str:
    """The cohort rule, frozen the way the exit policies are frozen.

    Which states count as a decline is the whole content of the comparison: move
    one state from ``STATE_IS_A_DECLINE`` to the other tuple and the declined
    cohort changes without a single number in the arithmetic changing. So the
    membership of both tuples is inside the fingerprint, not only the prose.
    """
    raw = "|".join([
        PARTITION_RULE,
        ",".join(FILTER_GROUPS),
        ",".join(STATE_IS_A_DECLINE),
        ",".join(STATE_IS_NOT_A_DECISION),
        UNMEASURED_IS_NOT_A_DECLINE,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
