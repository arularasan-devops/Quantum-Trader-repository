"""Throughput of the resumable raw ingest on a synthetic capture archive.

Not a test and not a claim about the user's box: it measures rows per second on
generated rows so the wall-clock estimate handed to the operator is measured
rather than guessed, and it checks that a second pass over the same files costs
essentially nothing.

    .venv/bin/python _bench_ingest_stream.py [rows]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

from app.research.phase17 import store as p17store
from app.research.phase35 import capture, store

T0 = 1_788_752_100.0


def row(n: int) -> dict:
    ts = T0 + n * 0.5
    return {
        "observation_id": f"bench-{n}",
        "signal_ts": ts,
        "capture_ts": ts,
        "instrument": "CRUDEOIL" if n % 2 else "NIFTY",
        "family": "MCX" if n % 2 else "INDEX",
        "candidate_class": "BUY" if n % 3 == 0 else "WAIT",
        "direction": "LONG",
        "selected_vehicle": "CE",
        "market_signal_id": f"bench-{n:06d}",
        "plan": {"lot_size": 100},
    }


def main() -> int:
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000
    with tempfile.TemporaryDirectory() as capdir:
        fp = os.path.join(capdir, "phase17_observations.jsonl")
        with open(fp, "w", encoding="utf-8") as fh:
            for n in range(rows):
                fh.write(json.dumps(row(n)) + "\n")
        mb = os.path.getsize(fp) / 1e6
        original = p17store.path
        p17store.path = lambda name: os.path.join(capdir, name)
        try:
            con = store.connect(":memory:")
            t0 = time.time()
            first = capture.ingest_stream(con, now=T0)
            t1 = time.time()
            second = capture.ingest_stream(con, now=T0)
            t2 = time.time()
        finally:
            p17store.path = original
            con.close()
    print(json.dumps({
        "rows": rows,
        "megabytes": round(mb, 1),
        "first_pass_seconds": round(t1 - t0, 2),
        "rows_per_second": round(rows / max(t1 - t0, 1e-9)),
        "megabytes_per_second": round(mb / max(t1 - t0, 1e-9), 1),
        "observations_written": first["observations_written"],
        "second_pass_seconds": round(t2 - t1, 3),
        "second_pass_files_read": second["files_read"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
