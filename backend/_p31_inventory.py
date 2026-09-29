"""Phase 31 §1 — inventory: what premium-path evidence actually exists.

Read-only. Answers one question before any statistic is computed: how many
recorded calls have a real two-sided premium at the decision instant AND a
later premium to measure the move against.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter

from app.research.phase17 import store as p17store

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def scan_observations() -> None:
    # The series helper, not a glob of ``*.jsonl``: compaction gzips a rolled
    # file and removes the original, so a glob stops matching evidence that is
    # still there, and an inventory that undercounts is worse than none.
    files = [p for p in p17store.series_paths(p17store.OBSERVATIONS)
             if os.path.exists(p)]
    tot = Counter()
    by_src = Counter()
    by_q = Counter()
    tracking_kinds = Counter()
    path_lens = []
    book_rows = 0
    for fp in files:
        with p17store.open_series(fp) as fh:
            for raw in fh:
                line = raw.decode("utf-8", "ignore").strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    tot["unparsable"] += 1
                    continue
                tot["rows"] += 1
                sel = r.get("selected") or {}
                by_src[sel.get("source")] += 1
                by_q[sel.get("data_quality")] += 1
                if sel.get("bid") is not None and sel.get("ask") is not None:
                    book_rows += 1
                tr = r.get("tracking")
                if isinstance(tr, str):
                    tracking_kinds[tr] += 1
                elif isinstance(tr, dict):
                    tracking_kinds["DICT"] += 1
                    for key in ("path", "samples", "points", "observations"):
                        v = tr.get(key)
                        if isinstance(v, list):
                            path_lens.append(len(v))
                            break
                else:
                    tracking_kinds[str(type(tr).__name__)] += 1
    print("== phase17 observation files:", len(files))
    print("rows                :", tot["rows"], " unparsable:", tot["unparsable"])
    print("two-sided at entry  :", book_rows)
    print("source              :", dict(by_src))
    print("data_quality        :", dict(by_q))
    print("tracking            :", dict(tracking_kinds))
    if path_lens:
        path_lens.sort()
        print("path samples/row    : n=%d min=%d median=%d max=%d"
              % (len(path_lens), path_lens[0],
                 path_lens[len(path_lens) // 2], path_lens[-1]))


def scan_chain_snapshots() -> None:
    db = os.path.join(DATA, "history.db")
    if not os.path.exists(db):
        print("== chain_snapshots: history.db missing")
        return
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n, lo, hi = con.execute(
        "select count(*), min(ts), max(ts) from chain_snapshots"
    ).fetchone()
    print("\n== chain_snapshots:", n, "snapshots")
    days = con.execute(
        "select count(distinct date(ts,'unixepoch','+5 hours','+30 minutes'))"
        " from chain_snapshots"
    ).fetchone()[0]
    print("sessions covered    :", days)
    per_inst = con.execute(
        "select instrument, count(*) from chain_snapshots group by 1 order by 2 desc"
    ).fetchall()
    print("per instrument      :", per_inst[:10])
    with_book = 0
    checked = 0
    legs = 0
    for (payload,) in con.execute(
        "select payload from chain_snapshots order by ts limit 4000"
    ):
        checked += 1
        try:
            rows = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for leg in rows if isinstance(rows, list) else []:
            legs += 1
            if leg.get("bid") is not None and leg.get("ask") is not None:
                with_book += 1
    print(f"sampled {checked} snapshots / {legs} legs: "
          f"{with_book} legs carry bid+ask")
    print("span                :", lo, "->", hi)
    con.close()


if __name__ == "__main__":
    scan_observations()
    scan_chain_snapshots()
