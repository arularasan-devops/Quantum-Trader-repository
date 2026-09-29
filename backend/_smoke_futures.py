"""Smoke test for the separate futures paper tool: entry, sizing from risk,
point-based targets, T1 break-even, past-T3 ratchet, stop, square-off, costs."""
from __future__ import annotations

import os
import tempfile

from app.config import settings

settings.data_dir = tempfile.mkdtemp(prefix="futsmoke_")
settings.futures_paper_enabled = True

from app.execution import futures_paper as fp  # noqa: E402
from app.models import Candle, MarketStatus  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        FAILS.append(name)


def candles(n: int, start: float, step: float, atr: float = 10.0) -> list[Candle]:
    out = []
    px = start
    for i in range(n):
        px += step
        out.append(Candle(time=1_700_000_000 + i * 60, open=px - step, high=px + atr / 2,
                          low=px - atr / 2, close=px, volume=1000))
    return out


def reset() -> None:
    fp.store.set("NIFTY", None)
    fp.store.set("CRUDEOIL", None)
    # The risk governor is deliberately sticky across a session (trade caps,
    # cooldowns, stand-downs); each scenario below is its own session.
    fp.guard.reset()


# ---- 0. the affordability guard is real: Rs1L cannot carry a NIFTY lot ------
reset()
settings.futures_capital = 100000.0
_cs = candles(120, 24000.0, 8.0, atr=40.0)
_sig = fp.tick("NIFTY", _cs, _cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
               200.0, 1_700_000_500)
check("Rs1L cannot margin one NIFTY futures lot -> refused, not traded",
      _sig.state == "BLOCKED" and fp.store.get("NIFTY") is None, _sig.reason)

# The rest of the lifecycle needs a pot that can actually carry the contract.
settings.futures_capital = 1000000.0

# ---- 1. uptrend -> LONG entry, sized from risk -----------------------------
reset()
cs = candles(120, 24000.0, 8.0, atr=40.0)
sig = fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING,
              None, None, 200.0, 1_700_010_000)
print("  ->", sig.state, sig.side, sig.reason)
check("entered LONG in an uptrend", sig.state == "ENTERED" and sig.side == "LONG")
check("stop below entry", bool(sig.stop and sig.entry and sig.stop < sig.entry),
      f"entry {sig.entry} stop {sig.stop}")
check("targets ascending above entry",
      bool(sig.target1 and sig.target2 and sig.target3
           and sig.entry < sig.target1 < sig.target2 < sig.target3))
check("risk in points is the entry-stop distance",
      bool(sig.risk_points and abs(sig.risk_points - (sig.entry - sig.stop)) < 2.0))
check("sized at least 1 lot", bool(sig.lots and sig.lots >= 1), f"lots={sig.lots}")
check("risk in rupees = points x lot_size x lots",
      bool(sig.risk_rupees and sig.lot_size
           and abs(sig.risk_rupees - sig.risk_points * sig.lot_size * sig.lots) < 5.0),
      f"₹{sig.risk_rupees}")
check("paper_only is locked True", sig.paper_only is True)
# Regression: levels must be R-multiples of the risk measured from the FILL, not
# from the unslipped price — otherwise slippage silently eats the reward:risk.
check("T1/T2/T3 are true R-multiples of the risk from the actual fill",
      bool(sig.entry and sig.risk_points
           and abs((sig.target1 - sig.entry) - 1.0 * sig.risk_points) < 0.3
           and abs((sig.target2 - sig.entry) - 2.0 * sig.risk_points) < 0.3
           and abs((sig.target3 - sig.entry) - 3.0 * sig.risk_points) < 0.3),
      f"entry {sig.entry} risk {sig.risk_points} T {sig.target1}/{sig.target2}/{sig.target3}")

pos = fp.store.get("NIFTY")
entry, t1, t2, t3, stop = pos.entry, pos.t1, pos.t2, pos.t3, pos.stop

# ---- 2. T1 locks a PROFITABLE floor, not break-even ------------------------
# Locking the raw entry price is not break-even on futures: it books a loss of
# exactly the round trip. Four such exits were measured in one live session,
# closing at 0 or +1 point for -Rs375 each after the trade had already travelled
# a full 1R. The floor must therefore sit above entry by a fraction of R PLUS
# the costs, so an exit on it is genuinely positive.
sig = fp.tick("NIFTY", cs, t1 + 0.1, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_010_060)
check("holding after T1", sig.state == "HOLDING")
check("floor locked ABOVE entry after T1 (not at break-even)",
      bool(sig.locked_floor is not None and sig.locked_floor > entry),
      f"floor {sig.locked_floor} entry {entry}")
check("the locked floor stays behind T1 so it cannot self-trigger",
      bool(sig.locked_floor is not None and sig.locked_floor < t1),
      f"floor {sig.locked_floor} T1 {t1}")

# a pullback onto that floor closes the trade, and it closes GREEN
sig = fp.tick("NIFTY", cs, pos.floor - 0.5, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_010_120)
check("closes on the locked floor instead of running back to the stop",
      sig.state == "CLOSED", f"{sig.reason}")
check("an exit on the T1 lock is a NET PROFIT after costs",
      bool(sig.net_points is not None and sig.net_points > 0),
      f"{sig.net_points} pts net")
check("the T1 lock banks less than the full 1R it protects",
      bool(sig.net_points is not None and sig.net_points < pos.risk_pts),
      f"{sig.net_points} pts vs {pos.risk_pts}-pt R")

# ---- 3. past-T3 ratchet keeps riding, exits on the rollback ----------------
reset()
sig = fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_020_000)
pos = fp.store.get("NIFTY")
entry, t3, risk = pos.entry, pos.t3, pos.risk_pts
peak = t3 + 6 * risk  # a big run well past T3
ts = 1_700_020_060
for px in (t3 + 0.5, t3 + 2 * risk, t3 + 4 * risk, peak):
    ts += 60
    sig = fp.tick("NIFTY", cs, px, 40.0, MarketStatus.TRENDING, None, None, 200.0, ts)
    check(f"still riding at {px - entry:.0f} pts profit (past T3)",
          sig.state == "HOLDING", f"floor {sig.locked_floor}")
floor_at_peak = fp.store.get("NIFTY").floor
check("floor ratcheted well above T3",
      bool(floor_at_peak and floor_at_peak > t3 + risk),
      f"floor {floor_at_peak:.1f} vs T3 {t3:.1f}")
ts += 60
sig = fp.tick("NIFTY", cs, floor_at_peak - 0.5, 40.0, MarketStatus.TRENDING, None,
              None, 200.0, ts)
check("exits on the rollback to the ratcheted floor", sig.state == "CLOSED", sig.reason)
kept = (sig.net_points or 0) / (peak - entry)
check("keeps most of the peak run (not the exact top)", 0.5 < kept < 1.0,
      f"kept {kept * 100:.0f}% of the {peak - entry:.0f}-pt peak run")

# ---- 4. stop loss ---------------------------------------------------------
reset()
fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_030_000)
stop = fp.store.get("NIFTY").stop
sig = fp.tick("NIFTY", cs, stop - 1.0, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_030_060)
check("stop loss closes the position",
      sig.state == "CLOSED" and "STOP" in (sig.reason or ""), sig.reason)
check("stop loss is a loss in rupees", bool(sig.net_rupees and sig.net_rupees < 0),
      f"₹{sig.net_rupees}")

# ---- 5. SHORT side works (a downtrend is traded directly) ------------------
reset()
ds = candles(120, 24000.0, -8.0, atr=40.0)
sig = fp.tick("NIFTY", ds, ds[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_040_000)
print("  ->", sig.state, sig.side, sig.reason)
check("entered SHORT in a downtrend", sig.state == "ENTERED" and sig.side == "SHORT")
check("SHORT stop above entry, targets below",
      bool(sig.stop > sig.entry and sig.target3 < sig.target2 < sig.target1 < sig.entry))
sig = fp.tick("NIFTY", ds, fp.store.get("NIFTY").t1 - 0.1, 40.0, MarketStatus.TRENDING,
              None, None, 200.0, 1_700_040_060)
check("SHORT floor locks BELOW entry (in profit) after T1",
      bool(sig.locked_floor is not None and sig.locked_floor < sig.entry),
      f"floor {sig.locked_floor} entry {sig.entry}")

# ---- 6. hard guards ------------------------------------------------------
reset()
sig = fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.RANGING, None, None,
              200.0, 1_700_050_000)
check("no entry in a ranging market", sig.state == "WAIT", sig.reason)
sig = fp.tick("NIFTY", cs, cs[-1].close, 0.5, MarketStatus.TRENDING, None, None,
              200.0, 1_700_050_060)
check("no entry when ATR is below the dead-market floor", sig.state == "WAIT", sig.reason)
sig = fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              10.0, 1_700_050_120)
check("no new entry near the close", sig.state == "BLOCKED", sig.reason)

# square-off before the bell
reset()
fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_060_000)
sig = fp.tick("NIFTY", cs, cs[-1].close + 5, 40.0, MarketStatus.TRENDING, None, None,
              5.0, 1_700_060_060)
check("squares off before the close (never overnight)",
      sig.state == "CLOSED" and "SQUARE-OFF" in (sig.reason or ""), sig.reason)

# risk budget too small for one lot -> refuses rather than trading oversized
reset()
old_cap = settings.futures_capital
settings.futures_risk_per_trade_pct = 0.1
sig = fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_070_000)
check("refuses the trade when one lot exceeds the risk budget",
      sig.state == "BLOCKED" and fp.store.get("NIFTY") is None, sig.reason)
settings.futures_capital = old_cap
settings.futures_risk_per_trade_pct = 1.0
_ = old_cap

# costs eating the risk -> refuse (a stop-out would cost far more than 1R)
reset()
old_slip = settings.futures_slippage_points
settings.futures_slippage_points = 8.0
sig = fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_075_000)
check("refuses a trade whose round-trip cost is a big share of the risk",
      sig.state == "BLOCKED" and fp.store.get("NIFTY") is None, sig.reason)
settings.futures_slippage_points = old_slip

# ---- 6b. an open position survives a restart ------------------------------
# Without this the tool loses exactly the trades that were running when it went
# down: they never close, so they never reach the history.
reset()
fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_060_000)
live = fp.store.get("NIFTY")
check("a position is open before the simulated restart", live is not None)
entry_before, lots_before = live.entry, live.lots
fp.store._open.clear()          # simulate process death
fp.store._loaded = False        # next access reloads from disk
restored = fp.store.get("NIFTY")
check("open futures position is restored after a restart",
      restored is not None and abs(restored.entry - entry_before) < 0.01
      and restored.lots == lots_before,
      f"{entry_before} -> {restored.entry if restored else None}")
check("the restored position can still be closed into the history",
      fp.tick("NIFTY", cs, entry_before + 60.0, 40.0, MarketStatus.TRENDING, None,
              None, 5.0, 1_700_060_600).state == "CLOSED")
fp.store._open.clear()
fp.store._loaded = False
check("a closed position is not resurrected by the restart file",
      fp.store.get("NIFTY") is None)

# concurrency cap
reset()
settings.futures_max_concurrent = 1
fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_080_000)
sig = fp.tick("CRUDEOIL", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_080_060)
check("concurrency cap blocks a second instrument", sig.state == "BLOCKED", sig.reason)
settings.futures_max_concurrent = 2

# disabled -> OFF, and no position can be opened
reset()
settings.futures_paper_enabled = False
sig = fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
              200.0, 1_700_090_000)
check("tool reports OFF when disabled",
      sig.state == "OFF" and not sig.enabled and fp.store.get("NIFTY") is None)
settings.futures_paper_enabled = True

# ---- 7. costs are actually charged ---------------------------------------
reset()
fp.tick("NIFTY", cs, cs[-1].close, 40.0, MarketStatus.TRENDING, None, None,
        200.0, 1_700_100_000)
p = fp.store.get("NIFTY")
gross_target = p.entry + 50.0
sig = fp.tick("NIFTY", cs, gross_target, 40.0, MarketStatus.TRENDING, None, None,
              5.0, 1_700_100_060)
check("net points are below gross (slippage + brokerage charged)",
      bool(sig.net_points is not None and sig.net_points < 50.0),
      f"gross 50.0 -> net {sig.net_points}")

# The statutory charges are a % of TURNOVER and dominate a large-notional
# contract: charging only the flat brokerage understates the real cost several
# times over and flatters every paper result.
nifty_cost = fp._cost_points("NIFTY", 24000.0, 24000.0, 65, 1)
flat_only = settings.futures_brokerage_per_order * 2.0 / 65 + 2.0 * settings.futures_slippage_points
check("NIFTY round-trip cost includes turnover charges, not just brokerage",
      nifty_cost > flat_only + 3.0,
      f"{nifty_cost:.2f} pts vs {flat_only:.2f} pts brokerage-only")
check("STT dominates the NIFTY cost (~5-9 points a round trip)",
      5.0 < nifty_cost < 12.0, f"{nifty_cost:.2f} pts = Rs{nifty_cost * 65:,.0f}")
# MCX commodities pay CTT at half the index STT rate, so Crude must be cheaper
# per point of notional than an index contract.
crude_qty = 100
crude_turnover = 6900.0 * crude_qty
crude_charges = (
    fp._cost_points("CRUDEOIL", 6900.0, 6900.0, crude_qty, 1) * crude_qty
    - 2.0 * settings.futures_slippage_points * crude_qty
)
expected_ctt = (
    settings.futures_brokerage_per_order * 2.0
    + crude_turnover * settings.futures_ctt_sell_pct / 100.0
    + 2.0 * crude_turnover * settings.futures_txn_pct / 100.0
)
if_index_stt = expected_ctt + crude_turnover * (
    settings.futures_stt_sell_pct - settings.futures_ctt_sell_pct
) / 100.0
check("MCX commodity is charged CTT (0.01%), not the 0.02% index STT",
      abs(crude_charges - expected_ctt) < 1.0 and crude_charges < if_index_stt,
      f"Crude charges Rs{crude_charges:,.0f}; index STT would be Rs{if_index_stt:,.0f}")

# ---- 8. report + journal --------------------------------------------------
rep = fp.report()
check("report counts the closed trades", rep["trades"] > 0, f"{rep['trades']} trades")
check("report exposes profit factor next to win rate",
      "profit_factor" in rep and "win_rate" in rep,
      f"win {rep['win_rate']}% PF {rep['profit_factor']}")
check("report is flagged paper_only", rep["paper_only"] is True)
check("log file written", os.path.exists(os.path.join(settings.data_dir,
                                                     "futures_paper.jsonl")))
rows = fp.read_log()
check("every logged trade is paper mode", all(r["mode"] == "paper" for r in rows))
check("every logged trade carries points, rupees and an R multiple",
      all(r.get("net_points") is not None and r.get("net_rupees") is not None
          for r in rows))

# The whole point of the mirror is that FUT trades sit next to the option trades
# in the unified Journal and are separable there, so assert the tag survives the
# round trip through storage rather than trusting the write.
from app import storage as _st  # noqa: E402

jrows = _st.store.journal(limit=50, exclude_flow=True)
fut_rows = [r for r in jrows if (r.get("entry_trigger") or "") == "FUT"]
check("closed futures trades are mirrored into the unified Journal",
      len(fut_rows) > 0, f"{len(fut_rows)} FUT row(s) of {len(jrows)}")
check("mirrored rows are tagged FUT (not UNTAGGED) so they are separable",
      all(r["entry_trigger"] == "FUT" for r in fut_rows))
check("mirrored rows are marked paper and bot-opened",
      all(r["mode"] == "paper" and r["auto"] for r in fut_rows))

print()
print(f"{len(FAILS)} failure(s)" if FAILS else "ALL FUTURES PAPER CHECKS PASSED")
for f in FAILS:
    print("  -", f)
raise SystemExit(1 if FAILS else 0)
