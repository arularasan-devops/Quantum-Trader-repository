"""One Journal for live + paper: on a LIVE stack the Journal's Paper tab shows
the Paper Books' closed trades (same feed, same DB); on a paper stack nothing
is added (it journals its own paper trades)."""
import os, sys, time
os.environ.setdefault("QT_DATA_DIR", "/tmp/qt_jpb_smoke")
os.environ["QT_DATA_PROVIDER"] = "simulated"
from fastapi.testclient import TestClient
from app.config import settings
from app.engine import paper_books
from app import main, storage

fails = []
def check(n, ok):
    print(("PASS " if ok else "FAIL ") + n)
    if not ok: fails.append(n)

pb = paper_books.books()
now = time.time()
for tid, book, net in (("jpb-1", "5L-FULL", 1234.5), ("jpb-2", "10L-RISK", -99.0)):
    pb.db.execute("DELETE FROM jr_book_trades WHERE trade_id = ?", (tid,))
    pb.db.execute(
        "INSERT INTO jr_book_trades (trade_id, book, capital, sizing, instrument, option_symbol, "
        "lot_size, lots, capital_used, risk_rupees, entry_ts, entry_price, status, exit_ts, "
        "exit_price, exit_reason, gross_pnl, costs, net_pnl, setup, confidence) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, book, 500000, "FULL", "NIFTY", "NIFTY28OCT2624000CE", 65, 2, 19500, 3000,
         now - 600, 150.0, "CLOSED", now - 60, 160.0, "TARGET", net + 60, 60.0, net, "PULLBACK", 84.0))

c = TestClient(main.app)
settings.trade_mode = "live"
check("off by default", settings.journal_paper_books == "" and not any(
    (r.get("note") or "").startswith("Paper Book") for r in c.get("/api/trade-journal").json()["trades"]))
settings.journal_paper_books = "5L-FULL"
j = c.get("/api/trade-journal?mode=paper").json()
mine = [r for r in j["trades"] if r.get("note") == "Paper Book 5L-FULL"]
check("live stack: 5L-FULL trade in Journal Paper tab", len(mine) == 1 and mine[0]["mode"] == "paper"
      and mine[0]["net_pnl"] == 1234.5 and mine[0]["win"] and mine[0]["option_type"] == "CE"
      and mine[0]["entry_trigger"] == "PULLBACK" and mine[0]["holding_minutes"] == 9.0)
check("other books not mirrored by default", not any(r.get("note") == "Paper Book 10L-RISK" for r in j["trades"]))
lv = c.get("/api/trade-journal?mode=live").json()
check("Live tab has no paper rows", all(r["mode"] == "live" for r in lv["trades"]))
al = c.get("/api/trade-journal?mode=all").json()
check("All tab includes the paper book trade", any(r.get("note") == "Paper Book 5L-FULL" for r in al["trades"]))
old = c.get(f"/api/trade-journal?mode=paper&until={now - 3600}").json()
check("date range respected", not any(r.get("note", "").startswith("Paper Book") for r in old["trades"]))
csv = c.get("/api/trade-journal.csv?mode=paper").text
check("CSV carries it", "Paper Book 5L-FULL" in csv)
settings.journal_paper_books = "5L-FULL,10L-RISK"
j2 = c.get("/api/trade-journal?mode=paper").json()
check("QT_JOURNAL_PAPER_BOOKS adds books", sum(1 for r in j2["trades"] if (r.get("note") or "").startswith("Paper Book")) == 2)
settings.journal_paper_books = ""
check("empty setting = none", not any((r.get("note") or "").startswith("Paper Book")
                                       for r in c.get("/api/trade-journal").json()["trades"]))
settings.journal_paper_books = "5L-FULL"
settings.trade_mode = "paper"
check("paper stack: nothing mirrored", not any((r.get("note") or "").startswith("Paper Book")
                                              for r in c.get("/api/trade-journal").json()["trades"]))
for tid in ("jpb-1", "jpb-2"):
    pb.db.execute("DELETE FROM jr_book_trades WHERE trade_id = ?", (tid,))
print("ALL JOURNAL PAPER-BOOK CHECKS PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
