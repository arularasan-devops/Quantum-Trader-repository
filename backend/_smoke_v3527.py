"""v3.5.27 smoke: near-spot option pick + slim socket frames. No broker, no DB."""
import os, sys, tempfile

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="qt_v3527_"))
os.environ.setdefault("QT_DATA_PROVIDER", "simulated")

from app.engine import decision as D  # noqa: E402
from app.models import Candle, OptionQuote, OptionType  # noqa: E402

ok = bad = 0


def check(name, cond):
    global ok, bad
    ok, bad = (ok + 1, bad) if cond else (ok, bad + 1)
    print(("PASS " if cond else "FAIL ") + name)


def Q(k, t, prem, delta, oi):
    return OptionQuote(symbol=f"X{k:g}{t}", strike=k,
                       option_type=OptionType.CALL if t == "CE" else OptionType.PUT,
                       premium=prem, iv=0.3, delta=delta, gamma=0, theta=0, vega=0,
                       oi=oi, oi_change=0, volume=0)


def pick(chain, spot, call=True):
    return D._select_option(chain, spot, call, 1.0).option_symbol


# Expiry-day stock chain (RELIANCE 27 Oct): ATM thin OI, far rows heavy OI.
rel = [Q(1190, "CE", 6.0, 0.55, 2000), Q(1200, "CE", 2.0, 0.35, 3000),
       Q(1260, "CE", 0.1, 0.5, 90000), Q(1020, "CE", 141.7, 0.52, 90000)]
check("RELIANCE far wing / deep ITM not picked", pick(rel, 1193.0) == "X1190CE")
# ₹0.1 rows never win while a priced near strike exists.
check("min premium", pick([Q(1190, "CE", 0.1, 0.52, 9000), Q(1200, "CE", 3.0, 0.4, 9000)], 1193.0) == "X1200CE")
# CRUDEOIL 27 Oct live pick (9900 PE at spot 9693, 2.1% away) is unchanged.
crude = [Q(k, "PE", p, d, 9000) for k, p, d in
         [(9700, 330, -0.45), (9800, 420, -0.5), (9900, 665.5, -0.53), (10000, 750, -0.62)]]
check("CRUDEOIL pick unchanged", pick(crude, 9693.0, call=False) == "X9900PE")
# Nothing within the band: nearest priced strike, never None.
check("band fallback", pick([Q(1300, "CE", 1.0, 0.2, 9000), Q(1400, "CE", 1.0, 0.1, 9000)], 1193.0) == "X1300CE")

# Socket frames: full history first, then only forming bars until the shape changes.
from app.main import hub  # noqa: E402
from app.state import registry  # noqa: E402

st = registry.get("CRUDEOIL")
snap = st.tick()
if snap is None:
    print("SKIP frame checks (no snapshot builder)")
else:
    snap.futures_candles = [Candle(time=60 * i, open=1, high=1, low=1, close=1, volume=0) for i in range(1, 121)]
    snap.selected_option_candles = list(snap.futures_candles)
    f1 = hub._frame("CRUDEOIL", snap)
    f2 = hub._frame("CRUDEOIL", snap)
    check("first frame full", not f1.get("candles_tail") and len(f1["futures_candles"]) == 120)
    check("next frame tail", f2.get("candles_tail") is True and len(f2["futures_candles"]) == 2)
    snap.futures_candles = snap.futures_candles[1:] + [Candle(time=60 * 121, open=1, high=1, low=1, close=1, volume=0)]
    f3 = hub._frame("CRUDEOIL", snap)
    check("new bar resends full", not f3.get("candles_tail") and len(f3["futures_candles"]) == 120)
    snap.display_option_symbol = "OTHER"
    check("contract change resends full", not hub._frame("CRUDEOIL", snap).get("candles_tail"))

print(f"{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
