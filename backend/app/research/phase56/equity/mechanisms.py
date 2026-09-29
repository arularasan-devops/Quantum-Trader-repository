"""Entry families (§5). Each returns a boolean date x symbol signal matrix.

Every family reads only `panel.features`, which already contains nothing but
information from sessions strictly before the fill. A family that wanted to peek
at the fill session has nothing to peek at.

The signal matrix is intersected with the §12 eligibility mask before it leaves
this module, so a mechanism can never trade a name that was illiquid, too cheap,
too short-lived or outside the declared universe on that date. That intersection
is done here rather than in the caller because "the mechanism fired but the name
was not eligible" is not a trade, and counting it as one would inflate every
sample count in the study.
"""
from __future__ import annotations

import numpy as np

from .panel import Panel

UNKNOWN_FAMILY = "UNKNOWN_ENTRY_FAMILY"


def _finite(*matrices: np.ndarray) -> np.ndarray:
    out = np.isfinite(matrices[0])
    for matrix in matrices[1:]:
        out &= np.isfinite(matrix)
    return out


def _volume_filter(panel: Panel, params: dict) -> np.ndarray:
    minimum = params.get("volume_ratio_min")
    if minimum is None:
        return np.ones(panel.shape, dtype=bool)
    ratio = panel.features["volume_ratio"]
    return np.isfinite(ratio) & (ratio >= minimum)


def _trend_filter(panel: Panel, params: dict) -> np.ndarray:
    lookback = params.get("above_ma")
    if lookback is None:
        return np.ones(panel.shape, dtype=bool)
    close = panel.features["prev_close"]
    average = panel.features[f"ma_{lookback}"]
    return _finite(close, average) & (close > average)


def signal(panel: Panel, spec: dict) -> np.ndarray:
    """The boolean entry matrix for one registered grid row."""
    family = spec["family"]
    params = spec.get("params", {})
    feature = panel.features

    if family == "PREV_DAY_HIGH_CLOSE_BREAK":
        close, high = feature["prev_close"], feature["prev2_high"]
        raw = _finite(close, high) & (close > high)
    elif family == "PREV_DAY_CLOSE_BREAK":
        close, prior = feature["prev_close"], feature["prev2_close"]
        raw = _finite(close, prior) & (close > prior)
    elif family == "PREV_DAY_LOW_RECLAIM":
        # Weakness then reclaim: yesterday traded below the prior session's low
        # and still closed above it.
        low, prior_low, close = feature["prev_low"], feature["prev2_low"], feature["prev_close"]
        raw = _finite(low, prior_low, close) & (low < prior_low) & (close > prior_low)
    elif family == "PREV_DAY_CLOSE_REVERSAL":
        # Closed in the top quarter of its own range after opening lower.
        high, low, close, open_ = (
            feature["prev_high"],
            feature["prev_low"],
            feature["prev_close"],
            feature["prev_open"],
        )
        span = high - low
        raw = (
            _finite(high, low, close, open_)
            & (span > 0)
            & (close < open_)
            & ((close - low) / np.where(span > 0, span, np.nan) >= 0.75)
        )
    elif family == "MOMENTUM":
        returns = feature[f"ret_{params['lookback']}"]
        raw = np.isfinite(returns) & (returns >= params["min_return"])
    elif family == "N_DAY_HIGH_BREAK":
        close = feature["prev_close"]
        level = feature[f"high_{params['lookback']}"]
        raw = _finite(close, level) & (close >= level)
    elif family == "DECLINE":
        returns = feature[f"ret_{params['lookback']}"]
        raw = np.isfinite(returns) & (returns <= params["max_return"])
    elif family == "BELOW_MA":
        distance = feature[f"dist_ma_{params['lookback']}"]
        raw = np.isfinite(distance) & (distance <= params["max_distance"])
    elif family == "ABNORMAL_RANGE":
        ratio = feature["prev_range_atr"]
        raw = np.isfinite(ratio) & (ratio >= params["min_range_atr"])
    elif family == "VOLUME_SELLOFF_STABILISE":
        returns = feature[f"ret_{params['lookback']}"]
        volume = feature["volume_ratio"]
        close, open_ = feature["prev_close"], feature["prev_open"]
        raw = (
            _finite(returns, volume, close, open_)
            & (returns <= params["max_return"])
            & (volume >= params["volume_ratio_min"])
            & (close >= open_)  # the stabilisation session itself
        )
    elif family == "VOL_COMPRESSION":
        atr_pct = feature["atr_pct"]
        raw = np.isfinite(atr_pct) & (atr_pct <= params["max_atr_pct"])
    elif family == "RANGE_COMPRESSION":
        span = feature["range_pct_20"]
        raw = np.isfinite(span) & (span <= params["max_range_pct"])
    elif family == "VOL_EXPANSION":
        ratio = feature["atr_ratio"]
        raw = np.isfinite(ratio) & (ratio >= params["min_atr_ratio"])
    elif family == "TREND_PULLBACK":
        drawdown = feature["drawdown_from_high_20"]
        raw = np.isfinite(drawdown) & (drawdown <= -params["min_pullback"])
    elif family == "BREAKOUT_PULLBACK":
        level = feature[f"high_{params['lookback']}"]
        close = feature["prev_close"]
        # Broke out inside the window, then pulled back below the level.
        broke = _recent(_finite(close, level) & (close >= level), params["window"])
        drawdown = feature["drawdown_from_high_20"]
        raw = broke & np.isfinite(drawdown) & (drawdown <= -params["min_pullback"])
    elif family == "BREAKOUT_RECLAIM":
        level = feature[f"high_{params['lookback']}"]
        close = feature["prev_close"]
        above = _finite(close, level) & (close >= level)
        # Reclaimed the level after at least one session back below it.
        raw = above & _recent(~above, params["window"]) & ~_shift_bool(above, 1)
    elif family == "RELATIVE_STRENGTH":
        excess = feature[f"rs_{params['lookback']}"]
        raw = np.isfinite(excess) & (excess >= params["min_excess"])
    elif family == "CROSS_SECTIONAL":
        raw = _cross_sectional(panel, params)
    else:
        raise ValueError(f"{UNKNOWN_FAMILY}: {family}")

    raw = raw & _volume_filter(panel, params) & _trend_filter(panel, params)
    return raw & panel.eligible


def _shift_bool(matrix: np.ndarray, periods: int) -> np.ndarray:
    out = np.zeros_like(matrix)
    if periods > 0:
        out[periods:] = matrix[:-periods]
    return out


def _recent(matrix: np.ndarray, window: int) -> np.ndarray:
    """True where ``matrix`` was true at least once in the previous ``window``."""
    out = np.zeros_like(matrix)
    for offset in range(1, window + 1):
        out |= _shift_bool(matrix, offset)
    return out


def _cross_sectional(panel: Panel, params: dict) -> np.ndarray:
    """Rank the eligible universe by one prior-information metric, take top N."""
    metric = panel.features[params["metric"]]
    ascending = bool(params.get("ascending", False))
    top_n = int(params["top_n"])
    scores = np.where(panel.eligible & np.isfinite(metric), metric, np.nan)
    if ascending:
        scores = -scores
    out = np.zeros(panel.shape, dtype=bool)
    order = np.argsort(np.where(np.isfinite(scores), -scores, np.inf), axis=1, kind="stable")
    rows = np.arange(panel.shape[0])[:, None]
    columns = order[:, :top_n]
    out[rows, columns] = True
    # argsort always yields N columns even when fewer names scored; drop those.
    return out & np.isfinite(scores)


def all_signals(panel: Panel, grid) -> dict[str, np.ndarray]:
    return {spec["name"]: signal(panel, spec) for spec in grid}
