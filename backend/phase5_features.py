"""Phase 5 pass 1 — causal feature/outcome extraction for the regime,
direction and entry-probability research.

One row per sampled bar. Every feature is computed from bars ``<= t`` only, and
every outcome from bars ``> t`` only; the two never share a bar. Both sides are
scored at every bar on IDENTICAL levels so that "is CE better than PE" cannot be
answered by one side being measured on easier terms.

The outcome is measured on the UNDERLYING, not on a premium. Phase 2/3 used
modelled 0.6-delta premiums and the user's Phase 5 brief forbids optimising
against those as if they were real; the archive has no real chains (see
``chain_provenance.py`` — 0 REAL_BROKER snapshots), so the honest choice is to
research the part that IS real data. Everything downstream therefore describes
underlying moves, and a real premium's theta/spread/skew will only make it
worse, never better.

The engine's own verdict is replayed at the same bars (same
``compute_indicators`` / ``classify_market`` / ``decide`` path as production) so
Step 5 can compare against the accepted-BUY population. The engine needs an
option chain to run at all, and that chain is modelled — it is used ONLY to
reproduce the engine's decision, never as an outcome.

    python phase5_features.py --instrument CRUDEOIL --out ~/p5_crude.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import datetime, timedelta, timezone

from app.analysis import scanner
from app.backtest import angel_history as ah
from app.config import settings
from app.engine.decision import classify_market, compute_indicators, decide
from app.market.instruments import REGISTRY
from app.models import Candle, OptionQuote, OptionType

_IST = timezone(timedelta(hours=5, minutes=30))

_ENGINE_WINDOW = 240     # what production hands the engine
_SCAN_WINDOW = 90        # what the scanner requires
_HORIZON = 30            # forward bars the outcome is measured over
_STOP_ATR = 0.8
_RR = 1.2

# Modelled chain, for reproducing the engine's decision only (see docstring).
_MODEL_DELTA = 0.6
_MODEL_TIME_VALUE = 20.0
_MODEL_FLOOR = 1.0


def _premium(spot: float, strike: float, side: OptionType) -> float:
    signed = (spot - strike) if side == OptionType.CALL else (strike - spot)
    return max(_MODEL_FLOOR, _MODEL_TIME_VALUE + _MODEL_DELTA * signed)


def _chain(root: str, spot: float, step: float) -> list[OptionQuote]:
    atm = round(spot / step) * step
    out: list[OptionQuote] = []
    for k in (atm - step, atm, atm + step):
        for side in (OptionType.CALL, OptionType.PUT):
            out.append(OptionQuote(
                symbol=f"{root}{int(k)}{side.value}", strike=float(k),
                option_type=side, premium=round(_premium(spot, k, side), 2),
                iv=0.0,
                delta=_MODEL_DELTA if side == OptionType.CALL else -_MODEL_DELTA,
                gamma=0.0, theta=0.0, vega=0.0, oi=0, oi_change=0, volume=0,
            ))
    return out


def _atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(len(candles) - period, len(candles)):
        prev = candles[i - 1].close
        trs.append(max(candles[i].high - candles[i].low,
                       abs(candles[i].high - prev), abs(candles[i].low - prev)))
    return sum(trs) / len(trs) if trs else None


def _pct(a: float, b: float) -> float:
    return 0.0 if not b else round(100.0 * (a - b) / b, 4)


def outcome(fwd: list[Candle], entry: float, stop_dist: float, up: bool) -> dict:
    """Target-before-stop / MFE / MAE in R, on the forward bars only.

    A bar that touches both levels counts as a STOP: 1-minute OHLC cannot say
    which came first, and assuming the win is how a backtest flatters itself.
    """
    target = entry + _RR * stop_dist * (1 if up else -1)
    stop = entry - stop_dist * (1 if up else -1)
    mfe = mae = 0.0
    result = "OPEN"
    bars = 0
    for c in fwd:
        bars += 1
        mfe = max(mfe, (c.high - entry) if up else (entry - c.low))
        mae = max(mae, (entry - c.low) if up else (c.high - entry))
        hit_t = (c.high >= target) if up else (c.low <= target)
        hit_s = (c.low <= stop) if up else (c.high >= stop)
        if hit_t and hit_s:
            result = "STOP"
            break
        if hit_t:
            result = "TARGET"
            break
        if hit_s:
            result = "STOP"
            break
    return {
        "result": result,
        "r": round(_RR if result == "TARGET" else -1.0 if result == "STOP"
                   else (mfe - mae) / stop_dist, 4),
        "mfe_r": round(mfe / stop_dist, 4),
        "mae_r": round(mae / stop_dist, 4),
        "bars_to_resolve": bars,
    }


def features(window: list[Candle], atr14: float, snap, score, res) -> dict:
    """Causal features. ``window`` ends at t; nothing after t is visible."""
    c = window[-1]
    closes = [x.close for x in window]
    vols = [x.volume for x in window]
    px = c.close

    def ret(n: int) -> float:
        return _pct(px, closes[-1 - n]) if len(closes) > n else 0.0

    rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(len(closes) - 30, len(closes)) if closes[i - 1]]
    rvol = statistics.pstdev(rets) * 100.0 if len(rets) > 2 else 0.0
    base_rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
                 for i in range(max(1, len(closes) - 120), len(closes)) if closes[i - 1]]
    base_rvol = statistics.pstdev(base_rets) * 100.0 if len(base_rets) > 2 else 0.0

    atr50 = _atr(window, 50) or atr14
    v20 = statistics.mean(vols[-20:]) if len(vols) >= 20 else (vols[-1] or 0.0)
    v5 = statistics.mean(vols[-5:]) if len(vols) >= 5 else (vols[-1] or 0.0)

    hi20 = max(x.high for x in window[-21:-1]) if len(window) > 21 else c.high
    lo20 = min(x.low for x in window[-21:-1]) if len(window) > 21 else c.low
    swing_lo = min(x.low for x in window[-30:])
    swing_hi = max(x.high for x in window[-30:])

    signs = [1 if closes[i] > closes[i - 1] else -1
             for i in range(len(closes) - 10, len(closes))]
    persistence = abs(sum(signs)) / 10.0

    # 90-bar VWAP: a session VWAP needs session boundaries the archive does not
    # carry, so this is a rolling proxy and is named as one.
    tv = sum(x.close * (x.volume or 1.0) for x in window[-90:])
    tq = sum((x.volume or 1.0) for x in window[-90:])
    vwap90 = tv / tq if tq else px

    dt = datetime.fromtimestamp(c.time, _IST)
    return {
        "ret_1": ret(1), "ret_3": ret(3), "ret_5": ret(5),
        "ret_15": ret(15), "ret_30": ret(30),
        "atr_pct": round(100.0 * atr14 / px, 4) if px else 0.0,
        "atr_expansion": round(atr14 / atr50, 4) if atr50 else 1.0,
        "rvol_30": round(rvol, 4),
        "rvol_vs_base": round(rvol / base_rvol, 4) if base_rvol else 1.0,
        "vwap_dist_atr": round((px - vwap90) / atr14, 4) if atr14 else 0.0,
        "ema_spread_atr": round(((snap.ema9 or px) - (snap.ema20 or px)) / atr14, 4)
                          if atr14 else 0.0,
        "ema50_dist_atr": round((px - (snap.ema50 or px)) / atr14, 4) if atr14 else 0.0,
        "adx": round(snap.adx, 2) if snap.adx is not None else None,
        "rsi": round(snap.rsi, 2) if snap.rsi is not None else None,
        "macd_hist": round(snap.macd_hist, 4) if snap.macd_hist is not None else None,
        "rel_volume": round((c.volume or 0.0) / v20, 4) if v20 else 1.0,
        "vol_accel": round(v5 / v20, 4) if v20 else 1.0,
        "breakout_atr": round((px - hi20) / atr14, 4) if atr14 else 0.0,
        "breakdown_atr": round((lo20 - px) / atr14, 4) if atr14 else 0.0,
        "ext_from_swing_lo_atr": round((px - swing_lo) / atr14, 4) if atr14 else 0.0,
        "ext_from_swing_hi_atr": round((swing_hi - px) / atr14, 4) if atr14 else 0.0,
        "persistence_10": round(persistence, 3),
        "supertrend_dir": snap.supertrend_dir,
        "tod_min": dt.hour * 60 + dt.minute,
        "dow": dt.weekday(),
        "scan_score": score,
        "scan_state": res.classification,
        "scan_verdict": res.verdict,
        "scan_dir": res.direction,
    }


def vol_bucket(atr_pct: float, ref: list[float]) -> str:
    """Volatility regime as a quantile of the instrument's own ATR%, so it is
    comparable across instruments instead of being an absolute points threshold."""
    if not ref:
        return "UNKNOWN"
    q1, q2, q3 = (ref[int(len(ref) * f)] for f in (0.25, 0.5, 0.75))
    if atr_pct <= q1:
        return "VOL_LOW"
    if atr_pct <= q2:
        return "VOL_NORMAL"
    if atr_pct <= q3:
        return "VOL_ELEVATED"
    return "VOL_HIGH"


def tod_bucket(minute: int, mcx: bool) -> str:
    if mcx:
        if minute < 11 * 60:
            return "MORNING"
        if minute < 15 * 60:
            return "MIDDAY"
        if minute < 18 * 60 + 30:
            return "US_PRE"
        if minute < 21 * 60:
            return "US_OPEN"
        return "LATE"
    if minute < 10 * 60:
        return "OPEN"
    if minute < 12 * 60:
        return "MORNING"
    if minute < 14 * 60:
        return "MIDDAY"
    return "CLOSE"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--bars", type=int, default=0, help="0 = all cached bars")
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if not candles:
        raise SystemExit(f"no cached candles at {path}")
    if args.bars:
        candles = candles[-args.bars:]

    spec = REGISTRY.get(args.instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else args.instrument
    mcx = bool(spec and spec.exchange == "MCX")

    # ATR% reference distribution for the volatility buckets, built from a
    # coarse first pass over the SAME history (it is a description of the
    # instrument, not a forward-looking quantity).
    ref: list[float] = []
    for i in range(_ENGINE_WINDOW, len(candles) - _HORIZON, max(1, args.stride * 20)):
        a = _atr(candles[i - 60:i + 1])
        if a and candles[i].close:
            ref.append(100.0 * a / candles[i].close)
    ref.sort()

    out_path = os.path.expanduser(args.out)
    n = kept = 0
    t0 = time.time()
    with open(out_path, "w") as fh:
        for i in range(_ENGINE_WINDOW, len(candles) - _HORIZON - 1, args.stride):
            n += 1
            ew = candles[i - _ENGINE_WINDOW:i + 1]
            sw = candles[i - _SCAN_WINDOW:i + 1]
            spot = float(ew[-1].close)
            a = _atr(ew)
            if not a or a <= 0 or not spot:
                continue

            chain = _chain(root, spot, step)
            snap = compute_indicators(ew, chain)
            status = classify_market(snap, False)
            dec, _ = decide(ew, chain, snap, 0.0, False, status, False, None, 1.0,
                            spot=spot)
            res = scanner.scan_one(scanner.ScanInput(
                instrument=args.instrument, ltp=spot, candles=tuple(sw),
                # Replay candles carry no tick age; freshness is declared, not
                # inferred, and the report says so.
                data_age_ms=0.0, freshness="FRESH", data_quality_score=100.0,
            ))

            f = features(ew, a, snap, res.opportunity_score, res)
            fwd = candles[i + 1:i + 1 + _HORIZON]
            up = outcome(fwd, spot, _STOP_ATR * a, True)
            dn = outcome(fwd, spot, _STOP_ATR * a, False)

            eng_side = dec.option_type.value if dec.option_type else None
            row = {
                "i": i, "ts": ew[-1].time, "spot": spot, "atr": round(a, 4),
                "trend_regime": status.value,
                "vol_regime": vol_bucket(f["atr_pct"], ref),
                "tod": tod_bucket(f["tod_min"], mcx),
                "engine_signal": dec.signal.value,
                "engine_side": eng_side,
                "engine_conf": (dec.gates.confidence if dec.gates else None),
                "engine_blocker": (dec.gates.primary_blocker if dec.gates else None),
                "features": f,
                "CE": up,     # a CE profits from the underlying going UP
                "PE": dn,
            }
            fh.write(json.dumps(row) + "\n")
            kept += 1

    el = time.time() - t0
    print(json.dumps({
        "instrument": args.instrument,
        "bars_available": len(candles),
        "bars_sampled": n,
        "rows_written": kept,
        "stride": args.stride,
        "horizon_bars": _HORIZON,
        "levels": {"stop_atr": _STOP_ATR, "reward_risk": _RR},
        "outcome_basis": "UNDERLYING move (no premium, no theta, no spread)",
        "engine_chain": "MODELLED 0.6-delta (used only to reproduce the decision)",
        "seconds": round(el, 1),
        "out": out_path,
    }, indent=2))


if __name__ == "__main__":
    main()
