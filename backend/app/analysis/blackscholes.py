"""Black-Scholes pricing and greeks.

Used by the premium-SELLING engine, which needs a theoretical value for a leg
that is not in the live chain (a further-OTM hedge) and by the spread backtest,
where no historical option chain exists. Prices produced here are MODELLED, not
measured — every caller must label them as such.

Standard library only (``math``), so no new dependency.
"""
from __future__ import annotations

import math

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _d1_d2(spot: float, strike: float, t: float, vol: float, rate: float) -> tuple[float, float]:
    v = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / v
    return d1, d1 - v


def price(
    spot: float,
    strike: float,
    years: float,
    vol: float,
    *,
    is_call: bool,
    rate: float = 0.065,
) -> float:
    """Black-Scholes value of a European option. ``years`` is time to expiry."""
    if spot <= 0 or strike <= 0:
        return 0.0
    intrinsic = max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
    if years <= 0 or vol <= 0:
        return intrinsic
    d1, d2 = _d1_d2(spot, strike, years, vol, rate)
    disc = math.exp(-rate * years)
    if is_call:
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def delta(
    spot: float,
    strike: float,
    years: float,
    vol: float,
    *,
    is_call: bool,
    rate: float = 0.065,
) -> float:
    """Option delta. Positive for calls, negative for puts."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if years <= 0 or vol <= 0:
        itm = (spot > strike) if is_call else (spot < strike)
        return (1.0 if is_call else -1.0) if itm else 0.0
    d1, _ = _d1_d2(spot, strike, years, vol, rate)
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def strike_for_delta(
    spot: float,
    years: float,
    vol: float,
    target_delta: float,
    *,
    is_call: bool,
    step: float,
    rate: float = 0.065,
) -> float:
    """Strike whose |delta| is closest to ``target_delta``, snapped to ``step``.

    A credit spread is chosen by delta rather than by a fixed distance: delta is
    the market's own estimate of the probability the strike finishes in the
    money, so a 0.20-delta short leg is roughly an 80% chance of expiring
    worthless. That is exactly the knob that sets the win rate.
    """
    if step <= 0:
        step = 1.0
    best = round(spot / step) * step
    best_err = float("inf")
    span = max(1, int(6.0 * vol * math.sqrt(max(years, 1e-6)) * spot / step))
    for k in range(-span, span + 1):
        strike = best + k * step if k else best
        strike = round(spot / step) * step + k * step
        if strike <= 0:
            continue
        d = abs(delta(spot, strike, years, vol, is_call=is_call, rate=rate))
        err = abs(d - target_delta)
        if err < best_err:
            best_err, best = err, strike
    return best


def realised_vol(closes: list[float], *, periods_per_year: float) -> float:
    """Annualised realised volatility from a close series (log returns).

    Historical vol is used as the IV proxy in the backtest because no option
    chain history exists. Real IV usually trades ABOVE realised vol, which is
    precisely the seller's edge — so using realised vol here is the
    *conservative* choice: it under-states the credit collected.
    """
    if len(closes) < 3:
        return 0.0
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i] > 0 and closes[i - 1] > 0
    ]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * periods_per_year)
