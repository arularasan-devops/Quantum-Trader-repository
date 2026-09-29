"""Phase 35 CLI — research and paper only. Places no order, changes no gate.

    python -m app.research.phase35.cli ingest      # raw capture from phase 17
    python -m app.research.phase35.cli rebuild     # derived books from raw only
    python -m app.research.phase35.cli recost      # cost legs built with no lot size
    python -m app.research.phase35.cli report      # §24 verdict + answerability
    python -m app.research.phase35.cli checkpoints # where the §16 bars stand
    python -m app.research.phase35.cli frozen      # §15/§17 fingerprint check
    python -m app.research.phase35.cli stagea      # five-year Stage A discovery

``rebuild`` recreates every derived table from the raw store, which is how §21's
reproducibility claim is checked rather than asserted: run it twice and the
report is identical. It resumes by default — sessions already rebuilt are
skipped, so an interrupted pass costs the session it was in rather than all of
them — and ``--restart`` throws the derived tables away and does the store again.
``--redo YYYY-MM-DD`` sits between the two: it rebuilds the named session and
leaves every other one built, which is what a session that is complete but was
derived by older code needs.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from app.research.phase35 import frozen as p35frozen
from app.research.phase35 import recost as p35recost
from app.research.phase35 import report as p35report
from app.research.phase35 import service as p35service
from app.research.phase35 import stagea as p35stagea
from app.research.phase35 import store as p35store


def _stage_printer():
    """Print one line per rebuild stage advance, with elapsed wall clock.

    The derived rebuild is the hours-long pass and printed only when it finished,
    which makes a working pass and a hung one indistinguishable. Elapsed time is
    measured, not estimated: no remaining-time figure is printed, because the
    pass is quadratic in how densely each session was sampled — measured at 5.2s,
    18.7s, 69.8s and 268s for 5k, 10k, 20k and 40k observations of one session —
    so a rate read off the sessions done so far would not carry to the next one.
    """
    started = time.monotonic()

    def say(ev: dict) -> None:
        done = ev["done"]
        total = ev.get("total")
        share = (
            f" of {total} ({100.0 * done / total:.1f}%)"
            if total else ""
        )
        print(
            f"  [{int(time.monotonic() - started):>6}s] {ev['stage']}: "
            f"{done}{share}",
            flush=True,
        )

    return say


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 35 opportunity attribution")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="append new raw observations and quotes")
    ing.add_argument("--limit", type=int, default=None)
    ing.add_argument(
        "--instrument", default=None,
        help="read only this instrument's captured rows this pass (throughput "
             "only; skipped rows stay ingestable by a later pass)",
    )
    ing.add_argument(
        "--since", default=None, metavar="YYYY-MM-DD",
        help="read only observation files last written on or after this local "
             "date (throughput only; older files stay ingestable)",
    )
    ing.add_argument(
        "--raw-only", action="store_true",
        help="append raw observations and quotes and stop, without rebuilding "
             "the derived tables. Phases 39-41 read raw only, so this is all "
             "they need; the Phase 35 report needs the derived rebuild too",
    )
    ing.add_argument(
        "--rescan", action="store_true",
        help="with --raw-only, ignore the recorded read offsets and read every "
             "capture file from its first byte. Only needed if the offsets are "
             "suspected wrong; re-reading cannot duplicate a row",
    )
    ing.add_argument(
        "--quiet", action="store_true",
        help="do not print progress (per capture file with --raw-only, per "
             "rebuild stage without it)",
    )
    ing.add_argument(
        "--restart", action="store_true",
        help="discard every derived table and rebuild the whole store instead of "
             "resuming at the first session not yet built",
    )
    ing.add_argument(
        "--throttle", type=float, default=0.0, metavar="SECONDS",
        help="sleep this long after each checkpointed chunk, so the rebuild "
             "yields the machine to a live capture sharing it. Makes the pass "
             "slower and changes nothing it measures",
    )

    rb = sub.add_parser("rebuild", help="rebuild every derived table from raw")
    rb.add_argument("--limit", type=int, default=None)
    rb.add_argument("--instrument", default=None)
    rb.add_argument("--since", default=None, metavar="YYYY-MM-DD")
    rb.add_argument(
        "--quiet", action="store_true", help="do not print per-stage progress"
    )
    rb.add_argument(
        "--restart", action="store_true",
        help="discard every derived table and rebuild the whole store instead of "
             "resuming at the first session not yet built",
    )
    rb.add_argument(
        "--redo", action="append", default=[], metavar="YYYY-MM-DD",
        help="rebuild this session even though it is checkpointed as built, "
             "leaving every other session alone. Repeatable. Use it when a "
             "session is complete but was derived by older code, so its legs "
             "are not comparable with the newer ones",
    )
    rb.add_argument(
        "--throttle", type=float, default=0.0, metavar="SECONDS",
        help="sleep this long after each checkpointed chunk, so the rebuild "
             "yields the machine to a live capture sharing it",
    )

    rc = sub.add_parser(
        "recost",
        help="charge the round trip on legs that were entered but never costed, "
             "and re-grade them from the stored paths (no raw re-read)",
    )
    rc.add_argument(
        "--dry-run", action="store_true",
        help="count the legs that would be recosted and write nothing",
    )
    rc.add_argument(
        "--quiet", action="store_true", help="do not print per-batch progress",
    )

    rep = sub.add_parser("report", help="the §24 verdict and the answerability map")
    rep.add_argument("--json", action="store_true", help="print the full payload")
    rep.add_argument("--write", action="store_true", help="write JSON artefacts")

    sub.add_parser("checkpoints", help="§16 milestone status per book")
    sub.add_parser("frozen", help="§15/§17 frozen candidate verification")
    sa = sub.add_parser("stagea", help="§1A five-year historical discovery")
    sa.add_argument("--write", action="store_true", help="write the ranked table")
    st = sub.add_parser(
        "status", help="raw and derived row counts, and which sessions are built"
    )
    st.add_argument(
        "--reset-sessions", action="store_true",
        help="forget which sessions are derived, so the next rebuild redoes all "
             "of them. Bookkeeping only: no measurement lives here",
    )
    cur = sub.add_parser(
        "cursors", help="how far each capture file has been read (bookkeeping)"
    )
    cur.add_argument(
        "--reset", action="store_true",
        help="forget every read position, so the next raw-only pass rescans",
    )

    args = ap.parse_args()

    if float(getattr(args, "throttle", 0.0) or 0.0) < 0:
        ap.error("--throttle must be zero or a positive number of seconds")
    if getattr(args, "redo", None) and getattr(args, "restart", False):
        # --restart already rebuilds every session, so naming two of them means
        # the caller expected the rest to be left alone.
        ap.error("--redo names sessions to rebuild; --restart rebuilds all of "
                 "them. Use one or the other")
    if getattr(args, "raw_only", False) and float(args.throttle or 0.0) > 0:
        # The raw read is the minutes-long half and has no chunk to pause
        # between. Silently ignoring the flag would let a throttled raw pass
        # look throttled when it is not.
        ap.error("--throttle applies to the derived rebuild, not --raw-only")

    if args.cmd == "stagea":
        out = p35stagea.run(
            out_dir=p35service.artefact_dir() if args.write else None
        )
        print(p35stagea.headline(out))
        return

    if args.cmd == "frozen":
        con = p35store.connect()
        try:
            out = p35frozen.verify(os.path.dirname(p35store.db_path()))
        finally:
            con.close()
        print(json.dumps(out, indent=2, default=str))
        return

    con = p35store.connect()
    try:
        if args.cmd == "ingest":
            if args.raw_only:
                # A pass over a large archive that prints only at the end cannot
                # be told apart from a hung one, which is exactly how the last
                # one got interrupted.
                def say(ev: dict) -> None:
                    print(
                        f"  {ev['file']}: {ev['source_rows']} source rows read, "
                        f"{ev['observations_written']} new observations written"
                        + (" (file complete)" if ev.get("file_done") else ""),
                        flush=True,
                    )

                print(json.dumps(
                    p35service.ingest_raw(
                        con, limit=args.limit, instrument=args.instrument,
                        since=args.since, resume=not args.rescan,
                        progress=None if args.quiet else say,
                    ),
                    indent=2, default=str,
                ))
                return
            print(json.dumps(
                p35service.rebuild(
                    con, limit=args.limit, instrument=args.instrument,
                    since=args.since, resume=not args.restart,
                    pause=args.throttle,
                    progress=None if args.quiet else _stage_printer(),
                )["ingested"],
                indent=2, default=str,
            ))
            return
        if args.cmd == "rebuild":
            print(json.dumps(
                p35service.rebuild(
                    con, limit=args.limit, instrument=args.instrument,
                    since=args.since, resume=not args.restart,
                    redo=tuple(args.redo or ()),
                    pause=args.throttle,
                    progress=None if args.quiet else _stage_printer(),
                ),
                indent=2, default=str,
            ))
            return
        if args.cmd == "recost":
            before = p35recost.summary(con)
            if args.dry_run:
                print(p35recost.as_json({
                    "candidates": p35recost.candidate_count(con),
                    "before": before,
                    "wrote": None,
                }))
                return
            started = time.monotonic()

            def said(tally: dict) -> None:
                print(
                    f"  [{int(time.monotonic() - started):>6}s] RECOST: "
                    f"{tally['legs_seen']} of {tally['candidates']} legs, "
                    f"{tally['resolved']} resolved",
                    flush=True,
                )

            out = p35recost.run(
                con, progress=None if args.quiet else said,
            )
            print(p35recost.as_json({
                "recost": out, "before": before, "after": p35recost.summary(con),
            }))
            return
        if args.cmd == "checkpoints":
            print(json.dumps(
                p35service.state(con)["checkpoints"], indent=2, default=str
            ))
            return
        if args.cmd == "status":
            if args.reset_sessions:
                print(json.dumps({
                    "forgotten_sessions": p35store.clear_session_progress(con),
                    "note": "the next rebuild derives every session again; the "
                            "derived rows themselves are untouched until it "
                            "replaces them",
                }, indent=2))
                return
            built = p35store.session_progress(con)
            print(json.dumps({
                **p35store.counts(con),
                # Which sessions are derived, so a resumed pass can be checked
                # rather than trusted. A session appears here only after its rows
                # were written and committed.
                # A session interrupted part-way reports how far it got, so a
                # rebuild that has to be restarted can be seen to resume rather
                # than begin again.
                "derived_sessions": {
                    session: {
                        "stage": row["stage"],
                        "observations_built": int(
                            (row.get("payload") or {}).get("observations") or 0
                        ),
                        "observations_in_session": int(row["observations"]),
                    }
                    for session, row in sorted(built.items())
                },
            }, indent=2, default=str))
            return
        if args.cmd == "cursors":
            # How far each capture file has been read. Bookkeeping only: deleting
            # it costs a re-read, never any evidence.
            cur = p35store.capture_cursors(con)
            if args.reset:
                print(json.dumps({
                    "forgotten": p35store.clear_capture_cursors(con),
                    "note": "the next raw-only pass reads every file from its "
                            "first byte; re-reading cannot duplicate a row",
                }, indent=2))
                return
            print(json.dumps({
                os.path.basename(k): {
                    "offset": v["offset"], "size": v["size"],
                    "rows_seen": v["rows_seen"],
                    "complete": int(v["offset"]) >= int(v["size"]),
                }
                for k, v in sorted(cur.items())
            }, indent=2, default=str))
            return

        state = p35service.state(con)
        payload = dict(state)
        print(p35report.headline(state))
        for row in p35report.answerability(state):
            print(
                f"  {row['section']:<28} n={row['n']:<7} floor={row['floor']:<5} "
                f"{row['status']}"
            )
        if args.write:
            for p in p35service.write_artefacts(payload):
                print(f"wrote {p}")
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
    finally:
        con.close()


if __name__ == "__main__":
    main()
