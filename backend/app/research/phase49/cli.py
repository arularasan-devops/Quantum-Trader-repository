"""Phase 49 CLI — correct a recorded tally by addition, and read the registry.

    .venv/bin/python -m app.research.phase49.cli supersede
    .venv/bin/python -m app.research.phase49.cli supersede --session 2026-09-17
    .venv/bin/python -m app.research.phase49.cli registry
    .venv/bin/python -m app.research.phase49.cli events
    .venv/bin/python -m app.research.phase49.cli status

``supersede`` is the only subcommand that writes. It appends: a fresh tally
under the corrected selection, and a registry row saying which tally that
replaces and why. It never updates or deletes a recorded row, and re-running it
writes nothing new.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase49 import (
    CURRENT,
    RULE_TEXT,
    SUPERSEDED,
    SUPERSESSION_IS_NOT_A_DELETION,
)
from app.research.phase49 import events as events_mod
from app.research.phase49 import service


def _p47_max_calls() -> int:
    """Phase 47's own bound. Imported here, not at module scope: Phase 47 reads
    this package to annotate its tallies, and importing it back at module level
    is a cycle that fails in one import order and works in the other."""
    from app.research.phase47 import service as p47service

    return int(p47service.MAX_CALLS)


def _supersede(args: argparse.Namespace) -> int:
    payload = service.supersede(session=args.session, limit=args.limit)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print("TALLY SUPERSESSION — APPEND ONLY, NOTHING OVERWRITTEN")
    print(f"  session {payload['session'] or '—'}")
    if not payload["arms"]:
        print("  nothing to supersede: every recorded tally for this session "
              "was already taken under the current selection")
    for arm in payload["arms"]:
        print()
        print(f"  {arm['arm']}")
        print(f"    OLD TALLY   {arm['old_calls']}  "
              f"({str(arm['old_snapshot_id'])[:12]})   STATUS: {SUPERSEDED}")
        # "at least" on the number itself, not only in the note below it: the
        # line a reader quotes is this one, and a bounded recount read as a
        # total is the same fault as the tally being corrected here.
        new = (f"at least {arm['new_calls']}" if arm.get("truncated")
               else str(arm["new_calls"]))
        print(f"    NEW TALLY   {new}  "
              f"({str(arm['new_snapshot_id'])[:12]})   STATUS: {CURRENT}")
        print(f"    SUPERSEDED: yes   reason {arm['reason']}")
        legs = (f"at least {arm['new_legs']}" if arm.get("truncated")
                else str(arm["new_legs"]))
        events_seen = (f"{arm['new_events']}+" if arm.get("truncated")
                       else str(arm["new_events"]))
        print(f"    LEG COUNT {legs}   EVENT COUNT {events_seen}")
        print(f"    COUNT IS {arm['count_is']}")
        if arm.get("truncated"):
            print(f"    READ AS: at least {arm['new_calls']} — this recount "
                  f"stopped at its bound of {arm['bound']} while the arm "
                  f"holds {arm['available']}")
    print()
    print(f"  {SUPERSESSION_IS_NOT_A_DELETION}")
    return 0


def _registry(args: argparse.Namespace) -> int:
    payload = service.registry(session=args.session, limit=args.limit)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print("SUPERSESSION REGISTRY — APPEND ONLY")
    print("  session     arm                            old  new  reason")
    for row in payload["records"]:
        print(f"  {str(row.get('session')):10}  {str(row.get('arm')):28}  "
              f"{str(row.get('superseded_calls')):>3}  "
              f"{str(row.get('superseding_calls')):>3}  {row.get('reason')}")
    print()
    print(f"  {SUPERSESSION_IS_NOT_A_DELETION}")
    return 0


def _events(args: argparse.Namespace) -> int:
    """The current board's legs reduced to events, both counts printed."""
    from app.research.phase47 import ARM_PRODUCTION, ARM_RESEARCH
    from app.research.phase47 import service as p47service

    payload = p47service.board(
        instrument=args.instrument, session=args.session, limit=args.limit,
    )
    if args.json:
        print(json.dumps(
            {"events": payload["events"], "counts": payload["event_counts"],
             "bound": payload["bound"]},
            indent=2, default=str,
        ))
        return 0
    print("EVENT GROUPING — LEG COUNT AND EVENT COUNT, NEVER ONE WITHOUT THE OTHER")
    print(f"  session {payload['session'] or '—'}")
    print(f"  rule {RULE_TEXT}")
    for arm in (ARM_RESEARCH, ARM_PRODUCTION):
        counts = payload["event_counts"][arm]
        print()
        print(f"  {arm}")
        bound = payload["bound"][arm]
        print(f"    LEG COUNT {counts[events_mod.LEG_COUNT]}   "
              f"EVENT COUNT {counts[events_mod.EVENT_COUNT]}   "
              f"COUNT IS {bound['count_is']}")
        if bound["truncated"]:
            print(f"    READ AS: at least {bound['selected']} legs — this "
                  f"reading stopped at its bound of {bound['limit']} while "
                  f"the arm holds {bound['available']}; the event count is a "
                  f"floor too")
        for event in payload["events"][arm]:
            print(f"    {event['event_id']}  "
                  f"{str(event.get('contract') or '—')[:22]:22}  "
                  f"{event['observation_count']} observations  1 event  "
                  f"span {event['span_sec']}s")
    print()
    print(f"  {payload['event_counts'][ARM_RESEARCH]['not_a_result']}")
    return 0


def _status(args: argparse.Namespace) -> int:
    print(json.dumps(service.status(), indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase49")
    sub = parser.add_subparsers(dest="cmd", required=True)

    supersede = sub.add_parser(
        "supersede", help="re-record a session tally and register the old one")
    supersede.add_argument("--session")
    supersede.add_argument("--limit", type=int, default=None,
                           help="legs to count per arm (default: the "
                                "recorder's own bound)")
    supersede.add_argument("--json", action="store_true")
    supersede.set_defaults(func=_supersede)

    registry = sub.add_parser("registry", help="every correction on the record")
    registry.add_argument("--session")
    registry.add_argument("--limit", type=int, default=50)
    registry.add_argument("--json", action="store_true")
    registry.set_defaults(func=_registry)

    events = sub.add_parser("events", help="legs grouped into events")
    events.add_argument("--instrument")
    events.add_argument("--session")
    # The recorder's bound, not the panel's: a leg count read over a narrower
    # window than the one the tally was taken under would disagree with the
    # journal for no reason a reader could see.
    events.add_argument("--limit", type=int, default=_p47_max_calls())
    events.add_argument("--json", action="store_true")
    events.set_defaults(func=_events)

    status = sub.add_parser("status", help="paths and safety labels")
    status.set_defaults(func=_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
