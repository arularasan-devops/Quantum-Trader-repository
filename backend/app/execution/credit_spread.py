"""Premium-SELLING engine — defined-risk credit spreads. PAPER ONLY.

Where the high win rate comes from
----------------------------------
Buying options fights theta every second. Selling a spread collects theta: the
position wins whenever price does NOT run past the short strike. Sell a
0.20-delta strike and it expires worthless ~80% of the time — so the ~80% win
rate is a property of the strike's delta, not a prediction. Sell 0.10 delta and
the nominal win rate goes to ~90% while the credit shrinks. The win rate is a
dial; the money is in the payoff.

What the backtest actually measured (1,243 NIFTY sessions, legs priced with
Black-Scholes on trailing realised vol as an IV proxy)::

    frictionless, intraday        81.4% win   PF 2.41
    + bid-ask on all four legs    66.1% win   PF 1.19
    held overnight (gap risk)     55.0% win   PF 0.65   <-- LOSES
    + severe IV spike             51.8% win   PF 0.57   <-- LOSES

Two hard rules follow directly from those rows and are enforced here:

1. INTRADAY ONLY. The position must be flat before the close. An overnight gap
   straight through the short strike cannot be managed, and it turns a winning
   strategy into a losing one.
2. DEFINED RISK ONLY. Every short leg is paired with a further-OTM long leg, so
   the worst case is (width - credit) and is known before entry. Naked selling
   is never constructed here — that is how accounts are wiped in one gap.

Everything this module produces is MODELLED, not a live option quote, and it is
ADVISORY: it never places an order. ``paper_only`` is hard-locked True.
"""
from __future__ import annotations

import numpy as np

from app.analysis import blackscholes as bs
from app.config import settings
from app.models import (
    Candle,
    CreditSpreadSignal,
    MarketStatus,
    SpreadLeg,
)

_TRADING_DAYS = 252.0
_MIN_VOL = 0.05
_MINUTES_PER_SESSION = 375.0


def _realised_vol(candles: list[Candle], lookback: int) -> float:
    """Trailing realised volatility, annualised, used as the IV proxy.

    Real implied vol is usually HIGHER than realised, so this understates the
    credit the market would actually pay — a conservative direction to err in.
    """
    closes = [c.close for c in candles[-(lookback + 1):]]
    if len(closes) < 3:
        return _MIN_VOL
    per_year = _TRADING_DAYS * _MINUTES_PER_SESSION
    return max(_MIN_VOL, bs.realised_vol(closes, periods_per_year=per_year))


def _structure_for(status: MarketStatus, htf_trend: str | None) -> tuple[str, list[str]]:
    """Sell the side price is moving AWAY from; sell both when it is going nowhere.

    A RANGING market is exactly the regime the option-BUYING engine has to skip,
    which is the real reason to run this alongside it rather than instead of it.
    """
    trend = (htf_trend or "").upper()
    if status in (MarketStatus.TRENDING, MarketStatus.BREAKOUT):
        if trend == "UP":
            return "BULL_PUT", ["PE"]
        if trend == "DOWN":
            return "BEAR_CALL", ["CE"]
    return "IRON_CONDOR", ["PE", "CE"]


def detect(
    candles: list[Candle],
    spot: float,
    strike_step: float,
    status: MarketStatus,
    htf_trend: str | None,
    minutes_to_close: int | None,
) -> CreditSpreadSignal:
    """Build a defined-risk credit-spread proposal. Never places an order."""
    sig = CreditSpreadSignal(enabled=settings.credit_spread_enabled, paper_only=True)
    if not settings.credit_spread_enabled:
        return sig
    if len(candles) < 60 or spot <= 0:
        sig.reason = "not enough data"
        return sig

    sig.minutes_to_close = minutes_to_close
    # INTRADAY ONLY: refuse to open anything that cannot be closed today. The
    # backtest is unambiguous that carrying one of these overnight loses money.
    if minutes_to_close is not None:
        if minutes_to_close <= settings.credit_spread_close_before_minutes:
            sig.state = "CLOSE"
            sig.reason = (
                f"{minutes_to_close}m to close — intraday only, flatten before the bell"
            )
            return sig
        if minutes_to_close < settings.credit_spread_min_minutes_open:
            sig.state = "BLOCKED"
            sig.reason = (
                f"only {minutes_to_close}m left — too little time for theta to pay"
            )
            return sig

    vol = _realised_vol(candles, settings.credit_spread_vol_lookback)
    sig.implied_vol = round(vol * 100, 1)
    years = max(1e-6, (minutes_to_close or _MINUTES_PER_SESSION) / _MINUTES_PER_SESSION
                / _TRADING_DAYS)

    structure, sides = _structure_for(status, htf_trend)
    sig.structure = structure

    width = settings.credit_spread_width_steps * strike_step
    legs: list[SpreadLeg] = []
    credit = 0.0
    max_loss = 0.0
    short_pe: float | None = None
    short_ce: float | None = None

    for side in sides:
        is_call = side == "CE"
        short_k = bs.strike_for_delta(
            spot, years, vol, settings.credit_spread_short_delta,
            is_call=is_call, step=strike_step,
        )
        long_k = short_k + width if is_call else short_k - width
        if long_k <= 0:
            continue
        p_short = bs.price(spot, short_k, years, vol, is_call=is_call)
        p_long = bs.price(spot, long_k, years, vol, is_call=is_call)
        leg_credit = p_short - p_long
        if leg_credit <= 0:
            continue
        legs.append(SpreadLeg(
            action="SELL", option_type=side, strike=round(short_k, 1),
            premium=round(p_short, 2),
            delta=round(bs.delta(spot, short_k, years, vol, is_call=is_call), 3),
        ))
        legs.append(SpreadLeg(
            action="BUY", option_type=side, strike=round(long_k, 1),
            premium=round(p_long, 2),
            delta=round(bs.delta(spot, long_k, years, vol, is_call=is_call), 3),
        ))
        credit += leg_credit
        max_loss += width - leg_credit
        if is_call:
            short_ce = short_k
        else:
            short_pe = short_k

    if not legs or credit <= 0:
        sig.reason = "no strike pays a worthwhile credit at this volatility"
        return sig

    # The bid-ask is crossed on every leg, at entry and again at exit. In the
    # backtest this alone took the win rate from 81% to 66%, so it is charged
    # up front rather than discovered in the P&L.
    friction = settings.credit_spread_slippage_per_leg * len(legs) * 2
    credit -= friction
    if credit <= 0:
        sig.reason = "the bid-ask would eat the entire credit"
        return sig

    sig.legs = legs
    sig.net_credit = round(credit, 2)
    sig.max_loss = round(max_loss, 2)
    sig.reward_risk = round(credit / max_loss, 3) if max_loss > 0 else None
    sig.stop_at = round(settings.credit_spread_stop_multiple * credit, 2)
    if short_pe is not None:
        sig.breakeven_low = round(short_pe - credit, 1)
    if short_ce is not None:
        sig.breakeven_high = round(short_ce + credit, 1)

    # Probability of the short strike expiring worthless, straight from delta.
    short_deltas = [abs(lg.delta) for lg in legs if lg.action == "SELL"]
    sig.win_probability = round((1.0 - float(np.sum(short_deltas))) * 100, 1)

    if sig.reward_risk is not None and sig.reward_risk < settings.credit_spread_min_rr:
        sig.state = "BLOCKED"
        sig.reason = (
            f"credit {credit:.1f} vs risk {max_loss:.1f} (R:R {sig.reward_risk}) "
            f"below the {settings.credit_spread_min_rr} floor — the tail is not paid for"
        )
        return sig

    sig.state = "PROPOSE"
    sig.reason = (
        f"{structure.replace('_', ' ').title()} — collect {credit:.1f}, risk capped at "
        f"{max_loss:.1f}, ~{sig.win_probability:.0f}% of expiring worthless. PAPER ONLY."
    )
    return sig
