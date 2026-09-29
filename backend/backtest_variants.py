"""Compare Candle-Flow configurations over the SAME cached history (READ-ONLY).

Answers "what settings are actually best?" with measured numbers instead of
opinion. Re-runs the real Flow backtest for several (min_body_frac,
confirm_candles, giveback) combinations and prints a comparison table on the
underlying-move outcome. Uses cached candles only — no API calls.
"""
from __future__ import annotations

import argparse

from app.backtest import angel_history as ah
from app.backtest import flow_backtest as fb
from app.config import settings

VARIANTS = [
    # label, min_body_frac, confirm_candles, giveback_points
    ("loose (0.10/1)", 0.10, 1, 8.0),
    ("default (0.30/2)", 0.30, 2, 8.0),
    ("steady (0.35/3)", 0.35, 3, 8.0),
    ("strict (0.45/3)", 0.45, 3, 10.0),
    ("very strict (0.50/4)", 0.50, 4, 12.0),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    print(f"Loaded {len(candles)} cached candles from {path}\n")
    if len(candles) < 50:
        raise SystemExit("Not enough candles.")

    saved = (
        settings.flow_min_body_frac,
        settings.flow_confirm_candles,
        settings.flow_giveback_points,
    )
    header = f"{'config':22} {'trades':>7} {'win%':>6} {'avg':>7} {'total':>9} {'PF':>5}"
    print(header)
    print("-" * len(header))
    try:
        for label, body, confirm, give in VARIANTS:
            settings.flow_min_body_frac = body
            settings.flow_confirm_candles = confirm
            settings.flow_giveback_points = give
            res = fb.run(candles, args.instrument, args.interval)
            u = res.stats()["underlying_points"]
            print(
                f"{label:22} {u.get('n', 0):>7} {u.get('win_rate_pct'):>6} "
                f"{u.get('avg'):>7} {u.get('sum'):>9} {str(u.get('profit_factor')):>5}"
            )
    finally:
        (
            settings.flow_min_body_frac,
            settings.flow_confirm_candles,
            settings.flow_giveback_points,
        ) = saved
    print("\nAll numbers are on the REAL underlying move (measured). Higher PF / "
          "less-negative total = better. If every config is <1.0 PF, the tape was "
          "choppy and Flow correctly makes little/no edge there.")


if __name__ == "__main__":
    main()
