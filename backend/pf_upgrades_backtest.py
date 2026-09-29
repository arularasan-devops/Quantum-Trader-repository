"""READ-ONLY backtest for the three profit-factor upgrades.

Replays the REAL decision pipeline (compute_indicators -> classify_market ->
decide) exactly like the live dashboard, then measures each upgrade separately
so only what MEASURABLY helps is kept:

1. TIME-OF-DAY  -- profit factor is reported per entry-hour bucket, so the
   filter is derived from the data instead of guessed.
2. SCALE-OUT    -- take half off at T1 and ride the rest to T2 with the stop
   moved to break-even, vs the baseline "all out at T1".
3. ALLOWLIST    -- per-instrument totals, so weak instruments can be dropped.

Outcomes are MEASURED underlying points (no option theta/spread modelled).

    .venv/bin/python pf_upgrades_backtest.py --instrument NIFTY --limit 60000
"""
from __future__ import annotations

import argparse
import time as _time
from collections import defaultdict

from app.backtest import angel_history as ah
from app.backtest.flow_backtest import _build_chain
from app.config import settings
from app.engine.decision import (
    _atr_multiples,
    classify_market,
    compute_indicators,
    decide,
)
from app.engine.signal_gate import _timeframe_bias
from app.market.instruments import REGISTRY
from app.models import MarketStatus, Signal

_TREND = {MarketStatus.TRENDING, MarketStatus.BREAKOUT}


def _pf(vals):
    win = sum(v for v in vals if v > 0)
    loss = -sum(v for v in vals if v <= 0)
    return round(win / loss, 3) if loss > 0 else None


def _ist_hour(ts: int) -> int:
    """Entry hour in IST (the cache stores epoch seconds)."""
    return _time.gmtime(ts + 19800).tm_hour


def collect(candles, instrument, *, adx_min=20.0, tail=180):
    """Walk the data once and record every actionable BUY with BOTH exit models."""
    spec = REGISTRY.get(instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else instrument

    n = len(candles)
    i = tail
    trades: list[dict] = []
    while i < n - 1:
        window = candles[i + 1 - tail: i + 1]
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
        actionable = (
            status in _TREND
            and adx is not None and adx >= adx_min
            and exp_move is not None and exp_move > 0
            and ((side == "CE" and htf == "UP") or (side == "PE" and htf == "DOWN"))
            and ((side == "CE" and bias15 == "UP") or (side == "PE" and bias15 == "DOWN"))
        )
        if not actionable:
            i += 1
            continue

        atr = snap.atr or (0.004 * spot)
        stop_mult, tgt_mults = _atr_multiples(status)
        d = 1.0 if side == "CE" else -1.0
        entry = spot
        stop = entry - d * stop_mult * atr
        t1 = entry + d * tgt_mults[0] * atr
        t2 = entry + d * tgt_mults[1] * atr
        max_hold = dec.expected_holding_minutes or 30

        # --- baseline: all out at T1, stop at the ATR stop --------------------
        base_pnl = None
        # --- scale-out: half at T1, remainder to T2 with stop -> break-even ---
        scaled_pnl = None
        half_booked = False
        j = i + 1
        last = j
        while j < n:
            cj = candles[j]
            last = j
            hit_stop = (cj.low <= stop) if d > 0 else (cj.high >= stop)
            hit_t1 = (cj.high >= t1) if d > 0 else (cj.low <= t1)
            hit_t2 = (cj.high >= t2) if d > 0 else (cj.low <= t2)

            if base_pnl is None:
                if hit_stop:
                    base_pnl = d * (stop - entry)
                elif hit_t1:
                    base_pnl = d * (t1 - entry)

            if scaled_pnl is None:
                if not half_booked:
                    if hit_stop:
                        scaled_pnl = d * (stop - entry)
                    elif hit_t1:
                        half_booked = True
                        stop = entry  # runner protected at break-even
                else:
                    if hit_t2:
                        scaled_pnl = 0.5 * d * (t1 - entry) + 0.5 * d * (t2 - entry)
                    elif hit_stop:  # stop is now break-even
                        scaled_pnl = 0.5 * d * (t1 - entry)

            if base_pnl is not None and scaled_pnl is not None:
                break
            if (cj.time - bar.time) >= max_hold * 60:
                close = cj.close
                if base_pnl is None:
                    base_pnl = d * (close - entry)
                if scaled_pnl is None:
                    scaled_pnl = (
                        0.5 * d * (t1 - entry) + 0.5 * d * (close - entry)
                        if half_booked else d * (close - entry)
                    )
                break
            j += 1
        if base_pnl is None:
            base_pnl = d * (candles[-1].close - entry)
        if scaled_pnl is None:
            scaled_pnl = d * (candles[-1].close - entry)

        trades.append({
            "hour": _ist_hour(bar.time),
            "base": base_pnl,
            "scaled": scaled_pnl,
        })
        i = last + 1

    return trades


def _line(name, vals):
    if not vals:
        return f"   {name:<16}: (no trades)"
    wins = sum(1 for v in vals if v > 0)
    return (f"   {name:<16}: {len(vals):>5} trades  win {round(wins / len(vals) * 100, 1):>5}%  "
            f"total {round(sum(vals), 1):>9} pts  PF {_pf(vals)}")


def report(instrument, trades):
    print(f"\n########## {instrument} — {len(trades)} actionable BUYs ##########")

    print("\n-- 1. EXIT MODEL --")
    print(_line("baseline T1", [t["base"] for t in trades]))
    print(_line("scale-out", [t["scaled"] for t in trades]))

    print("\n-- 2. BY ENTRY HOUR (IST) --")
    by_hour = defaultdict(list)
    for t in trades:
        by_hour[t["hour"]].append(t["base"])
    for h in sorted(by_hour):
        print(_line(f"{h:02d}:00", by_hour[h]))

    losing_hours = [h for h, v in by_hour.items() if (_pf(v) or 0) < 1.0]
    kept = [t["base"] for t in trades if t["hour"] not in losing_hours]
    print(f"\n-- 3. TIME FILTER, IN-SAMPLE (drop hours with PF<1: {sorted(losing_hours)}) --")
    print("   WARNING: hours chosen on the SAME data they are scored on (overfit).")
    print(_line("filtered", kept))
    kept_scaled = [t["scaled"] for t in trades if t["hour"] not in losing_hours]
    print(_line("filtered+scaled", kept_scaled))

    # WALK-FORWARD: choose the bad hours on the FIRST half only, then score them
    # on the SECOND half, which the choice never saw. This is the only number
    # that says whether the time filter is a real edge or curve-fitting.
    half = len(trades) // 2
    train, test = trades[:half], trades[half:]
    train_hours = defaultdict(list)
    for t in train:
        train_hours[t["hour"]].append(t["base"])
    drop = {h for h, v in train_hours.items() if (_pf(v) or 0) < 1.0}
    print(f"\n-- 4. TIME FILTER, WALK-FORWARD (hours {sorted(drop)} chosen on 1st half) --")
    print(_line("2nd half raw", [t["base"] for t in test]))
    print(_line("2nd half filt", [t["base"] for t in test if t["hour"] not in drop]))
    print(_line("2nd half f+sc", [t["scaled"] for t in test if t["hour"] not in drop]))
    print(_line("2nd half scale", [t["scaled"] for t in test]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NIFTY")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    settings.reversal_entry_enabled = False
    settings.ignition_entry_enabled = False
    settings.min_reward_risk = 1.2
    settings.veto_premium_explosion = True

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if args.limit and len(candles) > args.limit:
        candles = candles[-args.limit:]
    print(f"{args.instrument}: {len(candles):,} candles")
    report(args.instrument, collect(candles, args.instrument))


if __name__ == "__main__":
    main()
