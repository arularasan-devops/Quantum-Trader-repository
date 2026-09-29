"""The after-close five-year history collection — Phase 21 §2. RESEARCH ONLY.

One command, run by hand after the close. It is deliberately NOT wired into the
live session and never will be: the collection is hours of rate-limited HTTP
against Angel's historical endpoint, and running it inside the tick loop is what
would starve the capture path the whole of Phase 21 exists to feed. So this file
is a guard and a runner around :mod:`phase14_collect`, not a second collector —
one implementation of resume, one store.

What it adds over calling the collector directly:

* it REFUSES while the market is open, so a five-year pull cannot compete with
  live capture for the same rate limit;
* it refuses while a live engine is answering on the API port, for the same
  reason, unless ``--force`` is given deliberately;
* it prints the option-history caveat every time (see below) rather than leaving
  a reader to infer scope from a filename.

The caveat, stated once here and printed on every run: what is collected is
UNDERLYING history. Expired option chains are not available from the feed, so
this data can support a statement about where the market went and NOTHING about
what an option would have paid for it. No five-year option profitability claim
can be made from it, and none is made.

    .venv/bin/python phase21_collect_overnight.py                 # 5y, whole optionable universe
    .venv/bin/python phase21_collect_overnight.py --years 3
    .venv/bin/python phase21_collect_overnight.py --instruments NIFTY,BANKNIFTY
    .venv/bin/python phase21_collect_overnight.py --coverage-only  # what is stored, no network
    .venv/bin/python phase21_collect_overnight.py --force          # skip the session guards

Interrupt it with Ctrl-C and run the same command again: it continues from the
windows it has not stored. Credentials come from the SMARTAPI_* environment and
are never printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

# IST. The equity session closes at 15:30; the guard waits until 16:00 so a
# tail-end capture and its writes are finished before an hours-long pull starts.
IST_OFFSET = dt.timedelta(hours=5, minutes=30)
SAFE_AFTER_HOUR = 16
SAFE_BEFORE_HOUR = 9

CAVEAT = (
    "SCOPE: five-year UNDERLYING history only. Expired option chains are not "
    "available from the feed, so nothing collected here can price a historical "
    "option or support a five-year option profitability claim."
)


def _ist_now() -> dt.datetime:
    return dt.datetime.utcnow() + IST_OFFSET


def _session_is_open(now: dt.datetime) -> bool:
    """Roughly: is this a weekday between the open and the guard hour.

    Deliberately coarse and deliberately conservative — it errs toward refusing.
    A precise holiday calendar is not needed to answer "should an hours-long
    download start right now", and ``--force`` covers the days it is wrong.
    """
    if now.weekday() >= 5:
        return False
    return SAFE_BEFORE_HOUR <= now.hour < SAFE_AFTER_HOUR


def _engine_is_live(port: int) -> bool:
    """Is a capture engine answering on this box right now."""
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed localhost URL
            f"http://127.0.0.1:{port}/api/phase17/health", timeout=2
        ) as resp:
            return 200 <= int(resp.status) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default=None,
                    help="comma-separated; default = every optionable NFO/BFO name")
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--interval", default="ONE_MINUTE",
                    choices=["ONE_MINUTE", "FIVE_MINUTE"])
    ap.add_argument("--coverage-only", action="store_true",
                    help="report what is already stored and exit (no network)")
    ap.add_argument("--port", type=int, default=8000,
                    help="API port checked for a running capture engine")
    ap.add_argument("--force", action="store_true",
                    help="run even if the session looks open or an engine is live")
    args = ap.parse_args()

    # Flushed, because the child process writes to the same stdout: unflushed,
    # the scope caveat lands underneath the collector's output or after it.
    print(CAVEAT, flush=True)
    # Absolute, so the command works from any working directory.
    here = pathlib.Path(__file__).resolve().parent
    collector = str(here / "phase14_collect.py")
    cmd = [sys.executable, collector,
           "--years", str(args.years), "--interval", args.interval]
    if args.instruments:
        cmd += ["--instruments", args.instruments]

    if args.coverage_only:
        # No network, no rate limit, no competition with capture: always allowed.
        raise SystemExit(subprocess.call([*cmd, "--coverage-only"], cwd=here))

    now = _ist_now()
    refusals: list[str] = []
    if _session_is_open(now):
        refusals.append(
            f"market session looks OPEN ({now:%a %H:%M} IST); collection is an "
            f"after-close job and would compete with live capture for the same "
            f"rate limit"
        )
    if _engine_is_live(args.port):
        refusals.append(
            f"a capture engine is answering on port {args.port}; stop it "
            f"(./stop.sh) or pass --force if you accept the slower capture"
        )
    if refusals and not args.force:
        for why in refusals:
            print(f"REFUSING: {why}")
        raise SystemExit(2)
    for why in refusals:
        print(f"WARNING (--force): {why}")

    print(f"starting {args.years:g}-year {args.interval} collection at "
          f"{now:%Y-%m-%d %H:%M} IST — resumable, Ctrl-C is safe", flush=True)
    raise SystemExit(subprocess.call(cmd, cwd=here))


if __name__ == "__main__":
    main()
