"""Generic historical CSV import layer.

**What this is.** A way to put a candle file you already hold a licence for on
disk and have the existing candle research read it. It normalises, fingerprints
and registers the file; it reports what the file does and does not contain; and
it stops there. Nothing in this package fetches anything: there is no HTTP
client, no vendor SDK and no scheduler in it, and :mod:`_smoke_historical_import`
walks the import graph and fails if one appears. The only way a row gets in is a
path an operator typed.

**The distinction the whole package exists to protect.** An OHLC candle is
:data:`HISTORICAL_CANDLE_DATA`. It is not a book. It carries no bid, no ask, no
size and no spread, so it cannot say what a round trip would have cost, and a
study priced on it is not a study of execution. Every dataset therefore carries
:data:`bid_ask_status`, every research answer derived from one is labelled
:data:`EXECUTION_UNMEASURED` unless a real two-sided quote is genuinely present
in the source, and no code path here writes a bid, an ask or a spread it was not
given. A midpoint is not a fill. This project has already published one table
built on that assumption and had to withdraw it.

**Fail closed, four ways.** A file whose columns cannot be mapped without
guessing is :data:`IMPORT_REJECTED`, not mapped on a best guess. A file whose
timezone cannot be established from its own timestamps is rejected until an
operator states it, because a five-and-a-half-hour error looks exactly like a
session that opens early. A file mixing symbols, timeframes or contracts is
rejected rather than merged. And a gap is a gap: nothing here interpolates,
carries forward, takes a nearest timestamp or invents a bar, so a thin dataset
reads as thin instead of reading as five clean years.

**Provenance is the point.** The source file is never modified and never moved.
The normalised dataset stores the source's SHA-256, its original name, its
columns, the importer version and a fingerprint of the normalised rows, so a
number in a later report can be traced to the exact bytes it came from — and a
re-import of the same bytes is :data:`ALREADY_IMPORTED` rather than a second
copy inflating every count downstream.

**What it cannot do.** It cannot promote anything. An imported dataset can
produce a :data:`HISTORICAL_LEAD` at best, and the path from there to anything
live is unchanged: live executable validation, paper validation, chronological
holdout, shadow, and only then a decision that is not this package's to make.

    python -m app.research.historical_import.cli inspect <file>
    python -m app.research.historical_import.cli import <file> --source <name>
    python -m app.research.historical_import.cli validate <dataset_id>
    python -m app.research.historical_import.cli list
    python -m app.research.historical_import.cli coverage
"""
from __future__ import annotations

VERSION = "1.0"
SCHEMA_VERSION = "historical_import.v1"

# ---------------------------------------------------------------------------
# What a dataset IS. The first of these is the reason the package exists.
# ---------------------------------------------------------------------------
HISTORICAL_CANDLE_DATA = "HISTORICAL_CANDLE_DATA"
LIVE_EXECUTABLE_BOOK = "LIVE_EXECUTABLE_BOOK"

# Stamped on any research answer that would need a bid and an ask to be true,
# on a dataset that has neither. It is not a warning, it is the answer.
EXECUTION_UNMEASURED = "EXECUTION_UNMEASURED"

# ---------------------------------------------------------------------------
# Import outcomes.
# ---------------------------------------------------------------------------
IMPORTED = "IMPORTED"
ALREADY_IMPORTED = "ALREADY_IMPORTED"
IMPORT_REJECTED = "IMPORT_REJECTED"

# ---------------------------------------------------------------------------
# Rejection reasons. Machine-readable, and each names the thing to fix rather
# than the thing that happened.
# ---------------------------------------------------------------------------
NO_ROWS = "NO_ROWS_THE_FILE_PARSED_TO_NOTHING"
UNMAPPED_COLUMNS = "COLUMNS_COULD_NOT_BE_MAPPED_WITHOUT_GUESSING_SUPPLY_A_MAP"
AMBIGUOUS_COLUMNS = "TWO_COLUMNS_CLAIM_THE_SAME_FIELD_SUPPLY_A_MAP"
MISSING_TIMEZONE = "TIMEZONE_NOT_IN_THE_FILE_SUPPLY_TZ_EXPLICITLY"
UNPARSEABLE_TIMESTAMPS = "TIMESTAMPS_DID_NOT_PARSE"
MIXED_SYMBOLS = "THE_FILE_CONTAINS_MORE_THAN_ONE_SYMBOL"
MIXED_CONTRACTS = "THE_FILE_CONTAINS_MORE_THAN_ONE_CONTRACT_OR_EXPIRY"
MIXED_TIMEFRAMES = "BAR_SPACING_IS_NOT_ONE_TIMEFRAME"
UNKNOWN_TIMEFRAME = "TIMEFRAME_COULD_NOT_BE_ESTABLISHED_SUPPLY_TIMEFRAME"
CONTRACT_AMBIGUOUS = "OPTION_ROWS_WITHOUT_STRIKE_EXPIRY_AND_SIDE"
BAD_PRICES = "TOO_MANY_ROWS_HAVE_IMPOSSIBLE_OR_NON_POSITIVE_PRICES"
DUPLICATE_CONFLICT = "THE_SAME_TIMESTAMP_CARRIES_DIFFERENT_PRICES"
NO_INSTRUMENT = "INSTRUMENT_NOT_IN_THE_FILE_SUPPLY_INSTRUMENT"

# The same reasons as a caller sees them: prefixed with the outcome, because a
# reason travels into reports and registry lines where "MIXED_SYMBOLS" alone
# reads like a property of the data rather than the refusal it caused. Several
# are emitted with a detail suffix (which symbols, how many rows), so a caller
# testing for one compares with ``startswith``.
REJECT: dict[str, str] = {
    "NO_ROWS": f"{IMPORT_REJECTED}:{NO_ROWS}",
    "UNMAPPED_COLUMNS": f"{IMPORT_REJECTED}:{UNMAPPED_COLUMNS}",
    "AMBIGUOUS_COLUMNS": f"{IMPORT_REJECTED}:{AMBIGUOUS_COLUMNS}",
    "MISSING_TIMEZONE": f"{IMPORT_REJECTED}:{MISSING_TIMEZONE}",
    "UNPARSEABLE_TIMESTAMPS": f"{IMPORT_REJECTED}:UNPARSEABLE_ROWS_",
    "MIXED_SYMBOLS": f"{IMPORT_REJECTED}:{MIXED_SYMBOLS}",
    "MIXED_CONTRACTS": f"{IMPORT_REJECTED}:{MIXED_CONTRACTS}",
    "MIXED_TIMEFRAMES": f"{IMPORT_REJECTED}:{MIXED_TIMEFRAMES}",
    "UNKNOWN_TIMEFRAME": f"{IMPORT_REJECTED}:{UNKNOWN_TIMEFRAME}",
    "CONTRACT_AMBIGUOUS": f"{IMPORT_REJECTED}:{CONTRACT_AMBIGUOUS}",
    "BAD_PRICES": f"{IMPORT_REJECTED}:{BAD_PRICES}",
    "DUPLICATE_CONFLICT": f"{IMPORT_REJECTED}:{DUPLICATE_CONFLICT}",
    "NO_INSTRUMENT": f"{IMPORT_REJECTED}:{NO_INSTRUMENT}",
    "FILE_NOT_FOUND": f"{IMPORT_REJECTED}:FILE_NOT_FOUND",
}

# ---------------------------------------------------------------------------
# Quality, contract, option and bid/ask status vocabulary.
# ---------------------------------------------------------------------------
QUALITY_CLEAN = "CLEAN"
QUALITY_ANOMALIES = "ANOMALIES_PRESENT_SEE_REPORT"
QUALITY_REJECTED = "REJECTED"

CONTRACT_PRESENT = "CONTRACT_IDENTITY_PRESENT"
CONTRACT_ABSENT = "NO_CONTRACT_IDENTITY_UNDERLYING_SERIES_ONLY"

OPTION_PRESENT = "STRIKE_EXPIRY_AND_SIDE_PRESENT"
OPTION_ABSENT = "NOT_AN_OPTION_DATASET"

BID_ASK_PRESENT = "TWO_SIDED_QUOTES_PRESENT_IN_THE_SOURCE"
BID_ASK_ABSENT = "OHLC_ONLY_NO_EXECUTABLE_PRICE_EXISTS_IN_THIS_FILE"

# A session whose trading status cannot be established from the dataset. An
# absent Tuesday is not a capture failure unless something independent says the
# exchange was open, and a candle file says nothing about that.
NOT_GRADED = "NOT_GRADED_THE_DATASET_CANNOT_SAY_IF_THIS_DAY_SHOULD_HAVE_TRADED"

# ---------------------------------------------------------------------------
# Research eligibility. Says what a dataset may be used FOR, never what it
# proves. The thresholds are the Phase 24 ones, imported rather than restated
# so a dataset cannot clear a local, friendlier bar.
# ---------------------------------------------------------------------------
ELIGIBLE_FULL = "CANDLE_RESEARCH_WITH_A_FIVE_YEAR_CHRONOLOGICAL_SPLIT"
ELIGIBLE_SHORT = "CANDLE_RESEARCH_DESCRIPTIVE_ONLY_TOO_SHORT_TO_SPLIT"
ELIGIBLE_NONE = "NOT_SUITABLE_FOR_RESEARCH_SEE_DATA_QUALITY"

# The promotion path. Stated here so no caller has to remember it, and so the
# smoke can assert this package never emits anything further along it.
HISTORICAL_LEAD = "HISTORICAL_LEAD"
PROMOTION_CEILING = HISTORICAL_LEAD
PROMOTION_PATH = (
    HISTORICAL_LEAD,
    "LIVE_EXECUTABLE_VALIDATION",
    "PAPER_VALIDATION",
    "CHRONOLOGICAL_HOLDOUT",
    "SHADOW_PRODUCTION",
)
NEVER_FROM_IMPORT = (
    "IMPORTED_HISTORY_CANNOT_ENABLE_A_PRODUCTION_SIGNAL_BY_ITSELF"
)

# ---------------------------------------------------------------------------
# The normalised row. Deliberately small: OHLCV plus identity. Anything the
# source did not contain is absent from the row rather than present as a zero,
# because a zero volume and an unreported volume are different facts.
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = ("time", "open", "high", "low", "close")
OPTIONAL_FIELDS = ("volume", "open_interest")

# Fields that may only ever appear in a row when the SOURCE carried them. They
# are listed so the smoke can assert no code path here writes one.
EXECUTABLE_FIELDS = ("bid", "ask", "bid_size", "ask_size", "spread")

DATA_DIR = "data/historical_import"
REGISTRY_FILE = "registry.jsonl"
BARS_FILE = "bars.jsonl"
MANIFEST_FILE = "dataset.json"
