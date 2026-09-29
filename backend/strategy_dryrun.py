"""5-year DRY RUN comparing exit/stop strategies on cached 1-min history.

READ-ONLY. No API calls, no orders. Reuses the FROZEN engine's actionable-BUY
detection (same gate as the live dashboard), then manages each trade tick-by-tick
on the underlying under several strategies so we can measure which gives MORE
profit / LESS loss:

  V0 baseline     : regime ATR stop + T1 target (current behaviour)
  V1 tight-stop   : tight ATR stop + T1 target
  V2 conf-tiered  : STRONG confidence -> ride with ratcheting trail (let it run);
                    WEAK confidence   -> quick-scalp (bank a few points, no wait);
                    tight stop + move to break-even once green
  V3 v-tight+BE   : very tight stop, break-even after green, ride winners

Option P&L is MODELLED from the underlying move (delta approximation, no theta),
translated to rupees per 1 lot so the loss size is comparable to real capital.
It is directional guidance, NOT an exact options backtest.
"""
from __future__ import annotations

import argparse
import datetime as dt
import statistics

from app.backtest import angel_history as ah
from app.backtest.flow_backtest import _build_chain
from app.config import settings
from app.engine.decision import _atr_multiples, classify_market, compute_indicators, decide
from app.engine.signal_gate import _timeframe_bias
from app.market.instruments import REGISTRY
from app.models import MarketStatus, Signal

_TREND = {MarketStatus.TRENDING, MarketStatus.BREAKOUT}
_MODEL_DELTA = 0.6  # premium points per favourable underlying point (ATM approx)


def _signals(candles, instrument, adx_min, tail=180):
    """Yield actionable entries (i, side, entry_spot, atr, regime, confidence,
    stop_mult, tgt_mults, max_hold) using the SAME gate as engine_backtest."""
    spec = REGISTRY.get(instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else instrument
    n = len(candles)
    i = tail
    out = []
    while i < n - 1:
        window = candles[i + 1 - tail : i + 1]
        bar = window[-1]
        spot = bar.close
        chain = _build_chain(root, spot, step, None)
        snap = compute_indicators(window, chain, 0.0)
        status = classify_market(snap, False)
        dec, _ = decide(window, chain, snap, 0.0, False, status, False, None, 1.0, spot)
        side = dec.option_type.value if dec.option_type else None
        if dec.signal != Signal.BUY or side is None:
            i += 1
            continue
        htf = (dec.htf_trend or "").upper()
        bias15 = _timeframe_bias(window, 15)
        adx = snap.adx
        exp_move = dec.expected_move_points
        gate_ok = (
            status in _TREND
            and adx is not None and adx >= adx_min
            and exp_move is not None and exp_move > 0
            and ((side == "CE" and htf == "UP") or (side == "PE" and htf == "DOWN"))
            and ((side == "CE" and bias15 == "UP") or (side == "PE" and bias15 == "DOWN"))
        )
        if not gate_ok:
            i += 1
            continue
        atr = snap.atr or (0.004 * spot)
        stop_mult, tgt_mults = _atr_multiples(status)
        max_hold = dec.expected_holding_minutes or 30
        out.append((i, side, spot, atr, status.value, round(dec.confidence, 1),
                    stop_mult, tgt_mults, max_hold))
        i += 1  # allow re-eval; the manager decides no-overlap by skipping
    return out


def _manage(candles, sig, strat):
    """Simulate one trade from a signal under a strategy dict. Returns
    (points, exit_reason, hold_min, exit_idx)."""
    i, side, entry, atr, regime, conf, stop_mult, tgt_mults, max_hold = sig
    n = len(candles)
    d = 1.0 if side == "CE" else -1.0

    # stop distance
    sm = strat.get("stop_mult")
    stop_dist = (sm if sm is not None else stop_mult) * atr
    stop = entry - d * stop_dist
    # target (None => ride, no fixed target)
    tgt_idx = strat.get("target_idx", 0)
    target = None if strat.get("ride") else entry + d * tgt_mults[tgt_idx] * atr

    strong = conf >= strat.get("strong_conf", 999)
    ride = strat.get("ride_if_strong", False) and strong
    scalp = strat.get("scalp_if_weak", False) and not strong
    trail_give = strat.get("trail_giveback_atr", 0.6) * atr
    scalp_pts = strat.get("scalp_atr", 0.4) * atr
    be = strat.get("breakeven_after_green", False)

    peak = entry
    exit_spot, reason, exit_ts = entry, "hold_end", candles[i].time
    j = i + 1
    armed = False
    while j < n:
        cj = candles[j]
        # update peak in the favourable direction
        cur_fav = cj.high if d > 0 else cj.low
        if d * (cur_fav - peak) > 0:
            peak = cur_fav
        run = d * (peak - entry)  # favourable run in points (>=0)

        # break-even ratchet once green enough
        if be and run >= trail_give and d * (entry - stop) > 0:
            stop = entry  # move stop to break-even

        # 1) hard stop (adverse)
        adverse = cj.low if d > 0 else cj.high
        if d * (adverse - stop) <= 0:
            exit_spot, reason, exit_ts = stop, "stop", cj.time
            break
        # 2) fixed target
        if target is not None and d * ((cj.high if d > 0 else cj.low) - target) >= 0:
            exit_spot, reason, exit_ts = target, "target", cj.time
            break
        # 3) quick-scalp for weak signals: bank small profit once green & ticks down
        if scalp and run >= scalp_pts:
            close_run = d * (cj.close - entry)
            if close_run < run:  # gave back off the peak intrabar
                exit_spot, reason, exit_ts = cj.close, "scalp", cj.time
                break
        # 4) ride with ratcheting trail for strong signals
        if ride:
            if run > 0:
                armed = True
            trail = peak - d * trail_give
            if armed and d * (cj.close - trail) <= 0 and d * (cj.close - entry) > 0:
                exit_spot, reason, exit_ts = cj.close, "trail", cj.time
                break
        # 5) time stop
        if (cj.time - candles[i].time) >= max_hold * 60:
            exit_spot, reason, exit_ts = cj.close, "hold_end", cj.time
            break
        j += 1
    else:
        exit_spot, exit_ts, j = candles[-1].close, candles[-1].time, n - 1

    points = d * (exit_spot - entry)
    hold = (exit_ts - candles[i].time) / 60.0
    return points, reason, hold, j


def _summ(trades, lot):
    if not trades:
        return None
    pts = [t[0] for t in trades]
    wins = [p for p in pts if p > 0]
    losses = [p for p in pts if p <= 0]
    gw, gl = sum(wins), -sum(losses)
    # rupee P&L per 1 lot via delta model
    rupees = [p * _MODEL_DELTA * lot for p in pts]
    # max drawdown on the cumulative rupee equity curve
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for r in rupees:
        eq += r
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {
        "trades": len(pts),
        "win_pct": round(len(wins) / len(pts) * 100, 1),
        "avg_win_pts": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss_pts": round(statistics.mean(losses), 2) if losses else 0.0,
        "worst_pts": round(min(pts), 1),
        "total_pts": round(sum(pts), 1),
        "pf": round(gw / gl, 2) if gl > 0 else None,
        "rupees_per_lot": round(sum(rupees), 0),
        "avg_loss_rupees": round((statistics.mean(losses) * _MODEL_DELTA * lot), 0) if losses else 0.0,
        "max_dd_rupees": round(mdd, 0),
    }


STRATS = {
    "V0 baseline (ATR stop+T1)": {"stop_mult": None, "target_idx": 0},
    "V1 tight stop 0.6ATR+T1": {"stop_mult": 0.6, "target_idx": 0},
    "V2 conf ride/scalp": {
        "stop_mult": 0.7, "ride": True, "ride_if_strong": True, "scalp_if_weak": True,
        "strong_conf": 80, "trail_giveback_atr": 0.6, "scalp_atr": 0.4,
        "breakeven_after_green": True,
    },
    "V3 v-tight 0.4ATR+BE+ride": {
        "stop_mult": 0.4, "ride": True, "ride_if_strong": True, "scalp_if_weak": True,
        "strong_conf": 75, "trail_giveback_atr": 0.5, "scalp_atr": 0.3,
        "breakeven_after_green": True,
    },
    # ATR stop kept WIDE (protect win rate) but ride winners instead of booking T1
    "V4 ATR stop + ride winners": {
        "stop_mult": None, "ride": True, "ride_if_strong": True,
        "strong_conf": 0, "trail_giveback_atr": 1.0,
    },
    # ATR stop + break-even once green + let it run to T2 (bigger winners, BE floor)
    "V5 ATR stop + BE + T2": {
        "stop_mult": None, "target_idx": 1, "breakeven_after_green": True,
        "trail_giveback_atr": 0.8,
    },
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--adx-min", type=float, default=20.0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    spec = REGISTRY.get(args.instrument)
    lot = spec.lot_size if spec else 1
    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if args.limit:
        candles = candles[-args.limit:]
    if len(candles) < 500:
        raise SystemExit("Not enough candles.")
    first = dt.datetime.fromtimestamp(candles[0].time).strftime("%Y-%m-%d")
    last = dt.datetime.fromtimestamp(candles[-1].time).strftime("%Y-%m-%d")
    print(f"{args.instrument}: {len(candles):,} bars  {first}..{last}  lot={lot}")
    print("Detecting actionable signals (same gate as live)…", flush=True)
    sigs = _signals(candles, args.instrument, args.adx_min)
    print(f"Actionable signals: {len(sigs):,}\n")

    hdr = (f"{'strategy':28}{'trades':>7}{'win%':>6}{'avgW':>7}{'avgL':>7}"
           f"{'worst':>7}{'totPts':>8}{'PF':>6}{'₹/lot':>10}{'avL₹':>8}{'maxDD₹':>9}")
    print(hdr)
    print("-" * len(hdr))
    for label, strat in STRATS.items():
        trades = []
        last_exit = -1
        for sig in sigs:
            if sig[0] <= last_exit:
                continue  # no overlapping trades
            pts, reason, hold, jexit = _manage(candles, sig, strat)
            trades.append((pts, reason, hold))
            last_exit = jexit
        s = _summ(trades, lot)
        if not s:
            continue
        print(f"{label:28}{s['trades']:>7}{s['win_pct']:>6}{s['avg_win_pts']:>7}"
              f"{s['avg_loss_pts']:>7}{s['worst_pts']:>7}{s['total_pts']:>8}"
              f"{str(s['pf']):>6}{s['rupees_per_lot']:>10,.0f}"
              f"{s['avg_loss_rupees']:>8,.0f}{s['max_dd_rupees']:>9,.0f}")
    print("\n₹ P&L is MODELLED (delta=0.6, no theta) per 1 lot — directional, not exact.")
    print("Goal: higher ₹/lot AND smaller avL₹ (avg loss) / maxDD. PF>1 = net edge.")


if __name__ == "__main__":
    main()
