"""Smoke: the tick scheduler must never stop scanning.

The reported symptom was "only 5 instruments scanned" on a 46-name watchlist,
with a measured scan lag of 789s against a 2s target — and the futures tool
enabled but never trading. One bug caused both: the scan's time budget was
measured from the TOP of the cycle, so the mandatory watched ticks spent it
before the loop reached a single scanned instrument, and the loop broke out
every cycle forever. The futures engine only evaluates a setup on a tick, so a
dead scan is a silent futures tool.

These checks run the real Hub loop with deliberately slow ticks — slower than
the whole scan budget — and assert the scan still advances.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="qt-smoke-scan-")
os.environ["QT_DATA_DIR"] = _TMP

from app.config import settings  # noqa: E402

settings.data_dir = _TMP

from app import main as main_mod  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


class _FakePosition:
    option_symbol = ""


class _FakeSnapshot:
    """Only what the hub touches: a JSON dump for the websocket push."""

    def model_dump(self, mode: str = "json") -> dict:
        return {}


class _FakeState:
    """An instrument whose tick costs a fixed, controllable amount of time."""

    def __init__(self, instrument: str, cost: float) -> None:
        self.instrument = instrument
        self.position = _FakePosition()
        self._cost = cost
        self.ticks = 0

    def tick(self) -> _FakeSnapshot:
        time.sleep(self._cost)
        self.ticks += 1
        return _FakeSnapshot()


def run_loop(states: list[_FakeState], seconds: float) -> main_mod.Hub:
    """Drive the REAL Hub.run loop over fake instruments for a wall-clock span."""
    hub = main_mod.Hub()

    async def drive() -> None:
        task = asyncio.get_event_loop().create_task(hub.run())
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    return hub


# The hub reaches for the registry, the futures store and the default
# instrument; substitute all three so the test is about scheduling only.
class _FakeRegistry:
    def __init__(self, states: list[_FakeState]) -> None:
        self._states = states

    def get(self, instrument: str) -> _FakeState:
        return self._states[0]

    def active(self) -> list[_FakeState]:
        return list(self._states)


class _FakeFuturesStore:
    def get(self, instrument: str):  # noqa: ANN201 - mirrors the real signature
        return None


# --- 1. a slow watched tick must not stop the scan --------------------------
# 0.5s per tick is louder than reality but the same shape: one mandatory watched
# tick already costs more than the whole 0.24s scan budget. Before the fix this
# produced exactly zero scanned ticks, forever.
SLOW = 0.5
states = [_FakeState(f"INST{i}", SLOW) for i in range(8)]
main_mod.registry = _FakeRegistry(states)
main_mod.futures_paper.store = _FakeFuturesStore()
main_mod.DEFAULT_INSTRUMENT = "INST0"
settings.auto_trade_enabled = True
settings.futures_paper_enabled = False
settings.auto_pick_enabled = False
settings.day_movers_auto_enabled = False
settings.tick_interval_seconds = 0.4

hub = run_loop(states, 4.0)

watched_ticks = states[0].ticks
scanned = [s for s in states[1:] if s.ticks > 0]
check(
    "the watched instrument still ticks every cycle",
    watched_ticks > 0,
    f"{watched_ticks} ticks",
)
check(
    "a watched tick slower than the whole scan budget does NOT stop the scan",
    len(scanned) > 0,
    f"{len(scanned)} of 7 scanned instruments ticked",
)
check(
    "the per-cycle floor is honoured, so the scan advances every cycle",
    hub.scanned_per_cycle > 0,
    f"{hub.scanned_per_cycle} scanned per cycle",
)
check(
    "coverage spreads over the universe instead of repeating one name",
    len(scanned) >= 2,
    f"ticked: {[s.instrument for s in scanned]}",
)
check(
    "scan ages are recorded for scanned names, not only watched ones",
    len([i for i in hub.scan_ages() if i != "INST0"]) >= 1,
    f"{sorted(hub.scan_ages())}",
)

# --- 2. the floor is a floor, not a free-for-all ---------------------------
# The watched instrument must not be starved by the scan: with slow ticks the
# scan takes the minimum and stops, so watched ticks stay the majority.
check(
    "the scan takes its floor and stops — the watched name still leads",
    watched_ticks >= max((s.ticks for s in states[1:]), default=0),
    f"watched {watched_ticks} vs busiest scanned {max((s.ticks for s in states[1:]), default=0)}",
)
check(
    "the floor is small enough to bound the cost of a cycle",
    main_mod.Hub._SCAN_MIN_PER_CYCLE <= 3,
    f"_SCAN_MIN_PER_CYCLE={main_mod.Hub._SCAN_MIN_PER_CYCLE}",
)

# --- 3. futures-only arming scans too -------------------------------------
# The futures engine sees a setup only on a tick. With just futures enabled the
# scan must still run, or the tool trades nothing but the default instrument.
for s in states:
    s.ticks = 0
settings.auto_trade_enabled = False
settings.futures_paper_enabled = True
hub2 = run_loop(states, 3.0)
check(
    "with only the FUTURES tool armed, non-default instruments are still scanned",
    any(s.ticks > 0 for s in states[1:]),
    f"{len([s for s in states[1:] if s.ticks > 0])} of 7 ticked",
)

# --- 4. fast ticks: the scan keeps up ------------------------------------
# With a realistic cheap tick the whole universe must be swept quickly, so the
# budget is not silently throttling a scan that could keep up.
for s in states:
    s.ticks = 0
fast = [_FakeState(f"F{i}", 0.005) for i in range(8)]
main_mod.registry = _FakeRegistry(fast)
main_mod.DEFAULT_INSTRUMENT = "F0"
settings.auto_trade_enabled = True
hub3 = run_loop(fast, 3.0)
check(
    "a cheap tick sweeps the entire universe",
    all(s.ticks > 0 for s in fast),
    f"{len([s for s in fast if s.ticks > 0])} of 8 ticked",
)

# --- 5. the report explains the lag instead of only flagging it -----------
check(
    "the measured per-tick cost is reported",
    hub3.tick_cost_ms is not None and hub3.tick_cost_ms > 0,
    f"{hub3.tick_cost_ms}ms",
)
sweep = hub3.sweep_estimate_sec(7)
check(
    "a full-sweep estimate is derived from the measured cost, not a constant",
    sweep is not None and sweep > 0,
    f"~{sweep}s for 7 scanned names",
)
check(
    "the sweep estimate grows with the universe (a bigger list IS slower)",
    (hub3.sweep_estimate_sec(40) or 0) > (hub3.sweep_estimate_sec(7) or 0),
    f"7 names ~{hub3.sweep_estimate_sec(7)}s vs 40 names ~{hub3.sweep_estimate_sec(40)}s",
)
check(
    "an empty scan set reports no sweep rather than a fake 0s",
    hub3.sweep_estimate_sec(0) is None,
)

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: {FAILED}")
    sys.exit(1)
print("ALL SCAN-SCHEDULER CHECKS PASSED")
