"""Phase 54 — MULTI_DAY_EXPANSION_PULLBACK_CONTINUATION, frozen before measurement.

One mechanism, one registered grid of one hundred and twenty-eight
parameterizations per instrument, graded once.

Why this phase exists, stated in the terms the previous three results earned:

* Phase 51 searched 5,264 intraday hypotheses and found nothing robust; the
  CRUDEOIL leads were one event set spelled nineteen ways;
* Phase 52 tested one rare intraday mechanism and died of **frequency** — about
  eight qualifying sessions a year;
* Phase 53 tested one *frequent* intraday mechanism, graded it on 1,791 and 871
  trades, and found a real but tiny gross edge — a median +0.006R on CRUDEOIL
  against a cost that is 1.8x that edge. Its binding constraints were the
  180-minute clock and the round trip, not the sample.

Phase 54 therefore changes the **thing being varied**: the holding period and the
size of the move, not the entry's cleverness. A 2R target off a daily-ATR stop is
hundreds of points rather than tens, so the same fixed round trip should stop
being decisive. That is a hypothesis about *scale*, and it is the one variable
the first four phases never moved.

What this module will not do:

* it will not reinterpret, loosen or re-run Phases 41-53. Each keeps its own
  answer at its own fingerprint;
* it will not import an earlier phase's research engine. The shared **cost
  model** is deliberately reused through Phase 24's public helper, because a
  research layer that invents a cheaper cost produces numbers nobody can
  reconcile; the daily series is built through Phase 27's public resampler for
  the same reason. Everything else is local;
* it will not add a parameter after seeing a result. The 128 combinations below
  are the complete registered grid and the fingerprint is over exactly them;
* it will not rank by discovery P&L, and it will not call the best row a winner;
* it will not read a future session to decide an entry. ``HIGH20``/``LOW20`` and
  the ATR exclude the session being judged, the expansion needs a completed
  close, the pullback and the reclaim need completed closes, and the fill is the
  **next session's open**;
* it will not call any of this a prediction, a recommendation or a signal.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "phase54.v1"
MECHANISM = "MULTI_DAY_EXPANSION_PULLBACK_CONTINUATION"

SOURCE = "HISTORICAL_CANDLE_DATA"
VEHICLE = "FUTURES_ONLY"

# --- the mechanism, frozen ------------------------------------------------
#
# Decisions are taken on **completed daily bars** aggregated from the stored
# one-minute series. Resolution is walked on completed five-minute bars, so a
# stop and a target inside one session are ordered as finely as the data allows
# rather than being settled by a daily candle that cannot say which came first.
DECISION_TIMEFRAME = "COMPLETED_DAILY_BARS_AGGREGATED_FROM_THE_ONE_MINUTE_SERIES"
RESOLUTION_TIMEFRAME_MINUTES = 5

# The breakout reference window, in completed prior sessions. The session being
# judged is **not** in its own window.
LOOKBACKS = (20, 30)

# How many completed sessions after the expansion the pullback and the reclaim
# have to happen in.
PULLBACK_WINDOWS = (2, 3)

# The ATR that scales every distance: Wilder over twenty completed daily true
# ranges, ending at the session before the one being judged. Held at twenty for
# both lookbacks on purpose — it is a volatility scale, not a second breakout
# window, and letting it track the lookback would confound "a wider reference
# window" with "a slower volatility estimate" in the same grid cell.
ATR_WINDOW_SESSIONS = 20
ATR_BASIS = "TRAILING_WILDER_ATR_20_OVER_COMPLETED_DAILY_SESSIONS_EXCLUDING_TODAY"

# --- the two interpretations this brief left open, declared here ----------
#
# 1. What counts as a pullback. "Price must retrace toward HIGH20" is not
#    measurable as written, so the measurable reading is used: some session in
#    the window must actually **trade back to the level** (its low reaches
#    HIGH20 for a long, its high reaches LOW20 for a short). A session that
#    merely stalls above the level is not a pullback here.
PULLBACK_BASIS = (
    "A_PULLBACK_REQUIRES_A_COMPLETED_SESSION_WHOSE_RANGE_ACTUALLY_REACHES_THE_"
    "LEVEL_A_SESSION_THAT_ONLY_DRIFTS_TOWARD_IT_IS_NOT_COUNTED"
)

# 2. Whether the touching session may itself be the reclaim. It may. A session
#    that trades down to HIGH20 and still closes above it is a pullback and a
#    reclaim in one bar, and the fill is the **next** session's open either way,
#    so nothing about the entry is decided from information that bar did not
#    carry. The stricter reading — demanding a later session close back beyond
#    the level — is almost unsatisfiable inside a two-session window and would
#    convert this test into a sample-size artefact. It is therefore measured as
#    a **diagnostic** alongside the frozen result rather than smuggled in as a
#    parameter, so a reader can see exactly what the choice cost.
RECLAIM_BASIS = (
    "THE_SESSION_THAT_TRADES_BACK_TO_THE_LEVEL_MAY_ITSELF_BE_THE_RECLAIM_IF_IT_"
    "CLOSES_BEYOND_THE_LEVEL_AND_THE_FILL_IS_STILL_THE_NEXT_SESSIONS_OPEN"
)
STRICT_RECLAIM_IS_A_DIAGNOSTIC = (
    "THE_STRICTER_READING_REQUIRING_A_SEPARATE_LATER_RECLAIM_SESSION_IS_COUNTED_"
    "AS_A_DIAGNOSTIC_AND_NEVER_GRADED_SO_THE_REGISTERED_GRID_STAYS_THE_"
    "REGISTERED_GRID"
)

# 3. Which low the stop sits under. Under the frozen reading the touching
#    session *is* the reclaiming session — a session that trades back to the
#    level and does not close inside it has closed beyond it — so the pullback
#    extreme is that one session's extreme, complete before the fill. There is
#    no window of sessions to take an extreme over, and no later session's low
#    can be borrowed into a stop that was set before it existed.
PULLBACK_EXTREME_BASIS = (
    "THE_EXTREME_OF_THE_SINGLE_COMPLETED_SESSION_THAT_TRADED_BACK_TO_THE_LEVEL_"
    "AND_STILL_CLOSED_BEYOND_IT_WHICH_UNDER_THE_FROZEN_READING_IS_BOTH_THE_"
    "TOUCH_AND_THE_RECLAIM"
)

STOP_BUFFER_ATR = (0.25, 0.50)
# A stop wider than this share of ATR20 is refused outright.
MAX_RISK_ATR = (1.0, 1.5)

# The target is a multiple of the measured risk, not a price level.
TARGET_R_MULTIPLES = (1.5, 2.0)

# The clock, in **completed sessions counting the entry session itself**. An
# unresolved position leaves at the open of the session after the last allowed
# one; there is no intraday time cap, which is the point of the phase.
MAX_HOLD_SESSIONS = (5, 10)
HOLD_BASIS = (
    "THE_MAXIMUM_HOLD_COUNTS_THE_ENTRY_SESSION_ITSELF_AND_AN_UNRESOLVED_"
    "POSITION_EXITS_AT_THE_OPEN_OF_THE_SESSION_AFTER_THE_LAST_ALLOWED_ONE"
)

# The economic feasibility gate, in round trips of the modelled cost.
COST_GATE_MULTIPLES = (5.0, 8.0)

# One position per instrument, no pyramiding, and no re-entry until a
# **completely new** expansion event — one whose expansion session is strictly
# after the session the previous position left on. Without that last clause the
# rule would re-enter on an expansion it had already seen while it was in the
# trade, which is the same event charged twice.
MAX_OPEN_POSITIONS = 1
REENTRY_BASIS = (
    "AFTER_AN_EXIT_THE_NEXT_TRADE_REQUIRES_AN_EXPANSION_SESSION_STRICTLY_AFTER_"
    "THE_SESSION_THE_POSITION_LEFT_ON_SO_NO_EXPANSION_IS_TRADED_TWICE"
)

# --- partitions -----------------------------------------------------------
DISCOVERY = "DISCOVERY"
VALIDATION = "VALIDATION"
UNTOUCHED_HOLDOUT = "UNTOUCHED_HOLDOUT"
PARTITIONS = (DISCOVERY, VALIDATION, UNTOUCHED_HOLDOUT)
PARTITION_SHARES = (0.60, 0.20, 0.20)
PARTITION_BASIS = (
    "A_TRADE_BELONGS_TO_THE_PARTITION_OF_ITS_ENTRY_SESSION_AND_A_MULTI_DAY_"
    "POSITION_OPENED_AT_THE_END_OF_ONE_PARTITION_MAY_RESOLVE_A_FEW_SESSIONS_"
    "INSIDE_THE_NEXT_WHICH_IS_DISCLOSED_RATHER_THAN_HIDDEN"
)

# Eligibility floors for the file, not the mechanism: kept out of the
# fingerprint so importing another CSV cannot re-hash a measured definition.
MIN_BARS = 50_000
MIN_SESSIONS = 250

# --- sample floors and the promotion bar, declared before measurement -----
#
# Lower than Phase 53's and unapologetically so: this mechanism can fire at most
# a handful of times a year by construction, and a floor of a hundred discovery
# trades would guarantee `INSUFFICIENT_SAMPLE` on every row and measure nothing.
# The protection against grading noise is placed where it belongs instead — a
# hard total-trade floor below which **no** row may be promoted however good it
# looks, plus false-discovery correction over all 256 registered cells.
MIN_TRADES_DISCOVERY = 25
MIN_TRADES_VALIDATION = 8
MIN_TRADES_HOLDOUT = 8
MIN_SESSIONS_DISCOVERY = 25
MIN_TRADES_FOR_PROMOTION = 40

MIN_PROFIT_FACTOR = 1.2
MAX_DRAWDOWN_R = 12.0
MAX_TOP_TRADE_SHARE = 0.35
MIN_YEARS_POSITIVE = 3
MAX_YEAR_SHARE = 0.60
MAX_QUARTER_SHARE = 0.40
COST_STRESS_MULTIPLES = (1.0, 1.5, 2.0)
FDR_ALPHA = 0.05
FDR_DENOMINATOR_BASIS = (
    "THE_CORRECTION_RUNS_OVER_EVERY_REGISTERED_CELL_OF_THE_GRID_ON_EVERY_"
    "INSTRUMENT_MEASURED_NEVER_OVER_THE_DISTINCT_EVENT_SETS_AND_NEVER_OVER_"
    "THE_SURVIVORS_BECAUSE_EVERY_DISCARDED_COMBINATION_WAS_ANOTHER_CHANCE_TO_"
    "FIND_THE_ONE_THAT_LOOKS_GOOD"
)

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

# The six readings this phase is required to distinguish. HOLDING_PERIOD_EFFECT
# is new here and it is the whole reason the phase exists: a net that improves
# materially from the five-session cap to the ten-session cap on the *same*
# entries is the holding period paying, not the entry condition.
DIRECTIONAL_EDGE = "DIRECTIONAL_EDGE"
COST_DRIVEN_APPEARANCE = "COST_DRIVEN_APPEARANCE"
HOLDING_PERIOD_EFFECT = "HOLDING_PERIOD_EFFECT"
RECENT_PERIOD_OVERFIT = "RECENT_PERIOD_OVERFIT"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
NO_EDGE = "NO_EDGE"
EFFECT_READINGS = (
    DIRECTIONAL_EDGE, COST_DRIVEN_APPEARANCE, HOLDING_PERIOD_EFFECT,
    RECENT_PERIOD_OVERFIT, INSUFFICIENT_SAMPLE, NO_EDGE,
)

# The three effects the brief demands be separated, and the interaction case.
EFFECT_DIRECTIONAL = "DIRECTIONAL_EFFECT"
EFFECT_COST = "COST_EFFECT"
EFFECT_TIME = "TIME_HOLDING_EFFECT"
EFFECT_INTERACTION = "INTERACTION_OF_MORE_THAN_ONE"
EFFECT_NONE = "NONE_OF_THE_THREE_IS_MEASURABLE"
EFFECT_SOURCES = (
    EFFECT_DIRECTIONAL, EFFECT_COST, EFFECT_TIME, EFFECT_INTERACTION,
    EFFECT_NONE,
)

# --- refusal reasons, counted rather than hidden --------------------------
NOT_KNOWABLE = "LOOKBACK_WINDOW_OR_ATR_NOT_YET_KNOWABLE"
NO_EXPANSION = "NO_COMPLETED_DAILY_CLOSE_BEYOND_THE_LOOKBACK_EXTREME"
NO_PULLBACK = "PRICE_NEVER_TRADED_BACK_TO_THE_LEVEL_INSIDE_THE_PULLBACK_WINDOW"
INVALIDATED = "A_COMPLETED_DAILY_CLOSE_BACK_INSIDE_THE_RANGE_INVALIDATED_THE_SETUP"
NO_RECLAIM = "NO_RECLAIM_INSIDE_THE_PULLBACK_WINDOW_SO_THE_SETUP_EXPIRED"
NO_FORWARD_SESSION = "NO_SESSION_LEFT_TO_FILL_THE_NEXT_OPEN_IN"
STOP_TOO_WIDE = "INITIAL_STOP_WIDER_THAN_THE_DECLARED_MAX_RISK_IN_ATR"
STOP_NOT_POSITIVE = "THE_FILL_OPENED_PAST_ITS_OWN_STOP"
POSITION_ALREADY_OPEN = "A_POSITION_WAS_ALREADY_OPEN_AND_THERE_IS_NO_PYRAMIDING"
REFUSALS = (
    NOT_KNOWABLE, NO_EXPANSION, NO_PULLBACK, INVALIDATED, NO_RECLAIM,
    NO_FORWARD_SESSION, STOP_TOO_WIDE, STOP_NOT_POSITIVE,
    POSITION_ALREADY_OPEN, COST_BLOCKED,
)

LONG_CONTINUATION = "MULTI_DAY_EXPANSION_RECLAIM_LONG"
SHORT_CONTINUATION = "MULTI_DAY_EXPANSION_RECLAIM_SHORT"
DIRECTIONS = (LONG_CONTINUATION, SHORT_CONTINUATION)

# --- honesty strings that travel with the numbers ------------------------
CANDLE_HAS_NO_BOOK = (
    "A_CANDLE_HAS_NO_BID_NO_ASK_AND_NO_DEPTH_SO_EVERY_FILL_HERE_IS_MODELLED_"
    "FROM_THE_NEXT_SESSIONS_OPEN_PLUS_CONFIGURED_SLIPPAGE_AND_EVERY_NET_FIGURE_"
    "IS_OPTIMISTIC_BY_AT_LEAST_ONE_UNMEASURED_SPREAD"
)
SAME_BAR_TIE_IS_A_LOSS = (
    "WHEN_ONE_FIVE_MINUTE_BAR_CONTAINS_BOTH_THE_STOP_AND_THE_TARGET_THE_STOP_IS_"
    "TAKEN_BECAUSE_A_CANDLE_CANNOT_SAY_WHICH_CAME_FIRST_AND_THE_OPTIMISTIC_"
    "READING_IS_WHAT_TURNS_A_LOSING_RULE_INTO_A_WINNING_BACKTEST"
)
OVERNIGHT_RISK_IS_ONLY_PARTLY_MODELLED = (
    "A_MULTI_DAY_POSITION_CARRIES_OVERNIGHT_GAP_RISK_A_LEVEL_THE_MARKET_HAD_"
    "ALREADY_GAPPED_THROUGH_IS_FILLED_AT_THAT_BARS_OPEN_RATHER_THAN_AT_THE_"
    "LEVEL_BUT_A_CANDLE_STILL_CANNOT_SHOW_WHAT_A_RESTING_ORDER_WOULD_HAVE_"
    "RECEIVED_SO_LOSSES_HERE_REMAIN_THE_OPTIMISTIC_READING"
)
COST_GATE_IS_NOT_A_PROBABILITY = (
    "THE_MOVEMENT_COST_MULTIPLE_IS_A_DISTANCE_DIVIDED_BY_A_MODELLED_ROUND_TRIP_"
    "IT_IS_AN_ECONOMIC_FEASIBILITY_FILTER_AND_IT_IS_NOT_A_PROBABILITY_A_"
    "PREDICTION_A_CONFIDENCE_OR_A_SCORE"
)
FAMILIES_NOT_DISCOVERIES = (
    "PARAMETERIZATIONS_THAT_ENTER_THE_SAME_INSTANTS_ARE_ONE_ENTRY_EVENT_SET_AND_"
    "THOSE_THAT_ALSO_SHARE_A_STOP_AND_A_TARGET_ARE_ONE_EVENT_FAMILY_SO_THE_"
    "HUNDRED_AND_TWENTY_EIGHT_ARE_COLLAPSED_BY_HASHING_AND_THE_CORRECTION_STILL_"
    "RUNS_OVER_THE_WHOLE_DENOMINATOR_NOT_OVER_THE_SURVIVORS"
)
NO_OPTION_CLAIM = (
    "NO_HISTORICAL_OPTION_BID_ASK_EXISTS_IN_THIS_FILE_SO_NO_OPTION_RESULT_IS_"
    "PRODUCED_PHASE_54_DISCOVERY_IS_FUTURES_ONLY_AND_THE_VEHICLE_QUESTION_IS_"
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
EARLIER_PHASES_UNTOUCHED = (
    "PHASES_41_TO_53_ARE_NOT_REINTERPRETED_NOT_LOOSENED_AND_NOT_RERUN_BY_THIS_"
    "PHASE_EACH_KEEPS_ITS_OWN_ANSWER_AT_ITS_OWN_FINGERPRINT"
)


def preregistration() -> dict:
    """Every declared value, as the artefact header and the fingerprint input."""
    return {
        "version": VERSION,
        "mechanism": MECHANISM,
        "source": SOURCE,
        "vehicle": VEHICLE,
        "decision_timeframe": DECISION_TIMEFRAME,
        "resolution_timeframe_minutes": RESOLUTION_TIMEFRAME_MINUTES,
        "lookbacks": list(LOOKBACKS),
        "pullback_windows": list(PULLBACK_WINDOWS),
        "atr_basis": ATR_BASIS,
        "atr_window_sessions": ATR_WINDOW_SESSIONS,
        "pullback_basis": PULLBACK_BASIS,
        "reclaim_basis": RECLAIM_BASIS,
        "strict_reclaim_is_a_diagnostic": STRICT_RECLAIM_IS_A_DIAGNOSTIC,
        "pullback_extreme_basis": PULLBACK_EXTREME_BASIS,
        "stop_buffer_atr": list(STOP_BUFFER_ATR),
        "max_risk_atr": list(MAX_RISK_ATR),
        "target_r_multiples": list(TARGET_R_MULTIPLES),
        "max_hold_sessions": list(MAX_HOLD_SESSIONS),
        "hold_basis": HOLD_BASIS,
        "cost_gate_multiples": list(COST_GATE_MULTIPLES),
        "max_open_positions": MAX_OPEN_POSITIONS,
        "reentry_basis": REENTRY_BASIS,
        "partitions": list(PARTITIONS),
        "partition_shares": list(PARTITION_SHARES),
        "partition_basis": PARTITION_BASIS,
        "min_trades": {
            DISCOVERY: MIN_TRADES_DISCOVERY,
            VALIDATION: MIN_TRADES_VALIDATION,
            UNTOUCHED_HOLDOUT: MIN_TRADES_HOLDOUT,
        },
        "min_sessions_discovery": MIN_SESSIONS_DISCOVERY,
        "min_trades_for_promotion": MIN_TRADES_FOR_PROMOTION,
        "min_profit_factor": MIN_PROFIT_FACTOR,
        "max_drawdown_r": MAX_DRAWDOWN_R,
        "max_top_trade_share": MAX_TOP_TRADE_SHARE,
        "min_years_positive": MIN_YEARS_POSITIVE,
        "max_year_share": MAX_YEAR_SHARE,
        "max_quarter_share": MAX_QUARTER_SHARE,
        "cost_stress_multiples": list(COST_STRESS_MULTIPLES),
        "total_parameterizations_per_instrument": len(variants()),
        "fdr_alpha": FDR_ALPHA,
        "fdr_denominator_basis": FDR_DENOMINATOR_BASIS,
        "final_statuses": list(FINAL_STATUSES),
        "effect_readings": list(EFFECT_READINGS),
        "effect_sources": list(EFFECT_SOURCES),
        "refusals": list(REFUSALS),
        "directions": list(DIRECTIONS),
        "honesty": {
            "candle_has_no_book": CANDLE_HAS_NO_BOOK,
            "same_bar_tie": SAME_BAR_TIE_IS_A_LOSS,
            "overnight_risk": OVERNIGHT_RISK_IS_ONLY_PARTLY_MODELLED,
            "cost_gate": COST_GATE_IS_NOT_A_PROBABILITY,
            "families": FAMILIES_NOT_DISCOVERIES,
            "options": NO_OPTION_CLAIM,
            "not_a_prediction": NOT_A_PREDICTION,
            "isolation": NO_ORDER_PATH,
            "earlier_phases": EARLIER_PHASES_UNTOUCHED,
        },
    }


def fingerprint() -> str:
    """Stable hash of the declared mechanism. Changes if any parameter changes."""
    payload = json.dumps(preregistration(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def variants() -> list[dict]:
    """The complete registered grid: 2^7 = 128, in a fixed order.

    Enumerated here rather than inside the search so the count is a property of
    the pre-registration and not of the code path that happened to run. Nothing
    is added after a result is seen, and nothing is skipped for looking
    unpromising.
    """
    out: list[dict] = []
    for lookback in LOOKBACKS:
        for window in PULLBACK_WINDOWS:
            for buf in STOP_BUFFER_ATR:
                for max_risk in MAX_RISK_ATR:
                    for target in TARGET_R_MULTIPLES:
                        for hold in MAX_HOLD_SESSIONS:
                            for gate in COST_GATE_MULTIPLES:
                                out.append({
                                    "variant_id": (
                                        f"LB{lookback}_PB{window}"
                                        f"_BUF{buf:.2f}_RISK{max_risk:.1f}"
                                        f"_TGT{target:.1f}R_HOLD{hold}"
                                        f"_GATE{gate:.0f}"
                                    ),
                                    "lookback": int(lookback),
                                    "pullback_window": int(window),
                                    "stop_buffer_atr": float(buf),
                                    "max_risk_atr": float(max_risk),
                                    "target_r": float(target),
                                    "max_hold_sessions": int(hold),
                                    "cost_gate": float(gate),
                                })
    return out


TOTAL_PARAMETERIZATIONS = len(variants())
