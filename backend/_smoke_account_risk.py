"""Smoke: the account-wide risk governor, the opening-window block, the
futures percentage ATR gate, futures lot sizing and the journal capital columns.

Every scenario here is a replay of something that actually happened on 13 Aug,
so a regression shows up as the same rupee figure going wrong again.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Point the data dir at a scratch folder BEFORE anything loads persisted state,
# so a smoke run can never touch the real journal or guard files.
_TMP = tempfile.mkdtemp(prefix="qt-smoke-risk-")
os.environ["QT_DATA_DIR"] = _TMP

from app.config import settings  # noqa: E402

settings.data_dir = _TMP

from app.analysis import indicators as ind  # noqa: E402
from app.engine import account_risk as ar  # noqa: E402
from app.execution import futures_paper as fp  # noqa: E402
from app.models import Candle, MarketStatus  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


# --- 1. the cap is ACCOUNT-WIDE ------------------------------------------
# 13 Aug walked -992 (SENSEX), -1,960 (INFY), -1,067 (NIFTY), -2,570 (CRUDE).
# Per-instrument counters never reached the Rs5,000 cap; the account did, at the
# fourth trade, and the fourteen trades after that point lost Rs10,492.
acct = ar.account
acct.reset()
settings.max_daily_loss = 5000.0
settings.max_trades_per_day = 10
settings.max_consecutive_losses = 99  # isolate the loss cap from the streak rule
now = 1_786_600_000.0

for inst, pnl in (("SENSEX", -992.0), ("INFY", -1960.0), ("NIFTY", -1067.0)):
    acct.record_entry(inst, now)
    acct.record_exit(inst, pnl, now, stopped=True)
check(
    "three losses on three different instruments do not trip the cap",
    acct.blocked(now) is None,
    f"day P&L Rs{acct.day_pnl:,.0f}",
)
acct.record_entry("CRUDEOIL", now)
acct.record_exit("CRUDEOIL", -2570.0, now, stopped=True)
blocked = acct.blocked(now)
check(
    "the fourth loss crosses the account cap and halts the book",
    blocked is not None and "daily loss cap" in blocked,
    str(blocked),
)
check(
    "the halt is measured on the pooled P&L, not one instrument",
    abs(acct.day_pnl - -6589.0) < 0.01,
    f"Rs{acct.day_pnl:,.0f}",
)

# --- 2. it survives a restart -------------------------------------------
# The old counters were in memory only, so deploying a build mid-session handed
# the bot a fresh Rs5,000 to lose on every instrument.
reloaded = ar.AccountRisk()
still_blocked = reloaded.blocked(now)  # reads the persisted file on first use
check(
    "the day's loss survives a restart",
    abs(reloaded.day_pnl - acct.day_pnl) < 0.01 and still_blocked is not None,
    f"reloaded Rs{reloaded.day_pnl:,.0f} — {still_blocked}",
)

# --- 3. consecutive-loss stand-down ------------------------------------
acct.reset()
settings.max_daily_loss = 100000.0
settings.max_consecutive_losses = 3
settings.risk_stand_down_sec = 3600.0
for _ in range(3):
    acct.record_exit("CRUDEOIL", -1000.0, now, stopped=True)
blocked = acct.blocked(now)
check("3 losses in a row stand the book down", blocked is not None and "row" in blocked, str(blocked))
check(
    "the stand-down expires rather than killing the session",
    acct.blocked(now + 3601) is None,
)
acct.record_exit("CRUDEOIL", 500.0, now + 3601)
check("a win clears the streak", acct.consecutive_losses == 0)

# --- 4. per-instrument cooldown, longer after a stop -------------------
acct.reset()
settings.auto_trade_reentry_cooldown_sec = 900.0
settings.auto_trade_post_stop_cooldown_sec = 1800.0
acct.record_exit("CRUDEOIL", 500.0, now)          # a target exit
acct.record_exit("NIFTY", -500.0, now, stopped=True)  # a stop
check(
    "a target exit is cooled for the shorter window",
    acct.instrument_blocked("CRUDEOIL", now + 100) is not None
    and acct.instrument_blocked("CRUDEOIL", now + 901) is None,
)
check(
    "a stop-out is cooled for longer than a target",
    acct.instrument_blocked("NIFTY", now + 901) is not None
    and acct.instrument_blocked("NIFTY", now + 1801) is None,
    str(acct.instrument_blocked("NIFTY", now + 901)),
)
check(
    "the cooldown is per instrument, not global",
    acct.instrument_blocked("BANKNIFTY", now + 10) is None,
)

# --- 5. opening-window block ------------------------------------------
# 09:04-09:17 IST entries exited at -47.8% / -22.4% / -16.7% against an 8% stop.
settings.auto_trade_no_entry_open_minutes = 15.0
IST = 19800


def ist_ts(hh: int, mm: int) -> float:
    """A UTC timestamp whose IST wall clock reads hh:mm."""
    base = 1_786_600_000
    midnight_utc = base - ((base + IST) % 86400)  # UTC instant of IST midnight
    return midnight_utc + hh * 3600 + mm * 60


nfo_open = ar.session_open_min("NFO")
mcx_open = ar.session_open_min("MCX")
check("the equity bell is 09:15, not 09:00", nfo_open == 9 * 60 + 15, str(nfo_open))
check("the commodity bell is 09:00", mcx_open == 9 * 60, str(mcx_open))
check(
    "an index option is refused at 09:20",
    acct.opening_window_block(ist_ts(9, 20), nfo_open) is not None,
)
check(
    "the same instrument is allowed at 09:31",
    acct.opening_window_block(ist_ts(9, 31), nfo_open) is None,
)
check(
    "a commodity is allowed at 09:20 — its bell was 09:00",
    acct.opening_window_block(ist_ts(9, 20), mcx_open) is None,
)
check(
    "a commodity is refused at 09:05",
    acct.opening_window_block(ist_ts(9, 5), mcx_open) is not None,
)

# --- 6. the futures ATR gate is a % of price, not points ---------------
# 5 absolute points is 0.06% of a Crude contract but several times a 1-minute ATR
# on a mid-priced stock future, which is why only Crude ever traded. The same
# uptrend is fed in at two price scales; both must be judged the same way.
settings.futures_min_atr_pct = 0.03
settings.futures_min_atr_points = 0.0


def uptrend(base: float, step_frac: float, n: int = 90) -> list[Candle]:
    """A clean uptrend at an arbitrary price scale, in fractions of price."""
    out: list[Candle] = []
    px = base
    for i in range(n):
        step = px * step_frac
        o = px
        c = px + step
        out.append(
            Candle(
                time=1_786_600_000 + i * 60,
                open=o,
                high=c + step * 0.2,
                low=o - step * 0.2,
                close=c,
                volume=10000.0,
            )
        )
        px = c
    return out


crude = uptrend(7900.0, 0.0006)
stock = uptrend(800.0, 0.0006)
crude_atr = ind.atr(*fp._arrays(crude), 14) or 0.0
stock_atr = ind.atr(*fp._arrays(stock), 14) or 0.0
side_c, why_c = fp._entry_setup(crude, crude[-1].close, crude_atr, MarketStatus.TRENDING)
side_s, why_s = fp._entry_setup(stock, stock[-1].close, stock_atr, MarketStatus.TRENDING)
check(
    "Crude clears the volatility gate",
    "too quiet" not in why_c,
    f"ATR {crude_atr:.2f} — {why_c}",
)
check(
    "the SAME trend on a Rs800 stock future clears it too",
    "too quiet" not in why_s,
    f"ATR {stock_atr:.2f} — {why_s}",
)
check(
    "and both are judged identically — the gate is now scale-invariant",
    why_c == why_s and side_c == side_s,
    f"crude: {why_c} | stock: {why_s}",
)
check(
    "and the old absolute gate is what used to refuse it",
    stock_atr < 5.0,
    f"stock ATR {stock_atr:.2f} vs the old 5-pt minimum",
)
# A genuinely dead book must still be refused, at either price scale.
flat = uptrend(800.0, 0.000005)
flat_atr = ind.atr(*fp._arrays(flat), 14) or 0.0
side_f, why_f = fp._entry_setup(flat, flat[-1].close, flat_atr, MarketStatus.TRENDING)
check(
    "a dead book is still refused as too quiet",
    side_f is None and "too quiet" in why_f,
    f"ATR {flat_atr:.4f} — {why_f}",
)
# The legacy absolute override still works when deliberately set.
settings.futures_min_atr_points = 5.0
side_o, why_o = fp._entry_setup(stock, stock[-1].close, stock_atr, MarketStatus.TRENDING)
check(
    "the legacy absolute override still applies when set above zero",
    side_o is None and "pt minimum" in why_o,
    why_o,
)
settings.futures_min_atr_points = 0.0

# --- 7. futures lot sizing: risk binds, margin binds, cap binds --------
settings.futures_capital = 500000.0
settings.futures_risk_per_trade_pct = 1.0
settings.futures_margin_pct = 12.0
settings.futures_paper_lots = 5
settings.futures_size_by_margin = False
fp.store.set("CRUDEOIL", None)
lots, note = fp._size_lots(7900.0, 20.0, 100)
check(
    "the 1% budget buys 2 Crude lots at a 20-point stop, not the old 1",
    lots == 2,
    f"{lots} lot(s) — {note}",
)
check("the note states the rupee risk and the margin", "risking" in note and "margin" in note, note)
settings.futures_size_by_margin = True
lots_m, note_m = fp._size_lots(7900.0, 20.0, 100)
check(
    "margin-max sizing takes more lots than the risk rule allows",
    lots_m > 2,
    f"{lots_m} lot(s) — {note_m}",
)
check(
    "and it says so: the implied risk is above the 1% budget",
    "% of" in note_m,
    note_m,
)
settings.futures_size_by_margin = False
settings.futures_paper_lots = 1
lots_c, _ = fp._size_lots(7900.0, 20.0, 100)
check("the hard cap still binds when set", lots_c == 1, str(lots_c))
settings.futures_paper_lots = 5
big, note_big = fp._size_lots(7900.0, 5000.0, 100)
check("a stop too wide for the budget is refused outright", big == 0, note_big)

# --- 8. journal capital columns ---------------------------------------
pos = fp._Position(
    instrument="CRUDEOIL",
    side="LONG",
    entry=7846.0,
    lots=2,
    stop=7826.0,
    t1=7866.0,
    t2=7886.0,
    t3=7906.0,
    opened=int(now),
    entry_reason="TEST",
)
pos.risk_pts = 20.0
LOT = pos.lot_size
QTY = LOT * pos.lots
rec = fp._close(pos, 7866.0, "TARGET 1 lock", int(now) + 600)
expect_margin = 7846.0 * QTY * 12.0 / 100.0
check(
    "capital used is the MARGIN blocked, not the notional",
    abs(float(rec["capital_used"]) - round(expect_margin, 0)) < 1.0,
    f"Rs{rec['capital_used']:,.0f} margin vs Rs{rec['notional_rupees']:,.0f} notional",
)
check(
    "the notional is recorded separately",
    float(rec["notional_rupees"]) > float(rec["capital_used"]) * 5,
)
check(
    "risk at entry is in rupees",
    abs(float(rec["risk_rupees"]) - 20.0 * QTY) < 1.0,
    f"Rs{rec['risk_rupees']:,.0f}",
)
check("costs are recorded in rupees", float(rec["cost_rupees"]) > 0, f"Rs{rec['cost_rupees']:,.0f}")
roc = float(rec["return_on_capital_pct"])
check(
    "return is expressed against the capital actually used",
    abs(roc - float(rec["net_rupees"]) / expect_margin * 100.0) < 0.05,
    f"{roc:.2f}% on Rs{expect_margin:,.0f}",
)

# --- 9. the post-T1 floor trails instead of sitting at the T1 lock -----
# All three winners on 13 Aug exited on the T1 lock at ~0.5R however far they ran.
settings.futures_post_t1_trail_r = 0.5
settings.futures_t1_lock_r = 0.5
pos2 = fp._Position(
    instrument="CRUDEOIL",
    side="LONG",
    entry=7800.0,
    lots=1,
    stop=7780.0,
    t1=7820.0,
    t2=7840.0,
    t3=7860.0,
    opened=int(now),
    entry_reason="TEST",
)
pos2.risk_pts = 20.0
fp._manage(pos2, 7822.0, 300.0, int(now))       # T1 touched
floor_at_t1 = pos2.floor
fp._manage(pos2, 7835.0, 300.0, int(now) + 60)  # runs to 1.75R
floor_after_run = pos2.floor
check(
    "the T1 lock starts above the entry, cost-aware",
    floor_at_t1 is not None and floor_at_t1 > pos2.entry,
    f"floor {floor_at_t1}",
)
check(
    "the floor RATCHETS UP as the trade runs past T1",
    floor_after_run is not None and floor_after_run > floor_at_t1,
    f"{floor_at_t1} -> {floor_after_run}",
)
check(
    "so a 1.75R run banks more than the old flat 0.5R lock",
    floor_after_run - pos2.entry > 0.5 * pos2.risk_pts,
    f"+{floor_after_run - pos2.entry:.1f} pts vs +{0.5 * pos2.risk_pts:.1f} pts",
)

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: {FAILED}")
    sys.exit(1)
print("ALL ACCOUNT-RISK / FUTURES-SIZING CHECKS PASSED")
