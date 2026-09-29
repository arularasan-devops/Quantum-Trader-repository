"""Phase 25 §4 — the condition vocabulary: market context plus real economics.

The market-context half is Phase 24's vocabulary, imported unchanged, so
"trend agrees" or "pullback 20-40%" means the same thing in both studies and a
reader does not need a translation table. The side passed in is the *underlying*
view the option expresses: a CE candidate is a LONG view, a PE candidate a SHORT
one, so one vocabulary covers both without a mirrored second list.

The half added here is the part the five-year study could never test: the
executable economics of the contract that was actually quoted — measured spread,
break-even hurdle, premium level, moneyness, delta and leg liquidity. Bands are
absolute and round, and are fixed here before any outcome is seen. Nothing is
normalised against a statistic of the whole window, because a per-instrument
median computed over the full capture would leak the holdout into the
development features.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import conditions as p24conditions

# Bands fixed before any result. The hurdle brackets deliberately match the
# 3% / 5% brackets the Phase 23 shadow runs, so the two studies can be read
# against each other — this one does not decide any live threshold.
SPREAD_BANDS = (1.0, 2.0, 5.0)
HURDLE_BANDS = (3.0, 5.0)
PREMIUM_FLOOR = 20.0
PREMIUM_RICH = 100.0
DELTA_BANDS = ((0.0, 0.35), (0.35, 0.55), (0.55, 1.01))


def _finite(a: np.ndarray) -> np.ndarray:
    return np.isfinite(a)


def option_conditions() -> dict:
    """Name -> predicate over (features, side), for the contract's economics."""
    c: dict = {}

    for band in SPREAD_BANDS:
        c[f"spread_le_{band:g}pct"] = (
            lambda f, s, b=band: _finite(f["spread_pct"]) & (f["spread_pct"] <= b)
        )
    c["spread_gt_5pct"] = lambda f, s: (
        _finite(f["spread_pct"]) & (f["spread_pct"] > 5.0)
    )

    for band in HURDLE_BANDS:
        c[f"hurdle_le_{band:g}pct"] = (
            lambda f, s, b=band: _finite(f["hurdle_pct"]) & (f["hurdle_pct"] <= b)
        )
    c["hurdle_3_to_5pct"] = lambda f, s: (
        _finite(f["hurdle_pct"]) & (f["hurdle_pct"] > 3.0) & (f["hurdle_pct"] <= 5.0)
    )
    c["hurdle_gt_5pct"] = lambda f, s: (
        _finite(f["hurdle_pct"]) & (f["hurdle_pct"] > 5.0)
    )

    c["premium_ge_20"] = lambda f, s: (
        _finite(f["premium_ask"]) & (f["premium_ask"] >= PREMIUM_FLOOR)
    )
    c["premium_lt_20"] = lambda f, s: (
        _finite(f["premium_ask"]) & (f["premium_ask"] < PREMIUM_FLOOR)
    )
    c["premium_ge_100"] = lambda f, s: (
        _finite(f["premium_ask"]) & (f["premium_ask"] >= PREMIUM_RICH)
    )

    # Moneyness is side-aware: a CE above spot is out of the money, a PE above
    # spot is in the money.
    otm = lambda f, s: np.where(  # noqa: E731 - paired with itm below, read together
        s > 0, f["moneyness_pct"] > 0.15, f["moneyness_pct"] < -0.15
    )
    c["leg_out_of_the_money"] = lambda f, s: _finite(f["moneyness_pct"]) & otm(f, s)
    c["leg_in_the_money"] = lambda f, s: _finite(f["moneyness_pct"]) & ~otm(f, s)
    c["leg_within_0p25pct_of_spot"] = lambda f, s: (
        _finite(f["moneyness_pct"]) & (np.abs(f["moneyness_pct"]) <= 0.25)
    )

    for lo, hi in DELTA_BANDS:
        c[f"delta_{int(lo * 100)}_{int(hi * 100)}"] = (
            lambda f, s, lo=lo, hi=hi: (
                _finite(f["abs_delta"]) & (f["abs_delta"] >= lo) & (f["abs_delta"] < hi)
            )
        )

    c["leg_volume_ge_1000"] = lambda f, s: (
        _finite(f["leg_volume"]) & (f["leg_volume"] >= 1_000)
    )
    c["leg_oi_ge_10000"] = lambda f, s: (
        _finite(f["leg_oi"]) & (f["leg_oi"] >= 10_000)
    )
    return c


def build() -> dict:
    """The whole vocabulary: Phase 24's market context plus option economics."""
    c = dict(p24conditions.build())
    c.update(option_conditions())
    return c


def masks(feat: dict, side: np.ndarray) -> dict[str, np.ndarray]:
    """Evaluate the vocabulary once; discovery then only ANDs booleans."""
    return {
        name: np.asarray(fn(feat, side), dtype=bool)
        for name, fn in build().items()
    }
