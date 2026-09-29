"""Phase 47 CLI — read the call board, record a session tally, print the state.

    .venv/bin/python -m app.research.phase47.cli board --instrument CRUDEOIL
    .venv/bin/python -m app.research.phase47.cli record
    .venv/bin/python -m app.research.phase47.cli sessions
    .venv/bin/python -m app.research.phase47.cli status

``record`` is the only subcommand that writes, and it writes one thing: the
tally the board shows at that moment, into Phase 47's own table. No subcommand
admits a call, changes a definition, places an order or promotes anything.
"""
from __future__ import annotations

import argparse
import json
import os

from app.research.phase47 import (
    ARM_PRODUCTION,
    ARM_RESEARCH,
    COUNT_IS_A_FLOOR,
    MD_NAME,
    NOT_A_PROMOTION,
    PAPER_ONLY,
)
from app.research.phase47 import service
from app.research.phase47 import store as store_mod
from app.research.phase49 import CURRENT, SUPERSEDED
from app.research.phase49 import events as p49events


def _artefact_dir() -> str:
    base = os.path.dirname(store_mod.db_path())
    os.makedirs(base, exist_ok=True)
    return base


def _fmt(value: object, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _tally_lines(
    name: str,
    tally: dict,
    events: dict | None = None,
    bound: dict | None = None,
) -> list[str]:
    counted: list[str] = []
    if events:
        # Printed on its own line, above the money, because every ratio below
        # it divides by the leg count and the event count is the honest sample.
        counted += [
            f"  LEG COUNT {events[p49events.LEG_COUNT]}   "
            f"EVENT COUNT {events[p49events.EVENT_COUNT]}   "
            f"repeated legs {events['repeated_observation_legs']}",
            f"  grouping rule {events['grouping_rule']} "
            f"({events['rule_fingerprint']})",
        ]
    if bound and bound.get("truncated"):
        counted.append(
            f"  COUNT IS A FLOOR — this reading stopped at its bound of "
            f"{bound['limit']} and the arm holds {bound['available']}; "
            f"read every count below as 'at least'"
        )
    return [
        f"{name}",
        *counted,
        f"  calls {tally['calls']}  marked {tally['marked']}  "
        f"unmarkable {tally['unmarkable']}  stale marks {tally['stale_marks']}",
        f"  net% total {_fmt(tally['net_pct_total'])}  "
        f"mean {_fmt(tally['net_pct_mean'])}  "
        f"win rate {_fmt(tally['win_rate_pct'], 2)}  "
        f"PF {_fmt(tally['profit_factor'], 3)}",
        f"  best {_fmt(tally['best_pct'])}  worst {_fmt(tally['worst_pct'])}  "
        f"drawdown {_fmt(tally['drawdown_pct'])}  "
        f"avg cost {_fmt(tally['avg_cost_points'])}",
    ]


def _board(args: argparse.Namespace) -> int:
    payload = service.board(
        instrument=args.instrument, session=args.session, limit=args.limit,
    )
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print("RESEARCH CALL BOARD — PAPER ONLY, NO ORDER PATH")
    print(f"session {payload['session'] or '—'}  "
          f"definition {payload['definition']}  "
          f"journalled option rows {payload['journalled_rows_in_session']}")
    print()
    print("  time      vehicle  contract              entry    mark     "
          "net%      state")
    for call in payload["calls"][:args.limit]:
        stamp = _clock(call.get("decision_ts"))
        print(f"  {stamp:8}  {str(call.get('vehicle') or '—'):7}  "
              f"{str(call.get('contract') or '—')[:20]:20}  "
              f"{_fmt(call.get('entry_price'), 2):>7}  "
              f"{_fmt(call.get('mark_price'), 2):>7}  "
              f"{_fmt(call.get('net_pct'), 3):>8}  "
              f"{call.get('state')}")
    print()
    for line in _tally_lines(f"{ARM_RESEARCH}", payload["arms"][ARM_RESEARCH],
                             payload["event_counts"][ARM_RESEARCH],
                             payload["bound"][ARM_RESEARCH]):
        print(line)
    print()
    for line in _tally_lines(
        f"{ARM_PRODUCTION}", payload["arms"][ARM_PRODUCTION],
        payload["event_counts"][ARM_PRODUCTION],
        payload["bound"][ARM_PRODUCTION],
    ):
        print(line)
    print()
    for arm in (ARM_RESEARCH, ARM_PRODUCTION):
        for event in payload["events"][arm]:
            print(f"  {event['event_id']}  {arm:30}  "
                  f"{str(event.get('contract') or '—')[:20]:20}  "
                  f"{_clock(event.get('first_decision_ts'))}–"
                  f"{_clock(event.get('last_decision_ts'))}  "
                  f"{event['observation_count']} observations  1 event")
    print()
    print(f"  {payload['event_counts'][ARM_RESEARCH]['not_a_result']}")
    print()
    print(f"COMPARISON: {payload['comparison']['verdict']}")
    print(f"  shared legs {payload['comparison']['shared_legs']}")
    print(f"  {payload['comparison']['not_independent']}")
    print(f"  {payload['comparison']['session_only']}")
    print()
    print(f"  {NOT_A_PROMOTION}")
    _write_artefact(payload)
    return 0


def _clock(ts: object) -> str:
    if not isinstance(ts, (int, float)):
        return "—"
    import datetime as _dt
    return _dt.datetime.fromtimestamp(
        float(ts), _dt.timezone(_dt.timedelta(hours=5, minutes=30)),
    ).strftime("%H:%M:%S")


def _write_artefact(payload: dict) -> None:
    """The board as a markdown record, so a session leaves a file behind."""
    out = os.path.join(_artefact_dir(), MD_NAME)
    lines = [
        "# RESEARCH CALL BOARD",
        "",
        f"**{PAPER_ONLY} — {payload['order_path']}**",
        "",
        f"- session: `{payload['session'] or '—'}`",
        f"- definition: `{payload['definition']}`",
        f"- option rows journalled in this session: "
        f"{payload['journalled_rows_in_session']}",
        "",
        "## Columns",
        "",
        "| arm | LEG COUNT | EVENT COUNT | COUNT IS | marked | unmarkable | "
        "net% total | win rate | PF |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm, tally in payload["arms"].items():
        events = payload["event_counts"][arm]
        bound = payload["bound"][arm]
        lines.append(
            f"| {arm} | {bound['reported']} | "
            f"{events[p49events.EVENT_COUNT]}"
            f"{'+' if bound['truncated'] else ''} | "
            f"`{bound['count_is']}` | {tally['marked']} | "
            f"{tally['unmarkable']} | {_fmt(tally['net_pct_total'])} | "
            f"{_fmt(tally['win_rate_pct'], 2)} | "
            f"{_fmt(tally['profit_factor'], 3)} |"
        )
    lines += [
        "",
        f"Bound on this reading: {payload['bound']['limit']} legs per arm "
        f"(ceiling {payload['bound']['max']}). A count that reached it is "
        f"published as `at least N` and its event count with a `+`: the "
        f"number is then a property of the bound, not of the session.",
        "",
        f"Grouping rule: `{payload['event_counts'][ARM_RESEARCH]['grouping_rule']}`"
        f" (`{payload['event_counts'][ARM_RESEARCH]['rule_fingerprint']}`)",
        "",
        payload["event_counts"][ARM_RESEARCH]["not_a_result"],
        "",
        "## Events",
        "",
        "| event_id | arm | contract | strike | direction | first | last | "
        "observations | leg ids |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm in (ARM_RESEARCH, ARM_PRODUCTION):
        for event in payload["events"][arm]:
            # The leg ids are written out in full: an event is a reading of
            # legs that stay individually addressable, not a replacement for
            # them.
            legs = ",".join(str(i) for i in event["overlay_ids"] if i)
            lines.append(
                f"| `{event['event_id']}` | {arm} | "
                f"{event.get('contract') or '—'} | "
                f"{event.get('strike') if event.get('strike') is not None else '—'}"
                f" | {event.get('direction') or '—'} | "
                f"{_clock(event.get('first_decision_ts'))} | "
                f"{_clock(event.get('last_decision_ts'))} | "
                f"{event['observation_count']} | `{legs or '—'}` |"
            )
    lines += [
        "",
        f"`{payload['comparison']['verdict']}` — "
        f"{payload['comparison']['not_independent']}",
        "",
        payload["comparison"]["session_only"],
        "",
        NOT_A_PROMOTION,
        "",
    ]
    with open(out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(f"  wrote {os.path.relpath(out, store_mod.root())}")


def _record(args: argparse.Namespace) -> int:
    counts = service.record(
        instrument=args.instrument, session=args.session, limit=args.limit,
    )
    print(json.dumps(counts, indent=2, default=str))
    return 0


def _sessions(args: argparse.Namespace) -> int:
    payload = service.sessions(limit=args.limit)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print("RECORDED SESSION TALLIES — PAPER ONLY")
    print("  session     arm                            legs  events  marked  "
          "net% total  win%   record")
    floors = False
    for row in payload["sessions"]:
        legs = row.get("legs")
        shown = _fmt(row.get("calls") if legs is None else legs, 0)
        # A count that stopped at its bound is printed as a floor, in the cell
        # itself, because a footnote is read after the number is believed.
        if row.get("count_is") == COUNT_IS_A_FLOOR:
            shown = f"{shown}+"
            floors = True
        print(f"  {str(row.get('session')):10}  {str(row.get('arm')):28}  "
              f"{shown:>5}  "
              f"{_fmt(row.get('events'), 0):>6}  {row.get('marked'):6}  "
              f"{_fmt(row.get('net_pct_total')):>10}  "
              f"{_fmt(row.get('win_rate_pct'), 2):>5}  "
              f"{row.get('record_status') or CURRENT}")
    if floors:
        print()
        print(f"  + {COUNT_IS_A_FLOOR}: that reading stopped at its selection "
              f"bound, so its legs are a floor. Re-record with a larger "
              f"--limit to count the rest.")
    superseded = payload.get("superseded") or []
    if superseded:
        print()
        print(f"  {SUPERSEDED} TALLIES — kept, not deleted")
        for row in superseded:
            print(f"    {row.get('session')}  {row.get('arm')}  "
                  f"OLD {row.get('calls')}  →  superseded by "
                  f"{str(row.get('superseded_by'))[:12]}  "
                  f"reason {row.get('superseded_reason')}")
        print(f"  {payload['supersession_is_additive']}")
    print()
    print(f"  {NOT_A_PROMOTION}")
    return 0


def _status(args: argparse.Namespace) -> int:
    print(json.dumps(service.status(), indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase47")
    sub = parser.add_subparsers(dest="cmd", required=True)

    board = sub.add_parser("board", help="the live call board")
    board.add_argument("--instrument")
    board.add_argument("--session")
    board.add_argument("--limit", type=int, default=40)
    board.add_argument("--json", action="store_true")
    board.set_defaults(func=_board)

    record = sub.add_parser("record", help="journal the current session tally")
    record.add_argument("--instrument")
    record.add_argument("--session")
    record.add_argument("--limit", type=int, default=service.MAX_CALLS)
    record.set_defaults(func=_record)

    sessions = sub.add_parser("sessions", help="recorded session tallies")
    sessions.add_argument("--limit", type=int, default=40)
    sessions.add_argument("--json", action="store_true")
    sessions.set_defaults(func=_sessions)

    status = sub.add_parser("status", help="paths, definition, safety labels")
    status.set_defaults(func=_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
