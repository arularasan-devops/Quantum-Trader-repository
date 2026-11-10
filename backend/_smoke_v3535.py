"""Smoke v3.5.35: SELL / STRADDLE research books, point stops in books, 1-lot pre-trail
skip. SQLite temp DB; no provider, no broker."""
import os
import tempfile
import time
from types import SimpleNamespace as NS

os.environ.setdefault("QT_TRADE_MODE", "paper")
from app.config import settings  # noqa: E402
import app.engine.paper_books as pb  # noqa: E402
from app.models import Signal  # noqa: E402
from app.research.db import Database  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    fails += 0 if ok else 1


settings.data_provider = "simulated"
settings.ignore_market_hours = True
settings.paper_capital = 500000.0
settings.daily_profit_target = 10_000_000.0  # keep the day lock out of these checks
settings.daily_lock_steps = ""
settings.auto_trade_strategy_gate = False
settings.auto_trade_breakeven_after_arm = False
settings.auto_trade_stop_points = {}
pb.get_spec = lambda s: NS(lot_size=100)
url = f"sqlite:///{tempfile.mkdtemp()}/b.db"
B = pb.PaperBooks(Database(url), ["500000:SELL", "500000:STRADDLE", "500000:RISK"])
check("books parse", [b.id for b in B.books] == ["5L-SELL", "5L-STRADDLE", "5L-RISK"])
S, D, R = B.books
CE, PE = NS(value="CE"), NS(value="PE")


def chain(ce, pe):
    return [NS(symbol="X100CE", strike=100.0, option_type=CE, premium=ce, bid=ce - 0.1, ask=ce,
               ask_size=None),
            NS(symbol="X100PE", strike=100.0, option_type=PE, premium=pe, bid=pe - 0.1, ask=pe,
               ask_size=None)]


st = NS(instrument="X", _last_market_status="TRENDING", _feed_ready=lambda: True)
dec = NS(signal=Signal.BUY, recommended_option="X100CE", entry_trigger="SUPERTREND",
         confidence=90.0, stop_loss=45.0, target1=55.0, target2=60.0, target3=65.0,
         option_type=CE)
hold = NS(**{**dec.__dict__, "signal": Signal.HOLD})
now = time.time()
B.on_tick(st, dec, chain(50.0, 40.0), now)
s = next(iter(S.positions.values()))
check("SELL book shorts the opposite (PE) at its bid", s.option == "X100PE" and s.side == "SHORT"
      and abs(s.entry - 39.9) < 1e-9 and s.lots == 1, (s.option, s.side, s.entry))
row = B.db.query_one("SELECT * FROM jr_book_trades WHERE book='5L-SELL'")
check("SELL stop above entry, T1 below (real prices stored)",
      row["initial_stop"] > 39.9 and row["target1"] < 39.9 and row["side"] == "SHORT",
      (row["initial_stop"], row["target1"]))
check("SELL margin counted, not premium", abs(S.deployed() - 100 * 100 * 0.15) < 1e-6, S.deployed())
legs = sorted(p.option for p in D.positions.values())
check("STRADDLE buys CE and PE, 1 lot each", legs == ["X100CE", "X100PE"]
      and all(p.lots == 1 for p in D.positions.values()), legs)
check("RISK book unchanged (long CE)", "X" in R.positions and R.positions["X"].side == "LONG")

# restart restores the short in mirrored space
B2 = pb.PaperBooks(Database(url), ["500000:SELL", "500000:STRADDLE", "500000:RISK"])
s2 = next(iter(B2.books[0].positions.values()))
check("restart restores short stop/targets", abs(s2.stop - s.stop) < 1e-9 and abs(s2.t1 - s.t1) < 1e-9
      and s2.side == "SHORT", (s2.stop, s.stop))
check("restart restores both straddle legs", len(B2.books[1].positions) == 2)

# CE rallies, PE collapses: short PE profits; straddle PE leg stops, CE leg keeps running
B.on_tick(st, hold, chain(56.0, 30.0), now + 1)
pe_leg = B.db.query_one("SELECT * FROM jr_book_trades WHERE book='5L-STRADDLE' AND leg='PE'")
check("STRADDLE losing PE leg stopped", pe_leg["status"] == "CLOSED"
      and pe_leg["exit_reason"] in ("STOP LOSS",), (pe_leg["status"], pe_leg["exit_reason"]))
check("STRADDLE CE leg still open", any(p.leg == "CE" for p in D.positions.values()))
sp = next(iter(S.positions.values()), None)
check("SELL short still open (past T1 lock, rides)", sp is not None and sp.floor is not None,
      sp and sp.floor)
B.on_tick(st, hold, chain(56.0, 37.0), now + 2)
srow = B.db.query_one("SELECT * FROM jr_book_trades WHERE book='5L-SELL'")
check("SELL closes on the bounce, buy-back at the ask, gross = (entry - ask) x qty",
      srow["status"] == "CLOSED" and abs(srow["gross_pnl"] - (39.9 - 37.0) * 100) < 1e-6
      and srow["net_pnl"] < srow["gross_pnl"], (srow["status"], srow["gross_pnl"], srow["exit_reason"]))

# a short that rallies hits its stop with a loss
B3 = pb.PaperBooks(Database(f"sqlite:///{tempfile.mkdtemp()}/c.db"), ["500000:SELL"])
B3.on_tick(st, dec, chain(50.0, 40.0), now)
B3.on_tick(st, hold, chain(44.0, 47.0), now + 1)
r3 = B3.db.query_one("SELECT * FROM jr_book_trades")
check("SELL stop: premium up -> STOP LOSS with loss", r3["status"] == "CLOSED"
      and r3["exit_reason"] == "STOP LOSS" and r3["gross_pnl"] < 0, (r3["exit_reason"], r3["gross_pnl"]))

# point stop applied to book entries
settings.auto_trade_stop_points = {"X": 5.0}
B4 = pb.PaperBooks(Database(f"sqlite:///{tempfile.mkdtemp()}/d.db"), ["500000:FULL"])
B4.on_tick(st, dec, chain(50.0, 40.0), now)
check("point stop: FULL book stop = entry - 5", abs(B4.books[0].positions["X"].stop - 45.0) < 1e-9,
      B4.books[0].positions["X"].stop)
settings.auto_trade_stop_points = {}

# 1-lot pre-trail skip
settings.auto_trade_pre_t1_trail = True
settings.auto_trade_pre_t1_trail_arm_profit_pct = 3.0


def P(lots):
    return pb.Pos("t", "X", "O", 100, lots, 50.0, 45.0, 55.0, 60.0, 65.0, 0, 50.0)


settings.auto_trade_pre_t1_trail_min_lots = 1
p = P(1)
pb.manage(p, 53.0, 5e5)
check("min_lots=1: 1-lot pre-trail exits (old behaviour)", pb.manage(p, 51.0, 5e5) == "PRE-T1 PEAK TRAIL")
settings.auto_trade_pre_t1_trail_min_lots = 2
p = P(1)
pb.manage(p, 53.0, 5e5)
check("min_lots=2: 1-lot holds through the pullback", pb.manage(p, 51.0, 5e5) is None)
check("min_lots=2: 1-lot still stops at the hard stop", pb.manage(p, 44.9, 5e5) == "STOP LOSS")
p = P(2)
pb.manage(p, 53.0, 5e5)
check("min_lots=2: 2-lot keeps the pre-trail", pb.manage(p, 51.0, 5e5) == "PRE-T1 PEAK TRAIL")
src = open("app/state.py").read()
check("engine pre-trail gated on lots", "p.quantity_lots >= max(1, settings.auto_trade_pre_t1_trail_min_lots)" in src)
print(f"{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
