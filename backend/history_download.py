"""Long-history downloader (READ-ONLY) — daily or intraday, multi-year.

Unlike ``backtest_run.py`` (which uses a single front-month futures contract and
so can't span years), this resolves the right long-history source per instrument:

* NSE/BFO indices (NIFTY, BANKNIFTY, SENSEX) -> the SPOT INDEX token, which
  Angel keeps for many years.
* MCX commodities (CRUDEOIL, NATURALGAS) -> a CONTINUOUS front-month series
  stitched across all futures contracts.

Usage:
    .venv/bin/python history_download.py --instrument NIFTY    --years 5 --interval ONE_DAY
    .venv/bin/python history_download.py --instrument CRUDEOIL --years 5 --interval ONE_DAY

Credentials come from SMARTAPI_* environment variables. Places no orders.
"""
from __future__ import annotations

import argparse
import datetime as dt

from app.backtest import angel_history as ah
from app.config import settings
from app.market.instruments import REGISTRY

# Instruments that have a spot index with long daily history.
_INDEX_INSTRUMENTS = {"NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", "INDIAVIX"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", required=True)
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--interval", default="ONE_DAY")
    ap.add_argument(
        "--exchange",
        default=None,
        help="override exchange (needed for symbols not in the registry, e.g. INDIAVIX -> NSE)",
    )
    ap.add_argument(
        "--source",
        choices=["auto", "index", "continuous"],
        default="auto",
        help="auto = index for NSE/BFO indices, continuous futures for MCX",
    )
    args = ap.parse_args()

    inst_u = args.instrument.upper()
    spec = REGISTRY.get(inst_u)
    if args.exchange:
        exchange = args.exchange.upper()
    elif spec is not None:
        exchange = spec.exchange
    else:
        raise SystemExit(
            f"Unknown instrument {args.instrument}; pass --exchange (e.g. --exchange NSE)."
        )

    source = args.source
    if source == "auto":
        source = "index" if inst_u in _INDEX_INSTRUMENTS else "continuous"

    end = dt.date.today()
    start = end - dt.timedelta(days=int(args.years * 365.25))
    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)

    print(f"Logging in to Angel to download {args.instrument} {args.interval} ({source})...")
    smart = ah.login_smart()

    if source == "index":
        token, symbol = ah.index_token(args.instrument, exchange)
        print(f"Index: {symbol} token={token}")
        print(f"Downloading {start} .. {end} (chunked, rate-limited)...")
        candles = ah.download_candles(smart, token, exchange, args.interval, start, end)
    else:
        contracts = ah.list_future_contracts(args.instrument, exchange)
        if not contracts:
            raise SystemExit(f"No futures contracts found for {args.instrument} on {exchange}.")
        print(f"Stitching {len(contracts)} futures contracts into a continuous series...")
        candles = ah.download_continuous_futures(
            smart, contracts, exchange, args.interval, start, end
        )

    ah.save_candles(path, candles)
    print(f"Saved {len(candles)} candles -> {path}")
    if candles:
        first = dt.datetime.fromtimestamp(candles[0].time).strftime("%Y-%m-%d")
        last = dt.datetime.fromtimestamp(candles[-1].time).strftime("%Y-%m-%d")
        print(f"Range: {first} .. {last}")


if __name__ == "__main__":
    main()
