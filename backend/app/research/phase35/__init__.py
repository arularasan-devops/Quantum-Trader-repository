"""Phase 35 — opportunity attribution and full-market paper trading.

The question this package exists to answer is one sentence long: *is the system
losing money because there is no profitable opportunity, or because it selects,
enters, expresses or exits the opportunity incorrectly?* Every module here is
built to answer that and nothing else. There is no new indicator, no new
predictive rule, no search over families — the vocabularies below are
**attribution labels**, not a strategy space to be optimised over.

Three design decisions are worth stating once, here, because the rest of the
package depends on them.

**Raw is raw.** §21 asks that no observation ever be overwritten. That is
enforced structurally in :mod:`app.research.phase35.store`: the raw tables have
no update path at all, and derived tables are rebuilt from raw rather than
edited in place. A report that cannot be regenerated from raw rows is a report
that cannot be checked.

**Executable or unmeasured.** §4 forbids interpolation, forward-fill, a nearby
quote, LTP substitution and midpoint substitution. So a long option paper entry
exists only where an ask was quoted at the decision instant and an exit exists
only where a bid was quoted. Where they were not, the row is
:data:`UNMEASURED` with a reason string, and it stays in the store — a missing
book is evidence about liquidity, not a row to drop. This is why coverage
percentages appear beside every number in this phase.

**Spread is charged exactly once.** When a fill is taken from the executable
side (buy at ask, sell at bid), the spread is already inside the two prices;
adding a modelled spread on top would charge it twice and make every vehicle
look uneconomic. So :mod:`app.research.phase35.book` charges brokerage and
statutory charges on executable fills and adds a spread term only for fills
that came from a traded price. The two cases carry different evidence labels so
a reader can never confuse them.

What Phase 34 already established, and what this phase therefore treats as the
leading hypothesis rather than a discovery: on real premium candles the gross
edge of the live signal family is about zero on ₹25+ premiums and strongly
positive on sub-₹25 premiums where per-order brokerage alone is ~10% of
premium. That makes *vehicle economics*, not direction, the first suspect — so
:data:`EXCESSIVE_COST` is a first-class cause here and §19's bands are measured
per book rather than assumed away.
"""
from __future__ import annotations

VERSION = "35.0"

# ---------------------------------------------------------------------------
# Evidence vocabulary. Deliberately continuous with Phase 17 (EXACT/GOOD/...)
# and Phase 34 (MEASURED_TRADED_PRICE/SPREAD_MODELLED/...) so three phases of
# rows can be read side by side without a translation table.
# ---------------------------------------------------------------------------
MEASURED_EXECUTABLE = "MEASURED_EXECUTABLE"      # ask-in / bid-out from a real book
MEASURED_TRADED_PRICE = "MEASURED_TRADED_PRICE"  # traded price, spread modelled on top
SPREAD_MODELLED = "SPREAD_MODELLED"              # net figure includes an assumed spread
UNMEASURED = "UNMEASURED"                        # required executable data absent
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"        # sample below the frozen floor

# Prices measured on an executable book, but the contract multiplier the charges
# were divided by came from the instrument specification rather than from the
# quote. Both halves of a cost figure are provenance, and the lot size is the
# half that decides how a flat per-order fee lands in premium points — so a
# spec-sourced lot may not wear MEASURED_EXECUTABLE. It ranks below it
# everywhere, which is why cost coverage counts only the fully measured label.
MEASURED_EXECUTABLE_SPEC_LOT = "MEASURED_EXECUTABLE_LOT_FROM_CONTRACT_SPEC"

EVIDENCE: tuple[str, ...] = (
    MEASURED_EXECUTABLE,
    MEASURED_EXECUTABLE_SPEC_LOT,
    MEASURED_TRADED_PRICE,
    SPREAD_MODELLED,
    UNMEASURED,
    REQUIRES_MORE_DATA,
)

# ---------------------------------------------------------------------------
# Where a leg's lot size came from. Recorded per leg rather than inferred,
# because a cost computed on a contract property is a different kind of number
# from one computed on a captured multiplier and only the row can say which.
# ---------------------------------------------------------------------------
LOT_FROM_QUOTE = "QUOTE_AT_DECISION_INSTANT"
LOT_FROM_PLAN = "PLAN_CONTEXT_AT_DECISION_INSTANT"
LOT_FROM_SPEC = "CONTRACT_SPEC"
LOT_SOURCES: tuple[str, ...] = (LOT_FROM_QUOTE, LOT_FROM_PLAN, LOT_FROM_SPEC)

# Reasons a row is UNMEASURED. A bare UNMEASURED is nearly useless three months
# later: "no ask was quoted" and "the strike was never captured" imply different
# fixes, so the reason is part of the raw row.
NO_BOOK = "NO_TWO_SIDED_BOOK"
NO_ASK = "NO_ASK_AT_DECISION_INSTANT"
NO_BID = "NO_BID_AT_EXIT_INSTANT"
NO_QUOTE = "NO_QUOTE_CAPTURED"
NO_PATH = "NO_FORWARD_PATH_CAPTURED"
STALE_QUOTE = "QUOTE_TOO_OLD_FOR_DECISION_INSTANT"
NO_LOT_SIZE = "LOT_SIZE_UNKNOWN"
SIMULATED_BOOK = "SIMULATED_BOOK_NOT_EVIDENCE"
FIXTURE_ROW = "FIXTURE_ROW_NOT_EVIDENCE"
UNMEASURED_REASONS: tuple[str, ...] = (
    NO_BOOK, NO_ASK, NO_BID, NO_QUOTE, NO_PATH, STALE_QUOTE, NO_LOT_SIZE,
    SIMULATED_BOOK, FIXTURE_ROW,
)

# A quote is evidence only if a real feed produced it. Simulator rows and the
# smoke fixtures both carry a full two-sided book, which is precisely why they
# have to be excluded by provenance rather than by looking at the numbers: a
# fabricated bid/ask is indistinguishable from a real one once it is in a column.
#
# ``PUSH`` is in this set because that is the string the live capture actually
# writes: Phase 17 records ``provider.feed_mode()``, which is ``push`` for a
# live broker WebSocket, ``rest`` for its polled quotes and ``simulated`` for
# the simulator. Spelling the socket "WEBSOCKET" here while the feed spelled it
# "push" is what filed a whole session of real two-sided books as
# SIMULATED_BOOK_NOT_EVIDENCE. The simulator's own label stays out, so the
# provenance guard is unchanged in what it refuses.
REAL_QUOTE_SOURCES: frozenset[str] = frozenset(
    {"WEBSOCKET", "PUSH", "REST", "REAL_BROKER"}
)

# Provenance is not enough on its own: the market provider carries a book
# forward across ticks that arrive without depth, so a genuinely WEBSOCKET-
# sourced bid/ask can be minutes old and still be non-null at the decision
# instant. §4 forbids a nearby quote, and a carried-forward book is exactly
# that. A book is executable only when the feed dated it and that date is
# within this tolerance of the capture; an undated book cannot be proved fresh
# and is therefore not evidence.
EXECUTABLE_BOOK_MAX_AGE_MS = 2000.0

# ---------------------------------------------------------------------------
# §5 — the two paper books. Kept as separate book names on every row rather
# than as two stores, so one query can compare them and neither can be reported
# without the other.
# ---------------------------------------------------------------------------
CURRENT_ENGINE_PAPER = "CURRENT_ENGINE_PAPER"
FULL_MARKET_PAPER = "FULL_MARKET_PAPER"
BOOKS: tuple[str, ...] = (CURRENT_ENGINE_PAPER, FULL_MARKET_PAPER)

# ---------------------------------------------------------------------------
# §2 — where an opportunity observation came from. ENGINE is what the existing
# system decided; BOARD is what the market offered independently of it. The
# whole engine-vs-market comparison is a group-by on this column, which is why
# it is a stored field and not an inference at report time.
# ---------------------------------------------------------------------------
ENGINE = "ENGINE"
BOARD = "BOARD"
SOURCES: tuple[str, ...] = (ENGINE, BOARD)

# Vehicles. NO_TRADE is a vehicle on purpose: "the right answer was to stand
# aside" is an outcome the comparison must be able to express.
CE = "CE"
PE = "PE"
FUTURES = "FUTURES"
NO_TRADE = "NO_TRADE"
VEHICLES: tuple[str, ...] = (CE, PE, FUTURES, NO_TRADE)

LONG = "LONG"
SHORT = "SHORT"
DIRECTIONS: tuple[str, ...] = (LONG, SHORT)

# Capture preserves whatever word the upstream row used, because raw evidence is
# not rewritten to suit a later reader. Phase 17 records a *view*
# (BULLISH/BEARISH); this study reasons about a *position* (LONG/SHORT). Those
# are the same fact in two vocabularies, and the translation belongs here, in one
# place, at the point of reading — the earlier arrangement compared "BULLISH"
# against LONG, found no match, and refused every observation for having no
# direction, which reads as a data problem when it is a spelling problem.
_DIRECTION_SYNONYMS: dict[str, str] = {
    LONG: LONG, "BULLISH": LONG, "BULL": LONG, "UP": LONG, "BUY": LONG,
    "CALL": LONG,
    SHORT: SHORT, "BEARISH": SHORT, "BEAR": SHORT, "DOWN": SHORT,
    "SELL": SHORT, "PUT": SHORT,
}


def normalize_direction(value: object) -> str | None:
    """LONG, SHORT, or None when the row recorded no direction we can read.

    Returns None rather than defaulting: an unrecognised word is not evidence of
    a long view, and assuming one would put those rows on the wrong side of
    every directional split.
    """
    if not isinstance(value, str):
        return None
    return _DIRECTION_SYNONYMS.get(value.strip().upper())


# ---------------------------------------------------------------------------
# §3 — opportunity type vocabulary. Frozen: a type may be added only by editing
# this tuple, so a run cannot invent a label and a report cannot silently widen
# its own hypothesis count.
# ---------------------------------------------------------------------------
DIRECTIONAL = "DIRECTIONAL"
PULLBACK = "PULLBACK"
REVERSAL = "REVERSAL"
BREAKOUT = "BREAKOUT"
CONTINUATION = "CONTINUATION"
VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
RANGE_EXPANSION = "RANGE_EXPANSION"
RELATIVE_VALUE = "RELATIVE_VALUE"
STRUCTURAL = "STRUCTURAL_RELATIONSHIP"
UNCLASSIFIED = "UNCLASSIFIED"
OPPORTUNITY_TYPES: tuple[str, ...] = (
    DIRECTIONAL,
    PULLBACK,
    REVERSAL,
    BREAKOUT,
    CONTINUATION,
    VOLATILITY_EXPANSION,
    RANGE_EXPANSION,
    RELATIVE_VALUE,
    STRUCTURAL,
    UNCLASSIFIED,
)

# ---------------------------------------------------------------------------
# §10 — primary cause of a losing engine BUY. Exactly one per losing trade, and
# only where the data can prove it: OTHER is not a failure of the taxonomy, it
# is the honest answer when nothing in the row discriminates.
# ---------------------------------------------------------------------------
WRONG_DIRECTION = "WRONG_DIRECTION"
WRONG_VEHICLE = "WRONG_VEHICLE"
WRONG_STRIKE = "WRONG_STRIKE"
BAD_ENTRY = "BAD_ENTRY"
INSUFFICIENT_ROOM = "INSUFFICIENT_ROOM"
EXCESSIVE_COST = "EXCESSIVE_COST"
BAD_EXIT = "BAD_EXIT"
GIVEBACK = "GIVEBACK"
OTHER = "OTHER"
UNATTRIBUTED = "UNATTRIBUTED_INSUFFICIENT_DATA"
LOSS_CAUSES: tuple[str, ...] = (
    WRONG_DIRECTION,
    WRONG_VEHICLE,
    WRONG_STRIKE,
    BAD_ENTRY,
    INSUFFICIENT_ROOM,
    EXCESSIVE_COST,
    BAD_EXIT,
    GIVEBACK,
    OTHER,
)

# §18 — which channel profitability was lost through. Narrower than the cause
# list above and answers a different question: the cause says what to change,
# the channel says where the money went.
CHANNEL_COST = "COST"
CHANNEL_DIRECTION = "DIRECTION"
CHANNEL_ENTRY = "ENTRY"
CHANNEL_VEHICLE = "VEHICLE"
CHANNEL_EXIT = "EXIT"
CHANNEL_GIVEBACK = "GIVEBACK"
CHANNEL_NONE = "PROFITABLE_NO_LOSS_CHANNEL"
CHANNELS: tuple[str, ...] = (
    CHANNEL_COST,
    CHANNEL_DIRECTION,
    CHANNEL_ENTRY,
    CHANNEL_VEHICLE,
    CHANNEL_EXIT,
    CHANNEL_GIVEBACK,
)

# §11 / §13 labels.
ENGINE_MISSED_WINNER = "ENGINE_MISSED_WINNER"
ENGINE_AVOIDED_LOSER = "ENGINE_AVOIDED_LOSER"
COUNTERFACTUAL_VEHICLE_COMPARISON = "COUNTERFACTUAL_VEHICLE_COMPARISON"

# Historical status vocabulary. §1 is explicit that a historical result may be a
# CANDIDATE and may never be VALIDATED by history alone, so VALIDATED is simply
# not a value this phase can emit.
CANDIDATE = "CANDIDATE"
DESCRIBED = "DESCRIBED_ONLY"
REJECTED = "REJECTED"
STATUSES: tuple[str, ...] = (CANDIDATE, DESCRIBED, REJECTED, REQUIRES_MORE_DATA)

# ---------------------------------------------------------------------------
# §8 — the horizons every paper entry is measured at, in minutes. SESSION_CLOSE
# is not a minute count and is carried separately so nothing has to encode a
# session length that differs between NFO and MCX.
# ---------------------------------------------------------------------------
HORIZONS: tuple[int, ...] = (1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120)
SESSION_CLOSE = "SESSION_CLOSE"

# ---------------------------------------------------------------------------
# §16 — evidence floors, frozen. These are the numbers that decide whether a
# result may be spoken about as evidence, so they live in code where a run
# cannot argue with them.
# ---------------------------------------------------------------------------
CHECKPOINT1_SESSIONS = 20                # observation checkpoint only
GENERAL_MIN_TRADES = 100
GENERAL_MIN_SESSIONS = 50
PAIR_MIN_TRADES = 200
PAIR_MIN_SESSIONS = 60
PAIR_MIN_COST_COVERAGE_PCT = 80.0

# §19 — economic bands, measured never enabled. The premium bands are the ones
# Phase 34 measured on real premium candles, kept identical so the two phases'
# tables can be compared row for row.
PREMIUM_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 25.0), (25.0, 100.0), (100.0, float("inf")),
)
SPREAD_PCT_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 1.0), (1.0, 3.0), (3.0, 5.0), (5.0, float("inf")),
)
# Room/cost multiples. 3x is the ratio the live flow gate already uses, so the
# table can say what that choice costs and what it saves rather than assuming it.
ROOM_COST_MULTIPLES: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0)

# Everything in this package is one of these two, on every artefact and every
# API payload, so a panel cannot present a research row as a live instruction.
RESEARCH_ONLY = "RESEARCH_ONLY"
PAPER_ONLY = "PAPER_ONLY"

DB_NAME = "opportunity.db"
ARTEFACT_DIR = "data/phase35"
