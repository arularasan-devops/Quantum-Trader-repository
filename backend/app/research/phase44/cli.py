"""Phase 44 CLI. Reads the raw store read-only; writes only its own journal.

    python -m app.research.phase44.cli frozen
    python -m app.research.phase44.cli status [--json]
    python -m app.research.phase44.cli preview --session 2026-09-10 [--json]
    python -m app.research.phase44.cli arm --operator NAME [--note TEXT]
    python -m app.research.phase44.cli disarm --operator NAME [--note TEXT]
    python -m app.research.phase44.cli record [--session DATE] [--all]

``preview`` is the command to use while the recorder is dormant: it evaluates
the arm and prints what would have been journalled, without writing a row.
``record`` refuses unless the recorder has been armed under the current
definition, and prints the refusal rather than failing silently.

There is no command here that places, plans or simulates an order, none that
writes to the raw store or to ``opportunity.db``, and none that touches Phase
41, Phase 42, Phase 43 or the live capture.
"""
from __future__ import annotations

import argparse
import json
import os

from app.research import phase44
from app.research.phase44 import freeze, recorder, report, store, switch

DEFAULT_RAW = os.path.join("data", "opportunity.db")


def _raw_path(given: str | None) -> str:
    return given or os.path.join(store.root(), DEFAULT_RAW)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase44")
    parser.add_argument("--raw", help="Phase 35 store to read (read-only)")
    parser.add_argument("--db", help="journal database (defaults to data/phase44)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("frozen", help="print the frozen definition and its hashes")
    st = sub.add_parser("status", help="switch state, journal tally, progress")
    st.add_argument("--json", action="store_true")

    pv = sub.add_parser("preview", help="evaluate one session, write nothing")
    pv.add_argument("--session", required=True)
    pv.add_argument("--json", action="store_true")

    for name, helptext in (("arm", "allow recording"),
                           ("disarm", "stop recording")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--operator", required=True,
                       help="who is turning it on or off, recorded in the log")
        p.add_argument("--note", default="", help="why")

    rec = sub.add_parser("record", help="journal a session (armed only)")
    rec.add_argument("--session", help="one session, YYYY-MM-DD")
    rec.add_argument("--all", action="store_true",
                     help="every session the raw store holds")

    args = parser.parse_args(argv)

    if args.cmd == "frozen":
        print(json.dumps(freeze.fingerprint(), indent=2, sort_keys=True))
        return 0

    con = store.connect(args.db)

    if args.cmd in ("arm", "disarm"):
        action = switch.arm if args.cmd == "arm" else switch.disarm
        state = action(con, operator=args.operator, note=args.note)
        print(json.dumps(state, indent=2, sort_keys=True))
        if state["may_record"]:
            print("\n  ARMED. Rows will be journalled by `record`. This records"
                  "\n  evidence only: no gate, signal, exit or order path is"
                  "\n  affected, and the arm remains REQUIRES_MORE_DATA.")
        return 0

    if args.cmd == "status":
        payload = report.status(con)
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json
              else report.render(payload))
        return 0

    raw = store.open_raw(_raw_path(args.raw))

    if args.cmd == "preview":
        payload = recorder.preview(raw, args.session)
        print(json.dumps(payload["tally"], indent=2, sort_keys=True)
              if args.json else report.render_preview(payload))
        return 0

    if not args.all and not args.session:
        parser.error("record needs --session DATE or --all")
    if args.all:
        payload = recorder.record_all(con, raw)
    else:
        one = recorder.record(con, raw, args.session)
        payload = {"sessions": [{k: one[k] for k in
                                 ("session", "written", "recorded", "state",
                                  "refusal", "tally")}],
                   "written": one["written"], "state": one["state"]}
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    if payload["written"] == 0:
        state = payload["state"]
        print(f"\n  Nothing was written. Recorder state: {state}.")
        if state == phase44.DORMANT:
            print("  Dormant is the shipped default; `arm` is deliberate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
