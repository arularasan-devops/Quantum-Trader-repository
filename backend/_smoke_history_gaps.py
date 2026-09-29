"""Smoke test: the one-minute history recorder stores whole windows, not one bar.

Blocker B from the Phase 6 export: 137 of 357 Crude one-minute rows were missing
(38%), and the same shape reproduces in this repo's own store (CRUDEOIL 25.8%,
NIFTY 36.0% missing, with contiguous 50+ bar holes). The cause is not the feed —
the provider's window holds those bars — it is that each pass persisted only
``candles[-1]``, so every minute that elapsed between two scans of the same
instrument was never written.

What is proved: a window write stores every bar it is given, re-writing is a
no-op, and ``candle_gaps`` measures a hole rather than hiding it.
"""
from __future__ import annotations

import os
import tempfile

from app.models import Candle
from app.storage import HistoryStore

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


def _bars(start: int, n: int, skip: set[int] | None = None) -> list[Candle]:
    skip = skip or set()
    return [
        Candle(time=start + i * 60, open=100.0, high=101.0, low=99.0, close=100.5,
               volume=10.0)
        for i in range(n) if i not in skip
    ]


def main() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = HistoryStore(path)
        start = 1_700_000_000

        # A single pass hands over its whole trailing window.
        written = store.record_candles("TEST", _bars(start, 60))
        ok(written == 60, f"the whole window must be written, got {written}")
        ok(len(store.candles("TEST", 500)) == 60, "all 60 bars must be readable")

        gaps = store.candle_gaps("TEST", 500)
        ok(gaps["missing"] == 0, f"a complete window has no holes: {gaps}")
        ok(gaps["missing_pct"] == 0.0, f"missing_pct must be 0: {gaps}")

        # The same pass repeating (which is what a scan does) must not duplicate.
        store.record_candles("TEST", _bars(start, 60))
        ok(len(store.candles("TEST", 500)) == 60, "re-writing a bar is a no-op")

        # The old one-bar-per-pass behaviour, reproduced: bars in between are lost
        # and the gap report has to show it.
        for c in _bars(start + 100 * 60, 10, skip={2, 3, 4, 7}):
            store.record_candle("HOLED", c)
        holed = store.candle_gaps("HOLED", 500)
        ok(holed["missing"] == 4, f"four dropped bars must be counted: {holed}")
        ok(holed["gaps"] and holed["gaps"][0]["missing_bars"] == 3,
           f"the largest hole must be reported: {holed['gaps']}")

        # An overnight/weekend absence is not a missing bar.
        store.record_candles("SESSIONS", _bars(start, 5))
        store.record_candles("SESSIONS", _bars(start + 86_400, 5))
        sess = store.candle_gaps("SESSIONS", 500)
        ok(sess["missing"] == 0,
           f"a session boundary must not be counted as missing: {sess}")

        print(f"HISTORY GAPS SMOKE PASSED ({checks} checks)")
    finally:
        if os.path.exists(path):
            os.unlink(path)


if __name__ == "__main__":
    main()
