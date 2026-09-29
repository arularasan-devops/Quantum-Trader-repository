"""Memory and time for the Phase 42 sweep on a store larger than the live one.

The report is the place this project has twice run out of memory, so the sweep
is measured rather than assumed: it must stay flat in peak memory as the store
grows, because it only ever holds counters and one session's minute series.

    .venv/bin/python _bench_phase42.py [sessions] [legs_per_session]
"""

from __future__ import annotations

import os
import resource
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _smoke_phase42 import _leg, _obs, _quote  # noqa: E402
from app.research.phase35 import store  # noqa: E402
from app.research.phase42 import report  # noqa: E402

MIN = 60.0
T0 = 1_760_000_000.0
CHUNK = 20_000


def seed(con, sessions: int, per_session: int) -> None:
    for s in range(sessions):
        session = f"2026-{(s // 28) + 1:02d}-{(s % 28) + 1:02d}"
        base = T0 + s * 86_400
        obs, quotes, legs = [], [], []
        for i in range(per_session):
            obs_id = f"{session}-{i}"
            ts = base + (i % 360) * MIN
            obs.append(_obs(obs_id, ts=ts, session=session,
                            instrument="CRUDEOIL"))
            quotes.append(_quote(obs_id, ts=ts, underlying=8700.0 + i % 37))
            legs.append(_leg(f"{session}-L{i}", obs_id, ts=ts,
                             net=(2.0 if i % 2 else -3.0),
                             cost=(0.5, 1.0, 2.0, 4.0)[i % 4]))
            if len(obs) >= CHUNK:
                store.save_observations(con, obs)
                store.save_quotes(con, quotes)
                store.save_legs(con, legs)
                obs, quotes, legs = [], [], []
        store.save_observations(con, obs)
        store.save_quotes(con, quotes)
        store.save_legs(con, legs)
        print(f"  seeded {session}", flush=True)


def main() -> int:
    sessions = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    per_session = int(sys.argv[2]) if len(sys.argv) > 2 else 60_000
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bench.db")
        con = store.connect(path)
        seed(con, sessions, per_session)
        size = os.path.getsize(path) / 1e6
        started = time.monotonic()
        payload = report.run(con, with_giveback=True)
        took = time.monotonic() - started
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"store {size:.0f} MB, {sessions} sessions x {per_session} legs")
        print(f"sweep {took:.1f}s, peak RSS {peak:.0f} MB")
        print("verdict:", payload["verdict"]["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
