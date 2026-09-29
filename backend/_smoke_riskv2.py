"""Smoke test for Risk Management v2 (stop refinement + pre-trade validator)."""
from app.engine import risk_v2
from app.models import OptionQuote, OptionType


def mkq(premium, delta, gamma, oi=100000, volume=50000):
    return OptionQuote(
        symbol="TEST24000CE", strike=24000, option_type=OptionType.CALL,
        premium=premium, iv=0.18, delta=delta, gamma=gamma, theta=-5, vega=8,
        oi=oi, oi_change=0, volume=volume,
    )


# 1. Healthy ATM call: delta 0.5, gamma small; underlying stop 20 pts.
q = mkq(120.0, 0.5, 0.002)
stop, src = risk_v2.refined_premium_stop(premium=120.0, underlying_stop_distance=20.0, quote=q)
print(f"1 refined stop={stop} src={src}")
v = risk_v2.validate_buy(premium=120.0, proposed_stop=stop, target1=160.0, quote=q)
print(f"1 approved={v.approved} rr={v.reward_risk} loss%={v.max_premium_loss_pct} rej={v.rejections}")
assert src == "delta+gamma"
assert v.approved, "healthy trade should pass"

# 2. Cheap low-delta option where linear stop would blow past premium -> reject.
q2 = mkq(6.0, 0.12, 0.02)
stop2, _ = risk_v2.refined_premium_stop(premium=6.0, underlying_stop_distance=80.0, quote=q2)
print(f"2 refined stop={stop2}")
v2 = risk_v2.validate_buy(premium=6.0, proposed_stop=stop2, target1=12.0, quote=q2)
print(f"2 approved={v2.approved} loss%={v2.max_premium_loss_pct} rej={v2.rejections}")
assert not v2.approved, "unrealistic stop must be rejected, not forced"

# 3. Poor reward:risk -> reject.
v3 = risk_v2.validate_buy(premium=100.0, proposed_stop=90.0, target1=105.0, quote=mkq(100, 0.5, 0.002))
print(f"3 approved={v3.approved} rr={v3.reward_risk} rej={v3.rejections}")
assert not v3.approved and v3.reward_risk is not None and v3.reward_risk < 1.2

# 4. Spread check skipped when no bid/ask.
v4 = risk_v2.validate_buy(premium=100.0, proposed_stop=80.0, target1=140.0, quote=mkq(100, 0.5, 0.002))
spread = next(c for c in v4.checks if c.name == "Spread")
print(f"4 spread available={spread.available} passed={spread.passed}")
assert spread.available is False

print("\nALL RISK V2 CHECKS PASSED")
