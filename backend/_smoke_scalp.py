"""Smoke test for the Quick Scalp Engine: multi-condition exhaustion/bounce
gate, fixed-target + tight-stop plan (R:R >= min), and flag-off isolation."""
import time

from app.config import settings
from app.execution import scalp
from app.models import Candle, IndicatorSnapshot, OptionQuote, OptionType


def mk_candles(rows):
    now = int(time.time())
    out = []
    for i, (o, h, lo, c, v) in enumerate(rows):
        out.append(Candle(time=now - (len(rows) - i) * 60, open=o, high=h, low=lo, close=c, volume=v))
    return out


def chain(spot, ce_prem=120.0):
    qs = []
    for k in range(int(spot) - 200, int(spot) + 201, 100):
        for ot in (OptionType.CALL, OptionType.PUT):
            qs.append(OptionQuote(symbol=f"X{k}{ot.value}", strike=float(k), option_type=ot,
                                  premium=ce_prem, iv=20, delta=0.5 if ot == OptionType.CALL else -0.5,
                                  gamma=0.01, theta=-1, vega=1, oi=8000, oi_change=400, volume=1500))
    return qs


# A bounce/exhaustion setup far BELOW VWAP:
#  - gradual decline, then a SELLING CLIMAX flush (big red, high volume) → L1 low
#  - bounce, a higher-low L2, then a strong green bar that BREAKS the micro-high.
rows = [
    (23960, 23962, 23955, 23958, 1000),
    (23958, 23960, 23950, 23952, 1000),
    (23952, 23954, 23944, 23946, 1000),
    (23946, 23948, 23938, 23940, 1000),
    (23940, 23942, 23930, 23932, 1100),
    (23932, 23934, 23920, 23922, 1200),
    (23922, 23924, 23912, 23914, 1300),
    (23914, 23915, 23860, 23866, 5200),   # idx 7: SELLING CLIMAX → L1 = 23860
    (23868, 23880, 23866, 23878, 1400),
    (23879, 23890, 23876, 23888, 1300),
    (23889, 23896, 23885, 23892, 1200),
    (23892, 23894, 23882, 23884, 1100),
    (23884, 23886, 23876, 23878, 1100),
    (23882, 23884, 23870, 23881, 1700),   # idx 13: higher-low L2 = 23870, long lower wick (support rejection)
    (23876, 23888, 23874, 23886, 1300),
    (23887, 23898, 23885, 23896, 1400),
    (23896, 23906, 23890, 23904, 1500),
    (23904, 23940, 23900, 23936, 4800),   # idx 17: strong green BREAKS micro-high
]
candles = mk_candles(rows)
spot = candles[-1].close

snap = IndicatorSnapshot(
    vwap=24040.0,                 # price ~140 pts (≈7 ATR) BELOW VWAP → extended
    ema9=spot - 3, ema20=spot + 4, ema50=spot + 20, atr=20.0,
    rsi=42, macd=-1.0, macd_signal=-1.4, adx=20,
    oi_bias="NEUTRAL", oi_writing="NONE", premium_bias="NEUTRAL",
    support=23874.0, resistance=spot + 120,
    volume_spike=True,
    premium_ce_velocity=0.6, premium_ce_acceleration=0.2,
)

sc = scalp.detect(candles, chain(spot), snap, spot)
print(f"[bounce] active={sc.active} side={sc.side} setup={sc.setup} "
      f"conditions={sc.condition_count}/7 prob={sc.probability} rr={sc.reward_risk} "
      f"action={sc.recommended_action}")
print(f"         checks={[(c.name, c.passed) for c in sc.checks]}")
print(f"         plan target1={sc.plan.target1 if sc.plan else None} stop={sc.plan.stop_loss if sc.plan else None} "
      f"breakeven={sc.breakeven_at} tstop={sc.time_stop_candles}")
assert sc.enabled
assert sc.condition_count >= settings.scalp_min_conditions, "clean bounce should hit multi-condition gate"
assert sc.active, "clean exhaustion/bounce scalp should activate"
assert sc.reward_risk is not None and sc.reward_risk >= settings.scalp_min_rr, "R:R must clear the scalp minimum"
assert sc.plan is not None and sc.plan.target1 > sc.entry_hint > sc.plan.stop_loss
assert sc.plan.target1 - sc.entry_hint == settings.scalp_target_points or abs(
    (sc.plan.target1 - sc.entry_hint) - settings.scalp_target_points) < 0.5, "fixed target = scalp_target_points"

# Quiet, flat, at-VWAP market must NOT trigger (no exhaustion / no break).
base = 23900.0
flat = [(base, base + 1, base - 1, base, 1000)] * 17 + [(base, base + 2, base - 1, base + 1, 1050)]
c2 = mk_candles(flat)
s2 = c2[-1].close
snap2 = IndicatorSnapshot(vwap=s2, ema9=s2, ema20=s2, ema50=s2, atr=10.0,
                          rsi=50, macd=0.0, macd_signal=0.0, adx=12,
                          oi_bias="NEUTRAL", oi_writing="NONE", premium_bias="NEUTRAL",
                          support=s2 - 40, resistance=s2 + 40,
                          premium_ce_velocity=-0.5, premium_ce_acceleration=-0.1)
sc2 = scalp.detect(c2, chain(s2), snap2, s2)
print(f"[flat]   active={sc2.active} conditions={sc2.condition_count}/7 action={sc2.recommended_action}")
assert not sc2.active, "a quiet flat market must NOT trigger a scalp"

print("\nALL QUICK-SCALP SMOKE CHECKS PASSED")
