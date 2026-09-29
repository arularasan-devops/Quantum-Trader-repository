"""Phase 37 CLI — collect the research tabs for a session, then count them.

    python -m app.research.phase37.cli collect
    python -m app.research.phase37.cli collect --session 2026-09-07
    python -m app.research.phase37.cli rollup
    python -m app.research.phase37.cli schedule-status
    python -m app.research.phase37.cli durable
    python -m app.research.phase37.cli cas-warm

`collect` needs the backend running, because the tabs are what it reads.
`rollup` reads only what was written and needs nothing running. Neither command
writes to a raw store, takes a trade, promotes a candidate or changes a gate.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase37 import DEFAULT_BASE_URL, DEFAULT_TIMEOUT_S
from app.research.phase37 import collect as p37collect
from app.research.phase37 import durable as p37durable
from app.research.phase37 import rollup as p37rollup
from app.research.phase37 import schedule as p37schedule


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="phase37",
        description="session evidence snapshot across the research tabs",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="snapshot every tab for one session")
    c.add_argument("--base-url", default=DEFAULT_BASE_URL)
    c.add_argument(
        "--session", default=None,
        help="YYYY-MM-DD to file this snapshot against (default: today, IST)",
    )
    c.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    c.add_argument(
        "--dry-run", action="store_true",
        help="fetch and report, but write nothing",
    )

    r = sub.add_parser("rollup", help="count sessions and report the floors")
    r.add_argument("--json", action="store_true", help="full state, not the rows")
    r.add_argument(
        "--with-durable", action="store_true",
        help="also tally journal evidence — scans the whole journal series,"
             " which is tens of GB in the field and takes minutes",
    )

    sub.add_parser(
        "schedule-status",
        help="what the in-app nightly snapshot is waiting for",
    )

    sub.add_parser(
        "cas-warm",
        help="rebuild the CAS report cache now and report what it cost",
    )

    d = sub.add_parser(
        "durable",
        help="sessions with journal evidence on disk (never counted as sessions)",
    )
    d.add_argument("--json", action="store_true", help="per-session rows too")
    d.add_argument(
        "--max-bytes", type=int, default=None,
        help="cap the newest bytes scanned per journal for a quick look;"
             " the result is marked incomplete when the cap bites",
    )

    args = ap.parse_args(argv)

    if args.cmd == "collect":
        manifest = p37collect.collect(
            base_url=args.base_url, session=args.session,
            timeout=args.timeout, write=not args.dry_run,
        )
        print(json.dumps(manifest, indent=2, default=str))
        # A partial session is a real outcome, not an error: it is recorded and
        # simply does not count toward a floor.
        return 0

    if args.cmd == "cas-warm":
        from app.research.phase18 import cas_cache

        print(json.dumps(cas_cache.ensure(), indent=2, default=str))
        return 0

    if args.cmd == "schedule-status":
        print(json.dumps(p37schedule.status(), indent=2, default=str))
        return 0

    if args.cmd == "durable":
        tally = p37durable.tally(max_bytes=args.max_bytes)
        tally["snapshot_sessions_complete"] = \
            p37rollup.rollup()["sessions_complete"]
        tally["note_on_the_two_numbers"] = (
            "The snapshot count is the one every floor is measured against. "
            "The two differ because journals survive a night nobody collected "
            "and a snapshot does not; a day present here and absent there is "
            "evidenced but not countable."
        )
        if args.json:
            print(json.dumps(tally, indent=2, default=str))
        else:
            print(json.dumps(
                {k: v for k, v in tally.items() if k != "sessions"},
                indent=2, default=str))
        print()
        print(p37durable.headline(tally))
        return 0

    state = p37rollup.rollup(with_durable=args.with_durable)
    if args.json:
        print(json.dumps(state, indent=2, default=str))
    else:
        summary = {k: v for k, v in state.items() if k != "rows"}
        print(json.dumps(summary, indent=2, default=str))
    print()
    print(p37rollup.headline(state))
    return 0


if __name__ == "__main__":  # pragma: no cover - console entry
    raise SystemExit(main())
