"""Phase 52 — GAP FAILURE -> RANGE RE-ENTRY -> REVERSION, one mechanism, frozen.

One hypothesis, stated in full before anything is measured, and measured once.
Phase 51 searched five thousand rules and returned zero; this phase does the
opposite thing: a single named mechanism, a bounded robustness grid around it,
and an honest verdict on whether it has repeatable positive net expectancy after
realistic cost.

Everything that could be chosen after seeing a result is chosen here instead:
the gap thresholds, the confirmation timeframes, the stop cap, the cost gate,
the target, the hold limit, the sample floors, the promotion bar and the
partition boundaries. The fingerprint over this module's declared values is
printed by ``cli prereg`` before the study runs, so the grid cannot grow a
parameter after the fact without changing the fingerprint.

**Phases 41-51 are not touched.** Their definitions and fingerprints are
imported nowhere for modification; this phase reuses three of their *functions*
(the Phase 27 resampler, the Phase 24 cost model, the Phase 51 BH correction)
precisely so that its numbers reconcile against theirs rather than drifting into
a friendlier cost or a looser correction of its own.

Four things this engine will not do:

* it will not fabricate an option quote. §12 is futures-only, the historical file
  has no option book, and a premium modelled from an underlying candle would be
  an invention. The vehicle question is deferred to the live executable book;
* it will not report the best of thirty-six parameterizations as a discovery.
  Nested thresholds select nested event sets, so the grid is collapsed to
  **unique event families** by hashing each variant's actual entry set, and the
  multiplicity correction runs over families;
* it will not read a future bar to decide an entry. The gap needs the previous
  session closed, the failure needs a *completed* bar, the fill is the next bar's
  open, and the smoke test recomputes the whole event set on truncated data and
  asserts the prefix is unchanged;
* it will not call any of this a prediction, a recommendation or a signal.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "phase52.v1"
MECHANISM = "GAP_FAILURE_RANGE_REENTRY_REVERSION"

SOURCE = "HISTORICAL_CANDLE_DATA"
VEHICLE = "FUTURES_ONLY"

# --- the mechanism, frozen ------------------------------------------------
#
# Decision timeframe. §1 asks for completed 5-minute bars derived without
# look-ahead; the Phase 27 resampler already labels an aggregated bar with its
# closing minute, which is the instant a decision on it can be taken.
DECISION_TIMEFRAME_MINUTES = 5

# §4's waiting period. No entry may be decided on a bar that closes inside the
# first fifteen minutes of the session, so the opening print and the two bars
# after it can define the opening area but can never trigger a trade.
WAIT_MINUTES = 15

# §3's gap thresholds, in ATR beyond the previous day's extreme.
GAP_ATR_THRESHOLDS = (0.10, 0.20, 0.30)

# §13's two confirmations. A 5-minute close back inside the previous day's
# range, or the close of a completed 15-minute bucket.
CONFIRM_5M = "CLOSE_5M_INSIDE_RANGE"
CONFIRM_15M = "CLOSE_15M_INSIDE_RANGE"
CONFIRMATIONS = (CONFIRM_5M, CONFIRM_15M)

# §7's stop: the failed extension's extreme, pushed out by this much ATR.
STOP_BUFFER_ATR = 0.25
# §7/§13's cap on stop distance. Wider than this and the session is refused.
MAX_STOP_ATR = (0.50, 0.75)

# §9's economic feasibility gate: the distance to target measured in round trips.
COST_GATE_MULTIPLES = (3.0, 4.0, 5.0)

# §10's hold limit, in minutes from the fill.
MAX_HOLD_MINUTES = 120

# §11: at most one trade per instrument per session. A consequence worth
# stating, because it decides what the trade count means: **trade count and
# independent session count are identical by construction**, so no confidence
# interval here is inflated by two trades on one move.
MAX_TRADES_PER_SESSION = 1

# The ATR used everywhere above is a trailing Wilder ATR over **completed daily
# sessions**, not over intraday bars. Both readings are defensible and they are
# not interchangeable: 0.10 of a 5-minute ATR is a fraction of a point, which
# would admit essentially every gap and collapse §13's 0.10/0.20/0.30 grid into
# one cell, while 0.10 of a daily ATR is the ordinary meaning of "the session
# opened clear of yesterday's high". The daily reading is therefore frozen here,
# and it is a declared interpretation of §2 rather than a measured result.
ATR_WINDOW_SESSIONS = 14
ATR_BASIS = "TRAILING_WILDER_ATR_14_OVER_COMPLETED_DAILY_SESSIONS"

# --- partitions -----------------------------------------------------------
DISCOVERY = "DISCOVERY"
VALIDATION = "VALIDATION"
UNTOUCHED_HOLDOUT = "UNTOUCHED_HOLDOUT"
PARTITIONS = (DISCOVERY, VALIDATION, UNTOUCHED_HOLDOUT)
PARTITION_SHARES = (0.60, 0.20, 0.20)

# Eligibility floors for an instrument to be studied at all. These describe the
# file, not the mechanism, so they stay out of the pre-registration payload and
# out of the fingerprint: importing a sixth CSV must not re-hash a definition
# that has already been measured and written to an immutable artefact.
MIN_BARS = 50_000
MIN_SESSIONS = 250
HISTORICAL_CANDLE_DATA = SOURCE
NOT_EXECUTABLE_BOOK = (
    "HISTORICAL_OHLC_IS_NOT_AN_EXECUTABLE_BOOK_IT_CARRIES_NO_BID_NO_ASK_AND_NO_DEPTH"
)

# --- sample floors and the promotion bar, declared before measurement -----
#
# These are much lower than Phase 51's hundred-trade floor, and deliberately:
# this mechanism fires at most once per session and only on a gap that fails, so
# a five-year file cannot produce hundreds of them. The floors are set to the
# smallest samples on which the corresponding claim is worth making at all, and
# a partition below its floor is reported unmeasured rather than graded.
MIN_TRADES_DISCOVERY = 40
MIN_TRADES_VALIDATION = 15
MIN_TRADES_HOLDOUT = 15

# §17's bar.
MIN_PROFIT_FACTOR = 1.2
MAX_DRAWDOWN_R = 12.0
# Stricter than §17's "majority": a third of the net from one trade is already
# a rule that worked once.
MAX_TOP_TRADE_SHARE = 0.35
MIN_YEARS_POSITIVE = 3
# §15's recent-period test: no single year may carry more than this share.
MAX_YEAR_SHARE = 0.60
COST_STRESS_MULTIPLES = (1.0, 1.5, 2.0)
FDR_ALPHA = 0.05

# --- statuses -------------------------------------------------------------
ROBUST_CANDIDATE = "ROBUST_CANDIDATE"
PROMISING_NEEDS_DATA = "PROMISING_NEEDS_DATA"
HISTORICAL_LEAD = "HISTORICAL_LEAD"
OVERFIT_RISK = "OVERFIT_RISK"
COST_BLOCKED = "COST_BLOCKED"
REJECTED = "REJECTED"
FINAL_STATUSES = (
    ROBUST_CANDIDATE, PROMISING_NEEDS_DATA, HISTORICAL_LEAD,
    OVERFIT_RISK, COST_BLOCKED, REJECTED,
)

# §15's four readings of the effect.
DIRECTIONAL_EDGE = "DIRECTIONAL_EDGE"
COST_DRIVEN_APPEARANCE = "COST_DRIVEN_APPEARANCE"
RECENT_PERIOD_OVERFIT = "RECENT_PERIOD_OVERFIT"
NO_EDGE = "NO_EDGE"
EFFECT_READINGS = (
    DIRECTIONAL_EDGE, COST_DRIVEN_APPEARANCE, RECENT_PERIOD_OVERFIT, NO_EDGE,
)

# --- refusal reasons, counted rather than hidden --------------------------
NO_GAP = "NO_GAP"
NO_EXTENSION = "NO_EXTENSION_BEYOND_THE_OPENING_AREA"
NO_FAILURE_CLOSE = "NO_CLOSE_BACK_INSIDE_THE_PREVIOUS_DAY_RANGE"
STOP_TOO_WIDE = "STOP_WIDER_THAN_THE_DECLARED_CAP"
TARGET_WRONG_SIDE = "PREVIOUS_DAY_CLOSE_IS_NOT_BEYOND_THE_ENTRY"
NO_FORWARD_WINDOW = "NO_FORWARD_BARS_LEFT_IN_THE_SESSION"
NOT_KNOWABLE = "PREVIOUS_SESSION_OR_ATR_NOT_YET_KNOWABLE"
REFUSALS = (
    NO_GAP, NO_EXTENSION, NO_FAILURE_CLOSE, STOP_TOO_WIDE, TARGET_WRONG_SIDE,
    NO_FORWARD_WINDOW, NOT_KNOWABLE, COST_BLOCKED,
)

# --- honesty strings that travel with the numbers ------------------------
CANDLE_HAS_NO_BOOK = (
    "A_CANDLE_HAS_NO_BID_NO_ASK_AND_NO_DEPTH_SO_EVERY_FILL_HERE_IS_MODELLED_"
    "FROM_THE_NEXT_BARS_OPEN_PLUS_CONFIGURED_SLIPPAGE_AND_EVERY_NET_FIGURE_IS_"
    "OPTIMISTIC_BY_AT_LEAST_ONE_UNMEASURED_SPREAD"
)
SAME_BAR_TIE_IS_A_LOSS = (
    "WHEN_ONE_BAR_CONTAINS_BOTH_THE_STOP_AND_THE_TARGET_THE_STOP_IS_TAKEN_"
    "BECAUSE_A_FIVE_MINUTE_CANDLE_CANNOT_SAY_WHICH_CAME_FIRST_AND_THE_"
    "OPTIMISTIC_READING_IS_WHAT_TURNS_A_LOSING_RULE_INTO_A_WINNING_BACKTEST"
)
COST_GATE_IS_NOT_A_PROBABILITY = (
    "THE_MOVEMENT_COST_MULTIPLE_IS_A_DISTANCE_DIVIDED_BY_A_MODELLED_ROUND_TRIP_"
    "IT_IS_AN_ECONOMIC_FEASIBILITY_FILTER_AND_IT_IS_NOT_A_PROBABILITY_A_"
    "CONFIDENCE_OR_A_SCORE"
)
FAMILIES_NOT_DISCOVERIES = (
    "NESTED_THRESHOLDS_SELECT_NESTED_EVENT_SETS_SO_THE_THIRTY_SIX_"
    "PARAMETERIZATIONS_ARE_COLLAPSED_BY_HASHING_EACH_VARIANTS_ACTUAL_ENTRY_SET_"
    "AND_TEN_SPELLINGS_OF_ONE_EVENT_SET_ARE_ONE_FAMILY_NOT_TEN_DISCOVERIES"
)
NO_OPTION_CLAIM = (
    "NO_HISTORICAL_OPTION_BID_ASK_EXISTS_IN_THIS_FILE_SO_NO_OPTION_RESULT_IS_"
    "PRODUCED_THE_VEHICLE_QUESTION_IS_DEFERRED_TO_THE_LIVE_EXECUTABLE_BOOK_AND_"
    "ONLY_IF_THE_FUTURES_MECHANISM_SURVIVES"
)
NOT_A_PREDICTION = (
    "EVERY_ROW_HERE_IS_A_HISTORICAL_MEASUREMENT_ON_MODELLED_FILLS_IT_IS_NOT_A_"
    "PREDICTION_NOT_A_RECOMMENDATION_NOT_A_SIGNAL_AND_NOT_A_CLAIM_OF_REAL_"
    "MONEY_SUITABILITY"
)
NO_ORDER_PATH = (
    "THIS_MODULE_READS_HISTORICAL_FILES_AND_WRITES_RESEARCH_ARTEFACTS_IT_HAS_NO_"
    "TICK_HOOK_NO_API_ROUTE_NO_BROKER_IMPORT_AND_NO_ORDER_PATH"
)


def preregistration() -> dict:
    """Every declared value, as the artefact header and the fingerprint input."""
    return {
        "version": VERSION,
        "mechanism": MECHANISM,
        "source": SOURCE,
        "vehicle": VEHICLE,
        "decision_timeframe_minutes": DECISION_TIMEFRAME_MINUTES,
        "wait_minutes": WAIT_MINUTES,
        "gap_atr_thresholds": list(GAP_ATR_THRESHOLDS),
        "confirmations": list(CONFIRMATIONS),
        "stop_buffer_atr": STOP_BUFFER_ATR,
        "max_stop_atr": list(MAX_STOP_ATR),
        "cost_gate_multiples": list(COST_GATE_MULTIPLES),
        "max_hold_minutes": MAX_HOLD_MINUTES,
        "max_trades_per_session": MAX_TRADES_PER_SESSION,
        "target": "PREVIOUS_DAY_CLOSE",
        "atr_basis": ATR_BASIS,
        "atr_window_sessions": ATR_WINDOW_SESSIONS,
        "partitions": list(PARTITIONS),
        "partition_shares": list(PARTITION_SHARES),
        "min_trades": {
            DISCOVERY: MIN_TRADES_DISCOVERY,
            VALIDATION: MIN_TRADES_VALIDATION,
            UNTOUCHED_HOLDOUT: MIN_TRADES_HOLDOUT,
        },
        "min_profit_factor": MIN_PROFIT_FACTOR,
        "max_drawdown_r": MAX_DRAWDOWN_R,
        "max_top_trade_share": MAX_TOP_TRADE_SHARE,
        "min_years_positive": MIN_YEARS_POSITIVE,
        "max_year_share": MAX_YEAR_SHARE,
        "cost_stress_multiples": list(COST_STRESS_MULTIPLES),
        "fdr_alpha": FDR_ALPHA,
        "final_statuses": list(FINAL_STATUSES),
        "effect_readings": list(EFFECT_READINGS),
        "refusals": list(REFUSALS),
        "honesty": {
            "candle_has_no_book": CANDLE_HAS_NO_BOOK,
            "same_bar_tie": SAME_BAR_TIE_IS_A_LOSS,
            "cost_gate": COST_GATE_IS_NOT_A_PROBABILITY,
            "families": FAMILIES_NOT_DISCOVERIES,
            "options": NO_OPTION_CLAIM,
            "not_a_prediction": NOT_A_PREDICTION,
            "isolation": NO_ORDER_PATH,
        },
    }


def fingerprint() -> str:
    """Stable hash of the declared mechanism. Changes if any parameter changes."""
    payload = json.dumps(preregistration(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def variants() -> list[dict]:
    """§13's bounded grid: the complete cross product, in a fixed order.

    Enumerated here rather than in the search so the count is a property of the
    pre-registration. Nothing is added to this grid later, and nothing in it is
    skipped because it looked unpromising.
    """
    out: list[dict] = []
    for gap in GAP_ATR_THRESHOLDS:
        for confirm in CONFIRMATIONS:
            for stop_cap in MAX_STOP_ATR:
                for gate in COST_GATE_MULTIPLES:
                    out.append({
                        "variant_id": (
                            f"GAP{gap:.2f}_{confirm}_STOP{stop_cap:.2f}"
                            f"_GATE{gate:.1f}"
                        ),
                        "gap_atr": float(gap),
                        "confirmation": confirm,
                        "max_stop_atr": float(stop_cap),
                        "cost_gate": float(gate),
                    })
    return out
