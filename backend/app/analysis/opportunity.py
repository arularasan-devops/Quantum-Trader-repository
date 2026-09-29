"""Opportunity score — is this instrument moving enough to be worth trading?

The journal showed the losing pattern plainly: signals fired on instruments
whose premium could not travel far enough to pay for the round trip, so a
correct directional call still banked +0.7% against an 8% stop. Volatility is
therefore scored *before* a BUY is surfaced, in the only unit that matters —
**how far the option premium can move, as a percentage of the premium** — not
in underlying points, which mean nothing across a ₹7,900 commodity and a ₹16
stock option.

Everything here is read-only and advisory. It never places a trade; it can only
withhold a BUY and say why.
"""
from __future__ import annotations

from app.config import settings
from app.market.instruments import get_spec
from app.models import Candle, MarketStatus, Snapshot

# An ATM option's premium moves roughly delta x the underlying. ATM delta sits
# near 0.5; deliberately conservative, since overstating it would let a quiet
# instrument through the gate.
_ATM_DELTA = 0.5

# How many ATRs a realistic intraday leg covers. Measured from the fresh signal
# export: the median resolved signal's best move was ~5.4% of premium and T1
# (~11%) was reached by 31% of them, which is the 2-3 ATR range on the
# underlying. Three is the honest middle, not the best case.
_LEG_ATRS = 3.0

_DEAD_REGIMES = {MarketStatus.LOW_VOLUME}


def _volume_surge(candles: list[Candle], look: int = 5, base: int = 30) -> float | None:
    """Recent volume against this instrument's own recent average.

    Relative to itself, never an absolute threshold — 2,000 lots is a surge on
    one contract and a dead tape on another.
    """
    vols = [float(c.volume or 0.0) for c in candles[-base:]]
    if len(vols) < look + 5 or sum(vols) <= 0:
        return None
    recent = sum(vols[-look:]) / look
    earlier = vols[:-look]
    avg = sum(earlier) / len(earlier) if earlier else 0.0
    if avg <= 0:
        return None
    return round(recent / avg, 2)


def _cost_pct_of_premium(premium: float, lot_size: int) -> float:
    """Round-trip cost as a percentage of the premium paid.

    Brokerage is per order and STT is charged on the sell side, so the same
    rupee cost is a rounding error on an expensive contract and a wall on a
    cheap one — which is exactly why the floor below is a percentage.
    """
    if premium <= 0 or lot_size <= 0:
        return 0.0
    turnover = premium * lot_size
    brokerage = 2.0 * settings.brokerage_per_lot
    taxes = turnover * (settings.option_stt_sell_pct + 2.0 * settings.option_txn_pct) / 100.0
    return round(100.0 * (brokerage + taxes) / turnover, 3)


def round_trip_cost_pct(premium: float, lot_size: int) -> float:
    """Public name for the round-trip cost, so research shares the board's model.

    The per-instrument studies must charge exactly what the board charges, or a
    name looks uneconomic in one place and fine in the other.
    """
    return _cost_pct_of_premium(premium, lot_size)


def score(
    instrument: str,
    *,
    spot: float,
    atr: float,
    premium: float,
    adx: float | None,
    volume_surge: float | None = None,
    thin_volume: bool = False,
) -> dict:
    """Score how much profit potential these inputs represent.

    Takes plain numbers rather than a snapshot so the auto-trader can apply the
    SAME test the board shows — otherwise the board says "too quiet" while the
    bot buys, and neither can be trusted.

    Returns ``score`` (0-100), ``tradeable`` and a plain-language ``reason``.
    Never raises on missing data: what cannot be measured is reported as
    unmeasurable, not as an opportunity.
    """
    lot_size = max(1, get_spec(instrument).lot_size)
    surge = volume_surge

    atr_pct = round(100.0 * atr / spot, 3) if spot > 0 and atr > 0 else None
    # The headline number: how far the PREMIUM can travel on a normal leg.
    move_pct = (
        round(100.0 * _ATM_DELTA * atr * _LEG_ATRS / premium, 2)
        if premium > 0 and atr > 0
        else None
    )
    cost_pct = _cost_pct_of_premium(premium, lot_size) if premium > 0 else None
    # Net of costs, because a move that only pays the broker is not an
    # opportunity however large it looks.
    net_move_pct = round(move_pct - cost_pct, 2) if (move_pct is not None and cost_pct) else move_pct
    floor = settings.opportunity_min_move_pct
    blockers: list[str] = []
    if move_pct is None:
        blockers.append("no ATR or premium yet — cannot measure the move")
    elif (net_move_pct or 0.0) < floor:
        blockers.append(
            f"a normal leg moves the premium ~{net_move_pct:.1f}% net of costs, "
            f"under the {floor:g}% floor — too quiet to pay for the trade"
        )
    if premium > 0 and premium < settings.auto_trade_min_premium:
        blockers.append(
            f"premium ₹{premium:.1f} is below the ₹{settings.auto_trade_min_premium:g} floor"
        )
    if thin_volume:
        blockers.append("volume is thin")

    # --- score, 0-100, weighted toward the thing that actually pays ---
    # Movement 50, trend strength 25, participation 15, premium quality 10.
    move_score = 0.0
    if net_move_pct is not None and floor > 0:
        move_score = 50.0 * min(1.0, max(0.0, net_move_pct) / (floor * 2.0))
    adx_score = 25.0 * min(1.0, (adx or 0.0) / 30.0)
    surge_score = 15.0 * min(1.0, (surge or 0.0) / 1.5) if surge is not None else 0.0
    prem_score = 10.0 if premium >= settings.auto_trade_min_premium else 0.0
    total = round(move_score + adx_score + surge_score + prem_score, 1)

    tradeable = not blockers and total >= settings.opportunity_min_score
    if blockers:
        reason = blockers[0]
    elif not tradeable:
        reason = (
            f"opportunity score {total:.0f} is below the {settings.opportunity_min_score:g} "
            f"minimum — the move is there but weak"
        )
    else:
        reason = (
            f"a normal leg is worth ~{net_move_pct:.1f}% of premium net of costs"
            + (f", ADX {adx:.0f}" if adx else "")
            + (f", volume {surge:.1f}x its average" if surge else "")
        )

    return {
        "instrument": instrument,
        "score": total,
        "tradeable": tradeable,
        "reason": reason,
        "blockers": blockers,
        "atr_points": round(atr, 2) if atr else None,
        "atr_pct": atr_pct,
        "expected_move_pct": move_pct,
        "net_move_pct": net_move_pct,
        "cost_pct": cost_pct,
        "volume_surge": surge,
        "adx": round(adx, 1) if adx is not None else None,
        "premium": round(premium, 2) if premium else None,
        "min_move_pct": floor,
        "min_score": settings.opportunity_min_score,
    }


def evaluate(snap: Snapshot) -> dict:
    """Score the opportunity in a full snapshot (the board's view)."""
    dec = snap.decision
    return score(
        snap.instrument,
        spot=float(dec.spot_price or snap.futures_price or 0.0),
        atr=float(snap.indicators.atr or 0.0),
        premium=float(dec.current_premium or 0.0),
        adx=snap.indicators.adx,
        volume_surge=_volume_surge(snap.futures_candles),
        thin_volume=snap.market_status in _DEAD_REGIMES,
    )
