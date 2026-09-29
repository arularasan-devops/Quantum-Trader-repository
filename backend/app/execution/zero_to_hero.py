"""Zero-to-Hero expiry sleeve — SPECULATIVE, HIGH RISK, isolated budget.

A deliberately tiny punt taken ONLY on expiry day: buy a cheap far-OTM option in
the day's bias direction. On a strong expiry-day trend such a leg can multiply
many times over; far more often it decays to ZERO. It has **no measured edge and
no backtest** — it is NOT the safe strategy and is walled off from the core
capital by a fixed daily budget (``zero_to_hero_budget``) that is the most it can
ever lose in a day.

This module is pure/read-only: it inspects the option chain and the day's bias
and returns a suggested hero leg (or an inactive reason). It never places an
order itself — execution is handled (and gated) by the caller.
"""
from __future__ import annotations

from app.config import settings
from app.market.instruments import get_spec
from app.models import Decision, OptionQuote, OptionType, ZeroToHero


def _direction(decision: Decision) -> OptionType | None:
    """CE / PE for the day from the validated bias. Prefers the engine's own
    directional call; falls back to the higher-timeframe trend. SIDEWAYS / no
    clean direction → None (no punt — the sleeve stays out)."""
    if decision.option_type is not None:
        return decision.option_type
    trend = (decision.htf_trend or "").upper()
    if trend == "UP":
        return OptionType.CALL
    if trend == "DOWN":
        return OptionType.PUT
    return None


def _pick_leg(
    chain: list[OptionQuote],
    spot: float,
    side: OptionType,
    step: float,
) -> OptionQuote | None:
    """Cheapest far-OTM leg of the wanted side within the premium window and at
    least ``otm_steps`` strikes out of the money."""
    lo, hi = settings.zero_to_hero_min_premium, settings.zero_to_hero_max_premium
    reach = max(1, settings.zero_to_hero_otm_steps) * max(1.0, step)
    if side == OptionType.CALL:
        target = spot + reach
        legs = [q for q in chain if q.option_type == OptionType.CALL and q.strike >= target]
    else:
        target = spot - reach
        legs = [q for q in chain if q.option_type == OptionType.PUT and q.strike <= target]
    legs = [q for q in legs if lo <= q.premium <= hi]
    if not legs:
        return None
    # closest to the far-OTM target, then cheapest — a true lottery ticket
    legs.sort(key=lambda q: (abs(q.strike - target), q.premium))
    return legs[0]


def evaluate(
    instrument: str,
    chain: list[OptionQuote],
    spot: float,
    decision: Decision,
    is_expiry_day: bool,
    budget_left: float,
) -> ZeroToHero:
    """Build the hero call for the day, or an inactive reason. ``budget_left`` is
    the remaining sleeve budget after anything already spent today."""
    budget = round(settings.zero_to_hero_budget, 0)
    inactive = ZeroToHero(active=False, is_expiry_day=is_expiry_day, budget=budget)

    if not settings.zero_to_hero_enabled:
        return inactive.model_copy(update={"reason": "Zero-to-Hero sleeve is off"})
    if settings.zero_to_hero_expiry_only and not is_expiry_day:
        return inactive.model_copy(update={"reason": "Not expiry day — sleeve waits for expiry"})
    if budget_left <= 0:
        return inactive.model_copy(update={"reason": "Daily sleeve budget already spent"})

    side = _direction(decision)
    if side is None:
        return inactive.model_copy(
            update={"reason": "No clean direction yet — no punt in a sideways tape"}
        )

    spec = get_spec(instrument)
    leg = _pick_leg(chain, spot, side, spec.strike_step)
    if leg is None:
        return inactive.model_copy(
            update={"reason": "No cheap far-OTM leg in the premium window"}
        )

    lot_size = max(1, spec.lot_size)
    per_lot_cost = leg.premium * lot_size
    lots = int(budget_left // per_lot_cost)
    if lots < 1:
        return inactive.model_copy(
            update={
                "reason": (
                    f"One lot (₹{per_lot_cost:,.0f}) exceeds the remaining "
                    f"₹{budget_left:,.0f} sleeve budget"
                ),
                "side": side,
            }
        )
    cost = round(per_lot_cost * lots, 0)
    return ZeroToHero(
        active=True,
        is_expiry_day=is_expiry_day,
        reason="Expiry-day hero punt (speculative)",
        side=side,
        option_symbol=leg.symbol,
        strike=leg.strike,
        premium=round(leg.premium, 1),
        lot_size=lot_size,
        lots=lots,
        budget=budget,
        cost=cost,
        max_loss=cost,
        note="HIGH RISK — cheap far-OTM leg; usually expires worthless. Never the core strategy.",
    )
