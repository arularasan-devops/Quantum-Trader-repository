"""Phase 27 §2 — the higher-timeframe candidate pool.

Same construction as Phase 24, deliberately: every eligible bar produces a LONG
candidate *and* a SHORT candidate, so discovery has to earn the direction from
the features instead of inheriting it from how the pool was built, and the pool's
own base rate stays close to a coin flip for a cohort to be measured against.

What genuinely changes with the timeframe, and why:

* the **holding window and the tail rule are held constant in clock time**, not
  in bars. Three hours is three hours whether that is 180 one-minute bars, 36
  five-minute bars or 12 fifteen-minute bars. Keeping the bar count instead would
  have given the 15-minute study a 45-hour hold and quietly compared two
  different strategies;
* the **stride is one bar**, which is a candidate every 5 or 15 minutes against
  Phase 24's every-5-minutes. Coarser bars are already close to independent, and
  a finer stride would count one setup several times;
* the **feature windows stay in bars**, so ATR(14) on 15-minute bars is a
  15-minute ATR — that is the point of the study, and it is the number that makes
  the round-trip charges a smaller fraction of the risk taken;
* the session-anchored features need the opening window to have closed, which is
  one bar at 15 minutes and three at 5 minutes rather than a fixed bar count.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import features, outcomes
from app.research.phase27 import bars

# Clock-time constants, converted to bars per timeframe.
HORIZON_MINUTES = 180
MIN_MINUTES_LEFT = 30
STRIDE_BARS = 1

WARMUP_BARS = features.SLOW_VOL_WIN + features.PULLBACK_LOOKBACK

FEATURE_KEYS = (
    "trend_1m", "trend_5m", "trend_15m", "ema_stack", "vwap_side",
    "vwap_dist_atr", "momentum_atr", "acceleration_atr", "vol_expansion",
    "candle_expansion", "swing_high_dist_atr", "swing_low_dist_atr",
    "opening_range_side", "prev_day_side", "gap_pct", "expansion_atr",
    "pullback_depth_pct", "pullback_bars", "continuation", "exhaustion",
    "minute_of_day", "atr",
)


def horizon_bars(timeframe: int) -> int:
    """Bars in the three-hour bounded hold at this timeframe."""
    return max(1, HORIZON_MINUTES // int(timeframe))


def min_bars_left(timeframe: int) -> int:
    """Bars of session that must remain for an outcome to be resolvable."""
    return max(1, MIN_MINUTES_LEFT // int(timeframe))


def opening_bars(timeframe: int) -> int:
    """Bars until the opening range has closed, plus one to act on it."""
    tf = int(timeframe)
    return int(-(-features.OPENING_MINUTES // tf)) + 1


class Pool:
    """Candidates for one instrument at one timeframe."""

    __slots__ = (
        "instrument", "timeframe", "feat", "idx", "side", "out", "ts",
        "session", "sessions", "bar_stats",
    )

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


def _bars_into_session(sess: np.ndarray) -> np.ndarray:
    n = sess.size
    out = np.empty(n, dtype=np.int64)
    start = 0
    for k in range(1, n + 1):
        if k == n or sess[k] != sess[start]:
            out[start:k] = np.arange(k - start)
            start = k
    return out


def build(instrument: str, timeframe: int, **resolve_kw) -> Pool | None:
    """Build and resolve one instrument's pool at ``timeframe`` minutes.

    ``resolve_kw`` is forwarded to :func:`outcomes.resolve` so the robustness grid
    can re-resolve the *same* candidates under worse assumptions rather than
    generating a different pool that would not be comparable. ``horizon`` defaults
    to the three-hour equivalent for this timeframe and can still be overridden.
    """
    tf = int(timeframe)
    got = bars.load(instrument, tf)
    if got is None:
        return None
    series, stats = got
    left = min_bars_left(tf)
    if len(series) < WARMUP_BARS + left:
        return None
    feat = features.build(series)
    sess = feat["session"]
    last_bar = _session_last_bar(sess)

    n = len(series)
    base = np.arange(n)
    eligible = np.zeros(n, dtype=bool)
    eligible[WARMUP_BARS::STRIDE_BARS] = True
    eligible &= np.isfinite(feat["atr"]) & (feat["atr"] > 0)
    eligible &= np.isfinite(feat["expansion_atr"])
    eligible &= (last_bar - base) >= left
    eligible &= _bars_into_session(sess) >= opening_bars(tf)

    idx = base[eligible]
    if idx.size == 0:
        return None

    idx2 = np.concatenate((idx, idx))
    side = np.concatenate((
        np.full(idx.size, outcomes.LONG, dtype=np.int8),
        np.full(idx.size, outcomes.SHORT, dtype=np.int8),
    ))

    resolve_kw.setdefault("horizon", horizon_bars(tf))
    out = outcomes.resolve(
        instrument,
        series.high, series.low, series.open, series.ts,
        idx2, side, feat["atr"], last_bar[idx2],
        **resolve_kw,
    )
    p = Pool()
    p.instrument = instrument
    p.timeframe = tf
    p.feat = {k: np.asarray(feat[k])[idx2] for k in FEATURE_KEYS}
    p.idx = idx2
    p.side = side
    p.out = out
    p.ts = series.ts[idx2]
    p.session = sess[idx2]
    p.sessions = int(np.unique(sess[idx]).size)
    p.bar_stats = stats
    return p


def summary(p: Pool) -> dict:
    """Pool-level counts, including the base rate any discovery must beat."""
    o = p.out
    res = o.resolved
    total = int(res.sum())
    if total == 0:
        return {
            "instrument": p.instrument,
            "timeframe_minutes": p.timeframe,
            "candidates": 0,
        }
    return {
        "instrument": p.instrument,
        "timeframe_minutes": p.timeframe,
        "candidates": int(len(p)),
        "resolved": total,
        "unresolved": int((~res).sum()),
        "sessions": p.sessions,
        "bars": int(p.bar_stats.get("bars") or 0),
        "short_bar_pct": p.bar_stats.get("short_bar_pct"),
        "long": int((p.side == outcomes.LONG).sum()),
        "short": int((p.side == outcomes.SHORT).sum()),
        "horizon_bars": horizon_bars(p.timeframe),
        "horizon_minutes": HORIZON_MINUTES,
        "base_t1_before_sl_pct": round(float(o.t1_before_sl[res].mean() * 100.0), 2),
        "base_avg_net_r": round(float(o.net_r[res].mean()), 4),
        "base_timeout_pct": round(
            float((o.outcome[res] == outcomes.TIMEOUT).mean() * 100.0), 2
        ),
        "median_atr_points": round(float(np.median(o.risk[res])), 3),
        "first_ts": int(p.ts.min()),
        "last_ts": int(p.ts.max()),
    }
