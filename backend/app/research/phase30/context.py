"""Phase 30 §4/§5/§6 — context vocabulary, frozen confirmation, entry quality.

Three frozen things live here, all decided before any outcome is computed:

* the **context vocabulary** (§4). It reuses Phase 24's side-aware condition set —
  trend, VWAP, EMA, volatility, expansion, pullback, room, time of day, gap — and
  adds the dimensions §4 asks for that Phase 24 did not carry: RSI regime, volume
  regime, support/resistance proximity and day of week. Nothing generates
  arbitrary combinations: the list is fixed, and §13 caps how many may be
  combined;
* the **confirmation rule** (§5). One definition, frozen: the bar after the
  pattern bar must close beyond the pattern bar's close in the trade's direction
  *and* close in the favourable half of its own range. The confirmed arm then
  fills one bar later than the unconfirmed arm, so the extra bar of hindsight is
  paid for with a worse entry rather than granted for free;
* the **entry-quality bands** (§6). IDEAL / GOOD / ACCEPTABLE / EXHAUSTED are
  computed only from information available at the decision bar. ``EXHAUSTED`` is
  not assumed to be bad — it is a label, and §19 asks what it measures.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import conditions as p24cond
from app.research.phase24.data import Series
from app.research.phase24.features import IST_OFFSET

RSI_WIN = 14
VOL_WIN = 20
SR_WIN = 20
SR_TOL_ATR = 0.25

IDEAL, GOOD, ACCEPTABLE, EXHAUSTED = "IDEAL", "GOOD", "ACCEPTABLE", "EXHAUSTED"
QUALITY_LABELS = (IDEAL, GOOD, ACCEPTABLE, EXHAUSTED)


def _rolling(a: np.ndarray, win: int) -> np.ndarray:
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


def rsi(close: np.ndarray, win: int = RSI_WIN) -> np.ndarray:
    """Wilder RSI, computed forward over the series so index ``i`` uses bars <= i."""
    n = close.size
    out = np.full(n, np.nan)
    if n <= win:
        return out
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = float(gain[1:win + 1].mean())
    avg_l = float(loss[1:win + 1].mean())
    for k in range(win, n):
        if k > win:
            avg_g = (avg_g * (win - 1) + gain[k]) / win
            avg_l = (avg_l * (win - 1) + loss[k]) / win
        if avg_l <= 0:
            out[k] = 100.0 if avg_g > 0 else 50.0
        else:
            out[k] = 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def extra_features(s: Series, feat: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """§4 dimensions Phase 24's feature set did not carry, causal by construction."""
    atr = feat["atr"]
    a = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)
    swing_hi = _rolling(s.high, SR_WIN).max(axis=1)
    swing_lo = _rolling(s.low, SR_WIN).min(axis=1)
    avg_vol = _rolling_mean(s.volume, VOL_WIN)
    with np.errstate(invalid="ignore", divide="ignore"):
        return {
            "rsi": rsi(s.close),
            "volume_ratio": np.where(avg_vol > 0, s.volume / avg_vol, np.nan),
            "resistance_dist_atr": (swing_hi - s.close) / a,
            "support_dist_atr": (s.close - swing_lo) / a,
            # 0 = Monday, IST calendar day. 1970-01-01 was a Thursday.
            "weekday": (((s.ts + IST_OFFSET) // 86_400) + 3) % 7,
        }


def condition_masks(feat: dict, side: np.ndarray) -> dict[str, np.ndarray]:
    """The whole §4 vocabulary evaluated once, over candidate rows."""
    masks = p24cond.masks(feat, side)

    def finite(a: np.ndarray) -> np.ndarray:
        return np.isfinite(a)

    rsi_v = feat["rsi"]
    masks["rsi_above_60"] = finite(rsi_v) & (rsi_v >= 60.0)
    masks["rsi_below_40"] = finite(rsi_v) & (rsi_v <= 40.0)
    masks["rsi_midrange_40_60"] = finite(rsi_v) & (rsi_v > 40.0) & (rsi_v < 60.0)
    # Side-aware: RSI stretched in the trade's own direction is a different claim
    # from RSI stretched against it, and one name for both would be untestable.
    masks["rsi_stretched_with_side"] = np.where(
        side > 0, finite(rsi_v) & (rsi_v >= 70.0), finite(rsi_v) & (rsi_v <= 30.0)
    )
    masks["rsi_stretched_against_side"] = np.where(
        side > 0, finite(rsi_v) & (rsi_v <= 30.0), finite(rsi_v) & (rsi_v >= 70.0)
    )

    vr = feat["volume_ratio"]
    masks["volume_above_1p5x"] = finite(vr) & (vr >= 1.5)
    masks["volume_below_0p7x"] = finite(vr) & (vr <= 0.7)

    # Support/resistance proximity, side-aware: for a long, "at resistance" is
    # the level in the way and "at support" is the level behind it.
    rd, sd = feat["resistance_dist_atr"], feat["support_dist_atr"]
    ahead = np.where(side > 0, rd, sd)
    behind = np.where(side > 0, sd, rd)
    masks["level_in_the_way_0p25atr"] = finite(ahead) & (ahead <= SR_TOL_ATR)
    masks["level_behind_0p25atr"] = finite(behind) & (behind <= SR_TOL_ATR)
    masks["clear_of_level_1atr"] = finite(ahead) & (ahead >= 1.0)

    wd = feat["weekday"]
    for k, name in enumerate(("mon", "tue", "wed", "thu", "fri")):
        masks[f"day_{name}"] = wd == k
    return {k: np.asarray(v, dtype=bool) for k, v in masks.items()}


def confirmation(s: Series, idx: np.ndarray, side: np.ndarray) -> np.ndarray:
    """§5 — frozen next-candle confirmation for the bar after ``idx``.

    Confirmed means the next bar both closed beyond the pattern bar's close in
    the trade's direction and closed in the favourable half of its own range. The
    confirmed arm's fill is one bar later, so this costs an entry rather than
    granting a free look at the future.
    """
    n = s.close.size
    nxt = np.minimum(idx + 1, n - 1)
    beyond = np.where(side > 0, s.close[nxt] > s.close[idx], s.close[nxt] < s.close[idx])
    rng = s.high[nxt] - s.low[nxt]
    with np.errstate(invalid="ignore", divide="ignore"):
        pos = np.where(rng > 0, (s.close[nxt] - s.low[nxt]) / rng, np.nan)
    strong = np.where(side > 0, pos >= 0.5, pos <= 0.5)
    return np.asarray(beyond & strong & (idx + 1 < n), dtype=bool)


def entry_quality(feat: dict, side: np.ndarray) -> np.ndarray:
    """§6 — one label per candidate from decision-time information only.

    The bands are frozen here. ``EXHAUSTED`` is assigned on extension, not on any
    belief about whether extension is bad; §19 answers that from the outcomes.
    """
    room = np.where(side > 0, feat["swing_high_dist_atr"], feat["swing_low_dist_atr"])
    vwap = np.abs(feat["vwap_dist_atr"])
    ext = np.where(np.isfinite(feat["momentum_atr"]), side * feat["momentum_atr"], np.nan)
    labels = np.full(side.size, ACCEPTABLE, dtype=object)

    stretched = (
        (np.isfinite(vwap) & (vwap >= 2.0))
        | (np.isfinite(ext) & (ext >= 2.0))
        | (np.isfinite(room) & (room <= 0.5))
    )
    ideal = (
        np.isfinite(room) & (room >= 2.0)
        & np.isfinite(vwap) & (vwap <= 1.0)
        & ~stretched
    )
    good = (
        np.isfinite(room) & (room >= 1.5)
        & np.isfinite(vwap) & (vwap <= 1.5)
        & ~stretched & ~ideal
    )
    labels[stretched] = EXHAUSTED
    labels[good & ~stretched] = GOOD
    labels[ideal & ~stretched] = IDEAL
    return labels
