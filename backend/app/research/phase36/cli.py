"""Phase 36 CLI — research only, paper only, read only.

There is no command here that writes to raw, places an order, enables a vehicle
or changes a gate. The store is opened by Phase 35, whose raw tables carry
database triggers that abort an UPDATE or DELETE, so even a mistake in this file
cannot damage the evidence it reads.

    python -m app.research.phase36.cli run --instrument CRUDEOIL
    python -m app.research.phase36.cli coverage --instrument CRUDEOIL
    python -m app.research.phase36.cli diagnose --instrument CRUDEOIL
    python -m app.research.phase36.cli report --instrument CRUDEOIL
"""
from __future__ import annotations

import argparse
import json

from app.research.phase35 import store
from app.research.phase36 import DEFAULT_INSTRUMENT
from app.research.phase36 import diagnose as p36diagnose
from app.research.phase36 import report as p36report
from app.research.phase36 import service as p36service
from app.research.phase36 import triples as p36triples


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="phase36",
        description="CRUDEOIL vehicle selection study (research only)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, help_text in (
        ("coverage", "how many comparable triples exist, and why the rest do not"),
        ("diagnose", "which field made each quote unusable, and what lacks direction"),
        ("run", "the full study: tables, robustness, verdict, artefacts"),
        ("report", "print the 14 report sections and the verdict"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--instrument", default=DEFAULT_INSTRUMENT)
        p.add_argument("--limit", type=int, default=None)
        if name not in ("coverage", "diagnose"):
            p.add_argument(
                "--no-entry-timing", action="store_true",
                help="skip §15 (three extra resolutions of every leg)",
            )
            p.add_argument(
                "--no-artefacts", action="store_true",
                help="print only; do not write the json artefacts",
            )

    args = ap.parse_args(argv)
    con = store.connect()
    try:
        instrument = (args.instrument or DEFAULT_INSTRUMENT).strip().upper()
        if args.cmd == "coverage":
            built = p36triples.build(con, instrument=instrument, limit=args.limit)
            built.pop("triples", None)
            print(json.dumps(built, indent=2, default=str))
            return 0

        if args.cmd == "diagnose":
            state = p36diagnose.diagnose(
                con, instrument=instrument, limit=args.limit)
            print(json.dumps(state, indent=2, default=str))
            print()
            print(p36diagnose.headline(state))
            return 0

        state = p36service.run(
            con, instrument=instrument, limit=args.limit,
            with_entry_timing=not args.no_entry_timing,
        )
        if not args.no_artefacts:
            state["artefacts"] = p36service.write_artefacts(state)

        if args.cmd == "run":
            print(json.dumps(p36service.summary(state), indent=2, default=str))
            print()
            print(state["headline"])
            return 0

        print(state["headline"])
        print()
        for sec in p36report.sections(state):
            print(f"{sec['section']}  (n={sec.get('n')})")
            print(json.dumps(sec.get("detail"), indent=2, default=str))
            print()
        print(json.dumps(state["verdict"], indent=2, default=str))
        return 0
    finally:
        con.close()


if __name__ == "__main__":  # pragma: no cover - console entry
    raise SystemExit(main())
