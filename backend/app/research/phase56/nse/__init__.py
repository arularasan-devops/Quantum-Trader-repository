"""Phase 56 stage 2 — the NSE archive loader. §2/§3/§4 made constructible.

Stage 1 measured the Angel SmartAPI source against §36 and stopped the branch at
``DATASET_INADEQUATE_STOP``: that provider silently back-adjusts its daily
candles and publishes no ex-date, no ratio and no action type, so ``RAW_PRICE``
is unrecoverable, the §4 RAW-vs-ADJUSTED separation is not constructible, and
every level-based rule in §5/§8 (previous-day high, 20/50-day high, 52-week
high, previous-low invalidation) would be quietly wrong across every split and
bonus in the window.

This stage replaces the source rather than the standard. It reads NSE's own
published archives, which are raw:

* **prices** — the daily cash-market bhavcopy. Two published schemas cover the
  window (the legacy ``cmDDMMMYYYYbhav.csv`` and the UDiFF
  ``BhavCopy_NSE_CM_...csv``); both carry OHLC, previous close, traded quantity
  and ISIN, and both are unadjusted. On TATASTEEL's 1:10 ex-date the file
  reports the prior close as 959.40 and the open as 98.10 — the discontinuity is
  *present in the data*, which is precisely what makes the adjustment factor
  derivable and auditable instead of taken on trust;
* **delivery** — ``sec_bhavdata_full_DDMMYYYY.csv`` adds delivered quantity and
  delivery percentage, the §3 "where available" fields. Treated as an optional
  enrichment: a session missing it is a session without delivery data, never a
  session without prices;
* **corporate actions** — ``Bc<ddmmyy>.csv`` inside the daily PR archive, with
  ``EX_DT`` and a ``PURPOSE`` string per security. This is the §4
  ``CORPORATE_ACTION_METADATA`` table: dated, typed, and published by the
  exchange rather than inferred from the price series.

Three design commitments, frozen before any price is read:

1. **raw is immutable.** Ingested rows land in a raw store that is only ever
   appended to. The adjusted research series is a *derived artefact* written
   elsewhere and rebuildable from scratch; nothing in this package can rewrite a
   raw print. §4 asks for the separation to exist; here it is structural, not a
   naming convention.

2. **an unexplained discontinuity is reported, never repaired.** A price jump
   with no corporate action on that ex-date is labelled
   ``UNEXPLAINED_DISCONTINUITY`` and the symbol is excluded around it. A jump
   whose observed ratio contradicts the published ratio is
   ``CA_RATIO_MISMATCH``. Silently smoothing either one is how a data loader
   manufactures returns, so neither is smoothed.

3. **what the archive does not publish is not invented.** Historical *index*
   membership (NIFTY 50 / 500 constituents by date) is not in these files and is
   not available free anywhere. It is therefore not claimed. §5/§11 permit a
   declared alternative, so eligibility is a deterministic liquidity and price
   screen computed from strictly prior sessions (§12), declared here before
   measurement and labelled ``DECLARED_LIQUIDITY_UNIVERSE`` in every artefact —
   it is not called an index universe anywhere.

Research only. No order path, no production signal, no tick hook, no change to
any Phase 41–55 file, artefact or fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date

VERSION = "phase56.v2.nse_archive_loader"

SOURCE = "NSE_PUBLISHED_ARCHIVES"

#: Only the archive host is used. ``www.nseindia.com`` answers scripted GETs with
#: HTTP 403 (edge bot rules); the archive host serves these files with no
#: session, no cookie handshake and no key, which is also why nothing in this
#: package reads a credential.
ARCHIVE_HOSTS = (
    "https://nsearchives.nseindia.com",
    "https://archives.nseindia.com",
)

DATA_CLASS = "HISTORICAL_CANDLE_DATA"
PRICE_CLASS = "RAW_UNADJUSTED_EXCHANGE_BHAVCOPY"
EXECUTION_MODEL = "CASH_EQUITY_LONG_ONLY"

#: The eligible cash-equity series. ``EQ`` is normal rolling settlement. ``BE``
#: and ``BZ`` are trade-to-trade / surveillance series: those rows are *stored*
#: (they are part of what traded that day, and dropping them would silently
#: shrink the historical universe) but they are not eligible for entry, and the
#: audit reports how many names that excludes per session.
ELIGIBLE_SERIES = ("EQ",)
STORED_SERIES = ("EQ", "BE", "BZ")

# --------------------------------------------------------- action taxonomy §4

ACTION_SPLIT = "SPLIT"
ACTION_BONUS = "BONUS"
ACTION_RIGHTS = "RIGHTS"
ACTION_DIVIDEND = "DIVIDEND"
ACTION_MERGER = "MERGER"
ACTION_DEMERGER = "DEMERGER"
ACTION_CONSOLIDATION = "CONSOLIDATION"
ACTION_CAPITAL_REDUCTION = "CAPITAL_REDUCTION"
ACTION_BUYBACK = "BUYBACK"
ACTION_SCHEME_SUSPENSION = "SCHEME_SUSPENSION"
ACTION_SYMBOL_CHANGE = "SYMBOL_CHANGE"
ACTION_NO_PRICE_EFFECT = "NO_PRICE_EFFECT"
ACTION_UNCLASSIFIED = "UNCLASSIFIED"

#: Actions whose price effect is a clean multiplicative factor derivable from the
#: published text alone. Only these adjust the research series.
PRICE_FACTOR_ACTIONS = (ACTION_SPLIT, ACTION_BONUS, ACTION_CONSOLIDATION)

#: Actions that move the price but whose factor is *not* derivable from the
#: published text (a rights issue needs the subscription price and take-up; a
#: merger or capital reduction changes the security itself). These are recorded
#: and become a blackout, never an adjustment. Guessing a factor here would put
#: a fabricated return into the series.
STRUCTURAL_UNADJUSTED_ACTIONS = (
    ACTION_RIGHTS,
    ACTION_MERGER,
    ACTION_DEMERGER,
    ACTION_CAPITAL_REDUCTION,
    ACTION_SCHEME_SUSPENSION,
)

#: Cash dividends are recorded with their amount but do not adjust the series:
#: the study is a price-return long-only cash study, and NSE's own level
#: definitions (52-week high, previous-day high) are price levels. §4 asks for
#: dividends to be *handled*; handling them means labelling the convention, and
#: the dividend amount is carried so a total-return variant stays possible later.
DIVIDEND_ADJUSTS_PRICE = False

# ---------------------------------------------------------- quality labels §4

OK = "OK"
UNEXPLAINED_DISCONTINUITY = "UNEXPLAINED_DISCONTINUITY"
CA_RATIO_MISMATCH = "CA_RATIO_MISMATCH"
CA_UNQUANTIFIED = "CA_UNQUANTIFIED"
BLACKOUT = "BLACKOUT"

#: A single-session close-to-close move beyond this is not treatable as a price
#: move: NSE's widest operating band for a security without derivatives is 20%.
#: Anything past the threshold is either a corporate action or a data error, and
#: the loader must decide which *by looking at the action table*, not by
#: smoothing. Frozen here so the threshold cannot be tuned after seeing results.
DISCONTINUITY_RATIO_LOW = 0.80
DISCONTINUITY_RATIO_HIGH = 1.25

#: Tolerance between the ratio implied by the published action and the ratio
#: observed in the raw prices. Wide enough to absorb a real overnight move on
#: top of the action, tight enough that a wrong ratio cannot hide.
CA_RATIO_TOLERANCE = 0.15

#: Sessions either side of the published ``EX_DT`` that may be searched for the
#: boundary where the prices actually changed basis. The PR bulletin's ex-date is
#: an announcement field and does not always land on that session: HAL's 1:2 face
#: value split printed one session earlier, INOXWIND's 3:1 bonus five sessions
#: later. Applying a factor on the announced date instead of the session the
#: basis changed corrupts every level rule in between, so the loader looks for
#: the matching boundary inside this bounded, pre-declared window and records the
#: offset it used. Outside the window no alignment is attempted.
CA_EX_DATE_SEARCH_SESSIONS = 7

#: A published ex-date must have a session within this many calendar days before
#: any alignment is attempted. BCG's August action would otherwise have been
#: pinned to the October session the name resumed trading on, which is not the
#: session its basis changed and is not a fact this source can establish.
CA_EX_DATE_MAX_CALENDAR_DAYS = 10

# ------------------------------------------------- declared eligibility §5/§12

#: Declared *before* measurement, computed from strictly prior sessions only.
#: Not an index membership list and never described as one.
UNIVERSE_LABEL = "DECLARED_LIQUIDITY_UNIVERSE"
UNIVERSE_LOOKBACK_SESSIONS = 20
UNIVERSE_MIN_MEDIAN_TURNOVER_INR = 5_00_00_000.0  # 5 crore median daily turnover
UNIVERSE_MIN_PRICE_INR = 20.0
UNIVERSE_MIN_PRIOR_SESSIONS = 252
UNIVERSE_TOP_N_BY_TURNOVER = 500

SURVIVORSHIP_CONTROL_FULL = "SURVIVORSHIP_CONTROL_FROM_DATED_EXCHANGE_FILES"
SURVIVORSHIP_CONTROL_LIMITED = "SURVIVORSHIP_CONTROL_LIMITED"

# --------------------------------------------------------------- purpose regex

_SPLIT_RE = re.compile(
    r"FV\s*SPLT?\s*FRM\s*(?:RS|RE)?\s*([0-9]+(?:\.[0-9]+)?)\s*TO\s*(?:RS|RE)?\s*([0-9]+(?:\.[0-9]+)?)",
    re.I,
)
#: A face-value change stated without the ``FV SPLT`` prefix — consolidations are
#: published as "CONSOLIDATION OF SHARES FRM RE 1 TO RS 10".
_FV_CHANGE_RE = re.compile(
    r"FRM\s*(?:RS|RE)?\s*([0-9]+(?:\.[0-9]+)?)\s*TO\s*(?:RS|RE)?\s*([0-9]+(?:\.[0-9]+)?)",
    re.I,
)
_BONUS_RE = re.compile(r"BONUS\s*([0-9]+)\s*:\s*([0-9]+)", re.I)
_RIGHTS_RE = re.compile(r"RIGHTS\s*([0-9]+)\s*:\s*([0-9]+)", re.I)
_DIV_RE = re.compile(
    r"(?:DIV|DIVIDEND|DISTRI)[^0-9]*(?:RS|RE)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:PER|PR)\s*(?:SH|UT)",
    re.I,
)


@dataclass(frozen=True)
class CorporateAction:
    """One published action. ``factor`` multiplies pre-ex-date raw prices."""

    symbol: str
    series: str
    ex_date: date
    purpose: str
    action_type: str
    factor: float | None
    dividend_inr: float | None
    quantified: bool
    source_file: str


def classify_purpose(purpose: str) -> tuple[str, float | None, float | None]:
    """Map a published ``PURPOSE`` string to (action_type, factor, dividend).

    ``factor`` is the multiplier applied to *pre-ex-date* raw prices to put them
    on the post-ex-date basis, and is returned only for the three action types
    whose factor the exchange's own wording fully determines. Everything else
    returns ``None`` and is handled as a blackout or as no price effect — an
    unrecognised string becomes ``UNCLASSIFIED`` rather than being assumed
    harmless, because assuming harmless is the failure that puts a phantom
    -90% return into a backtest.
    """
    text = " ".join((purpose or "").split()).upper()
    if not text:
        return ACTION_UNCLASSIFIED, None, None

    m = _SPLIT_RE.search(text)
    if m is None and ("CONSOLIDAT" in text or "FV CHANGE" in text or "FVCHANGE" in text):
        m = _FV_CHANGE_RE.search(text)
    if m:
        old_fv, new_fv = float(m.group(1)), float(m.group(2))
        if old_fv > 0 and new_fv > 0:
            factor = new_fv / old_fv
            kind = ACTION_SPLIT if new_fv < old_fv else ACTION_CONSOLIDATION
            return kind, factor, None
        return ACTION_UNCLASSIFIED, None, None

    m = _BONUS_RE.search(text)
    if m:
        new, held = float(m.group(1)), float(m.group(2))
        if new > 0 and held > 0:
            # "BONUS a:b" — a new shares for every b held.
            return ACTION_BONUS, held / (held + new), None
        return ACTION_UNCLASSIFIED, None, None

    if _RIGHTS_RE.search(text) or "RIGHTS" in text:
        return ACTION_RIGHTS, None, None

    for needle, kind in (
        ("DEMERGER", ACTION_DEMERGER),
        ("MERGER", ACTION_MERGER),
        ("AMALGAMATION", ACTION_MERGER),
        ("CAPITAL REDUCTION", ACTION_CAPITAL_REDUCTION),
        ("SCHEME", ACTION_SCHEME_SUSPENSION),
        ("NAME CHANGE", ACTION_SYMBOL_CHANGE),
        ("SYMBOL CHANGE", ACTION_SYMBOL_CHANGE),
    ):
        if needle in text:
            return kind, None, None

    if "BUY BACK" in text or "BUYBACK" in text:
        return ACTION_BUYBACK, None, None

    m = _DIV_RE.search(text)
    if m:
        return ACTION_DIVIDEND, None, float(m.group(1))

    for needle in (
        "DIVIDEND",
        "DIV",
        "INTEREST",
        "INT PAYMENT",
        "INTPAYMENT",
        "REDEMPTION",
        "ANNUAL GENERAL MEETING",
        "AGM",
        "EGM",
        "EOGM",
        "DISTRIBUTION",
        "CALL",
        "STP",
    ):
        if needle in text:
            # Price-effect-free for a cash equity holder, or an unparsed cash
            # amount. Either way no factor, and the amount stays unknown rather
            # than being guessed at.
            return (ACTION_DIVIDEND if needle.startswith("DIV") else ACTION_NO_PRICE_EFFECT), None, None

    return ACTION_UNCLASSIFIED, None, None


# ------------------------------------------------------------------ prereg §1

@dataclass(frozen=True)
class Preregistration:
    """Frozen stage-2 declaration. Fingerprinted before any price is read."""

    version: str = VERSION
    source: str = SOURCE
    price_class: str = PRICE_CLASS
    execution_model: str = EXECUTION_MODEL
    eligible_series: tuple[str, ...] = ELIGIBLE_SERIES
    universe_label: str = UNIVERSE_LABEL
    universe_lookback_sessions: int = UNIVERSE_LOOKBACK_SESSIONS
    universe_min_median_turnover_inr: float = UNIVERSE_MIN_MEDIAN_TURNOVER_INR
    universe_min_price_inr: float = UNIVERSE_MIN_PRICE_INR
    universe_min_prior_sessions: int = UNIVERSE_MIN_PRIOR_SESSIONS
    universe_top_n_by_turnover: int = UNIVERSE_TOP_N_BY_TURNOVER
    price_factor_actions: tuple[str, ...] = PRICE_FACTOR_ACTIONS
    structural_unadjusted_actions: tuple[str, ...] = STRUCTURAL_UNADJUSTED_ACTIONS
    dividend_adjusts_price: bool = DIVIDEND_ADJUSTS_PRICE
    discontinuity_band: tuple[float, float] = (
        DISCONTINUITY_RATIO_LOW,
        DISCONTINUITY_RATIO_HIGH,
    )
    ca_ratio_tolerance: float = CA_RATIO_TOLERANCE
    ca_ex_date_search_sessions: int = CA_EX_DATE_SEARCH_SESSIONS
    ca_ex_date_max_calendar_days: int = CA_EX_DATE_MAX_CALENDAR_DAYS
    declared_limitations: tuple[str, ...] = (
        "PUBLISHED_EX_DATE_MAY_NOT_BE_THE_SESSION_THE_BASIS_CHANGED",
        "HISTORICAL_INDEX_MEMBERSHIP_NOT_PUBLISHED_IN_THESE_ARCHIVES",
        "HISTORICAL_SECTOR_CLASSIFICATION_NOT_PUBLISHED_IN_THESE_ARCHIVES",
        "RIGHTS_MERGER_DEMERGER_PRICE_EFFECT_NOT_DERIVABLE_FROM_PUBLISHED_TEXT",
        "DIVIDENDS_RECORDED_BUT_DO_NOT_ADJUST_A_PRICE_RETURN_SERIES",
    )
    prohibited: tuple[str, ...] = (
        "FUTURES",
        "OPTIONS",
        "LEVERAGE",
        "MARGIN",
        "SHORT_SELLING",
        "REAL_BROKER_ORDERS",
        "PHASE_41_55_MODIFICATION",
    )
    notes: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict:
        out = {}
        for key, value in self.__dict__.items():
            out[key] = list(value) if isinstance(value, tuple) else value
        return out

    def fingerprint(self) -> str:
        blob = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


PREREG = Preregistration()
