"""Phase 32 CLI — research only.

    python -m app.research.phase32.cli inventory
    python -m app.research.phase32.cli run [--instrument NIFTY ...]
    python -m app.research.phase32.cli show
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.phase24 import data as p24data
from app.research.phase32 import report, run, universe


def _inventory() -> int:
    for row in universe.inventory():
        print(f"{row['instrument']:<14} {row['tier']:<14} bars={row['bars']:<9} "
              f"sessions={row['sessions']}")
    return 0


def _run(instruments: list[str] | None) -> int:
    res = run.study(instruments)
    out = report.write(res)
    print(f"headline: {res['headline']}")
    print(f"hypotheses counted: {res['hypotheses_counted']}")
    print(f"validated: {res['validated'] or 'none'}")
    print(f"artefacts: {out['dir']}")
    return 0


def _show() -> int:
    p = Path(p24data._resolve(report.OUT_DIR)) / "p32_answers.json"
    if not p.exists():
        print("no result yet; run: python -m app.research.phase32.cli run")
        return 1
    print(json.dumps(json.loads(p.read_text()), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 32 reachable-T1 study")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory")
    r = sub.add_parser("run")
    r.add_argument("--instrument", action="append", default=None)
    sub.add_parser("show")
    args = ap.parse_args(argv)
    if args.cmd == "inventory":
        return _inventory()
    if args.cmd == "run":
        return _run(args.instrument)
    return _show()


if __name__ == "__main__":
    raise SystemExit(main())
