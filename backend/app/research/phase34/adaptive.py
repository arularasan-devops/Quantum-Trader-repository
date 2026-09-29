"""Phase 34 §2 — adaptive exits against fixed holds, on the five-year data.

Phase 33 tested only *fixed* holds (15/30/60/120 minutes) and found the median
favourable peak at minute 43-72 with giveback starting immediately. That leaves one
question genuinely open, and it is the one the chart raises: a rule that *rides* a
leg and hands back a known slice may keep more of it than any clock can.

Four exit families, all frozen before any result was computed:

``FIXED``
    Close at a fixed horizon. Phase 33's arm, repeated here so the comparison is
    like for like on the same rows.
``TRAIL_ATR``
    Exit when price falls ``k`` ATR below the highest point reached since entry.
``TRAIL_GIVEBACK``
    Exit when the trade has handed back ``g`` percent of its best look.
``FLIP``
    Exit when the entry condition's own trend sign reverses — the chart's arrows.

Rules that matter for honesty, all of them costly to the result:

* every entry the rule produces is counted, including the whipsaws. A trailing exit
  looks wonderful when only the winners are counted, and that is the whole trap in
  reading a chart.
* the exit is evaluated bar by bar on the *low* for a long (the high for a short),
  so a trail is hit the moment the bar could have hit it, not at the close.
* within one bar, if both the trail level and a new peak are possible, the trail is
  taken. A one-minute bar cannot order them and the optimistic reading is what
  manufactures an edge.
* a trail that the bar *opened* beyond is filled at that open, not at the level. On
  a premium series the gap through a trail is common and paying the level would be
  free money the market never offered.
* a position is closed at the session's last bar. No overnight, because the
  overnight gap is a different study.
* cost is charged once per round trip from the same model every other phase uses.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data
from app.research.phase31.excursion import LONG, _prepare
from app.research.phase32.reach import cost_pct

FIXED = "FIXED"
TRAIL_ATR = "TRAIL_ATR"
TRAIL_GIVEBACK = "TRAIL_GIVEBACK"
FLIP = "FLIP"

# Frozen exit grids.
FIXED_HORIZONS = (15, 30, 60, 120)
ATR_MULTIPLES = (1.0, 1.5, 2.0, 3.0)
GIVEBACK_PCT = (25.0, 33.0, 50.0)

ATR_PERIOD = 14
MAX_BARS = 375  # one index session; a trail is never carried overnight


def atr(s: data.Series, period: int = ATR_PERIOD) -> np.ndarray:
    """Wilder-style ATR in price units, computed causally.

    ``atr[i]`` uses bars up to and including ``i``, so a decision on bar ``i`` may
    use it. The first ``period`` values are NaN rather than a seeded guess.
    """
    high, low, close = s.high, s.low, s.close
    prev_close = np.concatenate(([np.nan], close[:-1]))
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
    )
    out = np.full(len(s), np.nan)
    if len(s) <= period:
        return out
    run = float(np.nanmean(tr[1 : period + 1]))
    out[period] = run
    for i in range(period + 1, len(s)):
        t = tr[i]
        if not np.isfinite(t):
            t = run
        run = (run * (period - 1) + t) / period
        out[i] = run
    return out


def resolve(
    s: data.Series,
    side: int,
    entries: np.ndarray,
    *,
    kind: str,
    param: float,
    cost: np.ndarray,
    flip_sign: np.ndarray | None = None,
) -> dict:
    """Walk every entry forward under one exit rule and return its economics.

    Returns net percentage returns per trade, not aggregates, so the caller can
    split them chronologically without re-running the walk.
    """
    sess, _ = _prepare(s)
    sgn = 1.0 if side == LONG else -1.0
    a = atr(s)
    open_, high, low, close = s.open, s.high, s.low, s.close
    idx = np.flatnonzero(entries)

    rets: list[float] = []
    holds: list[int] = []
    peaks: list[float] = []
    reasons: list[str] = []
    ts: list[int] = []
    fills: list[float] = []

    for i in idx:
        fill_i = i + 1
        if fill_i >= len(s) or sess[fill_i] != sess[i]:
            continue
        entry = float(open_[fill_i])
        if not np.isfinite(entry) or entry <= 0:
            continue
        band = float(a[i]) if kind == TRAIL_ATR else np.nan
        if kind == TRAIL_ATR and not np.isfinite(band):
            continue
        best = -np.inf          # best favourable excursion, percent
        exit_pct = None
        reason = ""
        held = 0
        last = fill_i           # last bar inside the entry's own session
        for j in range(fill_i, min(fill_i + MAX_BARS, len(s))):
            if sess[j] != sess[fill_i]:
                break
            last = j
            held = j - fill_i + 1
            fav_extreme = float(high[j] if side == LONG else low[j])
            adv_extreme = float(low[j] if side == LONG else high[j])
            fav_pct = 100.0 * sgn * (fav_extreme - entry) / entry
            adv_pct = 100.0 * sgn * (adv_extreme - entry) / entry
            open_pct = 100.0 * sgn * (float(open_[j]) - entry) / entry

            # The adverse side of the bar is taken first: within a bar the trail
            # level and a new peak cannot be ordered, and taking the peak first
            # would let the trail ratchet on a bar that had already stopped out.
            if kind == TRAIL_ATR and best > -np.inf:
                stop_pct = best - 100.0 * band * param / entry
                if adv_pct <= stop_pct:
                    exit_pct = min(stop_pct, open_pct)
                    reason = "TRAIL_HIT"
                    break
            if kind == TRAIL_GIVEBACK and best > 0.0:
                stop_pct = best * (1.0 - param / 100.0)
                if adv_pct <= stop_pct:
                    exit_pct = min(stop_pct, open_pct)
                    reason = "TRAIL_HIT"
                    break
            best = max(best, fav_pct)

            if kind == FLIP and flip_sign is not None:
                if flip_sign[j] * sgn < 0:
                    exit_pct = 100.0 * sgn * (float(close[j]) - entry) / entry
                    reason = "FLIP"
                    break
            if kind == FIXED and held >= int(param):
                exit_pct = 100.0 * sgn * (float(close[j]) - entry) / entry
                reason = "HORIZON"
                break
        if exit_pct is None:
            # ``last`` and not ``j``: the loop breaks *on* the first bar of the next
            # session, and closing there would book an overnight gap the rule never
            # held. That is a separate study and it is not this one.
            exit_pct = 100.0 * sgn * (float(close[last]) - entry) / entry
            reason = "SESSION_CLOSE"
        c = float(cost[i]) if np.isfinite(cost[i]) else np.nan
        rets.append(exit_pct - c)
        holds.append(held)
        peaks.append(float(best) if np.isfinite(best) else np.nan)
        reasons.append(reason)
        ts.append(int(s.ts[i]))
        # The fill price of the trades that actually resolved, in the same order as
        # the returns: an arm can skip an entry (no ATR yet, no bar left in the
        # session), so a caller pricing charges must not re-derive it from the
        # signal array and pair a charge with the wrong trade.
        fills.append(entry)

    return {
        "kind": kind,
        "param": param,
        "n": len(rets),
        "net_pct": np.asarray(rets, dtype=np.float64),
        "hold_minutes": np.asarray(holds, dtype=np.int32),
        "peak_pct": np.asarray(peaks, dtype=np.float64),
        "reasons": reasons,
        "ts": np.asarray(ts, dtype=np.int64),
        "entry_price": np.asarray(fills, dtype=np.float64),
    }


def arms() -> list[tuple[str, float]]:
    """The frozen exit grid as (kind, param) pairs. Counted for multiple testing."""
    out: list[tuple[str, float]] = [(FIXED, float(h)) for h in FIXED_HORIZONS]
    out += [(TRAIL_ATR, float(k)) for k in ATR_MULTIPLES]
    out += [(TRAIL_GIVEBACK, float(g)) for g in GIVEBACK_PCT]
    out.append((FLIP, 0.0))
    return out


def costs_for(instrument: str, s: data.Series, multiple: float = 1.0) -> np.ndarray:
    entry = np.concatenate((s.open[1:], [np.nan]))
    return cost_pct(instrument, entry) * float(multiple)
