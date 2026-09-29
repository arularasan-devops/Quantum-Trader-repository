"""Phase 53 — OPENING_RANGE_BREAKOUT_RETEST, frozen before measurement.

One mechanism, one registered grid of sixty-four parameterizations, graded once.
Phase 53 is a **different shape of question** from Phase 52 and the difference is
worth stating, because it decides what the result can mean:

* Phase 52 tested a rare event (a gap that fails) and died of **frequency** —
  about eight qualifying sessions a year per instrument, against a declared
  discovery floor of forty trades;
* Phase 53 tests an event that **exists in every session**. An opening range is
  always there, so a null result here cannot be blamed on rarity and the sample
  floors can be set where they belong for a higher-frequency rule.

What this module does not do, and will not be argued into doing later:

* it will not reinterpret, loosen or re-run Phase 52. That phase's answer is
  ``ROBUST_CANDIDATES = 0`` at fingerprint ``d3f7ccfd208d5e9e`` and it stays
  that way;
* it will not import any earlier research phase's engine. The shared *cost model*
  is deliberately reused through Phase 24's public helper, because a research
  layer that invents a cheaper cost model produces numbers nobody can reconcile;
  everything else is local, so no earlier phase's isolation invariant is broken
  by this one existing;
* it will not add a parameter after seeing a result. The sixty-four combinations
  below are the complete registered grid and the fingerprint is over exactly
  them;
* it will not rank by discovery P&L, and it will not call the best row a winner;
* it will not read a future bar to decide an entry. The opening range needs the
  first thirty minutes closed, the breakout needs a *completed* bar, the retest
  needs a bar after the breakout, the confirmation needs a bar after the retest,
  and the fill is the bar after that;
* it will not call any of this a prediction, a recommendation or a signal.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "phase53.v1"
MECHANISM = "OPENING_RANGE_BREAKOUT_RETEST"

SOURCE = "HISTORICAL_CANDLE_DATA"
VEHICLE = "FUTURES_ONLY"

# --- the mechanism, frozen ------------------------------------------------
#
# Every entry is filled on a completed five-minute bar's open, whatever
# timeframe confirmed the breakout or the retest. The coarser bars decide
# *whether* there is an event; the five-minute series decides *where* it fills
# and how it resolves, so no result depends on a fill inside a bar.
DECISION_TIMEFRAME_MINUTES = 5

# The opening range: the first thirty minutes of the session. No bar closing
# inside it may break out of it, and the range is published only to the bars
# that follow it.
OPENING_RANGE_MINUTES = 30

# The breakout must be confirmed by the close of a completed 15- or 30-minute
# bucket. A 5-minute breakout close is deliberately **not** offered: it is the
# same event at a noisier resolution and it is not in the registered grid.
BREAKOUT_15M = "BREAKOUT_CLOSE_15M"
BREAKOUT_30M = "BREAKOUT_CLOSE_30M"
BREAKOUT_CONFIRMATIONS = (BREAKOUT_15M, BREAKOUT_30M)

# The retest window: price must come back to the level within this many minutes
# of the breakout bar's close, or the setup is abandoned for that direction.
RETEST_WINDOW_MINUTES = 60

# How close to the level counts as a retest, in ATR. Zero means the level must
# actually trade.
RETEST_TOLERANCES = (0.00, 0.10)

# How long after the retest touch the confirming close may arrive. The brief
# bounds the *retest* to sixty minutes but leaves the confirmation unbounded;
# unbounded, a "retest confirmation" four hours later is a different trade
# wearing this one's name, so the same sixty minutes is applied and declared
# here as an interpretation rather than discovered later as a tuning knob.
CONFIRMATION_WINDOW_MINUTES = 60

# Which completed close may confirm the retest held.
RETEST_5M = "RETEST_CLOSE_5M"
RETEST_15M = "RETEST_CLOSE_15M"
RETEST_CONFIRMATIONS = (RETEST_5M, RETEST_15M)

# The stop sits beyond the retest's own extreme by this much ATR. "The retest
# low" is read as the lowest low the market actually made while it came back to
# the level and was confirmed — the touching bar through the confirming bar
# inclusive, every one of them complete before the fill. Read as the touching
# bar alone, a stop could sit inside a low the market had already printed two
# bars later and the trade would be stopped by its own history. Declared here
# rather than chosen later.
RETEST_EXTREME_BASIS = (
    "THE_EXTREME_MADE_FROM_THE_TOUCHING_BAR_THROUGH_THE_CONFIRMING_BAR_INCLUSIVE"
)
STOP_BUFFER_ATR = (0.25, 0.50)
# A stop wider than this is refused outright, whatever the buffer produced.
MAX_STOP_ATR = 1.00

# The target is a multiple of the measured risk, not a price level. This is the
# other structural difference from Phase 52, where the target was the previous
# day's close: here a wider stop moves the target with it, so stop buffer and
# target multiple do not select the same event twice.
TARGET_R_MULTIPLES = (1.5, 2.0)

# The economic feasibility gate, in round trips of the modelled cost.
COST_GATE_MULTIPLES = (3.0, 4.0)

# The clock, in minutes from the fill.
MAX_HOLD_MINUTES = 180

# One accepted long and one accepted short per instrument per session, and no
# re-entry in the same direction after a stop. A consequence worth stating:
# trade count and independent session count are **not** identical here, unlike
# Phase 52 — a session may contribute one long and one short — so the session
# count is reported separately everywhere and never inferred from the trades.
MAX_LONG_PER_SESSION = 1
MAX_SHORT_PER_SESSION = 1

# The ATR is a trailing Wilder ATR over **completed daily sessions**. The
# session being traded contributes nothing to its own ATR, so no threshold here
# is scaled by the move it is meant to measure.
ATR_WINDOW_SESSIONS = 14
ATR_BASIS = "TRAILING_WILDER_ATR_14_OVER_COMPLETED_DAILY_SESSIONS"

# --- partitions -----------------------------------------------------------
DISCOVERY = "DISCOVERY"
VALIDATION = "VALIDATION"
UNTOUCHED_HOLDOUT = "UNTOUCHED_HOLDOUT"
PARTITIONS = (DISCOVERY, VALIDATION, UNTOUCHED_HOLDOUT)
PARTITION_SHARES = (0.60, 0.20, 0.20)

# Eligibility floors for an instrument to be studied at all. These describe the
# file rather than the mechanism, so they are kept out of the pre-registration
# payload and out of the fingerprint: importing another CSV must not re-hash a
# definition that has already been measured into an immutable artefact.
MIN_BARS = 50_000
MIN_SESSIONS = 250

# --- sample floors and the promotion bar, declared before measurement -----
#
# Higher than Phase 52's, and deliberately: this mechanism can fire twice a
# session, so a five-year file that produces only a handful of events is
# telling us the *gates* are binding, not that the setup is rare. A partition
# below its floor is reported unmeasured rather than graded.
MIN_TRADES_DISCOVERY = 100
MIN_TRADES_VALIDATION = 30
MIN_TRADES_HOLDOUT = 30
MIN_SESSIONS_DISCOVERY = 60

MIN_PROFIT_FACTOR = 1.2
MAX_DRAWDOWN_R = 15.0
# Stricter than "the majority": a third of the net from one trade is already a
# rule that worked once.
MAX_TOP_TRADE_SHARE = 0.35
MIN_YEARS_POSITIVE = 3
MAX_YEAR_SHARE = 0.60
MAX_QUARTER_SHARE = 0.40
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

# The five readings of the effect this phase is required to distinguish.
DIRECTIONAL_EDGE = "DIRECTIONAL_EDGE"
COST_DRIVEN_APPEARANCE = "COST_DRIVEN_APPEARANCE"
RECENT_PERIOD_OVERFIT = "RECENT_PERIOD_OVERFIT"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
NO_EDGE = "NO_EDGE"
EFFECT_READINGS = (
    DIRECTIONAL_EDGE, COST_DRIVEN_APPEARANCE, RECENT_PERIOD_OVERFIT,
    INSUFFICIENT_SAMPLE, NO_EDGE,
)

# --- refusal reasons, counted rather than hidden --------------------------
NOT_KNOWABLE = "PREVIOUS_SESSION_OR_ATR_NOT_YET_KNOWABLE"
NO_OPENING_RANGE = "SESSION_TOO_SHORT_TO_CLOSE_A_THIRTY_MINUTE_OPENING_RANGE"
NO_BREAKOUT = "NO_COMPLETED_BAR_CLOSED_OUTSIDE_THE_OPENING_RANGE"
NO_RETEST = "PRICE_NEVER_RETURNED_TO_THE_LEVEL_WITHIN_THE_RETEST_WINDOW"
NO_RETEST_CONFIRMATION = "NO_COMPLETED_BAR_CLOSED_BACK_BEYOND_THE_LEVEL_AFTER_THE_RETEST"
NO_FORWARD_WINDOW = "NO_FORWARD_BARS_LEFT_IN_THE_SESSION"
STOP_TOO_WIDE = "STOP_WIDER_THAN_THE_DECLARED_ONE_ATR_CAP"
STOP_NOT_POSITIVE = "THE_FILL_OPENED_PAST_ITS_OWN_STOP"
REFUSALS = (
    NOT_KNOWABLE, NO_OPENING_RANGE, NO_BREAKOUT, NO_RETEST,
    NO_RETEST_CONFIRMATION, NO_FORWARD_WINDOW, STOP_TOO_WIDE,
    STOP_NOT_POSITIVE, COST_BLOCKED,
)

LONG_BREAKOUT = "OPENING_RANGE_BREAKOUT_RETEST_LONG"
SHORT_BREAKOUT = "OPENING_RANGE_BREAKOUT_RETEST_SHORT"
DIRECTIONS = (LONG_BREAKOUT, SHORT_BREAKOUT)

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
RETEST_TOUCH_IS_NOT_AN_ORDERED_PATH = (
    "A_RETEST_IS_DETECTED_FROM_A_BARS_RANGE_TOUCHING_THE_LEVEL_AND_A_BAR_CANNOT_"
    "SAY_WHEN_INSIDE_ITSELF_THAT_TOUCH_HAPPENED_SO_THE_CONFIRMING_CLOSE_IS_"
    "REQUIRED_ON_A_LATER_COMPLETED_BAR_AND_NEVER_ON_THE_TOUCHING_BAR_ITSELF"
)
COST_GATE_IS_NOT_A_PROBABILITY = (
    "THE_MOVEMENT_COST_MULTIPLE_IS_A_DISTANCE_DIVIDED_BY_A_MODELLED_ROUND_TRIP_"
    "IT_IS_AN_ECONOMIC_FEASIBILITY_FILTER_AND_IT_IS_NOT_A_PROBABILITY_A_"
    "CONFIDENCE_OR_A_SCORE"
)
FAMILIES_NOT_DISCOVERIES = (
    "PARAMETERIZATIONS_THAT_SELECT_THE_SAME_ENTRY_INSTANTS_ARE_ONE_HYPOTHESIS_"
    "WEARING_SEVERAL_NAMES_SO_THE_SIXTY_FOUR_ARE_COLLAPSED_BY_HASHING_EACH_"
    "VARIANTS_ACTUAL_ENTRY_SET_AND_THE_CORRECTION_STILL_RUNS_OVER_THE_WHOLE_"
    "DENOMINATOR_NOT_OVER_THE_SURVIVORS"
)
NO_OPTION_CLAIM = (
    "NO_HISTORICAL_OPTION_BID_ASK_EXISTS_IN_THIS_FILE_SO_NO_OPTION_RESULT_IS_"
    "PRODUCED_PHASE_53_DISCOVERY_IS_FUTURES_ONLY_AND_THE_VEHICLE_QUESTION_IS_"
    "DEFERRED_TO_THE_LIVE_EXECUTABLE_BOOK"
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
PHASE_52_UNTOUCHED = (
    "PHASE_52_IS_NOT_REINTERPRETED_NOT_LOOSENED_AND_NOT_RERUN_BY_THIS_PHASE_ITS_"
    "ANSWER_REMAINS_ZERO_ROBUST_CANDIDATES_AT_ITS_OWN_FINGERPRINT"
)


def preregistration() -> dict:
    """Every declared value, as the artefact header and the fingerprint input."""
    return {
        "version": VERSION,
        "mechanism": MECHANISM,
        "source": SOURCE,
        "vehicle": VEHICLE,
        "decision_timeframe_minutes": DECISION_TIMEFRAME_MINUTES,
        "opening_range_minutes": OPENING_RANGE_MINUTES,
        "breakout_confirmations": list(BREAKOUT_CONFIRMATIONS),
        "retest_window_minutes": RETEST_WINDOW_MINUTES,
        "confirmation_window_minutes": CONFIRMATION_WINDOW_MINUTES,
        "retest_tolerances_atr": list(RETEST_TOLERANCES),
        "retest_confirmations": list(RETEST_CONFIRMATIONS),
        "retest_extreme_basis": RETEST_EXTREME_BASIS,
        "stop_buffer_atr": list(STOP_BUFFER_ATR),
        "max_stop_atr": MAX_STOP_ATR,
        "target_r_multiples": list(TARGET_R_MULTIPLES),
        "cost_gate_multiples": list(COST_GATE_MULTIPLES),
        "max_hold_minutes": MAX_HOLD_MINUTES,
        "max_long_per_session": MAX_LONG_PER_SESSION,
        "max_short_per_session": MAX_SHORT_PER_SESSION,
        "atr_basis": ATR_BASIS,
        "atr_window_sessions": ATR_WINDOW_SESSIONS,
        "partitions": list(PARTITIONS),
        "partition_shares": list(PARTITION_SHARES),
        "min_trades": {
            DISCOVERY: MIN_TRADES_DISCOVERY,
            VALIDATION: MIN_TRADES_VALIDATION,
            UNTOUCHED_HOLDOUT: MIN_TRADES_HOLDOUT,
        },
        "min_sessions_discovery": MIN_SESSIONS_DISCOVERY,
        "min_profit_factor": MIN_PROFIT_FACTOR,
        "max_drawdown_r": MAX_DRAWDOWN_R,
        "max_top_trade_share": MAX_TOP_TRADE_SHARE,
        "min_years_positive": MIN_YEARS_POSITIVE,
        "max_year_share": MAX_YEAR_SHARE,
        "max_quarter_share": MAX_QUARTER_SHARE,
        "cost_stress_multiples": list(COST_STRESS_MULTIPLES),
        "fdr_alpha": FDR_ALPHA,
        "final_statuses": list(FINAL_STATUSES),
        "effect_readings": list(EFFECT_READINGS),
        "refusals": list(REFUSALS),
        "directions": list(DIRECTIONS),
        "honesty": {
            "candle_has_no_book": CANDLE_HAS_NO_BOOK,
            "same_bar_tie": SAME_BAR_TIE_IS_A_LOSS,
            "retest_touch": RETEST_TOUCH_IS_NOT_AN_ORDERED_PATH,
            "cost_gate": COST_GATE_IS_NOT_A_PROBABILITY,
            "families": FAMILIES_NOT_DISCOVERIES,
            "options": NO_OPTION_CLAIM,
            "not_a_prediction": NOT_A_PREDICTION,
            "isolation": NO_ORDER_PATH,
            "phase52": PHASE_52_UNTOUCHED,
        },
    }


def fingerprint() -> str:
    """Stable hash of the declared mechanism. Changes if any parameter changes."""
    payload = json.dumps(preregistration(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def variants() -> list[dict]:
    """The complete registered grid: 2x2x2x2x2x2 = 64, in a fixed order.

    Enumerated here rather than inside the search so the count is a property of
    the pre-registration and not of the code path that happened to run. Nothing
    is added to this grid after a result is seen, and nothing in it is skipped
    because it looked unpromising.
    """
    out: list[dict] = []
    for breakout in BREAKOUT_CONFIRMATIONS:
        for retest in RETEST_CONFIRMATIONS:
            for tol in RETEST_TOLERANCES:
                for buf in STOP_BUFFER_ATR:
                    for target in TARGET_R_MULTIPLES:
                        for gate in COST_GATE_MULTIPLES:
                            out.append({
                                "variant_id": (
                                    f"BO{breakout[-3:]}_RT{retest[-3:]}"
                                    f"_TOL{tol:.2f}_BUF{buf:.2f}"
                                    f"_TGT{target:.1f}R_GATE{gate:.1f}"
                                ),
                                "breakout_confirmation": breakout,
                                "retest_confirmation": retest,
                                "retest_tolerance_atr": float(tol),
                                "stop_buffer_atr": float(buf),
                                "target_r": float(target),
                                "cost_gate": float(gate),
                            })
    return out


TOTAL_PARAMETERIZATIONS = len(variants())
