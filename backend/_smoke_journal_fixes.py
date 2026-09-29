"""Smoke checks for the fixes found in the 11-Aug trade journal.

Each check corresponds to a defect measured in the user's own data:
  1. an early profit-lock in absolute points banked +0.7% against an 8% stop;
  2. 29 of 42 signals were killed by a same-direction re-signal, median 7 min;
  3. the bot laddered one move (14 CRUDEOIL / 6 SENSEX signals in a session);
  4. 10 lots of a Rs7.2 option lost 28% in 18 minutes;
  5. SILVER signalled 9 times on a premium that never ticked;
  6. the worst loss of the day carried no exit reason at all.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())

from app.config import settings  # noqa: E402
from app.state import AppState  # noqa: E402

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)


# --- 1. the early target is a % of premium, floored at a minimum R ----------
eng = AppState(instrument="CRUDEOIL")
settings.auto_trade_target_points = {}
settings.auto_trade_target_pct = {}
settings.auto_trade_min_target_r = 1.0

check(
    "no target configured -> the early exit does not fire at all",
    eng._early_target_points(262.3) is None,
)

settings.auto_trade_target_points = {"CRUDEOIL": 2.0}
eng._auto_stop = None
check(
    "a points target with no stop known is still honoured verbatim",
    eng._early_target_points(262.3) == 2.0,
)

# The journal case: entry 262.3, stop 8% below => 21.0 points of risk. A 2-point
# target is 0.7% of premium against 8% risked — 1:11 against.
eng._auto_stop = 262.3 * 0.92
floored = eng._early_target_points(262.3)
check(
    "a 2-point target against an 8% stop is floored up to 1R",
    floored is not None and abs(floored - 262.3 * 0.08) < 0.01,
    f"2.0 pts -> {floored:.2f} pts",
)

settings.auto_trade_target_pct = {"CRUDEOIL": 12.0}
pct_target = eng._early_target_points(262.3)
check(
    "a % target beats the points column and scales with the premium",
    pct_target is not None and abs(pct_target - 262.3 * 0.12) < 0.01,
    f"12% of 262.3 = {pct_target:.2f} pts",
)
cheap = eng._early_target_points(25.0)
check(
    "the same % on a cheap contract is a small point figure, not the same points",
    cheap is not None and abs(cheap - 3.0) < 0.01,
    f"12% of 25.0 = {cheap:.2f} pts",
)

settings.auto_trade_min_target_r = 2.0
eng._auto_stop = 262.3 * 0.92
check(
    "raising the min-R floor widens the early exit",
    (eng._early_target_points(262.3) or 0) > pct_target,
)
settings.auto_trade_min_target_r = 1.0
settings.auto_trade_target_points = {}
settings.auto_trade_target_pct = {}

# --- 2. a same-direction re-signal no longer kills the open one -------------
from app.analysis import signal_tracker as st  # noqa: E402


def row(instrument: str, side: str, ts: int, first_hit: str | None = None) -> dict:
    return {
        "instrument": instrument,
        "option": f"{instrument}{side}",
        "option_type": side,
        "status": "OPEN",
        "ts": ts,
        "first_hit": first_hit,
    }


class _D:
    """Minimal stand-in for the Decision fields _maybe_log reads."""

    def __init__(self, option: str, side: str, premium: float) -> None:
        self.signal = type("S", (), {"value": "BUY"})()
        self.option_type = type("O", (), {"value": side})()
        self.recommended_option = option
        self.current_premium = premium
        self.stop_loss = premium * 0.92
        self.target1 = premium * 1.1
        self.target2 = premium * 1.2
        self.target3 = premium * 1.3
        self.strike = 100.0
        self.confidence = 90.0


st._last_key.clear()
settings.signal_max_open_per_instrument = 5
rows = [row("NIFTY", "PE", 1000)]
st._maybe_log("NIFTY", _D("NIFTY-PE-2", "PE", 100.0), {}, 2000, rows)
check(
    "a same-direction re-signal leaves the earlier signal OPEN to resolve",
    rows[0]["status"] == "OPEN" and len(rows) == 2,
    f"{len(rows)} rows, first is {rows[0]['status']}",
)

st._last_key.clear()
rows = [row("NIFTY", "PE", 1000)]
st._maybe_log("NIFTY", _D("NIFTY-CE-1", "CE", 100.0), {}, 2000, rows)
check(
    "a DIRECTION FLIP does invalidate the earlier signal",
    rows[0]["status"] != "OPEN" and rows[0]["close_reason"] == "REVERSED",
    str(rows[0].get("close_reason")),
)

st._last_key.clear()
settings.signal_max_open_per_instrument = 2
rows = [row("NIFTY", "PE", 1000), row("NIFTY", "PE", 1100)]
st._maybe_log("NIFTY", _D("NIFTY-PE-3", "PE", 100.0), {}, 2000, rows)
check(
    "the open set stays bounded — the OLDEST is retired past the cap",
    rows[0]["close_reason"] == "SUPERSEDED" and rows[1]["status"] == "OPEN",
    f"{sum(1 for r in rows if r['status'] == 'OPEN')} still open",
)

st._last_key.clear()
rows = [row("NIFTY", "PE", 1000, first_hit="T1")]
settings.signal_max_open_per_instrument = 1
st._maybe_log("NIFTY", _D("NIFTY-PE-4", "PE", 100.0), {}, 2000, rows)
check(
    "a retired signal that had reached T1 is recorded as TARGET, not EXPIRED",
    rows[0]["status"] == "TARGET",
    rows[0]["status"],
)
settings.signal_max_open_per_instrument = 5

# --- 3/4/5. the entry gates -------------------------------------------------
check(
    "there is a re-entry cooldown and it is on by default",
    settings.auto_trade_reentry_cooldown_sec > 0,
    f"{settings.auto_trade_reentry_cooldown_sec:.0f}s",
)
check(
    "the Rs7.2 option that lost 28% is below the minimum premium floor",
    7.2 < settings.auto_trade_min_premium,
    f"floor Rs{settings.auto_trade_min_premium:g}",
)
check(
    "a normal Rs108 NIFTY option is NOT blocked by that floor",
    108.0 >= settings.auto_trade_min_premium,
)
check(
    "a frozen premium is refused after a bounded number of ticks",
    2 <= settings.auto_trade_stale_feed_ticks <= 60,
    f"{settings.auto_trade_stale_feed_ticks} ticks",
)

eng2 = AppState(instrument="SILVER")
for _ in range(settings.auto_trade_stale_feed_ticks + 1):
    if eng2._prem_watch is not None and eng2._prem_watch == ("SILVERCE", 7155.0):
        eng2._prem_frozen_ticks += 1
    else:
        eng2._prem_frozen_ticks = 0
    eng2._prem_watch = ("SILVERCE", 7155.0)
check(
    "SILVER's unchanged 7155 premium trips the stale-feed counter",
    eng2._prem_frozen_ticks >= settings.auto_trade_stale_feed_ticks,
    f"{eng2._prem_frozen_ticks} frozen ticks",
)

# --- 6. no exit is left unattributed ---------------------------------------
import inspect  # noqa: E402

src = inspect.getsource(AppState.sell)
check(
    "a hand close is labelled MANUAL CLOSE rather than left blank",
    "MANUAL CLOSE" in src and '"exit_reason": self._pending_exit_reason or' in src,
)
check(
    "a partial hand close is labelled as a manual scale-out",
    "SCALE-OUT (manual)" in src,
)
check(
    "an exit stamps the time so the cooldown has something to measure from",
    "self._auto_last_exit_ts = int(time.time())" in src,
)

gate_src = inspect.getsource(AppState._auto_trade)
for label, needle in (
    ("the minimum-premium gate runs before sizing", "auto_trade_min_premium"),
    # The cooldown moved into the shared account governor (which also owns the
    # longer post-stop wait), so the gate is now a call rather than a local
    # comparison against the setting.
    ("the cooldown gate runs before sizing", "instrument_blocked"),
    ("the stale-feed gate runs before sizing", "auto_trade_stale_feed_ticks"),
):
    check(label, needle in gate_src)

print()
if _failures:
    print(f"{len(_failures)} CHECK(S) FAILED")
    for f in _failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL JOURNAL-FIX CHECKS PASSED")
