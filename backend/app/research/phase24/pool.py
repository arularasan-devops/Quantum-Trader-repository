"""Phase 24 §3/§13 — the broad candidate pool.

The pool is deliberately *not* filtered for a strategy. Every eligible bar
produces a LONG candidate and a SHORT candidate, so discovery has to earn the
direction from the features rather than inherit it from how the pool was built.
That also means the pool's own base rate is close to a coin flip: any edge found
later is measured against that, not against nothing.

Eligibility is only about whether a trade could have been placed and resolved:
features formed, ATR positive, and enough of the session left to reach a
conclusion. Nothing about quality enters here.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data, features, outcomes

# A candidate every minute would count the same setup ~30 times and make every
# sample size a fiction. One per STRIDE minutes keeps the pool broad while
# leaving the cohorts close to independent.
STRIDE = 5

# Enough of the session must remain for the bounded hold to conclude, otherwise
# the row is a forced close rather than a resolved outcome.
MIN_BARS_LEFT = 30

# Warm-up: the longest feature window plus the pullback lookback.
WARMUP_BARS = features.SLOW_VOL_WIN + features.PULLBACK_LOOKBACK

FEATURE_KEYS = (
    "trend_1m", "trend_5m", "trend_15m", "ema_stack", "vwap_side",
    "vwap_dist_atr", "momentum_atr", "acceleration_atr", "vol_expansion",
    "candle_expansion", "swing_high_dist_atr", "swing_low_dist_atr",
    "opening_range_side", "prev_day_side", "gap_pct", "expansion_atr",
    "pullback_depth_pct", "pullback_bars", "continuation", "exhaustion",
    "minute_of_day", "atr",
)


class Pool:
    """Candidates for one instrument: features, side and resolved outcome."""

    __slots__ = ("instrument", "feat", "idx", "side", "out", "ts", "session", "sessions")

    def __len__(self) -> int:
        return int(self.idx.size)


def _session_last_bar(sess: np.ndarray) -> np.ndarray:
    """Index of the final bar of each bar's own session."""
    n = sess.size
    out = np.empty(n, dtype=np.int64)
    start = 0
    for k in range(1, n + 1):
        if k == n or sess[k] != sess[start]:
            out[start:k] = k - 1
            start = k
    return out


def build(instrument: str, **resolve_kw) -> Pool | None:
    """Build and resolve the candidate pool for one instrument.

    ``resolve_kw`` is forwarded to :func:`outcomes.resolve` so §9's robustness
    grid can re-resolve the *same* candidates under worse assumptions instead of
    generating a new pool that would not be comparable.
    """
    series = data.load_series(instrument)
    if series is None or len(series) < WARMUP_BARS + MIN_BARS_LEFT:
        return None
    feat = features.build(series)
    sess = feat["session"]
    last_bar = _session_last_bar(sess)

    n = len(series)
    eligible = np.zeros(n, dtype=bool)
    eligible[WARMUP_BARS::STRIDE] = True
    eligible &= np.isfinite(feat["atr"]) & (feat["atr"] > 0)
    eligible &= np.isfinite(feat["expansion_atr"])
    eligible &= (last_bar - np.arange(n)) >= MIN_BARS_LEFT
    # A bar must also be far enough into its own session for the session-anchored
    # features (opening range, VWAP) to mean anything.
    base = np.arange(n)
    eligible &= _bars_into_session(sess) >= features.OPENING_MINUTES + 5

    idx = base[eligible]
    if idx.size == 0:
        return None

    # Both directions for every eligible bar: the pool must not assume a side.
    idx2 = np.concatenate((idx, idx))
    side = np.concatenate((
        np.full(idx.size, outcomes.LONG, dtype=np.int8),
        np.full(idx.size, outcomes.SHORT, dtype=np.int8),
    ))

    out = outcomes.resolve(
        instrument,
        series.high, series.low, series.open, series.ts,
        idx2, side, feat["atr"], last_bar[idx2],
        **resolve_kw,
    )
    p = Pool()
    p.instrument = instrument
    p.feat = {k: np.asarray(feat[k])[idx2] for k in FEATURE_KEYS}
    p.idx = idx2
    p.side = side
    p.out = out
    p.ts = series.ts[idx2]
    p.session = sess[idx2]
    p.sessions = int(np.unique(sess[idx]).size)
    return p


def _bars_into_session(sess: np.ndarray) -> np.ndarray:
    """How many bars have elapsed in each bar's own session."""
    n = sess.size
    out = np.empty(n, dtype=np.int64)
    start = 0
    for k in range(1, n + 1):
        if k == n or sess[k] != sess[start]:
            out[start:k] = np.arange(k - start)
            start = k
    return out


def summary(p: Pool) -> dict:
    """Pool-level counts, including the base rate any discovery must beat."""
    o = p.out
    res = o.resolved
    total = int(res.sum())
    if total == 0:
        return {"instrument": p.instrument, "candidates": 0}
    t1 = float(o.t1_before_sl[res].mean() * 100.0)
    return {
        "instrument": p.instrument,
        "candidates": int(len(p)),
        "resolved": total,
        "unresolved": int((~res).sum()),
        "sessions": p.sessions,
        "long": int((p.side == outcomes.LONG).sum()),
        "short": int((p.side == outcomes.SHORT).sum()),
        "base_t1_before_sl_pct": round(t1, 2),
        "base_avg_net_r": round(float(o.net_r[res].mean()), 4),
        "base_timeout_pct": round(
            float((o.outcome[res] == outcomes.TIMEOUT).mean() * 100.0), 2
        ),
        "first_ts": int(p.ts.min()),
        "last_ts": int(p.ts.max()),
    }
