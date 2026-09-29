"""Phase 23 CLI — print the shadow report, or write it as artefacts.

    python -m app.research.phase23.cli report
    python -m app.research.phase23.cli artefacts --outdir data/phase23

Read-only. Nothing here promotes a threshold or writes into the trading path.
"""
from __future__ import annotations

import argparse
import json
import os

from app.research.phase23 import report, service, verdict


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _print_report(built: dict, ver: dict) -> None:
    cov = built["coverage"]
    print("PHASE 23 — BREAK-EVEN HURDLE PAPER SHADOW (options only)")
    print(f"  opportunities {cov['opportunities']} · measured book "
          f"{cov['measured_book']} ({_fmt(cov['measured_pct'])}%) · "
          f"engine bought {cov['engine_would_buy']} · resolved "
          f"{cov['resolved_trades']}")
    print("")
    cols = ("arm", "accepted", "rejected", "t1_before_sl_pct", "win_pct",
            "profit_factor", "net_pnl", "avg_net_r", "median_net_r",
            "max_drawdown", "max_losing_streak", "avg_hurdle_pct",
            "median_measured_spread_pct")
    print(" | ".join(c.upper() for c in cols))
    for arm in (report.EXISTING, report.SHADOW_5, report.SHADOW_3):
        block = built["cumulative"][arm]
        print(" | ".join(_fmt(block.get(c)) for c in cols))
    print("")
    for arm in (report.SHADOW_5, report.SHADOW_3):
        att = built["cumulative"][arm].get("attribution") or {}
        print(f"{arm}: MONEY SAVED BY REFUSING HIGH-HURDLE TRADES "
              f"{_fmt(att.get('money_saved_by_refusing_high_hurdle_trades'))} · "
              f"MONEY MISSED BY REFUSING THEM "
              f"{_fmt(att.get('money_missed_by_refusing_them'))} · net "
              f"{_fmt(att.get('net_effect'))} · net excl. largest "
              f"{_fmt(att.get('net_effect_excluding_largest'))}")
    print("")
    print("THRESHOLD SWEEP 3%-5%")
    for row in built["sweep"]:
        print(f"  {row['threshold_pct']:>5g}% accepted {row['accepted']:>4} "
              f"rejected {row['rejected']:>4} net {_fmt(row['net_pnl']):>12} "
              f"vs existing {_fmt(row['vs_existing_net_pnl']):>12} "
              f"PF {_fmt(row['profit_factor']):>6} "
              f"net effect {_fmt(row['net_effect']):>12} "
              f"(excl. largest {_fmt(row['net_effect_excluding_largest'])})")
    print("")
    print(f"VERDICT: {ver['status']} · discovered threshold "
          f"{_fmt(ver['discovered_threshold_pct'])} · measured resolved trades "
          f"{ver['measured_resolved_trades']} over {ver['sessions']} session(s)")
    if ver["status"] != verdict.PROMOTE:
        reasons = sorted({r for c in ver["candidates"]
                          for r in c["blocking_reasons"]})
        for reason in reasons:
            print(f"  blocked: {reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    art = sub.add_parser("artefacts")
    art.add_argument("--outdir", default="data/phase23")
    args = parser.parse_args(argv)

    payload = service.summary()
    if args.cmd == "report":
        _print_report(payload["report"], payload["verdict"])
        return 0

    os.makedirs(args.outdir, exist_ok=True)
    for name, blob in (("report", payload["report"]),
                       ("verdict", payload["verdict"]),
                       ("health", payload["health"])):
        path = os.path.join(args.outdir, f"phase23_{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, indent=2, default=str)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
