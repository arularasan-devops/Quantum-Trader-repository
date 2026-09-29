"""Phase 32 — what a chosen T1 is worth on a premium, and which strike to pick.

No measurement here either. Given a measured underlying distance, the premium
percentage it implies is the inverse of Phase 31's bridge::

    premium_gain_pct = underlying_move_pct * delta / (premium_over_strike / 100)

which is why the *same* T1 is worth wildly different premium percentages
depending on the strike chosen: a premium worth 0.4% of spot moves ten times the
percentage of one worth 4%, for the same points of underlying.

Every row is labelled ``DERIVED_PREMIUM``. It ignores theta, changing implied
volatility, the bid/ask spread and the fact that premium response is not linear —
all of which make the real outcome worse, so it is an optimistic bound.
"""
from __future__ import annotations

from app.research.phase31 import premium_map
from app.research.phase32 import DELTA_BANDS, DERIVED_PREMIUM, PREMIUM_TARGETS_PCT

NOTE = premium_map.OPTIMISTIC_BOUND_NOTE


def premium_gain_pct(
    underlying_move_pct: float, premium_ratio_pct: float, delta: float
) -> float:
    """Premium percentage implied by an underlying move, at a stated delta."""
    if premium_ratio_pct <= 0:
        raise ValueError("premium/strike ratio must be positive")
    return underlying_move_pct * delta / (premium_ratio_pct / 100.0)


def strike_choice(
    instrument: str, measured_ratio: dict, t1_pct: float, *, reached_pct: float | None,
    median_minutes: float | None,
) -> list[dict]:
    """What the chosen T1 pays on a premium, per delta band.

    The reach rate and timing columns are the *measured* ones for that underlying
    distance, so the probability half of every row is real even though the premium
    half is arithmetic.
    """
    ratio_pct, ratio_source = premium_map.premium_over_strike_pct(
        instrument, measured_ratio
    )
    rows = []
    for delta in DELTA_BANDS:
        gain = premium_gain_pct(t1_pct, ratio_pct, delta)
        rows.append({
            "basis": DERIVED_PREMIUM,
            "instrument": instrument,
            "t1_underlying_pct": t1_pct,
            "delta": delta,
            "premium_over_strike_pct": round(ratio_pct, 4),
            "premium_ratio_source": ratio_source,
            "implied_premium_gain_pct": round(gain, 3),
            "measured_reach_pct": reached_pct,
            "measured_median_minutes": median_minutes,
            "hits_targets": [
                t for t in PREMIUM_TARGETS_PCT if gain >= t
            ],
            "note": NOTE,
        })
    return rows
