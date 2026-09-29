"""Phase 33 CLI — research only, writes artefacts and changes nothing else.

    python -m app.research.phase33.cli inventory
    python -m app.research.phase33.cli run
    python -m app.research.phase33.cli cards
    python -m app.research.phase33.cli answers
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.phase24 import data as p24data
from app.research.phase33 import report, study, universe


def _inventory() -> int:
    ans = universe.answerability()
    for row in ans["futures"]:
        print(f"{row['instrument']:<14} {row['tier']:<14} bars={row['bars']:<9} "
              f"sessions={row['sessions']}")
    print()
    print(f"real two-sided option observations: "
          f"{ans['options']['real_two_sided_observations']} "
          f"(floor {ans['options']['min_required']}) -> "
          f"{ans['options']['premium_hold_study']}")
    print(f"engine BUYs: {ans['engine']['actionable_buys']} over "
          f"{ans['engine']['sessions']} session(s) -> "
          f"{ans['engine']['engine_vs_board_study']}")
    print(f"answerable now: {ans['answerable_now']}/{ans['questions_total']}")
    return 0


def _run() -> int:
    res = study.run()
    out = report.write(res)
    print(f"headline: {study.headline(res)}")
    print(f"validated: {res['correction']['validated']}")
    print(f"research leads: {res['correction']['research_leads']}")
    print(f"artefacts: {out['dir']}")
    return 0


def _dump(name: str) -> int:
    p = Path(p24data._resolve(report.OUT_DIR)) / name
    if not p.exists():
        print("no result yet; run: python -m app.research.phase33.cli run")
        return 1
    print(json.dumps(json.loads(p.read_text()), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="phase33", description=__doc__)
    ap.add_argument("command",
                    choices=("inventory", "run", "cards", "answers"))
    args = ap.parse_args(argv)
    if args.command == "inventory":
        return _inventory()
    if args.command == "run":
        return _run()
    if args.command == "cards":
        return _dump("p33_signal_cards.json")
    return _dump("p33_answers.json")


if __name__ == "__main__":
    raise SystemExit(main())
