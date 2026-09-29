"""Volume Profile — POC / VAH / VAL (Phase 1).

Builds a price/volume histogram over the recent session and derives:
  * POC (Point of Control) — the price level with the most traded volume;
  * Value Area — the contiguous band around the POC that contains ~70% of the
    session volume, bounded by VAH (high) and VAL (low).

These are the prices the market actually accepted/rejected — high-value context
for entries (buy pullbacks to VAL support in an uptrend, fade VAH in a range)
and for regime detection (price inside the value area == balance/range).

Everything here is a transparent heuristic computed from OHLCV candles — no
tick-level data is required, so it works on the same 1-min candles we already
have.
"""
from __future__ import annotations

import numpy as np

from app.models import Candle


def volume_profile(
    candles: list[Candle],
    bins: int = 30,
    value_area_pct: float = 0.70,
) -> dict | None:
    """Return {poc, vah, val} for the given candles, or None if not enough data.

    Each candle's volume is spread uniformly across the price bins its
    high-low range spans (an even, order-independent approximation of where
    trade occurred), which is more faithful than dumping it all on the close.
    """
    if len(candles) < 12:
        return None
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    vols = np.array([max(0.0, c.volume) for c in candles], dtype=float)

    lo = float(lows.min())
    hi = float(highs.max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None
    if vols.sum() <= 0:
        # No volume info (some feeds omit it): fall back to a time histogram so
        # POC/VAH/VAL still reflect where price spent the most time.
        vols = np.ones_like(vols)

    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    hist = np.zeros(bins, dtype=float)

    bin_w = (hi - lo) / bins
    for c_hi, c_lo, vol in zip(highs, lows, vols):
        if vol <= 0:
            continue
        # bins covered by this candle's range
        lo_idx = int((c_lo - lo) / bin_w)
        hi_idx = int((c_hi - lo) / bin_w)
        lo_idx = max(0, min(bins - 1, lo_idx))
        hi_idx = max(0, min(bins - 1, hi_idx))
        span = hi_idx - lo_idx + 1
        hist[lo_idx:hi_idx + 1] += vol / span

    total = hist.sum()
    if total <= 0:
        return None

    poc_idx = int(hist.argmax())
    poc = float(centers[poc_idx])

    # Expand a window outward from the POC until it holds `value_area_pct` of
    # volume, always taking the richer adjacent bin next.
    lo_i = hi_i = poc_idx
    captured = hist[poc_idx]
    target = value_area_pct * total
    while captured < target and (lo_i > 0 or hi_i < bins - 1):
        left = hist[lo_i - 1] if lo_i > 0 else -1.0
        right = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        if right >= left:
            hi_i += 1
            captured += hist[hi_i]
        else:
            lo_i -= 1
            captured += hist[lo_i]

    val = float(edges[lo_i])
    vah = float(edges[hi_i + 1])
    return {"poc": round(poc, 1), "vah": round(vah, 1), "val": round(val, 1)}


def value_area_position(price: float, vah: float | None, val: float | None) -> str | None:
    if vah is None or val is None:
        return None
    if price > vah:
        return "ABOVE_VALUE"
    if price < val:
        return "BELOW_VALUE"
    return "INSIDE_VALUE"
