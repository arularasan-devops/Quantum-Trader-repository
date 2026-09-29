"""Phase 30 CLI — research only, paper only, no order path.

    python -m app.research.phase30.cli coverage
    python -m app.research.phase30.cli patterns [--instrument NIFTY]
    python -m app.research.phase30.cli run [--instrument NIFTY]
    python -m app.research.phase30.cli show
    python -m app.research.phase30.cli rerender

``coverage`` says what the data can answer before anything is searched.
``patterns`` prints the frozen definitions and their occurrence counts, so the
vocabulary can be inspected before any outcome exists. ``run`` is the
authoritative study and writes the artefacts. ``show`` re-prints the last verdict
without recomputing it, so a verdict cannot quietly change between two readings.

The stopping rule is printed next to the verdict every time, including when the
verdict is a lead, so the standard the run was held to is always on screen beside
the result it produced.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from app.research.phase30 import (
    NO_AVERAGING_EDGE_FOUND,
    NO_CANDLE_PATTERN_EDGE_FOUND,
    patterns,
    pool,
    report,
    run as runner,
)

BAR = "=" * 78
SUB = "-" * 78


def cmd_coverage() -> int:
    cov = runner.coverage()
    print(BAR)
    print("PHASE 30 — DATA COVERAGE AND ANSWERABILITY")
    print(BAR)
    five = cov["underlying_five_year"]
    print("USABLE FIVE-YEAR 1-MINUTE SERIES")
    for row in five.get("usable") or []:
        print(f"  {row['instrument']:<12} bars {row['bars']:>9,}  "
              f"sessions {row['sessions']:>5}  {row.get('first')} -> {row.get('last')}")
    print()
    print("EXCLUDED (INSUFFICIENT_HISTORY)")
    for row in (five.get("excluded") or [])[:20]:
        print(f"  {row['instrument']:<12} {row.get('reason')}")
    print()
    print("OPTION BOOKS")
    print(f"  {json.dumps(cov['option_books'], default=str)[:600]}")
    print()
    print("ANSWERABLE")
    for k, v in cov["answerable"].items():
        print(f"  {k}: {v}")
    return 0


def cmd_patterns(instrument: str | None) -> int:
    print(BAR)
    print("PHASE 30 — FROZEN PATTERN VOCABULARY")
    print(BAR)
    print(f"definition fingerprint: {patterns.FINGERPRINT}")
    print(f"patterns: {len(patterns.NAMES)}")
    print()
    if not instrument:
        for name in patterns.NAMES:
            side = {1: "LONG", -1: "SHORT", 0: "BOTH"}[patterns.SIDES[name]]
            print(f"  {name:<26} {side}")
        return 0
    prep = pool.prepare(instrument)
    if prep is None:
        print(f"{instrument}: INSUFFICIENT_HISTORY")
        return 1
    counts = patterns.occurrence_counts(prep.det)
    total = len(prep.series)
    print(f"{instrument}: {total:,} bars")
    for name in sorted(counts, key=lambda n: -counts[n]):
        pct = 100.0 * counts[name] / max(1, total)
        side = {1: "LONG", -1: "SHORT", 0: "BOTH"}[patterns.SIDES[name]]
        print(f"  {name:<26} {side:<6} {counts[name]:>8,}  {pct:>6.2f}% of bars")
    return 0


def cmd_run(instrument: str | None) -> int:
    names = [instrument] if instrument else None
    result = runner.run(names)
    files = report.write(result)
    _print_verdict(result)
    print()
    print("ARTEFACTS")
    for name, path in files.items():
        print(f"  {name:<38} {path}")
    return 0


def cmd_rerender() -> int:
    """Re-render the artefacts from the stored measured result.

    Only the wording and layout can change here; every number comes from the
    stored run, so a report correction can never move a verdict.
    """
    p = os.path.join(report.out_dir(), "p30_raw_result.json")
    if not os.path.exists(p):
        print("no stored Phase 30 result on disk — run: "
              "python -m app.research.phase30.cli run")
        return 1
    with open(p, encoding="utf-8") as fh:
        result = json.load(fh)
    files = report.write(result)
    _print_verdict(result)
    print()
    print("ARTEFACTS RE-RENDERED FROM THE STORED RESULT (no recomputation)")
    for name, path in files.items():
        print(f"  {name:<38} {path}")
    return 0


def cmd_show() -> int:
    d = report.out_dir()
    p = os.path.join(d, "p30_verdict.json")
    if not os.path.exists(p):
        print("no Phase 30 verdict on disk yet — run: "
              "python -m app.research.phase30.cli run")
        return 1
    with open(p, encoding="utf-8") as fh:
        payload = json.load(fh)
    print(BAR)
    print("PHASE 30 — LAST VERDICT ON DISK")
    print(BAR)
    print(json.dumps(payload, indent=2))
    print()
    print(f"stopping rules: {NO_CANDLE_PATTERN_EDGE_FOUND} / {NO_AVERAGING_EDGE_FOUND}")
    return 0


def _print_verdict(result: dict) -> None:
    v = result.get("verdict") or {}
    hyp = result.get("hypotheses") or {}
    print()
    print(BAR)
    print("PHASE 30 — VERDICT")
    print(BAR)
    print(f"  pattern edge   : {v.get('pattern_verdict')}")
    print(f"  averaging edge : {v.get('averaging_verdict')}")
    print(f"  candidates     : VALIDATED {v.get('validated')}  "
          f"RESEARCH_LEAD {v.get('research_leads')}  REJECTED {v.get('rejected')}")
    print(f"  hypotheses     : {hyp.get('total')} counted "
          f"(stage1 {hyp.get('stage1')}, stage2 {hyp.get('stage2')})")
    print(f"  runtime        : {result.get('runtime_sec')}s")
    print(SUB)
    print(f"  stopping rules : {NO_CANDLE_PATTERN_EDGE_FOUND} / "
          f"{NO_AVERAGING_EDGE_FOUND}")
    print(SUB)
    print("  " + (v.get("note") or ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="phase30", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage")
    p_pat = sub.add_parser("patterns")
    p_pat.add_argument("--instrument", default=None)
    p_run = sub.add_parser("run")
    p_run.add_argument("--instrument", default=None)
    sub.add_parser("show")
    sub.add_parser("rerender")
    args = ap.parse_args(argv)
    if args.cmd == "coverage":
        return cmd_coverage()
    if args.cmd == "patterns":
        return cmd_patterns(args.instrument)
    if args.cmd == "run":
        return cmd_run(args.instrument)
    if args.cmd == "rerender":
        return cmd_rerender()
    return cmd_show()


if __name__ == "__main__":
    sys.exit(main())
