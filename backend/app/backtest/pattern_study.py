"""Pattern → next-move hit-rate study (read-only / offline).

For every 1-minute candle in the historical data, this runs the SAME
``app.analysis.structure.candle_pattern`` recogniser the live engine uses, then
measures what price actually did over the next few candles. It answers the
honest version of "can we predict the next move from a pattern?":

  * next-candle follow-through  — did the very next candle close in the
    pattern's expected direction? (tests the "will it go green/red next?" idea)
  * forward continuation        — over the next H candles, how often did price
    move at least `target` points in the expected direction (MFE), and what was
    the average signed move.

Every number is compared against the UNCONDITIONAL baseline (all bars), so we
can see whether a pattern carries any edge over random, and split by whether the
pattern agrees with the higher-timeframe (EMA) trend.

No orders, no live-engine changes. Underlying moves are real/measured.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

import numpy as np

from app.analysis.structure import candle_pattern

# Expected directional lean of each recognised pattern. +1 bullish, -1 bearish.
# DOJI / INSIDE_BAR are indecision/continuation → excluded from directional test.
_BULL = {
    "MORNING_STAR", "THREE_WHITE_SOLDIERS", "BULLISH_ENGULFING", "PIERCING",
    "BULLISH_HARAMI", "TWEEZER_BOTTOM", "HAMMER", "MARUBOZU_UP",
}
_BEAR = {
    "EVENING_STAR", "THREE_BLACK_CROWS", "BEARISH_ENGULFING", "DARK_CLOUD_COVER",
    "BEARISH_HARAMI", "TWEEZER_TOP", "SHOOTING_STAR", "MARUBOZU_DOWN",
}


def _dir(name: str) -> int:
    if name in _BULL:
        return 1
    if name in _BEAR:
        return -1
    return 0


def load_closes_ohlc(path: str) -> dict:
    o, h, lo, c = [], [], [], []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            o.append(d["open"])
            h.append(d["high"])
            lo.append(d["low"])
            c.append(d["close"])
    return {
        "open": np.array(o, dtype=float),
        "high": np.array(h, dtype=float),
        "low": np.array(lo, dtype=float),
        "close": np.array(c, dtype=float),
    }


@dataclass
class _Acc:
    n: int = 0
    next_follow: int = 0            # next candle closed in expected dir
    hit_target: int = 0            # reached +target pts (MFE) within horizon
    fwd: list[float] = field(default_factory=list)   # signed move at horizon

    def add(self, follow: bool, hit: bool, fwd_signed: float) -> None:
        self.n += 1
        self.next_follow += int(follow)
        self.hit_target += int(hit)
        self.fwd.append(fwd_signed)

    def summary(self) -> dict:
        if self.n == 0:
            return {"n": 0}
        return {
            "n": self.n,
            "next_follow_pct": round(self.next_follow / self.n * 100, 1),
            "hit_target_pct": round(self.hit_target / self.n * 100, 1),
            "avg_fwd_pts": round(statistics.mean(self.fwd), 2),
            "median_fwd_pts": round(statistics.median(self.fwd), 2),
        }


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values, dtype=float)
    acc = values[0]
    for i, v in enumerate(values):
        acc = alpha * v + (1 - alpha) * acc
        out[i] = acc
    return out


def run(
    path: str,
    *,
    horizon: int = 10,
    target: float = 5.0,
    window: int = 5,
    ema_fast: int = 20,
    ema_slow: int = 60,
) -> dict:
    data = load_closes_ohlc(path)
    o, h, lo, c = data["open"], data["high"], data["low"], data["close"]
    n = len(c)
    ema_f = _ema(c, ema_fast)
    ema_s = _ema(c, ema_slow)

    per_pattern: dict[str, _Acc] = {}
    per_pattern_trend: dict[str, _Acc] = {}   # pattern agrees with EMA trend
    per_pattern_counter: dict[str, _Acc] = {}  # pattern against EMA trend
    baseline = _Acc()

    start = max(window, ema_slow)
    for i in range(start, n - horizon):
        j = i - 2
        name = candle_pattern(o[j : i + 1], h[j : i + 1], lo[j : i + 1], c[j : i + 1])
        d = _dir(name)

        # baseline: unconditional forward move framed as "up" (+1)
        b_next = c[i + 1] > c[i]
        b_mfe = float(np.max(h[i + 1 : i + 1 + horizon]) - c[i])
        b_fwd = float(c[i + horizon] - c[i])
        baseline.add(b_next, b_mfe >= target, b_fwd)

        if d == 0:
            continue

        # forward outcome signed by the pattern's expected direction
        next_follow = (c[i + 1] - c[i]) * d > 0
        seg_h = h[i + 1 : i + 1 + horizon]
        seg_l = lo[i + 1 : i + 1 + horizon]
        # maximum favourable excursion in the expected direction
        if d > 0:
            mfe = float(np.max(seg_h) - c[i])
        else:
            mfe = float(c[i] - np.min(seg_l))
        fwd_signed = float((c[i + horizon] - c[i]) * d)

        per_pattern.setdefault(name, _Acc()).add(next_follow, mfe >= target, fwd_signed)

        trend_up = ema_f[i] > ema_s[i]
        agrees = (d > 0 and trend_up) or (d < 0 and not trend_up)
        bucket = per_pattern_trend if agrees else per_pattern_counter
        bucket.setdefault(name, _Acc()).add(next_follow, mfe >= target, fwd_signed)

    return {
        "path": path,
        "bars": n,
        "params": {"horizon": horizon, "target": target, "ema_fast": ema_fast, "ema_slow": ema_slow},
        "baseline": baseline.summary(),
        "patterns": {k: v.summary() for k, v in sorted(per_pattern.items())},
        "patterns_with_trend": {k: v.summary() for k, v in sorted(per_pattern_trend.items())},
        "patterns_against_trend": {k: v.summary() for k, v in sorted(per_pattern_counter.items())},
    }
