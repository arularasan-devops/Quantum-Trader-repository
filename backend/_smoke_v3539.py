"""Smoke v3.5.39: Angel option 1-min candles refresh after option_candles_refresh_sec
(old candles kept until the refetch succeeds); square-off defaults 15:15 / 23:15.
No broker: the historical fetch is faked."""
import os
import tempfile
import time

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())
from types import SimpleNamespace  # noqa: E402

from app.config import settings  # noqa: E402
from app.market import angelone  # noqa: E402
from app.models import Candle  # noqa: E402

FAIL = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    if not ok:
        FAIL.append(name)


CALLS = []
NEXT = {"rows": [1]}
angelone._historical_candles = lambda shard, params, blocking: (CALLS.append(params) or (NEXT["rows"], ""))
angelone._to_candles = lambda rows: [Candle(time=i, open=10, high=11, low=9, close=10, volume=1) for i in rows]
p = object.__new__(angelone.AngelOneProvider)
p._options = [SimpleNamespace(symbol="X", token="1")]
p._opt_candles = {}
p._opt_candles_at = {}
p._exchange = "MCX"
p._shard = SimpleNamespace(hist=SimpleNamespace(warming_count=lambda: 0))
settings.option_candles_refresh_sec = 180.0
check("first call fetches", len(p.option_candles("X")) == 1 and len(CALLS) == 1)
NEXT["rows"] = [1, 2]
check("within refresh window: cached, no fetch", len(p.option_candles("X")) == 1 and len(CALLS) == 1)
p._opt_candles_at["X"] -= 200
check("after refresh window: refetched", len(p.option_candles("X")) == 2 and len(CALLS) == 2)
p._opt_candles_at["X"] -= 200
NEXT["rows"] = []
check("failed refetch keeps old candles", len(p.option_candles("X")) == 2 and len(CALLS) == 3)
settings.option_candles_refresh_sec = 0
NEXT["rows"] = [1, 2, 3]
check("0 = never refresh", len(p.option_candles("X")) == 2 and len(CALLS) == 3)
check("square-off defaults 15:15 / 23:15",
      type(settings)().auto_trade_live_squareoff_equity_ist == "15:15"
      and type(settings)().auto_trade_live_squareoff_mcx_ist == "23:15", "")
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
