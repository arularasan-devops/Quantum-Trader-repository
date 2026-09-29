"""Phase 28 CLI — research only, paper only, no order path.

    python -m app.research.phase28.cli coverage
    python -m app.research.phase28.cli collect --instrument TCS [--years 5]
    python -m app.research.phase28.cli run [--quick] [--instrument NIFTY]
    python -m app.research.phase28.cli show

``coverage`` says what can be studied before anything is searched, and in
particular whether the cash-equity half of the question is answerable at all.
``collect`` fetches daily history for a name that has none, which is the only way
the stock question gets an answer rather than an ``INSUFFICIENT_HISTORY`` label.
``run`` is the authoritative study and writes the artefacts. ``show`` re-prints
the last verdict without recomputing it, so a verdict cannot quietly change
between two readings of it.

The stopping rule is printed next to the verdict every time, including when the
verdict is a lead, so the standard the run was held to is always on screen beside
the result it produced.
"""
from __future__ import annotations

import argparse
import sys

from app.research.phase28 import (
    EQUITY_DELIVERY,
    STOP_RULE,
    collect,
    dailybars,
    discover,
    rank,
    report,
)

BAR = "=" * 78
SUB = "-" * 78


def _fmt(v: object, width: int = 9) -> str:
    if v is None:
        return "n/a".rjust(width)
    if isinstance(v, float):
        return f"{v:>{width}.4f}"
    return str(v).rjust(width)


def cmd_coverage() -> int:
    cov = dailybars.coverage()
    print(BAR)
    print("PHASE 28 — MULTI-DAY COVERAGE (DAILY BARS, FUTURES AND CASH EQUITY)")
    print(BAR)
    print(f"eligibility: >= {dailybars.MIN_DAILY_BARS} daily bars and "
          f">= {dailybars.MIN_YEARS} chronological years")
    print()
    print("USABLE")
    for row in cov["usable"]:
        print(
            f"  {row['instrument']:<12} {row['vehicle']:<16} "
            f"{str(row['source']):<22} bars {row['daily_bars']:>5}  "
            f"years {row['span_years']:>5}  thin {row.get('thin_sessions')}"
        )
    if not cov["usable"]:
        print("  none — no instrument has enough daily history on this machine")
    print()
    print("EXCLUDED (a data status, never a strategy result)")
    for row in cov.get("excluded") or []:
        print(f"  {str(row.get('instrument')):<12} "
              f"{str(row.get('vehicle')):<16} {row.get('reason')}")
    print()
    eq = [r for r in cov["usable"] if r["vehicle"] == EQUITY_DELIVERY]
    print(f"cash-equity names testable: {len(eq)}")
    if not eq:
        print("  the stock half of the question is UNANSWERED, not answered")
        print("  negatively. To answer it, collect daily history first:")
        print("    python -m app.research.phase28.cli collect --instrument TCS")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    print(BAR)
    print("PHASE 28 — DAILY HISTORY COLLECTION")
    print(BAR)
    print("credentials are read from the environment only; nothing is printed.")
    print()
    failed = 0
    for inst in args.instrument:
        try:
            res = collect.collect(inst, years=float(args.years))
        except Exception as exc:
            failed += 1
            print(f"  {inst:<12} FAILED: {exc}")
            continue
        print(f"  {res['instrument']:<12} {res['from']} -> {res['to']}  "
              f"fetched {res['rows_fetched']:>6}  "
              f"on disk {res['rows_on_disk_before']} -> "
              f"{res['rows_on_disk_after']}")
        print(f"               {res['path']}")
        for row in res["windows_failed"]:
            print(f"               window failed {row}")
    print()
    print("limitation, carried into the report: these are the provider's own")
    print("adjusted daily bars, so splits/bonuses/dividends are only as adjusted")
    print("as the provider makes them, delisted names are absent, and a fixed")
    print("watchlist therefore carries survivorship bias. Neither is corrected.")
    print()
    print("then re-run: python -m app.research.phase28.cli run")
    return 1 if failed else 0


def _print_pools(block: dict) -> None:
    print(f"POOL ECONOMICS — {block['vehicle']}")
    print(
        f"  {'instrument':<11}{'stop':>5}{'hz':>4}{'trades':>8}{'T1%':>7}"
        f"{'net R':>9}{'gap%':>7}{'risk pts':>10}{'cost/risk':>10}"
        f"{'days':>6}{'rolls':>7}"
    )
    for p in block["pools"]:
        if not p.get("resolved"):
            continue
        print(
            f"  {p['instrument']:<11}{p['stop_band_atr']:>5}"
            f"{p['horizon_trading_days']:>4}{p['resolved']:>8}"
            f"{p['base_t1_before_sl_pct']:>7}{_fmt(p['base_avg_net_r'], 9)}"
            f"{_fmt(p.get('gap_resolved_pct'), 7)}"
            f"{_fmt(p.get('median_risk_points'), 10)}"
            f"{_fmt(p.get('cost_as_fraction_of_risk'), 10)}"
            f"{_fmt(p.get('median_trading_days_held'), 6)}"
            f"{str(p.get('rolls_charged')):>7}"
        )
    print(
        "  a 1.5R target needs 40.00% of trades to reach T1 before the stop to "
        "break even BEFORE costs"
    )
    print()


def _print_top(block: dict) -> None:
    print(f"TOP COHORTS — {block['vehicle']}")
    if not block.get("top10"):
        print("  no cohort reached the minimum development sample")
        print()
        return
    for i, r in enumerate(block["top10"], start=1):
        dev = r.get("development") or {}
        val = r.get("validation") or {}
        hold = r.get("holdout") or {}
        bench = r.get("benchmark_holdout") or {}
        print(
            f"  {i:>2}. {r['instrument']:<10} {r['side']:<6} "
            f"stop {r['stop_band_atr']}x  {r['horizon_trading_days']}d  "
            f"{r['status']}"
        )
        print(f"      conditions: {' + '.join(r['conditions'])}")
        print(f"      dev   n={dev.get('trades', 0):<6} "
              f"netR={_fmt(dev.get('avg_net_r'))}  T1%={dev.get('t1_before_sl_pct')}")
        print(f"      val   n={val.get('trades', 0):<6} "
              f"netR={_fmt(val.get('avg_net_r'))}  T1%={val.get('t1_before_sl_pct')}")
        print(f"      hold  n={hold.get('trades', 0):<6} "
              f"netR={_fmt(hold.get('avg_net_r'))}  PF={hold.get('profit_factor')}")
        wf = r.get("walk_forward") or {}
        print(f"      folds {wf.get('folds_positive', 0)}/"
              f"{wf.get('folds_scored', 0)} positive   "
              f"FDR survivor: {r.get('fdr_survivor')}   "
              f"beats passive: {bench.get('beats_benchmark')} "
              f"({bench.get('benchmark')})")
        for reason in r.get("failed_clauses") or []:
            print(f"      x {reason}")
    print()


def _print_comparison(out: dict) -> None:
    print(BAR)
    print("HOLDING PERIOD COMPARISON — WHAT THE HORIZON ACTUALLY CHANGED")
    print(BAR)
    rows = (out.get("holding_period_comparison") or {}).get("rows") or []
    print(f"  {'holding period':<34}{'instrument':<11}{'stop':>5}"
          f"{'risk pts':>10}{'cost pts':>10}{'cost/risk':>10}{'net R':>9}")
    for row in rows:
        print(
            f"  {str(row.get('holding_period')):<34}"
            f"{str(row.get('instrument')):<11}"
            f"{_fmt(row.get('stop_band_atr'), 5)}"
            f"{_fmt(row.get('median_risk_points'), 10)}"
            f"{_fmt(row.get('median_cost_points'), 10)}"
            f"{_fmt(row.get('cost_as_fraction_of_risk'), 10)}"
            f"{_fmt(row.get('avg_net_r'), 9)}"
        )
    cost = (out.get("answers") or {}).get(
        "did_the_longer_horizon_fix_the_cost_arithmetic"
    ) or {}
    print()
    print("  median cost/risk intraday (Phase 27): "
          f"{cost.get('median_cost_as_fraction_of_risk_intraday')}")
    print("  median cost/risk daily (this phase):  "
          f"{cost.get('median_cost_as_fraction_of_risk_daily')}")
    print()


def _print_answers(out: dict) -> None:
    print(BAR)
    print("DIRECT ANSWERS")
    print(BAR)
    ans = out.get("answers") or {}
    print("  does a multi-day horizon contain a directional edge?")
    print(f"    {ans.get('does_a_multi_day_horizon_contain_a_directional_edge')}")
    print()
    for label, key in (
        ("FUTURES", "futures_verdict"), ("CASH EQUITY", "cash_equity_verdict")
    ):
        row = ans.get(key) or {}
        print(f"  {label}: {row.get('status')}")
        if row.get("note"):
            print(f"    {row['note']}")
        d = row.get("diagnostics") or {}
        for name, value in d.items():
            print(f"    {name:<36}{value}")
        best = row.get("closest_cohort")
        if isinstance(best, dict):
            print(f"    closest: {best['instrument']} {best['side']} stop "
                  f"{best['stop_band_atr']}x {best['horizon_trading_days']}d — "
                  f"{best['status']}")
            print(f"      {' + '.join(best.get('conditions') or [])}")
            print(f"      dev {_fmt(best.get('development_avg_net_r'))} | "
                  f"val {_fmt(best.get('validation_avg_net_r'))} | "
                  f"hold {_fmt(best.get('holdout_avg_net_r'))} "
                  f"(n={best.get('holdout_trades')}, "
                  f"PF={best.get('holdout_profit_factor')})")
            for reason in best.get("failed_clauses") or []:
                print(f"      x {reason}")
        elif best:
            print(f"    closest: {best}")
        print()
    bench = ans.get("can_any_long_rule_beat_buy_and_hold") or {}
    print(f"  beats buy-and-hold: {bench.get('cohorts_beating_buy_and_hold')} of "
          f"{bench.get('cohorts_compared')} comparable cohorts")
    gaps = ans.get("what_overnight_gaps_cost") or {}
    print("  trades resolved by a gap through the level: "
          f"{gaps.get('pct_of_trades_resolved_by_a_gap_through_the_level')}%")
    print(f"  hypotheses evaluated: {ans.get('how_many_hypotheses_were_tried')}")
    print(f"  anything promoted: {ans.get('is_anything_promoted')}")
    print()


def _print_conclusion(out: dict) -> None:
    c = out.get("conclusion") or {}
    print(BAR)
    print(f"VERDICT: {c.get('verdict')} — {c.get('headline')}")
    print(BAR)
    if c.get("statement"):
        print(c["statement"])
        print()
    for row in c.get("closest") or []:
        if not isinstance(row, dict):
            continue
        print(f"  closest {row.get('vehicle')}: {row.get('instrument')} "
              f"{row.get('side')} stop {row.get('stop_band_atr')}x "
              f"{row.get('horizon_trading_days')}d")
        print(f"    {' + '.join(row.get('conditions') or [])}")
        print(f"    holdout n={row.get('holdout_trades')} "
              f"netR={_fmt(row.get('holdout_avg_net_r'))} "
              f"PF={row.get('holdout_profit_factor')}")
        for reason in row.get("failed_clauses") or []:
            print(f"    x {reason}")
        print()
    for lead in c.get("leads") or []:
        print(f"  LEAD {lead.get('strategy_id')} {lead.get('instrument')} "
              f"{lead.get('side')} {lead.get('horizon_trading_days')}d: "
              f"{' + '.join(lead.get('conditions') or [])}")
    eq = c.get("equity_caveat") or out.get("equity_history") or {}
    if eq.get("status") == "INSUFFICIENT_HISTORY":
        print(SUB)
        print("CASH EQUITY — UNANSWERED, NOT REJECTED")
        print(f"  {eq.get('note')}")
        print(f"  {eq.get('how_to_answer_it')}")
    print(SUB)
    print(f"STOPPING RULE — {STOP_RULE}")
    print(f"stopping rule triggered: {c.get('stop_rule_triggered')}")
    if c.get("stop_rule_triggered"):
        print("no further strategy-search phase should be created on this evidence.")
    print(SUB)
    print("RESEARCH ONLY. PAPER ONLY. Nothing in this study is wired to the live")
    print("engine, the gates, the stop/target/exit or any order path, option")
    print("strategy logic and the option-book capture are untouched, and the")
    print(f"highest label it can publish is {rank.RESEARCH_LEAD}.")
    print(BAR)


def _show(out: dict) -> None:
    print(BAR)
    print(f"PHASE 28 — MULTI-DAY FUTURES & EQUITY STUDY ({out.get('version')})")
    print(BAR)
    print(f"instruments {out.get('instruments')}   quick={out.get('quick_mode')}   "
          f"{out.get('runtime_seconds')}s   data {out.get('data_status')}")
    print(f"stop bands {list(discover.STOP_BANDS)}   horizons "
          f"{list(discover.HORIZONS)} trading sessions")
    print()
    for block in out.get("vehicle_results") or []:
        if block.get("windows"):
            print(f"WINDOWS — {block['vehicle']}")
            for name, w in block["windows"].items():
                if isinstance(w, dict) and w.get("from"):
                    print(f"  {name:<12} {w['from']} -> {w['to']}")
            print()
        _print_pools(block)
        _print_top(block)
    _print_comparison(out)
    _print_answers(out)
    _print_conclusion(out)


def cmd_run(args: argparse.Namespace) -> int:
    instruments = tuple(args.instrument) if args.instrument else None
    out = report.run(instruments=instruments, quick=bool(args.quick))
    _show(out)
    print()
    print("artefacts:")
    for name, path in (out.get("artefacts") or {}).items():
        print(f"  {name:<18} {path}")
    return 0


def cmd_show() -> int:
    out = report.latest()
    if out is None:
        print("no Phase 28 report on disk yet — run: "
              "python -m app.research.phase28.cli run")
        return 1
    print(BAR)
    print(f"PHASE 28 — LAST VERDICT ({out.get('version')})")
    print(BAR)
    print(f"instruments {out.get('instruments')}   quick={out.get('quick_mode')}   "
          f"{out.get('runtime_seconds')}s   data {out.get('data_status')}")
    print()
    _print_answers(out)
    _print_conclusion(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="phase28", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage", help="what can be studied, before anything is run")
    col = sub.add_parser(
        "collect", help="fetch daily history for a name that has none"
    )
    col.add_argument("--instrument", action="append", required=True,
                     help="instrument to collect (repeatable)")
    col.add_argument("--years", type=float, default=5.0,
                     help="how many years back to request (default 5)")
    run_p = sub.add_parser("run", help="the authoritative multi-day study")
    run_p.add_argument("--quick", action="store_true",
                       help="fewer stop bands, one horizon, no geometry sweep — "
                            "a smoke run, never the answer")
    run_p.add_argument("--instrument", action="append",
                       help="restrict to an instrument (repeatable)")
    sub.add_parser("show", help="re-print the last verdict without recomputing it")
    args = ap.parse_args(argv)
    if args.cmd == "coverage":
        return cmd_coverage()
    if args.cmd == "collect":
        return cmd_collect(args)
    if args.cmd == "run":
        return cmd_run(args)
    return cmd_show()


if __name__ == "__main__":
    sys.exit(main())
