"""v3.5.10: stepped profit lock, pause-then-small-trades after a breach,
journal rebuild of empty day counters, entry-chase guard. Standalone, temp data dir."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
os.environ["QT_DATA_DIR"] = tempfile.mkdtemp()
from app.config import settings  # noqa: E402

settings.data_dir = os.environ["QT_DATA_DIR"]
from app.engine import account_risk as ar  # noqa: E402

fails = 0


def check(name, ok, got=None):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — got {got!r}"))
    fails += 0 if ok else 1


settings.daily_profit_target = 30000.0
settings.daily_lock_steps = "15000:50,30000:75,60000:85"
check("no lock below first step", ar.day_lock_level(14000) is None, ar.day_lock_level(14000))
check("peak 23.8k keeps 50%", abs(ar.day_lock_level(23778) - 11889) < 1, ar.day_lock_level(23778))
check("peak 40k keeps 75%", ar.day_lock_level(40000) == 30000, ar.day_lock_level(40000))
check("peak 80k keeps 85%", ar.day_lock_level(80000) == 68000, ar.day_lock_level(80000))
settings.daily_profit_target = 0.0
check("target 0 disables the lock", ar.day_lock_level(80000) is None)
settings.daily_profit_target = 30000.0
settings.daily_lock_steps = ""
check("empty steps = old rule (none under target)", ar.day_lock_level(28739) is None)
check("empty steps = old rule (75% over target)", ar.day_lock_level(40000) == 30000)
settings.daily_lock_steps = "15000:50,30000:75,60000:85"

settings.daily_lock_pause_sec = 1800.0
settings.daily_lock_resume_room = 6000.0
settings.daily_lock_max_resumes = 1
a = ar.AccountRisk()
t = 1_800_000_000.0
a.roll_day(t)
a.day_pnl = 20000.0
a.note_equity(t, 0.0)
check("peak 20k -> lock 10k", a.lock_level() == 10000, a.lock_level())
check("not blocked at peak", a.blocked(t) is None, a.blocked(t))
a.day_pnl = 9000.0
a.note_equity(t + 10, 0.0)
check("breach flattens", a.must_flatten(9000.0))
b = a.blocked(t + 20) or ""
check("breach pauses (not whole day)", "pausing" in b, b)
a.note_equity(t + 1900, 0.0)
check("pause over -> unblocked", a.blocked(t + 1900) is None, a.blocked(t + 1900))
check("resume floor = equity - 6k", a.lock_level() == 3000, a.lock_level())
budget = ar.entry_loss_budget(a.day_peak_equity, 9000.0, per_trade_cap=15000, lock=a.lock_level())
check("post-lock budget small (50% of 6k)", budget == 3000, budget)
check("open position not flattened after resume", not a.must_flatten(9000.0))
a.day_pnl = 12000.0
a.note_equity(t + 2000, 0.0)
check("floor trails new highs", a.lock_level() == 6000, a.lock_level())
a2 = ar.AccountRisk()
a2.roll_day(t + 2001)
check("floor persisted across restart", a2.lock_level() == 6000 and a2.lock_resumes == 1, a2.lock_level())
a.day_pnl = 5000.0
a.note_equity(t + 2100, 0.0)
b = a.blocked(t + 9999) or ""
check("second breach stops for the day", "stopped for today" in b, b)

r = ar.AccountRisk()
r.roll_day(t)
r.reset()
r.roll_day(t)
check("restore fills empty counters", r.restore_day(t, 21324.0, 7) and r.day_pnl == 21324.0
      and r.trades_today == 7 and r.day_peak_equity == 21324.0, (r.day_pnl, r.trades_today))
check("restore never overwrites live counters", not r.restore_day(t, 999.0, 9) and r.day_pnl == 21324.0)

from app.market import instruments  # noqa: E402
check("TATAMOTORS/IRCTC/ALUMINIUM removed",
      not ({"TATAMOTORS", "IRCTC", "ALUMINIUM"} & set(instruments.REGISTRY)))

import inspect  # noqa: E402
from app import state  # noqa: E402
src = inspect.getsource(state)
check("chase guard wired", "ENTRY_CHASE" in src and "auto_trade_max_chase_pct" in src)
check("loss budget sized on the ask", "_auto_entry_ask" in src)
chase = (38.45 - 36.8) / (36.8 - 33.9) * 100
check("TITAN 22-Oct would be skipped (57% > 30%)", chase > settings.auto_trade_max_chase_pct, chase)

print("\nALL V3.5.10 CHECKS PASSED" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
