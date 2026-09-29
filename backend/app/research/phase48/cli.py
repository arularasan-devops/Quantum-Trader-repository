"""Phase 48 CLI — print the admission funnel, per session or per day.

    .venv/bin/python -m app.research.phase48.cli funnel
    .venv/bin/python -m app.research.phase48.cli funnel --options --instrument CRUDEOIL
    .venv/bin/python -m app.research.phase48.cli sessions
    .venv/bin/python -m app.research.phase48.cli status

No subcommand writes to any journal. ``funnel`` writes the report artefact and
nothing else, and the artefact is derived output: delete it and it regenerates.
"""
from __future__ import annotations

import argparse
import json
import os

from app.research.phase46 import store as p46store
from app.research.phase48 import NOT_A_RESULT, NOT_A_THRESHOLD_SEARCH
from app.research.phase48 import service


def _artefact_root() -> str:
    """The data directory the overlay journal already lives under.

    Derived from the journal path rather than from a constant, so the report
    lands beside the evidence it describes even when the store has been moved.
    """
    return os.path.dirname(os.path.dirname(p46store.db_path()))


def _funnel(args: argparse.Namespace) -> int:
    payload = service.report(
        session=args.session,
        instrument=args.instrument,
        options_only=bool(args.options),
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(service.render(payload))
    for written in service.write_artefacts(payload, root=_artefact_root()):
        print(f"  wrote {written}")
    return 0


def _sessions(args: argparse.Namespace) -> int:
    payload = service.sessions(
        limit=args.limit, options_only=bool(args.options))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print("ADMISSION FUNNEL BY SESSION — READ-ONLY ATTRIBUTION")
    print(f"  {'session':<12} {'observed':>9} {'admitted':>9} "
          f"{'feed':>8} {'said no':>8}  dominant blocker")
    for row in payload["sessions"]:
        print(f"  {row['session']:<12} {row['observed']:>9} "
              f"{row['admitted']:>9} {row['feed_could_not_speak']:>8} "
              f"{row['definition_said_no']:>8}  {row['dominant_blocker']}")
    if not payload["sessions"]:
        print("  no session in the overlay journal yet")
    print()
    print(f"  {NOT_A_RESULT}")
    print(f"  {NOT_A_THRESHOLD_SEARCH}")
    return 0


def _status(_args: argparse.Namespace) -> int:
    print(json.dumps(service.status(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase48")
    sub = parser.add_subparsers(dest="cmd", required=True)

    funnel = sub.add_parser("funnel", help="the funnel for one session")
    funnel.add_argument("--session", default=None)
    funnel.add_argument("--instrument", default=None)
    funnel.add_argument("--options", action="store_true",
                        help="CE and PE only — the vehicles the call board takes")
    funnel.add_argument("--json", action="store_true")
    funnel.set_defaults(func=_funnel)

    sessions = sub.add_parser("sessions", help="one line per session")
    sessions.add_argument("--limit", type=int, default=40)
    sessions.add_argument("--options", action="store_true")
    sessions.add_argument("--json", action="store_true")
    sessions.set_defaults(func=_sessions)

    status = sub.add_parser("status", help="paths, definition, stage list")
    status.set_defaults(func=_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
