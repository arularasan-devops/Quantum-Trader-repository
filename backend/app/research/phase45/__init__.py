"""Phase 45 — the shadow signal board and its paper journal.

**What this is.** An observation layer beside the production engine. It reads
the evidence Phase 17 already captures at the decision instant — both sides of
the option book, the synchronized futures book, the plan, the trailing range —
and publishes what the *research* definition would have said, on its own board,
into its own journal. It answers one question the production board cannot:
across FUTURES, CE and PE priced at the same instant, which vehicle would have
monetized the opportunity, and what did the round trip cost.

**What this is not.** It is not a strategy, not a promotion and not a signal.
Nothing here is read by the engine, no threshold here is wired to a gate, and
there is no import path from this package to an order, a broker or an execution
module — :mod:`_smoke_phase45` walks the import graph and fails if one appears.
A "paper entry" is a row in a research database.

**Why it fails closed.** Every price it uses must be executable at the decision
instant: a long option enters at the ASK and exits at the BID, a futures leg
takes the side its direction requires. Where the book is absent, one-sided or
stale the row is :data:`SHADOW_UNMEASURED` with a machine-readable reason. A
midpoint, an LTP, a nearest-timestamp quote or the futures price standing in for
a missing option are all refused — each of them turns an unmeasured instant into
a tradeable-looking one, and this project has already published a table built
that way.

**Why the arithmetic is imported, not restated.** The gate quantity comes from
:mod:`app.research.phase42.exante`, the executable fills and costs from
:mod:`app.research.phase35.book`, and the forward path from
:mod:`app.research.phase35.path`. A copied formula is a second formula: it
agrees with its parent until one is corrected. Phase 41/42/44 definitions and
fingerprints are read here and never written.

**Outcomes never reach admission.** :mod:`resolver` writes MFE, MAE, giveback,
exit price and net P&L into a separate table, keyed by event. The evaluator
does not import it and the smoke parses :mod:`evaluator` for any mention of an
outcome field, because the mistake it guards against — a realised peak read as
an entry filter — has already been made once in this project.
"""
from __future__ import annotations

VERSION = "45.0"
PHASE = "PHASE45_SHADOW_SIGNAL_BOARD_AND_PAPER_JOURNAL"

# Relative to the configured data directory, so a session pointed elsewhere
# takes its shadow journal with it.
ARTEFACT_DIR = "phase45"
DB_NAME = "phase45_shadow.db"
MD_NAME = "SHADOW_SIGNALS.md"

# --- what a row is, stated on the row itself -------------------------------
# Production rows and shadow rows must never be pooled by a later reader, so
# the classification is a column rather than a table name.
SHADOW_SIGNAL = "SHADOW_SIGNAL"
PRODUCTION_SIGNAL = "PRODUCTION_SIGNAL"
RESEARCH_ONLY = "RESEARCH_ONLY"
PAPER_ONLY = "PAPER_ONLY"
NO_ORDER_PATH = "NO_ORDER_PATH_NO_BROKER_NO_REAL_MONEY"
NOT_A_PROMOTION = (
    "PAPER_ONLY_NOT_A_SIGNAL: this board reports what a research definition "
    "would have seen at an instant the engine already handled its own way. It "
    "changes no production signal, arms no gate, places no order, and is not "
    "evidence that any vehicle or any definition is profitable."
)

# --- shadow actions --------------------------------------------------------
SHADOW_BUY = "SHADOW_BUY"
SHADOW_SELL = "SHADOW_SELL"
SHADOW_WAIT = "SHADOW_WAIT"
SHADOW_UNMEASURED = "SHADOW_UNMEASURED"
ACTIONS: tuple[str, ...] = (
    SHADOW_BUY, SHADOW_SELL, SHADOW_WAIT, SHADOW_UNMEASURED,
)

# --- why a row is UNMEASURED ----------------------------------------------
# Machine-readable, because "UNMEASURED" alone is unusable three months later:
# a feed that never quotes a size and a feed that went stale at 14:30 need
# opposite fixes and leave the same blank column.
MISSING_BID = "MISSING_BID"
MISSING_ASK = "MISSING_ASK"
ONE_SIDED_BOOK = "ONE_SIDED_BOOK"
CROSSED_BOOK = "CROSSED_BOOK_ASK_BELOW_BID"
STALE_QUOTE = "STALE_QUOTE"
CAPTURE_GAP = "CAPTURE_GAP"
STALE_DECISION_BAR = "STALE_DECISION_BAR"
CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
COST_UNMEASURED = "COST_UNMEASURED"
NO_DELTA = "NO_DELTA_AT_DECISION_INSTANT"
NO_RANGE = "NO_TRAILING_RANGE_BEFORE_DECISION_INSTANT"
NO_DIRECTION = "NO_DIRECTION_AT_DECISION_INSTANT"
NO_LOT_SIZE = "NO_LOT_SIZE"
NO_QUOTE = "NO_QUOTE_FOR_THIS_VEHICLE"
OTHER = "OTHER"
UNMEASURED_REASONS: tuple[str, ...] = (
    MISSING_BID, MISSING_ASK, ONE_SIDED_BOOK, CROSSED_BOOK, STALE_QUOTE,
    CAPTURE_GAP, STALE_DECISION_BAR, CONTRACT_MISMATCH, COST_UNMEASURED,
    NO_DELTA, NO_RANGE, NO_DIRECTION, NO_LOT_SIZE, NO_QUOTE, OTHER,
)

# --- why a measurable row still waits --------------------------------------
# A WAIT is a measured refusal and is journalled with the same evidence as a
# signal: without it the board would only ever record the instants it liked,
# and the base rate of the definition could never be computed.
NOT_A_BUY_CANDIDATE = "ENGINE_CLASS_IS_NOT_A_FRESH_CANDIDATE"
BELOW_LIVE_GATE = "EXPECTED_MOVE_BELOW_THE_LIVE_COST_MULTIPLE"
DIRECTION_DISAGREES = "VEHICLE_OPPOSES_THE_READ_DIRECTION"
WAIT_REASONS: tuple[str, ...] = (
    NOT_A_BUY_CANDIDATE, BELOW_LIVE_GATE, DIRECTION_DISAGREES,
)

# --- paper lifecycle, separate from any production state -------------------
OBSERVED = "OBSERVED"
SIGNALLED = "SHADOW_SIGNAL"
PAPER_ENTRY = "PAPER_ENTRY"
PAPER_OPEN = "PAPER_OPEN"
PAPER_EXIT = "PAPER_EXIT"
RESOLVED = "RESOLVED"
LIFECYCLE: tuple[str, ...] = (
    OBSERVED, SIGNALLED, PAPER_ENTRY, PAPER_OPEN, PAPER_EXIT, RESOLVED,
)

# --- vehicles --------------------------------------------------------------
# CE and PE are never collapsed into one "option" result: the whole point of a
# synchronized board is that the two are priced differently at the same instant
# and only one of them monetized the move.
CE = "CE"
PE = "PE"
FUTURES = "FUTURES"
VEHICLES: tuple[str, ...] = (FUTURES, CE, PE)
VEHICLE_LABELS: dict[str, str] = {
    FUTURES: "FUTURES", CE: "CALL (CE)", PE: "PUT (PE)",
}

# --- freshness -------------------------------------------------------------
# Not a new tolerance: the quote must carry a Phase 17 quality label in the
# FILLABLE set (EXACT or GOOD, i.e. within ten seconds), which is the same bar
# the paper simulator already applies to a costed fill. A row priced off a
# minute-old book is a fabricated fill whichever phase prices it.
MAX_DECISION_BAR_AGE_SEC = 120.0

# --- outcome fields, forbidden inside the evaluator ------------------------
# Parsed by the smoke. Naming them here rather than in prose is what makes the
# "no hindsight" claim checkable.
OUTCOME_FIELDS: tuple[str, ...] = (
    "mfe_pct", "mae_pct", "giveback_pct", "peak_pct", "exit_price", "exit_ts",
    "exit_side", "net_pnl", "gross_pnl", "realized", "t1_hit", "t2_hit",
    "t3_hit", "hold_minutes", "outcome_status",
)
