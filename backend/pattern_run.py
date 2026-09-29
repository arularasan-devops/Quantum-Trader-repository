"""Runner for the pattern → next-move hit-rate study.

Usage:
    .venv/bin/python pattern_run.py --instrument NIFTY --horizon 10 --target 5
"""
from __future__ import annotations

import argparse
import json

from app.backtest import pattern_study

_DATA = {
    "CRUDEOIL": "data/backtest/CRUDEOIL_ONE_MINUTE.jsonl",
    "NIFTY": "data/backtest/NIFTY_ONE_MINUTE.jsonl",
}


def _fmt_table(title: str, patterns: dict, baseline: dict) -> str:
    lines = [f"\n{title}", "-" * 88]
    lines.append(
        f"{'pattern':<22}{'n':>7}{'next→':>8}{'hit≥T':>8}{'avgFwd':>9}{'medFwd':>9}"
    )
    lines.append(
        f"{'BASELINE (all bars)':<22}{baseline['n']:>7}"
        f"{baseline['next_follow_pct']:>7}%{baseline['hit_target_pct']:>7}%"
        f"{baseline['avg_fwd_pts']:>9}{baseline['median_fwd_pts']:>9}"
    )
    for name, s in sorted(patterns.items(), key=lambda kv: -kv[1].get("n", 0)):
        if s.get("n", 0) < 30:
            continue
        lines.append(
            f"{name:<22}{s['n']:>7}{s['next_follow_pct']:>7}%{s['hit_target_pct']:>7}%"
            f"{s['avg_fwd_pts']:>9}{s['median_fwd_pts']:>9}"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NIFTY", choices=list(_DATA))
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--target", type=float, default=5.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    res = pattern_study.run(_DATA[args.instrument], horizon=args.horizon, target=args.target)

    if args.json:
        print(json.dumps(res, indent=2))
        return

    print(f"\n=== {args.instrument} pattern → next-move study ===")
    print(f"bars: {res['bars']:,}  horizon: {args.horizon} candles  target: {args.target} pts")
    print(
        "\nColumns: next→ = next candle continued in the pattern's direction; "
        "hit≥T = reached target pts (MFE) within horizon; avgFwd/medFwd = signed "
        "move at horizon (pts, + = pattern's way)."
    )
    b = res["baseline"]
    print(_fmt_table("ALL occurrences", res["patterns"], b))
    print(_fmt_table("ONLY when pattern AGREES with EMA20/60 trend", res["patterns_with_trend"], b))
    print(_fmt_table("ONLY when pattern is AGAINST EMA20/60 trend", res["patterns_against_trend"], b))


if __name__ == "__main__":
    main()
