"""Phase 24 CLI — run the five-year discovery study, or print the last one.

    python -m app.research.phase24.cli coverage
    python -m app.research.phase24.cli run [--quick] [--instrument NIFTY]
    python -m app.research.phase24.cli show

Read-only with respect to trading: this command reads history, writes report
artefacts under ``data/phase24`` and touches nothing in the signal, paper or
order path.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase24 import VALIDATED, data, report


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.4f}"
    return str(v)


def _print_coverage(cov: dict, books: dict) -> None:
    print("PHASE 24 — 5-YEAR DATA COVERAGE")
    for row in cov["usable"]:
        print(f"  USABLE      {row['instrument']:<12} bars {row['bars']:>9,} "
              f"sessions {row['sessions']:>5}")
    for row in cov["excluded"]:
        print(f"  INSUFFICIENT {row['instrument']:<11} bars {row['bars']:>7,} · "
              f"{row['reason']}")
    print("")
    print(f"  {cov['equity_note']}")
    print("")
    print("HISTORICAL OPTION BOOKS")
    print(f"  snapshots {books['snapshots']:,} · real-broker "
          f"{books['real_broker_snapshots']:,} · with two-sided book "
          f"{books['snapshots_with_two_sided_book']:,}")
    print(f"  {books['note']}")
    for inst, n in (books.get("two_sided_by_instrument") or {}).items():
        print(f"    {inst:<12} {n:>9,} snapshots with a two-sided book")
    if not cov["usable"]:
        print("")
        print("  NO INSTRUMENT ON THIS MACHINE HAS A FIVE-YEAR 1-MINUTE SERIES.")
        print("  The study cannot run and will report REQUIRES_MORE_DATA rather")
        print("  than a negative result. Expected files, one JSON object per line")
        print("  with time/open/high/low/close/volume:")
        for inst, rel in sorted(data.BACKTEST_FILES.items()):
            print(f"    {inst:<10} backend/{rel}")


def _print_report(out: dict) -> None:
    print(f"PHASE 24 — {out['version']} · {out['runtime_seconds']}s"
          f"{' · QUICK' if out['quick_mode'] else ''}")
    print("")
    print("WINDOWS")
    for name, w in out["windows"].items():
        print(f"  {name:<12} {w['from']} -> {w['to']}")
    print("")
    print("POOLS (no entry rule applied — this is the coin every rule must beat)")
    print("  INSTRUMENT  STOP  TRADES     T1%   AVG_NET_R  COST/RISK")
    for p in out["pools"]:
        print(f"  {p['instrument']:<11} {p['stop_band_atr']:<5} "
              f"{p.get('resolved', 0):>7,} {_fmt(p.get('base_t1_before_sl_pct')):>7} "
              f"{_fmt(p.get('base_avg_net_r')):>11} "
              f"{_fmt(p.get('cost_as_fraction_of_risk')):>10}")
    print("")
    print(f"hypotheses evaluated {out['hypotheses_evaluated']:,} · cleared "
          f"development {out['candidates_surviving_development']} · cohorts carried "
          f"{out.get('cohorts_examined_including_near_misses', 0)}")
    print("")
    print("TOP 10 DISCOVERED STRATEGIES")
    for r in out["top10"]:
        h = r["holdout"]
        print(f"  {r['strategy_id']} {r['instrument']:<9} {r['side']:<5} "
              f"stop {r['stop_band_atr']}xATR · {' AND '.join(r['conditions'])}")
        print(f"      dev {r['development']['trades']:>6} trades "
              f"T1 {_fmt(r['development']['t1_before_sl_pct'])}% "
              f"netR {_fmt(r['development']['avg_net_r'])} | "
              f"val netR {_fmt(r['validation'].get('avg_net_r'))} | "
              f"holdout {h.get('trades', 0)} trades netR {_fmt(h.get('avg_net_r'))} "
              f"PF {_fmt(h.get('profit_factor'))}")
        print(f"      walk-forward {r['walk_forward']['folds_positive']}/"
              f"{r['walk_forward']['folds_scored']} folds positive · score "
              f"{_fmt(r['score'])} · {r['status']}")
        if r["failed_clauses"]:
            print(f"      failed: {'; '.join(r['failed_clauses'])}")
    print("")
    print("BASELINE COMPARISON (same geometry, costs and windows)")
    for inst, rows in out["baselines"].items():
        print(f"  {inst}")
        for label, b in rows.items():
            print(f"    {label:<44} trades {b['trades']:>7,} "
                  f"T1 {_fmt(b['t1_before_sl_pct']):>7}% netR "
                  f"{_fmt(b['avg_net_r']):>9} PF {_fmt(b['profit_factor'])}")
    if out.get("geometry_sweep"):
        print("")
        print("GEOMETRY SWEEP (no entry rule; breakeven vs achieved)")
        for g in out["geometry_sweep"]:
            print(f"  {g['instrument']:<9} stop {g['stop_band_atr']}xATR "
                  f"T1 {g['t1_r']}R · achieved {_fmt(g['t1_before_sl_pct'])}% vs "
                  f"breakeven {_fmt(g['breakeven_t1_pct_before_costs'])}% "
                  f"· netR {_fmt(g['avg_net_r'])} · cost/risk "
                  f"{_fmt(g['cost_as_fraction_of_risk'])}")
    print("")
    print("VEHICLES")
    for name, v in out["vehicles"].items():
        print(f"  {name:<9} {v['status']}")
    print("")
    print("ANSWERS")
    for k, v in out["answers"].items():
        if k in ("cost_wall_note",):
            continue
        print(f"  {k}: {json.dumps(v, default=str)[:300]}")
    print("")
    c = out["conclusion"]
    print("=" * 72)
    print(f"BEST VALIDATED STRATEGY: {c['BEST_VALIDATED_STRATEGY']}")
    print(f"EXPECTED T1-BEFORE-SL:   {_fmt(c['EXPECTED_T1_BEFORE_SL'])}")
    print(f"NET EXPECTANCY:          {_fmt(c['NET_EXPECTANCY_R'])}")
    print(f"PROFIT FACTOR:           {_fmt(c['PROFIT_FACTOR'])}")
    print(f"TRADES PER WEEK:         {_fmt(c['TRADES_PER_WEEK'])}")
    print(f"STATUS:                  {c['STATUS']}")
    if c["BEST_VALIDATED_STRATEGY"] == "NONE":
        closest = c["CLOSEST_CANDIDATE"]
        if isinstance(closest, dict):
            print("")
            print(f"CLOSEST CANDIDATE:       {closest['strategy_id']} "
                  f"{closest['instrument']} {closest['side']} "
                  f"stop {closest['stop_band_atr']}xATR")
            print(f"  conditions:            {' AND '.join(closest['conditions'])}")
            print(f"  development net R:     {_fmt(closest['development']['avg_net_r'])}")
            print(f"  validation net R:      {_fmt(closest['validation'].get('avg_net_r'))}")
            print(f"  holdout net R:         {_fmt(closest['holdout'].get('avg_net_r'))}")
            print("  why it failed:")
            for reason in closest["why_it_failed"]:
                print(f"    - {reason}")
        else:
            print(f"CLOSEST CANDIDATE:       {closest}")
    print("=" * 72)
    print("PAPER/RESEARCH ONLY — no production signal, gate or order path is "
          "changed by this study.")
    if not any(r["status"] == VALIDATED for r in out["ranked"]):
        print("No strategy is VALIDATED, so no fingerprint is published for live "
              "matching.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 24 five-year discovery study")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage", help="what history actually exists")
    run = sub.add_parser("run", help="run the study and write artefacts")
    run.add_argument("--quick", action="store_true",
                     help="two stop bands, no geometry sweep, short stress grid")
    run.add_argument("--instrument", action="append", default=None,
                     help="restrict to one instrument (repeatable)")
    sub.add_parser("show", help="print the last written report")
    args = ap.parse_args(argv)

    if args.cmd == "coverage":
        _print_coverage(data.coverage(), data.option_book_coverage())
        return 0
    if args.cmd == "run":
        cov = data.coverage()
        if not cov["usable"]:
            # Refuse rather than write a report full of zeroes that reads like a
            # verdict on the market.
            print("PHASE 24 — CANNOT RUN")
            print("  no instrument has a five-year 1-minute series on disk, so "
                  "there is nothing to search.")
            print("  run `python -m app.research.phase24.cli coverage` for the "
                  "expected file paths.")
            return 2
        instruments = tuple(args.instrument) if args.instrument else None
        out = report.run(instruments=instruments, quick=bool(args.quick))
        _print_report(out)
        print("")
        for name, path in out["artefacts"].items():
            print(f"  wrote {name:<20} {path}")
        return 0
    out = report.latest()
    if not out:
        print("no Phase 24 report on disk yet; run "
              "`python -m app.research.phase24.cli run` first")
        return 1
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
