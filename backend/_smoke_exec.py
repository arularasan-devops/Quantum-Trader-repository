"""Smoke test for the Phase 3.6 Execution Intelligence Layer (advisory)."""
from app.config import settings
from app.execution import execution_validator
from app.models import Decision, IndicatorSnapshot, OptionQuote, OptionType, Signal

settings.execution_intelligence_enabled = True


def mkq(sym, strike, prem, delta, gamma=0.002, theta=-4, vega=8, oi=100000, vol=50000):
    return OptionQuote(symbol=sym, strike=strike, option_type=OptionType.CALL,
                       premium=prem, iv=0.2, delta=delta, gamma=gamma, theta=theta,
                       vega=vega, oi=oi, oi_change=0, volume=vol)


chain = [
    mkq("N25100CE", 25100, 210, 0.62, oi=80000, vol=40000),
    mkq("N25150CE", 25150, 165, 0.55, oi=150000, vol=90000),
    mkq("N25200CE", 25200, 120, 0.48, oi=120000, vol=70000),
    mkq("N25250CE", 25250, 82, 0.38, oi=60000, vol=30000),
]

# Case A: entry NOT extended, decent buffer -> ENTER_NOW-ish.
snapA = IndicatorSnapshot(vwap=25190, atr=40, ema20=25185, support=25120, resistance=25320)
decA = Decision(signal=Signal.BUY, confidence=84, signal_strength=70, trade_quality="A",
                recommended_option="N25200CE", option_type=OptionType.CALL,
                current_premium=120.0, spot_price=25200.0, underlying_stop=25160.0,
                stop_loss=95.0, target1=170.0)
ei = execution_validator.evaluate(decA, chain, snapA, vix=12.5)
print(f"A action={ei.recommended_action} grade={ei.grade} timing={ei.entry_timing} "
      f"strikeQ={ei.strike_quality} survival={ei.trade_survival} pullback%={ei.pullback_probability}")
print(f"A strike changed={ei.strike.changed} rec={ei.strike.recommended_symbol}")
print(f"A reasons={ei.reasons[:2]}")
assert ei.enabled and decA.signal == Signal.BUY  # never changes direction

# Case B: price extended far above VWAP -> WAIT_FOR_RETRACEMENT.
snapB = IndicatorSnapshot(vwap=25100, atr=30, ema20=25110, support=25050, resistance=25400,
                          breakout="BREAKOUT")
decB = Decision(signal=Signal.BUY, confidence=84, signal_strength=70, trade_quality="A",
                recommended_option="N25200CE", option_type=OptionType.CALL,
                current_premium=120.0, spot_price=25200.0, underlying_stop=25170.0,
                stop_loss=95.0, target1=170.0)
eiB = execution_validator.evaluate(decB, chain, snapB, vix=12.5)
print(f"\nB action={eiB.recommended_action} grade={eiB.grade} timing={eiB.entry_timing} "
      f"pullback%={eiB.pullback_probability} betterEntry={eiB.entry.better_entry}")
print(f"B reasons={eiB.reasons[:2]}")
assert eiB.recommended_action in ("WAIT_FOR_RETRACEMENT", "CHANGE_STRIKE", "SKIP_TRADE")
assert decB.signal == Signal.BUY  # direction still unchanged

# Case C: tiny buffer + cheap option -> low survival -> SKIP/CHANGE.
snapC = IndicatorSnapshot(vwap=25198, atr=60, ema20=25150, support=24900, resistance=25400)
decC = Decision(signal=Signal.BUY, confidence=84, signal_strength=70, trade_quality="A",
                recommended_option="N25250CE", option_type=OptionType.CALL,
                current_premium=82.0, spot_price=25200.0, underlying_stop=25170.0,
                stop_loss=78.0, target1=95.0)
eiC = execution_validator.evaluate(decC, chain, snapC, vix=None)
vixc = next(c for c in eiC.survival.components if c.name == "VIX")
print(f"\nC action={eiC.recommended_action} survival={eiC.trade_survival} vix_available={vixc.available}")
print(f"C reasons={eiC.reasons[:2]}")
assert vixc.available is False  # honest: no VIX supplied

print("\nALL EXECUTION INTELLIGENCE CHECKS PASSED")
