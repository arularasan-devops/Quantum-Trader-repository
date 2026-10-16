"""Smoke: parallel PAPER books — sizing, isolation, staged exits, 75% day lock,
persistence/restart. SQLite temp DB; no provider, no broker."""
import os
import sys
import tempfile
import time
from types import SimpleNamespace as NS

os.environ.setdefault("QT_TRADE_MODE", "paper")
from app.config import settings  # noqa: E402
from app.engine import paper_books as pb  # noqa: E402
from app.models import Signal  # noqa: E402
from app.research.db import Database  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    fails += 0 if ok else 1


settings.data_provider = "simulated"
settings.paper_capital = 500000.0
settings.daily_profit_target = 10000.0
settings.daily_lock_keep_pct = 75.0
settings.paper_books_risk_pct = 1.0
tmp = tempfile.mkdtemp()
url = os.environ.get("BOOKS_SMOKE_DB_URL") or f"sqlite:///{tmp}/books.db"
if os.environ.get("BOOKS_SMOKE_DB_URL"):  # throwaway test DB only
    _d = Database(url)
    _d.execute("DROP TABLE IF EXISTS jr_book_trades")
    _d.execute("DROP TABLE IF EXISTS jr_book_days")
B = pb.PaperBooks(Database(url))
ids = [b.id for b in B.books]
check("six books", ids == ["5L-FULL", "5L-RISK", "10L-FULL", "10L-RISK", "15L-FULL", "15L-RISK"], ids)

# sizing: entry 13.7, stop 12.5, lot 1250 (the NATURALGAS case)
b5f, b5r, b10f, b10r, b15f, b15r = B.books
lots = {b.id: pb.size_lots(b, 13.7, 12.5, 1250)[0] for b in B.books}
check("FULL lots = capital / (ask x lot)", lots["5L-FULL"] == 29 and lots["10L-FULL"] == 58
      and lots["15L-FULL"] == 87, lots)
check("RISK lots = 1% capital / ((entry-stop) x lot)", lots["5L-RISK"] == 3 and lots["10L-RISK"] == 6
      and lots["15L-RISK"] == 10, lots)
check("RISK keeps 1 lot when 1% < one lot's risk", pb.size_lots(b5r, 100.0, 10.0, 75)[0] == 1)
check("RISK never above affordable", pb.size_lots(b5r, 13.7, 13.69, 1250)[0] == 29)

# staged exits
def P(**k):
    return pb.Pos("t", "X", "O", 1250, 29, 13.7, 12.5, 15.0, 16.0, 17.0, 0, 13.7, **k)


p = P()
check("hard stop before T1", pb.manage(p, 12.4, 5e5) == "STOP LOSS")
p = P()
pb.manage(p, 14.0, 5e5)  # +0.3 x 36250 = ₹10,875 -> rupee arm on 5L
check("rupee arm on 5L moves stop to >= entry", p.armed and p.stop >= 13.7, p.stop)
check("PRE-T1 trail exits on pullback", pb.manage(p, 13.75, 5e5) == "PRE-T1 PEAK TRAIL")
p = P()
pb.manage(p, 14.0, 1.5e6)
check("rupee arm scales with capital (15L needs ₹30k)", not p.armed)
p = P()
check("T1 reached: no exit", pb.manage(p, 15.0, 5e5) is None and p.floor == 15.0)
pb.manage(p, 15.8, 5e5)
check("T1->T2 trail raises floor", p.floor > 15.0, p.floor)
check("falls to floor: T1 LOCK exit", pb.manage(p, 15.0, 5e5) == "T1 LOCK")
p = P()
pb.manage(p, 17.5, 5e5)
check("past T3: runner exits under floor", pb.manage(p, 16.9, 5e5) == "T3 RUNNER TRAIL")

# lifecycle via on_tick with a fake engine
now = time.time()
st = NS(instrument="NATURALGAS", _last_market_status="TRENDING", _feed_ready=lambda: True)
settings.auto_trade_strategy_gate = True
dec = NS(signal=Signal.BUY, recommended_option="NG_CE", entry_trigger="PULLBACK", confidence=85.0,
         stop_loss=12.5, target1=15.0, target2=16.0, target3=17.0,
         option_type=NS(value="CE"))
def q(prem):
    return [NS(symbol="NG_CE", bid=prem - 0.05, ask=prem, premium=prem, ask_size=None)]


import app.engine.paper_books as mod  # noqa: E402
mod.get_spec = lambda s: NS(lot_size=1250)
B.on_tick(st, dec, q(13.7), now)
check("all six books entered the same signal", all("NATURALGAS" in b.positions for b in B.books))
check("each book its own lots", [b.positions["NATURALGAS"].lots for b in B.books] == [29, 3, 58, 6, 87, 10],
      [b.positions["NATURALGAS"].lots for b in B.books])
B.on_tick(st, dec, q(13.8), now + 1)
check("no duplicate entry on same signal", B.db.query_one("SELECT COUNT(*) n FROM jr_book_trades")["n"] == 6)
bad = NS(**{**dec.__dict__, "entry_trigger": "BREAKOUT_RETEST", "recommended_option": "NG_PE"})
st2 = NS(instrument="GOLD", _last_market_status="TRENDING", _feed_ready=lambda: True)
B.on_tick(st2, bad, [NS(symbol="NG_PE", bid=9.9, ask=10, premium=10, ask_size=None)], now)
check("strategy gate refuses non-PULLBACK", not any("GOLD" in b.positions for b in B.books), B.last_skip)

# restart restores open positions
B2 = pb.PaperBooks(Database(url))
check("restart restores open positions", all("NATURALGAS" in b.positions for b in B2.books))

# 5L-FULL: 29 lots x 1250 = 36,250 qty; move to 14.2 -> equity ~ +16k (peak); then 14.0 -> under 75% lock
dec_hold = NS(**{**dec.__dict__, "signal": Signal.HOLD})
settings.auto_trade_pre_t1_trail = False
B2.on_tick(st, dec_hold, q(14.15), now + 2)
f5 = B2.books[0]
check("5L-FULL day peak tracked", f5.day_peak > 10000, f5.day_peak)
lvl = pb.day_lock_level(f5.day_peak)
check("lock = 75% of day peak", abs(lvl - f5.day_peak * 0.75) < 1e-6, lvl)
B2.on_tick(st, dec_hold, q(13.98), now + 3)
check("5L-FULL locked and flattened under the lock", f5.locked and not f5.positions,
      (f5.locked, f5.equity(), lvl))
check("5L-RISK unaffected by 5L-FULL lock", not B2.books[1].locked and B2.books[1].positions)
row = B2.db.query_one("SELECT exit_reason, net_pnl FROM jr_book_trades WHERE book='5L-FULL'")
check("lock exit persisted", row["exit_reason"] == "PROFIT LOCK" and row["net_pnl"] > 0, row)
B2.on_tick(st, dec, q(13.9), now + 4)  # re-armed key? new key needed, but locked anyway
check("locked book takes no new entries", not f5.positions)
s = B2.summary()
check("summary lists six books", len(s["books"]) == 6 and s["books"][0]["trades"] == 1, s["books"][0])
check("trades filter by book", B2.trades("5L-FULL")["total"] == 1)
B3 = pb.PaperBooks(Database(url))
check("lock state survives restart", B3.books[0].locked)
print("All checks passed!" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
