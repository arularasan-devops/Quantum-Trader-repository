"""Offline Candle-Flow backtest runner (READ-ONLY).

Usage:
  # download ~1y of 1-min CRUDEOIL futures and run the Flow backtest
  .venv/bin/python backtest_run.py --instrument CRUDEOIL --months 12 --download

  # re-run on already-cached data (no API calls)
  .venv/bin/python backtest_run.py --instrument CRUDEOIL

Credentials (for --download) come from SMARTAPI_* environment variables.
Places no orders; never touches the live engine.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from app.backtest import angel_history as ah
from app.backtest import flow_backtest as fb
from app.config import settings
from app.market.instruments import REGISTRY


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--download", action="store_true", help="fetch from Angel (else use cache)")
    args = ap.parse_args()

    spec = REGISTRY.get(args.instrument)
    if spec is None:
        raise SystemExit(f"Unknown instrument {args.instrument}")
    exchange = spec.exchange
    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)

    if args.download:
        print(f"Logging in to Angel to download {args.instrument} {args.interval}...")
        smart = ah.login_smart()
        token, symbol, expiry = ah.front_future_token(args.instrument, exchange)
        print(f"Front contract: {symbol} token={token} expiry={expiry}")
        end = dt.date.today()
        start = end - dt.timedelta(days=int(args.months * 30.5))
        print(f"Downloading {start} .. {end} (chunked, rate-limited)...")
        candles = ah.download_candles(smart, token, exchange, args.interval, start, end)
        ah.save_candles(path, candles)
        print(f"Saved {len(candles)} candles -> {path}")
    else:
        candles = ah.load_candles(path)
        print(f"Loaded {len(candles)} cached candles from {path}")

    if len(candles) < 50:
        raise SystemExit("Not enough candles to backtest.")

    res = fb.run(candles, args.instrument, args.interval)
    stats = res.stats()

    first = dt.datetime.fromtimestamp(stats["first_ts"]).strftime("%Y-%m-%d")
    last = dt.datetime.fromtimestamp(stats["last_ts"]).strftime("%Y-%m-%d")
    print("\n" + "=" * 62)
    print(f" CANDLE-FLOW BACKTEST — {stats['instrument']} ({stats['interval']})")
    print(f" {first} .. {last}   bars={stats['bars']}")
    print("=" * 62)
    print(f" config: {stats['config']}")
    print(f" trades taken : {stats['num_trades']}   (switches: {stats['switches']})")
    print(f" avg hold     : {stats['avg_hold_min']} min")
    u = stats["underlying_points"]
    print("\n --- UNDERLYING points (REAL, measured futures move) ---")
    print(f"   win rate      : {u.get('win_rate_pct')}%  ({u.get('wins')}W / {u.get('losses')}L)")
    print(f"   avg / median  : {u.get('avg')} / {u.get('median')} pts")
    print(f"   best / worst  : {u.get('best')} / {u.get('worst')} pts")
    print(f"   total         : {u.get('sum')} pts")
    print(f"   profit factor : {u.get('profit_factor')}")
    m = stats["premium_points_MODELLED"]
    print("\n --- OPTION premium points (MODELLED — not real, illustration) ---")
    print(f"   win rate      : {m.get('win_rate_pct')}%")
    print(f"   avg / median  : {m.get('avg')} / {m.get('median')} pts")
    print(f"   profit factor : {m.get('profit_factor')}")
    print(f"\n {stats['model_note']}")
    print("=" * 62)

    out = ah.cache_path(settings.data_dir, args.instrument, args.interval).replace(
        ".jsonl", "_flow_backtest.json"
    )
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2)
    print(f"Full stats JSON -> {out}")


if __name__ == "__main__":
    main()
