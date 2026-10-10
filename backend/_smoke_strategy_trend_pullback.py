"""Smoke: the EXISTING trend-plus-pullback strategy, pinned deterministically (Phase D).

Documents current behaviour of app/analysis/structure.py and
app/engine/decision.py — it does not change or tune any threshold.
See docs/STRATEGY_TREND_PULLBACK.md.
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="qt_strategy_"))

from app.analysis import structure  # noqa: E402
from app.engine import decision as dec  # noqa: E402
from app.models import Candle, OptionQuote, OptionType, Signal  # noqa: E402

passed = failed = 0
fails: list[str] = []


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        fails.append(msg)
        print(f"FAIL: {msg}")


# ---- trend_from_emas: strict EMA stacking ----
ok(structure.trend_from_emas(103, 102, 101) == "UP", "EMA9 > EMA20 > EMA50 is UP")
ok(structure.trend_from_emas(99, 100, 101) == "DOWN", "EMA9 < EMA20 < EMA50 is DOWN")
ok(structure.trend_from_emas(102, 100, 101) == "SIDEWAYS", "unstacked EMAs are SIDEWAYS")
ok(structure.trend_from_emas(None, 100, 101) == "SIDEWAYS", "missing EMA is SIDEWAYS, not a guess")

# ---- pullback: price at/near/through EMA20 in the trend direction (0.6*ATR band) ----
c = np.array([100.0, 101.0, 100.5])
ok(structure.pullback(c, 100.4, "UP", 1.0) == "PULLBACK_UP", "uptrend within 0.6 ATR of EMA20 is PULLBACK_UP")
ok(structure.pullback(np.array([100.0, 99.0, 99.5]), 100.0, "UP", 0.1) == "PULLBACK_UP",
   "uptrend closing below EMA20 is PULLBACK_UP")
ok(structure.pullback(np.array([100.0, 102.0, 103.0]), 100.0, "UP", 1.0) == "NONE",
   "uptrend 3 ATR above EMA20 is extended, not a pullback")
ok(structure.pullback(np.array([100.0, 99.0, 99.8]), 100.0, "DOWN", 1.0) == "PULLBACK_DOWN",
   "downtrend popping to EMA20 is PULLBACK_DOWN")
ok(structure.pullback(np.array([100.0, 98.0, 97.0]), 100.0, "DOWN", 1.0) == "NONE",
   "downtrend 3 ATR below EMA20 is extended, not a pullback")
ok(structure.pullback(c, 100.4, "SIDEWAYS", 1.0) == "NONE", "no pullback without a trend")
ok(structure.pullback(c, None, "UP", 1.0) == "NONE", "no pullback without EMA20")
ok(structure.pullback(c[:2], 100.4, "UP", 1.0) == "NONE", "no pullback with < 3 closes")
ok(abs(100.6 - 100.0) <= 0.6 * 1.0 and structure.pullback(
    np.array([1.0, 1.0, 100.6]), 100.0, "UP", 1.0) == "PULLBACK_UP",
   "0.6 ATR boundary is inclusive")


# ---- higher-timeframe (5-min) trend gate ----
def candles(path: list[float], start: int = 1_760_000_000) -> list[Candle]:
    out, prev = [], path[0]
    for i, px in enumerate(path):
        hi, lo = max(prev, px) + 0.2, min(prev, px) - 0.2
        out.append(Candle(time=start + 60 * i, open=prev, high=hi, low=lo, close=px, volume=1000))
        prev = px
    return out


up = [100 + 0.25 * i for i in range(120)]
down = [130 - 0.25 * i for i in range(120)]
flat = [100 + (0.3 if i % 2 else -0.3) for i in range(120)]
ok(dec._htf_trend(candles(up))[0] == "UP", "steady rise is a 5-min UP trend")
ok(dec._htf_trend(candles(down))[0] == "DOWN", "steady fall is a 5-min DOWN trend")
ok(dec._htf_trend(candles(flat))[0] == "SIDEWAYS", "chop is SIDEWAYS (no direction)")
ok(dec._htf_trend(candles(up[:20]))[0] == "SIDEWAYS", "fewer than 6 HTF bars is SIDEWAYS")
s = dec._htf_trend(candles(up))[1]
ok(0.0 < s <= 1.0, "trend strength is bounded in (0, 1]")


# ---- full decide(): direction gating + determinism ----
def chain(spot: float) -> list[OptionQuote]:
    out = []
    for k in (spot - 10, spot - 5, spot, spot + 5, spot + 10):
        for ot in (OptionType.CALL, OptionType.PUT):
            out.append(OptionQuote(symbol=f"X{int(k)}{ot.value}", strike=round(k), option_type=ot,
                                   premium=25.0, iv=0.3, delta=0.5, gamma=0.01, theta=-1.0,
                                   vega=1.0, oi=10000, oi_change=100, volume=5000))
    return out


def run(path: list[float]):
    cs = candles(path)
    ch = chain(path[-1])
    snap = dec.compute_indicators(cs, ch)
    status = dec.classify_market(snap, False)
    d, _ = dec.decide(cs, ch, snap, 0.0, False, status, False, None)
    return snap, d


_, d_flat = run(flat)
ok((d_flat.market_signal or d_flat.signal) != Signal.BUY, "no BUY when the 5-min trend is flat")

pull = up[:110] + [up[109] - 0.3 * i for i in range(1, 11)]
snap_u, d_u = run(pull)
_, d_u2 = run(pull)
ok(d_u.model_dump(exclude={"time", "timestamp"}) == d_u2.model_dump(exclude={"time", "timestamp"})
   or d_u.signal == d_u2.signal, "decide() is deterministic for identical input")
def buy_side(d):
    sig = d.market_signal or d.signal
    return d.option_type.value if sig == Signal.BUY and d.option_type else None


htf_u = dec._htf_trend(candles(pull))[0]
ok(buy_side(d_u) in (None, OptionType.CALL.value) or htf_u != "UP",
   f"a BUY in a 5-min uptrend is a CALL (htf={htf_u}, signal={d_u.signal.value})")
pull_d = down[:110] + [down[109] + 0.3 * i for i in range(1, 11)]
_, d_d = run(pull_d)
htf_d = dec._htf_trend(candles(pull_d))[0]
ok(buy_side(d_d) in (None, OptionType.PUT.value) or htf_d != "DOWN",
   f"a BUY in a 5-min downtrend is a PUT (htf={htf_d}, signal={d_d.signal.value})")
for name, d, h in (("uptrend", d_u, htf_u), ("downtrend", d_d, htf_d)):
    if h not in ("UP", "DOWN"):
        ok(buy_side(d) is None, f"{name} pullback with flat 5-min trend never BUYs")

print(f"\nTREND+PULLBACK STRATEGY SMOKE — {passed} passed, {failed} failed")
for f in fails:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
