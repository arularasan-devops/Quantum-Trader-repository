"""Peak memory of the Phase 35 report, old materialising path vs new SQL path.

Builds a synthetic derived store on disk, then runs the report sections in a
child process and reports peak RSS. Usage:

    .venv/bin/python _bench_report.py build   --sessions 8 --per 700
    .venv/bin/python _bench_report.py run     --mode new|old
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _equiv_report as equiv  # noqa: E402
from app.research.phase35 import (  # noqa: E402
    CURRENT_ENGINE_PAPER,
    FULL_MARKET_PAPER,
    paper,
    service,
    store,
    vehicle,
)

DB = "/tmp/p35_bench.db"


def _peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def build(sessions: int, per: int) -> None:
    if os.path.exists(DB):
        os.remove(DB)
    con = store.connect(DB)
    for day in range(sessions):
        for n in range(per):
            equiv._triple(con, day, n,
                          base=[8.0, 40.0, 120.0, 260.0, 700.0, 30.0][n % 6],
                          vol=float(100 * (n % 6 + 1) ** 3))
        con.commit()
        print(f"  seeded session {day + 1}/{sessions}", flush=True)
    paper.build(con, flush_every=2000)
    vehicle.build(con, flush_every=2000)
    con.commit()
    print(json.dumps({k: v for k, v in store.counts(con).items()
                      if not isinstance(v, dict)}))


def run(mode: str) -> None:
    con = store.connect(DB)
    t0 = time.time()
    if mode == "new":
        out = service.state(con)
        legs = out["books"][FULL_MARKET_PAPER]["paper_resolved"]
    else:
        with tempfile.TemporaryDirectory() as tmp:
            old = equiv._load_old(tmp)
            books = {
                b: old["milestones"].aggregate(con, book=b)
                for b in (CURRENT_ENGINE_PAPER, FULL_MARKET_PAPER)
            }
            old["attrib"].histogram(store.attributions(con))
            old["vehicle"].summary(con)
            old["missed"].summary(con)
            old["filters"].study(con, book=FULL_MARKET_PAPER)
            legs = books[FULL_MARKET_PAPER]["paper_resolved"]
    print(json.dumps({
        "mode": mode, "legs_reported": legs,
        "seconds": round(time.time() - t0, 1),
        "peak_rss_mb": round(_peak_mb(), 1),
    }))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["build", "run"])
    ap.add_argument("--sessions", type=int, default=8)
    ap.add_argument("--per", type=int, default=700)
    ap.add_argument("--mode", choices=["new", "old"], default="new")
    a = ap.parse_args()
    if a.action == "build":
        build(a.sessions, a.per)
    else:
        run(a.mode)
