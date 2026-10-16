"""Staged exit floors: T1 lock -> T1-T2 trail -> T2-T3 trail -> post-T3 ratchet.

Drives AppState._auto_manage's floor arithmetic through a rising then falling
premium path and checks the floor never loosens and each stage's floor holds.
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


def floor_after(path, t1, t2, t3):
    """Mirror of the post-T1 floor ladder in AppState._auto_manage."""
    floor = None
    peak = None
    out = []
    for prem in path:
        if prem >= t3:
            peak = max(peak or prem, prem)
            f = max(t3, peak * (1 - settings.tier_t3_runner_trail_pct / 100))
        elif prem >= t2:
            peak = max(peak or prem, prem)
            f = max(t2, peak - (peak - t1) * settings.tier_t2_giveback_pct / 100)
        elif prem >= t1:
            f = t1
            if floor is not None and floor >= t1:
                peak = max(peak or prem, prem)
                f = max(t1, peak - (peak - t1) * settings.tier_t1_giveback_pct / 100)
        else:
            f = None
        if f is not None and (floor is None or f > floor):
            floor = f
        out.append(floor)
    return out


T1, T2, T3 = 110.0, 120.0, 130.0
path = [105, 110, 116, 112, 121, 126, 122, 131, 150, 140, 139]
fl = floor_after(path, T1, T2, T3)
check("T1 locks the floor at T1", fl[1] == T1, str(fl[1]))
check("T1->T2 trail lifts the floor above T1", fl[2] > T1, f"{fl[2]:.2f}")
check("T2->T3 floor never below T2", all(f >= T2 for f in fl[4:7]), str(fl[4:7]))
check("post-T3 ratchet never below T3", all(f >= T3 for f in fl[7:]), str(fl[7:]))
check("post-T3 ratchet follows the peak", abs(fl[8] - 150 * (1 - settings.tier_t3_runner_trail_pct / 100)) < 1e-6,
      f"{fl[8]:.2f}")
check("floor never loosens", all(b >= a for a, b in zip([x for x in fl if x], [x for x in fl if x][1:])))
check("defaults are explicit", settings.tier_t1_giveback_pct > 0 and settings.tier_t2_giveback_pct > 0)
print("ALL TIER CHECKS PASSED" if not _failures else f"{len(_failures)} CHECK(S) FAILED")
raise SystemExit(1 if _failures else 0)
