"""Vectorised round-trip cost, identical to the §16 schedule in ``phase56.costs``.

The scalar model in ``app.research.phase56.costs`` is authoritative. This module
is a matrix form of the *same* schedule so a few million trades can be costed;
the smoke suite asserts agreement against the scalar function on sampled prices
and dates, so the two cannot drift apart silently.

Costs are charged on a fixed ₹1,00,000 notional per position, which is what makes
the flat components (₹20 brokerage per order, ₹13 DP per sell) a meaningful
fraction rather than an arbitrary one. Quantity is floored, so the notional is
never exceeded. Slippage is charged on both sides and never improves a fill.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from . import POSITION_NOTIONAL_INR
from ..costs import (
    BROKERAGE_REGIMES,
    DP_CHARGE_PER_SELL,
    EXCHANGE_CHARGE_REGIMES,
    GST,
    SEBI_FEE,
    SLIPPAGE_PER_SIDE,
    STAMP_DUTY_BUY,
    STT_DELIVERY,
)


def regime_arrays(dates: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-session brokerage flat, brokerage pct cap and exchange rate.

    Built once per study and passed in, because parsing 1,490 ISO dates per
    costed batch dominated the run before it was hoisted out.
    """
    parsed = np.array([dt.date.fromisoformat(str(day)) for day in dates])
    flat = np.zeros(len(parsed))
    pct = np.zeros(len(parsed))
    exchange = np.zeros(len(parsed))
    for start, value_flat, value_pct in BROKERAGE_REGIMES:
        mask = parsed >= start
        flat[mask] = value_flat
        pct[mask] = value_pct
    for start, rate in EXCHANGE_CHARGE_REGIMES:
        exchange[parsed >= start] = rate
    return flat, pct, exchange


def net_returns(
    *,
    entry_price: np.ndarray,
    exit_price: np.ndarray,
    buy_rate_index: np.ndarray,
    sell_rate_index: np.ndarray,
    dates: np.ndarray | None = None,
    rates: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    multiplier: float = 1.0,
    notional: float = POSITION_NOTIONAL_INR,
) -> np.ndarray:
    """Net return per trade as a fraction of the buy turnover.

    ``buy_rate_index`` / ``sell_rate_index`` are positions into ``dates``, so a
    trade that straddles a rate change is charged the buy-side regime on the buy
    and the sell-side regime on the sell rather than one blended guess.
    """
    if rates is None:
        if dates is None:
            raise ValueError("pass either dates or precomputed rates")
        rates = regime_arrays(dates)
    flat, pct, exchange = rates
    quantity = np.floor(notional / entry_price)
    quantity = np.where(quantity < 1, 1.0, quantity)
    buy_turnover = entry_price * quantity
    sell_turnover = exit_price * quantity
    turnover = buy_turnover + sell_turnover

    def brokerage(turnover_side, index):
        flat_side = flat[index]
        pct_side = pct[index]
        charged = np.minimum(flat_side, turnover_side * pct_side)
        return np.where(flat_side > 0, charged, 0.0)

    brk = brokerage(buy_turnover, buy_rate_index) + brokerage(sell_turnover, sell_rate_index)
    exch = buy_turnover * exchange[buy_rate_index] + sell_turnover * exchange[sell_rate_index]
    sebi = turnover * SEBI_FEE
    stt = turnover * STT_DELIVERY
    stamp = buy_turnover * STAMP_DUTY_BUY
    gst = (brk + exch + sebi) * GST
    dp = DP_CHARGE_PER_SELL * (1 + GST)
    slippage = turnover * SLIPPAGE_PER_SIDE

    total = (brk + exch + sebi + stt + stamp + gst + dp + slippage) * multiplier
    gross = sell_turnover - buy_turnover
    return (gross - total) / buy_turnover


def cost_fraction(
    *,
    entry_price: np.ndarray,
    exit_price: np.ndarray,
    buy_rate_index: np.ndarray,
    sell_rate_index: np.ndarray,
    dates: np.ndarray | None = None,
    rates: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    multiplier: float = 1.0,
) -> np.ndarray:
    """Round-trip cost as a fraction of buy turnover (for §16 attribution)."""
    gross = exit_price / entry_price - 1.0
    net = net_returns(
        entry_price=entry_price,
        exit_price=exit_price,
        buy_rate_index=buy_rate_index,
        sell_rate_index=sell_rate_index,
        dates=dates,
        rates=rates,
        multiplier=multiplier,
    )
    return gross - net
