"""Classify stored option-chain snapshots as REAL-broker or SIMULATED — research only.

Phase 3 needs a real option chain to be answerable: strikes differ from each
other through skew, spread and liquidity, and none of those exist in a modelled
ladder. ``history.db`` already holds tens of thousands of ``chain_snapshots``,
but they are only useful if they came from the broker.

The two writers are distinguishable without any provider flag:

* ``market/angelone.py`` builds each quote from a SmartAPI FULL quote and sets
  ``oi_change=0`` for every leg (the endpoint does not expose an OI delta), and
  its ``iv`` is solved from the traded premium, so IV varies strike to strike in
  a skew shape it did not choose.
* ``market/simulated.py`` draws ``oi_change`` and ``volume`` from Gaussians and
  builds ``iv`` from a fixed formula, so ``oi_change`` is almost never exactly
  zero across a whole payload.

So: a snapshot whose every leg has ``oi_change == 0`` is broker data; one with
non-zero OI deltas is simulator data. Reported per instrument, with the session
coverage that a real-premium Phase 3 repeat would have to work from.

    .venv/bin/python chain_provenance.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
from collections import defaultdict

from app.config import settings

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def classify(payload: str) -> str:
    try:
        legs = json.loads(payload)
    except (ValueError, TypeError):
        return "UNREADABLE"
    if not legs:
        return "EMPTY"
    if all((leg.get("oi_change") or 0) == 0 for leg in legs):
        return "REAL"
    return "SIMULATED"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    path = args.db or os.path.join(settings.data_dir, "history.db")
    if not os.path.exists(path):
        raise SystemExit(f"no history db at {path}")

    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sessions: dict[str, set[str]] = defaultdict(set)
    span: dict[str, list[int]] = {}
    for inst, ts, payload in db.execute(
        "SELECT instrument, ts, payload FROM chain_snapshots ORDER BY ts"
    ):
        kind = classify(payload)
        counts[inst][kind] += 1
        if kind == "REAL":
            day = dt.datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d")
            sessions[inst].add(day)
            lo, hi = span.get(inst, [ts, ts])
            span[inst] = [min(lo, ts), max(hi, ts)]
    db.close()

    per_instrument = {}
    for inst, kinds in sorted(counts.items(), key=lambda kv: -sum(kv[1].values())):
        real = kinds.get("REAL", 0)
        per_instrument[inst] = {
            "real_broker_snapshots": real,
            "simulated_snapshots": kinds.get("SIMULATED", 0),
            "other": sum(v for k, v in kinds.items() if k not in ("REAL", "SIMULATED")),
            "real_sessions": len(sessions.get(inst, ())),
            "real_first_ist": (
                dt.datetime.fromtimestamp(span[inst][0], _IST).isoformat()
                if inst in span else None
            ),
            "real_last_ist": (
                dt.datetime.fromtimestamp(span[inst][1], _IST).isoformat()
                if inst in span else None
            ),
        }

    total_real = sum(v["real_broker_snapshots"] for v in per_instrument.values())
    report = {
        "db": path,
        "rule": "a snapshot whose every leg has oi_change == 0 was written by the "
                "Angel provider; a snapshot with non-zero OI deltas was written by "
                "the simulator",
        "total_real_broker_snapshots": total_real,
        "phase3_real_chain_ready": total_real > 0,
        "per_instrument": per_instrument,
    }
    out = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(out)


if __name__ == "__main__":
    main()
