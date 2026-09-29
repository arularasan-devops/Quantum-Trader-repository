"""READ-ONLY audit of every input the engine feeds into its confidence score.

Motivation: the SuperTrend input was found reading "UP" on 100% of bars — a
constant vote silently biasing the score. This replays real cached candles and
measures, per scored input:

  * ``fires%``   — how often it produces a vote at all (0% = dead weight),
  * ``bull/bear``— the split of its direction (100/0 = a constant bias, not evidence),
  * ``weight``   — its configured weight, so a broken input's impact is visible.

Anything that never fires, or always votes the same way, is not evidence and
should be fixed or removed rather than left scoring trades.

    .venv/bin/python engine_audit.py --instrument NIFTY --samples 800
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from app.backtest import angel_history as ah
from app.backtest.flow_backtest import _build_chain
from app.config import settings
from app.engine.decision import _gather_votes, compute_indicators
from app.market.instruments import REGISTRY


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NIFTY")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--samples", type=int, default=800)
    ap.add_argument("--tail", type=int, default=180)
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    spec = REGISTRY.get(args.instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else args.instrument

    n = len(candles)
    stride = max(1, (n - args.tail) // args.samples)
    fires: dict[str, int] = defaultdict(int)
    bull: dict[str, int] = defaultdict(int)
    weight: dict[str, float] = {}
    confirm_only: dict[str, bool] = {}
    total = 0

    for i in range(args.tail, n, stride):
        window = candles[i + 1 - args.tail : i + 1]
        spot = window[-1].close
        chain = _build_chain(root, spot, step, None)
        snap = compute_indicators(window, chain, 0.0)
        votes = _gather_votes(snap, float(spot), 0.0, chain)
        total += 1
        for name, direction, w, _detail, _level, conf_only in votes:
            fires[name] += 1
            if direction > 0:
                bull[name] += 1
            weight[name] = w
            confirm_only[name] = conf_only

    print(f"{args.instrument}: audited {total} sampled bars out of {n:,} candles\n")
    print(
        f"{'input':<26} {'fires%':>7} {'bull%':>7} {'bear%':>7} {'weight':>7} "
        f"{'kind':>9}  verdict"
    )
    rows = sorted(weight, key=lambda k: -fires[k])
    dead, constant = [], []
    for name in rows:
        f = fires[name]
        fp = f / total * 100
        bp = bull[name] / f * 100 if f else 0.0
        verdict = "ok"
        if fp < 1.0:
            verdict = "DEAD — never fires"
            dead.append(name)
        elif fp > 95 and (bp > 97 or bp < 3):
            verdict = "BROKEN — constant, always the same side"
            constant.append(name)
        elif bp > 97 or bp < 3:
            verdict = "one-sided (check)"
        kind = "confirm" if confirm_only[name] else "PRIMARY"
        print(
            f"{name:<26} {fp:>6.1f}% {bp:>6.1f}% {100 - bp:>6.1f}% "
            f"{weight[name]:>7.1f} {kind:>9}  {verdict}"
        )

    # Inputs the code can emit but which never appeared at all.
    print()
    if dead:
        print("DEAD inputs (never fire on this data):", ", ".join(dead))
    if constant:
        print("BROKEN inputs (constant vote):", ", ".join(constant))
    if not dead and not constant:
        print("No dead or constant inputs found.")


if __name__ == "__main__":
    main()
