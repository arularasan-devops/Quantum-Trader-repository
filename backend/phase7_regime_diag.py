"""Blocker C diagnosis: why does the live regime engine say EXHAUSTED 85% of the
time and FRESH_MOMENTUM never?

The hypothesis under test is that this is a DATA artefact, not a market fact: the
classifier measures extension as distance from the 30-bar swing in ATR units, so a
window whose bars are missing spans far more real time than 30 minutes, widening
the swing range while the ATR of the surviving bars stays put. Extension inflates,
and every bar lands in the >=2 / >=3 ATR buckets.

Method: classify every bar of the stored history twice — once on the series as
stored (holes included, i.e. what the live engine sees) and once on contiguous
runs only (a window is used only when its 240 bars are consecutive minutes) — and
compare the state distributions and extension quantiles.

Research/diagnostic only: reads the store, imports the same code the live engine
uses, changes nothing.
"""
from __future__ import annotations

import argparse
import json
import statistics as stats

from app import storage
from app.ai import features as F
from app.ai import regime as reg


def _quantiles(xs: list[float]) -> dict:
    if not xs:
        return {}
    xs = sorted(xs)
    return {
        "n": len(xs),
        "p10": round(xs[len(xs) // 10], 2),
        "p50": round(stats.median(xs), 2),
        "p90": round(xs[min(len(xs) - 1, 9 * len(xs) // 10)], 2),
        "max": round(xs[-1], 2),
    }


def _classify_series(candles: list, contiguous_only: bool) -> dict:
    states: dict[str, int] = {}
    exts: list[float] = []
    windows = 0
    skipped = 0
    for i in range(F.WINDOW, len(candles)):
        win = candles[i - F.WINDOW:i]
        if contiguous_only:
            stamps = [int(c.time) for c in win]
            deltas = {b - a for a, b in zip(stamps, stamps[1:])}
            if deltas != {60}:
                skipped += 1
                continue
        feats = F.compute(win)
        if not feats:
            skipped += 1
            continue
        r = reg.classify(feats, None)
        states[r.state] = states.get(r.state, 0) + 1
        exts.append(r.extension_atr)
        windows += 1
    total = max(1, windows)
    return {
        "windows": windows,
        "skipped": skipped,
        "state_pct": {k: round(100.0 * v / total, 1)
                      for k, v in sorted(states.items(), key=lambda kv: -kv[1])},
        "extension_atr": _quantiles(exts),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default="CRUDEOIL,NIFTY")
    ap.add_argument("--limit", type=int, default=1200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    report: dict = {"horizon_bars": F.WINDOW, "instruments": {}}
    for inst in [s.strip().upper() for s in args.instruments.split(",") if s.strip()]:
        candles = storage.store.candles(inst, args.limit)
        gaps = storage.store.candle_gaps(inst, args.limit)
        report["instruments"][inst] = {
            "stored_bars": len(candles),
            "missing_pct": gaps.get("missing_pct"),
            "as_stored": _classify_series(candles, contiguous_only=False),
            "contiguous_only": _classify_series(candles, contiguous_only=True),
        }
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
