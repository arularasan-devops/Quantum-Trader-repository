"""Phase 24 §5 — the bounded, interpretable condition vocabulary.

Every condition is side-aware: "trend agrees" means agreeing with the candidate's
own direction, so one vocabulary covers LONG and SHORT without a mirrored second
list. Conditions are boolean and named in plain words, because §6 penalises
complexity and a rule nobody can read cannot be reviewed before it trades.

The vocabulary is fixed and small on purpose. The number of conditions is the
number of hypotheses, and every extra one raises the multiple-testing correction
that any survivor has to clear.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from app.research.phase24.features import DOWN, UP

# Buckets are round and defined here, before any result is seen.
PULLBACK_BANDS = ((0.0, 20.0), (20.0, 40.0), (40.0, 60.0), (60.0, 100.0))
TIME_BUCKETS = (
    ("open_0930_1030", 570, 630),
    ("morning_1030_1200", 630, 720),
    ("midday_1200_1400", 720, 840),
    ("late_1400_close", 840, 1_440),
)

Condition = Callable[[dict, np.ndarray], np.ndarray]


def _agrees(values: np.ndarray, side: np.ndarray) -> np.ndarray:
    """A directional feature pointing the same way as the trade."""
    return np.where(side > 0, values == UP, values == DOWN)


def _opposes(values: np.ndarray, side: np.ndarray) -> np.ndarray:
    return np.where(side > 0, values == DOWN, values == UP)


def _finite(a: np.ndarray) -> np.ndarray:
    return np.isfinite(a)


def build() -> dict[str, Condition]:
    """Name -> predicate over (features, side)."""
    c: dict[str, Condition] = {}

    c["trend_5m_agrees"] = lambda f, s: _agrees(f["trend_5m"], s)
    c["trend_15m_agrees"] = lambda f, s: _agrees(f["trend_15m"], s)
    c["trend_5m_and_15m_agree"] = lambda f, s: (
        _agrees(f["trend_5m"], s) & _agrees(f["trend_15m"], s)
    )
    c["trend_15m_opposes"] = lambda f, s: _opposes(f["trend_15m"], s)
    c["ema_stack_agrees"] = lambda f, s: _agrees(f["ema_stack"], s)
    c["vwap_side_agrees"] = lambda f, s: _agrees(f["vwap_side"], s)
    c["opening_range_agrees"] = lambda f, s: _agrees(f["opening_range_side"], s)
    c["prev_day_level_agrees"] = lambda f, s: _agrees(f["prev_day_side"], s)

    c["momentum_agrees_0p5atr"] = lambda f, s: (
        _finite(f["momentum_atr"]) & (s * f["momentum_atr"] >= 0.5)
    )
    c["momentum_against"] = lambda f, s: (
        _finite(f["momentum_atr"]) & (s * f["momentum_atr"] <= -0.5)
    )
    c["accelerating_with_side"] = lambda f, s: (
        _finite(f["acceleration_atr"]) & (s * f["acceleration_atr"] > 0)
    )

    c["volatility_expanding"] = lambda f, s: (
        _finite(f["vol_expansion"]) & (f["vol_expansion"] >= 1.2)
    )
    c["volatility_compressed"] = lambda f, s: (
        _finite(f["vol_expansion"]) & (f["vol_expansion"] <= 0.8)
    )
    c["candle_expansion_1p5x"] = lambda f, s: (
        _finite(f["candle_expansion"]) & (f["candle_expansion"] >= 1.5)
    )
    c["expansion_leg_2atr"] = lambda f, s: (
        _finite(f["expansion_atr"]) & (f["expansion_atr"] >= 2.0)
    )
    c["expansion_leg_4atr"] = lambda f, s: (
        _finite(f["expansion_atr"]) & (f["expansion_atr"] >= 4.0)
    )

    for lo, hi in PULLBACK_BANDS:
        c[f"pullback_{int(lo)}_{int(hi)}pct"] = (
            lambda f, s, lo=lo, hi=hi: (
                _finite(f["pullback_depth_pct"])
                & (f["pullback_depth_pct"] >= lo)
                & (f["pullback_depth_pct"] < hi)
            )
        )
    c["continuation_confirmed"] = lambda f, s: f["continuation"] == s
    c["no_continuation"] = lambda f, s: f["continuation"] == 0
    c["exhaustion_bar"] = lambda f, s: f["exhaustion"] == 1
    c["no_exhaustion"] = lambda f, s: f["exhaustion"] == 0

    # Room: distance to the trailing swing in the trade's favour. This is the
    # "room to target" idea the earlier phases kept finding, stated as a feature
    # rather than as an assumption.
    c["room_1p5atr_ahead"] = lambda f, s: np.where(
        s > 0,
        _finite(f["swing_high_dist_atr"]) & (f["swing_high_dist_atr"] >= 1.5),
        _finite(f["swing_low_dist_atr"]) & (f["swing_low_dist_atr"] >= 1.5),
    )
    c["room_3atr_ahead"] = lambda f, s: np.where(
        s > 0,
        _finite(f["swing_high_dist_atr"]) & (f["swing_high_dist_atr"] >= 3.0),
        _finite(f["swing_low_dist_atr"]) & (f["swing_low_dist_atr"] >= 3.0),
    )
    c["near_vwap_1atr"] = lambda f, s: (
        _finite(f["vwap_dist_atr"]) & (np.abs(f["vwap_dist_atr"]) <= 1.0)
    )
    c["stretched_from_vwap_2atr"] = lambda f, s: (
        _finite(f["vwap_dist_atr"]) & (np.abs(f["vwap_dist_atr"]) >= 2.0)
    )

    for name, lo, hi in TIME_BUCKETS:
        c[f"time_{name}"] = (
            lambda f, s, lo=lo, hi=hi: (
                (f["minute_of_day"] >= lo) & (f["minute_of_day"] < hi)
            )
        )

    c["gap_day_0p5pct"] = lambda f, s: (
        _finite(f["gap_pct"]) & (np.abs(f["gap_pct"]) >= 0.5)
    )
    c["flat_open_day"] = lambda f, s: (
        _finite(f["gap_pct"]) & (np.abs(f["gap_pct"]) < 0.5)
    )
    return c


def masks(feat: dict, side: np.ndarray) -> dict[str, np.ndarray]:
    """Evaluate the whole vocabulary once; discovery then only ANDs booleans."""
    return {
        name: np.asarray(fn(feat, side), dtype=bool)
        for name, fn in build().items()
    }
