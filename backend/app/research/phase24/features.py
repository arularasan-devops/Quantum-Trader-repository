"""Phase 24 §2/§3 — entry-timestamp features, computed backwards only.

Every array returned here is causal: the value at index ``i`` uses bars ``<= i``
and nothing later. That property is not a comment, it is asserted in the smoke
test by recomputing the features on a truncated series and checking the prefix is
unchanged — the one bug class that would make this whole study worthless is a
feature that peeks, and it is silent unless it is tested for.

Session boundaries are IST calendar days. Session-anchored features (VWAP, the
opening range, the previous day's levels, the gap) reset at the boundary instead
of running across it, because a trader at 09:16 has no access to yesterday's
intraday path as a continuous series.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24.data import Series

IST_OFFSET = 19_800  # +05:30 in seconds

# Windows are round numbers deliberately: a window tuned to the data is a
# parameter fitted before the split, and §10 asks for the opposite.
ATR_WIN = 14
SLOW_VOL_WIN = 60
RANGE_WIN = 20
SWING_WIN = 20
MOM_WIN = 5
TREND_5M = 5
TREND_15M = 15
OPENING_MINUTES = 15
PULLBACK_LOOKBACK = 30

UP, DOWN, FLAT = 1, -1, 0


def _sessions(ts: np.ndarray) -> np.ndarray:
    """IST calendar day index per bar."""
    return (ts + IST_OFFSET) // 86_400


def _minutes_into_day(ts: np.ndarray) -> np.ndarray:
    return ((ts + IST_OFFSET) % 86_400) // 60


def _windows(a: np.ndarray, win: int) -> np.ndarray:
    """Trailing windows of length ``win`` ending at each index, front-padded.

    A view, not a copy: the pullback measurement needs an argmax over a 30-bar
    window at every one of ~1.4M bars, and materialising those windows would cost
    more memory than the study is worth.
    """
    pad = np.full(win - 1, a[0] if a.size else np.nan, dtype=np.float64)
    return np.lib.stride_tricks.sliding_window_view(
        np.concatenate((pad, a.astype(np.float64))), win
    )


def _rolling_max(a: np.ndarray, win: int) -> np.ndarray:
    """Trailing maximum over ``win`` bars ending at each index (inclusive)."""
    if a.size == 0:
        return np.array([], dtype=np.float64)
    return _windows(a, win).max(axis=1)


def _rolling_min(a: np.ndarray, win: int) -> np.ndarray:
    if a.size == 0:
        return np.array([], dtype=np.float64)
    return _windows(a, win).min(axis=1)


def _rolling_mean(a: np.ndarray, win: int) -> np.ndarray:
    """Trailing mean; the first ``win-1`` values are NaN rather than a short mean,
    so a candidate is never scored on a half-formed window."""
    out = np.full(a.size, np.nan)
    if a.size < win:
        return out
    csum = np.cumsum(np.insert(a, 0, 0.0))
    out[win - 1:] = (csum[win:] - csum[:-win]) / float(win)
    return out


def _ema(a: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    out = np.empty(a.size)
    if a.size == 0:
        return out
    out[0] = a[0]
    for k in range(1, a.size):
        out[k] = alpha * a[k] + (1.0 - alpha) * out[k - 1]
    return out


def _true_range(s: Series) -> np.ndarray:
    prev_close = np.concatenate(([s.close[0]], s.close[:-1]))
    return np.maximum.reduce([
        s.high - s.low,
        np.abs(s.high - prev_close),
        np.abs(s.low - prev_close),
    ])


def _session_vwap(s: Series, sess: np.ndarray) -> np.ndarray:
    """Session-anchored VWAP; falls back to a running typical-price mean when the
    feed reports no volume, which the MCX series does for some sessions."""
    typical = (s.high + s.low + s.close) / 3.0
    out = np.empty(s.close.size)
    start = 0
    for k in range(s.close.size + 1):
        if k == s.close.size or (k > 0 and sess[k] != sess[start]):
            seg = slice(start, k)
            vol = s.volume[seg]
            tp = typical[seg]
            cv = np.cumsum(vol)
            running = np.cumsum(tp) / (np.arange(tp.size) + 1)
            if cv[-1] > 0:
                weighted = np.cumsum(tp * vol) / np.maximum(cv, 1e-9)
                # Bars before the session's first traded volume have no VWAP yet;
                # they take the running typical mean rather than a divide by zero.
                out[seg] = np.where(cv > 0, weighted, running)
            else:
                out[seg] = running
            start = k
    return out


def _trend(close: np.ndarray, win: int, atr: np.ndarray) -> np.ndarray:
    """Trend over ``win`` bars: the close's net move scaled by ATR.

    A move smaller than a fifth of one ATR is FLAT. Using ATR rather than a fixed
    point threshold is what lets NIFTY and CRUDEOIL share one definition.
    """
    prev = np.concatenate((np.full(win, np.nan), close[:-win])) if close.size > win \
        else np.full(close.size, np.nan)
    delta = close - prev
    scale = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)
    norm = delta / scale
    out = np.full(close.size, FLAT, dtype=np.int8)
    out[norm >= 0.2] = UP
    out[norm <= -0.2] = DOWN
    out[~np.isfinite(norm)] = FLAT
    return out


def _session_levels(
    s: Series, sess: np.ndarray, mins: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Opening-range high/low, previous session high/low and the gap.

    The opening range is only published once the opening window has closed; until
    then it is NaN, because a trader at 09:20 does not know the 09:30 range.
    """
    n = s.close.size
    or_hi = np.full(n, np.nan)
    or_lo = np.full(n, np.nan)
    pd_hi = np.full(n, np.nan)
    pd_lo = np.full(n, np.nan)
    gap = np.full(n, np.nan)
    bounds: list[tuple[int, int]] = []
    start = 0
    for k in range(1, n + 1):
        if k == n or sess[k] != sess[start]:
            bounds.append((start, k))
            start = k
    prev_hi = prev_lo = prev_close = np.nan
    for lo, hi in bounds:
        seg = slice(lo, hi)
        day_mins = mins[seg]
        first = day_mins[0]
        opening = day_mins < first + OPENING_MINUTES
        if opening.any():
            o_hi = s.high[seg][opening].max()
            o_lo = s.low[seg][opening].min()
            after = ~opening
            idx = np.arange(lo, hi)
            or_hi[idx[after]] = o_hi
            or_lo[idx[after]] = o_lo
        pd_hi[seg] = prev_hi
        pd_lo[seg] = prev_lo
        if np.isfinite(prev_close) and prev_close > 0:
            gap[seg] = 100.0 * (s.open[lo] - prev_close) / prev_close
        prev_hi = s.high[seg].max()
        prev_lo = s.low[seg].min()
        prev_close = s.close[hi - 1]
    return or_hi, or_lo, pd_hi, pd_lo, gap


def _pullback(
    s: Series, atr: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Expansion size, pullback depth %, pullback bars, continuation, exhaustion.

    Measured strictly backwards over ``PULLBACK_LOOKBACK`` bars: locate the swing
    low and the running high after it (for a bullish leg), size the expansion in
    ATR, then measure how much of that leg the current close has given back.
    Nothing here asserts that a pullback is required — depth is simply a feature,
    and §3 forbids assuming it matters.
    """
    n = s.close.size
    if n == 0:
        z = np.array([], dtype=np.float64)
        return z, z, z, z.astype(np.int8), z.astype(np.int8)
    win = PULLBACK_LOOKBACK
    hw = _windows(s.high, win)
    lw = _windows(s.low, win)
    hi_i = hw.argmax(axis=1)
    lo_i = lw.argmin(axis=1)
    hi_v = hw.max(axis=1)
    lo_v = lw.min(axis=1)
    leg = hi_v - lo_v
    valid = np.isfinite(atr) & (atr > 0) & (leg > 0)
    # Front-padding makes the first bars' windows synthetic, so they are dropped
    # rather than measured.
    valid[:win] = False
    bullish = hi_i > lo_i

    with np.errstate(invalid="ignore", divide="ignore"):
        exp_atr = np.where(valid, leg / atr, np.nan)
        depth = np.where(
            valid,
            np.where(
                bullish,
                100.0 * (hi_v - s.close) / leg,
                100.0 * (s.close - lo_v) / leg,
            ),
            np.nan,
        )
    # Window index -> bar index: the window ending at k starts at k-win+1.
    start = np.arange(n) - win + 1
    bars = np.where(
        valid,
        np.where(bullish, np.arange(n) - (start + hi_i), np.arange(n) - (start + lo_i)),
        np.nan,
    ).astype(np.float64)

    prior_high = np.concatenate(([np.nan], s.high[:-1]))
    prior_low = np.concatenate(([np.nan], s.low[:-1]))
    gave_back = np.isfinite(depth) & (depth > 0.0)
    cont = np.where(
        valid & bullish & gave_back & (s.close > prior_high), 1,
        np.where(valid & ~bullish & gave_back & (s.close < prior_low), -1, 0),
    ).astype(np.int8)

    rng = s.high - s.low
    with np.errstate(invalid="ignore", divide="ignore"):
        close_pos = np.where(rng > 0, (s.close - s.low) / rng, np.nan)
    # A wide bar closing back at one extreme of its own range is the exhaustion
    # shape the user's chart showed; it is recorded as a feature, not acted on.
    exhaust = (
        valid & (rng > 1.5 * atr)
        & np.isfinite(close_pos) & ((close_pos < 0.35) | (close_pos > 0.65))
    ).astype(np.int8)
    return exp_atr, depth, bars, cont, exhaust


def build(s: Series) -> dict[str, np.ndarray]:
    """All §3 market-context and pullback features for one series."""
    sess = _sessions(s.ts)
    mins = _minutes_into_day(s.ts)
    tr = _true_range(s)
    atr = _rolling_mean(tr, ATR_WIN)
    atr_slow = _rolling_mean(tr, SLOW_VOL_WIN)
    ema9 = _ema(s.close, 9)
    ema21 = _ema(s.close, 21)
    vwap = _session_vwap(s, sess)
    rng = s.high - s.low
    avg_rng = _rolling_mean(rng, RANGE_WIN)
    swing_hi = _rolling_max(s.high, SWING_WIN)
    swing_lo = _rolling_min(s.low, SWING_WIN)
    mom = np.concatenate((np.full(MOM_WIN, np.nan), s.close[MOM_WIN:] - s.close[:-MOM_WIN]))
    prev_mom = np.concatenate((np.full(1, np.nan), mom[:-1]))
    or_hi, or_lo, pd_hi, pd_lo, gap = _session_levels(s, sess, mins)
    exp_atr, depth, pb_bars, cont, exhaust = _pullback(s, atr)
    with np.errstate(invalid="ignore", divide="ignore"):
        return {
            "ts": s.ts,
            "session": sess,
            "minute_of_day": mins,
            "close": s.close,
            "atr": atr,
            "vol_expansion": atr / atr_slow,
            "candle_expansion": rng / avg_rng,
            "trend_1m": _trend(s.close, 1, atr),
            "trend_5m": _trend(s.close, TREND_5M, atr),
            "trend_15m": _trend(s.close, TREND_15M, atr),
            "ema_stack": np.where(
                (s.close > ema9) & (ema9 > ema21), UP,
                np.where((s.close < ema9) & (ema9 < ema21), DOWN, FLAT),
            ).astype(np.int8),
            "vwap_side": np.where(
                s.close > vwap, UP, np.where(s.close < vwap, DOWN, FLAT)
            ).astype(np.int8),
            "vwap_dist_atr": (s.close - vwap) / atr,
            "momentum_atr": mom / atr,
            "acceleration_atr": (mom - prev_mom) / atr,
            "swing_high_dist_atr": (swing_hi - s.close) / atr,
            "swing_low_dist_atr": (s.close - swing_lo) / atr,
            "opening_range_side": np.where(
                np.isfinite(or_hi) & (s.close > or_hi), UP,
                np.where(np.isfinite(or_lo) & (s.close < or_lo), DOWN, FLAT),
            ).astype(np.int8),
            "prev_day_side": np.where(
                np.isfinite(pd_hi) & (s.close > pd_hi), UP,
                np.where(np.isfinite(pd_lo) & (s.close < pd_lo), DOWN, FLAT),
            ).astype(np.int8),
            "gap_pct": gap,
            "expansion_atr": exp_atr,
            "pullback_depth_pct": depth,
            "pullback_bars": pb_bars,
            "continuation": cont,
            "exhaustion": exhaust,
        }
