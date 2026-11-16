"""Smoke: gap-up mode rules (app/engine/gapup.py) — lock ladder, cooldown,
no-buy-after-lock, restart persistence and the morning window."""
import datetime as dt
import os
import tempfile

# Always a throwaway dir: never touch the real order ledger (inside Docker
# QT_DATA_DIR is /app/data).
os.environ["QT_DATA_DIR"] = tempfile.mkdtemp()

from app.config import settings  # noqa: E402
from app.engine import gapup  # noqa: E402

FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(name)


def ist(h: int, m: int) -> float:
    return dt.datetime(2026, 11, 16, h, m, tzinfo=gapup.IST).timestamp()  # a Monday


check("gap % from prev close", round(gapup.gap_pct(101.0, 100.0), 6) == 1.0)
check("no gap without prev close", gapup.gap_pct(101.0, None) is None)
check("gap-up 1% -> CE", gapup.gap_side(1.0) == "CE")
check("gap-down 1% -> PE", gapup.gap_side(-1.0) == "PE")
check("gap 0.3% -> none", gapup.gap_side(0.3) is None and gapup.gap_side(-0.3) is None)
check("no gap -> none", gapup.gap_side(None) is None)
check("smoke uses a temp data dir", settings.data_dir != "/app/data", settings.data_dir)
check("NSE window open 09:15", gapup.in_window("NSE", ist(9, 15)))
check("NSE closed 09:10", not gapup.in_window("NSE", ist(9, 10)))
check("NSE closed 09:45", not gapup.in_window("NSE", ist(9, 45)))
check("MCX window open 09:00", gapup.in_window("MCX", ist(9, 0)))
check(f"high-qty target {settings.gapup_target_pts_hq:g} pts (configured)",
      gapup.target_points(1500) == settings.gapup_target_pts_hq)
check(f"low-qty target {settings.gapup_target_pts_lq:g} pts (configured)",
      gapup.target_points(250) == settings.gapup_target_pts_lq)

check("no lock below 5k", gapup.lock_floor(4999) is None)
check("lock 5k at 5k", gapup.lock_floor(5000) == 5000)
check("still 5k at 6.9k", gapup.lock_floor(6999) == 5000)
check("lock 7k at 7k", gapup.lock_floor(7000) == 7000)
check("lock 9k at 9.5k", gapup.lock_floor(9500) == 9000)

t = ist(9, 16)
b = gapup.DayBook()
check("fresh book allows entry", b.entry_block("SBIN", t) is None)
b.open_trade("SBIN", {"symbol": "SBIN-CE", "lots": 2, "notional": 50000}, t)
check("open trade blocks a second buy", b.entry_block("SBIN", t) is not None)
b.mark("SBIN", 3000, t)
check("3k MTM: no lock", b.floor is None and not b.breached(t))
b.close_trade("SBIN", 3000, t + 60)
check("60-min cooldown after exit", "cooldown" in (b.entry_block("SBIN", t + 120) or ""))
check("cooldown over after 60 min", b.entry_block("SBIN", t + 60 + 3601) is None)
check("other names unaffected", b.entry_block("ITC", t + 120) is None)
b.open_trade("ITC", {"symbol": "ITC-CE", "lots": 2}, t + 120)
b.mark("ITC", 2500, t + 130)  # 3000 + 2500 = 5500
check("5.5k day: lock 5k", b.floor == 5000, str(b.floor))
check("no new buys once locked", "lock" in (b.entry_block("TCS", t + 140) or ""))
b.mark("ITC", 4200, t + 150)  # 7200
check("7.2k day: lock 7k", b.floor == 7000, str(b.floor))
b.mark("ITC", 4100, t + 160)  # 7100
check("7.1k: not breached", not b.breached(t + 160))
b.mark("ITC", 3900, t + 170)  # 6900
check("6.9k: lock 7k breached -> sell all", b.breached(t + 170))
check("lock never steps down", b.floor == 7000)

b2 = gapup.DayBook()  # restart: reload from the order ledger
snap = b2.snapshot()
check("restart keeps lock", snap["lock"] == 7000, str(snap["lock"]))
check("restart keeps halt", "lock" in (b2.entry_block("TCS", t + 300) or ""))
check("restart keeps cooldown", "SBIN" in snap["last_exit"])
check("restart keeps open trade", b2.trade("ITC") is not None)
b2.update_trade("ITC", entry=1.0)
b2.open_trade("TCS", {"symbol": "TCS-CE", "entry": 1.0}, t + 400)
b4 = gapup.DayBook()  # restart: realised 3k + ITC (unknown yet) + TCS, floor 7k
b4.mark("TCS", 0, t + 410)
check("restart: no false lock exit before every trade ticked", not b4.breached(t + 410))
b4.mark("ITC", 3900, t + 420)  # 3000 + 3900 + 0 = 6900 <= 7000
check("restart: lock exit once all trades ticked", b4.breached(t + 420))
b5 = gapup.DayBook()
b5.mark("TCS", 0, t + 500)
check("restart: grace ends after 2 min", b5.breached(t + 500 + 121))
b2 = gapup.DayBook()
check("next day resets lock + cooldown",
      b2.entry_block("SBIN", t + 86400) is None and b2.floor is None)
b2.open_trade("NIFTY", {"symbol": "X"}, t + 86400)
b2.discard_trade("NIFTY")
check("discarded intent leaves no cooldown", b2.entry_block("NIFTY", t + 86401) is None)
b2.open_trade("NIFTY", {"symbol": "X"}, t + 86402)
b2.reject_trade("NIFTY", t + 86402)
check("broker-rejected buy starts cooldown", b2.entry_block("NIFTY", t + 86403) is not None
      and "NIFTY" not in b2.positions)
check("rejected cooldown ends after 60 min", b2.entry_block("NIFTY", t + 86402 + 3601) is None)

_cap, _pct = settings.gapup_max_loss_inr, settings.gapup_stop_pct
settings.gapup_max_loss_inr, settings.gapup_stop_pct = 1500.0, 15.0
check("HQ stop capped at Rs1500 (17.38 x 1650)",
      gapup.stop_price(17.38, 1650) == round(17.38 - 1500 / 1650, 2))
check("LQ stop stays 15% when tighter (20 x 150)", gapup.stop_price(20.0, 150) == 17.0)
settings.gapup_max_loss_inr = 0.0
check("cap 0 -> percent only", gapup.stop_price(17.38, 1650) == round(17.38 * 0.85, 2))
settings.gapup_max_loss_inr, settings.gapup_stop_pct = _cap, _pct

print("ALL PASS" if not FAIL else f"FAILED: {FAIL}")
raise SystemExit(1 if FAIL else 0)
