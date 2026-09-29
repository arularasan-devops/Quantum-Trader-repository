"""Smoke test for the futures risk governor and the cost-aware T1 lock.

Every check here corresponds to something that actually happened in one live
paper session: four "TARGET 1 lock" exits that closed at 0 or +1 point for a
loss of the round trip, and six stop-outs in a row on the same instrument
alternating LONG/SHORT one to seventeen minutes apart, with no cap of any kind
to stop the tool paying for the same chop over and over.
"""
from __future__ import annotations

import tempfile

from app.config import settings

settings.data_dir = tempfile.mkdtemp(prefix="futguard_")
settings.futures_paper_enabled = True
settings.futures_capital = 1_000_000.0

from app.execution import futures_paper as fp  # noqa: E402
from app.models import Candle, MarketStatus  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


def candles(n: int, start: float, step: float, atr: float = 40.0) -> list[Candle]:
    out, px = [], start
    for i in range(n):
        px += step
        out.append(Candle(time=1_700_000_000 + i * 60, open=px - step, high=px + atr / 2,
                          low=px - atr / 2, close=px, volume=1000))
    return out


UP = candles(120, 24000.0, 8.0)
DOWN = candles(120, 24000.0, -8.0)
T = 1_700_010_000


def fresh() -> None:
    fp.store.set("NIFTY", None)
    fp.guard.reset()


def enter(ts: int, cs=UP):
    return fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
                   200.0, ts)


def stop_out(ts: int) -> None:
    """Open a position and immediately run it into its stop."""
    enter(ts)
    pos = fp.store.get("NIFTY")
    assert pos is not None, "expected a position to stop out"
    fp.tick("NIFTY", UP, pos.stop - 1.0, 40.0, MarketStatus.TRENDING, None, None,
            200.0, ts + 60)


# ---- 1. the T1 lock is cost-aware and closes GREEN --------------------------
fresh()
sig = enter(T)
pos = fp.store.get("NIFTY")
lock = pos.floor_points()
cost = fp._cost_points("NIFTY", pos.entry, pos.entry, pos.lot_size, pos.lots)
check("the T1 lock distance is at least the full round-trip cost",
      lock > cost, f"lock {lock:.1f} pts vs {cost:.1f} pts of cost")
check("the T1 lock is a fraction of R, not the whole 1R",
      cost < lock < pos.risk_pts, f"lock {lock:.1f} vs R {pos.risk_pts:.1f}")
fp.tick("NIFTY", UP, pos.t1 + 0.1, 40.0, MarketStatus.TRENDING, None, None, 200.0, T + 60)
sig = fp.tick("NIFTY", UP, pos.floor - 0.5, 40.0, MarketStatus.TRENDING, None, None,
              200.0, T + 120)
check("an exit on the T1 lock books a profit, not a cost-sized loss",
      sig.state == "CLOSED" and (sig.net_rupees or 0) > 0, f"₹{sig.net_rupees}")

# With the lock switched off it degenerates to the old break-even behaviour,
# which is exactly the measured defect — asserted so the fix cannot silently
# regress to it.
fresh()
settings.futures_t1_lock_r = 0.0
enter(T)
pos = fp.store.get("NIFTY")
fp.tick("NIFTY", UP, pos.t1 + 0.1, 40.0, MarketStatus.TRENDING, None, None, 200.0, T + 60)
sig = fp.tick("NIFTY", UP, pos.entry + 0.1, 40.0, MarketStatus.TRENDING, None, None,
              200.0, T + 120)
check("a pure break-even lock would still lose the round trip (the old bug)",
      sig.state == "CLOSED" and (sig.net_rupees or 0) < 0, f"₹{sig.net_rupees}")
settings.futures_t1_lock_r = 0.5

# ---- 2. re-entry cooldown ---------------------------------------------------
fresh()
settings.futures_reentry_cooldown_sec = 600.0
settings.futures_flip_block_sec = 0.0
stop_out(T)
sig = enter(T + 120)
check("no re-entry inside the cooldown window",
      sig.state == "BLOCKED" and "cooldown" in (sig.reason or ""), sig.reason)
sig = enter(T + 900)
check("re-entry is allowed once the cooldown has expired",
      sig.state == "ENTERED", sig.reason)

# ---- 3. no immediate side flip after a stop ---------------------------------
fresh()
settings.futures_reentry_cooldown_sec = 0.0
settings.futures_flip_block_sec = 900.0
stop_out(T)
sig = fp.tick("NIFTY", DOWN, DOWN[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, T + 120)
check("the opposite side is refused right after a stop (same chop, not a signal)",
      sig.state == "BLOCKED" and "flip" in (sig.reason or ""), sig.reason)
sig = fp.tick("NIFTY", DOWN, DOWN[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, T + 1200)
check("the flip is allowed again once the block expires",
      sig.state == "ENTERED" and sig.side == "SHORT", sig.reason)

# ---- 4. consecutive-loss stand-down -----------------------------------------
# The caps deliberately stack, and in real trading the daily loss cap would fire
# first here (three NIFTY stops is ~Rs21,500). Lifted for this scenario so the
# stand-down is what is being measured rather than the loss cap.
fresh()
settings.futures_max_daily_loss = 10_000_000.0
settings.futures_reentry_cooldown_sec = 0.0
settings.futures_flip_block_sec = 0.0
settings.futures_max_consecutive_losses = 3
settings.futures_stand_down_sec = 3600.0
for i in range(3):
    stop_out(T + i * 300)
sig = enter(T + 1000)
check("3 stop-outs in a row stand the instrument down",
      sig.state == "BLOCKED" and "losses in a row" in (sig.reason or ""), sig.reason)
sig = enter(T + 3600 + 1000)
check("the stand-down expires after its window",
      sig.state == "ENTERED", sig.reason)

# ---- 5. per-instrument daily trade cap --------------------------------------
fresh()
settings.futures_max_consecutive_losses = 99
settings.futures_max_trades_per_day = 3
for i in range(3):
    stop_out(T + i * 300)
sig = enter(T + 2000)
check("the per-instrument daily trade cap stops the ladder",
      sig.state == "BLOCKED" and "cap is reached" in (sig.reason or ""), sig.reason)

# ---- 6. daily loss cap ------------------------------------------------------
fresh()
settings.futures_max_trades_per_day = 99
settings.futures_max_daily_loss = 5000.0
settings.futures_max_consecutive_losses = 3
stop_out(T)
check("one stop-out is enough to breach a Rs5,000 cap in this scenario",
      fp.guard.day_pnl <= -5000.0, f"day P&L ₹{fp.guard.day_pnl:,.0f}")
sig = enter(T + 5000)
check("the tool halts for the session once the daily loss cap is hit",
      sig.state == "BLOCKED" and "daily loss cap" in (sig.reason or ""), sig.reason)
settings.futures_max_daily_loss = 15000.0

# ---- 7. the governor survives a restart and rolls over at the day boundary --
fresh()
settings.futures_reentry_cooldown_sec = 600.0
stop_out(T)
reloaded = fp._Guard()
check("governor state is persisted, not held only in memory",
      reloaded.blocked("NIFTY", "LONG", T + 120) is not None,
      reloaded.blocked("NIFTY", "LONG", T + 120) or "")
check("a new trading day clears the caps",
      reloaded.blocked("NIFTY", "LONG", T + 86_400 * 2) is None)

# ---- 8. the cost-to-risk gate is a setting, and it bites --------------------
fresh()
settings.futures_max_cost_to_risk_pct = 1.0
sig = enter(T)
check("a 1% cost-to-risk ceiling refuses everything (the gate is live)",
      sig.state == "BLOCKED" and "costs would exceed" in (sig.reason or ""), sig.reason)
settings.futures_max_cost_to_risk_pct = 12.0

# ---- 9. the board reports what the governor has spent -----------------------
fresh()
stop_out(T)
snap = fp.guard.snapshot(T + 120)
check("the board exposes day P&L, the cap and per-instrument trade counts",
      snap["trades_today"].get("NIFTY") == 1 and snap["day_pnl"] < 0
      and snap["daily_loss_cap"] > 0, str(snap))

print()
print(f"{len(FAILS)} failure(s)" if FAILS else "ALL FUTURES GUARD CHECKS PASSED")
for f in FAILS:
    print("  -", f)
raise SystemExit(1 if FAILS else 0)
