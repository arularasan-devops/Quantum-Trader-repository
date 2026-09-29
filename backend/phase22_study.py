#!/usr/bin/env python
"""Phase 22 — core profitable setup validation, run from the command line.

RESEARCH AND PAPER ONLY. Reads a saved candidate pool and the captured vehicle
comparison, grades PULLBACK against NON_PULLBACK over development, validation,
a chronological holdout and walk-forward, prices the surviving candidates on
recorded books, and writes one verdict that is allowed to be negative.

    .venv/bin/python phase22_study.py \
        --pool ~/market_scan_pool.json \
        --capture data/vehicle_comparison.jsonl \
        --outdir ~/phase22

No Angel call, no order, safe to run while the paper app is up.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.phase22 import artefacts, definition as defn, service


def _fmt(value: object, suffix: str = "") -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):+.4f}{suffix}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", required=True,
                    help="candidate pool JSON (all candidates, incl. rejected)")
    ap.add_argument("--capture", default=service.DEFAULT_CAPTURE,
                    help="captured CE/PE/FUTURES comparison JSONL")
    ap.add_argument("--outdir", default="data/phase22")
    args = ap.parse_args()

    capture = args.capture if args.capture and Path(args.capture).exists() else None
    if args.capture and capture is None:
        print(f"capture {args.capture} not found — vehicle leg will be "
              "REQUIRES_MORE_DATA")
    payload = service.run_study(args.pool, capture)
    written = artefacts.write_all(args.outdir, payload)

    study = payload["study"]
    hold = study["periods"]["holdout"][defn.LABEL]
    other = study["periods"]["holdout"][defn.OTHER]
    print(f"\ndefinition   {defn.VERSION} ({defn.fingerprint()})")
    print(f"pool         {study['pool']['candidates']} candidates, "
          f"{study['pool']['sessions']} sessions")
    print(f"holdout      PULLBACK n={hold['trades']} "
          f"expectancy {_fmt(hold.get('expectancy_r'), 'R')} "
          f"PF {hold.get('profit_factor')} "
          f"T1-before-SL {hold.get('t1_before_sl_pct')}%")
    print(f"             NON_PULLBACK n={other['trades']} "
          f"expectancy {_fmt(other.get('expectancy_r'), 'R')} "
          f"PF {other.get('profit_factor')}")
    print(f"walk-forward {study['walk_forward'].get('verdict')}")
    vehicle = payload.get("vehicles")
    if vehicle:
        cov = vehicle["coverage"]
        print(f"vehicles     {cov['with_measured_option_book']} of "
              f"{cov['captured_opportunities']} with a measured book "
              f"({cov['measured_book_pct']}%), "
              f"{cov['no_trade']} NO_TRADE")
    else:
        print("vehicles     no capture supplied")
    print(f"\nVERDICT      {payload['verdict']['verdict']}")
    for check in payload["verdict"]["checks"]:
        print(f"  {check['state']:<7} {check['condition']}: {check['detail']}")
    print("\nartefacts:")
    for name in sorted(written):
        print(f"  {written[name]}")
    print(json.dumps({"verdict": payload["verdict"]["verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
