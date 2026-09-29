"""Canonical causal feature set for the Phase 6 AI engine.

ONE implementation, used by both the offline trainer (``phase6_dataset.py``) and
the live engines. Train/serve skew is the failure mode that silently invalidates
a model, and the cheapest defence is to make it impossible to have two versions
of the feature code.

Rules that hold everywhere in this module:

* Every value is computed from the candle window ending at ``t`` — the bar being
  decided on — and nothing after it. There is no outcome, no forward bar and no
  session aggregate that could leak one.
* No option-chain input. The archive has zero real chains (Phase 5, Step 1), so
  a model trained on IV/OI/spread would be a model of the simulator. Those fields
  are recorded live for future research but are not features.
* Indicators come from ``app.analysis.indicators`` (the same code the production
  engine uses) rather than being re-derived here.
* Missing values are returned as ``None`` and imputed at exactly one place
  (``vector``), so a live gap and a training gap are treated identically.

``FEATURES`` is the frozen, ordered column list. The model artefact records it
and the scorer refuses to run against a mismatch.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

import numpy as np

from app.analysis import indicators as ind
from app.models import Candle

_IST = timezone(timedelta(hours=5, minutes=30))

WINDOW = 240             # bars the feature set needs
MIN_WINDOW = 120         # below this nothing is computed at all

# Frozen column order. Appending is safe; reordering or removing invalidates
# every artefact trained before the change (the scorer checks and refuses).
FEATURES: tuple[str, ...] = (
    "ret_1", "ret_3", "ret_5", "ret_15", "ret_30",
    "atr_pct", "atr_expansion", "rvol_30", "rvol_vs_base",
    "vwap_dist_atr", "ema_spread_atr", "ema50_dist_atr",
    "adx", "rsi", "macd_hist",
    "rel_volume", "vol_accel",
    "breakout_atr", "breakdown_atr",
    "ext_from_swing_lo_atr", "ext_from_swing_hi_atr",
    "persistence_10", "supertrend_dir",
    "tod_min", "dow",
    "pullback_depth_atr", "bars_since_extreme",
    "side_is_pe",
)

# Imputation defaults, applied identically offline and live.
_DEFAULTS: dict[str, float] = {
    "adx": 20.0, "rsi": 50.0, "macd_hist": 0.0, "supertrend_dir": 0.0,
    "atr_expansion": 1.0, "rvol_vs_base": 1.0, "rel_volume": 1.0,
    "vol_accel": 1.0,
}


def _pct(a: float, b: float) -> float:
    return 0.0 if not b else round(100.0 * (a - b) / b, 4)


def atr(candles: list[Candle], period: int = 14) -> float | None:
    """ATR over the last ``period`` bars of the window (bars <= t only)."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(len(candles) - period, len(candles)):
        prev = candles[i - 1].close
        trs.append(max(candles[i].high - candles[i].low,
                       abs(candles[i].high - prev), abs(candles[i].low - prev)))
    return sum(trs) / len(trs) if trs else None


def compute(window: list[Candle]) -> dict | None:
    """Feature dict for the bar at the END of ``window``, or None if too short.

    ``side_is_pe`` is left out here and supplied by :func:`vector`, because both
    sides are scored on the same bar and only differ by that one column.
    """
    if len(window) < MIN_WINDOW:
        return None
    c = window[-1]
    px = float(c.close)
    a = atr(window)
    if not a or a <= 0 or not px:
        return None

    closes = [x.close for x in window]
    highs = np.array([x.high for x in window], dtype=float)
    lows = np.array([x.low for x in window], dtype=float)
    cl = np.array(closes, dtype=float)
    vols = [x.volume or 0.0 for x in window]

    def ret(n: int) -> float:
        return _pct(px, closes[-1 - n]) if len(closes) > n else 0.0

    rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(len(closes) - 30, len(closes)) if closes[i - 1]]
    rvol = statistics.pstdev(rets) * 100.0 if len(rets) > 2 else 0.0
    base = [(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(max(1, len(closes) - 120), len(closes)) if closes[i - 1]]
    base_rvol = statistics.pstdev(base) * 100.0 if len(base) > 2 else 0.0

    atr50 = atr(window, 50) or a
    v20 = statistics.mean(vols[-20:]) if len(vols) >= 20 else (vols[-1] or 0.0)
    v5 = statistics.mean(vols[-5:]) if len(vols) >= 5 else (vols[-1] or 0.0)

    hi20 = max(x.high for x in window[-21:-1]) if len(window) > 21 else c.high
    lo20 = min(x.low for x in window[-21:-1]) if len(window) > 21 else c.low
    swing_lo = min(x.low for x in window[-30:])
    swing_hi = max(x.high for x in window[-30:])

    signs = [1 if closes[i] > closes[i - 1] else -1
             for i in range(len(closes) - 10, len(closes))]

    # 90-bar rolling VWAP. A true session VWAP needs session boundaries the
    # archive does not carry, so this is a proxy and is named as one.
    tv = sum(x.close * (x.volume or 1.0) for x in window[-90:])
    tq = sum((x.volume or 1.0) for x in window[-90:])
    vwap90 = tv / tq if tq else px

    ema9 = ind.ema(cl, 9) or px
    ema20 = ind.ema(cl, 20) or px
    ema50 = ind.ema(cl, 50) or px
    st_dir = 0.0
    try:
        _, up = ind.supertrend(highs, lows, cl)
        st_dir = 1.0 if up else -1.0
    except Exception:
        st_dir = 0.0
    macd_hist = None
    try:
        _, _, macd_hist = ind.macd(cl)
    except Exception:
        macd_hist = None

    # Pullback depth and how long ago the extreme was — the two quantities the
    # Phase 5 stop-geometry finding says an entry should be judged on.
    win30 = window[-30:]
    if sum(signs) >= 0:
        extreme = max(range(len(win30)), key=lambda k: win30[k].high)
        depth = (win30[extreme].high - px) / a
    else:
        extreme = min(range(len(win30)), key=lambda k: win30[k].low)
        depth = (px - win30[extreme].low) / a
    bars_since = len(win30) - 1 - extreme

    dt = datetime.fromtimestamp(c.time, _IST)
    return {
        "ret_1": ret(1), "ret_3": ret(3), "ret_5": ret(5),
        "ret_15": ret(15), "ret_30": ret(30),
        "atr_pct": round(100.0 * a / px, 4),
        "atr_expansion": round(a / atr50, 4) if atr50 else 1.0,
        "rvol_30": round(rvol, 4),
        "rvol_vs_base": round(rvol / base_rvol, 4) if base_rvol else 1.0,
        "vwap_dist_atr": round((px - vwap90) / a, 4),
        "ema_spread_atr": round((ema9 - ema20) / a, 4),
        "ema50_dist_atr": round((px - ema50) / a, 4),
        "adx": (lambda v: round(v, 2) if v is not None else None)(
            ind.adx(highs, lows, cl)),
        "rsi": (lambda v: round(v, 2) if v is not None else None)(ind.rsi(cl)),
        "macd_hist": round(float(macd_hist), 4) if macd_hist is not None else None,
        "rel_volume": round((c.volume or 0.0) / v20, 4) if v20 else 1.0,
        "vol_accel": round(v5 / v20, 4) if v20 else 1.0,
        "breakout_atr": round((px - hi20) / a, 4),
        "breakdown_atr": round((lo20 - px) / a, 4),
        "ext_from_swing_lo_atr": round((px - swing_lo) / a, 4),
        "ext_from_swing_hi_atr": round((swing_hi - px) / a, 4),
        "persistence_10": round(abs(sum(signs)) / 10.0, 3),
        "supertrend_dir": st_dir,
        "tod_min": dt.hour * 60 + dt.minute,
        "dow": dt.weekday(),
        "pullback_depth_atr": round(depth, 4),
        "bars_since_extreme": float(bars_since),
        # context, not features — kept for the journal and the regime engine
        "_atr": round(a, 4),
        "_price": px,
        "_vwap90": round(vwap90, 4),
        "_ema20": round(ema20, 4),
        "_ema50": round(ema50, 4),
        "_swing_hi": round(swing_hi, 4),
        "_swing_lo": round(swing_lo, 4),
        # Where price sits inside the 30-bar swing, 0 = at the low, 1 = at the
        # high. Scale-free by construction, unlike distance-in-ATR whose typical
        # value is 3-5 on these instruments — see the regime engine.
        "_range_pos": (round((px - swing_lo) / (swing_hi - swing_lo), 4)
                       if swing_hi > swing_lo else 0.5),
        "_ts": int(c.time),
    }


def vector(feats: dict, side: str) -> list[float]:
    """Model input row for one side. Imputation happens here and only here."""
    out: list[float] = []
    for name in FEATURES:
        if name == "side_is_pe":
            out.append(1.0 if side.upper() == "PE" else 0.0)
            continue
        v = feats.get(name)
        if v is None:
            v = _DEFAULTS.get(name, 0.0)
        out.append(float(v))
    return out
