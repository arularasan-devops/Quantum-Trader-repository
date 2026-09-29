"""Phase 38 CLI — read only, research only, paper only.

    python -m app.research.phase38.cli run --instrument CRUDEOIL
    python -m app.research.phase38.cli report --instrument CRUDEOIL

``run`` writes the two artefacts and prints the summary; ``report`` prints the
whole markdown document to the terminal. Neither writes to the research store,
places an order, or changes any production behaviour — the Phase 35 raw tables
carry triggers that abort an UPDATE or DELETE, so this cannot damage the
evidence it reads even by mistake.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase35 import store
from app.research.phase38 import DEFAULT_INSTRUMENT
from app.research.phase38 import report as p38report
from app.research.phase38 import service as p38service


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="phase38",
        description=(
            "why every vehicle lost on the measured CRUDEOIL session "
            "(diagnostic only)"
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
        ("run", "build the diagnostic, write the md + json artefacts"),
        ("report", "print the whole markdown diagnostic"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--instrument", default=DEFAULT_INSTRUMENT)
        p.add_argument("--limit", type=int, default=None)
        p.add_argument(
            "--no-entry-timing", action="store_true",
            help="skip §7 (two extra resolutions of every leg)",
        )
        p.add_argument(
            "--no-artefacts", action="store_true",
            help="print only; do not write the md/json artefacts",
        )

    args = ap.parse_args(argv)
    con = store.connect()
    try:
        state = p38service.run(
            con, instrument=(args.instrument or DEFAULT_INSTRUMENT),
            limit=args.limit, with_entry_timing=not args.no_entry_timing,
        )
        if not args.no_artefacts:
            state["artefacts"] = p38service.write_artefacts(state)
        if args.cmd == "report":
            print(p38report.render(state))
            return 0
        print(json.dumps(p38service.summary(state), indent=2, default=str))
        print()
        print(state["headline"])
        for path in state.get("artefacts") or []:
            print(f"  wrote {path}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":  # pragma: no cover - console entry
    raise SystemExit(main())
