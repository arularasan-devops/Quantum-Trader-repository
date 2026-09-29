"""Phase 27 §2 — the same condition vocabulary, named in bars instead of minutes.

The vocabulary is Phase 24's, unchanged and un-extended. That is deliberate on
two counts: the number of conditions *is* the number of hypotheses, so a wider
list at a new timeframe would make the multiple-testing correction harder for no
stated reason; and holding the vocabulary fixed is what makes "the 15-minute
result differs from the 1-minute result" a statement about the timeframe rather
than about a different search.

Only the *names* change. ``trend_5m_agrees`` on 15-minute bars is a five-**bar**
trend, which is seventy-five minutes, and calling it ``trend_5m`` in a
15-minute report would be a lie a reader could not catch. The renamed keys carry
their minute equivalents in :func:`window_labels`.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import conditions as p24conditions
from app.research.phase24 import features

# Phase 24 name -> bar-honest Phase 27 name. Every other condition already reads
# in units that survive a change of timeframe (an ATR multiple, a percentage, a
# clock-time bucket), so it keeps its name and can be compared row for row.
RENAME = {
    "trend_5m_agrees": "trend_5bar_agrees",
    "trend_15m_agrees": "trend_15bar_agrees",
    "trend_5m_and_15m_agree": "trend_5bar_and_15bar_agree",
    "trend_15m_opposes": "trend_15bar_opposes",
}

# Conditions whose window is a bar count, for the report's window table.
BAR_WINDOWS = {
    "trend_5bar_agrees": features.TREND_5M,
    "trend_15bar_agrees": features.TREND_15M,
    "trend_5bar_and_15bar_agree": features.TREND_15M,
    "trend_15bar_opposes": features.TREND_15M,
    "momentum_agrees_0p5atr": features.MOM_WIN,
    "momentum_against": features.MOM_WIN,
    "accelerating_with_side": features.MOM_WIN,
    "volatility_expanding": features.SLOW_VOL_WIN,
    "volatility_compressed": features.SLOW_VOL_WIN,
    "candle_expansion_1p5x": features.RANGE_WIN,
    "room_1p5atr_ahead": features.SWING_WIN,
    "room_3atr_ahead": features.SWING_WIN,
    "expansion_leg_2atr": features.PULLBACK_LOOKBACK,
    "expansion_leg_4atr": features.PULLBACK_LOOKBACK,
}


def names() -> list[str]:
    """The vocabulary, in Phase 27 naming."""
    return sorted(RENAME.get(n, n) for n in p24conditions.build())


def masks(feat: dict, side: np.ndarray, timeframe: int) -> dict[str, np.ndarray]:
    """Evaluate the whole vocabulary once, keyed by the bar-honest names.

    ``timeframe`` is not used in the arithmetic — the predicates read features
    that were already computed on this timeframe's bars — it is part of the
    signature so a caller cannot evaluate one timeframe's masks against another
    timeframe's pool without saying so.
    """
    if int(timeframe) <= 0:
        raise ValueError("timeframe must be positive")
    return {
        RENAME.get(name, name): m
        for name, m in p24conditions.masks(feat, side).items()
    }


def window_labels(timeframe: int) -> dict[str, str]:
    """What each bar-count window means in clock time at this timeframe."""
    tf = int(timeframe)
    return {
        name: f"{win} bars = {win * tf} minutes"
        for name, win in sorted(BAR_WINDOWS.items())
    }
