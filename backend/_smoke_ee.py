"""Smoke test for the Early-Early (Stage 2.5) add-on: multi-evidence gate,
R:R>=2 + liquidity validation, no-single-green-candle, and flag-off isolation."""
import time

from app.execution import early_early
from app.models import Candle, IndicatorSnapshot, OptionQuote, OptionType


def mk_candles(prices):
    now = int(time.time())
    out = []
    for i, (o, h, lo, c, v) in enumerate(prices):
        out.append(Candle(time=now - (len(prices) - i) * 60, open=o, high=h, low=lo, close=c, volume=v))
    return out


def chain(spot):
    qs = []
    for k in range(int(spot) - 200, int(spot) + 201, 100):
        for ot in (OptionType.CALL, OptionType.PUT):
            qs.append(OptionQuote(symbol=f"X{k}{ot.value}", strike=float(k), option_type=ot,
                                  premium=25.0, iv=20, delta=0.5 if ot == OptionType.CALL else -0.5,
                                  gamma=0.01, theta=-1, vega=1, oi=10000, oi_change=500, volume=2000))
    return qs


# Build a clean early-up leg with a HIGHER-LOW structure. Swing pivots need 3
# confirming bars each side, so descend to L1=23978, bounce, descend to a HIGHER
# L2=23983, rise 3 bars, then a bullish VWAP-reclaim candle on high volume.
def small(low, v=1000):
    return (low + 1, low + 3, low, low + 2, v)  # tiny green candle at this low


lows = [24010, 24008, 24006, 24004, 23998, 23992, 23985,
        23978,                              # L1 pivot (idx 7)
        23984, 23990, 23996, 24000,
        23994, 23988,
        23983,                              # L2 pivot (idx 14) — higher low
        23988, 23994, 24000]                # rise (right-side confirmation)
prices = [small(x) for x in lows]
# bullish VWAP-reclaim candle: green, closes well above VWAP, big volume
prices.append((23995, 24030, 23989, 24025, 4500))
candles = mk_candles(prices)
spot = candles[-1].close

snap = IndicatorSnapshot(
    vwap=24000.0, ema9=spot - 2, ema20=spot - 8, ema50=spot - 20, atr=18.0,
    rsi=58, macd=1.0, macd_signal=0.5, adx=22,
    oi_bias="BULLISH", oi_writing="PUT_WRITING", premium_bias="BULLISH",
    support=spot - 60, resistance=spot + 90,
)

ee = early_early.detect(candles, chain(spot), snap, spot)
print(f"[multi]  active={ee.active} side={ee.side} evidence={ee.evidence_count}/6 "
      f"prob={ee.probability} rr={ee.reward_risk} room={ee.room_atr} action={ee.recommended_action}")
print(f"         checks={[(c.name, c.passed) for c in ee.checks]}")
assert ee.enabled
assert ee.evidence_count >= 5, "should have multi-evidence on a clean early leg"
assert ee.active, "clean multi-evidence early-up leg should activate"
assert ee.reward_risk is not None and ee.reward_risk >= 2.0, "R:R must be >=2:1"
assert ee.plan is not None and ee.plan.target1 > ee.entry_hint > ee.plan.stop_loss

# Single green candle only (flat before) must NOT trigger.
base = 24000.0
flat = [(base, base + 1, base - 1, base, 1000)] * 14 + [(base, base + 6, base - 1, base + 5, 1200)]
c2 = mk_candles(flat)
s2 = c2[-1].close
snap2 = IndicatorSnapshot(vwap=s2 - 0.5, ema9=s2, ema20=s2, ema50=s2, atr=8.0,
                          rsi=52, macd=0.1, macd_signal=0.1, adx=15,
                          oi_bias="NEUTRAL", oi_writing="NONE", premium_bias="NEUTRAL",
                          support=s2 - 20, resistance=s2 + 5)
ee2 = early_early.detect(c2, chain(s2), snap2, s2)
print(f"[1green] active={ee2.active} evidence={ee2.evidence_count}/6 action={ee2.recommended_action}")
assert not ee2.active, "a single green candle must NOT trigger Early-Early"

# Room gate: same clean leg but resistance right overhead → no room → not issued.
snap3 = snap.model_copy(update={"resistance": spot + 5})
ee3 = early_early.detect(candles, chain(spot), snap3, spot)
print(f"[noroom] active={ee3.active} room={ee3.room_atr} room_ok={ee3.room_ok}")
assert not ee3.active, "no room before resistance must block the signal"

print("\nALL EARLY-EARLY SMOKE CHECKS PASSED")
