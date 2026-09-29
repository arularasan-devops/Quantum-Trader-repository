"""Phase 56 stage 3 — long-only NSE cash-equity mechanism research.

Everything in this package is historical research and, if anything survives,
paper only. There is no order path, no broker import and no production signal
anywhere in it, and it touches nothing in phases 41-55.

It runs on the stage-2 archive dataset (`app.research.phase56.nse`), which passed
the §36 gate: raw exchange prices, dated corporate actions with derivable ratios,
and securities that stopped trading still present in every session up to their
last print. Two declared limitations carry through untouched and are printed in
every artefact: no historical index membership (so the universe is the declared
liquidity screen, never called an index) and no historical sector classification
(so the §14 sector cap is reported UNENFORCED rather than assumed satisfied).

The design decision that shapes this whole package: **the decision point is the
close of session t-1 and every entry fills at the open of session t.** Features
are therefore computed from sessions strictly earlier than the fill. That single
convention is what makes look-ahead structurally impossible rather than
carefully avoided — there is no code path that can see session t when deciding
to buy at its open, because the feature matrices are shifted before any
mechanism is evaluated.

Pre-registration is frozen here, before measurement, and fingerprinted: the
entry families, their parameter grid, the exit families, the partitions, the
cost model, the hypothesis budget and the promotion gates. A mechanism that is
not in this file cannot be tested, which is the mechanism that stops a grid from
quietly growing until something looks significant.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

VERSION = "phase56.v3.equity_mechanism_study"

# --------------------------------------------------------------- labels

#: Daily exchange bars. Not an executable book: no bid, ask or depth exists in
#: this data and none is invented anywhere in this package.
DATA_LABEL = "HISTORICAL_DAILY_BARS_RAW_EXCHANGE_ARCHIVE"
#: Ceiling this evidence can reach on its own. Phase 30 rules apply: historical
#: data cannot authorise a production signal, only paper observation.
EVIDENCE_CEILING = "HISTORICAL_LEAD"
UNIVERSE_LABEL = "DECLARED_LIQUIDITY_UNIVERSE"
MARKET_CONTEXT_LABEL = "NIFTY_500_PUBLISHED_INDEX_CLOSE"
SECTOR_CAP_STATUS = "UNENFORCED_NO_HISTORICAL_SECTOR_CLASSIFICATION"
COST_LABEL = "MODELLED_COST"

# --------------------------------------------------------------- decisions

NO_TRADE = "NO_TRADE"
PAPER_BUY = "PAPER_BUY"
WAIT = "WAIT"
HOLD = "HOLD"
EXIT = "EXIT"

EXIT_STOP = "STOP"
EXIT_TARGET = "TARGET"
EXIT_TIME = "TIME_EXIT"
EXIT_INVALIDATED = "SIGNAL_INVALIDATED"
EXIT_OPEN = "STILL_OPEN"

#: §35 forbids these labels outright. Asserted by the smoke suite against every
#: artefact this package writes.
FORBIDDEN_LABELS = ("BUY_PROBABILITY", "CONFIDENCE_SCORE", "GUARANTEED_RETURN")

# --------------------------------------------------------------- statuses

ROBUST_CANDIDATE = "ROBUST_CANDIDATE"
HISTORICAL_LEAD = "HISTORICAL_LEAD"
PROMISING_NEEDS_DATA = "PROMISING_NEEDS_DATA"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
OVERFIT_RISK = "OVERFIT_RISK"
SURVIVORSHIP_RISK = "SURVIVORSHIP_RISK"
COST_BLOCKED = "COST_BLOCKED"
NO_EDGE = "NO_EDGE"

#: §20 replication breadth.
SINGLE_STOCK = "SINGLE_STOCK"
SMALL_CLUSTER = "SMALL_CLUSTER"
MULTI_STOCK = "MULTI_STOCK"
BROAD_UNIVERSE = "BROAD_UNIVERSE"

# --------------------------------------------------------------- partitions

#: §18: chronological, never shuffled. Fractions of the session axis.
DISCOVERY_FRACTION = 0.60
VALIDATION_FRACTION = 0.20
#: The remainder is the untouched holdout, evaluated once, after freezing.
HOLDOUT_FRACTION = 0.20

#: Walk-forward: expanding-origin folds inside discovery+validation only.
WALK_FORWARD_FOLDS = 5

# --------------------------------------------------------------- sizing

PAPER_CAPITAL_INR = 10_00_000.0
#: One position's notional in the trade-level study. Fixed so the flat parts of
#: the cost model (₹20 brokerage, ₹13 DP) convert to a comparable fraction.
POSITION_NOTIONAL_INR = 1_00_000.0
MAX_WEIGHT_PER_STOCK = 0.10
MAX_WEIGHT_PER_SECTOR = 0.25
PORTFOLIO_SIZES = (1, 5, 10, 20)
WEIGHTING_METHODS = ("EQUAL_WEIGHT", "INVERSE_VOLATILITY")

# --------------------------------------------------------------- cost stress

COST_MULTIPLIERS = (1.0, 1.5, 2.0)

# --------------------------------------------------------------- feature grid

#: Lookbacks used by features, all applied to sessions strictly before the fill.
RETURN_LOOKBACKS = (1, 3, 5, 10, 20, 60)
HIGH_LOOKBACKS = (20, 50, 252)
MA_LOOKBACKS = (20, 50)
ATR_LOOKBACK = 14
VOL_LOOKBACK = 20
VOLUME_LOOKBACK = 20

#: §10 holding horizons, in trading sessions.
HOLD_HORIZONS = (1, 3, 5, 10, 20, 40, 60, 120)

# --------------------------------------------------------------- entry grid

#: The registered entry grid. Every tested entry is one row of this table and
#: nothing else is testable: ``family`` selects the code path in mechanisms.py,
#: ``params`` are the only thresholds allowed for it.
#:
#: Thresholds are round numbers chosen before measurement, two or three per
#: family. They are not tuned; a grid that is re-tuned after seeing results is
#: the overfit this whole structure exists to prevent.
ENTRY_GRID: tuple[dict, ...] = (
    # previous-day structures (§5)
    {"name": "PDH_CLOSE_BREAK", "family": "PREV_DAY_HIGH_CLOSE_BREAK", "params": {}},
    {"name": "PDH_CLOSE_BREAK_VOL", "family": "PREV_DAY_HIGH_CLOSE_BREAK", "params": {"volume_ratio_min": 1.5}},
    {"name": "PDC_BREAK", "family": "PREV_DAY_CLOSE_BREAK", "params": {}},
    {"name": "PDL_RECLAIM", "family": "PREV_DAY_LOW_RECLAIM", "params": {}},
    {"name": "PDC_REVERSAL", "family": "PREV_DAY_CLOSE_REVERSAL", "params": {}},
    # momentum (§5)
    {"name": "MOM_5", "family": "MOMENTUM", "params": {"lookback": 5, "min_return": 0.05}},
    {"name": "MOM_10", "family": "MOMENTUM", "params": {"lookback": 10, "min_return": 0.08}},
    {"name": "MOM_20", "family": "MOMENTUM", "params": {"lookback": 20, "min_return": 0.10}},
    {"name": "MOM_60", "family": "MOMENTUM", "params": {"lookback": 60, "min_return": 0.20}},
    {"name": "MOM_20_TREND", "family": "MOMENTUM", "params": {"lookback": 20, "min_return": 0.10, "above_ma": 50}},
    {"name": "MOM_20_VOL", "family": "MOMENTUM", "params": {"lookback": 20, "min_return": 0.10, "volume_ratio_min": 1.5}},
    # N-day and 52-week high breakouts (§5)
    {"name": "HIGH_20_BREAK", "family": "N_DAY_HIGH_BREAK", "params": {"lookback": 20}},
    {"name": "HIGH_50_BREAK", "family": "N_DAY_HIGH_BREAK", "params": {"lookback": 50}},
    {"name": "HIGH_252_BREAK", "family": "N_DAY_HIGH_BREAK", "params": {"lookback": 252}},
    {"name": "HIGH_20_BREAK_VOL", "family": "N_DAY_HIGH_BREAK", "params": {"lookback": 20, "volume_ratio_min": 1.5}},
    # mean reversion (§5)
    {"name": "DROP_1", "family": "DECLINE", "params": {"lookback": 1, "max_return": -0.06}},
    {"name": "DROP_3", "family": "DECLINE", "params": {"lookback": 3, "max_return": -0.10}},
    {"name": "DROP_5", "family": "DECLINE", "params": {"lookback": 5, "max_return": -0.12}},
    {"name": "BELOW_MA20", "family": "BELOW_MA", "params": {"lookback": 20, "max_distance": -0.10}},
    {"name": "ABNORMAL_RANGE", "family": "ABNORMAL_RANGE", "params": {"min_range_atr": 2.0}},
    {"name": "VOLUME_SELLOFF_STABILISE", "family": "VOLUME_SELLOFF_STABILISE", "params": {"lookback": 3, "max_return": -0.08, "volume_ratio_min": 2.0}},
    # volatility (§5)
    {"name": "VOL_COMPRESSION", "family": "VOL_COMPRESSION", "params": {"max_atr_pct": 0.02, "above_ma": 50}},
    {"name": "RANGE_COMPRESSION", "family": "RANGE_COMPRESSION", "params": {"max_range_pct": 0.06}},
    {"name": "VOL_EXPANSION", "family": "VOL_EXPANSION", "params": {"min_atr_ratio": 1.5}},
    # pullback (§5)
    {"name": "TREND_PULLBACK", "family": "TREND_PULLBACK", "params": {"above_ma": 50, "min_pullback": 0.05, "window": 10}},
    {"name": "BREAKOUT_PULLBACK", "family": "BREAKOUT_PULLBACK", "params": {"lookback": 20, "window": 10, "min_pullback": 0.04}},
    {"name": "BREAKOUT_RECLAIM", "family": "BREAKOUT_RECLAIM", "params": {"lookback": 20, "window": 10}},
    # relative strength (§5)
    {"name": "RS_20", "family": "RELATIVE_STRENGTH", "params": {"lookback": 20, "min_excess": 0.05}},
    {"name": "RS_60", "family": "RELATIVE_STRENGTH", "params": {"lookback": 60, "min_excess": 0.10}},
    # cross-sectional selection (§5) — rank the eligible universe, take the top slice
    {"name": "XS_RET_20", "family": "CROSS_SECTIONAL", "params": {"metric": "ret_20", "top_n": 20}},
    {"name": "XS_RET_60", "family": "CROSS_SECTIONAL", "params": {"metric": "ret_60", "top_n": 20}},
    {"name": "XS_VOLADJ_20", "family": "CROSS_SECTIONAL", "params": {"metric": "voladj_20", "top_n": 20}},
    {"name": "XS_RS_60", "family": "CROSS_SECTIONAL", "params": {"metric": "rs_60", "top_n": 20}},
    {"name": "XS_WORST_5", "family": "CROSS_SECTIONAL", "params": {"metric": "ret_5", "top_n": 20, "ascending": True}},
)

#: The registered exit grid (§8). ``hold`` is the maximum holding period in
#: sessions; ``stop_atr`` and ``target_atr`` are multiples of the signal-date
#: ATR; ``ma_exit`` closes on the first close below that moving average;
#: ``prev_low_stop`` invalidates on a break of the signal session's low;
#: ``trail_atr`` is a ratchet from the highest close reached.
EXIT_GRID: tuple[dict, ...] = (
    {"name": "HOLD_1", "hold": 1},
    {"name": "HOLD_3", "hold": 3},
    {"name": "HOLD_5", "hold": 5},
    {"name": "HOLD_10", "hold": 10},
    {"name": "HOLD_20", "hold": 20},
    {"name": "HOLD_40", "hold": 40},
    {"name": "HOLD_60", "hold": 60},
    {"name": "HOLD_120", "hold": 120},
    {"name": "STOP2_TARGET3_H20", "hold": 20, "stop_atr": 2.0, "target_atr": 3.0},
    {"name": "STOP2_TARGET4_H40", "hold": 40, "stop_atr": 2.0, "target_atr": 4.0},
    {"name": "STOP3_TARGET6_H60", "hold": 60, "stop_atr": 3.0, "target_atr": 6.0},
    {"name": "STOP2_MA20_H60", "hold": 60, "stop_atr": 2.0, "ma_exit": 20},
    {"name": "PREVLOW_MA20_H60", "hold": 60, "prev_low_stop": True, "ma_exit": 20},
    {"name": "TRAIL2_H60", "hold": 60, "stop_atr": 2.0, "trail_atr": 2.0},
)

#: §19 denominator. Every registered entry x exit pair is a hypothesis and stays
#: in the denominator whether or not it is later looked at.
TOTAL_HYPOTHESES = len(ENTRY_GRID) * len(EXIT_GRID)

#: Benjamini-Hochberg level applied over TOTAL_HYPOTHESES.
FDR_ALPHA = 0.10

# --------------------------------------------------------------- gates (§31)

MIN_TRADES = 200
MIN_STOCKS = 30
MIN_SESSIONS = 100
#: Portfolio drawdown ceiling for a robust candidate.
MAX_DRAWDOWN_LIMIT = 0.35
#: No single stock may contribute more than this share of total net profit.
MAX_SINGLE_STOCK_PROFIT_SHARE = 0.35
#: No single year may contribute more than this share of total net profit.
MAX_SINGLE_YEAR_PROFIT_SHARE = 0.60


@dataclass(frozen=True)
class Preregistration:
    version: str = VERSION
    data_label: str = DATA_LABEL
    evidence_ceiling: str = EVIDENCE_CEILING
    universe_label: str = UNIVERSE_LABEL
    market_context: str = MARKET_CONTEXT_LABEL
    sector_cap_status: str = SECTOR_CAP_STATUS
    decision_point: str = "CLOSE_OF_SESSION_T_MINUS_1"
    entry_price_rule: str = "NEXT_SESSION_OPEN"
    long_only: bool = True
    leverage: bool = False
    shorting: bool = False
    partitions: dict = field(
        default_factory=lambda: {
            "discovery": DISCOVERY_FRACTION,
            "validation": VALIDATION_FRACTION,
            "untouched_holdout": HOLDOUT_FRACTION,
            "ordering": "CHRONOLOGICAL_NEVER_SHUFFLED",
            "walk_forward_folds": WALK_FORWARD_FOLDS,
        }
    )
    entry_grid: tuple = ENTRY_GRID
    exit_grid: tuple = EXIT_GRID
    hold_horizons: tuple = HOLD_HORIZONS
    total_hypotheses: int = TOTAL_HYPOTHESES
    fdr_alpha: float = FDR_ALPHA
    cost_multipliers: tuple = COST_MULTIPLIERS
    portfolio_sizes: tuple = PORTFOLIO_SIZES
    weighting_methods: tuple = WEIGHTING_METHODS
    gates: dict = field(
        default_factory=lambda: {
            "min_trades": MIN_TRADES,
            "min_stocks": MIN_STOCKS,
            "min_sessions": MIN_SESSIONS,
            "max_drawdown": MAX_DRAWDOWN_LIMIT,
            "max_single_stock_profit_share": MAX_SINGLE_STOCK_PROFIT_SHARE,
            "max_single_year_profit_share": MAX_SINGLE_YEAR_PROFIT_SHARE,
        }
    )
    limitations: tuple = (
        "HISTORICAL_INDEX_MEMBERSHIP_UNAVAILABLE_UNIVERSE_IS_A_DECLARED_LIQUIDITY_SCREEN",
        "HISTORICAL_SECTOR_CLASSIFICATION_UNAVAILABLE_SECTOR_CAP_UNENFORCED",
        "DAILY_BARS_ONLY_NO_INTRADAY_PATH_WITHIN_A_SESSION",
        "NO_BID_ASK_OR_DEPTH_EXISTS_IN_THIS_DATA_AND_NONE_IS_FABRICATED",
        "SLIPPAGE_AND_COSTS_ARE_MODELLED_NOT_OBSERVED",
    )

    def as_dict(self) -> dict:
        out = asdict(self)
        out["entry_grid"] = [dict(row) for row in ENTRY_GRID]
        out["exit_grid"] = [dict(row) for row in EXIT_GRID]
        out["hold_horizons"] = list(HOLD_HORIZONS)
        out["cost_multipliers"] = list(COST_MULTIPLIERS)
        out["portfolio_sizes"] = list(PORTFOLIO_SIZES)
        out["weighting_methods"] = list(WEIGHTING_METHODS)
        out["limitations"] = list(self.limitations)
        return out

    def fingerprint(self) -> str:
        blob = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


PREREG = Preregistration()
