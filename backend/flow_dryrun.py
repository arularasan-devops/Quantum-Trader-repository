"""Research-only 5-year dry run for the Candle-Flow strategy variants.

Replays the REAL app.execution.flow.detect logic over the cached 1-minute
history for each instrument under two configs:

  OLD  — legacy Flow (no strength band, flips on decisive opposite candles)
  NEW  — sticky side (switch only on a strong confirmed reversal) + strength
         band (enter/hold >= floor, exit when it fades) + pattern-confirmed entry

Reports trades, win rate, avg win/loss, worst, total modelled points/₹, profit
factor and max drawdown for each. MODELLED premium (no theta/spread/slippage) —
directional guidance only, NOT a measured option P&L and NOT a profit guarantee.

Offline / read-only. Places no orders and never touches the live engine.
"""
from __future__ import annotations

import argparse

from app.backtest import angel_history, flow_backtest
from app.config import settings
from app.market.instruments import REGISTRY


def _drawdown(equity_steps: list[float]) -> float:
    """Max peak-to-trough drop of the cumulative equity curve."""
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for step in equity_steps:
        cum += step
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


def _report(name: str, res, lot_size: int) -> None:
    trades = res.trades
    prem = [t.premium_points for t in trades]
    wins = [p for p in prem if p > 0]
    losses = [p for p in prem if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
    n = len(prem) or 1
    total_pts = sum(prem)
    dd_pts = _drawdown(prem)
    print(f"\n=== {name} ===")
    print(f"  trades           : {len(prem)}   switches: {res.switches if hasattr(res, 'switches') else '-'}")
    print(f"  win rate         : {round(len(wins) / n * 100, 1)}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  avg win / loss   : +{(gross_win / max(1, len(wins))):.1f} / -{(gross_loss / max(1, len(losses))):.1f} pts")
    print(f"  worst trade      : {min(prem) if prem else 0:.1f} pts")
    print(f"  total (MODELLED) : {total_pts:.0f} pts  =  ₹{total_pts * lot_size:,.0f} / lot")
    print(f"  profit factor    : {pf}")
    print(f"  max drawdown     : {dd_pts:.0f} pts  =  ₹{dd_pts * lot_size:,.0f} / lot")


def _run_config(candles, instrument, *, floor, strong_only, require_pattern):
    settings.flow_enabled = True
    settings.flow_strength_floor = floor
    settings.flow_switch_strong_only = strong_only
    settings.flow_require_pattern = require_pattern
    return flow_backtest.run(candles, instrument)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NIFTY")
    ap.add_argument("--interval", default="ONE_MINUTE")
    args = ap.parse_args()

    inst = args.instrument.upper()
    path = angel_history.cache_path(settings.data_dir, inst, args.interval)
    candles = angel_history.load_candles(path)
    if not candles:
        print(f"No cached candles at {path}")
        return
    spec = REGISTRY.get(inst)
    lot_size = int(spec.lot_size) if spec else 1
    span0 = candles[0].time
    span1 = candles[-1].time
    print(f"{inst}: {len(candles):,} bars  ({span0} .. {span1})  lot={lot_size}")
    print("MODELLED premium (delta only, no theta/spread/slippage) — directional, NOT a profit guarantee.")

    old = _run_config(candles, inst, floor=0.0, strong_only=False, require_pattern=False)
    _report("OLD Flow (legacy)", old, lot_size)

    new = _run_config(candles, inst, floor=70.0, strong_only=True, require_pattern=True)
    _report("NEW Flow (sticky side + strength band 70 + pattern)", new, lot_size)


if __name__ == "__main__":
    main()
