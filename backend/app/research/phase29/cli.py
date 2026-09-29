"""Phase 29 CLI — run the defined-risk credit-spread study, or print the last one.

    python -m app.research.phase29.cli coverage
    python -m app.research.phase29.cli run [--quick] [--instrument NIFTY ...]
    python -m app.research.phase29.cli show

``--db-path`` points the study at another captured store (an archived session
store, or a merge of several) instead of the live one, and ``--out-dir`` keeps its
artefacts out of the live report. Both default to the live paths.

Read-only with respect to trading: this command reads the captured store, writes
report artefacts under ``data/phase29`` and touches nothing in the signal, paper
or order path. It does not modify the option capture or the existing option
strategy logic.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase29 import (
    MARGIN_CLAIM,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    books_min_note,
    legs,
    outcomes,
    report,
)


def _print_coverage(cov: dict) -> None:
    print("PHASE 29 — CAPTURED LADDER COVERAGE (can a spread be priced at all?)")
    if cov.get("unmeasured"):
        print(f"  unmeasured: {cov['cause']}")
        print(f"  {cov['note']}")
        return
    for row in cov["eligible"]:
        print(f"  ELIGIBLE     {row['instrument']:<12} snapshots "
              f"{row['snapshots']:>7,} · sessions {row['sessions']:>3} · "
              f"strikes {row.get('distinct_strikes_captured', 0):>4} · "
              f"strike step {row.get('strike_step_measured')}")
    for row in cov["reported_only"]:
        n = row.get("snapshots", row.get("real_broker_snapshots", 0))
        print(f"  REPORTED     {row['instrument']:<12} snapshots {n:>7,} · "
              f"{'; '.join(row.get('reasons') or [])}")
    print("")
    print(f"  {cov['note']}")
    print(f"  {books_min_note()}")
    print(f"  {cov['window_claim']}")


def _print_report(out: dict) -> None:
    print(f"PHASE 29 — {out['version']} · {out.get('runtime_seconds')}s"
          f"{' · QUICK' if out.get('quick_mode') else ''}")
    print("")
    g = out.get("geometry") or {}
    if g:
        print("EXECUTION MODEL")
        print(f"  entry {g['entry']}")
        print(f"  exit  {g['exit']}")
        print(f"  costs {g['costs_charged']}")
        print(f"  defined loss {g['defined_loss']}")
        print(f"  slippage {g['slippage_pct_per_side_per_leg']}% per side per leg · "
              f"stop bands {g['stop_credit_multiples']}x credit · "
              f"target {g['take_profit_fractions_of_credit'][0]} of credit")
        print(f"  split {g['split']}")
        print("")

    sweep = [
        {"instrument": r["instrument"], **row}
        for r in out.get("instruments") or []
        for row in (r.get("unconditional") or [])
    ]
    scored = [r for r in sweep if isinstance(r.get("avg_net_r"), (int, float))]
    if scored:
        scored.sort(key=lambda r: r["avg_net_r"], reverse=True)
        print("UNCONDITIONAL SWEEP — selling the structure with no rule at all")
        print(f"  {'instrument':<11} {'structure':<18} {'hold':<22} {'otm':>3} "
              f"{'w':>2} "
              f"{'stop':>5} {'n':>6} {'kept%':>7} {'need%':>7} {'netR':>8} "
              f"{'friction%':>10}")
        for r in scored[:14]:
            print(f"  {r['instrument']:<11} {r['structure']:<18} "
                  f"{str(r.get('hold')):<22} "
                  f"{r['short_steps_otm']:>3} {r['width_steps']:>2} "
                  f"{r['stop_credit_multiple']:>5} {r['trades']:>6,} "
                  f"{r['target_before_stop_pct']:>7} "
                  f"{r['breakeven_target_pct_before_costs']:>7} "
                  f"{r['avg_net_r']:>8} "
                  f"{r.get('median_friction_pct_of_credit'):>10}")
        positive = [r for r in scored if r["avg_net_r"] > 0]
        print(f"  {len(positive)} of {len(scored)} scored geometries positive "
              "before any condition")
        print("")

    ranked = out.get("ranked") or []
    if ranked:
        print(f"RANKED COHORTS — {len(ranked)} evaluated, best first")
        for r in ranked[:10]:
            hold = r.get("holdout") or {}
            print(f"  {r['status']:<18} {r['instrument']:<10} {r['structure']:<18} "
                  f"{str(r.get('hold')):<22} {'+'.join(r['conditions'])}")
            print(f"    holdout n={hold.get('trades')} netR={hold.get('avg_net_r')} "
                  f"PF={hold.get('profit_factor')} · folds "
                  f"{r.get('folds_positive')}/{r.get('folds_measurable')} · "
                  f"score {r.get('score')}")
            for reason in (r.get("failed_clauses") or [])[:3]:
                print(f"    - {reason}")
        print("")

    holds = [r for r in (out.get("hold_comparison") or []) if r.get("trades")]
    if holds:
        print("HOLD COMPARISON — how long the structure was kept open")
        print(f"  {'hold':<22} {'n':>7} {'netR':>9} {'kept%':>7} {'timeout%':>9} "
              f"{'geoms+':>7}")
        for r in holds:
            print(f"  {r['hold']:<22} {r['trades']:>7,} "
                  f"{r['trade_weighted_avg_net_r']:>9} "
                  f"{r['trade_weighted_target_before_stop_pct']:>7} "
                  f"{r['trade_weighted_timeout_pct']:>9} "
                  f"{r['positive_geometries']}/{r['scored_geometries']:>4}")
        print("  trade-weighted across geometries; a description of the sweep, "
              "not a strategy. Nothing is held overnight.")
        print("")

    bvs = out.get("buyer_versus_seller") or {}
    if bvs.get("available"):
        print("BUYER VERSUS SELLER")
        print(f"  buyer best avg net R {bvs['buyer_best_avg_net_r']} "
              f"({bvs['buyer_verdict']}) · {bvs['source']}")
        print(f"  {bvs['note']}")
        print("")

    c = out.get("conclusion") or {}
    print(f"VERDICT: {c.get('verdict')}")
    print(f"  {c.get('headline')}")
    print(f"  hypotheses evaluated: {out.get('hypotheses_evaluated'):,}")
    print(f"  window: {c.get('window', out.get('window_claim'))}")
    print(f"  margin: {MARGIN_CLAIM}")
    best = c.get("best_unconditional_row")
    if best:
        print(f"  best unconditional: {best['structure']} {best['short_steps_otm']} "
              f"steps OTM, width {best['width_steps']}, stop "
              f"{best['stop_credit_multiple']}x — kept "
              f"{best['target_before_stop_pct']}% against "
              f"{best['breakeven_target_pct_before_costs']}% needed, "
              f"{best['avg_net_r']}R")
    for row in out.get("answers") or []:
        print("")
        print(f"  Q: {row['question']}")
        print(f"  A: {row['answer']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="phase29",
        description=(
            "Defined-risk option credit-spread research over captured books. "
            "Research and paper only: nothing here places or influences an order."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    cov = sub.add_parser("coverage", help="which instruments can be studied at all")
    cov.add_argument("--db-path", default=None)
    cov.add_argument("--json", action="store_true")

    run = sub.add_parser("run", help="run the study and write the artefacts")
    run.add_argument("--quick", action="store_true",
                     help="one structure, one stop band, no robustness grid")
    run.add_argument("--instrument", action="append", default=None)
    run.add_argument("--db-path", default=None)
    run.add_argument("--out-dir", default=None)
    run.add_argument("--json", action="store_true")

    show = sub.add_parser("show", help="print the last written study")
    show.add_argument("--json", action="store_true")

    sub.add_parser("geometry", help="print the frozen structure set")

    args = ap.parse_args(argv)

    if args.cmd == "coverage":
        out = report.coverage(db_path=args.db_path)
        print(json.dumps(report.jsonable(out), indent=1, default=str)
              if args.json else "", end="")
        if not args.json:
            _print_coverage(out)
        return 0

    if args.cmd == "run":
        out = report.run(
            quick=bool(args.quick), instruments=args.instrument,
            db_path=args.db_path, out_dir=args.out_dir,
        )
        if args.json:
            print(json.dumps(report.jsonable(out), indent=1, default=str))
        else:
            _print_report(out)
        verdict = (out.get("conclusion") or {}).get("verdict")
        return 0 if verdict in (RESEARCH_LEAD, REQUIRES_MORE_DATA) else 1

    if args.cmd == "show":
        out = report.latest()
        if not out:
            print("no Phase 29 study has been run on this machine yet")
            return 1
        if args.json:
            print(json.dumps(report.jsonable(out), indent=1, default=str))
        else:
            _print_report(out)
        return 0

    if args.cmd == "geometry":
        print("PHASE 29 — FROZEN STRUCTURE SET (declared before any measurement)")
        for structure in legs.STRUCTURES:
            print(f"  {structure:<18} underlying view {legs.VIEW[structure]}")
        print(f"  short strike: {list(legs.SHORT_STEPS)} strike steps out of the "
              "money, step measured from the captured ladder")
        print(f"  protective strike: {list(legs.WIDTH_STEPS)} further steps out")
        print(f"  stop: {list(outcomes.STOP_CREDIT_MULTIPLES)} x credit "
              f"(capped at the defined loss)")
        print(f"  targets: {outcomes.TAKE_1} / {outcomes.TAKE_2} / "
              f"{outcomes.TAKE_3} of the credit")
        for name, (horizon, steps) in outcomes.HOLDS.items():
            print(f"  hold {name:<22} {horizon}s, at most {steps} paired "
                  "forward quotes, never past the session it opened in")
        return 0

    return 2


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
