"""Phase 26 CLI — compare the exits, test the events, print the advisory.

    python -m app.research.phase26.cli coverage
    python -m app.research.phase26.cli run [--quick] [--instrument NIFTY ...]
    python -m app.research.phase26.cli advisory
    python -m app.research.phase26.cli show

``--db-path`` points the study at another captured store and ``--out-dir`` keeps
its artefacts out of the live report; both default to the live paths.

Read-only with respect to trading: this reads the captured store, writes
artefacts under ``data/phase26`` and touches nothing in the signal, paper or
order path. It does not write anything belonging to Phase 23, 24 or 25.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase26 import (
    AVOID,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    coverage as coverage_mod,
    exits,
    report,
)


def _print_coverage(cov: dict) -> None:
    print("PHASE 26 — CAPTURED OPTION-BOOK COVERAGE")
    if cov.get("unmeasured"):
        print(f"  unmeasured: {cov['cause']}")
        print(f"  {cov['note']}")
        return
    for row in cov["eligible"]:
        print(f"  ELIGIBLE     {row['instrument']:<12} snapshots "
              f"{row['snapshots']:>7,} · sessions {row['sessions']:>3} · "
              f"resolvable quotes {row['resolvable_quotes']:>8,}")
    for row in cov["reported_only"]:
        n = row.get("snapshots", row.get("real_broker_snapshots", 0))
        print(f"  REPORTED     {row['instrument']:<12} snapshots {n:>7,} · "
              f"{'; '.join(row.get('reasons') or [])}")
    print("")
    print(f"  {cov['note']}")
    print(f"  {cov['window_claim']}")


def _print_variants(out: dict) -> None:
    print("EXIT VARIANTS — pooled across every studied instrument")
    base = exits.BASELINE_KEY
    for t in out.get("variant_totals") or []:
        mark = " (baseline)" if t["variant"] == base else ""
        print(f"  {t['variant']:<30}{mark}")
        print(f"      trades {t['trades']:>8,} · net {t['avg_net_r']:>8}R · "
              f"total ₹{t['total_net_rupees']:,.0f}")
        print(f"      profit exit first {t['profit_exit_before_sl_pct']:>6}% · "
              f"stopped {t['stop_out_pct']:>6}% · flat timeout "
              f"{t['flat_timeout_pct']:>6}% · positive "
              f"{t['positive_net_pct']:>6}%")
        print(f"      horizon exit {t['horizon_exit_pct']:>6}% of trades, "
              f"{t['horizon_exit_in_profit_pct']:>6}% of those in profit "
              f"(a variant with no fixed target ends there by construction)")
        print(f"      avg hold {t['avg_hold_sec']:,.0f}s · instruments positive "
              f"{len(t['instruments_positive'])} · better than baseline "
              f"{len(t['instruments_better_than_baseline'])}")


def _print_advisory(adv: dict) -> None:
    print("TRADABILITY ADVISORY — measured economics, advisory only")
    counts = adv.get("counts") or {}
    print("  " + " · ".join(f"{k} {v}" for k, v in counts.items()))
    for r in adv.get("rows") or []:
        if not r.get("trades"):
            continue
        print(f"  {r['verdict']:<20} {r['instrument']:<12} spread "
              f"{r['median_measured_spread_pct']:>6}% "
              f"({r['spread_as_fraction_of_risk']}x risk) · hurdle "
              f"{r['median_break_even_hurdle_pct']:>6}% · premium ₹"
              f"{r['median_premium_ask']:>8} · measured net "
              f"{r['measured_avg_net_r']}R")
    cf = adv.get("refusing_hurdle_gt_5pct_on_this_window") or {}
    if cf:
        print("")
        print("  refusing the >5% hurdle band on this window would have "
              f"avoided ₹{cf.get('losses_avoided_rupees', 0):,.0f} of losses and "
              f"given up ₹{cf.get('winners_given_up_rupees', 0):,.0f} of winners "
              f"(net ₹{cf.get('net_rupees_not_taken', 0):,.0f})")
    print("  nothing here is wired to a live refusal")


def _print_report(out: dict) -> None:
    print(f"PHASE 26 — {out['version']} · {out.get('runtime_seconds')}s"
          f"{' · QUICK' if out.get('quick_mode') else ''}")
    e = out.get("execution") or {}
    if e:
        print("")
        print("EXECUTION MODEL")
        print(f"  entry {e['entry']}")
        print(f"  exit  {e['exit']}")
        print(f"  costs {e['costs_charged']}")
        print(f"  split {e['split']}")
    print("")
    _print_variants(out)
    print("")
    for row in out.get("instruments") or []:
        if row.get("status") != "STUDIED":
            reasons = "; ".join((row.get("coverage") or {}).get("reasons") or [])
            print(f"  {REQUIRES_MORE_DATA}  {row['instrument']:<12} {reasons}")
            continue
        up = row.get("variant_uplift_vs_baseline_r") or {}
        print(f"  STUDIED    {row['instrument']:<12} candidates "
              f"{row.get('candidates', 0):,} · sessions {row.get('sessions', 0)} · "
              f"best on dev {row.get('best_variant_on_development')} · "
              f"holdout uplift {up.get('holdout')}R")
    print("")
    print(f"EVENT HYPOTHESES EVALUATED "
          f"{out.get('event_hypotheses_evaluated', 0):,}")
    ranked = out.get("ranked") or []
    leads = [r for r in ranked if r["status"] == RESEARCH_LEAD]
    print(f"  cohorts carried {len(ranked):,} · research leads {len(leads)}")
    for r in ranked[:3]:
        print(f"    {r['strategy_id']} {r['instrument']} {r['option_type']} "
              f"{r['variant']} [{' AND '.join(r['conditions'])}] → {r['status']}")
        for clause in r.get("failed_clauses") or []:
            print(f"        {clause}")
    print("")
    _print_advisory(out.get("advisory") or {})
    print("")
    c = out.get("conclusion") or {}
    print(f"VERDICT {c.get('verdict')}")
    print(f"  {c.get('headline')}")
    print(f"  {c.get('window')}")
    for a in out.get("answers") or []:
        print(f"  Q: {a['question']}")
        print(f"     {a['answer']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="phase26")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cov = sub.add_parser("coverage")
    cov.add_argument("--db-path", default=None)
    r = sub.add_parser("run")
    r.add_argument("--quick", action="store_true")
    r.add_argument("--instrument", action="append", default=None)
    r.add_argument("--json", action="store_true")
    r.add_argument("--db-path", default=None)
    r.add_argument("--out-dir", default=None)
    a = sub.add_parser("advisory")
    a.add_argument("--json", action="store_true")
    s = sub.add_parser("show")
    s.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "coverage":
        _print_coverage(coverage_mod.coverage(db_path=args.db_path))
        return 0
    if args.cmd == "run":
        out = report.run(quick=args.quick, instruments=args.instrument,
                         db_path=args.db_path, out_dir=args.out_dir)
        if args.json:
            print(json.dumps(out, indent=1, default=str))
        else:
            _print_report(out)
        return 0
    if args.cmd == "advisory":
        adv = report.latest_advisory()
        if not adv:
            print("no Phase 26 study has been run on this machine yet; run "
                  "`python -m app.research.phase26.cli run`")
            return 1
        if args.json:
            print(json.dumps(adv, indent=1, default=str))
        else:
            _print_advisory(adv)
            avoid = (adv.get("instruments_by_verdict") or {}).get(AVOID) or []
            if avoid:
                print(f"  avoid on economics alone: {', '.join(avoid)}")
        return 0
    out = report.latest()
    if not out:
        print("no Phase 26 study has been run on this machine yet; run "
              "`python -m app.research.phase26.cli run`")
        return 1
    if args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        _print_report(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
