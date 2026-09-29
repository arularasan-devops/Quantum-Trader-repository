"""READ-ONLY A/B backtest for the new early-turn/reversal entry + R:R gate.

Replays the REAL decision pipeline (compute_indicators -> classify_market ->
decide) over cached 1-min candles and takes a trade whenever the live dashboard
would show an ACTIONABLE BUY:

  * TREND entry  -> the existing Quantum-Signal gate agrees (regime/ADX/htf/bias)
  * REVERSAL     -> decide() tags entry_trigger == "REVERSAL" (new early-turn)

Each trade is managed on the UNDERLYING with the engine's regime ATR plan, so
the win-rate / profit-factor are MEASURED underlying points (no option theta or
spread modelled). Compares BASELINE (reversal off, no R:R gate) vs NEW.

    .venv/bin/python reversal_backtest.py --instrument NIFTY --limit 150000
"""
from __future__ import annotations

import argparse

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


def run(candles, instrument, *, adx_min=20.0, tail=180, reversal, rr_gate, veto):
    settings.reversal_entry_enabled = reversal
    settings.min_reward_risk = rr_gate
    settings.veto_premium_explosion = veto

    spec = REGISTRY.get(instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else instrument

    n = len(candles)
    i = tail
    trend_pts, rev_pts = [], []
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

        is_rev = (dec.entry_trigger or "").upper() == "REVERSAL"
        if is_rev:
            actionable = True
        else:
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
        direction = 1.0 if side == "CE" else -1.0
        entry = spot
        stop = entry - direction * stop_mult * atr
        target = entry + direction * tgt_mults[0] * atr
        max_hold = dec.expected_holding_minutes or 30

        exit_spot = entry
        j = i + 1
        while j < n:
            cj = candles[j]
            if direction > 0:
                if cj.low <= stop:
                    exit_spot = stop
                    break
                if cj.high >= target:
                    exit_spot = target
                    break
            else:
                if cj.high >= stop:
                    exit_spot = stop
                    break
                if cj.low <= target:
                    exit_spot = target
                    break
            if (cj.time - bar.time) >= max_hold * 60:
                exit_spot = cj.close
                break
            j += 1
        else:
            exit_spot = candles[-1].close
            j = n - 1

        pts = direction * (exit_spot - entry)
        (rev_pts if is_rev else trend_pts).append(pts)
        i = j + 1

    return trend_pts, rev_pts


def _report(label, trend_pts, rev_pts):
    allp = trend_pts + rev_pts
    def line(name, v):
        if not v:
            return f"   {name:<10}: (no trades)"
        wins = sum(1 for x in v if x > 0)
        return (f"   {name:<10}: {len(v):>5} trades  win {round(wins/len(v)*100,1)}%  "
                f"total {round(sum(v),1)} pts  PF {_pf(v)}")
    print(f"\n== {label} ==")
    print(line("ALL", allp))
    print(line("trend", trend_pts))
    print(line("reversal", rev_pts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NIFTY")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--limit", type=int, default=0, help="use only the LAST N candles")
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if args.limit and len(candles) > args.limit:
        candles = candles[-args.limit:]
    print(f"{args.instrument}: {len(candles):,} candles")

    t0, r0 = run(candles, args.instrument, reversal=False, rr_gate=0.0, veto=False)
    _report("BASELINE (reversal off, no R:R gate)", t0, r0)

    t1, r1 = run(candles, args.instrument, reversal=True, rr_gate=1.2, veto=True)
    _report("NEW (reversal on, R:R>=1.2, premium-veto)", t1, r1)


if __name__ == "__main__":
    main()
