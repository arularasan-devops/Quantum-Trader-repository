"""Compare raw Flow vs. trend-aligned Flow over the SAME cached history.

Tests whether only taking Flow entries that agree with a higher-timeframe EMA
trend turns the raw (edge-less) 1-min signal into something with a measured
edge. Read-only, cached data only.
"""
from __future__ import annotations

import argparse

from app.backtest import angel_history as ah
from app.backtest import flow_backtest as fb
from app.config import settings


def _row(label: str, stats: dict) -> str:
    u = stats["underlying_points"]
    return (
        f"{label:26} {u.get('n', 0):>7} {u.get('win_rate_pct'):>6} "
        f"{u.get('avg'):>7} {u.get('sum'):>10} {str(u.get('profit_factor')):>6}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    print(f"Loaded {len(candles)} cached candles from {path}\n")

    header = f"{'config':26} {'trades':>7} {'win%':>6} {'avg':>7} {'total':>10} {'PF':>6}"
    print(header)
    print("-" * len(header))
    raw = fb.run(candles, args.instrument, args.interval)
    print(_row("raw (no trend filter)", raw.stats()))
    for fast, slow in [(20, 60), (30, 120), (60, 240)]:
        ta = fb.run(
            candles, args.instrument, args.interval,
            trend_align=True, trend_fast=fast, trend_slow=slow,
        )
        print(_row(f"trend-aligned EMA{fast}/{slow}", ta.stats()))
    print("\nUnderlying move, measured. PF>1.0 = a real edge; still <1.0 = no edge "
          "even with the trend filter on this instrument/period.")


if __name__ == "__main__":
    main()
