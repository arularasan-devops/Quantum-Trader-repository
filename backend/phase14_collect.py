"""Collect multi-year underlying history, resumably (READ-ONLY). RESEARCH ONLY.

Run it, interrupt it, run it again — the second run continues from the windows it
has not stored yet instead of re-downloading years of candles. Nothing here places
an order or touches a production setting.

Usage:
    .venv/bin/python phase14_collect.py --years 5
    .venv/bin/python phase14_collect.py --instruments NIFTY,BANKNIFTY --years 3
    .venv/bin/python phase14_collect.py --years 5 --interval FIVE_MINUTE
    .venv/bin/python phase14_collect.py --coverage-only

Credentials come from SMARTAPI_* environment variables and are never logged.
Angel's historical endpoint is rate-limited (AB1021), so a five-year 1-minute
pull across a watchlist takes hours; that is the point of the resume.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from app.backtest import angel_history as ah
from app.config import settings
from app.market.angelone import _get_scrip_master
from app.market.instruments import REGISTRY
from app.research.db import Database
from app.research.history import _parse_ts
from app.research.phase14 import collector, coverage, tokens
from app.research.phase14.spot_store import SpotStore

# The names worth collecting first: the indices the tool trades most and the
# liquid single stocks. MCX commodities are excluded on purpose — they have no
# cash series (see app.research.phase14.tokens).
DEFAULT_INSTRUMENTS = [
    name for name, spec in REGISTRY.items() if spec.exchange in ("NFO", "BFO")
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default=None,
                    help="comma-separated; default = every NFO/BFO name in the registry")
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--interval", default=collector.ONE_MINUTE,
                    choices=[collector.ONE_MINUTE, collector.FIVE_MINUTE])
    ap.add_argument("--min-interval-sec", type=float,
                    default=collector.MIN_INTERVAL_SEC)
    ap.add_argument("--max-attempts", type=int, default=collector.MAX_ATTEMPTS,
                    help="tries per window before it is recorded as failed")
    ap.add_argument("--backoff-sec", type=float,
                    default=collector.BACKOFF_START_SEC,
                    help="first wait after a rate limit; doubles per retry")
    ap.add_argument("--coverage-only", action="store_true",
                    help="report what is already stored and exit (no network)")
    args = ap.parse_args()

    names = ([n.strip().upper() for n in args.instruments.split(",") if n.strip()]
             if args.instruments else DEFAULT_INSTRUMENTS)
    st = SpotStore(Database())

    if args.coverage_only:
        print(json.dumps(coverage.report(st, interval=args.interval), indent=2))
        return

    if settings.data_provider.lower() != "angelone":
        raise SystemExit(
            "Collection requires QT_DATA_PROVIDER=angelone and valid SmartAPI "
            "credentials. Refusing to store simulated candles as market history."
        )

    end = dt.date.today()
    start = end - dt.timedelta(days=int(args.years * 365.25))

    master = _get_scrip_master()
    series, unavailable = tokens.resolve_many(names, master)
    for name, why in unavailable.items():
        print(f"skip {name}: {why}")
    if not series:
        raise SystemExit("nothing to collect")

    print(f"logging in to Angel to collect {len(series)} series "
          f"{start}..{end} {args.interval}")
    smart = ah.login_smart()
    fetch = collector.angel_fetcher(smart, _parse_ts)

    summary = {"start": start.isoformat(), "end": end.isoformat(),
               "interval": args.interval, "backend": st.backend,
               "instruments_unavailable": unavailable, "series": []}
    for s in series:
        print(f"collecting {s.instrument} ({s.kind} {s.symbol} on {s.exchange})...")
        res = collector.collect_series(
            s, start=start, end=end, fetch=fetch, st=st,
            interval=args.interval, min_interval_sec=args.min_interval_sec,
            max_attempts=args.max_attempts, backoff_start_sec=args.backoff_sec)
        print(f"  windows {res['windows_fetched']} fetched, "
              f"{res['windows_skipped']} already stored, "
              f"{res['windows_retried']} retried after a rate limit, "
              f"{len(res['windows_failed'])} failed; "
              f"{res['rows_written']} bars written, {res['rows_stored']} stored")
        summary["series"].append(res)

    print(json.dumps(summary, indent=2))
    print(json.dumps(coverage.report(st, [s.instrument for s in series],
                                     args.interval), indent=2))


if __name__ == "__main__":
    main()
