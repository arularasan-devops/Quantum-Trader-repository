"""Phase 30 §2/§3 — the frozen candle vocabulary and candle measurements.

Every definition here is mathematical, computed from bars ``<= i`` only, and
frozen: :data:`FINGERPRINT` is derived from the source of this module, so a run
that edited a definition after seeing an outcome cannot present itself as this
study. The smoke test recomputes the detections on a truncated series and checks
the prefix is unchanged, because a pattern that peeks one bar forward is silent
and would invalidate everything downstream.

Thresholds are round numbers on purpose (0.1, 0.25, 0.5, 2.0 …). They are not
tuned — a threshold fitted to the data before the split is a fitted parameter
whatever it is called, and §13 caps the search deliberately.

Each pattern declares the side it is *tested* on:

* ``LONG`` / ``SHORT`` for the directional reversal and rejection shapes;
* ``BOTH`` for the neutral structure shapes (inside bar, doji, NR4 …), which
  carry no direction of their own and are therefore evaluated as two separate
  counted hypotheses rather than being handed a direction by the researcher.
"""
from __future__ import annotations

import hashlib
import os

import numpy as np

from app.research.phase24.data import Series

LONG, SHORT, BOTH = 1, -1, 0

# Shape thresholds, frozen.
DOJI_BODY = 0.10          # body as a fraction of range
SMALL_BODY = 0.35
STRONG_BODY = 0.50
MARUBOZU_BODY = 0.90
LONG_WICK = 0.60          # wick as a fraction of range
TINY_WICK = 0.10
WICK_TO_BODY = 2.00
SPINNING_WICK = 0.30
EXPANSION_MULT = 2.00     # range vs its own 20-bar average
PRIOR_LEG_BARS = 5        # how far back "the move before this bar" is measured
PRIOR_LEG_ATR = 0.50      # and how big it must be, in ATR
TWEEZER_TOL_ATR = 0.10
LEVEL_WIN = 20            # breakout/retest reference window
RETEST_TOL_ATR = 0.25
BREAKOUT_LOOKBACK = 5
EXTREME_TOL_ATR = 0.25    # "at" a 20-bar extreme, for rejection wicks
RANGE_WIN = 20

# Pattern name -> side under test. The order is fixed so the hypothesis count and
# the fingerprint are reproducible.
SIDES: dict[str, int] = {
    # Bullish / reversal
    "HAMMER": LONG,
    "INVERTED_HAMMER": LONG,
    "BULLISH_ENGULFING": LONG,
    "PIERCING_LINE": LONG,
    "MORNING_STAR": LONG,
    "THREE_WHITE_SOLDIERS": LONG,
    "TWEEZER_BOTTOM": LONG,
    "BULLISH_HARAMI": LONG,
    "DRAGONFLY_DOJI": LONG,
    # Bearish / reversal
    "SHOOTING_STAR": SHORT,
    "HANGING_MAN": SHORT,
    "BEARISH_ENGULFING": SHORT,
    "DARK_CLOUD_COVER": SHORT,
    "EVENING_STAR": SHORT,
    "THREE_BLACK_CROWS": SHORT,
    "TWEEZER_TOP": SHORT,
    "BEARISH_HARAMI": SHORT,
    "GRAVESTONE_DOJI": SHORT,
    # Continuation / structure
    "INSIDE_BAR": BOTH,
    "OUTSIDE_BAR": BOTH,
    "MARUBOZU": BOTH,
    "DOJI": BOTH,
    "SPINNING_TOP": BOTH,
    "RANGE_EXPANSION": BOTH,
    "NR4": BOTH,
    "NR7": BOTH,
    "ENGULFING_AFTER_PULLBACK": BOTH,
    "BREAKOUT_RETEST": BOTH,
    "FAILED_BREAKOUT": BOTH,
    "REJECTION_WICK": BOTH,
}

NAMES: tuple[str, ...] = tuple(SIDES)


def fingerprint() -> str:
    """Hash of this module's source — the frozen definition set's identity."""
    with open(os.path.abspath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


FINGERPRINT = fingerprint()


def _shift(a: np.ndarray, k: int, fill: float = np.nan) -> np.ndarray:
    """``a`` moved ``k`` bars into the future, i.e. value of bar ``i-k`` at ``i``.

    Front-filled with ``fill`` so the first bars are never scored against a
    wrapped-around value, which is what :func:`numpy.roll` would do.
    """
    out = np.full(a.size, fill, dtype=np.float64)
    if k < a.size:
        out[k:] = a[: a.size - k]
    return out


def _rolling(a: np.ndarray, win: int) -> np.ndarray:
    """Trailing windows of length ``win`` ending at each index, front-padded."""
    pad = np.full(win - 1, a[0] if a.size else np.nan, dtype=np.float64)
    return np.lib.stride_tricks.sliding_window_view(
        np.concatenate((pad, a.astype(np.float64))), win
    )


def _rolling_mean(a: np.ndarray, win: int) -> np.ndarray:
    out = np.full(a.size, np.nan)
    if a.size < win:
        return out
    csum = np.cumsum(np.insert(a.astype(np.float64), 0, 0.0))
    out[win - 1:] = (csum[win:] - csum[:-win]) / float(win)
    return out


def measure(s: Series, atr: np.ndarray) -> dict[str, np.ndarray]:
    """§3 — every per-candle measurement, from bars ``<= i`` only."""
    o, h, lo, c = s.open, s.high, s.low, s.close
    rng = h - lo
    body = np.abs(c - o)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - lo
    safe_rng = np.where(rng > 0, rng, np.nan)
    safe_body = np.where(body > 0, body, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return {
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "body": body,
            "range": rng,
            "upper_wick": upper,
            "lower_wick": lower,
            "body_over_range": body / safe_rng,
            "upper_over_body": upper / safe_body,
            "lower_over_body": lower / safe_body,
            "upper_over_range": upper / safe_rng,
            "lower_over_range": lower / safe_rng,
            "range_atr": rng / np.where(atr > 0, atr, np.nan),
            "avg_range_20": _rolling_mean(rng, RANGE_WIN),
            "volume": s.volume,
            "avg_volume_20": _rolling_mean(s.volume, RANGE_WIN),
            "close_position": (c - lo) / safe_rng,
            "range_position_20": (
                (c - _rolling(lo, RANGE_WIN).min(axis=1))
                / np.where(
                    (_rolling(h, RANGE_WIN).max(axis=1)
                     - _rolling(lo, RANGE_WIN).min(axis=1)) > 0,
                    _rolling(h, RANGE_WIN).max(axis=1)
                    - _rolling(lo, RANGE_WIN).min(axis=1),
                    np.nan,
                )
            ),
            "bullish": (c > o),
            "bearish": (c < o),
        }


def detect(
    s: Series, atr: np.ndarray, m: dict[str, np.ndarray] | None = None
) -> dict[str, np.ndarray]:
    """Boolean occurrence array per pattern name, aligned to the series.

    A pattern is flagged on the bar that *completes* it. The decision is taken on
    that bar's close; the fill happens later (see :mod:`context`), so nothing
    here needs a future bar and nothing here may look at one.
    """
    m = m if m is not None else measure(s, atr)
    o, h, lo, c = s.open, s.high, s.low, s.close
    body, rng = m["body"], m["range"]
    upper, lower = m["upper_wick"], m["lower_wick"]
    bor = m["body_over_range"]
    bull, bear = m["bullish"], m["bearish"]
    a = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)

    p_o, p_h, p_l, p_c = _shift(o, 1), _shift(h, 1), _shift(lo, 1), _shift(c, 1)
    p_body, p_rng = _shift(body, 1), _shift(rng, 1)
    p_bull, p_bear = p_c > p_o, p_c < p_o
    p2_o, p2_c = _shift(o, 2), _shift(c, 2)
    p2_body, p2_rng = _shift(body, 2), _shift(rng, 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        p_bor = np.where(p_rng > 0, p_body / p_rng, np.nan)
        p2_bor = np.where(p2_rng > 0, p2_body / p2_rng, np.nan)

    # "The move into this bar", used by the reversal shapes. A hammer inside a
    # flat drift is a different event from a hammer after a real decline, and the
    # distinction belongs in the definition rather than in a later search.
    prior = (c - _shift(c, PRIOR_LEG_BARS)) / a
    leg_down = prior <= -PRIOR_LEG_ATR
    leg_up = prior >= PRIOR_LEG_ATR

    with np.errstate(invalid="ignore", divide="ignore"):
        d: dict[str, np.ndarray] = {}

        # ---- Bullish / reversal -------------------------------------------
        d["HAMMER"] = (
            (bor <= SMALL_BODY)
            & (lower >= WICK_TO_BODY * body)
            & (upper <= TINY_WICK * rng)
            & leg_down
        )
        d["INVERTED_HAMMER"] = (
            (bor <= SMALL_BODY)
            & (upper >= WICK_TO_BODY * body)
            & (lower <= TINY_WICK * rng)
            & leg_down
        )
        d["BULLISH_ENGULFING"] = (
            p_bear & bull & (c > p_o) & (o < p_c) & (p_bor >= DOJI_BODY)
        )
        d["PIERCING_LINE"] = (
            p_bear & bull & (o < p_c)
            & (c >= p_c + 0.5 * p_body) & (c < p_o)
            & (p_bor >= STRONG_BODY)
        )
        d["MORNING_STAR"] = (
            (p2_c < p2_o) & (p2_bor >= STRONG_BODY)
            & (p_bor <= SMALL_BODY)
            & (np.maximum(p_o, p_c) < p2_c)
            & bull & (c > p2_c + 0.5 * p2_body)
        )
        d["THREE_WHITE_SOLDIERS"] = (
            bull & p_bull & (p2_c > p2_o)
            & (c > p_c) & (p_c > p2_c)
            & (bor >= STRONG_BODY) & (p_bor >= STRONG_BODY) & (p2_bor >= STRONG_BODY)
        )
        d["TWEEZER_BOTTOM"] = (
            p_bear & bull & (np.abs(lo - p_l) <= TWEEZER_TOL_ATR * a) & leg_down
        )
        d["BULLISH_HARAMI"] = (
            p_bear & bull & (p_bor >= STRONG_BODY)
            & (np.maximum(o, c) <= p_o) & (np.minimum(o, c) >= p_c)
        )
        d["DRAGONFLY_DOJI"] = (
            (bor <= DOJI_BODY) & (lower >= LONG_WICK * rng) & (upper <= TINY_WICK * rng)
        )

        # ---- Bearish / reversal -------------------------------------------
        d["SHOOTING_STAR"] = (
            (bor <= SMALL_BODY)
            & (upper >= WICK_TO_BODY * body)
            & (lower <= TINY_WICK * rng)
            & leg_up
        )
        d["HANGING_MAN"] = (
            (bor <= SMALL_BODY)
            & (lower >= WICK_TO_BODY * body)
            & (upper <= TINY_WICK * rng)
            & leg_up
        )
        d["BEARISH_ENGULFING"] = (
            p_bull & bear & (c < p_o) & (o > p_c) & (p_bor >= DOJI_BODY)
        )
        d["DARK_CLOUD_COVER"] = (
            p_bull & bear & (o > p_c)
            & (c <= p_c - 0.5 * p_body) & (c > p_o)
            & (p_bor >= STRONG_BODY)
        )
        d["EVENING_STAR"] = (
            (p2_c > p2_o) & (p2_bor >= STRONG_BODY)
            & (p_bor <= SMALL_BODY)
            & (np.minimum(p_o, p_c) > p2_c)
            & bear & (c < p2_c - 0.5 * p2_body)
        )
        d["THREE_BLACK_CROWS"] = (
            bear & p_bear & (p2_c < p2_o)
            & (c < p_c) & (p_c < p2_c)
            & (bor >= STRONG_BODY) & (p_bor >= STRONG_BODY) & (p2_bor >= STRONG_BODY)
        )
        d["TWEEZER_TOP"] = (
            p_bull & bear & (np.abs(h - p_h) <= TWEEZER_TOL_ATR * a) & leg_up
        )
        d["BEARISH_HARAMI"] = (
            p_bull & bear & (p_bor >= STRONG_BODY)
            & (np.maximum(o, c) <= p_c) & (np.minimum(o, c) >= p_o)
        )
        d["GRAVESTONE_DOJI"] = (
            (bor <= DOJI_BODY) & (upper >= LONG_WICK * rng) & (lower <= TINY_WICK * rng)
        )

        # ---- Continuation / structure -------------------------------------
        d["INSIDE_BAR"] = (h <= p_h) & (lo >= p_l) & (rng > 0)
        d["OUTSIDE_BAR"] = (h > p_h) & (lo < p_l)
        d["MARUBOZU"] = bor >= MARUBOZU_BODY
        d["DOJI"] = (bor <= DOJI_BODY) & (rng > 0)
        d["SPINNING_TOP"] = (
            (bor <= SPINNING_WICK)
            & (upper >= SPINNING_WICK * rng)
            & (lower >= SPINNING_WICK * rng)
        )
        d["RANGE_EXPANSION"] = rng >= EXPANSION_MULT * m["avg_range_20"]

        # NR4 / NR7: the narrowest range of the last 4 / 7 bars, ties excluded so
        # a flat-lined series does not manufacture thousands of occurrences.
        for name, win in (("NR4", 4), ("NR7", 7)):
            w = _rolling(rng, win)
            d[name] = (rng == w.min(axis=1)) & (rng < w[:, :-1].min(axis=1))

        # A bullish or bearish engulfing that appears *after* a real pullback in
        # an existing leg, rather than in the middle of nowhere.
        prior_hi = _rolling(h, LEVEL_WIN).max(axis=1)
        prior_lo = _rolling(lo, LEVEL_WIN).min(axis=1)
        leg = prior_hi - prior_lo
        pulled_back_long = (leg > 0) & ((prior_hi - p_c) / np.where(leg > 0, leg, np.nan) >= 0.2)
        pulled_back_short = (leg > 0) & ((p_c - prior_lo) / np.where(leg > 0, leg, np.nan) >= 0.2)
        d["ENGULFING_AFTER_PULLBACK"] = (
            (d["BULLISH_ENGULFING"] & pulled_back_long)
            | (d["BEARISH_ENGULFING"] & pulled_back_short)
        )

        # Breakout / retest / failure, measured against a level that was already
        # in place *before* the breakout: the rolling extreme as of the bar
        # before the lookback window, so the level is not defined by the move
        # that broke it.
        level_hi = _shift(_rolling(h, LEVEL_WIN).max(axis=1), BREAKOUT_LOOKBACK)
        level_lo = _shift(_rolling(lo, LEVEL_WIN).min(axis=1), BREAKOUT_LOOKBACK)
        broke_up = np.zeros(c.size, dtype=bool)
        broke_dn = np.zeros(c.size, dtype=bool)
        for k in range(1, BREAKOUT_LOOKBACK + 1):
            broke_up |= _shift(c, k) > level_hi
            broke_dn |= _shift(c, k) < level_lo
        d["BREAKOUT_RETEST"] = (
            (broke_up & (np.abs(lo - level_hi) <= RETEST_TOL_ATR * a) & (c > level_hi))
            | (broke_dn & (np.abs(h - level_lo) <= RETEST_TOL_ATR * a) & (c < level_lo))
        )
        d["FAILED_BREAKOUT"] = (
            ((h > level_hi) & (c < level_hi) & bear)
            | ((lo < level_lo) & (c > level_lo) & bull)
        )
        at_high = np.abs(prior_hi - h) <= EXTREME_TOL_ATR * a
        at_low = np.abs(lo - prior_lo) <= EXTREME_TOL_ATR * a
        d["REJECTION_WICK"] = (
            ((upper >= 0.5 * rng) & (bor <= SPINNING_WICK) & at_high)
            | ((lower >= 0.5 * rng) & (bor <= SPINNING_WICK) & at_low)
        )

    warm = max(RANGE_WIN, LEVEL_WIN) + BREAKOUT_LOOKBACK + PRIOR_LEG_BARS
    for name in NAMES:
        arr = np.asarray(d[name])
        arr = np.where(np.isfinite(rng) & (rng > 0), arr, False)
        # Front bars have synthetic windows; they are dropped, not measured.
        arr[:warm] = False
        d[name] = arr.astype(bool)
    return {k: d[k] for k in NAMES}


def occurrence_counts(d: dict[str, np.ndarray]) -> dict[str, int]:
    return {name: int(d[name].sum()) for name in NAMES}
