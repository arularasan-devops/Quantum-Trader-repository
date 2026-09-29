"""Where the replayed edge lives, if anywhere — cohort study over stored history.

The HTF sweep answered "which timeframe" and the answer was "neither": the
average replayed trade is flat in-sample and slightly negative in holdout at any
factor. This asks the next question instead — is the engine mixing a few good
setups into a great many indifferent ones? — by slicing the same replayed trades
on what the engine knew at entry, in-sample and holdout separately.

    .venv/bin/python phase14_cohorts.py --instruments NIFTY --years 1.5
    .venv/bin/python phase14_cohorts.py --instruments NIFTY --out ~/p14_cohorts.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time

from app.config import settings
from app.research.db import Database
from app.research.phase14 import cohorts, replay_spot, sweep
from app.research.phase14.spot_store import SpotStore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default=None, help="comma-separated")
    ap.add_argument("--factor", type=int, default=None,
                    help="HTF factor to replay at (default: the live setting)")
    ap.add_argument("--years", type=float, default=None,
                    help="replay only the most recent N years of stored history")
    ap.add_argument("--in-sample-fraction", type=float,
                    default=sweep.IN_SAMPLE_FRACTION)
    ap.add_argument("--min-cohort-trades", type=int,
                    default=cohorts.MIN_COHORT_TRADES)
    ap.add_argument("--out", default=None, help="write the full study as JSON")
    args = ap.parse_args()

    st = SpotStore(Database())
    names = ([n.strip().upper() for n in args.instruments.split(",") if n.strip()]
             if args.instruments else st.instruments())
    if not names:
        raise SystemExit("no stored history — run phase14_collect.py first")

    since = None
    if args.years:
        since = (dt.date.today()
                 - dt.timedelta(days=int(args.years * 365.25))).isoformat()
    factor = args.factor or int(settings.htf_factor)
    started = time.time()

    early_trades: list[dict] = []
    late_trades: list[dict] = []
    spans: dict[str, dict] = {}
    for name in names:
        candles = sweep.load_candles(name, st, since=since)
        early, late, span = sweep.split_sessions(candles, args.in_sample_fraction)
        spans[name] = span
        for label, leg in (("in-sample", early), ("holdout", late)):
            def note(done: int, total: int, trades: int,
                     _l: str = label, _n: str = name) -> None:
                print(f"[{int(time.time() - started)}s]   htf={factor} {_l} {_n}: "
                      f"bar {done}/{total} ({done * 100 // max(1, total)}%), "
                      f"{trades} trades", flush=True)

            res = replay_spot.run(name, leg, factor=factor, progress=note)
            if not res.get("ok"):
                print(f"  {name} {label}: skipped ({res.get('reason')})")
                continue
            (early_trades if label == "in-sample" else late_trades).extend(
                res["trades"])

    if not early_trades or not late_trades:
        raise SystemExit("not enough replayed trades in both periods to compare")

    study = cohorts.study(early_trades, late_trades,
                          min_trades=args.min_cohort_trades)

    print(f"\nhtf_factor={factor}  in-sample {study['in_sample_trades']} trades  "
          f"holdout {study['holdout_trades']} trades")
    for dim in study["dimensions"]:
        print(f"\n=== {dim['dimension']}")
        for row in dim["cohorts"]:
            if not row["enough_trades"]:
                continue
            a, b = row["in_sample"], row["holdout"]
            mark = "  <== survives" if row["survives_holdout"] else ""
            print(f"  {row['cohort']:<34} "
                  f"in n={a['trades']:<6} exp={a['expectancy_r']:+.3f}R  "
                  f"out n={b['trades']:<6} exp={b['expectancy_r']:+.3f}R "
                  f"PF={b['profit_factor']} win={b['win_rate']}% "
                  f"({row['share_of_holdout_pct']}% of book){mark}")

    print(f"\ncohorts tested: {study['cohorts_tested']}  "
          f"expected to look good by chance: ~{study['expected_false_positives']}")
    print(f"verdict: {study['verdict']}")
    print(f"\n{study['note']}")
    print(f"\nstudy took {int(time.time() - started)}s")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"htf_factor": factor, "since": since, "spans": spans,
                       "study": study}, fh, indent=2)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
