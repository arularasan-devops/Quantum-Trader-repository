"""Phase 31 CLI — research only, no order path.

    python -m app.research.phase31.cli evidence
    python -m app.research.phase31.cli realized
    python -m app.research.phase31.cli excursion [--instrument NIFTY]
    python -m app.research.phase31.cli run
    python -m app.research.phase31.cli show
    python -m app.research.phase31.cli rerender

``evidence`` states what can and cannot be measured before any number exists, so
the limits are visible before the results. ``run`` is the authoritative study and
writes the artefacts; ``show`` re-prints the stored result without recomputing, so
a figure cannot quietly change between two readings, and ``rerender`` rebuilds the
report from that stored result.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from app.research.phase31 import (
    HORIZONS,
    PCT_GRID,
    UNMEASURED,
    evidence,
    excursion,
    realized,
    report,
    run as runner,
)
from app.research.phase24 import data

BAR = "=" * 78
SUB = "-" * 78
RAW = "p31_raw_result.json"


def cmd_evidence() -> int:
    inv = evidence.inventory()
    print(BAR)
    print("PHASE 31 — WHAT THE STORED EVIDENCE CAN ANSWER")
    print(BAR)
    print("FIVE-YEAR UNDERLYING SERIES")
    for inst, row in (inv["underlying"] or {}).items():
        if row.get("present"):
            print(f"  {inst:<12} bars {row.get('bars'):>9,}  "
                  f"{row.get('first')} -> {row.get('last')}")
        else:
            print(f"  {inst:<12} MISSING")
    print()
    books = inv["option_books"]
    print("STORED OPTION CHAINS")
    print(f"  snapshots {books.get('snapshots'):,}  sessions {books.get('sessions')}")
    print(f"  provenance {json.dumps(books.get('by_source'), default=str)}")
    print(f"  REAL_BROKER snapshots {books.get('real_broker_snapshots')}")
    print(f"  sampled legs {books.get('legs_sampled'):,} of which two-sided "
          f"{books.get('legs_with_two_sided_quote')}")
    print()
    obs = inv["captured_observations"]
    print("EXACT-TIMESTAMP CAPTURES")
    print(f"  rows {obs.get('rows'):,}  two-sided at decision "
          f"{obs.get('two_sided_at_decision')}")
    print(f"  quality {json.dumps(obs.get('by_data_quality'), default=str)}")
    print()
    print(SUB)
    print(f"  real two-sided decision instants : "
          f"{inv['real_two_sided_decision_instants']}")
    print(f"  required for a distribution      : "
          f"{inv['min_required_for_premium_distribution']}")
    print(f"  premium percentage measurable    : "
          f"{inv['premium_percentage_measurable']}")
    if not inv["premium_percentage_measurable"]:
        print(f"  => premium distribution from stored chains is {UNMEASURED};")
        print("     the underlying excursion is measured instead, and the bridge")
        print("     to a premium percentage is labelled as an assumption.")
    return 0


def cmd_realized() -> int:
    r = realized.summary()
    print(BAR)
    print("PHASE 31 — WHAT THE RECORDED CALLS ACTUALLY RETURNED")
    print(BAR)
    print(f"rows seen {r['rows_seen']}")
    for k, v in sorted((r["classification"]["by_class"] or {}).items()):
        print(f"  {k:<30} {v}")
    print()
    print(f"usable engine-resolved option fills : {r['usable_option_trades']} "
          f"(need {r['min_required']}, sufficient: {r['sufficient']})")
    op, hold = r["option_premium_pct"], r["option_hold_minutes"]
    if op.get("n"):
        print(f"  premium %  mean {op.get('mean')}  best {op.get('best')}  "
              f"worst {op.get('worst')}")
        print(f"  hold min   median {hold.get('p50')}  max {hold.get('best')}")
    print(f"futures rows kept separate          : "
          f"{r['futures_rows_kept_separate']}")
    print()
    for lim in r["limits"]:
        print(f"  - {lim}")
    return 0


def cmd_excursion(instrument: str) -> int:
    s = data.load_series(instrument)
    if s is None:
        print(f"no five-year series for {instrument}")
        return 1
    print(BAR)
    print(f"PHASE 31 — MEASURED EXCURSION, {instrument}, {len(s):,} bars")
    print(BAR)
    for side, name in ((excursion.LONG, "LONG"), (excursion.SHORT, "SHORT")):
        summ = excursion.summarize(excursion.build(s, side))
        print(f"{name}  decision instants {summ['decision_instants']:,}  "
              f"sessions {summ['sessions']}")
        print("  horizon  fav p50   fav p90   adv p50   adv p10")
        for h in HORIZONS:
            hz = summ["horizons"][str(h)]
            f, a = hz["favourable_pct"], hz["adverse_pct"]
            print(f"  {h:>5}m  {f['p50']:>8}  {f['p90']:>8}  "
                  f"{a['p50']:>8}  {a['p10']:>8}")
        print("  move    reach60m  medmin  reach15m  advfirst")
        for thr in PCT_GRID:
            t = summ["thresholds"][f"{thr:.2f}"]
            print(f"  {thr:>5.2f}%  {t['reached_pct_of_instants']:>8}  "
                  f"{(t['minutes_to_reach'] or {}).get('p50'):>6}  "
                  f"{t['reached_within_15m_pct']:>8}  "
                  f"{t['adverse_same_size_first_pct']:>8}")
        print()
    return 0


def _print_verdict(result: dict) -> None:
    print(BAR)
    print("PHASE 31 — HOW MUCH PERCENT, AND WHEN")
    print(BAR)
    print(f"premium distribution from stored chains : "
          f"{result['premium_book_result']}")
    print(f"  {result['premium_book_reason']}")
    print()
    real = result["realized"]
    print(f"recorded engine-resolved option fills   : "
          f"{real['usable_option_trades']} (need {real['min_required']})")
    print()
    for row in report.answers(result):
        print(f"Q {row['question']}")
        print(f"  {row['answer']}")
        print(f"  basis: {row['basis']}")
        print()
    print(SUB)
    print("research only — no gate, entry, stop, target, sizing or order path "
          "was changed by this phase")


def cmd_run() -> int:
    result = runner.study()
    files = report.write(result)
    _print_verdict(result)
    print()
    print("ARTEFACTS")
    for name, path in files.items():
        print(f"  {name:<34} {path}")
    return 0


def _load_raw() -> dict | None:
    path = os.path.join(report.out_dir(), RAW)
    if not os.path.exists(path):
        print(f"no stored result at {path} — run `run` first")
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def cmd_show() -> int:
    result = _load_raw()
    if result is None:
        return 1
    _print_verdict(result)
    return 0


def cmd_rerender() -> int:
    result = _load_raw()
    if result is None:
        return 1
    files = report.write(result)
    for name, path in files.items():
        print(f"  {name:<34} {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="phase31", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("evidence")
    sub.add_parser("realized")
    ex = sub.add_parser("excursion")
    ex.add_argument("--instrument", default="NIFTY")
    sub.add_parser("run")
    sub.add_parser("show")
    sub.add_parser("rerender")
    a = p.parse_args(argv)
    if a.cmd == "evidence":
        return cmd_evidence()
    if a.cmd == "realized":
        return cmd_realized()
    if a.cmd == "excursion":
        return cmd_excursion(a.instrument)
    if a.cmd == "run":
        return cmd_run()
    if a.cmd == "show":
        return cmd_show()
    return cmd_rerender()


if __name__ == "__main__":
    sys.exit(main())
