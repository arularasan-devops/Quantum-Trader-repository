"""Market-structure, support/resistance, price-action and pattern detection.

Higher-level read of the price series that the decision engine consumes:
swing points, HH/HL/LH/LL structure, S/R levels, breakout / breakdown /
retest classification, candlestick patterns and a sudden-crash detector.
"""
from __future__ import annotations

import numpy as np


def swing_points(highs: np.ndarray, lows: np.ndarray, left: int = 3, right: int = 3):
    swing_highs, swing_lows = [], []
    n = len(highs)
    for i in range(left, n - right):
        if highs[i] == max(highs[i - left:i + right + 1]):
            swing_highs.append((i, float(highs[i])))
        if lows[i] == min(lows[i - left:i + right + 1]):
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def support_resistance(highs: np.ndarray, lows: np.ndarray):
    # No candles yet (e.g. feed warming up or the historical API rate-limited):
    # return neutral levels instead of calling max()/min() on an empty array,
    # which would raise ValueError and 500 the whole snapshot.
    if len(highs) == 0 or len(lows) == 0:
        return 0.0, 0.0
    sh, sl = swing_points(highs, lows)
    resistance = sh[-1][1] if sh else float(highs.max())
    support = sl[-1][1] if sl else float(lows.min())
    return support, resistance


def market_structure(highs: np.ndarray, lows: np.ndarray) -> str:
    sh, sl = swing_points(highs, lows)
    if len(sh) < 2 or len(sl) < 2:
        return "MIXED"
    hh = sh[-1][1] > sh[-2][1]
    hl = sl[-1][1] > sl[-2][1]
    lh = sh[-1][1] < sh[-2][1]
    ll = sl[-1][1] < sl[-2][1]
    if hh and hl:
        return "HH_HL"
    if lh and ll:
        return "LH_LL"
    return "MIXED"


def trend_from_emas(ema9, ema20, ema50) -> str:
    if None in (ema9, ema20, ema50):
        return "SIDEWAYS"
    if ema9 > ema20 > ema50:
        return "UP"
    if ema9 < ema20 < ema50:
        return "DOWN"
    return "SIDEWAYS"


def breakout_state(closes: np.ndarray, support: float, resistance: float, atr_val: float | None) -> str:
    if atr_val is None or len(closes) < 3:
        return "NONE"
    last = closes[-1]
    prev = closes[-2]
    tol = 0.25 * atr_val
    if prev <= resistance and last > resistance + tol:
        return "BREAKOUT"
    if prev >= support and last < support - tol:
        return "BREAKDOWN"
    if abs(last - resistance) <= tol or abs(last - support) <= tol:
        return "RETEST"
    return "NONE"


def candle_pattern(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> str:
    """Classic candlestick recognition, checked strongest-first.

    Multi-candle reversal/continuation shapes (three-candle stars & soldiers,
    then two-candle engulfing / piercing / harami / tweezers) take priority over
    single-candle shapes, because they carry more information. Returns ONE name
    (the most significant pattern present) or "NONE".
    """
    if len(closes) < 2:
        return "NONE"
    o, h, lo, c = opens[-1], highs[-1], lows[-1], closes[-1]
    po, ph, pl, pc = opens[-2], highs[-2], lows[-2], closes[-2]
    body = abs(c - o)
    pbody = abs(pc - po)
    rng = max(h - lo, 1e-9)
    upper = h - max(o, c)
    lower = min(o, c) - lo
    up, down = c > o, c < o
    pup, pdown = pc > po, pc < po

    # ---- three-candle patterns (strongest) ----
    if len(closes) >= 3:
        o2, c2 = opens[-3], closes[-3]
        b2 = abs(c2 - o2)
        mid_small = pbody <= 0.5 * b2  # small middle "star" body
        # Morning star: big down, small body, big up closing into 1st body
        if c2 < o2 and b2 > rng * 0.3 and mid_small and up and c >= (o2 + c2) / 2:
            return "MORNING_STAR"
        # Evening star: big up, small body, big down closing into 1st body
        if c2 > o2 and b2 > rng * 0.3 and mid_small and down and c <= (o2 + c2) / 2:
            return "EVENING_STAR"
        # Three white soldiers / black crows: three strong same-direction bodies
        if up and pup and c2 > o2 and c > pc > c2 and body > rng * 0.4:
            return "THREE_WHITE_SOLDIERS"
        if down and pdown and c2 < o2 and c < pc < c2 and body > rng * 0.4:
            return "THREE_BLACK_CROWS"

    # ---- two-candle patterns ----
    if up and pdown and c >= po and o <= pc:
        return "BULLISH_ENGULFING"
    if down and pup and c <= po and o >= pc:
        return "BEARISH_ENGULFING"
    # Piercing / dark cloud: open through prior extreme, close past prior midpoint
    if up and pdown and o < pl and c > (po + pc) / 2 and c < po:
        return "PIERCING"
    if down and pup and o > ph and c < (po + pc) / 2 and c > po:
        return "DARK_CLOUD_COVER"
    # Harami: small body engulfed by the prior (larger) body
    if body < 0.6 * pbody and max(o, c) <= max(po, pc) and min(o, c) >= min(po, pc):
        if up and pdown:
            return "BULLISH_HARAMI"
        if down and pup:
            return "BEARISH_HARAMI"
    # Tweezers: matching extremes on consecutive opposite candles
    tol = 0.1 * rng
    if pdown and up and abs(lo - pl) <= tol:
        return "TWEEZER_BOTTOM"
    if pup and down and abs(h - ph) <= tol:
        return "TWEEZER_TOP"
    # Inside bar: full range contained by the prior bar (compression/continuation)
    if h <= ph and lo >= pl:
        return "INSIDE_BAR"

    # ---- single-candle patterns ----
    if body / rng < 0.1:
        return "DOJI"
    if lower > 2 * body and upper < body:
        return "HAMMER"
    if upper > 2 * body and lower < body:
        return "SHOOTING_STAR"
    if body / rng > 0.8:
        return "MARUBOZU_UP" if up else "MARUBOZU_DOWN"
    return "NONE"


def _linreg_slope(y: np.ndarray) -> tuple[float, float]:
    """Return (normalised slope per bar, r^2) of a least-squares fit."""
    n = len(y)
    if n < 3:
        return 0.0, 0.0
    x = np.arange(n, dtype=float)
    x -= x.mean()
    ym = y.mean()
    yc = y - ym
    denom = float((x * x).sum()) or 1e-9
    slope = float((x * yc).sum()) / denom
    ss_tot = float((yc * yc).sum()) or 1e-9
    ss_res = float(((yc - slope * x) ** 2).sum())
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    norm_slope = slope / (abs(ym) + 1e-9)
    return norm_slope, r2


def chart_pattern(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                  atr_val: float | None, window: int = 40) -> tuple[str, int]:
    """Detect the dominant *market-flow* chart shape over a recent window.

    Returns (pattern_name, direction) where direction is +1 bullish, -1 bearish,
    0 neutral. Covers the shapes a trader reads by eye:
    V_REVERSAL / INVERTED_V, DOUBLE_BOTTOM / DOUBLE_TOP, and the underlying
    UPTREND_FLOW / DOWNTREND_FLOW / CHOPPY flow.
    """
    n = len(closes)
    if n < 12:
        return "NONE", 0
    w = min(window, n)
    h = highs[-w:]
    lo = lows[-w:]
    c = closes[-w:]
    atr = atr_val if atr_val and atr_val > 0 else float(np.std(c)) or 1.0
    rng = float(c.max() - c.min()) or 1e-9

    # --- V-shape / inverted-V: sharp reversal around an extreme in the middle ---
    imin = int(np.argmin(lo))
    imax = int(np.argmax(h))
    mid_lo, mid_hi = int(w * 0.2), int(w * 0.8)

    if mid_lo <= imin <= mid_hi:
        left_drop = lo[0] - lo[imin]
        right_rise = c[-1] - lo[imin]
        if left_drop > 1.4 * atr and right_rise > 1.4 * atr and right_rise > 0.6 * left_drop:
            return "V_REVERSAL", 1  # bullish V bottom
    if mid_lo <= imax <= mid_hi:
        left_rise = h[imax] - h[0]
        right_drop = h[imax] - c[-1]
        if left_rise > 1.4 * atr and right_drop > 1.4 * atr and right_drop > 0.6 * left_rise:
            return "INVERTED_V", -1  # bearish inverted-V top

    # --- double bottom / double top: two comparable extremes + midpoint break ---
    sh, sl = swing_points(h, lo, left=2, right=2)
    if len(sl) >= 2:
        (i1, v1), (i2, v2) = sl[-2], sl[-1]
        if abs(v1 - v2) <= 0.35 * atr and i2 - i1 >= 3:
            peak = float(h[i1:i2 + 1].max())
            if c[-1] > peak:  # neckline broken upward
                return "DOUBLE_BOTTOM", 1
    if len(sh) >= 2:
        (i1, v1), (i2, v2) = sh[-2], sh[-1]
        if abs(v1 - v2) <= 0.35 * atr and i2 - i1 >= 3:
            trough = float(lo[i1:i2 + 1].min())
            if c[-1] < trough:  # neckline broken downward
                return "DOUBLE_TOP", -1

    # --- otherwise classify the overall flow via regression on closes ---
    slope, r2 = _linreg_slope(c)
    strong = r2 >= 0.5 and abs(slope) * w > (atr / rng)
    if strong and slope > 0:
        return "UPTREND_FLOW", 1
    if strong and slope < 0:
        return "DOWNTREND_FLOW", -1
    return "CHOPPY", 0


def fake_breakout(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                  support: float, resistance: float, atr_val: float | None,
                  lookback: int = 6) -> str:
    """A trap: price pokes past a level then closes back inside within a few bars.

    FAKE_BREAKOUT (bearish) = poked above resistance but rejected back below.
    FAKE_BREAKDOWN (bullish) = poked below support but reclaimed above.
    """
    if atr_val is None or len(closes) < lookback + 1:
        return "NONE"
    tol = 0.25 * atr_val
    win_h = highs[-lookback:]
    win_l = lows[-lookback:]
    last = float(closes[-1])
    # someone ran stops above resistance in the window, but we now sit back below
    if float(win_h.max()) > resistance + tol and last < resistance - 0.1 * atr_val:
        return "FAKE_BREAKOUT"
    if float(win_l.min()) < support - tol and last > support + 0.1 * atr_val:
        return "FAKE_BREAKDOWN"
    return "NONE"


def liquidity_sweep(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                    atr_val: float | None, lookback: int = 12) -> str:
    """Stop-hunt: a single bar spikes beyond the prior swing extreme and snaps
    back, sweeping liquidity before reversing.

    SWEEP_HIGH (bearish) = wick took out prior highs then closed back down.
    SWEEP_LOW  (bullish) = wick took out prior lows then closed back up.
    """
    if atr_val is None or len(closes) < lookback + 1:
        return "NONE"
    prior_h = float(highs[-lookback - 1:-1].max())
    prior_l = float(lows[-lookback - 1:-1].min())
    hi, lo, close = float(highs[-1]), float(lows[-1]), float(closes[-1])
    # bearish sweep: pierced prior highs by >0.3 ATR but closed back under them
    if hi > prior_h + 0.3 * atr_val and close < prior_h:
        return "SWEEP_HIGH"
    if lo < prior_l - 0.3 * atr_val and close > prior_l:
        return "SWEEP_LOW"
    return "NONE"


def pullback(closes: np.ndarray, ema20: float | None, trend: str,
             atr_val: float | None) -> str:
    """Healthy trend pullback into the mean — a continuation entry, not a reversal.

    PULLBACK_UP (bullish) = uptrend dipping back toward/below EMA20.
    PULLBACK_DOWN (bearish) = downtrend popping back toward/above EMA20.
    """
    if ema20 is None or atr_val is None or len(closes) < 3:
        return "NONE"
    last = float(closes[-1])
    near = abs(last - ema20) <= 0.6 * atr_val
    if trend == "UP" and (last <= ema20 or near):
        return "PULLBACK_UP"
    if trend == "DOWN" and (last >= ema20 or near):
        return "PULLBACK_DOWN"
    return "NONE"


def fair_value_gap(highs: np.ndarray, lows: np.ndarray) -> tuple[float | None, float | None]:
    """Nearest unfilled 3-candle imbalance edge, as (bullish, bearish) retest level.

    A bullish gap exists where a candle's low prints above the high of the candle
    two bars earlier: price skipped that band, and its lower edge is the level a
    retrace would come back to. Bearish is the mirror. Only the most recent gap
    price has *not* yet traded back into is returned — once price has been inside
    the band the retest has already happened and the level is spent.

    RESEARCH ONLY today: this feeds the Phase 7 entry-policy comparison that
    Phase 13A re-runs. Nothing in the decision engine reads it.
    """
    n = len(highs)
    if n < 3 or len(lows) != n:
        return None, None
    bull: float | None = None
    bear: float | None = None
    for i in range(n - 1, 1, -1):
        later_low = float(min(lows[i + 1:])) if i + 1 < n else None
        later_high = float(max(highs[i + 1:])) if i + 1 < n else None
        if bull is None and lows[i] > highs[i - 2]:
            edge = float(lows[i])
            if later_low is None or later_low > edge:
                bull = edge
        if bear is None and highs[i] < lows[i - 2]:
            edge = float(highs[i])
            if later_high is None or later_high < edge:
                bear = edge
        if bull is not None and bear is not None:
            break
    return bull, bear


def crash_signal(closes: np.ndarray, volumes: np.ndarray, atr_val: float | None):
    """Detect a sudden institutional move: large candle + volume + momentum."""
    if atr_val is None or len(closes) < 6:
        return False, ""
    move = closes[-1] - closes[-2]
    big_candle = abs(move) > 2.2 * atr_val
    vol_avg = volumes[-11:-1].mean() if len(volumes) > 11 else volumes.mean()
    vol_surge = volumes[-1] > 2.5 * max(vol_avg, 1e-9)
    momentum_shift = np.sign(closes[-1] - closes[-3]) != np.sign(closes[-3] - closes[-5])
    if big_candle and vol_surge:
        direction = "DOWN" if move < 0 else "UP"
        return True, f"{direction} — large candle + volume surge (Δ{move:.1f}, vol×{volumes[-1] / max(vol_avg,1e-9):.1f})"
    if big_candle and momentum_shift:
        return True, "momentum shift with expanded range"
    return False, ""


def ignition(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    *,
    body_factor: float = 1.2,
    volume_factor: float = 1.5,
    compression_factor: float = 1.1,
    lookback: int = 10,
) -> str:
    """Detect the FIRST expansion candle of a move ("strong green/red candle").

    This is an *ignition* entry, not a reversal guess: the move has already
    started on the just-closed candle, so we join it on candle 1 instead of
    waiting for accumulated momentum to cross a threshold (which only happens
    several candles later, near the top of the run).

    Requires all three:
      * a conviction body -- ``body >= body_factor x`` the recent average body,
      * a volume surge vs the same lookback,
      * the move breaking out of a *compression*: the preceding candles were
        quieter than the recent average range (a base, not an ongoing run).

    Returns ``IGNITION_UP`` / ``IGNITION_DOWN`` / ``NONE``.
    """
    n = lookback + 2
    if min(len(opens), len(highs), len(lows), len(closes), len(volumes)) < n:
        return "NONE"
    body = abs(float(closes[-1]) - float(opens[-1]))
    prev_bodies = np.abs(closes[-lookback - 1:-1] - opens[-lookback - 1:-1])
    avg_body = float(prev_bodies.mean())
    if avg_body <= 0 or body < body_factor * avg_body:
        return "NONE"
    rng = float(highs[-1] - lows[-1])
    if rng <= 0 or body / rng < 0.5:  # a wick-heavy candle is not conviction
        return "NONE"
    vol_avg = float(volumes[-lookback - 1:-1].mean())
    if vol_avg > 0 and float(volumes[-1]) < volume_factor * vol_avg:
        return "NONE"
    # compression: the 3 candles before the ignition were quieter than the
    # lookback average range -- i.e. we are leaving a base, not chasing a run.
    prev_ranges = highs[-lookback - 1:-1] - lows[-lookback - 1:-1]
    avg_range = float(prev_ranges.mean())
    base_range = float((highs[-4:-1] - lows[-4:-1]).mean())
    if avg_range > 0 and base_range > compression_factor * avg_range:
        return "NONE"
    return "IGNITION_UP" if closes[-1] > opens[-1] else "IGNITION_DOWN"
