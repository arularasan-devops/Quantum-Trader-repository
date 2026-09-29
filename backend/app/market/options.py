"""Black-Scholes option pricing and Greeks.

Used by the simulated provider to synthesise a realistic option chain, and by
the analytics layer for expected-move / IV computations. When a real broker
feed is connected these values come from the exchange instead.
"""
from __future__ import annotations

import math

SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(spot: float, strike: float, r: float, sigma: float, t: float) -> tuple[float, float]:
    if sigma <= 0 or t <= 0:
        sigma = max(sigma, 1e-6)
        t = max(t, 1e-6)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return d1, d2


def price(spot: float, strike: float, r: float, sigma: float, t: float, is_call: bool) -> float:
    d1, d2 = _d1_d2(spot, strike, r, sigma, t)
    if is_call:
        val = spot * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
    else:
        val = strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    return max(val, 0.05)


def greeks(spot: float, strike: float, r: float, sigma: float, t: float, is_call: bool) -> dict[str, float]:
    d1, d2 = _d1_d2(spot, strike, r, sigma, t)
    pdf = _norm_pdf(d1)
    delta = _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0
    gamma = pdf / (spot * sigma * math.sqrt(t))
    vega = spot * pdf * math.sqrt(t) / 100.0  # per 1% vol
    theta_annual = (
        -(spot * pdf * sigma) / (2 * math.sqrt(t))
        - r * strike * math.exp(-r * t) * (_norm_cdf(d2) if is_call else _norm_cdf(-d2))
    )
    theta = theta_annual / 365.0  # per day
    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
    }


def implied_vol(target: float, spot: float, strike: float, r: float, t: float, is_call: bool) -> float:
    """Bisection solve for implied volatility."""
    lo, hi = 0.01, 3.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if price(spot, strike, r, mid, t, is_call) > target:
            hi = mid
        else:
            lo = mid
    return round(0.5 * (lo + hi), 4)
