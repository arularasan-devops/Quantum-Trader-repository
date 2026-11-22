"""Pullback Quality classifier (log-only).

Classifies the underlying's recent 1-min path at a BUY into an entry path
(FRESH_MOMENTUM / PULLBACK_CONTINUATION) and one label:
HEALTHY_PULLBACK, ABSORPTION_POSSIBLE, CONTINUATION_CONFIRMED,
PULLBACK_FAILURE, TREND_REVERSAL_RISK, EXHAUSTED_MOVE,
WAIT_FOR_CONFIRMATION, NO_TRADE.

Pure function, no I/O. Thresholds are in ``settings.pullback_quality_params``.
It only records (``pullback_quality_mode="log"``); it never blocks a trade.
"""
from __future__ import annotations

from app.config import settings
from app.models import Candle

DEFAULTS = {
    "window": 30,             # bars examined
    "min_impulse_atr": 2.0,   # impulse size in bar-range ATRs
    "max_extension_atr": 6.0, # fresh momentum beyond this is exhausted
    "healthy_min": 0.236,     # retracement band (fraction of the impulse)
    "healthy_max": 0.618,
    "absorption_max": 0.382,
    "reversal_max": 1.0,      # retracing the whole impulse = failure
    "strong_close": 0.7,      # close in the top 30% of the bar
    "min_rel_volume": 1.2,
    "min_option_hold": 0.5,   # option keeps >= 50% of its window range
    "min_reward_risk": 1.0,
}


def _p(k: str) -> float:
    return float((settings.pullback_quality_params or {}).get(k, DEFAULTS[k]))


def classify(*, call: bool, candles: list[Candle], option_closes: list[float] | None = None,
             ask: float | None = None, stop: float | None = None,
             target: float | None = None) -> dict:
    s = 1.0 if call else -1.0
    bars = [c for c in candles if c.high >= c.low > 0][-int(_p("window")):]
    rec: dict = {"path": None, "label": "NO_TRADE", "reasons": []}
    if len(bars) < 5:
        rec["reasons"].append("not enough bars")
        return rec
    hi = [c.high if call else -c.low for c in bars]
    lo = [c.low if call else -c.high for c in bars]
    cl = [s * c.close for c in bars]
    vol = [float(c.volume or 0.0) for c in bars]
    atr = sum(c.high - c.low for c in bars) / len(bars) or 1e-9
    i_h = max(range(len(bars)), key=lambda i: hi[i])
    i_l = min(range(i_h + 1), key=lambda i: lo[i])
    impulse = hi[i_h] - lo[i_l]
    last = len(bars) - 1
    rec.update(atr=round(atr, 3), impulse_pts=round(impulse, 2),
               impulse_atr=round(impulse / atr, 2), bars_since_high=last - i_h)
    if ask and stop is not None and target is not None and ask > stop:
        rr = (target - ask) / (ask - stop)
        rec["reward_risk"] = round(rr, 2)
        if rr < _p("min_reward_risk"):
            rec["reasons"].append(f"insufficient room: reward/risk {rr:.2f}")
            return rec
    if impulse / atr < _p("min_impulse_atr"):
        rec["reasons"].append("no impulse")
        return rec
    imp_vol = vol[i_l + 1:i_h + 1] or [vol[i_h]]
    imp_vol_avg = sum(imp_vol) / len(imp_vol)
    rng = bars[last].high - bars[last].low
    pos = ((cl[last] - lo[last]) / rng) if rng > 0 else 0.5
    if last == i_h:
        rec["path"] = "FRESH_MOMENTUM"
        prior = vol[:-1]
        rel = vol[last] / (sum(prior) / len(prior)) if prior and sum(prior) > 0 else 0.0
        rec["rel_volume"] = round(rel, 2)
        if impulse / atr > _p("max_extension_atr"):
            rec["label"] = "EXHAUSTED_MOVE"
        elif pos >= _p("strong_close") and rel >= _p("min_rel_volume"):
            rec["label"] = "CONTINUATION_CONFIRMED"
        else:
            rec["label"] = "WAIT_FOR_CONFIRMATION"
    else:
        rec["path"] = "PULLBACK_CONTINUATION"
        pull_lo = min(lo[i_h + 1:])
        retr = (hi[i_h] - pull_lo) / impulse
        pb_vol = vol[i_h + 1:]
        pb_vol_avg = sum(pb_vol) / len(pb_vol)
        rec.update(retracement=round(retr, 3),
                   pullback_vs_impulse_volume=round(pb_vol_avg / imp_vol_avg, 2)
                   if imp_vol_avg > 0 else None)
        broke = hi[last] > hi[last - 1] and pos >= _p("strong_close")
        if retr >= _p("reversal_max"):
            rec["label"] = "PULLBACK_FAILURE"
        elif retr > _p("healthy_max"):
            rec["label"] = "TREND_REVERSAL_RISK"
        elif retr < _p("healthy_min"):
            rec["label"] = "WAIT_FOR_CONFIRMATION"
        elif broke:
            rec["label"] = "CONTINUATION_CONFIRMED"
        elif pb_vol_avg < imp_vol_avg:
            rec["label"] = "HEALTHY_PULLBACK"
        elif retr <= _p("absorption_max"):
            rec["label"] = "ABSORPTION_POSSIBLE"
        else:
            rec["label"] = "WAIT_FOR_CONFIRMATION"
    oc = [x for x in (option_closes or []) if x and x > 0][-int(_p("window")):]
    if len(oc) >= 3 and max(oc) > min(oc):
        hold = (oc[-1] - min(oc)) / (max(oc) - min(oc))
        rec["option_hold"] = round(hold, 2)
        if rec["label"] == "CONTINUATION_CONFIRMED" and hold < _p("min_option_hold"):
            rec["label"] = "WAIT_FOR_CONFIRMATION"
            rec["reasons"].append(f"option weak: holds {hold:.0%} of its range")
    return rec
