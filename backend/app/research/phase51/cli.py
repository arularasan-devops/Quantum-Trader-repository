"""Phase 51 CLI — read-only. It reads candles and writes artefacts, nothing else.

    python -m app.research.phase51.cli universe
    python -m app.research.phase51.cli prereg
    python -m app.research.phase51.cli grid
    python -m app.research.phase51.cli search [--instrument NIFTY] [--write]
    python -m app.research.phase51.cli exits --instrument NIFTY [--lead-conditioned]
"""
from __future__ import annotations

import argparse
import json
import statistics

from app.research.phase51 import (
    families,
    preregistration,
    report,
    search,
    simulate,
    universe,
)


def _cmd_universe(_: argparse.Namespace) -> None:
    s = universe.survey()
    print("PHASE 51 UNIVERSE")
    for d in s["eligible"]:
        print(f"  ELIGIBLE  {d['instrument']:12s} {d['bars']:>9,} bars  "
              f"{d['sessions']:>6,} sessions  volume {d['volume_present']}  "
              f"{d['provenance']}  {d['source']}")
    for d in s["refused"]:
        print(f"  REFUSED   {d['instrument']:12s} {d.get('reason')}")
    print(f"  {s['import_note']}")
    print(f"  {s['execution_note']}")


def _cmd_prereg(_: argparse.Namespace) -> None:
    print(json.dumps(preregistration(), indent=2))


def _cmd_grid(_: argparse.Namespace) -> None:
    print(json.dumps(families.grid_size(), indent=2))


def _cmd_search(a: argparse.Namespace) -> None:
    result = search.run(
        [a.instrument] if a.instrument else None,
        progress_every=a.progress_every,
    )
    print(report.render(result, top=a.top))
    if a.write:
        for p in report.write_artefacts(result):
            print(f"  wrote {p}")


def _cmd_exits(a: argparse.Namespace) -> None:
    inst = search.run_instrument(a.instrument)
    if not inst.get("eligible"):
        print(f"{a.instrument}: not eligible — {inst.get('reason')}")
        return
    out = search.profit_capture_search(
        a.instrument, inst["rows"], lead_conditioned=a.lead_conditioned,
    )
    print("PHASE 51 PROFIT_CAPTURE_EDGE")
    print(f"  {out['entry_selection']}")
    print(f"  entries considered {out['entries_considered']}   "
          f"exit variants {out['exit_variants']}   "
          f"trials {out['total_exit_hypotheses_tested']}")
    if out["rows"]:
        # Per variant rather than per row: many entries select the same bars,
        # so a row listing reads as more evidence than it is. The question this
        # search can answer is which exit, not which entry.
        print("  BY EXIT VARIANT — across the frozen entry set")
        print(f"    {'exit rule':30s} {'median disc R':>13s} "
              f"{'best disc R':>11s} {'median val R':>12s} "
              f"{'median trades':>13s}  fdr within exit search")
        for name in [v[0] for v in simulate.EXIT_VARIANTS]:
            group = [r for r in out["rows"] if r["exit_rule"] == name]
            disc = [r["discovery_net_expectancy_r"] for r in group
                    if r["discovery_net_expectancy_r"] is not None]
            val = [r["validation_net_expectancy_r"] for r in group
                   if r["validation_net_expectancy_r"] is not None]
            trd = [r["discovery_trades"] for r in group
                   if r["discovery_trades"] is not None]
            passed = sum(1 for r in group if r["fdr_pass_within_exit_search"])
            print(f"    {name:30s} "
                  f"{(statistics.median(disc) if disc else float('nan')):>13.3f} "
                  f"{(max(disc) if disc else float('nan')):>11.3f} "
                  f"{(statistics.median(val) if val else float('nan')):>12.3f} "
                  f"{(statistics.median(trd) if trd else 0):>13.0f}  "
                  f"{passed} of {len(group)}")
    else:
        print("    none — no entry cleared discovery FDR, so there is no frozen")
        print("    entry set to search exits over. --lead-conditioned widens the")
        print("    set to every discovery lead and labels the result accordingly")
    for k, v in out["refused_path_dependent_exits"].items():
        print(f"    REFUSED {k:20s} {v}")
    print(f"  {out['conditioning_caveat']}")
    print(f"  {out['edge_separation']}")


def main_argv(argv: list[str] | None = None) -> None:
    """Parse ``argv`` and run one command. ``None`` means the real process args."""
    p = argparse.ArgumentParser(prog="phase51")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("universe").set_defaults(fn=_cmd_universe)
    sub.add_parser("prereg").set_defaults(fn=_cmd_prereg)
    sub.add_parser("grid").set_defaults(fn=_cmd_grid)
    s = sub.add_parser("search")
    s.add_argument("--instrument")
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--progress-every", type=int, default=0)
    s.add_argument("--write", action="store_true")
    s.set_defaults(fn=_cmd_search)
    e = sub.add_parser("exits")
    e.add_argument("--instrument", required=True)
    e.add_argument("--lead-conditioned", action="store_true")
    e.set_defaults(fn=_cmd_exits)
    a = p.parse_args(argv)
    a.fn(a)


def main() -> None:
    main_argv(None)


if __name__ == "__main__":
    main()
