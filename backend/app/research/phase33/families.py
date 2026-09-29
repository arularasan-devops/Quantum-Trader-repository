"""Phase 33 §2 — the signal families, built from labels that already exist.

§2 asks for the engine's own setup labels rather than a new strategy vocabulary,
so nothing is invented here. Each family is a frozen combination of conditions
that Phase 24 already froze (``conditions.build``) and shapes that Phase 30
already froze (``patterns.detect``); the names are the engine's own —
``PULLBACK``, ``CONTINUATION``, ``BREAKOUT``, ``BREAKOUT_RETEST``, ``REVERSAL``,
``MOMENTUM``, ``VWAP``, ``OPENING_RANGE``.

Two deliberate choices:

* ``ALL`` is a family. It is every eligible decision instant, and it is the
  control: a family whose hold curve matches ``ALL`` has told us nothing about
  holding, only about the instrument.
* ``EXHAUSTED_ENTRY`` is a family rather than a filter, because §6 of the earlier
  phase said to measure exhaustion instead of assuming it is bad.

A family is a *cohort mask*, not a strategy: it selects which decision instants
are aggregated, and the hold-time arithmetic downstream is identical for all of
them, so two families can always be compared like for like.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import conditions, features
from app.research.phase30 import patterns
from app.research.phase31.excursion import _prepare
from app.research.phase33 import MIN_ROWS_PER_FAMILY

ALL = "ALL"

# The first hour of the instrument's *own* session. A clock-time bucket would
# empty this family on MCX, whose session opens at 09:00 rather than 09:15.
OPENING_MINUTES = 60

# Reversal shapes, taken as a group: individually each is rare enough on one
# instrument that its hold curve would be noise, and Phase 30 already reported
# them one by one.
REVERSAL_LONG = (
    "HAMMER", "INVERTED_HAMMER", "BULLISH_ENGULFING", "PIERCING_LINE",
    "MORNING_STAR", "TWEEZER_BOTTOM", "BULLISH_HARAMI", "DRAGONFLY_DOJI",
)
REVERSAL_SHORT = (
    "SHOOTING_STAR", "HANGING_MAN", "BEARISH_ENGULFING", "DARK_CLOUD_COVER",
    "EVENING_STAR", "TWEEZER_TOP", "BEARISH_HARAMI", "GRAVESTONE_DOJI",
)

# Family -> the frozen condition names it ANDs together. Empty means "no
# condition", used by ALL and by the pattern-driven families.
CONDITION_FAMILIES: dict[str, tuple[str, ...]] = {
    "PULLBACK": ("trend_15m_agrees", "pullback_20_40pct", "continuation_confirmed"),
    "CONTINUATION": ("trend_5m_and_15m_agree", "continuation_confirmed"),
    "MOMENTUM": ("momentum_agrees_0p5atr", "accelerating_with_side"),
    "VWAP": ("vwap_side_agrees", "near_vwap_1atr"),
    "OPENING_RANGE": ("opening_range_agrees",),
    "EXHAUSTED_ENTRY": ("exhaustion_bar",),
}

# Family -> the frozen pattern names it takes the union of.
PATTERN_FAMILIES: dict[str, tuple[str, ...]] = {
    "BREAKOUT": ("RANGE_EXPANSION",),
    "BREAKOUT_RETEST": ("BREAKOUT_RETEST",),
    "FAILED_BREAKOUT": ("FAILED_BREAKOUT",),
    "REVERSAL": (),  # side-dependent, filled in build()
}

NAMES: tuple[str, ...] = (
    (ALL,) + tuple(CONDITION_FAMILIES) + tuple(PATTERN_FAMILIES)
)


class Cohorts:
    """Family masks for one instrument and one side, plus the features behind them."""

    __slots__ = ("instrument", "side", "feat", "masks", "warmup")

    def __init__(self, instrument: str, side: int) -> None:
        self.instrument = instrument
        self.side = side


def build(series, side: int) -> Cohorts:
    """Every family mask for one instrument and one side.

    Everything is computed from bars at or before the decision bar; the fill and
    the outcome live in :mod:`path`, so no family can see its own result.
    """
    feat = features.build(series)
    atr = feat["atr"]
    det = patterns.detect(series, atr)
    n = len(series)
    side_arr = np.full(n, float(side))
    cond = conditions.masks(feat, side_arr)

    c = Cohorts(series.instrument, side)
    c.feat = feat
    c.warmup = np.isfinite(atr) & (atr > 0)
    masks: dict[str, np.ndarray] = {ALL: np.ones(n, dtype=bool)}
    _, into_session = _prepare(series)
    for name, keys in CONDITION_FAMILIES.items():
        m = np.ones(n, dtype=bool)
        for k in keys:
            m &= np.asarray(cond[k], dtype=bool)
        if name == "OPENING_RANGE":
            m &= into_session < OPENING_MINUTES
        masks[name] = m
    for name, pats in PATTERN_FAMILIES.items():
        if name == "REVERSAL":
            pats = REVERSAL_LONG if side > 0 else REVERSAL_SHORT
        m = np.zeros(n, dtype=bool)
        for pname in pats:
            m |= np.asarray(det[pname], dtype=bool)
        masks[name] = m
    c.masks = {k: v & c.warmup for k, v in masks.items()}
    return c


def rankable(mask: np.ndarray) -> bool:
    """A family below the sample floor is described, never ranked (§19)."""
    return int(mask.sum()) >= MIN_ROWS_PER_FAMILY


def counts(c: Cohorts, eligible: np.ndarray) -> dict[str, int]:
    return {k: int((v & eligible).sum()) for k, v in c.masks.items()}
