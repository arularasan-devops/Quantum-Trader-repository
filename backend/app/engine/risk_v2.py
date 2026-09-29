"""Risk Management v2 — stop-loss refinement + Pre-Trade Validation.

CONTROLLED, FEATURE-FLAGGED (``QT_RISK_V2_ENABLED``), FULLY REVERSIBLE.

This layer runs *after* the frozen Phase-1.5 engine has produced its decision.
It NEVER touches entry logic, confidence scoring, or the strategy rules — it only:

1. **Refines the premium stop** with a volatility/Greeks-aware model
   (Delta + Gamma where available) instead of the linear delta approximation.
2. **Validates the trade before allowing a BUY** — liquidity, spread,
   reward:risk, maximum premium loss, and stop validity. When a check fails the
   BUY is *rejected* (downgraded to AVOID by the caller) with a clear, human
   reason, rather than forcing an unrealistic stop.

Everything here is pure/functional and reads only values the engine already
produced (premium, stop, targets, ATR, and the option quote's Greeks/OI/volume).
It places no orders and writes no state.
"""
from __future__ import annotations

from app.config import settings
from app.models import OptionQuote, TradeValidation, ValidationCheck


def _premium_move_for_underlying(delta: float, gamma: float, move_pts: float) -> float:
    """Estimate an option's premium change for an ADVERSE underlying move of
    ``move_pts`` using a second-order (Delta + Gamma) Taylor expansion.

    For a long option an adverse move shrinks |delta| (gamma), so the true
    premium loss is smaller than the linear ``delta*move``. Using
    ``delta - 0.5*gamma*move`` as the effective delta over the move captures
    that curvature and avoids over-tight OR over-loose premium stops.
    """
    d = abs(delta)
    g = abs(gamma)
    # Average delta across the adverse move (delta decreases by gamma*move). The
    # 2nd-order Taylor term is only valid for small moves, so bound the gamma
    # reduction to at most HALF the delta term — beyond that the expansion would
    # absurdly claim the option barely loses value on a large adverse move.
    eff_delta = max(0.5 * d, d - 0.5 * g * move_pts)
    return max(0.0, eff_delta * move_pts)


def refined_premium_stop(
    *,
    premium: float,
    underlying_stop_distance: float,
    quote: OptionQuote | None,
) -> tuple[float, str]:
    """Volatility/Greeks-aware premium stop level (absolute premium, not distance).

    Returns ``(stop_premium, source)`` where source is "delta+gamma" / "delta".
    The stop is NOT floored to an unrealistic value here — the validator decides
    whether the resulting stop is acceptable.
    """
    if quote is not None and quote.gamma:
        drop = _premium_move_for_underlying(quote.delta, quote.gamma, underlying_stop_distance)
        source = "delta+gamma"
    elif quote is not None and quote.delta:
        drop = abs(quote.delta) * underlying_stop_distance
        source = "delta"
    else:
        # No Greeks at all — fall back to the engine's own linear estimate via a
        # neutral 0.4 delta (documented approximation).
        drop = 0.4 * underlying_stop_distance
        source = "delta(approx)"
    return round(premium - drop, 1), source


def validate_buy(
    *,
    premium: float,
    proposed_stop: float,
    target1: float | None,
    quote: OptionQuote | None,
    bid: float | None = None,
    ask: float | None = None,
) -> TradeValidation:
    """Pre-Trade Validator. Returns a TradeValidation; ``approved`` is False when
    any enforced check fails. Every check carries a human-readable ``detail``.

    Checks: Stop Validity, Max Premium Loss, Reward:Risk, Liquidity, Spread.
    Missing-input checks are marked ``available=False`` and skipped (never a
    silent fail).
    """
    checks: list[ValidationCheck] = []
    rejections: list[str] = []

    max_loss_pct = settings.risk_v2_max_premium_loss_pct
    min_rr = settings.risk_v2_min_reward_risk

    # ---- 1. Stop validity: 0 < stop < premium ----
    stop_valid = proposed_stop is not None and 0.0 < proposed_stop < premium
    if stop_valid:
        detail = f"Stop {proposed_stop:.1f} is a valid level below premium {premium:.1f}."
    else:
        detail = (
            f"Stop {proposed_stop:.1f} is not a valid protective level for premium "
            f"{premium:.1f} (must be > 0 and < premium)."
        )
        rejections.append("Invalid stop: the computed stop is not below the premium — no real protection.")
    checks.append(ValidationCheck(name="Stop Validity", passed=stop_valid, detail=detail))

    # ---- 2. Max premium loss % ----
    loss_pct: float | None = None
    if premium > 0 and proposed_stop is not None:
        loss_pct = round((premium - proposed_stop) / premium * 100, 1)
        loss_ok = 0 < loss_pct <= max_loss_pct
        if loss_ok:
            detail = f"Stop risks {loss_pct:.1f}% of premium (≤ {max_loss_pct:.0f}% cap)."
        else:
            detail = f"Stop risks {loss_pct:.1f}% of premium (> {max_loss_pct:.0f}% cap)."
            rejections.append(
                f"Max premium loss exceeded: stop implies a {loss_pct:.0f}% loss, above the "
                f"{max_loss_pct:.0f}% limit."
            )
        checks.append(ValidationCheck(name="Max Premium Loss", passed=loss_ok, detail=detail))
    else:
        checks.append(ValidationCheck(
            name="Max Premium Loss", passed=False, available=False,
            detail="Cannot compute premium loss (missing premium/stop).",
        ))

    # ---- 3. Reward:Risk (Target 1 vs stop) ----
    rr: float | None = None
    if target1 is not None and proposed_stop is not None and premium - proposed_stop > 0:
        rr = round((target1 - premium) / (premium - proposed_stop), 2)
        rr_ok = rr >= min_rr
        if rr_ok:
            detail = f"Reward:Risk {rr:.2f} (≥ {min_rr:.1f} required)."
        else:
            detail = f"Reward:Risk {rr:.2f} (< {min_rr:.1f} required)."
            rejections.append(
                f"Poor reward:risk: {rr:.2f} is below the {min_rr:.1f} minimum for a BUY."
            )
        checks.append(ValidationCheck(name="Reward:Risk", passed=rr_ok, detail=detail))
    else:
        checks.append(ValidationCheck(
            name="Reward:Risk", passed=False, available=False,
            detail="Cannot compute reward:risk (missing target/stop).",
        ))

    # ---- 4. Liquidity (OI + volume) ----
    min_oi = settings.risk_v2_min_oi
    min_vol = settings.risk_v2_min_volume
    if min_oi <= 0 and min_vol <= 0:
        checks.append(ValidationCheck(
            name="Liquidity", passed=True, available=False,
            detail="Liquidity floors disabled (QT_RISK_V2_MIN_OI / _MIN_VOLUME = 0).",
        ))
    elif quote is None:
        checks.append(ValidationCheck(
            name="Liquidity", passed=False, available=False,
            detail="No option quote available to assess liquidity.",
        ))
    else:
        oi_ok = min_oi <= 0 or quote.oi >= min_oi
        vol_ok = min_vol <= 0 or quote.volume >= min_vol
        liq_ok = oi_ok and vol_ok
        detail = f"OI {quote.oi:,} (min {min_oi:,}), volume {quote.volume:,} (min {min_vol:,})."
        if not liq_ok:
            rejections.append(
                f"Illiquid option: OI {quote.oi:,} / volume {quote.volume:,} below the configured floor."
            )
        checks.append(ValidationCheck(name="Liquidity", passed=liq_ok, detail=detail))

    # ---- 5. Spread (only when the feed provides bid/ask) ----
    if bid is not None and ask is not None and ask > 0 and premium > 0:
        spread_pct = round((ask - bid) / premium * 100, 1)
        spread_ok = spread_pct <= settings.risk_v2_max_spread_pct
        detail = f"Spread {spread_pct:.1f}% of premium (≤ {settings.risk_v2_max_spread_pct:.0f}% cap)."
        if not spread_ok:
            detail = f"Spread {spread_pct:.1f}% of premium (> {settings.risk_v2_max_spread_pct:.0f}% cap)."
            rejections.append(
                f"Wide spread: {spread_pct:.1f}% of premium exceeds the "
                f"{settings.risk_v2_max_spread_pct:.0f}% limit — slippage risk."
            )
        checks.append(ValidationCheck(name="Spread", passed=spread_ok, detail=detail))
    else:
        checks.append(ValidationCheck(
            name="Spread", passed=True, available=False,
            detail="Bid/ask not provided by the feed — spread check skipped.",
        ))

    approved = len(rejections) == 0
    note = (
        "Trade approved by Risk v2 pre-trade validation."
        if approved
        else "Trade rejected by Risk v2 pre-trade validation (see reasons)."
    )
    return TradeValidation(
        enabled=True,
        approved=approved,
        checks=checks,
        rejections=rejections,
        max_premium_loss_pct=loss_pct,
        reward_risk=rr,
        note=note,
    )
