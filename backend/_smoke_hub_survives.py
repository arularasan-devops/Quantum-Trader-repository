"""Smoke: the tick loop outlives a raising tick, a raising cycle and a slow
research pass. Any of these used to end Hub.run silently, freezing every LTP
and auto-exit check while the API still answered."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="qt-smoke-hub-")
os.environ["QT_DATA_DIR"] = _TMP

from app.config import settings  # noqa: E402

settings.data_dir = _TMP
from app import main as main_mod  # noqa: E402
from app.research.phase45 import service as p45  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


class _Pos:
    option_symbol = ""


class _Snap:
    def model_dump(self, mode: str = "json") -> dict:
        return {}


class _State:
    def __init__(self, name: str, boom: bool = False) -> None:
        self.instrument, self.position, self.boom, self.ticks = name, _Pos(), boom, 0

    def tick(self) -> _Snap:
        self.ticks += 1
        if self.boom:
            raise RuntimeError("broker said no")
        return _Snap()


class _Registry:
    def __init__(self, states) -> None:
        self.states, self.fail_once = states, False

    def get(self, instrument):
        return self.states[0]

    def active(self):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("one bad cycle")
        return list(self.states)


class _Futures:
    def get(self, instrument):
        return None


good, bad = _State("INST0"), _State("INST1", boom=True)
reg = _Registry([good, bad])
main_mod.registry = reg
main_mod.futures_paper.store = _Futures()
main_mod.DEFAULT_INSTRUMENT = "INST0"
settings.auto_trade_enabled = True
settings.futures_paper_enabled = False
settings.auto_pick_enabled = False
settings.day_movers_auto_enabled = False
settings.tick_interval_seconds = 0.4
settings.phase45_shadow = True
p45.resolve_due = lambda: time.sleep(3.0)

hub = main_mod.Hub()


async def drive() -> tuple[int, bool]:
    task = asyncio.get_event_loop().create_task(hub.run())
    await asyncio.sleep(1.5)
    before = good.ticks
    reg.fail_once = True
    await asyncio.sleep(1.5)
    alive = not task.done()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return before, alive


before, alive = asyncio.run(drive())
check("a raising tick does not end the loop", alive)
check("the raising instrument is counted, not hidden", hub.tick_errors > 0 and bad.ticks > 1,
      f"{hub.tick_errors} errors, {bad.ticks} attempts")
check("other instruments keep ticking past it", good.ticks > 3, f"{good.ticks} ticks")
check("a raising cycle is counted and the loop goes on",
      hub.cycle_errors >= 1 and good.ticks > before, f"{good.ticks - before} ticks after it")
check("a 3s research pass does not hold the ticks", good.ticks >= 5, f"{good.ticks} ticks in 3s")
print("\nALL HUB-SURVIVAL CHECKS PASSED" if not FAILED else f"\nFAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
