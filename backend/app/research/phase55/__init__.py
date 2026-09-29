"""Phase 55 — MARKET_WIDE_INSTRUMENT_RESEARCH. Declaration and fingerprint.

Phases 51–54 returned zero robust candidates on two instruments. Two different
explanations survive that result and they are not distinguishable on two names:

A. the mechanisms are genuinely weak;
B. the instrument coverage was too narrow for a ten-events-a-year mechanism to
   accumulate a gradeable sample.

This phase exists to separate A from B, and nothing else. It does not invent a
mechanism, relax a gate or re-interpret a prior phase. It widens the universe,
runs the Phase 53 and Phase 54 families **unmodified** across whatever survives
the history audit, and corrects over the whole enlarged denominator — so a
candidate that only appears because twenty names were tested is penalised for
exactly that, not rewarded for it.

Three properties are frozen here before any data is collected:

* **the universe is declared, not discovered.** The twenty names below are fixed
  in this file. An instrument that fails the history audit is reported as failed
  and excluded; it is never swapped for a different name that happens to have
  cleaner data, because choosing instruments after seeing their data is the
  same error as choosing parameters after seeing their P&L;
* **per-instrument results are always reported beside any pooled number.**
  Pooling twenty names until a t-statistic crosses a threshold is the easiest
  available way to manufacture significance here, so the per-instrument table is
  the primary output and the pooled row is a summary of it, never a substitute;
* **the cost model is the shared one.** Every instrument is costed through
  ``phase19.futcosts`` reached via Phase 24, the same model Phases 51–54 used.
  Phase 53 died on cost, so a research layer that quietly adopted a cheaper cost
  model for the new names would produce a "discovery" that is nothing but the
  new assumption.

Research only. No order path, no production signal, no tick hook, no change to
any Phase 41–54 file or fingerprint.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "phase55.v1"

# --------------------------------------------------------------- the universe

#: Cash index series. No contract, no expiry, no roll: for these four the data
#: provenance question that dogs the futures names does not arise at all, which
#: makes them the cleanest evidence in the study.
CLASS_INDEX = "INDEX_CASH"
#: Cash equity series, likewise roll-free. Traded as futures or options in
#: practice, so the cost model is charged at the F&O lot, not at one share.
CLASS_EQUITY = "EQUITY_CASH"
#: MCX futures, carrying the provider-roll caveat established by the mcxhist
#: stage: one provider token per root, contract identity not exposed.
CLASS_MCX = "MCX_FUTURES"

#: instrument -> (class, exchange, session minutes). Session minutes are the
#: exchange's own, used to grade per-session coverage; NSE runs 09:15–15:30
#: (375 minutes) and MCX runs to 23:30 (870).
UNIVERSE: dict[str, tuple[str, str, int]] = {
    # index (4)
    "NIFTY": (CLASS_INDEX, "NSE", 375),
    "BANKNIFTY": (CLASS_INDEX, "NSE", 375),
    "FINNIFTY": (CLASS_INDEX, "NSE", 375),
    "MIDCPNIFTY": (CLASS_INDEX, "NSE", 375),
    # MCX (5)
    "CRUDEOIL": (CLASS_MCX, "MCX", 870),
    "GOLD": (CLASS_MCX, "MCX", 870),
    "SILVER": (CLASS_MCX, "MCX", 870),
    "NATURALGAS": (CLASS_MCX, "MCX", 870),
    "COPPER": (CLASS_MCX, "MCX", 870),
    # equity (11)
    "RELIANCE": (CLASS_EQUITY, "NSE", 375),
    "HDFCBANK": (CLASS_EQUITY, "NSE", 375),
    "ICICIBANK": (CLASS_EQUITY, "NSE", 375),
    "INFY": (CLASS_EQUITY, "NSE", 375),
    "TCS": (CLASS_EQUITY, "NSE", 375),
    "SBIN": (CLASS_EQUITY, "NSE", 375),
    "AXISBANK": (CLASS_EQUITY, "NSE", 375),
    "ITC": (CLASS_EQUITY, "NSE", 375),
    "LT": (CLASS_EQUITY, "NSE", 375),
    "BHARTIARTL": (CLASS_EQUITY, "NSE", 375),
    "TATAMOTORS": (CLASS_EQUITY, "NSE", 375),
}

#: Instruments whose five-year file the repository already ships. They are
#: audited like the rest but never re-collected and never overwritten, so no
#: Phase 51–54 result changes its data source underneath it.
ALREADY_SHIPPED: tuple[str, ...] = ("NIFTY", "CRUDEOIL")

# ------------------------------------------------------------- collection

ONE_MINUTE = "ONE_MINUTE"
DEFAULT_START = "2021-08-02"

#: Reused verbatim from the mcxhist stage, where they were measured against the
#: provider's actual rate limiting rather than guessed.
CHUNK_DAYS = 5
MIN_INTERVAL_SEC = 3.4
MAX_ATTEMPTS = 4
BACKOFF_START_SEC = 20.0

# --------------------------------------------------------------- audit gate

#: A session counts as fully covered at 90% of its exchange minutes and as
#: partially covered at 60%. Below that it is not graded — present but not
#: usable as a session.
SESSION_COVERAGE_OK = 0.90
SESSION_COVERAGE_PARTIAL = 0.60

#: A chronological 60/20/20 split needs enough sessions in every leg for the
#: holdout to mean anything. 750 graded sessions is three development years, one
#: validation year and one untouched year; below it the instrument is excluded
#: rather than run on a shorter window and compared against names with five.
MIN_GRADED_SESSIONS = 750
MIN_BARS = 100_000

GATE_INCLUDED = "INCLUDED"
GATE_EXCLUDED_HISTORY = "EXCLUDED_INSUFFICIENT_HISTORY"
GATE_EXCLUDED_QUALITY = "EXCLUDED_DATA_QUALITY"
GATE_UNRESOLVED = "EXCLUDED_TOKEN_UNRESOLVED"

DATA_CLASS = "HISTORICAL_CANDLE_DATA"
SERIES_CLASS_CASH = "CASH_SERIES_NO_ROLL"
SERIES_CLASS_FUTURES = "PROVIDER_CONTINUOUS_UNVERIFIED_ROLL"

# ----------------------------------------------------------------- families

#: The two mechanism families re-run across the universe, by the phase that
#: froze them. Neither definition is edited here; this phase only changes which
#: instruments they see and how large the correction denominator is.
FAMILIES: tuple[str, ...] = ("ORB_RETEST_P53", "MULTIDAY_EXPANSION_P54")


def preregistration() -> dict:
    """The frozen declaration. Anything not in here is not part of the phase."""
    return {
        "version": VERSION,
        "purpose": (
            "separate 'the mechanisms are weak' from 'the universe was too "
            "narrow' by re-running the Phase 53 and Phase 54 families, "
            "unmodified, across a declared twenty-instrument universe"
        ),
        "universe": {k: list(v) for k, v in sorted(UNIVERSE.items())},
        "already_shipped": list(ALREADY_SHIPPED),
        "families": list(FAMILIES),
        "interval": ONE_MINUTE,
        "start": DEFAULT_START,
        "chunk_days": CHUNK_DAYS,
        "min_interval_sec": MIN_INTERVAL_SEC,
        "max_attempts": MAX_ATTEMPTS,
        "backoff_start_sec": BACKOFF_START_SEC,
        "session_coverage_ok": SESSION_COVERAGE_OK,
        "session_coverage_partial": SESSION_COVERAGE_PARTIAL,
        "min_graded_sessions": MIN_GRADED_SESSIONS,
        "min_bars": MIN_BARS,
        "data_class": DATA_CLASS,
        "instrument_selection": (
            "declared in code before collection; an instrument that fails the "
            "audit is excluded and reported, never replaced by a better-looking "
            "name"
        ),
        "correction": (
            "the multiple-testing denominator is the registered grid of each "
            "family times the number of instruments actually graded, so "
            "widening the universe makes the correction harsher, not easier"
        ),
        "pooling": (
            "per-instrument results are the primary output; any pooled number "
            "is reported beside them and never in place of them"
        ),
        "costs": (
            "the shared phase19/phase24 cost model for every instrument, "
            "unchanged from Phases 51-54, charged at the F&O lot"
        ),
        "look_ahead": (
            "none introduced here: the families' own bar construction, entry "
            "timing and causal-ATR rules are used as frozen by Phase 53/54"
        ),
        "live_capture_boundary": (
            "data/history.db is neither read nor written by this package; "
            "live-capture rows are not history"
        ),
        "options": (
            "no historical option bid/ask/depth is fabricated; vehicle "
            "selection stays with the live executable book"
        ),
        "orders": "no broker order path exists in this package",
    }


def fingerprint() -> str:
    blob = json.dumps(preregistration(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
