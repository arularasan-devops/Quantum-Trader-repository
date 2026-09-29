"""Phase 29 §4 — the vocabulary: market context plus the seller's economics.

The market-context half is Phase 24's vocabulary, imported unchanged, so
"trend agrees" or "pullback 20-40%" means the same thing here as in every earlier
phase. The side passed in is the underlying view the structure expresses: a bull
put spread is a LONG view, a bear call spread a SHORT one.

An iron condor expresses **no** direction, so it is not given the directional
half of the vocabulary at all. Handing a range structure a "trend agrees"
condition would let discovery pick a direction for a position that has none, and
whichever way it picked would look like a finding.

The half added here is what only a seller's study can test: how much credit the
structure collected relative to the width it risked, how much of that credit the
round-trip friction eats, how far out of the money the short leg sat, and how
liquid the legs were. Bands are absolute, round, and fixed before any outcome is
seen. Nothing is normalised against a statistic of the whole window, because a
per-instrument median over the full capture leaks the holdout into development.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import conditions as p24conditions
from app.research.phase29 import legs

# Bands fixed before any result.
CREDIT_WIDTH_BANDS = ((0.0, 0.15), (0.15, 0.25), (0.25, 0.40), (0.40, 1.01))
FRICTION_BANDS = (20.0, 35.0, 50.0)
SHORT_DELTA_BANDS = ((0.0, 0.15), (0.15, 0.30), (0.30, 0.50))
CREDIT_FLOOR_POINTS = 5.0

# Phase 24 conditions that do not read the trade's direction. Listed explicitly
# rather than detected, so a future edit to Phase 24 cannot quietly hand a
# directional condition to a range structure.
NON_DIRECTIONAL = (
    "volatility_expanding",
    "volatility_compressed",
    "candle_expansion_1p5x",
    "expansion_leg_2atr",
    "expansion_leg_4atr",
    "pullback_0_20pct",
    "pullback_20_40pct",
    "pullback_40_60pct",
    "pullback_60_100pct",
    "no_continuation",
    "exhaustion_bar",
    "no_exhaustion",
    "near_vwap_1atr",
    "stretched_from_vwap_2atr",
    "time_open_0930_1030",
    "time_morning_1030_1200",
    "time_midday_1200_1400",
    "time_late_1400_close",
    "gap_day_0p5pct",
    "flat_open_day",
)


def _finite(a: np.ndarray) -> np.ndarray:
    return np.isfinite(a)


def spread_conditions() -> dict:
    """Name -> predicate over (features, side), for the structure's economics."""
    c: dict = {}

    for lo, hi in CREDIT_WIDTH_BANDS:
        c[f"credit_{int(lo * 100)}_{int(hi * 100)}pct_of_width"] = (
            lambda f, s, lo=lo, hi=hi: (
                _finite(f["credit_pct_of_width"])
                & (f["credit_pct_of_width"] >= lo * 100.0)
                & (f["credit_pct_of_width"] < hi * 100.0)
            )
        )

    for band in FRICTION_BANDS:
        c[f"friction_le_{band:g}pct_of_credit"] = (
            lambda f, s, b=band: _finite(f["friction_pct"]) & (f["friction_pct"] <= b)
        )
    c["friction_gt_50pct_of_credit"] = lambda f, s: (
        _finite(f["friction_pct"]) & (f["friction_pct"] > 50.0)
    )

    c["credit_ge_5_points"] = lambda f, s: (
        _finite(f["credit_points"]) & (f["credit_points"] >= CREDIT_FLOOR_POINTS)
    )
    c["credit_lt_5_points"] = lambda f, s: (
        _finite(f["credit_points"]) & (f["credit_points"] < CREDIT_FLOOR_POINTS)
    )

    for lo, hi in SHORT_DELTA_BANDS:
        c[f"short_delta_{int(lo * 100)}_{int(hi * 100)}"] = (
            lambda f, s, lo=lo, hi=hi: (
                _finite(f["short_abs_delta"])
                & (f["short_abs_delta"] >= lo)
                & (f["short_abs_delta"] < hi)
            )
        )

    c["short_leg_ge_1pct_otm"] = lambda f, s: (
        _finite(f["short_otm_pct"]) & (f["short_otm_pct"] >= 1.0)
    )
    c["short_leg_lt_0p5pct_otm"] = lambda f, s: (
        _finite(f["short_otm_pct"]) & (f["short_otm_pct"] < 0.5)
    )

    c["legs_liquid_oi_ge_10000"] = lambda f, s: (
        _finite(f["short_oi"]) & (f["short_oi"] >= 10_000)
    )
    c["legs_traded_volume_ge_1000"] = lambda f, s: (
        _finite(f["short_volume"]) & (f["short_volume"] >= 1_000)
    )
    c["iv_ge_15"] = lambda f, s: _finite(f["short_iv"]) & (f["short_iv"] >= 15.0)
    c["iv_lt_15"] = lambda f, s: _finite(f["short_iv"]) & (f["short_iv"] < 15.0)
    return c


def build(structure: str) -> dict:
    """The vocabulary available to one structure.

    A directional vertical gets Phase 24's full context plus the seller's
    economics. A range structure gets only the non-directional context plus the
    same economics — it has no direction to agree with.
    """
    context = dict(p24conditions.build())
    if structure == legs.IRON_CONDOR:
        context = {k: v for k, v in context.items() if k in NON_DIRECTIONAL}
    context.update(spread_conditions())
    return context


def masks(feat: dict, side: np.ndarray, structure: str) -> dict[str, np.ndarray]:
    """Evaluate the vocabulary once; discovery then only ANDs booleans."""
    return {
        name: np.asarray(fn(feat, side), dtype=bool)
        for name, fn in build(structure).items()
    }
