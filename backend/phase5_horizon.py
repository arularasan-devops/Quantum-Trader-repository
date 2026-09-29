"""Phase 5 control — is "fading beats following" a real effect or an artefact of
the 30-bar horizon and the ATR levels?

A trend-following engine is supposed to lose a symmetric short-horizon test and
win a longer one, so the follow-vs-fade result from ``phase5_analyze.py`` means
nothing until it is swept across horizons and stop widths. This re-scores the
SAME sampled bars and the SAME detected directions at several horizons and
stop distances; only the outcome window changes.

    python phase5_horizon.py --instrument CRUDEOIL --rows ~/p5_crude.jsonl \
        --out ~/p5_crude_horizon.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time

from app.backtest import angel_history as ah
from app.config import settings
from phase5_features import _atr

_HORIZONS = (15, 30, 60, 120, 240)
_STOPS = (0.5, 0.8, 1.5)
_RR = 1.2


def resolve(fwd, entry: float, stop_dist: float, up: bool, rr: float) -> float:
    """R outcome only (the full MFE/MAE breakdown lives in phase5_features)."""
    target = entry + rr * stop_dist * (1 if up else -1)
    stop = entry - stop_dist * (1 if up else -1)
    mfe = mae = 0.0
    for c in fwd:
        mfe = max(mfe, (c.high - entry) if up else (entry - c.low))
        mae = max(mae, (entry - c.low) if up else (c.high - entry))
        hit_t = (c.high >= target) if up else (c.low <= target)
        hit_s = (c.low <= stop) if up else (c.high >= stop)
        if hit_s or (hit_t and hit_s):
            return -1.0
        if hit_t:
            return rr
    return (mfe - mae) / stop_dist


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--rows", required=True)
    ap.add_argument("--sample", type=int, default=30000,
                    help="evenly spaced subsample of the rows, for runtime")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    candles = ah.load_candles(ah.cache_path(settings.data_dir, args.instrument,
                                            args.interval))
    rows = []
    with open(os.path.expanduser(args.rows)) as fh:
        for line in fh:
            r = json.loads(line)
            d = r["features"].get("scan_dir")
            if d in ("UP", "DOWN"):
                rows.append((r["i"], d == "UP", r["features"]["scan_state"],
                             r["ts"]))
    rows.sort(key=lambda t: t[3])
    if args.sample and len(rows) > args.sample:
        keep = len(rows) // args.sample
        rows = rows[::keep]

    out: dict = {
        "instrument": args.instrument,
        "rows": len(rows),
        "note": "follow = trade WITH the detected direction; fade = against it. "
                "Same bars and same entry price for both, only the horizon and "
                "stop width change.",
        "grid": {},
    }
    for h in _HORIZONS:
        for s in _STOPS:
            fol, fad = [], []
            fresh_fol, fresh_fad = [], []
            for i, up, state, _ in rows:
                a = _atr(candles[max(0, i - 60):i + 1])
                if not a or a <= 0 or i + 1 + h >= len(candles):
                    continue
                fwd = candles[i + 1:i + 1 + h]
                f = resolve(fwd, candles[i].close, s * a, up, _RR)
                g = resolve(fwd, candles[i].close, s * a, not up, _RR)
                fol.append(f)
                fad.append(g)
                if state == "FRESH_MOMENTUM":
                    fresh_fol.append(f)
                    fresh_fad.append(g)
            if not fol:
                continue
            diff = [a - b for a, b in zip(fol, fad)]
            se = statistics.pstdev(diff) / math.sqrt(len(diff)) if len(diff) > 2 else 0.0
            out["grid"][f"h{h}_stop{s}"] = {
                "n": len(fol),
                "follow_expectancy_r": round(statistics.mean(fol), 4),
                "fade_expectancy_r": round(statistics.mean(fad), 4),
                "follow_minus_fade_r": round(statistics.mean(diff), 4),
                "ci95": [round(statistics.mean(diff) - 1.96 * se, 4),
                         round(statistics.mean(diff) + 1.96 * se, 4)],
                "fresh_momentum_only": {
                    "n": len(fresh_fol),
                    "follow_expectancy_r": (round(statistics.mean(fresh_fol), 4)
                                            if fresh_fol else None),
                    "fade_expectancy_r": (round(statistics.mean(fresh_fad), 4)
                                          if fresh_fad else None),
                },
            }
            print(f"h={h:>3} stop={s} follow={out['grid'][f'h{h}_stop{s}']['follow_expectancy_r']:+.4f} "
                  f"fade={out['grid'][f'h{h}_stop{s}']['fade_expectancy_r']:+.4f} "
                  f"diff={out['grid'][f'h{h}_stop{s}']['follow_minus_fade_r']:+.4f}")

    if args.out:
        p = os.path.expanduser(args.out)
        with open(p, "w") as fh:
            json.dump({"as_of": int(time.time()), **out}, fh, indent=2)
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
