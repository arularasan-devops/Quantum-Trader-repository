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
FIRST fetch, which has no cached candles) still waits, and the cooldown is what
pushes a slot out of reach.
"""
from __future__ import annotations

import time

from app.market.angelone import (
    _HIST_COOLDOWN,
    _HIST_MIN_INTERVAL,
    _HIST_NONBLOCKING_MAX_WAIT,
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
wait = lim.reserve()
ok(0.0 < wait <= _HIST_MIN_INTERVAL + 1.0,
   f"declining must not have consumed a slot; blocking wait was {wait}")
ok(time.time() - began < 0.05, "reserve() must not sleep on the caller's behalf")

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
ok(cool.reserve() > _HIST_COOLDOWN * 0.5,
   "a blocking caller must still respect the cooldown")

# A generous budget accepts a queued slot: the guard is the budget, not a refusal
# to ever wait.
patient = _HistLimiter()
patient.try_reserve(_HIST_NONBLOCKING_MAX_WAIT)
w = patient.try_reserve(_HIST_MIN_INTERVAL * 2)
ok(w is not None and w > 0.0, f"a caller that can afford to wait must be served, got {w}")

print(f"\nHIST LIMITER SMOKE PASSED ({checks} checks)")
