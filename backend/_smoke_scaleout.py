"""Focused check that a scale-out books only the sold share and leaves a runner."""
from app.models import Position
from app.state import AppState

st = AppState()
st.position = Position(
    option_symbol="NIFTY24000CE",
    side="LONG",
    entry_premium=100.0,
    current_premium=120.0,
    quantity_lots=4,
    net_pnl=4000.0,
)
before = len(st.journal)
res = st.sell(lots=2)
print("partial ok        :", res.ok, "|", res.message)
print("lots sold         :", res.lots, "(expect 2)")
print("runner lots left  :", st.position.quantity_lots, "(expect 2)")
print("position still open:", bool(st.position.option_symbol), "(expect True)")
row = st.journal[-1]
print("booked pnl        :", row["net_pnl"], "(expect 2000.0 = half of 4000)")
print("journal rows added:", len(st.journal) - before, "(expect 1)")

st.position.net_pnl = 3000.0
res2 = st.sell()
print("\nfinal exit ok     :", res2.ok, "| lots", res2.lots, "(expect 2)")
print("position closed   :", not st.position.option_symbol, "(expect True)")
print("final booked pnl  :", st.journal[-1]["net_pnl"], "(expect 3000.0)")
total = st.journal[-2]["net_pnl"] + st.journal[-1]["net_pnl"]
print("total booked      :", total, "(2000 scale-out + 3000 runner)")
assert res.ok and res.lots == 2 and st.journal[-2]["net_pnl"] == 2000.0
assert res2.ok and not st.position.option_symbol
print("\nOK scale-out accounting")
