"""Phase 56 — CASH_EQUITY_LONG_ONLY_DISCOVERY. Stage 1: the §36 data gate.

The request is a long-only NSE cash-equity study: buy shares, hold, sell shares,
across the *historical* equity universe with survivorship-bias control,
corporate-action-aware prices, realistic costs, chronological validation and an
untouched holdout.

Its own §36 fixes the order of work, and this module is that order made literal:

    If adequate historical equity data is not currently available, do NOT
    proceed with a weak subset and call it a five-year market study. First
    report DATA_AVAILABLE / DATA_MISSING / UNIVERSE_COVERAGE /
    CORPORATE_ACTION_COVERAGE / HISTORICAL_MEMBERSHIP_COVERAGE /
    SURVIVORSHIP_CONTROL_STATUS. Then stop until the dataset is adequate.

So stage 1 contains no mechanism, no feature library, no portfolio construction
and no verdict about any strategy. It measures what the available source can and
cannot supply, and it computes one gate. A cross-sectional long-only stock study
run on a universe of today's survivors does not merely carry a caveat: ranking
2,690 names that are *known to still exist* by trailing return is a procedure
that manufactures momentum edges out of nothing, and no correction downstream
repairs it. That is why the gate is a hard stop rather than a warning printed
above a results table.

Three things are frozen here before any measurement:

* **the probe sets are declared, not chosen after seeing results.** The
  corporate-action probe list and the vanished-security probe list are fixed
  below with their published event dates and expected ratios. A provider that
  fails the probe cannot be rescued by substituting a friendlier symbol;
* **the gate thresholds are declared.** ``ADEQUATE`` requires every one of the
  six §36 fields to be satisfiable. Partial credit is reported as partial, and
  partial does not open the gate;
* **absence of evidence is labelled as such.** A probe that fails on a
  rate-limit or a timeout returns ``INCONCLUSIVE``, never ``MISSING`` — the
  mcxhist stage learned that lesson the hard way, where a throttled probe was
  indistinguishable from an absent series.

Research only. No order path, no production signal, no tick hook, no change to
any Phase 41–55 file, artefact or fingerprint.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "phase56.v1.stage1_data_gate"

# ------------------------------------------------------------------ the source

#: The only source this stage measures. Named so the audit cannot be read as a
#: statement about Indian equity data in general: it is a statement about what
#: *this* provider exposes.
SOURCE = "ANGEL_SMARTAPI_SCRIP_MASTER_AND_GETCANDLEDATA"

#: Published scrip master. Lists instruments that are tradable *now*.
SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

NSE = "NSE"
EQ_SUFFIX = "-EQ"
ONE_DAY = "ONE_DAY"

#: Seconds between historical calls. The provider bans on burst; the probe is
#: small, but it is spaced anyway so a probe can never be the reason a later
#: collection is throttled.
MIN_INTERVAL_SEC = 3.0

#: A throttled request is retried this many times, doubling the wait each time.
#: Retrying is not politeness: a probe that reported ABSENT on a rate limit would
#: label a present dataset missing, which is the one error this stage cannot make.
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_BACKOFF_SEC = 20.0

# ----------------------------------------------------------- security statuses

#: §2/§3 date-aware statuses. Stage 1 can only *assign* the ones the source
#: supports, and the audit reports which ones are unassignable — that is the
#: finding, not a gap in the code.
STATUS_ACTIVE = "ACTIVE"
STATUS_DELISTED = "DELISTED"
STATUS_SUSPENDED = "SUSPENDED"
STATUS_MERGED = "MERGED"
STATUS_DEMERGED = "DEMERGED"
STATUS_RENAMED = "RENAMED"
STATUS_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"

ALL_SECURITY_STATUSES = (
    STATUS_ACTIVE,
    STATUS_DELISTED,
    STATUS_SUSPENDED,
    STATUS_MERGED,
    STATUS_DEMERGED,
    STATUS_RENAMED,
    STATUS_INSUFFICIENT_HISTORY,
)

# --------------------------------------------------------------- probe outcomes

PROBE_PRESENT = "PRESENT"
PROBE_ABSENT = "ABSENT"
PROBE_INCONCLUSIVE = "INCONCLUSIVE"

#: Price-series adjustment verdicts. ``PRE_ADJUSTED`` means the provider already
#: applied the corporate action retroactively and did not say so; ``RAW`` means
#: the split/bonus appears as an uncorrected overnight jump.
SERIES_RAW = "RAW_UNADJUSTED"
SERIES_PRE_ADJUSTED = "PROVIDER_PRE_ADJUSTED_UNRECORDED"
SERIES_UNDETERMINED = "UNDETERMINED"

# ------------------------------------------------------ declared probe: actions

#: Published NSE corporate actions inside the provider's coverage, each with the
#: ratio a *raw* series must show across the ex-date. Ratio is
#: ``price_after / price_before``: a 1:10 split is 0.1, a 1:1 bonus is 0.5.
#: Fixed here so the verdict cannot be steered by picking a convenient event.
CORPORATE_ACTION_PROBES: tuple[dict, ...] = (
    {"symbol": "NESTLEIND", "ex_date": "2024-01-05", "action": "SPLIT_1_FOR_10", "ratio": 0.10},
    {"symbol": "TATASTEEL", "ex_date": "2022-07-28", "action": "SPLIT_1_FOR_10", "ratio": 0.10},
    {"symbol": "RELIANCE", "ex_date": "2024-10-28", "action": "BONUS_1_1", "ratio": 0.50},
    {"symbol": "WIPRO", "ex_date": "2024-12-03", "action": "BONUS_1_1", "ratio": 0.50},
    {"symbol": "BAJFINANCE", "ex_date": "2025-06-16", "action": "SPLIT_1_FOR_2_AND_BONUS_4_1", "ratio": 0.10},
)

#: A raw jump counts as detected when the observed close-to-close ratio sits
#: within this tolerance of the published ratio. Wide enough to absorb an
#: ordinary day's move on top of the action, narrow enough that it cannot fire
#: on an ordinary day alone.
ACTION_RATIO_TOLERANCE = 0.25

#: Days either side of the ex-date the probe reads, so a shifted ex-date or a
#: holiday cannot be mistaken for an absent action.
ACTION_WINDOW_DAYS = 12

# --------------------------------------------- declared probe: vanished names

#: Securities that traded on NSE inside the study window and then ceased to
#: exist under that symbol. Every one of them belongs in a survivorship-correct
#: universe for the part of the window it was tradable in. Their presence or
#: absence in the source is the survivorship measurement.
VANISHED_SECURITY_PROBES: tuple[dict, ...] = (
    {"symbol": "HDFC", "event_date": "2023-07-13", "event": "MERGED_INTO_HDFCBANK"},
    {"symbol": "MINDTREE", "event_date": "2022-11-24", "event": "MERGED_INTO_LTIM"},
    {"symbol": "LTI", "event_date": "2022-11-24", "event": "MERGED_INTO_LTIM"},
    {"symbol": "SRTRANSFIN", "event_date": "2022-11-24", "event": "RENAMED_SHRIRAMFIN"},
    {"symbol": "IDFC", "event_date": "2024-10-03", "event": "MERGED_INTO_IDFCFIRSTB"},
    {"symbol": "PVR", "event_date": "2023-02-06", "event": "RENAMED_PVRINOX"},
)

# ------------------------------------------------------- declared probe: cover

#: Symbols whose daily series is read end to end to measure the provider's
#: history floor and per-symbol call cost. Liquid, continuously listed, spread
#: across sectors; none of them is a study candidate, they are rulers.
COVERAGE_PROBES: tuple[str, ...] = (
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ITC",
    "MARUTI",
    "SUNPHARMA",
)

#: The window the study would need. §18 tests holds out to 60 sessions and §10
#: wants a 52-week high, so a usable series needs the window plus a year of
#: warm-up before it.
STUDY_START = "2021-08-02"
WARMUP_SESSIONS = 252
MIN_SESSIONS_PER_SECURITY = 504

# ------------------------------------------------------------------ gate labels

GATE_ADEQUATE = "DATASET_ADEQUATE_PROCEED"
GATE_INADEQUATE = "DATASET_INADEQUATE_STOP"
GATE_INCONCLUSIVE = "PROBE_INCONCLUSIVE_RETRY"

COVERAGE_FULL = "FULL"
COVERAGE_PARTIAL = "PARTIAL"
COVERAGE_NONE = "NONE"

SURVIVORSHIP_CONTROLLED = "SURVIVORSHIP_BIAS_CONTROLLED"
SURVIVORSHIP_LIMITED = "SURVIVORSHIP_BIAS_CONTROL_LIMITED"
SURVIVORSHIP_UNKNOWN = "SURVIVORSHIP_BIAS_CONTROL_UNKNOWN"

#: What stage 2 would need, stated now so "adequate" is not redefined later.
REQUIRED_FOR_STAGE_2: tuple[str, ...] = (
    "a security master carrying every symbol tradable during the window, "
    "including those since delisted, merged, renamed or suspended",
    "first_trade_date and last_known_trade_date per security, from the source "
    "rather than inferred from where the candles happen to begin",
    "a corporate-action table (split, bonus, rights, merger, demerger, "
    "consolidation, symbol change, dividend) with ex-dates and ratios",
    "raw unadjusted daily OHLCV, so RAW_PRICE and ADJUSTED_RESEARCH_PRICE can "
    "be preserved separately as §5 requires",
    "historical index membership by date for the ranking universe (§15/§23), "
    "or an explicit declared alternative eligibility rule",
    "a dated cash-equity cost schedule covering the window (§8)",
)


def preregistration() -> dict:
    """The frozen declaration. Fingerprinted; written before any measurement."""
    return {
        "version": VERSION,
        "phase": "PHASE_56_CASH_EQUITY_LONG_ONLY_DISCOVERY",
        "stage": "STAGE_1_DATA_AVAILABILITY_GATE",
        "source": SOURCE,
        "execution_model": "BUY_SHARES_HOLD_SELL_SHARES",
        "prohibited": [
            "FUTURES",
            "OPTIONS",
            "LEVERAGE",
            "SHORT_SELLING",
            "MARGIN",
            "BROKER_ORDER_PATH",
            "PRODUCTION_SIGNAL_CHANGE",
            "PHASE_41_TO_55_MODIFICATION",
        ],
        "study_window_start": STUDY_START,
        "warmup_sessions": WARMUP_SESSIONS,
        "min_sessions_per_security": MIN_SESSIONS_PER_SECURITY,
        "security_statuses": list(ALL_SECURITY_STATUSES),
        "corporate_action_probes": [dict(p) for p in CORPORATE_ACTION_PROBES],
        "action_ratio_tolerance": ACTION_RATIO_TOLERANCE,
        "vanished_security_probes": [dict(p) for p in VANISHED_SECURITY_PROBES],
        "coverage_probes": list(COVERAGE_PROBES),
        "gate_rule": (
            "ADEQUATE requires all six §36 fields satisfiable: a historically "
            "complete security master, dated listing/delisting, corporate "
            "actions, raw prices, dated membership and a dated cost schedule. "
            "Any MISSING field yields STOP. Any INCONCLUSIVE probe yields "
            "RETRY, never STOP, so a throttled provider is never recorded as "
            "an absent dataset."
        ),
        "required_for_stage_2": list(REQUIRED_FOR_STAGE_2),
    }


def fingerprint() -> str:
    """Stable 16-hex digest of the declaration."""
    body = json.dumps(preregistration(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()[:16]
