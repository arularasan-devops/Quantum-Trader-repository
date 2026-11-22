"""Smoke: dynamic capital-based lot sizing (NIFTY/SENSEX dynamic, others 1 lot)
and the Angel-account day P&L source for the profit lock. MOCKED broker only —
nothing contacts Angel One. Run:
    cd backend && python _smoke_dynamic_sizing.py
"""
from __future__ import annotations

import os
import tempfile
import time
from types import SimpleNamespace

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_dyn_size_")
os.environ.pop("QT_DB_URL", None)
os.environ.pop("QT_CAPITAL", None)

from app.config import settings  # noqa: E402
from app.engine import account_risk  # noqa: E402
from app.execution import dynamic_sizing as ds  # noqa: E402
from app.market import angelone  # noqa: E402

FAIL: list[str] = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {info}"))
    if not cond:
        FAIL.append(name)


def snap(v, age=0.0, src="test"):
    return {"value": v, "ts": time.time() - age, "source": src}


def run(inst="NIFTY", entry=100.0, stop=85.0, lot=65, funds=500000.0, margin=None,
        reserve=0.0, dep_i=0.0, dep_p=0.0, open_pos=0, day=0.0, extra=None, fage=0.0, mage=0.0):
    margin = entry * lot if margin is None else margin
    return ds.compute(instrument=inst, entry=entry, stop=stop, lot_size=lot,
                      funds=None if funds is None else snap(funds, fage),
                      margin_per_lot=None if margin is None else snap(margin, mage),
                      pending_reserve=reserve, deployed_instrument=dep_i,
                      deployed_portfolio=dep_p, open_positions=open_pos, day_pnl=day,
                      extra_caps=extra)


# defaults
f = type(settings).model_fields
check("mode default off", f["dyn_sizing_mode"].default == "off")
check("live default off", f["dyn_sizing_live"].default is False)
check("lock source default tool", f["daily_lock_equity_source"].default == "tool")

# 1. NIFTY sized by the tightest limit; every constraint recorded
r = run()
c = r["constraints"]
check("nifty lots = min of constraints", r["lots"] == min(c.values()) and r["lots"] >= 1, str(r))
for k in ("capital", "margin", "risk", "instrument_exposure", "portfolio_exposure",
          "open_positions", "max_lots"):
    check(f"constraint recorded: {k}", k in c, str(c))
for k in ("available_funds", "margin_per_lot", "risk_per_lot", "charges_per_lot",
          "pending_reserve", "usable_funds"):
    check(f"field recorded: {k}", k in r)
# risk per lot = (15 + 2*1 slippage) * 65 + charges
check("risk per lot includes slippage+charges",
      r["risk_per_lot"] > 17 * 65 and r["charges_per_lot"] > 0, str(r["risk_per_lot"]))
check("nifty binding is risk (5000/~1140)", r["binding"] == "risk" and r["lots"] == 4, str(r))

# 2. SENSEX dynamic too; capped by max_lots when everything else is loose
settings.dyn_sizing_limits["SENSEX"]["risk_per_trade"] = 10**7
settings.dyn_sizing_limits["SENSEX"]["max_exposure"] = 10**8
settings.dyn_sizing_max_portfolio_exposure = 10**8
settings.dyn_sizing_daily_loss_limit = 0
r = run("SENSEX", entry=50.0, stop=40.0, lot=20, funds=10**7)
check("sensex capped by max_lots 10", r["lots"] == 10 and r["binding"] == "max_lots", str(r))
settings.dyn_sizing_daily_loss_limit = 10000.0
settings.dyn_sizing_max_portfolio_exposure = 500000.0

# 3. other instruments: exactly 1 lot, even with huge funds
r = run("CRUDEOIL", entry=100.0, stop=90.0, lot=100, funds=10**7)
check("crudeoil 1 lot", r["lots"] == 1 and r["limits"]["max_lots"] == 1, str(r))
r = run("RELIANCE", entry=10.0, stop=8.0, lot=500, funds=10**7)
check("stock 1 lot", r["lots"] == 1, str(r))
# ...and still rejected when 1 lot fails a check
r = run("CRUDEOIL", entry=300.0, stop=100.0, lot=100, funds=10**7)
check("crudeoil 1 lot over risk -> reject", r["lots"] == 0 and r["reject"] == "LIMIT_RISK", str(r))

# 4. fail closed: unverified / stale funds or margin
check("no funds -> reject", run(funds=None)["reject"] == "FUNDS_UNVERIFIED")
check("stale funds -> reject", run(fage=60)["reject"] == "FUNDS_STALE")
r = ds.compute(instrument="NIFTY", entry=100, stop=85, lot_size=65, funds=snap(5e5),
               margin_per_lot=None, pending_reserve=0, deployed_instrument=0,
               deployed_portfolio=0, open_positions=0, day_pnl=0)
check("no margin -> reject", r["reject"] == "MARGIN_UNVERIFIED", str(r))
check("stale margin -> reject", run(mage=60)["reject"] == "MARGIN_STALE")
check("no stop -> reject", run(stop=None)["reject"] == "NO_PROTECTIVE_STOP")
check("stop above entry -> reject", run(stop=101)["reject"] == "NO_PROTECTIVE_STOP")

# 5. margin: 1 lot > usable funds -> reject; reserve is subtracted once
check("1 lot > funds -> reject", run(funds=5000.0)["reject"] in ("LIMIT_MARGIN", "LIMIT_CAPITAL"))
a = run(funds=20000.0)
b = run(funds=20000.0, reserve=13000.0)
check("pending reserve cuts margin lots", b["constraints"]["margin"] < a["constraints"]["margin"],
      f"{a['constraints']} {b['constraints']}")

# 6. exposure, open positions, daily loss room
check("instrument exposure full -> reject",
      run(dep_i=250000.0)["reject"] == "LIMIT_INSTRUMENT_EXPOSURE")
check("portfolio exposure full -> reject",
      run(dep_p=500000.0)["reject"] == "LIMIT_PORTFOLIO_EXPOSURE")
check("max open positions -> reject", run(open_pos=3)["reject"] == "LIMIT_OPEN_POSITIONS")
r = run(day=-9000.0)
check("daily loss room 1000 < 1 lot risk -> reject", r["reject"] == "LIMIT_RISK", str(r))
r = run(day=+20000.0)
check("profit does not enlarge risk budget", r["risk_budget"] == 5000.0, str(r["risk_budget"]))
check("extra cap applies", run(extra={"live_order_value_cap": 2})["lots"] == 2)

# 7. Angel snapshot: rmsLimit + getMarginApi, read-only, uncached
class Smart:
    def __init__(self, fail_margin=False):
        self.calls = []
        self.fail_margin = fail_margin

    def rmsLimit(self):
        self.calls.append("rms")
        return {"data": {"availablecash": "123456.5"}}

    def getMarginApi(self, params):
        self.calls.append(("margin", params))
        if self.fail_margin:
            raise RuntimeError("down")
        return {"data": {"totalMarginRequired": 6000.0}}

    def placeOrder(self, *a, **k):
        raise AssertionError("no orders")


prov = object.__new__(angelone.AngelOneProvider)
inst = angelone._Instrument("123", "NIFTYTEST100CE", 100.0, None, None, False, lot_size=65)
prov._options = [inst]
prov._exchange = "NFO"
sm = Smart()
prov._order_smart = lambda: sm
s = prov.sizing_snapshot("NIFTYTEST100CE", 100.0)
check("snapshot funds", s["funds"]["value"] == 123456.5 and s["funds"]["source"] == "angel_rms")
check("snapshot margin >= premium x lot",
      s["margin_per_lot"]["value"] == 6500.0, str(s["margin_per_lot"]))
p = sm.calls[1][1]["positions"][0]
check("margin params", p["qty"] == 65 and p["tradeType"] == "BUY" and p["token"] == "123", str(p))
s2 = prov.sizing_snapshot("NIFTYTEST100CE", 100.0)
check("snapshot uncached", sm.calls.count("rms") == 2)
sm2 = Smart(fail_margin=True)
prov._order_smart = lambda: sm2
s3 = prov.sizing_snapshot("NIFTYTEST100CE", 100.0)
check("margin API down -> margin None", s3["margin_per_lot"] is None and s3["funds"] is not None)
check("unknown symbol -> None", prov.sizing_snapshot("NOPE", 100.0) is None)

# 8. AppState wiring: paper sizing, live fail-closed, sizing events, lock source
from app.execution import order_ledger  # noqa: E402
from app.state import AppState, registry  # noqa: E402

settings.trade_mode = "paper"
st = AppState(instrument="NIFTY")
registry._states["NIFTY"] = st
st.tick()
opt = st.provider.option_chain()[0]
dec = SimpleNamespace(current_premium=float(opt.premium or 100.0),
                      stop_loss=float(opt.premium or 100.0) - 15.0)
r = st._dynamic_size(dec, opt.symbol, 65, False, 0.0, 0.0, 0)
check("paper sizing sources", r["funds_source"] == "paper_capital"
      and r["margin_source"] == "paper_premium" and r["lots"] >= 1, str(r))
r = st._dynamic_size(dec, opt.symbol, 65, True, 0.0, 0.0, 0)
check("live without broker snapshot -> reject", r["lots"] == 0
      and r["reject"] == "FUNDS_UNVERIFIED", str(r))
st.provider.sizing_snapshot = lambda sym, px: {"funds": snap(300000.0, src="angel_rms"),
                                               "margin_per_lot": snap(px * 65, src="angel_margin_api")}
r = st._dynamic_size(dec, opt.symbol, 65, True, 0.0, 0.0, 0)
check("live with fresh snapshot -> sized from Angel", r["funds_source"] == "angel_rms"
      and "live_order_value_cap" in r["constraints"]
      and r["lots"] == min(r["constraints"].values()), str(r))
st._sizing_event("SIZING_DECISION", dict(r, mode="log"), True)
ev = [e for e in order_ledger.read_events() if e.get("kind") == "SIZING_DECISION"] \
    if hasattr(order_ledger, "read_events") else None
if ev is not None:
    check("sizing event durable", ev and ev[-1].get("sizing_constraints"), str(ev[-1:]))
else:
    path = order_ledger._path(order_ledger.EVENTS_FILE)
    txt = open(path).read()
    check("sizing event durable", "SIZING_DECISION" in txt and "sizing_constraints" in txt)

account_risk.account.reset()
account_risk.account.day_pnl = 1000.0
check("lock source tool: tool mtm", st._lock_open_mtm(True) == 0.0)
settings.daily_lock_equity_source = "angel"
angelone.broker_day_pnl = lambda ttl=10.0: 9500.0
check("lock source angel: equity = Angel P&L",
      account_risk.account.day_pnl + st._lock_open_mtm(True) == 9500.0)
check("lock source angel ignored in paper", st._lock_open_mtm(False) == 0.0)
angelone.broker_day_pnl = lambda ttl=10.0: None
check("Angel unreadable -> tool figure", st._lock_open_mtm(True) == 0.0)
settings.daily_lock_equity_source = "tool"

# 9. post-lock budget: next trade risk fits the room above the lock
settings.daily_profit_target = 7000.0
settings.daily_lock_steps = "7000:100"
lock = account_risk.day_lock_level(10000.0)
budget = account_risk.entry_loss_budget(10000.0, 10000.0, per_trade_cap=None, lock=lock)
check("lock 7k at peak 7k+ steps 7000:100 -> lock 10k peak", lock == 10000.0, str(lock))
budget = account_risk.entry_loss_budget(10000.0, 10000.0, per_trade_cap=None, lock=7000.0)
check("room above 7k lock shared 50%", abs(budget - 1500.0) < 1e-6, str(budget))

print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
