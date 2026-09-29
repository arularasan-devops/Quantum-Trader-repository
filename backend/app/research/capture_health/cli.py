"""Capture coverage CLI. Every command reads journals read-only and writes
nothing, with one exception named below: ``compact --apply`` stores rolled
files gzipped. Even that removes and rewrites no row, and verifies the copy
line-for-line before the original goes.

    python -m app.research.capture_health.cli report [--instrument CRUDEOIL]
    python -m app.research.capture_health.cli report --session 2026-09-10
    python -m app.research.capture_health.cli report --sessions 10 [--json]
    python -m app.research.capture_health.cli freshness --session 2026-09-12
    python -m app.research.capture_health.cli compact            # dry run
    python -m app.research.capture_health.cli compact --apply    # gzip them

Safe to run while the market is open and the capture is live: the reporting
commands open every file for reading, the derived store is opened with SQLite's
read-only URI, and no setting the live engine reads is touched. ``compact``
never touches the live file the capture is appending to.
"""
from __future__ import annotations

import argparse
import json
import os

from app.research.capture_health import coverage, freshness, report
from app.research.phase17 import compact

DEFAULT_INSTRUMENT = "CRUDEOIL"
DEFAULT_DB = os.path.join("data", "opportunity.db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capture_health")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report", help="coverage per session, with the gaps")
    rep.add_argument("--instrument", default=DEFAULT_INSTRUMENT)
    rep.add_argument("--session", action="append", default=None,
                     help="one session, YYYY-MM-DD; repeatable")
    rep.add_argument("--sessions", type=int, default=None,
                     help="only the most recent N sessions")
    rep.add_argument("--db", default=DEFAULT_DB,
                     help="derived Phase 35 store, read-only; '' to skip")
    rep.add_argument("--json", action="store_true")

    fresh = sub.add_parser(
        "freshness",
        help="whether one session's captured book moved or was a replay",
    )
    fresh.add_argument("--session", required=True, help="YYYY-MM-DD IST")
    fresh.add_argument("--json", action="store_true")

    pack = sub.add_parser(
        "compact",
        help="store ROLLED journal files gzipped; no row removed or rewritten",
    )
    pack.add_argument("--apply", action="store_true",
                      help="without this it only reports what it would do")
    pack.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "compact":
        state = compact.run(apply=args.apply)
        print(json.dumps(state, indent=2, sort_keys=True) if args.json
              else compact.render(state))
        return 0

    if args.cmd == "freshness":
        state = freshness.audit(args.session)
        print(json.dumps(state, indent=2, sort_keys=True) if args.json
              else freshness.render(state))
        return 0

    measurement = coverage.measure(
        args.instrument,
        sessions=args.session,
        limit=args.sessions,
        db_path=args.db or None,
    )
    if args.json:
        print(json.dumps(measurement, indent=2, sort_keys=True))
    else:
        print(report.render(measurement))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
