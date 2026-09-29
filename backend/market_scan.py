"""One command: collect several instruments, replay them, rank them. RESEARCH ONLY.

Chains the three steps that were run by hand — history collection, the candidate
replay, and the instrument x volatility grading — and finishes by reprinting the
ranked table on its own, so a whole-market answer is one command and one block of
output to read.

Nothing here places an order or changes a production setting. Every number it
prints is gross and UNDERLYING_ONLY: no brokerage, tax or spread, and not option
P&L. No probability is published.

    .venv/bin/python market_scan.py --years 5
    .venv/bin/python market_scan.py --instruments NIFTY,BANKNIFTY --years 3
    .venv/bin/python market_scan.py --skip-collect          # grade what is stored
    .venv/bin/python market_scan.py --all                   # every NFO/BFO name

Angel's historical endpoint is rate-limited, so the collection step of a
multi-year, multi-instrument run takes hours. It is resumable: interrupt it and
run the same command again and it continues from the windows it has not stored.
Credentials come from the SMARTAPI_* environment and are never logged.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from app.market.instruments import REGISTRY
from app.research.db import Database
from app.research.phase14 import coverage
from app.research.phase14.spot_store import SpotStore

HERE = Path(__file__).resolve().parent

# Worth knowing about first: the index families the tool actually trades, then
# the most liquid single stocks. Collecting all 59 registry names at 1-minute
# resolution is an overnight job, so --all is opt-in rather than the default.
DEFAULT_SCAN = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX",
                "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS"]

TRADEABLE = {name for name, spec in REGISTRY.items()
             if spec.exchange in ("NFO", "BFO")}


def _step(title: str, argv: list[str]) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)
    started = time.time()
    done = subprocess.run([sys.executable, *argv], cwd=HERE, check=False)
    took = int(time.time() - started)
    if done.returncode != 0:
        raise SystemExit(f"{argv[0]} failed after {took}s "
                         f"(exit {done.returncode}) — nothing further was run")
    print(f"[{took}s] {argv[0]} finished", flush=True)


def _names(args: argparse.Namespace) -> list[str]:
    if args.all:
        return sorted(TRADEABLE)
    raw = ([n.strip().upper() for n in args.instruments.split(",") if n.strip()]
           if args.instruments else list(DEFAULT_SCAN))
    unknown = [n for n in raw if n not in TRADEABLE]
    if unknown:
        raise SystemExit(
            f"not tradeable names in the registry: {', '.join(unknown)}. "
            "The scan only accepts NFO/BFO instruments the engine knows; MCX "
            "commodities have no cash series to replay."
        )
    return raw


def _stored(names: list[str]) -> dict[str, dict]:
    report = coverage.report(SpotStore(Database()))
    return {row["instrument"]: row for row in report.get("per_instrument", [])
            if row["instrument"] in names}


def _ranking(study: dict) -> None:
    print(f"\n{'=' * 78}\nWHICH INSTRUMENT SUITS THIS SETUP\n{'=' * 78}")
    if not study.get("ranking_is_comparable"):
        print(f"  {study.get('instruments_graded', 0)} instrument(s) had frozen "
              "bands, so there is nothing to rank against them.")
        print("  Collect a second instrument and re-run — one instrument cannot "
              "answer 'which instrument'.")
        return
    print(f"  {'rank':>4} {'instrument':10s} {'band':7s} {'rows':>5} "
          f"{'T1':>7} {'exp':>9} {'move':>7} {'days':>6}  verdict")
    for row in study["ranking"]:
        rank = "-" if row["rank"] is None else str(row["rank"])
        t1 = "n/a" if row["holdout_t1_pct"] is None else f"{row['holdout_t1_pct']}%"
        exp = ("n/a" if row["holdout_expectancy_r"] is None
               else f"{row['holdout_expectancy_r']:+.3f}R")
        days = ("n/a" if row["session_share_pct"] is None
                else f"{row['session_share_pct']}%")
        print(f"  {rank:>4} {row['instrument']:10s} {row['band']:7s} "
              f"{row['holdout_rows']:>5} {t1:>7} {exp:>9} "
              f"{row['median_mfe_points'] or 'n/a':>7} {days:>6}  {row['verdict']}")
    survivors = [r for r in study["ranking"] if r["verdict"] == "SURVIVES_SO_FAR"]
    print(f"\n  {len(survivors)} cohort(s) cleared the sample bar, beat the "
          "whole-pool baseline and held across the folds.")
    print("  SURVIVES_SO_FAR is not a promotion: it is gross, UNDERLYING_ONLY, "
          "and not yet measured on option fills.")
    print("  Unranked rows did not clear the sample bar — a cohort does not win "
          "this table on a handful of rows.")
    print("  Read the move column beside the R column: cost is fixed in rupees, "
          "so points decide whether a small target pays.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instruments", default=None,
                    help=f"comma-separated; default = {','.join(DEFAULT_SCAN)}")
    ap.add_argument("--all", action="store_true",
                    help="every NFO/BFO name in the registry (an overnight run)")
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--skip-collect", action="store_true",
                    help="grade the history already stored, download nothing")
    ap.add_argument("--pool", default=str(Path.home() / "market_scan_pool.json"),
                    help="where the replayed candidate pool is saved")
    ap.add_argument("--out", default=str(Path.home() / "market_scan.json"))
    ap.add_argument("--md", default=str(Path.home() / "market_scan.md"))
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--min-fold-trades", type=int, default=30)
    ap.add_argument("--stride", type=int, default=None,
                    help="candidate stride; default = the replay's own")
    args = ap.parse_args()

    names = _names(args)
    joined = ",".join(names)
    print(f"scanning {len(names)} instrument(s): {joined}")
    print(f"years={args.years}  folds={args.folds}  pool={args.pool}")

    if not args.skip_collect:
        _step("STEP 1/3 — collect underlying history (resumable, rate-limited)",
              ["phase14_collect.py", "--instruments", joined,
               "--years", str(args.years)])
    else:
        print("\nSTEP 1/3 — skipped, grading stored history only")

    stored = _stored(names)
    missing = [n for n in names if n not in stored]
    print("\nstored history per instrument:")
    for name in names:
        row = stored.get(name)
        print(f"  {name:12s} " + ("no history stored — it cannot be graded"
                                  if row is None else
                                  f"{row['bars']} bars over {row['sessions']} "
                                  f"sessions ({row['first_session']} -> "
                                  f"{row['last_session']})"))
    if missing:
        print(f"  {len(missing)} instrument(s) have nothing stored and will be "
              "absent from the ranking rather than reported as zero")
    if len(stored) == len(missing) == 0 or not stored:
        raise SystemExit("no stored history for any requested instrument — run "
                         "without --skip-collect first")

    gradeable = ",".join(n for n in names if n in stored)
    replay = ["phase14_rules.py", "--instruments", gradeable,
              "--years", str(args.years), "--save-trades", args.pool]
    if args.stride is not None:
        replay += ["--stride", str(args.stride)]
    _step("STEP 2/3 — replay the candidate pool", replay)

    _step("STEP 3/3 — grade instrument x volatility band",
          ["phase19_volatility.py", "--trades", args.pool,
           "--folds", str(args.folds),
           "--min-fold-trades", str(args.min_fold_trades),
           "--out", args.out, "--md", args.md])

    with open(args.out) as fh:
        study = json.load(fh)
    _ranking(study)
    print(f"\nwrote {args.out}\nwrote {args.md}\npool kept at {args.pool} — "
          "re-grade it in seconds with phase19_volatility.py --trades")


if __name__ == "__main__":
    main()
