"""Phase 25 CLI — run the captured-window CE/PE study, or print the last one.

    python -m app.research.phase25.cli coverage
    python -m app.research.phase25.cli run [--quick] [--instrument NIFTY ...]
    python -m app.research.phase25.cli show

``--db-path`` points the study at another captured store (an archived session
store, or a merge of several) instead of the live one, and ``--out-dir`` keeps
its artefacts out of the live report. Both default to the live paths.

Read-only with respect to trading: this command reads the captured store, writes
report artefacts under ``data/phase25`` and touches nothing in the signal, paper
or order path. It does not read or write anything belonging to Phase 23 or
Phase 24.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase25 import REQUIRES_MORE_DATA, RESEARCH_LEAD, books, report


def _print_coverage(cov: dict) -> None:
    print("PHASE 25 — CAPTURED OPTION-BOOK COVERAGE")
    if cov.get("unmeasured"):
        print(f"  unmeasured: {cov['cause']}")
        print(f"  {cov['note']}")
        return
    for row in cov["eligible"]:
        print(f"  ELIGIBLE     {row['instrument']:<12} snapshots "
              f"{row['snapshots']:>7,} · sessions {row['sessions']:>3} · "
              f"resolvable quotes {row['resolvable_quotes']:>8,} · "
              f"distinct contracts {row['contract_paths']:>5,}")
    for row in cov["reported_only"]:
        n = row.get("snapshots", row.get("real_broker_snapshots", 0))
        print(f"  REPORTED     {row['instrument']:<12} snapshots {n:>7,} · "
              f"{'; '.join(row.get('reasons') or [])}")
    print("")
    print(f"  {cov['note']}")
    print(f"  minimums: {books.MIN_SNAPSHOTS:,} snapshots · "
          f"{books.MIN_SESSIONS} sessions · "
          f"{books.MIN_RESOLVABLE_QUOTES:,} quotes with a later quote of the "
          f"same contract · {books.MIN_CONTRACT_PATHS} distinct contracts")
    print(f"  {cov['window_claim']}")


def _print_report(out: dict) -> None:
    print(f"PHASE 25 — {out['version']} · {out.get('runtime_seconds')}s"
          f"{' · QUICK' if out.get('quick_mode') else ''}")
    print("")
    g = out.get("geometry") or {}
    if g:
        print("EXECUTION MODEL")
        print(f"  entry {g['entry']}")
        print(f"  exit  {g['exit']}")
        print(f"  costs {g['costs_charged']}")
        print(f"  slippage {g['slippage_pct_per_side']}% per side · "
              f"stop bands {g['stop_pct_of_premium_bands']}% of premium · "
              f"T1 {g['t1_r']}R")
        print(f"  split {g['split']}")
        print("")

    for row in out.get("instruments") or []:
        if row.get("status") != "STUDIED":
            reasons = "; ".join((row.get("coverage") or {}).get("reasons") or [])
            print(f"  {REQUIRES_MORE_DATA}  {row['instrument']:<12} {reasons}")
            continue
        p = row.get("pool") or {}
        print(f"  STUDIED    {row['instrument']:<12} candidates "
              f"{p.get('candidates', 0):,} · resolved {p.get('resolved', 0):,} · "
              f"sessions {p.get('sessions', 0)} · base T1 "
              f"{p.get('base_t1_before_sl_pct')}% · base net "
              f"{p.get('base_avg_net_r')}R")
        for s in row.get("geometry_sweep") or []:
            print(f"      stop {s['stop_band_pct_of_premium']:>4}% of premium: "
                  f"T1-first {s['t1_before_sl_pct']}% vs breakeven "
                  f"{s['breakeven_t1_pct_before_costs']}% · net "
                  f"{s['avg_net_r']}R · costs {s['cost_as_fraction_of_risk']}x risk "
                  f"(spread alone {s['spread_as_fraction_of_risk']}x)")
        for side, stats in (row.get("sides") or {}).items():
            print(f"      {side}: {stats.get('trades', 0):,} trades · T1 "
                  f"{stats.get('t1_before_sl_pct')}% · net "
                  f"{stats.get('avg_net_r')}R · PF {stats.get('profit_factor')} · "
                  f"avg ₹{stats.get('avg_net_rupees')}")
        for b in row.get("hurdle_bands") or []:
            print(f"      {b['band']:<24} trades {b.get('trades', 0):>6,} · net "
                  f"{b.get('avg_net_r')}R · total ₹{b.get('total_net_rupees')}")
    print("")
    print(f"HYPOTHESES EVALUATED {out.get('hypotheses_evaluated', 0):,}")
    leads = [r for r in out.get("ranked") or [] if r["status"] == RESEARCH_LEAD]
    print(f"  cohorts carried {len(out.get('ranked') or []):,} · research leads "
          f"{len(leads)}")
    for r in (out.get("ranked") or [])[:3]:
        print(f"    {r['strategy_id']} {r['instrument']} {r['option_type']} "
              f"stop {r['stop_band_pct_of_premium']}% "
              f"[{' AND '.join(r['conditions'])}] → {r['status']}")
        for clause in r.get("failed_clauses") or []:
            print(f"        {clause}")
    print("")
    c = out.get("conclusion") or {}
    print(f"VERDICT {c.get('verdict')}")
    print(f"  {c.get('headline')}")
    print(f"  {c.get('window')}")
    for a in out.get("answers") or []:
        print(f"  Q: {a['question']}")
        print(f"     {a['answer']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="phase25")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cov = sub.add_parser("coverage")
    cov.add_argument("--db-path", default=None)
    r = sub.add_parser("run")
    r.add_argument("--quick", action="store_true")
    r.add_argument("--instrument", action="append", default=None)
    r.add_argument("--json", action="store_true")
    r.add_argument("--db-path", default=None)
    r.add_argument("--out-dir", default=None)
    s = sub.add_parser("show")
    s.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "coverage":
        _print_coverage(report.coverage(db_path=args.db_path))
        return 0
    if args.cmd == "run":
        out = report.run(quick=args.quick, instruments=args.instrument,
                         db_path=args.db_path, out_dir=args.out_dir)
        if args.json:
            print(json.dumps(out, indent=1, default=str))
        else:
            _print_report(out)
        return 0
    out = report.latest()
    if not out:
        print("no Phase 25 study has been run on this machine yet; run "
              "`python -m app.research.phase25.cli run`")
        return 1
    if args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        _print_report(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
