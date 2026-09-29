"""Smoke test: the AI outcome resolver labels decisions, and never head-blocks.

This exists because of a real defect the Phase 6 export exposed: 13,276 journaled
decisions produced 0 outcomes. The resolver read forward bars only from the live
provider's in-memory window, which is empty for any instrument that is no longer
warm and after every restart, and it then skipped silently. Because the pending
query is ``ORDER BY ts ASC LIMIT n``, a single unlabellable row at the head of the
queue starved every later row forever — so waiting months would still have
produced no verdict.

What is proved:
 * a decision with persisted forward bars is labelled on BOTH sides from the
   persisted history alone (no live provider involved);
 * a decision that can never be labelled is marked UNRESOLVABLE past the give-up
   age, so it stops blocking the queue;
 * an UNRESOLVABLE row carries no labels and is excluded from the training dataset;
 * a stale-but-recent row is left pending rather than given up on prematurely.
"""
from __future__ import annotations

import os
import tempfile
import time

from app.ai import journal as aij
from app.ai import service
from app.models import Candle
from app.research.db import Database

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


def _bars(start_ts: int, n: int, price: float, step: float) -> list[Candle]:
    """A steadily rising series, so CE reaches target and PE reaches stop."""
    out = []
    for i in range(n):
        base = price + step * i
        out.append(Candle(time=start_ts + i * 60, open=base, high=base + step,
                          low=base - step * 0.2, close=base + step * 0.5,
                          volume=1000.0))
    return out


def main() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    previous = aij.use_journal(aij.AIJournal(Database(f"sqlite:///{db_path}")))
    try:
        j = aij.journal()
        now = int(time.time())
        old = now - service._GIVE_UP_SEC - 3600      # past the give-up age
        recent = now - 40 * 60                       # past the horizon, still young

        labelled_id = j.insert_decision({
            "ts": old, "instrument": "RESOLVABLE", "price": 6000.0, "atr": 10.0,
            "ai_decision": "WATCH", "ai_side": "CE",
        })
        blocked_id = j.insert_decision({
            "ts": old + 1, "instrument": "NO_DATA_INST", "price": 6000.0,
            "atr": 10.0, "ai_decision": "WATCH",
        })
        young_id = j.insert_decision({
            "ts": recent, "instrument": "NO_DATA_INST", "price": 6000.0,
            "atr": 10.0, "ai_decision": "WATCH",
        })

        # Forward bars exist for one instrument only, and only in "persisted"
        # storage — the live provider is never consulted here.
        bars = {"RESOLVABLE": _bars(old + 60, service._HORIZON_BARS + 5, 6000.0, 2.0)}
        saved_forward = service._forward_candles
        service._forward_candles = lambda inst: bars.get(inst, [])
        try:
            resolved = service._resolve_outcomes()
        finally:
            service._forward_candles = saved_forward

        ok(resolved == 1, f"exactly one row is labellable, got {resolved}")

        counts = j.outcome_counts()
        ok(counts["labelled"] == 1, f"one real label expected: {counts}")
        ok(counts["total"] == 2,
           f"the unlabellable OLD row must be marked, not skipped: {counts}")
        ok(any(b.startswith("UNRESOLVABLE") for b in counts["by_basis"]),
           f"give-up basis missing: {counts}")

        rows = {r["id"]: r for r in j.dataset()}
        ok(labelled_id in rows, "the labelled decision must enter the dataset")
        ok(blocked_id not in rows,
           "an UNRESOLVABLE row carries no labels and must stay out of the dataset")
        ok(rows[labelled_id]["ce_result"] == "TARGET",
           f"a rising series must hit the CE target: {rows[labelled_id]['ce_result']}")
        ok(rows[labelled_id]["pe_result"] == "STOP",
           f"the same series must stop the PE side: {rows[labelled_id]['pe_result']}")
        ok(rows[labelled_id]["taken_side"] == "CE"
           and rows[labelled_id]["taken_result"] == "TARGET",
           "the taken side must be labelled from the same geometry")

        # The young row is still pending: no data yet is not the same as never.
        pending = {r["id"] for r in j.unresolved_decisions(now, limit=50)}
        ok(young_id in pending, "a recent row must not be given up on early")

        # And the queue is no longer head-blocked: a second pass now reaches
        # anything newer instead of returning the same starved head.
        stats = service._state.get("resolve") or {}
        ok(stats.get("gave_up") == 1 and stats.get("labelled") == 1,
           f"resolver telemetry must explain the pass: {stats}")
        ok("SHORT_FORWARD_WINDOW" in (stats.get("skipped") or {})
           or "NO_CANDLES" in (stats.get("skipped") or {}),
           f"skip reasons must be reported, not silent: {stats}")

        print(f"AI RESOLVER SMOKE PASSED ({checks} checks)")
    finally:
        aij.use_journal(previous)
        if os.path.exists(db_path):
            os.unlink(db_path)


if __name__ == "__main__":
    main()
