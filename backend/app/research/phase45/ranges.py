"""The trailing range, accumulated live and computed by Phase 42's own code.

The forecast half of the gate quantity is "the typical one-minute move of the
underlying, taken from minutes strictly before the decision". Phase 42 computes
that from the raw store after the close; this board needs the same number while
the session is open. Rather than reimplement it — a second median is a second
definition, and the two would agree until one was corrected — this module only
*accumulates* the minute series and hands it to
:class:`app.research.phase42.exante.TrailingRange`.

Two things are refused rather than smoothed over:

* a decision whose immediately preceding minute was never observed is
  :data:`~app.research.phase45.CAPTURE_GAP` — a bar built across a hole charges
  the whole jump to one minute, which inflates the range and would make a
  thinly captured session admit more readily than a well captured one;
* a decision more than :data:`~app.research.phase45.MAX_DECISION_BAR_AGE_SEC`
  after the last observed minute is :data:`STALE_DECISION_BAR`.

Memory is bounded to the window the median needs plus a small margin, per
instrument, so a full session costs a fixed number of floats.
"""
from __future__ import annotations

import threading

from app.research.phase42 import RANGE_WINDOW
from app.research.phase42.exante import MINUTE, TrailingRange
from app.research.phase45 import (
    CAPTURE_GAP,
    MAX_DECISION_BAR_AGE_SEC,
    NO_RANGE,
    STALE_DECISION_BAR,
)

# The median needs RANGE_WINDOW changes, which is RANGE_WINDOW + 1 minutes; the
# margin lets the adjacency test see the minute before the window as well.
KEEP_MINUTES = RANGE_WINDOW + 4

LIVE_SOURCE = "LIVE_MINUTE_SERIES_FROM_THE_CAPTURE_TICK"


class LiveRanges:
    """One minute series per instrument, deduplicated to the last price seen."""

    def __init__(self, keep: int = KEEP_MINUTES) -> None:
        self._keep = max(4, int(keep))
        self._lock = threading.Lock()
        self._series: dict[str, dict[float, float]] = {}

    def note(self, instrument: str, ts: float, price: float | None) -> None:
        """Record the underlying price observed at ``ts``.

        The last observation inside a minute wins, so a densely sampled minute
        does not weigh more than a quiet one — the same rule Phase 42 applies
        when it rebuilds the series from the store.
        """
        if not instrument or not isinstance(price, (int, float)):
            return
        value = float(price)
        if value <= 0:
            return
        minute = float(ts) - (float(ts) % MINUTE)
        key = instrument.upper()
        with self._lock:
            slot = self._series.setdefault(key, {})
            slot[minute] = value
            if len(slot) > self._keep:
                for stale in sorted(slot)[: len(slot) - self._keep]:
                    slot.pop(stale, None)

    def before(self, instrument: str, ts: float) -> dict:
        """The typical one-minute move before ``ts``, or why there is none."""
        key = (instrument or "").upper()
        with self._lock:
            slot = dict(self._series.get(key) or {})
        if not slot:
            return _empty(NO_RANGE)
        minutes = sorted(slot)
        decision_minute = float(ts) - (float(ts) % MINUTE)
        last = minutes[-1]
        if decision_minute - last > MAX_DECISION_BAR_AGE_SEC:
            return _empty(STALE_DECISION_BAR, last_minute=last)
        # The minute immediately before the decision's own minute must have
        # been observed. Anything else means the window spans a hole.
        previous = decision_minute - MINUTE
        if previous not in slot and decision_minute not in slot:
            return _empty(CAPTURE_GAP, last_minute=last)
        series = TrailingRange(minutes, [slot[m] for m in minutes], LIVE_SOURCE)
        points, reason = series.before(float(ts))
        return {
            "trailing_range_points": points,
            "reason": reason,
            "source": LIVE_SOURCE,
            "minutes": len(minutes),
            "last_minute": last,
        }

    def reset(self) -> None:
        with self._lock:
            self._series.clear()


def _empty(reason: str, *, last_minute: float | None = None) -> dict:
    """A range that does not exist, and the reason it does not."""
    return {
        "trailing_range_points": None,
        "reason": reason,
        "source": LIVE_SOURCE,
        "minutes": 0,
        "last_minute": last_minute,
    }
