"""Pre-T1 profit trail: the give-back floor must scale with the premium.

Measured in the 12-Aug signal export (the first written by the fixed tracker):
3 of 12 stop-outs had already run +4% to +10.5% before rolling over, two of them
sat open for over three hours. The pre-T1 trail exists to bank those, but its
minimum give-back was expressed in premium POINTS — the same unit mistake the
target field had. On a Rs15 SBIN option a 2-point floor is 13% of the premium,
wider than the whole 8% stop, so the trail could never fire on a cheap contract
and the trade drifted into the hard stop.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())

from app.config import settings  # noqa: E402

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)


def giveback(entry: float, peak: float) -> float:
    """Mirror of the floor arithmetic in AppState._auto_manage."""
    run = peak - entry
    pct_floor = settings.auto_trade_pre_t1_trail_min_giveback_pct
    floor = (
        entry * pct_floor / 100.0
        if pct_floor > 0
        else settings.auto_trade_pre_t1_trail_min_giveback
    )
    return max(floor, run * (settings.auto_trade_pre_t1_trail_giveback_pct / 100.0))


check(
    "the % give-back floor is on by default",
    settings.auto_trade_pre_t1_trail_min_giveback_pct > 0,
    f"{settings.auto_trade_pre_t1_trail_min_giveback_pct}% of premium",
)

# --- the cheap contract that the points floor disabled ----------------------
ENTRY_CHEAP = 15.6            # the SBIN option from the 12-Aug export
peak = ENTRY_CHEAP * 1.06     # armed: 6% above entry
gb = giveback(ENTRY_CHEAP, peak)
stop_distance = ENTRY_CHEAP * settings.min_stop_pct_of_premium / 100.0
check(
    "cheap option: the give-back is narrower than the hard stop",
    gb < stop_distance,
    f"give-back {gb:.2f} vs stop {stop_distance:.2f} pts",
)
check(
    "cheap option: the trail sits above entry, so it can actually fire",
    peak - gb > ENTRY_CHEAP,
    f"trail {peak - gb:.2f} vs entry {ENTRY_CHEAP:.2f}",
)

settings.auto_trade_pre_t1_trail_min_giveback_pct = 0.0
gb_old = giveback(ENTRY_CHEAP, peak)
check(
    "regression: the old points floor was wider than the whole stop",
    gb_old > stop_distance and peak - gb_old < ENTRY_CHEAP,
    f"old give-back {gb_old:.2f} pts, trail {peak - gb_old:.2f} below entry",
)
settings.auto_trade_pre_t1_trail_min_giveback_pct = 0.8

# --- the expensive contract must be unaffected ------------------------------
ENTRY_RICH = 255.6            # the CRUDEOIL option from the same export
peak_rich = ENTRY_RICH * 1.06
gb_rich = giveback(ENTRY_RICH, peak_rich)
check(
    "expensive option: give-back still a share of the run, not a fixed point count",
    abs(gb_rich - (peak_rich - ENTRY_RICH) * 0.30) < 0.01,
    f"{gb_rich:.2f} pts on a {peak_rich - ENTRY_RICH:.2f} pt run",
)
check(
    "an identical run gives identical room on a cheap and a rich contract",
    abs(gb / ENTRY_CHEAP - gb_rich / ENTRY_RICH) < 0.001,
    f"cheap {gb / ENTRY_CHEAP * 100:.1f}% vs rich {gb_rich / ENTRY_RICH * 100:.1f}%",
)

# --- the floor never swallows a large run -----------------------------------
big_peak = ENTRY_RICH * 1.20
check(
    "on a big run the % floor is not the binding constraint",
    giveback(ENTRY_RICH, big_peak) > ENTRY_RICH * 0.008,
    f"{giveback(ENTRY_RICH, big_peak):.2f} pts",
)

print()
if _failures:
    print(f"{len(_failures)} CHECK(S) FAILED: {_failures}")
    raise SystemExit(1)
print("ALL PRE-T1 TRAIL CHECKS PASSED")
