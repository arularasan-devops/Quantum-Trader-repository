"""Phase 43 CLI. Read-only over the five-year files; writes only artefacts.

    python -m app.research.phase43.cli run [--json] [--write]
    python -m app.research.phase43.cli frozen
    python -m app.research.phase43.cli answerability [--json]

There is no command that places, plans or records an order, and no command that
touches Phase 41, Phase 42 or the live capture stores.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase43 import answerability as p43ans
from app.research.phase43 import freeze as p43freeze
from app.research.phase43 import report as p43report
from app.research.phase43 import study as p43study


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase43")
    sub = parser.add_subparsers(dest="cmd", required=True)

    runner = sub.add_parser("run", help="measure every declared candidate")
    runner.add_argument("--json", action="store_true", help="print the payload")
    runner.add_argument("--write", action="store_true", help="write artefacts")

    sub.add_parser("frozen", help="print the frozen definition and its hashes")

    ans = sub.add_parser("answerability",
                         help="what this dataset can and cannot answer")
    ans.add_argument("--json", action="store_true", help="print the payload")

    args = parser.parse_args(argv)

    if args.cmd == "frozen":
        print(json.dumps(p43freeze.fingerprint(), indent=2, sort_keys=True))
        return 0

    if args.cmd == "answerability":
        payload = p43ans.map_families()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    payload = p43study.run()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(p43report.render(payload))
    if args.write:
        for path in p43report.write(payload):
            print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
