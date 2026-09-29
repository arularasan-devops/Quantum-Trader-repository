"""Phase 27 CLI — research only, paper only, no order path.

    python -m app.research.phase27.cli coverage
    python -m app.research.phase27.cli run [--quick] [--instrument NIFTY]
                                           [--timeframe 15]
    python -m app.research.phase27.cli show

``coverage`` says what can be studied before anything is searched. ``run`` is the
authoritative study and writes the artefacts. ``show`` re-prints the last run
without recomputing it, so the verdict cannot quietly change between readings.

The stopping rule is printed with the verdict every time, including when the
verdict is a lead, so the standard the run was held to is always on screen next to
the result it produced.
"""
from __future__ import annotations

import argparse
import sys

from app.research.phase27 import STOP_RULE, bars, rank, report

BAR = "=" * 78
SUB = "-" * 78


def _fmt(v: object, width: int = 9) -> str:
    if v is None:
        return "n/a".rjust(width)
    if isinstance(v, float):
        return f"{v:>{width}.4f}"
    return str(v).rjust(width)


def cmd_coverage() -> int:
    cov = bars.coverage()
    print(BAR)
    print("PHASE 27 — HIGHER-TIMEFRAME FUTURES COVERAGE (5m / 15m)")
    print(BAR)
    print(f"timeframes: {cov['timeframes']}")
    print()
    print("USABLE 1-MINUTE SERIES (the source; Phase 24's own eligibility bar)")
    for row in cov["usable"]:
        print(
            f"  {row['instrument']:<12} bars {row.get('bars'):>9}  "
            f"sessions {row.get('sessions'):>5}"
        )
    if not cov["usable"]:
        print("  none — nothing can be aggregated on this machine")
    print()
    print("EXCLUDED")
    for row in cov.get("excluded") or []:
        print(f"  {row.get('instrument', '?'):<12} {row.get('reason')}")
    print()
    print("AGGREGATED BARS")
    for row in cov["resampled"]:
        print(
            f"  {row['instrument']:<12} {row['timeframe_minutes']:>3}m  "
            f"bars {row['bars']:>8}  full {row['full_bars']:>8}  "
            f"short {row['short_bars']:>6} ({row['short_bar_pct']}%)  "
            f"spans a session: {row['spans_a_session']}"
        )
    print()
    print(cov["one_minute_reference"])
    return 0


def _print_pools(block: dict) -> None:
    print(f"POOL ECONOMICS — {block['timeframe_minutes']}-MINUTE BARS")
    print(
        f"  {'instrument':<12}{'stop':>6}{'trades':>9}{'T1%':>8}"
        f"{'net R':>10}{'timeout%':>10}{'risk pts':>10}{'cost/risk':>11}"
    )
    for p in block["pools"]:
        if not p.get("resolved"):
            continue
        print(
            f"  {p['instrument']:<12}{p['stop_band_atr']:>6}"
            f"{p['resolved']:>9}{p['base_t1_before_sl_pct']:>8}"
            f"{_fmt(p['base_avg_net_r'], 10)}{p['base_timeout_pct']:>10}"
            f"{_fmt(p['median_atr_points'], 10)}"
            f"{_fmt(p['cost_as_fraction_of_risk'], 11)}"
        )
    print(
        "  a 1.5R target needs 40.00% of trades to reach T1 before the stop to "
        "break even BEFORE costs"
    )
    print()


def _print_top(block: dict) -> None:
    print(f"TOP COHORTS — {block['timeframe_minutes']}-MINUTE BARS")
    if not block["top10"]:
        print("  no cohort reached the minimum development sample")
        print()
        return
    for i, r in enumerate(block["top10"], start=1):
        dev = r.get("development") or {}
        val = r.get("validation") or {}
        hold = r.get("holdout") or {}
        print(
            f"  {i:>2}. {r['instrument']:<10} {r['side']:<6} "
            f"stop {r['stop_band_atr']}x  {r['status']}"
        )
        print(f"      conditions: {' + '.join(r['conditions'])}")
        print(
            f"      dev   n={dev.get('trades', 0):<6} netR={_fmt(dev.get('avg_net_r'))}"
            f"  T1%={dev.get('t1_before_sl_pct')}"
        )
        print(
            f"      val   n={val.get('trades', 0):<6} netR={_fmt(val.get('avg_net_r'))}"
            f"  T1%={val.get('t1_before_sl_pct')}"
        )
        print(
            f"      hold  n={hold.get('trades', 0):<6} "
            f"netR={_fmt(hold.get('avg_net_r'))}  PF={hold.get('profit_factor')}"
        )
        wf = r.get("walk_forward") or {}
        print(
            f"      folds {wf.get('folds_positive', 0)}/{wf.get('folds_scored', 0)} "
            f"positive   FDR survivor: {r.get('fdr_survivor')}"
        )
        for reason in r.get("failed_clauses") or []:
            print(f"      x {reason}")
    print()


def _print_comparison(out: dict) -> None:
    print(BAR)
    print("TIMEFRAME COMPARISON — WHAT THE BAR SIZE ACTUALLY CHANGED")
    print(BAR)
    rows = (out.get("comparison") or {}).get("rows") or []
    print(
        f"  {'tf':>4}  {'instrument':<12}{'stop':>6}{'trades':>9}{'T1%':>8}"
        f"{'breakeven%':>12}{'net R':>10}{'cost/risk':>11}"
    )
    for row in sorted(
        rows, key=lambda r: (r["timeframe_minutes"], str(r.get("instrument")),
                            r.get("stop_band_atr") or 0)
    ):
        print(
            f"  {row['timeframe_minutes']:>3}m  {str(row.get('instrument')):<12}"
            f"{_fmt(row.get('stop_band_atr'), 6)}"
            f"{str(row.get('trades')):>9}{_fmt(row.get('t1_before_sl_pct'), 8)}"
            f"{_fmt(row.get('breakeven_t1_pct_before_costs'), 12)}"
            f"{_fmt(row.get('avg_net_r'), 10)}"
            f"{_fmt(row.get('cost_as_fraction_of_risk'), 11)}"
        )
    ans = out.get("answers") or {}
    cost = ans.get("does_a_higher_timeframe_fix_the_cost_arithmetic") or {}
    direction = ans.get("does_a_higher_timeframe_fix_the_direction_problem") or {}
    print()
    print(f"  cost as a fraction of risk, by timeframe: "
          f"{cost.get('median_cost_as_fraction_of_risk_by_timeframe')}")
    print(f"  did the timeframe fix the cost arithmetic? {cost.get('answer')}")
    print(f"  did it fix the direction?                  {direction.get('answer')}")
    print()


def _print_answers(out: dict) -> None:
    print(BAR)
    print("DIRECT ANSWERS")
    print(BAR)
    ans = out.get("answers") or {}
    for tf, row in (ans.get("per_timeframe") or {}).items():
        print(f"  {tf} bars")
        print(f"    hypotheses evaluated                {row['hypotheses_evaluated']}")
        print(f"    cohorts ranked                      {row['cohorts_ranked']}")
        print(f"    cleared development                 {row['cleared_development']}")
        print(f"    positive validation                 {row['positive_validation']}")
        print(f"    positive untouched holdout          "
              f"{row['positive_untouched_holdout']}")
        print(f"    positive in all three windows       "
              f"{row['positive_in_all_three_windows']}")
        print(f"    walk-forward stable                 {row['walk_forward_stable']}")
        print(f"    survives every stress variant       "
              f"{row['survives_every_stress_variant']}")
        print(f"    survives multiple-testing (FDR)     {row['fdr_survivors']}")
        print(f"    ANY CANDIDATE PASSING EVERY GATE    "
              f"{row['any_candidate_surviving_all_gates']}")
        best = row.get("strongest_candidate")
        if isinstance(best, dict):
            print(f"    strongest: {best['instrument']} {best['side']} "
                  f"stop {best['stop_band_atr']}x — {best['status']}")
            print(f"      {' + '.join(best['conditions'])}")
            print(f"      dev {_fmt(best['development_avg_net_r'])} | "
                  f"val {_fmt(best['validation_avg_net_r'])} | "
                  f"hold {_fmt(best['holdout_avg_net_r'])} "
                  f"(n={best['holdout_trades']}, PF={best['holdout_profit_factor']})")
            for reason in best.get("why_it_failed") or []:
                print(f"      x {reason}")
        else:
            print(f"    strongest: {best}")
        print()
    print(f"  option strategy logic touched   {ans.get('option_strategy_logic_touched')}")
    print(f"  option capture touched          {ans.get('option_capture_touched')}")
    print(f"  anything ready for paper trade  "
          f"{ans.get('anything_ready_for_paper_trading')}")
    print()


def _print_conclusion(out: dict) -> None:
    c = out.get("conclusion") or {}
    print(BAR)
    print(f"VERDICT: {c.get('verdict')} — {c.get('headline')}")
    print(BAR)
    if c.get("statement"):
        print(c["statement"])
        print()
    for row in c.get("closest_candidate") or []:
        if not isinstance(row, dict):
            continue
        hold = row.get("holdout") or {}
        print(f"  closest at {row['timeframe_minutes']}m: {row['instrument']} "
              f"{row['side']} stop {row['stop_band_atr']}x")
        print(f"    {' + '.join(row['conditions'])}")
        print(f"    holdout n={hold.get('trades')} netR={_fmt(hold.get('avg_net_r'))} "
              f"PF={hold.get('profit_factor')}")
        for reason in row.get("why_it_failed") or []:
            print(f"    x {reason}")
        print()
    for lead in c.get("leads") or []:
        print(f"  LEAD {lead['strategy_id']} {lead['instrument']} {lead['side']} "
              f"at {lead['timeframe_minutes']}m: {' + '.join(lead['conditions'])}")
    print(SUB)
    print(f"STOPPING RULE — {STOP_RULE}")
    print(f"stopping rule triggered: {c.get('stop_rule_triggered')}")
    if c.get("stop_rule_triggered"):
        print("no further strategy-search phase should be created on this evidence.")
    print(SUB)
    print("RESEARCH ONLY. PAPER ONLY. Nothing in this study is wired to the live")
    print("engine, the gates, the stop/target/exit or any order path, and the")
    print(f"highest label it can publish is {rank.RESEARCH_LEAD}.")
    print(out.get("spread_claim"))
    print(BAR)


def cmd_run(args: argparse.Namespace) -> int:
    timeframes = (int(args.timeframe),) if args.timeframe else bars.TIMEFRAMES
    instruments = tuple(args.instrument) if args.instrument else None
    out = report.run(
        instruments=instruments, timeframes=timeframes, quick=bool(args.quick)
    )
    _show(out)
    print()
    print("artefacts:")
    for name, path in (out.get("artefacts") or {}).items():
        print(f"  {name:<18} {path}")
    return 0


def _show(out: dict) -> None:
    print(BAR)
    print(f"PHASE 27 — HIGHER-TIMEFRAME FUTURES STUDY ({out.get('version')})")
    print(BAR)
    print(f"instruments {out.get('instruments')}   timeframes "
          f"{out.get('timeframes')}   quick={out.get('quick_mode')}   "
          f"{out.get('runtime_seconds')}s")
    print(out.get("resample_claim"))
    print()
    blocks = out.get("timeframe_results") or out.get("per_timeframe") or []
    for block in blocks:
        if block.get("windows"):
            print(f"WINDOWS — {block['timeframe_minutes']}-MINUTE BARS")
            for name, w in block["windows"].items():
                print(f"  {name:<12} {w['from']} -> {w['to']}")
            print()
        _print_pools(block)
        if block.get("top10"):
            _print_top(block)
    _print_comparison(out)
    _print_answers(out)
    _print_conclusion(out)


def cmd_show() -> int:
    out = report.latest()
    if out is None:
        print("no Phase 27 report on disk yet — run: "
              "python -m app.research.phase27.cli run")
        return 1
    _show(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="phase27", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage", help="what can be studied, before anything is run")
    run_p = sub.add_parser("run", help="the authoritative 5m/15m study")
    run_p.add_argument("--quick", action="store_true",
                       help="two stop bands, no geometry sweep, spread/slippage "
                            "stress only — a smoke run, never the answer")
    run_p.add_argument("--instrument", action="append",
                       help="restrict to an instrument (repeatable)")
    run_p.add_argument("--timeframe", type=int, choices=list(bars.TIMEFRAMES),
                       help="restrict to one timeframe")
    sub.add_parser("show", help="re-print the last run without recomputing it")
    args = ap.parse_args(argv)
    if args.cmd == "coverage":
        return cmd_coverage()
    if args.cmd == "run":
        return cmd_run(args)
    return cmd_show()


if __name__ == "__main__":
    sys.exit(main())
