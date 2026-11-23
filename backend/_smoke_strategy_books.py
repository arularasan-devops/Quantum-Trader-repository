"""Smoke: PAPER strategy books — 9 independent ₹2.5L books, own trigger only, no shared capital."""
import os
import sys
import tempfile
import time
from types import SimpleNamespace as NS

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())
from app.config import settings  # noqa: E402
from app.engine import gapup, paper_books as pb, strategy_books as sbm  # noqa: E402
from app.models import Signal  # noqa: E402
from app.research.db import Database  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    fails += 0 if ok else 1


settings.data_provider = "simulated"
settings.strategy_books_enabled = True
settings.strategy_books_capital = 250000.0
settings.strategy_books_risk_pct = 1.0
settings.daily_profit_target = 0.0
settings.daily_lock_steps = ""
settings.auto_trade_min_confidence = 80.0
url = os.environ.get("SB_SMOKE_DB_URL") or f"sqlite:///{tempfile.mkdtemp()}/sb.db"
if os.environ.get("SB_SMOKE_DB_URL"):  # throwaway test DB only
    for t in ("jr_book_trades", "jr_book_days"):
        Database(url).execute(f"DROP TABLE IF EXISTS {t}")
spec = NS(lot_size=65, exchange="NSE")
sbm.get_spec = pb.get_spec = lambda s: spec
sbm.StrategyBooks._past_cutoff = staticmethod(lambda i, n: False)
sbm.StrategyBooks._session_exit = lambda self, p, n: None
gapup.in_window = lambda ex, now: False

SB = sbm.StrategyBooks(Database(url))
ids = [b.id for b in SB.books]
check("9 books by strategy name", ids == ["S-" + s for s in sbm.STRATEGIES], ids)
check("each book ₹2,50,000", all(b.capital == 250000 for b in SB.books))
check("total ₹22,50,000", SB.summary()["total_capital"] == 2250000)

now = time.time()
st = NS(instrument="NIFTY", _last_market_status="TRENDING", _feed_ready=lambda: True,
        provider=NS(day_ohlc=lambda: {}))
dec = NS(signal=Signal.BUY, recommended_option="N_CE", entry_trigger="PULLBACK",
         entry_triggers=["PULLBACK", "MOMENTUM"], confidence=85.0, stop_loss=115.0,
         target1=130.0, target2=140.0, target3=150.0, option_type=NS(value="CE"))


def q(sym, prem, typ="CE", strike=25000.0):
    return NS(symbol=sym, bid=prem - 0.5, ask=prem, premium=prem, ask_size=None, strike=strike,
              option_type=NS(value=typ))


SB.on_tick(st, dec, [q("N_CE", 120.0)], now)
held = {b.id for b in SB.books if b.positions}
check("only the signal's strategies bought", held == {"S-PULLBACK", "S-MOMENTUM"}, held)
p = SB._by_id["S-PULLBACK"].positions["NIFTY"]
check("lots from own capital & 1% risk: 2500 // ((120-115)*65) = 7", p.lots == 7, p.lots)
check("entry at the ask", p.entry == 120.0, p.entry)
check("1 lot above the 1% risk budget is not bought",
      SB.size(SB._by_id["S-REVERSAL"], 120.0, 80.0, 65)[0] == 0)
check("1 lot exactly at the risk budget is bought",
      SB.size(SB._by_id["S-REVERSAL"], 1.1, 0.9, 12500)[0] == 1)  # (1.1-0.9)*12500 = 2500.000...1
s0 = {r["book"]: r for r in SB.summary()["books"]}
check("used margin = 120*65*7", s0["S-PULLBACK"]["used_margin"] == 54600, s0["S-PULLBACK"])
check("available = balance - used", s0["S-PULLBACK"]["available"] == 250000 - 54600)
check("untouched book keeps full cash", s0["S-IGNITION"]["available"] == 250000)
SB.on_tick(st, dec, [q("N_CE", 121.0)], now + 1)
check("no duplicate buy on the same signal",
      SB.db.query_one("SELECT COUNT(*) n FROM jr_book_trades")["n"] == 2)

# stop out at the bid -> loss booked per book, other books unchanged
SB.on_tick(st, NS(**{**dec.__dict__, "signal": Signal.HOLD}), [q("N_CE", 110.0)], now + 2)
s1 = {r["book"]: r for r in SB.summary()["books"]}
lp = s1["S-PULLBACK"]["realized_pnl"]
check("stop exit closed both books", not SB._by_id["S-PULLBACK"].positions and not SB._by_id["S-MOMENTUM"].positions)
check("loss booked net of charges", lp < -(120 - 109.5) * 65 * 7 + 1, lp)
check("balance = capital + own realized", s1["S-PULLBACK"]["balance"] == round(250000 + lp, 2))
check("IGNITION capital untouched by others' losses", s1["S-IGNITION"]["balance"] == 250000)
row = SB.trades("S-PULLBACK")["rows"][0]
check("buy and exit recorded", row["status"] == "CLOSED" and row["exit_price"] == 109.5 and row["exit_reason"], row)

# breakout book
SB.on_breakout(st, {"option": "N_PE", "r_pts": 20.0}, [q("N_PE", 100.0, "PE")], now + 3)
bp = SB._by_id["S-BASE_BREAKOUT"].positions.get("NIFTY")
check("BASE_BREAKOUT book: stop = ask - R/2, T1 = +R/2", bp and bp.stop == 90.0 and bp.t1 == 110.0, bp)

# gap-up book
gapup.in_window = lambda ex, now: True
st_s = NS(instrument="SENSEX", _last_market_status="", _feed_ready=lambda: True,
          provider=NS(day_ohlc=lambda: {"open": 101.0, "prev_close": 100.0}))
chain = [q("S_CE_A", 300.0, "CE", 80000.0), q("S_PE_A", 150.0, "PE", 80000.0),
         q("S_CE_B", 200.0, "CE", 80100.0), q("S_PE_B", 205.0, "PE", 80100.0)]
SB.on_tick(st_s, NS(**{**dec.__dict__, "signal": Signal.HOLD}), chain, now + 4)
gp = SB._by_id["S-GAPUP"].positions.get("SENSEX")
check("GAPUP book bought the ATM CE on a 1% gap-up", gp and gp.option == "S_CE_B", gp)
SB.on_tick(st_s, NS(**{**dec.__dict__, "signal": Signal.HOLD}), chain, now + 5)
check("one GAPUP entry per day",
      SB.db.query_one("SELECT COUNT(*) n FROM jr_book_trades WHERE book='S-GAPUP'")["n"] == 1)
SB_r = sbm.StrategyBooks(Database(url))
SB_r._by_id["S-GAPUP"].positions.clear()
SB_r.on_tick(st_s, NS(**{**dec.__dict__, "signal": Signal.HOLD}), chain, now + 6)
check("no second GAPUP entry after a restart",
      SB_r.db.query_one("SELECT COUNT(*) n FROM jr_book_trades WHERE book='S-GAPUP'")["n"] == 1)

# isolation from the existing Paper Books board, restart, off switch
PB = pb.PaperBooks(Database(url))
check("Paper Books board does not list strategy rows", PB.trades()["total"] == 0, PB.trades()["total"])
SB2 = sbm.StrategyBooks(Database(url))
check("restart restores open positions and realized P&L",
      "NIFTY" in SB2._by_id["S-BASE_BREAKOUT"].positions
      and round(SB2.realized["S-PULLBACK"], 2) == round(lp, 2))
settings.strategy_books_enabled = False
n0 = SB.db.query_one("SELECT COUNT(*) n FROM jr_book_trades")["n"]
sbm._books = SB
sbm.on_tick(st, NS(**{**dec.__dict__, "recommended_option": "N_CE2", "entry_triggers": ["IGNITION"]}),
            [q("N_CE2", 50.0)], now + 6)
check("OFF: nothing recorded", SB.db.query_one("SELECT COUNT(*) n FROM jr_book_trades")["n"] == n0)
src = open(sbm.__file__).read()
check("no broker/order code in strategy_books",
      not any(w in src for w in ("place_order", "smartapi", "SmartConnect", "def sell", ".buy(")))
print("ALL STRATEGY BOOK CHECKS PASSED" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
