"""Phase 39 CLI — read only, research only, paper only.

    python -m app.research.phase39.cli coverage
    python -m app.research.phase39.cli cost      --instrument CRUDEOIL
    python -m app.research.phase39.cli run
    python -m app.research.phase39.cli report

``--instrument`` is optional and is a filter, not a scope: with no filter every
instrument in the captured store is measured, which is what "rank every F&O
instrument/vehicle" requires. There is no command here that collects data,
writes to raw, rebuilds a derived table, enables a vehicle, changes a gate or
places an order.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase35 import store
from app.research.phase39 import cost as p39cost
from app.research.phase39 import report as p39report
from app.research.phase39 import service as p39service


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="phase39",
        description=(
            "cost-to-edge feasibility: which instrument/vehicle pairs can "
            "cover their own round trip (capability only, not an edge)"
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
        ("coverage", "how many executable quotes exist, and why the rest do not"),
        ("cost", "round-trip cost and required move per instrument and vehicle"),
        ("run", "the full study; writes the md + json artefacts"),
        ("report", "print the whole markdown document"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--instrument", default=None,
            help="optional filter; default is every instrument captured",
        )
        if name in ("run", "report"):
            p.add_argument(
                "--no-artefacts", action="store_true",
                help="print only; do not write the md/json artefacts",
            )

    args = ap.parse_args(argv)
    instrument = (args.instrument or "").strip().upper() or None
    con = store.connect()
    try:
        if args.cmd == "coverage":
            print(json.dumps(
                p39cost.coverage(con, instrument=instrument),
                indent=2, default=str,
            ))
            return 0
        if args.cmd == "cost":
            print(json.dumps(
                p39cost.cost_table(con, instrument=instrument),
                indent=2, default=str,
            ))
            return 0

        state = p39service.run(con, instrument=instrument)
        if not args.no_artefacts:
            state["artefacts"] = p39service.write_artefacts(state)
        if args.cmd == "report":
            print(p39report.render(state))
            return 0
        print(json.dumps(p39service.summary(state), indent=2, default=str))
        print()
        print(state["headline"])
        for path in state.get("artefacts") or []:
            print(f"  wrote {path}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":  # pragma: no cover - console entry
    raise SystemExit(main())
