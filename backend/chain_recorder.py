"""Live option-chain recorder (READ-ONLY snapshots).

Real option-premium history CANNOT be backfilled (expired weekly contracts
disappear from Angel), so the only honest way to build a measured option dataset
is to record the live chain forward from today. This script snapshots the ATM
option chain (premium, IV, OI, greeks) for the chosen instruments every
``--interval`` seconds during market hours and appends each snapshot as one JSON
line to ``data/chain_history/<INSTRUMENT>_chain.jsonl``.

It never places an order and never mutates the live app — it only reads quotes.

Usage:
    .venv/bin/python chain_recorder.py --instruments NIFTY,CRUDEOIL --interval 60
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time

from app.config import settings
from app.market.provider import build_provider

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _snapshot(provider) -> dict | None:
    provider.step()
    candles = provider.futures_candles(2)
    spot = candles[-1].close if candles else None
    chain = provider.option_chain()
    if not chain:
        return None
    return {
        "ts": int(time.time()),
        "spot": spot,
        "chain": [
            {
                "symbol": q.symbol,
                "strike": q.strike,
                "type": q.option_type.value,
                "premium": q.premium,
                "iv": q.iv,
                "delta": q.delta,
                "gamma": q.gamma,
                "theta": q.theta,
                "vega": q.vega,
                "oi": q.oi,
                "oi_change": q.oi_change,
                "volume": q.volume,
                # Top of book, kept as None when the feed carried none. Without
                # these the recording can never price a fill, which is the whole
                # reason it is being made: every cost stays a family median.
                "bid": q.bid,
                "ask": q.ask,
            }
            for q in chain
        ],
    }


def _market_open() -> bool:
    now = dt.datetime.now(IST)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    # NSE 09:15-15:30; MCX runs later — record 09:00-23:35 to cover both.
    minutes = now.hour * 60 + now.minute
    return 9 * 60 <= minutes <= 23 * 60 + 35


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default="NIFTY,CRUDEOIL")
    ap.add_argument("--interval", type=int, default=60, help="seconds between snapshots")
    ap.add_argument("--skip-market-hours-check", action="store_true")
    args = ap.parse_args()

    instruments = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    providers = {inst: build_provider(settings.data_provider, inst) for inst in instruments}
    out_dir = os.path.join(settings.data_dir, "chain_history")
    os.makedirs(out_dir, exist_ok=True)

    print(f"Recording chains for {instruments} every {args.interval}s -> {out_dir}")
    print("Ctrl-C to stop. Snapshots are read-only; no orders are placed.")
    while True:
        if not args.skip_market_hours_check and not _market_open():
            time.sleep(30)
            continue
        for inst, provider in providers.items():
            try:
                snap = _snapshot(provider)
            except Exception as exc:  # keep recording the other instruments
                print(f"[{inst}] snapshot error: {type(exc).__name__}", flush=True)
                continue
            if snap is None:
                continue
            path = os.path.join(out_dir, f"{inst}_chain.jsonl")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(snap) + "\n")
            n = len(snap["chain"])
            print(f"[{inst}] {dt.datetime.now(IST):%H:%M:%S} spot={snap['spot']} strikes={n}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
