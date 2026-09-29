"""Smoke test for the futures PRE-T1 protection.

The defect this covers, measured on the recorded paper book: 12 of 22 futures
trades were in profit at some point and still closed at the full stop, giving
back ~Rs16.5k of shown profit, because the protective floor was only created
AFTER T1 was touched. Before T1 the only exit was the hard stop.

Also covers the plan-geometry gate (a NATURALGAS plan carried T1 0.2 pts away
against ~2.1 pts of round-trip cost, so hitting it still booked -Rs2,580) and the
path/MAE capture the counterfactual needs to grade the rule later.
"""
from __future__ import annotations

import os
import tempfile

from app.config import settings

settings.data_dir = tempfile.mkdtemp(prefix="futpret1_")
settings.futures_paper_enabled = True
settings.futures_capital = 1000000.0

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


def reset() -> None:
    fp.store.set("NIFTY", None)
    fp.store.set("CRUDEOIL", None)
    fp.guard.reset()


UP = candles(120, 24000.0, 8.0)
DOWN = candles(120, 24000.0, -8.0)


def open_long(ts: int) -> tuple[object, float]:
    reset()
    sig = fp.tick("NIFTY", UP, UP[-1].close, 40.0, MarketStatus.TRENDING, None, None,
                  200.0, ts)
    assert sig.state == "ENTERED" and sig.side == "LONG", sig.reason
    pos = fp.store.get("NIFTY")
    return pos, pos.risk_pts


# ---- 1. a green LONG can no longer become a full-stop loss ------------------
settings.futures_pre_t1_trail = True
settings.futures_pre_t1_breakeven = True
settings.futures_pre_t1_giveback_exit = False
settings.futures_pre_t1_arm_r = 0.5

pos, risk = open_long(1_700_100_000)
entry, hard_stop = pos.entry, pos.stop
fp.tick("NIFTY", UP, entry + 0.2 * risk, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_100_060)
check("below the arm threshold nothing is armed and the stop is untouched",
      not fp.store.get("NIFTY").armed and fp.store.get("NIFTY").stop == hard_stop,
      f"stop {fp.store.get('NIFTY').stop}")

fp.tick("NIFTY", UP, entry + 0.7 * risk, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_100_120)
pos = fp.store.get("NIFTY")
check("arms once the favourable excursion passes the arm threshold", pos.armed)
check("armed BEFORE T1 (this is the stage that was missing)", not pos.hit_t1)
check("the stop is ratcheted up, above entry", pos.stop > entry,
      f"entry {entry} stop {pos.stop}")
check("the ratchet sits above entry by at least the round-trip cost, so an exit "
      "there is not a cost-sized loss",
      pos.stop - entry >= fp._cost_points("NIFTY", entry, entry, pos.lot_size,
                                          pos.lots) * 0.99,
      f"gap {pos.stop - entry:.2f} pts")
check("R is still measured from the ENTRY stop after the ratchet",
      abs(pos.risk_pts - risk) < 1e-9, f"risk {pos.risk_pts} vs {risk}")

# Stepped down tick by tick: a paper fill is taken at the observed price, so a
# single jump straight through the ratchet would (correctly) fill at the jump
# price. The point being asserted is that the trade stops at the ratchet on the
# way down instead of continuing to the original stop.
ratchet = fp.store.get("NIFTY").stop
sig = None
ts = 1_700_100_180
for mult in (0.4, 0.2, 0.05, -0.2, -0.9):
    ts += 60
    sig = fp.tick("NIFTY", UP, entry + mult * risk, 40.0, MarketStatus.TRENDING, None,
                  None, 200.0, ts)
    if sig.state == "CLOSED":
        break
check("a reversal that would have hit the ORIGINAL stop now exits on the ratchet",
      sig.state == "CLOSED" and bool(sig.price and sig.price >= ratchet - 0.2 * risk),
      f"{sig.reason} at {sig.price} (ratchet {ratchet:.1f}, original stop {hard_stop})")
# The ratchet is a cost-adjusted scratch, not a profit target: filling on it nets
# ~zero, and a tick that steps slightly past it nets a small negative. What the
# stage buys is the difference between that and the full R the trade used to lose.
check("the exit is a scratch, not the full-R loss it used to be",
      bool(sig.net_points is not None and sig.net_points > -0.2 * risk),
      f"{sig.net_points} pts net vs a {risk:.0f}-pt R")

# ---- 2. same for a SHORT ----------------------------------------------------
reset()
sig = fp.tick("CRUDEOIL", DOWN, DOWN[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_110_000)
if sig.state == "ENTERED" and sig.side == "SHORT":
    pos = fp.store.get("CRUDEOIL")
    entry, risk = pos.entry, pos.risk_pts
    fp.tick("CRUDEOIL", DOWN, entry - 0.7 * risk, 40.0, MarketStatus.TRENDING, None,
            None, 200.0, 1_700_110_060)
    pos = fp.store.get("CRUDEOIL")
    check("SHORT arms symmetrically and ratchets the stop DOWN, below entry",
          pos.armed and pos.stop < entry, f"entry {entry} stop {pos.stop}")
    ts = 1_700_110_120
    sig = None
    for mult in (0.4, 0.2, 0.05, -0.2, -0.9):
        ts += 60
        sig = fp.tick("CRUDEOIL", DOWN, entry - mult * risk, 40.0, MarketStatus.TRENDING,
                      None, None, 200.0, ts)
        if sig.state == "CLOSED":
            break
    check("SHORT exits at a scratch on the ratchet instead of the original stop",
          sig.state == "CLOSED"
          and bool(sig.net_points is not None and sig.net_points > -0.2 * risk),
          f"{sig.reason} vs a {risk:.0f}-pt R")
else:
    check("SHORT entry available for the symmetry check", False, sig.reason)

# ---- 3. the ratchet alone never closes a trade that is still in profit ------
# It is a floor, not an exit: this is why it cannot clip a winner, and why the
# giveback exit (which can) is a separate switch.
pos, risk = open_long(1_700_120_000)
entry = pos.entry
ts = 1_700_120_000
for mult in (0.6, 0.8, 0.95):
    ts += 60
    sig = fp.tick("NIFTY", UP, entry + mult * risk, 40.0, MarketStatus.TRENDING, None,
                  None, 200.0, ts)
    check(f"still holding at +{mult:.2f}R with the ratchet armed",
          sig.state == "HOLDING", sig.reason)

# ---- 4. the giveback exit fires only when switched on -----------------------
settings.futures_pre_t1_giveback_exit = True
settings.futures_pre_t1_giveback_pct = 40.0
pos, risk = open_long(1_700_130_000)
entry = pos.entry
fp.tick("NIFTY", UP, entry + 0.9 * risk, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_130_060)
sig = fp.tick("NIFTY", UP, entry + 0.4 * risk, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_130_120)
check("giveback exit closes on a peak pullback before T1 when enabled",
      sig.state == "CLOSED" and "PRE-T1" in (sig.reason or ""), sig.reason)
check("the giveback exit books a profit, not a scratch loss",
      bool(sig.net_points is not None and sig.net_points > 0), f"{sig.net_points} pts")

settings.futures_pre_t1_giveback_exit = False
pos, risk = open_long(1_700_140_000)
entry = pos.entry
fp.tick("NIFTY", UP, entry + 0.9 * risk, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_140_060)
sig = fp.tick("NIFTY", UP, entry + 0.4 * risk, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_140_120)
check("with the giveback exit off, the same pullback keeps the trade alive "
      "(default: the recorded book had more winner value above the arm level "
      "than loss the exit could avoid)",
      sig.state == "HOLDING", sig.reason)

# ---- 5. the whole stage can be switched off --------------------------------
settings.futures_pre_t1_trail = False
pos, risk = open_long(1_700_150_000)
entry, hard_stop = pos.entry, pos.stop
fp.tick("NIFTY", UP, entry + 0.9 * risk, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_150_060)
pos = fp.store.get("NIFTY")
check("pre-T1 protection off -> nothing arms and the stop is unchanged",
      not pos.armed and pos.stop == hard_stop, f"stop {pos.stop}")
settings.futures_pre_t1_trail = True

# ---- 6. T1/T2/T3 locks still work exactly as before ------------------------
pos, risk = open_long(1_700_160_000)
entry, t1, t2 = pos.entry, pos.t1, pos.t2
sig = fp.tick("NIFTY", UP, t1 + 0.1, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_160_060)
check("T1 still locks a floor above entry", sig.state == "HOLDING"
      and bool(sig.locked_floor and sig.locked_floor > entry), f"{sig.locked_floor}")
sig = fp.tick("NIFTY", UP, t2 + 0.1, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_160_120)
check("T2 still moves the floor up to T2",
      bool(sig.locked_floor and sig.locked_floor >= t2 - 0.01), f"{sig.locked_floor}")

# ---- 7. armed state, R and the path survive a restart ----------------------
pos, risk = open_long(1_700_170_000)
entry = pos.entry
fp.tick("NIFTY", UP, entry + 0.7 * risk, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_170_060)
before = fp.store.get("NIFTY")
armed, saved_risk, saved_stop, samples = (
    before.armed, before.risk_pts, before.stop, len(before.path)
)
fp.store._loaded = False  # noqa: SLF001 - simulate a process restart
fp.store._open = {}  # noqa: SLF001
after = fp.store.get("NIFTY")
check("the open position is reloaded after a restart", after is not None)
check("the armed flag survives the restart (otherwise the ratchet re-arms from "
      "scratch and the protection silently disappears)",
      bool(after and after.armed == armed and armed))
check("the ratcheted stop and the ORIGINAL R both survive the restart",
      bool(after and abs(after.risk_pts - saved_risk) < 1e-9
           and abs(after.stop - saved_stop) < 1e-9),
      f"risk {after.risk_pts} stop {after.stop}")
check("the recorded path survives the restart",
      bool(after and len(after.path) == samples and samples > 0), f"{samples} samples")

# ---- 8. the closed record carries what a counterfactual needs --------------
sig = fp.tick("NIFTY", UP, after.t3 + 0.5, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_170_120)
rows = fp.read_log()
last = rows[-1]
check("closed record stores the tick path", isinstance(last.get("path"), list)
      and len(last["path"]) > 0, f"{len(last.get('path') or [])} samples")
check("closed record stores MAE next to MFE",
      last.get("max_adverse_points") is not None
      and last.get("max_favourable_points") is not None,
      f"MFE {last.get('max_favourable_points')} MAE {last.get('max_adverse_points')}")
check("closed record records whether the pre-T1 protection armed",
      "pre_t1_armed" in last, f"{last.get('pre_t1_armed')}")

# ---- 9. a target inside its own round-trip cost is refused ----------------
# The measured NATURALGAS plan: T1 0.2 pts away, ~2.1 pts of cost. Hitting it
# still booked -Rs2,580, so the plan is refused rather than the target moved.
settings.futures_min_t1_cost_multiple = 100000.0  # force every plan to fail the gate
reset()
sig = fp.tick("NIFTY", UP, UP[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_180_000)
check("a plan whose T1 is inside the cost multiple is BLOCKED, not entered",
      sig.state == "BLOCKED" and fp.store.get("NIFTY") is None, sig.reason)
check("the refusal says so in rupees-and-points terms",
      "round-trip cost" in (sig.reason or ""), sig.reason)
settings.futures_min_t1_cost_multiple = 2.0
reset()
sig = fp.tick("NIFTY", UP, UP[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_190_000)
check("a normal 1R target passes the gate at the shipped 2x setting",
      sig.state == "ENTERED", sig.reason)

check("log file written", os.path.exists(os.path.join(settings.data_dir,
                                                     "futures_paper.jsonl")))

print()
print(f"{len(FAILS)} failure(s)" if FAILS else "ALL FUTURES PRE-T1 CHECKS PASSED")
for f in FAILS:
    print("  -", f)
raise SystemExit(1 if FAILS else 0)
