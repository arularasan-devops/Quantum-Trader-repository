"""§4 — the mechanism families, as decision-time functions.

Every admission function in this module takes a :class:`Window` and returns a
:class:`Decision` or ``None``. The window is a **slice ending at the decision
bar**, constructed by the caller, so a mechanism cannot reach a later bar even
if it tries: the array it holds does not contain one. That is the structural
half of §16, and it is worth more than any amount of review, because look-ahead
is almost never deliberate — it arrives as an innocent-looking index.

The static half lives in :mod:`app.research.opportunity.guard`, which parses
these functions and refuses names that can only mean the future (``mfe``,
``mae``, ``giveback``, ``outcome``, ``exit_price``). Both halves are asserted by
the smoke.

The families are finite on purpose. Free composition of indicators is a search
space big enough to fit any five-year series, and a rule fitted to a space that
large tells you about the space, not the market. What varies inside a family is
a small declared parameter grid, and every combination generated is counted as
a hypothesis in the false-discovery accounting — including the ones that lose,
which is the only thing that makes the accounting mean anything.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.research.opportunity import (
    FAMILY_COST,
    FAMILY_MOMENTUM,
    FAMILY_REGIME,
    FAMILY_RELATIVE,
)

LONG = "LONG"
SHORT = "SHORT"

# Round-trip cost as a percentage of entry, modelled because candle history
# carries no book. It is the default for the cost family so a candidate
# generated without one is still costed rather than costed at zero.
MODELLED_ROUND_TRIP_PCT = 0.06


@dataclass(frozen=True)
class Window:
    """Bars up to and including the decision bar. Nothing after it exists here.

    Built by :func:`window_at`, which slices the series. A mechanism that wants
    tomorrow's high has nowhere to get it from, which is the point: the
    guarantee is in the data structure rather than in the discipline of whoever
    writes the next mechanism.
    """

    instrument: str
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    @property
    def price(self) -> float:
        """The decision-bar close — the last price that had actually printed."""
        return float(self.close[-1])

    @property
    def decision_ts(self) -> int:
        return int(self.ts[-1])

    def __len__(self) -> int:
        return int(self.ts.size)


@dataclass(frozen=True)
class Decision:
    """One admission. ``direction`` and ``why`` are both required.

    ``why`` carries the measured quantities the rule fired on, so a journal row
    can be re-checked later against the bars rather than taken on trust.
    """

    direction: str
    why: dict


def window_at(series, i: int, lookback: int) -> Window | None:
    """A window ending at bar ``i``. ``None`` when there is not enough past.

    Refusing rather than padding matters: a zero-padded lookback makes the
    first bars of a series look like a violent move away from nothing, and
    those phantom signals cluster at session opens where they are easiest to
    mistake for an opening-range effect.
    """
    lo = i - lookback + 1
    if lo < 0 or i >= len(series):
        return None
    hi = i + 1
    return Window(
        instrument=series.instrument,
        ts=series.ts[lo:hi], open=series.open[lo:hi], high=series.high[lo:hi],
        low=series.low[lo:hi], close=series.close[lo:hi],
        volume=series.volume[lo:hi],
    )


def true_range(w: Window) -> float:
    """Mean bar range over the window, in price. Zero-safe."""
    rng = w.high - w.low
    return float(np.mean(rng)) if rng.size else 0.0


def atr_pct(w: Window) -> float:
    """Mean bar range as a percentage of the decision price."""
    px = w.price
    if px <= 0:
        return 0.0
    return 100.0 * true_range(w) / px


# ---------------------------------------------------------------------------
# A. DIRECTIONAL MOMENTUM
# ---------------------------------------------------------------------------
def breakout(w: Window, *, lookback: int, buffer_atr: float) -> Decision | None:
    """Close beyond the prior extreme of the window by a fraction of its range.

    The prior extreme deliberately excludes the decision bar. Including it
    makes the test "is this bar's close near this bar's high", which fires
    constantly and measures nothing about a level being taken out.
    """
    if len(w) < 3:
        return None
    prior_high = float(np.max(w.high[:-1]))
    prior_low = float(np.min(w.low[:-1]))
    tr = true_range(w)
    if tr <= 0:
        return None
    pad = buffer_atr * tr
    px = w.price
    if px > prior_high + pad:
        return Decision(LONG, {"level": prior_high, "pad": pad, "price": px,
                               "mechanism": "BREAKOUT"})
    if px < prior_low - pad:
        return Decision(SHORT, {"level": prior_low, "pad": pad, "price": px,
                                "mechanism": "BREAKOUT"})
    return None


def momentum_persistence(w: Window, *, lookback: int,
                         min_run: int) -> Decision | None:
    """A run of same-signed bar closes, with the run measured, not assumed."""
    if len(w) < min_run + 1:
        return None
    diffs = np.diff(w.close[-(min_run + 1):])
    if np.all(diffs > 0):
        return Decision(LONG, {"run": int(min_run), "mechanism": "PERSISTENCE"})
    if np.all(diffs < 0):
        return Decision(SHORT, {"run": int(min_run), "mechanism": "PERSISTENCE"})
    return None


def pullback_continuation(w: Window, *, lookback: int,
                          retrace: float) -> Decision | None:
    """A retracement into an established trend, sized against the window swing.

    The trend is measured on the first two thirds of the window and the
    retracement on the last third, so the two are not the same bars — testing a
    pullback on the bars that define the trend is circular and always fires.
    """
    n = len(w)
    if n < lookback:
        return None
    cut = (2 * n) // 3
    trend_leg = w.close[:cut]
    if trend_leg.size < 3:
        return None
    swing_hi = float(np.max(w.high[:cut]))
    swing_lo = float(np.min(w.low[:cut]))
    span = swing_hi - swing_lo
    if span <= 0:
        return None
    up = float(trend_leg[-1]) > float(trend_leg[0])
    px = w.price
    if up:
        depth = (swing_hi - px) / span
        if retrace <= depth <= retrace + 0.25:
            return Decision(LONG, {"depth": depth, "swing": span,
                                   "mechanism": "PULLBACK"})
        return None
    depth = (px - swing_lo) / span
    if retrace <= depth <= retrace + 0.25:
        return Decision(SHORT, {"depth": depth, "swing": span,
                                "mechanism": "PULLBACK"})
    return None


def reversal(w: Window, *, lookback: int, extreme_atr: float) -> Decision | None:
    """Rejection of a window extreme: the bar prints the extreme and closes back.

    Uses only the decision bar's own high, low and close, all of which had
    printed by the decision instant.
    """
    if len(w) < lookback:
        return None
    tr = true_range(w)
    if tr <= 0:
        return None
    hi, lo, close = float(w.high[-1]), float(w.low[-1]), w.price
    prior_high = w.high[:-1]
    prior_low = w.low[:-1]
    if prior_high.size == 0 or prior_low.size == 0:
        return None
    at_top = hi >= float(np.max(prior_high))
    at_bottom = lo <= float(np.min(prior_low))
    if at_top and (hi - close) >= extreme_atr * tr:
        return Decision(SHORT, {"rejection": hi - close, "mechanism": "REVERSAL"})
    if at_bottom and (close - lo) >= extreme_atr * tr:
        return Decision(LONG, {"rejection": close - lo, "mechanism": "REVERSAL"})
    return None


# ---------------------------------------------------------------------------
# B. OPPORTUNITY VS COST
# ---------------------------------------------------------------------------
def movement_vs_cost(w: Window, *, lookback: int, multiple: float,
                     cost_pct: float = MODELLED_ROUND_TRIP_PCT) -> Decision | None:
    """Admit only when the *typical* move is a stated multiple of round-trip cost.

    The expected move is the window's mean bar range, which is a measured
    quantity and not a forecast. Direction comes from the window's own drift,
    because a cost filter is not a direction rule and pretending otherwise is
    how a cost gate ends up taking credit for a momentum effect.
    """
    if len(w) < 3 or cost_pct <= 0:
        return None
    move = atr_pct(w)
    if move < multiple * cost_pct:
        return None
    drift = float(w.close[-1]) - float(w.close[0])
    if drift == 0:
        return None
    return Decision(LONG if drift > 0 else SHORT,
                    {"expected_move_pct": move, "cost_pct": cost_pct,
                     "move_over_cost": move / cost_pct,
                     "mechanism": "MOVE_OVER_COST"})


def range_expansion(w: Window, *, lookback: int, ratio: float) -> Decision | None:
    """The decision bar's range against the window's average range."""
    if len(w) < 3:
        return None
    base = float(np.mean((w.high - w.low)[:-1]))
    if base <= 0:
        return None
    cur = float(w.high[-1] - w.low[-1])
    if cur < ratio * base:
        return None
    close, low, high = w.price, float(w.low[-1]), float(w.high[-1])
    if high == low:
        return None
    position = (close - low) / (high - low)
    if position >= 0.7:
        return Decision(LONG, {"expansion": cur / base, "close_position": position,
                               "mechanism": "RANGE_EXPANSION"})
    if position <= 0.3:
        return Decision(SHORT, {"expansion": cur / base, "close_position": position,
                                "mechanism": "RANGE_EXPANSION"})
    return None


# ---------------------------------------------------------------------------
# C. REGIME / STATE
# ---------------------------------------------------------------------------
def volatility_expansion(w: Window, *, lookback: int,
                         ratio: float) -> Decision | None:
    """Recent range against earlier range in the same window, direction from drift."""
    n = len(w)
    if n < lookback or n < 6:
        return None
    half = n // 2
    early = float(np.mean((w.high - w.low)[:half]))
    late = float(np.mean((w.high - w.low)[half:]))
    if early <= 0 or late < ratio * early:
        return None
    drift = float(w.close[-1]) - float(w.close[half])
    if drift == 0:
        return None
    return Decision(LONG if drift > 0 else SHORT,
                    {"vol_ratio": late / early, "mechanism": "VOL_EXPANSION"})


def session_position(w: Window, *, lookback: int,
                     minutes_in: int) -> Decision | None:
    """Time-of-day state: the opening range, resolved after a declared delay.

    The delay is counted in bars from the session's first bar in the window, so
    a candidate cannot smuggle in a look at the rest of the session — the
    window ends at the decision bar regardless.
    """
    if len(w) < minutes_in + 2:
        # The opening range must be a strict subset of the window, or the rule
        # is comparing the range against itself and fires on every bar.
        return None
    day = w.ts // 86_400
    if int(day[-1]) != int(day[0]):
        return None  # window straddles two sessions; the opening range is unclear
    opening = w.close[:minutes_in]
    if opening.size < 2:
        return None
    hi, lo = float(np.max(w.high[:minutes_in])), float(np.min(w.low[:minutes_in]))
    if hi <= lo:
        return None
    px = w.price
    if px > hi:
        return Decision(LONG, {"opening_high": hi, "minutes_in": minutes_in,
                               "mechanism": "OPENING_RANGE"})
    if px < lo:
        return Decision(SHORT, {"opening_low": lo, "minutes_in": minutes_in,
                                "mechanism": "OPENING_RANGE"})
    return None


# ---------------------------------------------------------------------------
# D. RELATIVE VALUE — two instruments at the same decision instant.
# ---------------------------------------------------------------------------
def relative_divergence(w: Window, other: Window, *, lookback: int,
                        z_threshold: float) -> Decision | None:
    """One leg's return against the other's over the same bars.

    Requires the two windows to end on the same timestamp. A pair measured at
    two different instants is not a pair, and that mistake is invisible in the
    output — so it is refused here rather than reported.
    """
    if other is None or len(w) < lookback or len(other) < lookback:
        return None
    if w.decision_ts != other.decision_ts:
        return None
    a = w.close
    b = other.close
    n = min(a.size, b.size)
    if n < lookback:
        return None
    ra = np.diff(np.log(np.maximum(a[-n:], 1e-9)))
    rb = np.diff(np.log(np.maximum(b[-n:], 1e-9)))
    spread = ra - rb
    if spread.size < 5:
        return None
    sd = float(np.std(spread))
    if sd <= 0:
        return None
    z = float(spread[-1]) / sd
    if abs(z) < z_threshold:
        return None
    # Divergence, traded as convergence on the leg that moved.
    return Decision(SHORT if z > 0 else LONG,
                    {"z": z, "against": other.instrument,
                     "mechanism": "RELATIVE_DIVERGENCE"})


# ---------------------------------------------------------------------------
# The declared parameter grid. Small, and every combination is a counted
# hypothesis — which is why it is small.
# ---------------------------------------------------------------------------
GRID: dict[str, dict] = {
    "breakout": {
        "family": FAMILY_MOMENTUM, "fn": breakout,
        "params": {"lookback": (15, 30, 60), "buffer_atr": (0.25, 0.5)},
    },
    "momentum_persistence": {
        "family": FAMILY_MOMENTUM, "fn": momentum_persistence,
        "params": {"lookback": (15, 30), "min_run": (3, 5)},
    },
    "pullback_continuation": {
        "family": FAMILY_MOMENTUM, "fn": pullback_continuation,
        "params": {"lookback": (30, 60), "retrace": (0.25, 0.5)},
    },
    "reversal": {
        "family": FAMILY_MOMENTUM, "fn": reversal,
        "params": {"lookback": (30, 60), "extreme_atr": (0.5, 1.0)},
    },
    "movement_vs_cost": {
        "family": FAMILY_COST, "fn": movement_vs_cost,
        "params": {"lookback": (30, 60), "multiple": (3.0, 4.0, 8.0)},
    },
    "range_expansion": {
        "family": FAMILY_COST, "fn": range_expansion,
        "params": {"lookback": (30, 60), "ratio": (1.5, 2.5)},
    },
    "volatility_expansion": {
        "family": FAMILY_REGIME, "fn": volatility_expansion,
        "params": {"lookback": (30, 60), "ratio": (1.5, 2.0)},
    },
    "session_position": {
        "family": FAMILY_REGIME, "fn": session_position,
        "params": {"lookback": (45, 60), "minutes_in": (15, 30)},
    },
    "relative_divergence": {
        "family": FAMILY_RELATIVE, "fn": relative_divergence,
        "params": {"lookback": (30, 60), "z_threshold": (2.0, 3.0)},
    },
}

# Mechanisms needing a second leg, so the generator can scope them to pairs and
# the screen knows to load two series rather than reporting a phantom absence.
PAIRED: frozenset[str] = frozenset({"relative_divergence"})

# Every admission function, for the look-ahead guard and the freeze.
ADMISSION_FUNCTIONS = tuple(spec["fn"] for spec in GRID.values())
