"""Per-exchange trading-session checks.

MCX runs to 23:30 IST; NSE/BSE derivatives stop at 15:30. The whole app used ONE
09:00-23:30 window, so every NSE/BSE name looked live all evening: ~40 frozen
instruments kept consuming the scan budget and Angel's rate limit that the names
still trading needed, the dashboard said "Market OPEN" for a shut exchange, and a
frozen premium read to the mover engine exactly like a quiet one.

What must hold:

* the session window is resolved per exchange;
* a closed instrument is skipped by the background scan, and comes back BY ITSELF
  at the next open (evaluated every cycle, never latched at startup);
* the instrument on screen and anything holding a position are never skipped;
* a closed market is not counted as evidence that an instrument is dead;
* the simulated 24/7 demo (``ignore_market_hours``) is unaffected.
"""
from __future__ import annotations

import datetime as dt
import inspect
import sys

from app.config import settings

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def at(hh: int, mm: int, weekday: int = 1) -> float:
    """A UTC epoch for a given IST wall-clock time on a chosen weekday."""
    # 2026-08-11 is a Tuesday; shift to the requested weekday.
    day = dt.date(2026, 8, 11) + dt.timedelta(days=weekday - 1)
    return dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST).timestamp()


# --- 1. windows are per exchange --------------------------------------------
check(
    "MCX keeps the late commodity close",
    settings.session_window_ist("MCX") == (settings.market_open_ist, settings.market_close_ist),
    "09:00-23:30",
)
for exch in ("NFO", "NSE", "BFO", "BSE"):
    check(
        f"{exch} uses the equity bells, not MCX's",
        settings.session_window_ist(exch) == (settings.equity_open_ist, settings.equity_close_ist),
        "09:15-15:30",
    )
check(
    "an unknown/omitted exchange keeps the previous widest window",
    settings.session_window_ist(None) == (settings.market_open_ist, settings.market_close_ist),
    "backwards compatible",
)

# --- 2. the 16:00 case that motivated this ----------------------------------
open_mcx, _ = settings.market_session(at(16, 0), "MCX")
open_nfo, note_nfo = settings.market_session(at(16, 0), "NFO")
check("at 16:00 IST Crude is still trading", open_mcx is True)
check("at 16:00 IST an NSE option is NOT", open_nfo is False, note_nfo)
check(
    "and the note names the real window rather than MCX's",
    settings.equity_close_ist in note_nfo,
    note_nfo,
)

# 10:00 IST: both open. 08:00: neither. Weekend: neither.
check("at 10:00 IST both exchanges are open",
      settings.market_session(at(10, 0), "MCX")[0] and settings.market_session(at(10, 0), "NFO")[0])
check("before the bell neither is open",
      not settings.market_session(at(8, 0), "MCX")[0]
      and not settings.market_session(at(8, 0), "NFO")[0])
sat = at(11, 0, weekday=6)
check("weekends are closed on both",
      not settings.market_session(sat, "MCX")[0] and not settings.market_session(sat, "NFO")[0],
      settings.market_session(sat, "NFO")[1])
check(
    "09:15 exactly counts as open for equities (inclusive bell)",
    settings.market_session(at(9, 15), "NFO")[0] is True,
)
check(
    "09:00 is open for MCX but not yet for equities",
    settings.market_session(at(9, 0), "MCX")[0] and not settings.market_session(at(9, 0), "NFO")[0],
)

# --- 3. the scheduler skips closed instruments, reversibly ------------------
from app import main as app_main  # noqa: E402

sched = inspect.getsource(app_main.Hub.run)
check(
    "the background scan skips instruments whose exchange is shut",
    "_exchange_open(st.instrument)" in sched,
    "closed names cost budget for nothing",
)
check(
    "the skip is recomputed every cycle, so an open re-includes them with no restart",
    "closed_now = [" in sched and "while True" in sched,
    "no latched state",
)
check(
    "watched instruments and open positions are still ticked when closed",
    sched.index("watched.add(DEFAULT_INSTRUMENT)") < sched.index("closed_now = ["),
    "priority is built before the skip and is not filtered by it",
)
check(
    "the count of skipped-as-closed instruments is reported",
    "closed_skipped" in inspect.getsource(app_main.scan_health),
    "a small evening scan count must read as the session, not a starved loop",
)

# --- 4. the simulated demo is unaffected ------------------------------------
prev = settings.ignore_market_hours
try:
    settings.ignore_market_hours = True
    check(
        "with ignore_market_hours the scan treats everything as open",
        app_main._exchange_open("INFY") is True,
        "24/7 simulated demo and the smokes stay unaffected",
    )
finally:
    settings.ignore_market_hours = prev

# --- 5. a closed market is not evidence of a dead instrument ----------------
from app.analysis.day_movers import DayMovers  # noqa: E402

ev = inspect.getsource(DayMovers.evaluate)
check(
    "closed-exchange rows are dropped before coverage is computed",
    'r.get("session_open", True)' in ev,
    "otherwise 40 frozen names block the pick all evening",
)
check(
    "rows without the flag still count as open",
    'session_open", True' in ev,
    "default True keeps older callers working",
)

mv = DayMovers()
mv.reset()
rows = [
    {"symbol": "CRUDEOIL", "measured": True, "net_move_pct": 9.0, "session_open": True},
    {"symbol": "NATURALGAS", "measured": True, "net_move_pct": 8.0, "session_open": True},
    {"symbol": "GOLD", "measured": True, "net_move_pct": 7.0, "session_open": True},
    {"symbol": "SILVER", "measured": True, "net_move_pct": 6.0, "session_open": True},
    {"symbol": "COPPER", "measured": True, "net_move_pct": 5.0, "session_open": True},
    # Shut equity names: frozen, unmeasured — must not drag coverage down.
    {"symbol": "INFY", "measured": False, "net_move_pct": None, "session_open": False},
    {"symbol": "TCS", "measured": False, "net_move_pct": None, "session_open": False},
    {"symbol": "SBIN", "measured": False, "net_move_pct": None, "session_open": False},
    {"symbol": "LT", "measured": False, "net_move_pct": None, "session_open": False},
    {"symbol": "RELIANCE", "measured": False, "net_move_pct": None, "session_open": False},
]
res = mv.evaluate(rows, now=at(20, 0))
check(
    "an evening MCX-only day can still pick, because the shut names are excluded",
    res["action"] == "picked",
    res["reason"],
)
check(
    "and it holds only instruments whose market is actually open",
    all(s in {"CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER"} for s in res["held"]),
    ", ".join(res["held"]),
)
# The same set with the flag saying "open" but nothing measured must NOT pick:
# that is the cold-data case, and it must still refuse.
mv2 = DayMovers()
mv2.reset()
cold = [dict(r, session_open=True, measured=False, net_move_pct=None) for r in rows]
res2 = mv2.evaluate(cold, now=at(20, 0))
check(
    "cold-but-open data still refuses to pick",
    res2["action"] == "waiting",
    res2["reason"],
)
mv.reset()
mv2.reset()

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: {FAILED}")
    sys.exit(1)
print("ALL SESSION-AWARENESS CHECKS PASSED")
