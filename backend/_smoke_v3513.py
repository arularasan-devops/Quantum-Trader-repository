"""v3.5.13: MCX session lock reset (morning lock kept as a floor), expired-pause
message, Paper Books pause/resume + square-off + carried-position exit, ABB /
SHRIRAMFIN in the universe, journal paper/live filter. No broker."""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_TRADE_MODE", "paper")
from app.config import settings  # noqa: E402
from app.engine import account_risk as ar  # noqa: E402
from app.engine import paper_books as pb  # noqa: E402
from app.market import instruments  # noqa: E402
from app.research.db import Database  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    fails += 0 if ok else 1


def ist(day_ts: float, hh: int, mm: int) -> float:
    """UTC ts of HH:MM IST on the IST day of day_ts."""
    mid = (int(day_ts) + 19800) // 86400 * 86400 - 19800
    return mid + hh * 3600 + mm * 60


settings.ignore_market_hours = False
settings.daily_profit_target = 30000.0
settings.daily_lock_steps = "15000:50,30000:75,60000:85"
settings.daily_lock_pause_sec = 600.0
settings.daily_lock_resume_room = 6000.0
settings.daily_lock_max_resumes = 1
settings.daily_lock_session_reset_ist = "15:30"
settings.data_dir = tempfile.mkdtemp()

# --- main account: session reset ------------------------------------------
day = time.time()
a = ar.AccountRisk()
a._loaded = True
t = ist(day, 11, 0)
a._roll(t)
a.day_pnl = 40000.0
a.note_equity(t, 0.0)
check("morning lock 75% of 40k", a.lock_level() == 30000, a.lock_level())
a.day_pnl = 28000.0
a.note_equity(t + 60, 0.0)
check("morning breach pauses", "pausing" in (a.blocked(t + 70) or ""), a.blocked(t + 70))
msg = a.blocked(t + 60 + 601) or ""
check("expired pause says 'pause is over'", "pause is over" in msg, msg)
a.note_equity(ist(day, 15, 31), 0.0)
check("15:30 reset clears the trip", a.blocked(ist(day, 15, 31)) is None, a.blocked(ist(day, 15, 31)))
check("carry = min(morning lock, equity - 6k)", a.lock_carry == 22000, a.lock_carry)
check("lock level = carry until evening steps", a.lock_level() == 22000, a.lock_level())
a.day_pnl = 28000.0 + 20000.0
a.note_equity(ist(day, 18, 0), 0.0)
check("evening 20k profit -> 50% step above base", a.lock_level() == 28000 + 10000, a.lock_level())
a.note_equity(ist(day, 19, 0), 0.0)
check("reset runs once per day", a.lock_base == 28000, a.lock_base)
a2 = ar.AccountRisk()
a2._load()
check("reset state persisted", a2.lock_base == 28000 and a2.lock_reset_day == a.day, (a2.lock_base, a2.lock_reset_day))
b = ar.AccountRisk()
b._loaded = True
b._roll(ist(day, 10, 0))
b.day_pnl = 5000.0
b.note_equity(ist(day, 15, 40), 0.0)
check("no morning lock -> no carry, fresh evening", b.lock_carry is None and b.lock_level() is None,
      (b.lock_carry, b.lock_level()))
settings.daily_lock_session_reset_ist = ""
c = ar.AccountRisk()
c._loaded = True
c._roll(ist(day, 10, 0))
c.day_pnl = 40000.0
c.note_equity(ist(day, 16, 0), 0.0)
check("reset off keeps old behaviour", c.lock_base == 0 and c.lock_level() == 30000, c.lock_level())
settings.daily_lock_session_reset_ist = "15:30"

# --- paper books ------------------------------------------------------------
check("squareoff MCX = 23:25", pb.squareoff_min("CRUDEOIL") == 23 * 60 + 25, pb.squareoff_min("CRUDEOIL"))
check("squareoff NSE = 15:25", pb.squareoff_min("SBIN") == 15 * 60 + 25, pb.squareoff_min("SBIN"))
B = pb.PaperBooks(Database(f"sqlite:///{tempfile.mkdtemp()}/b.db"))
k = B.books[0]
check("book cut-off after 23:25 MCX", B._past_cutoff("CRUDEOIL", ist(day, 23, 29)))
check("book open at 14:00 MCX", not B._past_cutoff("CRUDEOIL", ist(day, 14, 0)))
p = pb.Pos("t1", "CRUDEOIL", "CRUDEOIL X CE", 100, 9, 513.5, 480.0, 560, 600, 650,
           ist(day, 23, 20) - 86400, 513.5)
check("carried position closes next session", B._session_exit(p, ist(day, 9, 0)) == "SQUARE-OFF (carried)")
p2 = pb.Pos("t2", "SBIN", "SBIN X PE", 750, 3, 8.9, 7.9, 10.1, 11, 12, ist(day, 10, 0), 8.9)
check("NSE position squares off at 15:25", B._session_exit(p2, ist(day, 15, 26)) == "SQUARE-OFF")
check("NSE position held at 15:00", B._session_exit(p2, ist(day, 15, 0)) is None)
k.day = ar._today(ist(day, 10, 0))
k.day_realized, k.day_peak = 40000.0, 0.0
B._profit_lock(k, ist(day, 10, 0))
k.day_realized = 28000.0
B._profit_lock(k, ist(day, 10, 1))
check("book breach pauses (not whole day)", k.locked and k.pause_until > 0, (k.locked, k.pause_until))
B._profit_lock(k, ist(day, 10, 12))
check("book resumes after pause with small floor", not k.locked and k.floor == 22000, (k.locked, k.floor))
k.day_realized = 21000.0
B._profit_lock(k, ist(day, 10, 20))
check("second book breach stops (no resume left)", k.locked and k.pause_until == 0, (k.locked, k.pause_until))
B._profit_lock(k, ist(day, 15, 35))
check("book session reset at 15:30 frees it", not k.locked and k.reset_done, (k.locked, k.reset_done))
B._save_day(k, ist(day, 15, 35))
B2 = pb.PaperBooks(B.db)
B2._roll(ist(day, 15, 36), force=True)
k2 = B2.books[0]
check("book lock state persisted", k2.reset_done and k2.base == 21000 and k2.resumes == 0,
      (k2.reset_done, k2.base, k2.resumes))

# --- instruments / journal --------------------------------------------------
check("ABB + SHRIRAMFIN registered", "ABB" in instruments.REGISTRY and "SHRIRAMFIN" in instruments.REGISTRY)
check("ABB + SHRIRAMFIN active", "ABB" in instruments.UNIVERSE and "SHRIRAMFIN" in instruments.UNIVERSE)
from app.main import _filter_mode  # noqa: E402
rows = [{"mode": "paper"}, {"mode": "live"}, {}]
check("journal mode filter", len(_filter_mode(rows, "live")) == 1 and len(_filter_mode(rows, "paper")) == 2
      and len(_filter_mode(rows, "all")) == 3)

print("ALL V3.5.13 CHECKS PASSED" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
