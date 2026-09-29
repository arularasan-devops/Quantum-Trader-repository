"""Read-only CLI for the frozen pair capture.

``python -m app.research.pairs.cli status``  capture counts + both verdicts
``python -m app.research.pairs.cli retest``  measure the frozen config so far
``python -m app.research.pairs.cli basis``   near/next basis coverage (Test B)
``python -m app.research.pairs.cli spec``    the frozen spec and its fingerprint

Nothing here writes, trades, or fits a parameter.
"""
from __future__ import annotations

import argparse
import json

from app.research.pairs import report
from app.research.pairs import spec as pair_spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Frozen pair capture (research only)")
    ap.add_argument("command", choices=("status", "retest", "basis", "spec"))
    args = ap.parse_args(argv)
    out: dict
    if args.command == "status":
        out = report.status()
    elif args.command == "retest":
        out = report.retest()
    elif args.command == "basis":
        out = report.basis_status()
    else:
        out = pair_spec.as_dict()
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
