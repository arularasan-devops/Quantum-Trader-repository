"""Phase 42 CLI. Read-only: it opens the store, measures, and prints.

    python -m app.research.phase42.cli run [--json] [--write]
    python -m app.research.phase42.cli frozen
    python -m app.research.phase42.cli giveback [--json]
"""
from __future__ import annotations

import argparse
import json

from app.research.phase35 import store as p35store
from app.research.phase42 import freeze as p42freeze
from app.research.phase42 import giveback as p42giveback
from app.research.phase42 import report as p42report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase42")
    sub = parser.add_subparsers(dest="cmd", required=True)

    runner = sub.add_parser("run", help="sweep the multiples and validate")
    runner.add_argument("--json", action="store_true", help="print the payload")
    runner.add_argument("--write", action="store_true", help="write artefacts")
    runner.add_argument("--book", help="restrict to one paper book")
    runner.add_argument("--instrument", help="restrict to one instrument")
    runner.add_argument("--no-giveback", action="store_true",
                        help="skip the §2 path decomposition")

    sub.add_parser("frozen", help="print the frozen definition and its hashes")

    give = sub.add_parser("giveback", help="the §2 decomposition on its own")
    give.add_argument("--channel", help="restrict to one attribution channel")
    give.add_argument("--json", action="store_true", help="print the payload")

    args = parser.parse_args(argv)

    if args.cmd == "frozen":
        print(json.dumps(p42freeze.fingerprint(), indent=2, sort_keys=True))
        return 0

    con = p35store.connect()
    try:
        if args.cmd == "giveback":
            payload = p42giveback.decompose(con, channel=args.channel)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        payload = p42report.run(
            con, book=args.book, instrument=args.instrument,
            with_giveback=not args.no_giveback,
        )
    finally:
        con.close()

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(p42report.render(payload))
    if args.write:
        for path in p42report.write(payload):
            print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
