"""Smoke test: a secondary (non-blocking) historical fetch never waits in the tick.

The live scan measured 37s per instrument tick with one instrument updating, and
three stack samples 10s apart all sat in the same frame:

    tick -> compute_indicators -> option_candles -> _historical_candles

``option_candles`` documents itself as non-blocking because option charts are
secondary to the futures decision. It was not: it reserved a slot on the shared
per-key historical limiter and slept until that slot arrived. With 3.5s spacing
per key and an exponential AB1021 cooldown (up to 90s), that sleep is a queue,
and it ran inside the tick.

What is proved: a non-blocking caller gives up rather than sleeping past its
budget, giving up does not consume a slot, a blocking caller (an instrument's
FIRST fetch, which has no cached candles) still waits but only inside its own
budget, and the cooldown is what pushes a slot out of reach.

The blocking path was the same fault one level up. It called ``reserve()``,
which returns the next free slot however far away it is, so a first fetch during
a 90s cooldown slept ~90s with the tick loop inside it. The stall witness
recorded it: CRUDEOIL held the loop 108.8s and every other instrument lost those
minutes too. ``reserve()`` is gone; both paths reserve against a budget, and an
instrument that keeps failing for a reason a retry cannot fix drops off the
blocking path after a bounded number of passes instead of paying it every tick
for the whole session.
"""
from __future__ import annotations

import time

from app.market.angelone import (
    _HIST_BLOCKING_BUDGET_SEC,
    _HIST_BLOCKING_PASSES,
    _HIST_COOLDOWN,
    _HIST_COOLDOWN_MAX,
    _HIST_MIN_INTERVAL,
    _HIST_NONBLOCKING_MAX_WAIT,
    _historical_candles,
    _HistLimiter,
)

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


ok(_HIST_NONBLOCKING_MAX_WAIT < _HIST_MIN_INTERVAL,
   "a non-blocking budget at or above the call spacing would still queue")

lim = _HistLimiter()

# A cold limiter has a slot available immediately, for either kind of caller.
first = lim.try_reserve(_HIST_NONBLOCKING_MAX_WAIT)
ok(first is not None, "cold limiter must hand out a slot")
ok(first == 0.0, f"cold slot must need no wait, got {first}")

# The next slot is a full spacing away, so a non-blocking caller must decline it
# rather than sleep 3.5s+ inside the tick.
second = lim.try_reserve(_HIST_NONBLOCKING_MAX_WAIT)
ok(second is None, f"non-blocking caller must decline a queued slot, got {second}")

# Declining must not consume the budget: a blocking caller still gets the slot it
# would have got, i.e. one spacing after the last call, not two.
began = time.time()
wait = lim.try_reserve(_HIST_BLOCKING_BUDGET_SEC)
ok(wait is not None and 0.0 < wait <= _HIST_MIN_INTERVAL + 1.0,
   f"declining must not have consumed a slot; blocking wait was {wait}")
ok(time.time() - began < 0.05, "reserving must not sleep on the caller's behalf")

# Repeated declines are stable: still no slot, still nothing consumed.
for _ in range(5):
    ok(lim.try_reserve(_HIST_NONBLOCKING_MAX_WAIT) is None,
       "a busy budget must keep declining")

# An AB1021 cooldown pushes the slot far out of reach; a non-blocking caller must
# decline for the whole cooldown rather than sleep through it.
cool = _HistLimiter()
cool.trip_cooldown()
ok(cool.try_reserve(_HIST_NONBLOCKING_MAX_WAIT) is None,
   "a cooling limiter must decline a non-blocking caller")
# ...and so must a BLOCKING caller, because the cooldown outruns its budget. This
# is the 108.8s wedge: the old blocking path waited for that slot however long it
# took, inside the tick.
ok(_HIST_BLOCKING_BUDGET_SEC < _HIST_COOLDOWN,
   "a blocking budget at or above the base cooldown would sleep through it")
ok(_HIST_BLOCKING_BUDGET_SEC < _HIST_COOLDOWN_MAX,
   "a blocking budget must not reach the capped cooldown")
ok(cool.try_reserve(_HIST_BLOCKING_BUDGET_SEC) is None,
   "a cooling limiter must decline a blocking caller too, not sleep it")
ok(not hasattr(cool, "reserve"),
   "the unbounded reserve() must be gone, not merely unused")

# The first fetch is bounded in passes as well as in seconds: an instrument whose
# fetch fails for a reason a retry cannot fix never acquires the cache that would
# take it off the blocking path.
ok(_HIST_BLOCKING_PASSES >= 1,
   "an instrument must get at least one blocking first fetch")
ok(_HIST_BLOCKING_PASSES * _HIST_BLOCKING_BUDGET_SEC < 30.0,
   "the blocking passes must not add up to a stall of their own")

# A generous budget accepts a queued slot: the guard is the budget, not a refusal
# to ever wait.
patient = _HistLimiter()
patient.try_reserve(_HIST_NONBLOCKING_MAX_WAIT)
w = patient.try_reserve(_HIST_MIN_INTERVAL * 2)
ok(w is not None and w > 0.0, f"a caller that can afford to wait must be served, got {w}")

# --- the fetch itself, against a broker that is timing out -------------------
# This is the shape the field log showed: a ConnectTimeout to the historical
# endpoint on every attempt. What must hold is that the fetch returns inside its
# budget and hands back no rows, rather than holding the tick.


class _TimingOutSmart:
    """Stands in for SmartConnect when the connect times out, as it did live."""

    def __init__(self) -> None:
        self.calls = 0

    def getCandleData(self, params: dict) -> dict:
        self.calls += 1
        raise OSError(
            "HTTPSConnectionPool(host='apiconnect.angelone.in', port=443): "
            "Max retries exceeded (connect timeout=4)")


class _FakeShard:
    def __init__(self) -> None:
        self.smart = _TimingOutSmart()
        self.hist = _HistLimiter()


shard = _FakeShard()
began = time.time()
rows, diag = _historical_candles(shard, {"symboltoken": "1"}, blocking=True)
took = time.time() - began
ok(rows == [], "a timing-out fetch must return no rows, never a fabricated bar")
ok(took <= _HIST_BLOCKING_BUDGET_SEC + 1.0,
   f"a blocking fetch must return inside its budget, took {took:.1f}s")
ok("error" in diag or "deferred" in diag, f"the failure must be stated, got {diag!r}")

# A cooling limiter is the wedge case: the slot is 12s+ away and the old blocking
# path slept until it arrived. It must now decline without ever calling out.
cooling = _FakeShard()
cooling.hist.trip_cooldown()
began = time.time()
rows, diag = _historical_candles(cooling, {"symboltoken": "1"}, blocking=True)
took = time.time() - began
ok(rows == [] and took < 1.0,
   f"a blocking fetch during a cooldown must defer at once, took {took:.1f}s")
ok(cooling.smart.calls == 0,
   "deferring must not spend a call on the key's budget")
ok(cooling.hist.stats()["deferrals"] == 1, "the deferral must be counted")

print(f"\nHIST LIMITER SMOKE PASSED ({checks} checks)")
