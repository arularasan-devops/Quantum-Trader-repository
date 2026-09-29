"""Phase 6 pass 1 — training rows for the trade-probability model.

Uses ``app.ai.features.compute`` — the SAME function the live engine calls — so
the training matrix and the serving vector cannot drift apart. Features come from
bars ``<= t``, outcomes from bars ``> t``, and both sides are labelled on
identical levels at the same price so a side preference cannot come from one side
being measured on easier terms.

Outcomes are UNDERLYING moves. There are no real option chains in the archive
(``chain_provenance.py``: 0 REAL_BROKER snapshots), and Phase 5 established that
optimising against modelled premiums produces artefacts — the NIFTY PE "edge" was
one. A real premium's theta and spread can only make these numbers worse.

    .venv/bin/python phase6_dataset.py --instrument CRUDEOIL --out ~/p6_crude.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import time

from app.ai import features as F
from app.backtest import angel_history as ah
from app.config import settings

HORIZON = 30
STOP_ATR = 0.8
RR = 1.2


def outcome(fwd: list, entry: float, stop_dist: float, up: bool) -> dict:
    """Target-before-stop / MFE / MAE in R. A bar touching both counts as STOP."""
    target = entry + RR * stop_dist * (1 if up else -1)
    stop = entry - stop_dist * (1 if up else -1)
    mfe = mae = 0.0
    result = "OPEN"
    bars = 0
    for c in fwd:
        bars += 1
        mfe = max(mfe, (c.high - entry) if up else (entry - c.low))
        mae = max(mae, (entry - c.low) if up else (c.high - entry))
        hit_t = (c.high >= target) if up else (c.low <= target)
        hit_s = (c.low <= stop) if up else (c.high >= stop)
        if hit_s:
            result = "STOP"
            break
        if hit_t:
            result = "TARGET"
            break
    return {
        "result": result,
        "target_before_stop": 1 if result == "TARGET" else 0,
        "r": round(RR if result == "TARGET" else -1.0 if result == "STOP"
                   else (mfe - mae) / stop_dist, 4),
        "mfe_r": round(mfe / stop_dist, 4),
        "mae_r": round(mae / stop_dist, 4),
        "bars_to_resolve": bars,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--bars", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if not candles:
        raise SystemExit(f"no cached candles at {path}")
    if args.bars:
        candles = candles[-args.bars:]

    out_path = os.path.expanduser(args.out)
    t0 = time.time()
    kept = skipped = 0
    with open(out_path, "w") as fh:
        for i in range(F.WINDOW, len(candles) - HORIZON - 1, max(1, args.stride)):
            window = candles[i - F.WINDOW:i + 1]
            feats = F.compute(window)
            if feats is None:
                skipped += 1
                continue
            entry = float(feats["_price"])
            atr = float(feats["_atr"])
            fwd = candles[i + 1:i + 1 + HORIZON]
            if len(fwd) < HORIZON:
                break
            fh.write(json.dumps({
                "instrument": args.instrument,
                "ts": int(feats["_ts"]),
                "price": entry,
                "atr": atr,
                "features": {k: v for k, v in feats.items() if not k.startswith("_")},
                "CE": outcome(fwd, entry, STOP_ATR * atr, True),
                "PE": outcome(fwd, entry, STOP_ATR * atr, False),
            }) + "\n")
            kept += 1

    print(json.dumps({
        "instrument": args.instrument,
        "bars_available": len(candles),
        "rows_written": kept,
        "rows_skipped": skipped,
        "stride": args.stride,
        "horizon_bars": HORIZON,
        "levels": {"stop_atr": STOP_ATR, "reward_risk": RR},
        "feature_source": "app.ai.features.compute (same code the live engine uses)",
        "outcome_basis": "UNDERLYING move (no premium, no theta, no spread)",
        "seconds": round(time.time() - t0, 1),
        "out": out_path,
    }, indent=2))


if __name__ == "__main__":
    main()
