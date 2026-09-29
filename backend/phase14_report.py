"""Phase 14 report: coverage, then the HTF-factor sweep (READ-ONLY). RESEARCH ONLY.

Reads only stored candles — no network, no credentials, no orders, no production
setting touched. Coverage is printed first and deliberately: a sweep over data
full of holes is a confident answer to the wrong question.

Usage:
    .venv/bin/python phase14_report.py
    .venv/bin/python phase14_report.py --instruments NIFTY,BANKNIFTY
    .venv/bin/python phase14_report.py --factors 3,5,15,30 --out /tmp/p14.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

from app.research.db import Database
from app.research.phase14 import coverage, sweep
from app.research.phase14.spot_store import SpotStore

LIMITS = [
    "Underlying (cash index / equity) candles only — Angel's scrip master keeps "
    "live contracts, so expired futures and expired strikes cannot be re-fetched.",
    "R is measured on the underlying with the engine's own ATR/structure stop, "
    "GROSS of premium, spread, slippage and brokerage. It grades direction and "
    "timing; it says nothing about the option that would have been bought.",
    "No option chain is present in the replay, so strike choice, spread quality, "
    "OI, IV and premium health are untested here and remain live-capture evidence.",
    "No overnight holds: every replayed trade is flattened on its IST session's "
    "last bar.",
    "A bar covering both stop and target is scored as the stop.",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default=None, help="comma-separated")
    ap.add_argument("--factors", default=",".join(str(f) for f in sweep.DEFAULT_FACTORS))
    ap.add_argument("--in-sample-fraction", type=float, default=sweep.IN_SAMPLE_FRACTION)
    ap.add_argument("--min-trades", type=int, default=sweep.MIN_TRADES)
    ap.add_argument("--years", type=float, default=None,
                    help="replay only the most recent N years of stored history")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the progress lines of a long replay")
    ap.add_argument("--out", default=None, help="write the full report as JSON")
    args = ap.parse_args()

    st = SpotStore(Database())
    names = ([n.strip().upper() for n in args.instruments.split(",") if n.strip()]
             if args.instruments else st.instruments())
    if not names:
        raise SystemExit("no stored history — run phase14_collect.py first")

    cov = coverage.report(st, names)
    print(json.dumps(cov, indent=2))
    if not cov["bars_total"]:
        raise SystemExit("stored history has no bars")

    factors = tuple(int(f) for f in args.factors.split(",") if f.strip())
    since = None
    if args.years:
        since = (dt.date.today()
                 - dt.timedelta(days=int(args.years * 365.25))).isoformat()
        print(f"replaying sessions from {since} onward")

    started = time.time()

    def report(line: str) -> None:
        # A five-year 1-minute replay is hours long; silence is indistinguishable
        # from a hang, so it says where it is and flushes.
        print(f"[{int(time.time() - started)}s] {line}", flush=True)

    print(f"replaying {len(factors)} factor(s) over {cov['bars_total']} bars "
          f"— this takes hours for a five-year 1-minute history", flush=True)
    result = sweep.compare(names, st, factors=factors,
                           fraction=args.in_sample_fraction,
                           min_trades=args.min_trades, since=since,
                           report=None if args.quiet else report)
    for row in result["results"]:
        live = " (LIVE)" if row["is_live_setting"] else ""
        print(f"\n=== htf_factor={row['htf_factor']}{live} — "
              f"{'stable' if row['stable'] else 'UNSTABLE / THIN'}")
        for label, leg, per_year in (
            ("in-sample", row["in_sample"], row["in_sample_r_per_year"]),
            ("holdout  ", row["holdout"], row["holdout_r_per_year"]),
        ):
            print(f"  {label}  trades={leg['trades']:<6} "
                  f"sig/yr={leg['signals_per_year']:<7} "
                  f"sig/mo={leg['signals_per_month']:<6} "
                  f"trades/day={leg['trades_per_day']:<5} "
                  f"days_traded={leg['session_participation_pct']}%")
            print(f"  {' ' * len(label)}  win={leg['win_rate']}% "
                  f"exp={leg['expectancy_r']:+.3f}R "
                  f"PF={leg['profit_factor']} "
                  f"netR={leg['net_r']} "
                  f"R/yr={per_year:+.1f} "
                  f"maxDD={leg['max_drawdown_r']}R")
            print(f"  {' ' * len(label)}  hold med={leg['median_minutes_hold']}m "
                  f"p90={leg['p90_minutes_hold']}m "
                  f"to_target={leg['median_minutes_to_target']}m | "
                  f"MFE med={leg['median_mfe_r']}R "
                  f"MAE med={leg['median_mae_r']}R "
                  f"MAE(winners)={leg['median_mae_r_winners']}R")
            print(f"  {' ' * len(label)}  stop/noise med="
                  f"{leg['median_risk_over_noise']}x candle-range, "
                  f"inside 1 candle={leg['stop_inside_one_bar_pct']}%, "
                  f"resolved on next bar={leg['resolved_next_bar_pct']}%")
    print()
    print(result["verdict"])
    print()
    print("What this report cannot claim:")
    for line in LIMITS:
        print(f"  - {line}")

    print(f"\nreplay took {int(time.time() - started)}s", file=sys.stderr)

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"coverage": cov, "sweep": result, "limits": LIMITS}, fh, indent=2)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
