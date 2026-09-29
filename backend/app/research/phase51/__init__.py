"""PHASE 51 — HISTORICAL ALPHA DISCOVERY ENGINE (RESEARCH ONLY).

A separate research engine. Nothing in this package reads, writes or influences
the production signal engine, the paper execution rules, the broker order path,
or the definitions and fingerprints of Phases 41-50. The only code it borrows is
read-only: the stored candle series, the causal feature helpers and the futures
cost model.

The question this package asks is not "does mechanism X work". It is "what
conditional relationship in the stored history survives costs, a corrected
multiple-testing denominator, a chronological split and an untouched holdout".
Searching broadly makes a flattering result more likely by construction, so the
validation is deliberately stricter than the search is wide, and the engine is
built so that **zero robust candidates is a reportable outcome** rather than a
failure state to be tuned away.

What the data on disk supports, stated before any result:

* the stored five-year 1-minute series are **HISTORICAL_CANDLE_DATA**: open,
  high, low, close and volume. There is no bid, no ask and no depth, so no
  result here is an executable fill. Every net figure is optimistic by at least
  one spread and says so on the row;
* an imported CSV instrument is read through the existing historical-import
  adapter, so a new instrument needs a registered dataset and no code change.
  The adapter decides eligibility; this package never re-judges it;
* **historical options are not priced.** A premium is never modelled from a
  candle, so the CE/PE and vehicle-selection families report
  ``PROMISING_NEEDS_DATA`` unless a captured two-sided book exists at the exact
  instant. That is a data fact, not a finding;
* a 1-minute bar whose range contains both the stop and the target cannot say
  which came first. The stop wins. The optimistic reading of that tie is the
  single cheapest way to manufacture an edge that does not exist.

Three edges are measured separately and never summed into one number, because a
rule that only pays under one exit grid has found an exit, not an entry:

``ENTRY_EDGE``            the conditional relationship, on one frozen reference exit
``PROFIT_CAPTURE_EDGE``   what a searched exit adds to an already-surviving entry
``VEHICLE_SELECTION_EDGE`` which instrument the same relationship is best taken in
"""
from __future__ import annotations

import hashlib

VERSION = "PHASE51_HISTORICAL_ALPHA_DISCOVERY_V1"

# ---------------------------------------------------------------- vocabulary
# §14: the only statuses a candidate may end on. There is deliberately no
# status that means "likely to work", and no field anywhere in this package
# holds a probability, a confidence or a prediction.
ROBUST_CANDIDATE = "ROBUST_CANDIDATE"
PROMISING_NEEDS_DATA = "PROMISING_NEEDS_DATA"
HISTORICAL_LEAD = "HISTORICAL_LEAD"
OVERFIT_RISK = "OVERFIT_RISK"
REJECTED = "REJECTED"

FINAL_STATUSES = (
    ROBUST_CANDIDATE,
    PROMISING_NEEDS_DATA,
    HISTORICAL_LEAD,
    OVERFIT_RISK,
    REJECTED,
)

# §11: two outputs, and the distinction is mandatory. A lead is a historical
# observation; a candidate has passed every gate. They are never merged.
DISCOVERY_LEADS = "DISCOVERY_LEADS"
ROBUST_CANDIDATES = "ROBUST_CANDIDATES"

# §13: the three edges, kept apart.
ENTRY_EDGE = "ENTRY_EDGE"
PROFIT_CAPTURE_EDGE = "PROFIT_CAPTURE_EDGE"
VEHICLE_SELECTION_EDGE = "VEHICLE_SELECTION_EDGE"

# §2: what the source is, and what it is not.
HISTORICAL_CANDLE_DATA = "HISTORICAL_CANDLE_DATA"
NOT_EXECUTABLE_BOOK = (
    "A_CANDLE_HAS_NO_BID_NO_ASK_AND_NO_DEPTH_SO_EVERY_FILL_HERE_IS_MODELLED_FROM_"
    "THE_NEXT_BARS_OPEN_PLUS_CONFIGURED_SLIPPAGE_AND_EVERY_NET_FIGURE_IS_"
    "OPTIMISTIC_BY_AT_LEAST_ONE_UNMEASURED_SPREAD"
)
NO_OPTION_PRICING = (
    "NO_HISTORICAL_OPTION_PREMIUM_IS_MODELLED_FROM_AN_UNDERLYING_CANDLE_SO_THE_"
    "CE_PE_AND_VEHICLE_FAMILIES_REPORT_PROMISING_NEEDS_DATA_RATHER_THAN_A_RESULT"
)
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"

# §9: chronological only. Stated as a label so it appears in the artefact.
NO_SHUFFLE = (
    "PARTITIONS_ARE_CHRONOLOGICAL_THE_SERIES_IS_NEVER_SHUFFLED_AND_A_CANDIDATE_"
    "IS_FROZEN_BEFORE_IT_IS_EVALUATED_ON_THE_UNTOUCHED_HOLDOUT"
)
DISCOVERY = "DISCOVERY"
VALIDATION = "VALIDATION"
UNTOUCHED_HOLDOUT = "UNTOUCHED_HOLDOUT"
PARTITIONS = (DISCOVERY, VALIDATION, UNTOUCHED_HOLDOUT)

# §8: what the correction is applied to.
FDR_DENOMINATOR = (
    "THE_BENJAMINI_HOCHBERG_DENOMINATOR_IS_EVERY_HYPOTHESIS_THE_ENGINE_EVALUATED_"
    "INCLUDING_THE_ONES_DISCARDED_BEFORE_RANKING_A_CORRECTION_APPLIED_TO_THE_"
    "SURVIVORS_ONLY_WOULD_HAND_BACK_SIGNIFICANCE_THE_SEARCH_DID_NOT_EARN"
)
FDR_ALPHA = 0.05

# §12: the objective, in one line, so a reader cannot mistake the ranking key.
OBJECTIVE = (
    "THE_OBJECTIVE_IS_POSITIVE_NET_EXPECTANCY_AFTER_COSTS_NOT_MAXIMUM_WIN_RATE_A_"
    "LOW_WIN_RATE_RULE_WITH_A_BETTER_PAYOFF_IS_VALID_AND_A_HIGH_WIN_RATE_RULE_"
    "WITH_NEGATIVE_NET_EXPECTANCY_IS_INVALID"
)

# §15: what success is not.
NOT_SUCCESS = (
    "A_PROFITABLE_BACKTEST_IS_NOT_A_RESULT_SUCCESS_IS_A_SIMPLE_INTERPRETABLE_"
    "RELATIONSHIP_THAT_REMAINS_POSITIVE_AFTER_REALISTIC_COSTS_MULTIPLE_TESTING_"
    "CORRECTION_CHRONOLOGICAL_VALIDATION_AND_AN_UNTOUCHED_HOLDOUT"
)

# §16: no production impact.
NO_ORDER_PATH = (
    "THIS_PACKAGE_PLACES_NO_ORDER_READS_NO_BROKER_SESSION_CHANGES_NO_PRODUCTION_"
    "SIGNAL_AND_ALTERS_NO_PHASE_41_TO_50_DEFINITION_OR_FINGERPRINT"
)

# §10: a candidate that rests on one period, one trade or one regime is not a
# finding. These are the bars, frozen here before anything is measured.
MIN_TRADES = 100
MIN_SESSIONS = 30
MIN_YEARS_POSITIVE = 3          # of the five, how many must be net positive
MAX_TOP_TRADE_SHARE = 0.35      # one trade may not carry more than this of net R
COST_STRESS_MULTIPLES = (1.0, 1.5, 2.0)
MAX_DRAWDOWN_R = 25.0


def preregistration() -> dict:
    """Everything frozen before the search runs, as one payload.

    This is the object the fingerprint is taken over. If a later edit changes a
    threshold, a partition, a cost stress or the statuses, the fingerprint moves
    and results measured under the old definition cannot be pooled with results
    measured under the new one.
    """
    return {
        "version": VERSION,
        "objective": OBJECTIVE,
        "source": HISTORICAL_CANDLE_DATA,
        "not_executable_book": NOT_EXECUTABLE_BOOK,
        "no_option_pricing": NO_OPTION_PRICING,
        "partitions": list(PARTITIONS),
        "no_shuffle": NO_SHUFFLE,
        "fdr_alpha": FDR_ALPHA,
        "fdr_denominator": FDR_DENOMINATOR,
        "final_statuses": list(FINAL_STATUSES),
        "edges": [ENTRY_EDGE, PROFIT_CAPTURE_EDGE, VEHICLE_SELECTION_EDGE],
        "min_trades": MIN_TRADES,
        "min_sessions": MIN_SESSIONS,
        "min_years_positive": MIN_YEARS_POSITIVE,
        "max_top_trade_share": MAX_TOP_TRADE_SHARE,
        "cost_stress_multiples": list(COST_STRESS_MULTIPLES),
        "max_drawdown_r": MAX_DRAWDOWN_R,
        "not_success": NOT_SUCCESS,
        "no_order_path": NO_ORDER_PATH,
    }


def _flatten(obj: object) -> str:
    if isinstance(obj, dict):
        return "{" + ",".join(
            f"{k}:{_flatten(v)}" for k, v in sorted(obj.items())
        ) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_flatten(v) for v in obj) + "]"
    if isinstance(obj, float):
        return f"{obj:g}"
    return str(obj)


def preregistration_fingerprint() -> str:
    """Stable 16-hex digest of the frozen pre-registration.

    Independent of every earlier fingerprint by construction: it is taken over
    this package's own payload and contains no Phase 41-50 field, so nothing in
    those phases can move it and it cannot move them.
    """
    return hashlib.sha256(
        _flatten(preregistration()).encode()
    ).hexdigest()[:16]
