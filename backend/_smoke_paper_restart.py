"""A restart mid-trade resumes the open PAPER position (exits keep running),
and the journal never merges a new position into an old OPEN row."""
from __future__ import annotations

import os
import tempfile
import time

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())
os.environ["QT_TRADE_MODE"] = "paper"

from app.config import settings  # noqa: E402
from app.engine import paper_positions  # noqa: E402
from app.journal.store import store  # noqa: E402
from app.state import AppState  # noqa: E402

settings.trade_mode = "paper"
_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)


st = AppState(instrument="CRUDEOIL")
st.tick()
chain = st.provider.option_chain()
sym = next(q.symbol for q in chain if q.premium)

# an orphan OPEN row (position lost by an old restart) for the same contract
store().open_trade({"trade_id": "orphan-1", "session_id": "s", "instrument": "CRUDEOIL",
                    "mode": "paper", "strategy": "x", "option_symbol": sym,
                    "entry_ts": time.time() - 600, "entry_price": 1.23, "lots": 9})
res = st.buy(sym, lots=2, manual=False)
check("paper buy ok", res.ok, res.message)
orphan = store().db.query_one("SELECT status, exit_reason FROM jr_trades WHERE trade_id='orphan-1'")
check("old OPEN row closed as LOST ON RESTART, not merged",
      orphan["status"] == "CLOSED" and orphan["exit_reason"] == "LOST ON RESTART", str(orphan))
tid = st._jr_trade_id
check("new position got its own journal row", tid and tid != "orphan-1", str(tid))

st._auto_entry_active = True
st._auto_target1, st._auto_stop, st._auto_peak = 999.0, 0.5, res.fill_premium
st._auto_entry_prem, st._auto_entry_lots = res.fill_premium, 2
st.tick()
saved = paper_positions.load("CRUDEOIL")
check("open paper position saved", saved is not None and saved[1]["position"]["option_symbol"] == sym)

st2 = AppState(instrument="CRUDEOIL")  # the restart
check("restart restores the position", st2.position.option_symbol == sym
      and st2.position.quantity_lots == 2
      and st2.position.entry_premium == st.position.entry_premium)
check("restart restores the exit marks", st2._auto_entry_active and st2._auto_target1 == 999.0
      and st2._auto_stop == 0.5 and st2._auto_entry_prem == res.fill_premium)
check("restart restores the entry time", st2._entry_time == st._entry_time)
check("restart keeps the journal row", st2._jr_trade_id == tid)

# a re-recorded entry of the SAME position reuses the row
from app.journal import hooks  # noqa: E402
st2._jr_trade_id = None
q = next(q for q in st2.provider.option_chain() if q.symbol == sym)
hooks.on_entry(st2, q, 2, st.position.entry_premium, False)
check("same position recorded again reuses its row", st2._jr_trade_id == tid, str(st2._jr_trade_id))

st2.tick()
out = st2.sell()
check("restored position exits normally", out.ok, out.message)
check("full exit clears the saved position", paper_positions.load("CRUDEOIL") is None)
check("journal row of the restored trade closed",
      store().db.query_one("SELECT status FROM jr_trades WHERE trade_id=?", (tid,))["status"] == "CLOSED")
st3 = AppState(instrument="CRUDEOIL")
check("next restart has no position", not st3.position.option_symbol)

paper_positions.save("CRUDEOIL", {"position": {"option_symbol": sym, "quantity_lots": 1,
                                               "entry_premium": 1.0}})
store().db.execute("UPDATE jr_open_positions SET saved_ts = ?", (time.time() - 10 ** 6,))
st4 = AppState(instrument="CRUDEOIL")
check("too-old saved position is not restored", not st4.position.option_symbol
      and paper_positions.load("CRUDEOIL") is None)

print("ALL PASS" if not _failures else f"{len(_failures)} FAILED: {_failures}")
raise SystemExit(1 if _failures else 0)
