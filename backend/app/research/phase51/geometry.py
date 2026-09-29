"""Phase 51 §3/§6 — raw market geometry, and bounded arithmetic over it.

Raw structure before indicators, because an indicator is already somebody's
hypothesis. Every array here is a candidate explanatory variable and nothing
more: none of it is assumed predictive, and the engine's job is to find out that
most of it is not.

The one property that makes or breaks the whole engine is causality. The value
at index ``i`` uses bars ``<= i`` and nothing later, with two consequences that
are easy to get wrong and invisible once wrong:

* a session-anchored level is **NaN until it is knowable**. The 30-minute
  opening range does not exist at 09:20, the previous day's high does not exist
  on the first session of the series, and a NaN is how that is said. Filling it
  with the eventual value is a look-ahead of exactly the size of the window;
* a trailing window that would reach across a session boundary is not allowed to
  borrow the previous session's minutes for an intraday level. Yesterday's path
  is available only through yesterday's *closed* levels.

The smoke test recomputes every feature on a truncated series and asserts the
prefix is unchanged, which is the only reliable way to catch a peek.

§6's arithmetic is deliberately small and interpretable: ratios, normalised
distances, percentiles and one economic ratio (expected move over estimated
round-trip cost). There is no unconstrained symbolic search, because an
expression nobody can read cannot be judged, reproduced, or refused.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research.phase24 import outcomes as p24outcomes
from app.research.phase24.data import Series

IST_OFFSET = 19_800

# Windows are round numbers on purpose. A window chosen after seeing the result
# is a fitted parameter that never appears in the trial count.
ATR_WIN = 14
SLOW_ATR_WIN = 60
RANGE_PCTL_WIN = 20
ROLLING_WIN = 20
PREV_RANGE_PCTL_WIN = 60          # in sessions
VOLUME_WIN = 20
OPENING_WINDOWS = (5, 15, 30, 60)
RETURN_WINDOWS = (1, 5, 15, 30, 60)
TIME_BUCKET_MINUTES = 30

# Fixed probe points for reading the shared cost model's slope in price. Any two
# constants do, provided they are constants: a probe taken from the data would
# make a bar's cost feature depend on prices the bar has not seen.
COST_PROBE_PRICE = 1_000.0
COST_PROBE_STEP = 10_000.0


def sessions_of(ts: np.ndarray) -> np.ndarray:
    return (ts + IST_OFFSET) // 86_400


def minutes_into_day(ts: np.ndarray) -> np.ndarray:
    return ((ts + IST_OFFSET) % 86_400) // 60


def session_bounds(sess: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, end) index ranges, one per session."""
    n = sess.size
    if n == 0:
        return []
    starts = [0] + list(np.nonzero(sess[1:] != sess[:-1])[0] + 1)
    ends = starts[1:] + [n]
    return list(zip(starts, ends))


def _trailing_windows(a: np.ndarray, win: int) -> np.ndarray:
    """Trailing windows of length ``win`` ending at each index, front-padded."""
    if a.size == 0:
        return np.zeros((0, win), dtype=np.float64)
    pad = np.full(win - 1, a[0], dtype=np.float64)
    return np.lib.stride_tricks.sliding_window_view(
        np.concatenate((pad, a.astype(np.float64))), win
    )


def _rolling_max(a: np.ndarray, win: int) -> np.ndarray:
    return _trailing_windows(a, win).max(axis=1) if a.size else a.astype(np.float64)


def _rolling_min(a: np.ndarray, win: int) -> np.ndarray:
    return _trailing_windows(a, win).min(axis=1) if a.size else a.astype(np.float64)


def _rolling_mean(a: np.ndarray, win: int) -> np.ndarray:
    if a.size == 0:
        return a.astype(np.float64)
    c = np.concatenate(([0.0], np.cumsum(a, dtype=np.float64)))
    idx = np.arange(a.size)
    lo = np.maximum(0, idx - win + 1)
    return (c[idx + 1] - c[lo]) / (idx - lo + 1)


def _rolling_rank_pct(a: np.ndarray, win: int) -> np.ndarray:
    """Percentile of each value within its own trailing window, inclusive.

    A percentile rather than a level so one threshold means the same thing on
    NIFTY and on CRUDEOIL, whose ranges differ by two orders of magnitude.
    """
    if a.size == 0:
        return a.astype(np.float64)
    w = _trailing_windows(a, win)
    current = a.astype(np.float64)[:, None]
    return 100.0 * (w <= current).sum(axis=1) / float(win)


def _true_range(s: Series) -> np.ndarray:
    prev_close = np.concatenate(([s.close[0]], s.close[:-1]))
    return np.maximum.reduce([
        s.high - s.low,
        np.abs(s.high - prev_close),
        np.abs(s.low - prev_close),
    ])


def _wilder_atr(tr: np.ndarray, win: int) -> np.ndarray:
    """Wilder's ATR, with the first ``win`` bars left NaN rather than seeded.

    Seeding the first bars with the first true range publishes a number before
    the window exists, and the earliest sessions of the series would then carry
    candidates priced off a one-bar ATR.
    """
    n = tr.size
    out = np.full(n, np.nan)
    if n < win:
        return out
    out[win - 1] = tr[:win].mean()
    alpha = 1.0 / float(win)
    for i in range(win, n):
        out[i] = out[i - 1] + alpha * (tr[i] - out[i - 1])
    return out


def _running_extremes(
    s: Series, bounds: list[tuple[int, int]]
) -> tuple[np.ndarray, np.ndarray]:
    """Session high and low up to and including each bar."""
    hi = np.empty(s.high.size)
    lo = np.empty(s.low.size)
    for a, b in bounds:
        hi[a:b] = np.maximum.accumulate(s.high[a:b])
        lo[a:b] = np.minimum.accumulate(s.low[a:b])
    return hi, lo


def _previous_session_levels(
    s: Series, bounds: list[tuple[int, int]]
) -> dict[str, np.ndarray]:
    """Yesterday's closed high, low, close, midpoint and range, per bar.

    NaN through the whole first session: there is no previous day, and a first
    session priced off its own levels would be the look-ahead this engine exists
    to avoid.
    """
    n = s.close.size
    out = {k: np.full(n, np.nan) for k in
           ("prev_high", "prev_low", "prev_close", "prev_mid", "prev_range")}
    day_ranges: list[float] = []
    range_pctl = np.full(n, np.nan)
    prev = None
    for a, b in bounds:
        if prev is not None:
            p_hi, p_lo, p_close = prev
            out["prev_high"][a:b] = p_hi
            out["prev_low"][a:b] = p_lo
            out["prev_close"][a:b] = p_close
            out["prev_mid"][a:b] = (p_hi + p_lo) / 2.0
            out["prev_range"][a:b] = p_hi - p_lo
            # Where yesterday's range sits among the previous sessions' ranges,
            # counted over closed sessions only.
            hist = day_ranges[-PREV_RANGE_PCTL_WIN:]
            if len(hist) >= 10:
                r = p_hi - p_lo
                range_pctl[a:b] = 100.0 * sum(1 for x in hist if x <= r) / len(hist)
            day_ranges.append(p_hi - p_lo)
        prev = (
            float(s.high[a:b].max()),
            float(s.low[a:b].min()),
            float(s.close[b - 1]),
        )
    out["prev_range_percentile"] = range_pctl
    return out


def _opening_ranges(
    s: Series, bounds: list[tuple[int, int]], mins: np.ndarray
) -> dict[str, np.ndarray]:
    """Opening-range high/low for each declared window, published only once closed."""
    n = s.close.size
    out: dict[str, np.ndarray] = {}
    for win in OPENING_WINDOWS:
        hi = np.full(n, np.nan)
        lo = np.full(n, np.nan)
        for a, b in bounds:
            day_mins = mins[a:b]
            first = day_mins[0]
            inside = day_mins < first + win
            if not inside.any():
                continue
            after = np.nonzero(~inside)[0] + a
            hi[after] = float(s.high[a:b][inside].max())
            lo[after] = float(s.low[a:b][inside].min())
        out[f"opening_range_{win}m_high"] = hi
        out[f"opening_range_{win}m_low"] = lo
        out[f"opening_range_{win}m_size"] = hi - lo
    return out


def _day_open(s: Series, bounds: list[tuple[int, int]]) -> np.ndarray:
    out = np.empty(s.open.size)
    for a, b in bounds:
        out[a:b] = s.open[a]
    return out


def _session_vwap(s: Series, bounds: list[tuple[int, int]]) -> tuple[np.ndarray, bool]:
    """Session-anchored VWAP, and whether it is a true volume-weighted price.

    With no volume there is no VWAP. The running typical-price mean is returned
    in its place and the flag says so, so a family that quotes VWAP on a series
    without volume is quoting a mean and the report can refuse it.
    """
    typical = (s.high + s.low + s.close) / 3.0
    out = np.empty(s.close.size)
    volume_present = bool(np.any(s.volume > 0.0))
    # A negative volume would subtract from the session's cumulative weight and
    # drag the anchored price off the market. Those bars contribute no weight.
    volume = np.where(s.volume >= 0.0, s.volume, 0.0)
    for a, b in bounds:
        tp = typical[a:b]
        vol = volume[a:b]
        running = np.cumsum(tp) / (np.arange(tp.size) + 1)
        cv = np.cumsum(vol)
        if cv[-1] > 0:
            weighted = np.cumsum(tp * vol) / np.maximum(cv, 1e-9)
            out[a:b] = np.where(cv > 0, weighted, running)
        else:
            out[a:b] = running
    return out, volume_present


def _returns(close: np.ndarray, win: int) -> np.ndarray:
    if close.size <= win:
        return np.full(close.size, np.nan)
    prev = np.concatenate((np.full(win, np.nan), close[:-win]))
    return close - prev


def cost_points(instrument: str, close: np.ndarray) -> np.ndarray:
    """Modelled round-trip cost in points at each bar's price.

    From the shared futures cost model, per price, because statutory charges are
    a share of turnover and one median cost across five years misprices both
    ends of the series. Used only to form the expected-move-over-cost ratio;
    the trade's own cost is charged again, properly, by the resolver.

    The shared helper anchors its linear probe at the median of the prices it is
    handed, which would make this bar's feature depend on prices after it. The
    model is linear in price, so the probe is taken here at two fixed constants
    instead and applied per bar — the cost still comes from the shared model,
    and the feature reads nothing the bar could not know.
    """
    lot_size = int(get_spec(instrument).lot_size or 1)
    probe = np.array([COST_PROBE_PRICE, COST_PROBE_PRICE + COST_PROBE_STEP])
    c = p24outcomes.cost_points_per_trade(
        instrument, probe, probe, lot_size=lot_size, slippage_points=None
    )
    slope = (float(c[1]) - float(c[0])) / COST_PROBE_STEP
    intercept = float(c[0]) - slope * COST_PROBE_PRICE
    return intercept + slope * close


def build(s: Series) -> dict[str, np.ndarray]:
    """Every §3 feature and every §6 derived ratio, causal, as named arrays."""
    ts = s.ts
    sess = sessions_of(ts)
    mins = minutes_into_day(ts)
    bounds = session_bounds(sess)
    tr = _true_range(s)
    atr = _wilder_atr(tr, ATR_WIN)
    slow_atr = _wilder_atr(tr, SLOW_ATR_WIN)
    scale = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)

    f: dict[str, np.ndarray] = {}
    f["ts"] = ts.astype(np.float64)
    f["session"] = sess.astype(np.float64)
    # The bar itself travels with the features: a rule reads the close it
    # decided on from the same dictionary it reads its levels from, so the two
    # can never be taken from different alignments of the series.
    f["open"] = s.open
    f["high"] = s.high
    f["low"] = s.low
    f["close"] = s.close
    first_minute = np.repeat(
        np.array([mins[a] for a, _ in bounds], dtype=np.int64),
        np.array([b - a for a, b in bounds], dtype=np.int64),
    ) if bounds else np.zeros(0, dtype=np.int64)
    f["time_since_open"] = (mins - first_minute).astype(np.float64)
    f["time_bucket"] = np.floor(f["time_since_open"] / TIME_BUCKET_MINUTES)

    f.update(_previous_session_levels(s, bounds))
    f.update(_opening_ranges(s, bounds, mins))
    f["current_day_open"] = _day_open(s, bounds)
    f["opening_gap"] = f["current_day_open"] - f["prev_close"]
    f["opening_gap_pct"] = 100.0 * f["opening_gap"] / np.where(
        f["prev_close"] > 0, f["prev_close"], np.nan
    )

    sh, sl = _running_extremes(s, bounds)
    f["session_high"] = sh
    f["session_low"] = sl
    f["rolling_high"] = _rolling_max(s.high, ROLLING_WIN)
    f["rolling_low"] = _rolling_min(s.low, ROLLING_WIN)

    f["atr"] = atr
    f["slow_atr"] = slow_atr
    f["true_range"] = tr
    f["range_expansion"] = tr / scale
    f["range_percentile"] = _rolling_rank_pct(tr, RANGE_PCTL_WIN)
    f["volatility_ratio"] = atr / np.where(
        np.isfinite(slow_atr) & (slow_atr > 0), slow_atr, np.nan
    )

    # A negative volume is not a volume. About one bar in a thousand of both
    # stored series carries one, and dividing by a window that contains it would
    # publish a ratio with no reading at all, so those bars are absent instead.
    volume = np.where(s.volume >= 0.0, s.volume, np.nan)
    vol_mean = _rolling_mean(np.nan_to_num(volume, nan=0.0), VOLUME_WIN)
    f["volume_ratio"] = np.where(
        (vol_mean > 0) & np.isfinite(volume), volume / np.maximum(vol_mean, 1e-9),
        np.nan,
    )

    vwap, _ = _session_vwap(s, bounds)
    f["vwap"] = vwap
    f["distance_from_vwap"] = s.close - vwap

    for win in RETURN_WINDOWS:
        f[f"return_{win}m"] = _returns(s.close, win)
        f[f"return_{win}m_atr"] = f[f"return_{win}m"] / scale

    # §3's distances, in points and in ATR. The ATR form is what a threshold can
    # be declared in once for every instrument.
    for name, level in (
        ("distance_from_previous_high", f["prev_high"]),
        ("distance_from_previous_low", f["prev_low"]),
        ("distance_from_previous_close", f["prev_close"]),
        ("distance_from_previous_mid", f["prev_mid"]),
        ("distance_from_day_open", f["current_day_open"]),
    ):
        f[name] = s.close - level
        f[f"{name}_atr"] = f[name] / scale

    # §6 — bounded interpretable arithmetic. Four ratio forms, each with a
    # reading a human can state in one sentence.
    prev_range = np.where(f["prev_range"] > 0, f["prev_range"], np.nan)
    f["move_over_previous_day_range"] = (s.close - f["current_day_open"]) / prev_range
    day_range = np.where((sh - sl) > 0, sh - sl, np.nan)
    f["move_over_current_range"] = (s.close - f["current_day_open"]) / day_range
    f["opening_range_over_previous_range"] = (
        f["opening_range_15m_size"] / prev_range
    )
    f["prev_range_over_atr"] = f["prev_range"] / scale

    cost = cost_points(s.instrument, s.close)
    f["estimated_cost_points"] = cost
    safe_cost = np.where(cost > 0, cost, np.nan)
    # The economic ratio §6 asks for: one ATR of expected move measured against
    # what a round trip costs. Below 1.0 the move does not pay for the trade.
    f["expected_move_over_cost"] = atr / safe_cost
    f["previous_range_over_cost"] = f["prev_range"] / safe_cost
    return f


def feature_names() -> tuple[str, ...]:
    """Declared feature vocabulary, for the artefact and the smoke test."""
    return (
        "prev_high", "prev_low", "prev_close", "prev_mid", "prev_range",
        "prev_range_percentile", "current_day_open", "opening_gap",
        "opening_gap_pct", "session_high", "session_low", "rolling_high",
        "rolling_low", "atr", "slow_atr", "true_range", "range_expansion",
        "range_percentile", "volatility_ratio", "volume_ratio", "vwap",
        "distance_from_vwap", "time_since_open", "time_bucket",
        "distance_from_previous_high", "distance_from_previous_low",
        "distance_from_previous_close", "distance_from_previous_mid",
        "distance_from_day_open",
        "move_over_previous_day_range", "move_over_current_range",
        "opening_range_over_previous_range", "prev_range_over_atr",
        "estimated_cost_points", "expected_move_over_cost",
        "previous_range_over_cost",
    ) + tuple(f"return_{w}m" for w in RETURN_WINDOWS) + tuple(
        f"opening_range_{w}m_size" for w in OPENING_WINDOWS
    )
