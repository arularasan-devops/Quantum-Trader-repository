"""Phase 6 control study — does waiting for a pullback actually help?

The entry-quality engine's thresholds have to come from somewhere. Phase 5 found
that entering WITH a move loses because the entry sits at a local extreme where
the stop is nearest (the effect was flat across horizons and shrank ~60% as the
stop widened — stop geometry, not direction). "Wait for a pullback" is the obvious
implication, and obvious implications are exactly what this project has been wrong
about twice, so it is measured before it is implemented.

Design, on the same bars:

* ``IMMEDIATE`` — enter at the close of the signal bar.
* ``PULLBACK(d)`` — wait up to ``--wait`` bars for price to retrace ``d`` ATR from
  the signal bar's close, then enter there. If it never comes, the trade is
  recorded as NOT TAKEN (counted, not silently dropped — a strategy that skips
  the trades that ran away has to be charged for the winners it missed).
* Both use the SAME stop distance in ATR from their own entry and the same RR, and
  both are measured over the same forward horizon from the signal bar, so the
  pullback version does not get extra time.

Reports expectancy, target-before-stop, PF, MFE, MAE and the not-taken rate per
pullback depth, per instrument and per side, split chronologically into halves so
an effect that only exists in one period is visible.

    .venv/bin/python phase6_pullback.py --instrument CRUDEOIL --out ~/p6_pullback_crude.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time

from app.ai import features as F
from app.backtest import angel_history as ah
from app.config import settings

HORIZON = 30
STOP_ATR = 0.8
RR = 1.2
DEPTHS = (0.2, 0.35, 0.5, 0.75, 1.0)


def _resolve(bars: list, entry: float, stop_dist: float, up: bool) -> dict:
    target = entry + RR * stop_dist * (1 if up else -1)
    stop = entry - stop_dist * (1 if up else -1)
    mfe = mae = 0.0
    result = "OPEN"
    for c in bars:
        mfe = max(mfe, (c.high - entry) if up else (entry - c.low))
        mae = max(mae, (entry - c.low) if up else (c.high - entry))
        if (c.low <= stop) if up else (c.high >= stop):
            result = "STOP"
            break
        if (c.high >= target) if up else (c.low <= target):
            result = "TARGET"
            break
    return {
        "result": result,
        "r": RR if result == "TARGET" else -1.0 if result == "STOP"
             else (mfe - mae) / stop_dist,
        "mfe_r": mfe / stop_dist,
        "mae_r": mae / stop_dist,
    }


def _stats(rows: list[dict], not_taken: int) -> dict:
    if not rows:
        return {"trades": 0, "not_taken": not_taken}
    rs = [r["r"] for r in rows]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    n = len(rs)
    sd = statistics.pstdev(rs) if n > 1 else 0.0
    return {
        "trades": n,
        "not_taken": not_taken,
        "not_taken_pct": round(100.0 * not_taken / (n + not_taken), 2) if n + not_taken else None,
        "target_before_stop_pct": round(
            100.0 * sum(1 for r in rows if r["result"] == "TARGET") / n, 2),
        "expectancy_r": round(sum(rs) / n, 4),
        "expectancy_ci95": [round(sum(rs) / n - 1.96 * sd / (n ** 0.5), 4),
                            round(sum(rs) / n + 1.96 * sd / (n ** 0.5), 4)],
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) else None,
        "avg_mfe_r": round(sum(r["mfe_r"] for r in rows) / n, 4),
        "avg_mae_r": round(sum(r["mae_r"] for r in rows) / n, 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--stride", type=int, default=10)
    ap.add_argument("--wait", type=int, default=10, help="bars allowed for the pullback")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if not candles:
        raise SystemExit(f"no cached candles at {path}")

    # "Signal" bars: directional push with the move still fresh — the population
    # the AI engine would consider. Deliberately NOT the production engine's BUYs:
    # this measures the entry rule, and mixing in gate behaviour would confound it.
    buckets: dict[str, dict] = {}
    counts = {"bars": 0, "signals": 0}
    t0 = time.time()
    half_ts = candles[len(candles) // 2].time

    for i in range(F.WINDOW, len(candles) - HORIZON - args.wait - 1, max(1, args.stride)):
        counts["bars"] += 1
        window = candles[i - F.WINDOW:i + 1]
        feats = F.compute(window)
        if feats is None:
            continue
        atr = float(feats["_atr"])
        px = float(feats["_price"])
        persistence = float(feats.get("persistence_10") or 0.0)
        ext = float(feats.get("ext_from_swing_lo_atr") or 0.0)
        up = float(feats.get("ret_5") or 0.0) >= 0
        if persistence < 0.6:
            continue
        counts["signals"] += 1
        side = "CE" if up else "PE"
        period = "FIRST_HALF" if candles[i].time <= half_ts else "SECOND_HALF"
        fwd_all = candles[i + 1:i + 1 + HORIZON + args.wait]

        keys = [f"{side}|ALL", f"{side}|{period}",
                f"{side}|{'FRESH' if ext < 2.0 else 'EXTENDED'}"]

        imm = _resolve(fwd_all[:HORIZON], px, STOP_ATR * atr, up)
        for k in keys:
            b = buckets.setdefault(k, {"IMMEDIATE": {"rows": [], "nt": 0}})
            b["IMMEDIATE"]["rows"].append(imm)

        for d in DEPTHS:
            level = px - (1 if up else -1) * d * atr
            hit_at = None
            for j, c in enumerate(fwd_all[:args.wait]):
                if (c.low <= level) if up else (c.high >= level):
                    hit_at = j
                    break
            name = f"PULLBACK_{d}"
            for k in keys:
                b = buckets.setdefault(k, {})
                slot = b.setdefault(name, {"rows": [], "nt": 0})
                if hit_at is None:
                    slot["nt"] += 1
                else:
                    # Remaining horizon measured from the SIGNAL bar, so waiting
                    # buys no extra time.
                    rest = fwd_all[hit_at + 1:HORIZON]
                    slot["rows"].append(_resolve(rest, level, STOP_ATR * atr, up))

    report = {
        "instrument": args.instrument,
        "bars_scanned": counts["bars"],
        "signal_bars": counts["signals"],
        "stride": args.stride,
        "wait_bars": args.wait,
        "levels": {"stop_atr": STOP_ATR, "reward_risk": RR, "horizon_bars": HORIZON},
        "basis": "UNDERLYING move (no premium, no theta, no spread)",
        "populations": {
            key: {name: _stats(slot["rows"], slot["nt"]) for name, slot in sorted(b.items())}
            for key, b in sorted(buckets.items())
        },
        "seconds": round(time.time() - t0, 1),
    }
    out = os.path.expanduser(args.out)
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps({"out": out, "signal_bars": counts["signals"],
                      "populations": len(buckets),
                      "seconds": report["seconds"]}, indent=2))


if __name__ == "__main__":
    main()
