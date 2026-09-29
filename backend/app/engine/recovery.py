"""Recovery engine.

Given an open long-option position that is under water, estimate the
probability the premium recovers to break-even before it decays / expires,
the expected time to recover, and a HOLD vs EXIT recommendation.

The model blends:
  - how far under water the position is (drawdown vs ATR of the premium),
  - remaining directional edge from the decision engine,
  - theta decay pressure (time works against long options),
  - underlying momentum in the option's favour.
"""
from __future__ import annotations

import math

from app.models import RecoveryAnalysis


def analyse(
    entry_premium: float,
    current_premium: float,
    directional_edge: float,  # -1..1, +ve means underlying favours the option
    theta_per_day: float,
    minutes_held: int,
    minutes_to_expiry: int,
) -> RecoveryAnalysis:
    drawdown_pct = (current_premium - entry_premium) / entry_premium
    # base logistic on directional edge and drawdown depth
    x = 3.2 * directional_edge + 2.5 * drawdown_pct + 0.4
    prob = 1.0 / (1.0 + math.exp(-x))

    # theta drag: the longer to recover and the heavier the decay, the worse
    daily_decay_pct = abs(theta_per_day) / max(current_premium, 1e-6)
    prob *= max(0.2, 1.0 - daily_decay_pct * 1.5)

    # expiry pressure
    if minutes_to_expiry < 120:
        prob *= 0.7

    prob = max(0.02, min(0.97, prob))

    gap_pct = max(0.0, -drawdown_pct)
    # expected minutes to recover scales with gap and inverse of edge
    edge_speed = max(0.05, (directional_edge + 1.0) / 2.0)
    exp_minutes = int(min(minutes_to_expiry, 8 + gap_pct * 900 / edge_speed))

    action = "HOLD" if prob >= 0.55 and minutes_to_expiry > 60 else "EXIT"
    return RecoveryAnalysis(
        in_position=True,
        entry_premium=round(entry_premium, 1),
        current_premium=round(current_premium, 1),
        unrealized_pct=round(drawdown_pct * 100, 2),
        recovery_probability=round(prob, 3),
        expected_recovery_minutes=exp_minutes,
        recommended_action=action,
    )
