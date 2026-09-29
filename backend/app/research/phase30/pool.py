"""Phase 30 §12 — the full candidate pool, patterns and non-patterns alike.

Two properties §12 insists on and most candle studies skip:

* the pool contains the **non-pattern control**. Bars where no frozen pattern
  fired are sampled into the same pool with the same geometry and the same costs,
  so ``PATTERN vs NON_PATTERN`` is a measured comparison rather than a claim. A
  pattern that matches its own control has no incremental value however good its
  absolute hit rate looks;
* the pool does not choose a side. Every eligible bar contributes a LONG row and
  a SHORT row. A directional pattern is then tested only on its own side, and a
  structural pattern (inside bar, doji, NR4 …) is tested on both as two counted
  hypotheses.

Rows the current production engine would have refused are included: §12 asks for
rejected opportunities as well, and excluding them would grade the pattern on a
sample the engine had already filtered.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data, features, outcomes
from app.research.phase30 import context, patterns

# One control row every CONTROL_STRIDE non-pattern bars. The control only has to
# be large enough to pin the base rate precisely; a control the size of the whole
# series would triple the study's runtime for a number that stops moving.
CONTROL_STRIDE = 37

# Enough of the session must remain for the bounded hold to resolve, otherwise the
# row is a forced close rather than an outcome.
MIN_BARS_LEFT = 30
WARMUP_BARS = features.SLOW_VOL_WIN + features.PULLBACK_LOOKBACK

NON_PATTERN = "NON_PATTERN"

FEATURE_KEYS = (
    "trend_1m", "trend_5m", "trend_15m", "ema_stack", "vwap_side",
    "vwap_dist_atr", "momentum_atr", "acceleration_atr", "vol_expansion",
    "candle_expansion", "swing_high_dist_atr", "swing_low_dist_atr",
    "opening_range_side", "prev_day_side", "gap_pct", "expansion_atr",
    "pullback_depth_pct", "pullback_bars", "continuation", "exhaustion",
    "minute_of_day", "atr",
    "rsi", "volume_ratio", "resistance_dist_atr", "support_dist_atr", "weekday",
)

CANDLE_KEYS = (
    "body_over_range", "upper_over_range", "lower_over_range", "range_atr",
    "close_position", "range_position_20",
)


class Pool:
    """One instrument at one stop band, one confirmation arm."""

    __slots__ = (
        "instrument", "stop_atr", "confirmed_arm", "idx", "side", "out",
        "feat", "candle", "quality", "pat", "confirmed", "ts", "session",
        "sessions", "bars", "series",
    )

    def __len__(self) -> int:
        return int(self.idx.size)


def _session_last_bar(sess: np.ndarray) -> np.ndarray:
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


class Prepared:
    """Series, features and pattern detections computed once per instrument.

    Kept separate from :class:`Pool` because the stop grid and the confirmation
    arms re-resolve the *same* candidates: recomputing 1.4M bars of features five
    times would dominate the study and, worse, would let the arms drift apart.
    """

    __slots__ = (
        "instrument", "series", "feat", "candle", "det", "last_bar",
        "eligible_bars", "any_pattern",
    )


def prepare(instrument: str) -> Prepared | None:
    series = data.load_series(instrument)
    if series is None or len(series) < WARMUP_BARS + MIN_BARS_LEFT:
        return None
    feat = features.build(series)
    feat.update(context.extra_features(series, feat))
    candle = patterns.measure(series, feat["atr"])
    det = patterns.detect(series, feat["atr"], candle)

    n = len(series)
    sess = feat["session"]
    last_bar = _session_last_bar(sess)
    eligible = np.zeros(n, dtype=bool)
    eligible[WARMUP_BARS:] = True
    eligible &= np.isfinite(feat["atr"]) & (feat["atr"] > 0)
    eligible &= (last_bar - np.arange(n)) >= MIN_BARS_LEFT
    eligible &= _bars_into_session(sess) >= features.OPENING_MINUTES + 5

    p = Prepared()
    p.instrument = instrument
    p.series = series
    p.feat = feat
    p.candle = candle
    p.det = det
    p.last_bar = last_bar
    p.eligible_bars = eligible
    p.any_pattern = np.zeros(n, dtype=bool)
    for name in patterns.NAMES:
        p.any_pattern |= det[name]
    return p


def build(
    prep: Prepared,
    *,
    stop_atr: float,
    confirmed_arm: bool = False,
    **resolve_kw,
) -> Pool | None:
    """Resolve every candidate row for one stop band and one confirmation arm.

    ``confirmed_arm`` moves the decision reference to the confirming bar, so the
    fill is the open *after* it. The pattern masks still point at the pattern bar,
    which is what makes the two arms comparable row by row.
    """
    series, feat, det = prep.series, prep.feat, prep.det
    n = len(series)
    bars = np.arange(n)

    control = np.zeros(n, dtype=bool)
    control[::CONTROL_STRIDE] = True
    control &= ~prep.any_pattern
    keep = prep.eligible_bars & (prep.any_pattern | control)
    base = bars[keep]
    if base.size == 0:
        return None

    idx = np.concatenate((base, base))
    side = np.concatenate((
        np.full(base.size, outcomes.LONG, dtype=np.int8),
        np.full(base.size, outcomes.SHORT, dtype=np.int8),
    ))

    confirmed = context.confirmation(series, idx, side)
    if confirmed_arm:
        # The decision reference is the confirming bar; a row whose confirming bar
        # is outside its own session's tradable window is dropped rather than
        # resolved against tomorrow.
        ref = idx + 1
        alive = (ref < n) & (prep.last_bar[np.minimum(ref, n - 1)] - ref >= 1)
        alive &= confirmed
        if not alive.any():
            return None
        idx, side, confirmed = idx[alive], side[alive], confirmed[alive]
        ref = idx + 1
    else:
        ref = idx

    out = outcomes.resolve(
        prep.instrument,
        series.high, series.low, series.open, series.ts,
        ref, side, feat["atr"], prep.last_bar[ref],
        stop_atr=stop_atr,
        **resolve_kw,
    )

    p = Pool()
    p.instrument = prep.instrument
    p.stop_atr = float(stop_atr)
    p.confirmed_arm = bool(confirmed_arm)
    p.idx = idx
    p.side = side
    p.out = out
    p.feat = {k: np.asarray(feat[k])[idx] for k in FEATURE_KEYS}
    p.candle = {k: np.asarray(prep.candle[k])[idx] for k in CANDLE_KEYS}
    p.quality = context.entry_quality(p.feat, side)
    p.pat = {}
    for name in patterns.NAMES:
        hit = np.asarray(det[name])[idx]
        want = patterns.SIDES[name]
        if want != patterns.BOTH:
            hit = hit & (side == want)
        p.pat[name] = hit
    p.pat[NON_PATTERN] = ~np.asarray(prep.any_pattern)[idx]
    p.confirmed = confirmed
    p.ts = series.ts[idx]
    p.session = feat["session"][idx]
    p.sessions = int(np.unique(p.session).size)
    p.bars = n
    p.series = series
    return p


def summary(p: Pool) -> dict:
    """Pool counts and the base rate any pattern has to beat."""
    o = p.out
    res = o.resolved
    total = int(res.sum())
    if total == 0:
        return {"instrument": p.instrument, "candidates": 0}
    control = p.pat[NON_PATTERN] & res
    return {
        "instrument": p.instrument,
        "stop_atr": p.stop_atr,
        "confirmed_arm": p.confirmed_arm,
        "candidates": int(len(p)),
        "resolved": total,
        "unresolved": int((~res).sum()),
        "sessions": p.sessions,
        "bars": p.bars,
        "long": int((p.side == outcomes.LONG).sum()),
        "short": int((p.side == outcomes.SHORT).sum()),
        "confirmed_rows": int(p.confirmed.sum()),
        "base_t1_before_sl_pct": round(100.0 * float(o.t1_before_sl[res].mean()), 2),
        "base_avg_net_r": round(float(o.net_r[res].mean()), 4),
        "control_rows": int(control.sum()),
        "control_t1_before_sl_pct": (
            round(100.0 * float(o.t1_before_sl[control].mean()), 2)
            if control.any() else None
        ),
        "control_avg_net_r": (
            round(float(o.net_r[control].mean()), 4) if control.any() else None
        ),
        "first_ts": int(p.ts.min()),
        "last_ts": int(p.ts.max()),
        "pattern_occurrences": {
            name: int((p.pat[name] & res).sum()) for name in patterns.NAMES
        },
    }
