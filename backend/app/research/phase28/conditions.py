"""Phase 28 §4 — the daily condition vocabulary, fixed before the split.

Twenty-eight named conditions, each a boolean per candidate. The list is closed:
discovery may combine at most three of these and may not invent a twenty-ninth,
because an open vocabulary is how a bounded study turns into the endless indicator
search this programme has already stopped once.

The vocabulary is deliberately the one the screenshots and the trade-forum
material describe — EMA stacks, RSI, breakouts of the 20/50-day range, pullback
depth, the 200-day regime, volume expansion — restated as daily conditions and
measured, so "have we tested that setup" has a numeric answer for the swing
timeframe as well as the intraday one.

Direction matters: ``_agrees`` means the condition is read relative to the
candidate's own side, so ``trend_short_agrees`` is an up-trend for a long and a
down-trend for a short. Conditions with no side in their name are properties of
the bar and read the same for both sides.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24.outcomes import LONG
from app.research.phase28.features import DOWN, UP

# Pullback bands, in per cent of the 20-day swing range retraced from the extreme
# in the side's favour. Bands rather than a threshold so a rule cannot quietly
# tune a boundary.
PULLBACK_BANDS = (
    ("pullback_0_20pct", 0.0, 20.0),
    ("pullback_20_40pct", 20.0, 40.0),
    ("pullback_40_60pct", 40.0, 60.0),
    ("pullback_60_100pct", 60.0, 100.0),
)


def _dir(side: np.ndarray) -> np.ndarray:
    """+1 for a long candidate, -1 for a short."""
    return np.where(side == LONG, 1.0, -1.0)


def masks(feat: dict[str, np.ndarray], side: np.ndarray) -> dict[str, np.ndarray]:
    """Every condition as a boolean array aligned to the candidate arrays."""
    sgn = _dir(side)
    close = feat["close"]
    atr = feat["atr"]
    rsi = feat["rsi"]

    trend_short = feat["trend_short"]
    trend_long = feat["trend_long"]
    want = np.where(side == LONG, UP, DOWN)
    against = np.where(side == LONG, DOWN, UP)

    ema_up = feat["ema_fast_above_slow"] == 1
    regime_up = feat["above_regime_ema"] == 1

    breakout_20 = np.where(
        side == LONG, close > feat["prior_high_20"], close < feat["prior_low_20"]
    )
    breakout_50 = np.where(
        side == LONG, close > feat["prior_high_50"], close < feat["prior_low_50"]
    )

    pullback_pct = np.where(
        side == LONG, feat["pullback_from_high_pct"], feat["pullback_from_low_pct"]
    )

    mom_short = feat["mom_short_atr"] * sgn
    mom_long = feat["mom_long_atr"] * sgn
    gap = feat["gap_pct"] * sgn

    # Room to the 20-day extreme ahead of the trade, in ATR: a long into the top
    # of its own range has nowhere to go, and that is knowable at the close.
    ahead = np.where(
        side == LONG, feat["prior_high_20"] - close, close - feat["prior_low_20"]
    )
    room_atr = ahead / atr

    out: dict[str, np.ndarray] = {
        # trend and regime
        "trend_short_agrees": trend_short == want,
        "trend_long_agrees": trend_long == want,
        "trend_both_agree": (trend_short == want) & (trend_long == want),
        "trend_long_opposes": trend_long == against,
        "ema_stack_agrees": np.where(side == LONG, ema_up, ~ema_up),
        "above_200d_regime": np.where(side == LONG, regime_up, ~regime_up),
        # momentum / oscillator
        "rsi_agrees_50": np.where(side == LONG, rsi > 50.0, rsi < 50.0),
        "rsi_stretched_70_30": np.where(side == LONG, rsi >= 70.0, rsi <= 30.0),
        "rsi_reset_40_60": np.where(side == LONG, rsi <= 40.0, rsi >= 60.0),
        "momentum_5d_agrees": mom_short >= 0.5,
        "momentum_20d_agrees": mom_long >= 1.0,
        "momentum_5d_against": mom_short <= -0.5,
        # structure
        "breakout_20d": breakout_20,
        "breakout_50d": breakout_50,
        "near_52w_high": feat["pct_from_year_high"] >= -3.0,
        "far_from_52w_high": feat["pct_from_year_high"] <= -15.0,
        "room_2atr_ahead": room_atr >= 2.0,
        "room_4atr_ahead": room_atr >= 4.0,
        # volatility and bar shape
        "volatility_expanding": feat["vol_ratio"] >= 1.2,
        "volatility_compressed": feat["vol_ratio"] <= 0.8,
        "inside_narrow_range": feat["range_over_atr"] <= 0.7,
        "wide_range_bar_1p5x": feat["range_over_atr"] >= 1.5,
        "strong_body_agrees": (feat["body_over_range"] >= 0.6)
        & np.where(side == LONG, feat["closed_up"] == 1, feat["closed_up"] == 0),
        "volume_expansion_1p5x": feat["volume_ratio"] >= 1.5,
        "volume_dry_0p7x": feat["volume_ratio"] <= 0.7,
        # the day itself
        "gap_open_1pct_agrees": gap >= 1.0,
        "gap_open_against_1pct": gap <= -1.0,
    }
    for name, lo, hi in PULLBACK_BANDS:
        out[name] = (pullback_pct >= lo) & (pullback_pct < hi)
    return {k: np.asarray(v, dtype=bool) for k, v in out.items()}


def names() -> tuple[str, ...]:
    """The closed vocabulary, for the artefacts and the smoke test."""
    dummy_feat: dict[str, np.ndarray] = {
        k: np.zeros(2, dtype=np.float64)
        for k in (
            "close", "atr", "rsi", "prior_high_20", "prior_low_20", "prior_high_50",
            "prior_low_50", "pullback_from_high_pct", "pullback_from_low_pct",
            "mom_short_atr", "mom_long_atr", "gap_pct", "vol_ratio",
            "range_over_atr", "body_over_range", "volume_ratio",
            "pct_from_year_high",
        )
    }
    dummy_feat["atr"] = np.ones(2)
    dummy_feat["trend_short"] = np.zeros(2, dtype=np.int8)
    dummy_feat["trend_long"] = np.zeros(2, dtype=np.int8)
    dummy_feat["ema_fast_above_slow"] = np.zeros(2, dtype=np.int8)
    dummy_feat["above_regime_ema"] = np.zeros(2, dtype=np.int8)
    dummy_feat["closed_up"] = np.zeros(2, dtype=np.int8)
    return tuple(sorted(masks(dummy_feat, np.array([LONG, LONG]))))


__all__ = ["masks", "names", "PULLBACK_BANDS"]
