"""Smoke: socket token cap shared fairly across instruments, nearest strikes
first, and REST backfill of option strikes polls only the given tokens."""
import datetime as dt
import os

os.environ.setdefault("QT_TRADE_MODE", "paper")
from app.market import angelone as ao  # noqa: E402
from app.models import OptionType  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    fails += 0 if ok else 1


s = ao._LiveStream.__new__(ao._LiveStream)
s._provider_tokens = {1: ["F1", "a1", "a2", "a3"], 2: ["F2", "b1", "b2"], 3: ["F3"]}
cap = ao._MAX_WS_TOKENS
ao._MAX_WS_TOKENS = 5
got = s._desired_union()
ao._MAX_WS_TOKENS = cap
check("every future first, then strikes round-robin", got == {"F1", "F2", "F3", "a1", "b1"}, got)
check("wanted count reported", s._union_wanted == 8, s._union_wanted)

p = ao.AngelOneProvider.__new__(ao.AngelOneProvider)
exp = dt.date.today()
p._options = [ao._Instrument(f"t{k}", f"X{k}CE", float(k), exp, OptionType.CALL, False, 1)
              for k in range(100, 200, 10)]
p._spot = 151.0
order = p._atm_option_tokens()
check("ATM window nearest strike first", order[:3] == ["t150", "t160", "t140"], order[:3])


class FakeSmart:
    def __init__(self):
        self.calls = []

    def getMarketData(self, mode, req):
        toks = list(req.values())[0]
        self.calls.append(toks)
        return {"data": {"fetched": [{"symbolToken": t, "ltp": 10.0,
                                      "depth": {"buy": [{"price": 9.9, "quantity": 5}],
                                                "sell": [{"price": 10.1, "quantity": 5}]}}
                                     for t in toks]}}


p._smart = FakeSmart()
p._exchange = "NFO"
p._root = "TEST"
p._future = ao._Instrument("FUT", "XFUT", 0.0, exp, None, True, 1)
p._quotes = {}
p._token_tick_at = {}
p._refresh_quotes(only=["t150", "t160"])
check("backfill polls only the stale tokens", p._smart.calls == [["t150", "t160"]], p._smart.calls)
check("polled tokens get a fresh age", set(p._token_tick_at) == {"t150", "t160"}, p._token_tick_at)
check("polled book stored", p._quotes["t150"]["bid"] == 9.9 and p._quotes["t150"]["ask"] == 10.1, p._quotes["t150"])
print("ALL PASS" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
