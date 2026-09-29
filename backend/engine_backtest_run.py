"""Runner for the FROZEN-engine (Quantum-Signal gated) backtest — READ-ONLY.

    .venv/bin/python engine_backtest_run.py --instrument NIFTY --adx-min 20
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from app.backtest import angel_history as ah
from app.backtest import engine_backtest as eb
from app.config import settings
from app.market.instruments import REGISTRY


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NIFTY")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--adx-min", type=float, default=20.0)
    ap.add_argument("--target-idx", type=int, default=0, help="0=T1,1=T2,2=T3 ATR target")
    ap.add_argument("--limit", type=int, default=0, help="use only the first N candles (0=all)")
    args = ap.parse_args()

    if REGISTRY.get(args.instrument) is None:
        raise SystemExit(f"Unknown instrument {args.instrument}")
    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if args.limit:
        candles = candles[: args.limit]
    print(f"Loaded {len(candles):,} candles from {path}")
    if len(candles) < 300:
        raise SystemExit("Not enough candles.")

    res = eb.run(candles, args.instrument, adx_min=args.adx_min, target_idx=args.target_idx)
    s = res.stats()

    first = dt.datetime.fromtimestamp(s["first_ts"]).strftime("%Y-%m-%d")
    last = dt.datetime.fromtimestamp(s["last_ts"]).strftime("%Y-%m-%d")
    u = s["underlying_points"]
    r = s["r_multiple"]
    print("\n" + "=" * 64)
    print(f" FROZEN-ENGINE (Quantum-Signal gated) — {s['instrument']}")
    print(f" {first} .. {last}   bars={s['bars']:,}   adx_min={args.adx_min}")
    print("=" * 64)
    print(f" bars evaluated : {s['evaluated']:,}")
    print(f" raw BUY signals: {s['buys_seen']:,}")
    print(f" passed gate    : {s['actionable']:,}  (actionable trades)")
    print(f" trades taken   : {s['num_trades']:,}   avg hold {s['avg_hold_min']} min")
    print(f" exit reasons   : {s['exit_reasons']}")
    print("\n --- UNDERLYING points (REAL, measured) ---")
    print(f"   win rate      : {u.get('win_rate_pct')}%  ({u.get('wins')}W / {u.get('losses')}L)")
    print(f"   avg / median  : {u.get('avg')} / {u.get('median')} pts")
    print(f"   best / worst  : {u.get('best')} / {u.get('worst')} pts")
    print(f"   total         : {u.get('sum')} pts")
    print(f"   profit factor : {u.get('profit_factor')}")
    print("\n --- R-multiple (points / risk) ---")
    print(f"   avg R         : {r.get('avg')}   PF {r.get('profit_factor')}")
    print("\n --- WALK-FORWARD: points / PF per calendar year ---")
    for yr, ys in s.get("yearly", {}).items():
        print(
            f"   {yr}: {ys.get('n'):>5} trades  win {ys.get('win_rate_pct')}%  "
            f"total {ys.get('sum')} pts  PF {ys.get('profit_factor')}"
        )
    print("=" * 64)

    out = path.replace(".jsonl", "_engine_backtest.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2)
    print(f"Full stats JSON -> {out}")


if __name__ == "__main__":
    main()
