"""Lightweight walk-forward backtest.

Replays the futures candle history through the SAME indicator/vote logic the
live engine uses, and simulates directional option trades so you can see how the
weighted signals would have performed before trusting them live.

Honest scope: historical option chains are not stored, so option P&L is modelled
from the underlying move via a nominal ATM delta (0.5) × lot size. It validates
the *directional edge and stop/target discipline*, not exact premium ticks. Use
it as a sanity check on the signal logic, not a precise equity statement.
"""
from __future__ import annotations

import numpy as np

from app.config import settings
from app.engine import decision as eng
from app.market.instruments import InstrumentSpec
from app.models import BacktestResult, Candle

_NOMINAL_DELTA = 0.5


def run(candles: list[Candle], spec: InstrumentSpec) -> BacktestResult:
    n = len(candles)
    start = 60
    if n <= start + 10:
        return BacktestResult(
            instrument=spec.symbol, candles_tested=0, trades=0, wins=0, losses=0,
            win_rate=0.0, gross_pnl=0.0, net_pnl=0.0, max_drawdown=0.0,
            avg_hold_minutes=0.0, profit_factor=0.0, equity_curve=[0.0],
        )

    bar_minutes = settings.candle_interval_seconds / 60.0
    brokerage_per_trade = 40.0  # round-trip cost approximation

    equity = 0.0
    curve: list[float] = [0.0]
    peak = 0.0
    max_dd = 0.0
    wins = losses = trades = 0
    gross = 0.0
    hold_bars_total = 0

    in_trade = False
    entry_price = 0.0
    want_call = True
    stop = target = 0.0
    entry_i = 0

    for i in range(start, n):
        window = candles[: i + 1]
        _, h, low, c, _ = eng._arrays(window)
        close = float(c[-1])
        snap = eng.compute_indicators(window, [])
        atr_val = snap.atr or (0.004 * close)

        votes = eng._gather_votes(snap, close, 0.0, [])
        # primary evidence only (skip confirm-only indicators), mirroring decide()
        primary = [(d, w) for _, d, w, _, _, confirm_only in votes if not confirm_only]
        bull = sum(d * w for d, w in primary if d > 0)
        bear = sum(-d * w for d, w in primary if d < 0)
        tw = max(1.0, sum(w for _, w in primary))
        net = (bull - bear) / tw
        strength = abs(net)

        if not in_trade:
            trade_score = 100.0 * min(1.0, strength * 1.3)
            if trade_score >= 55 and strength >= 0.28:
                in_trade = True
                want_call = net > 0
                entry_price = close
                entry_i = i
                move = atr_val * (1.2 + strength)
                stop = entry_price - move if want_call else entry_price + move
                target = entry_price + 1.9 * move if want_call else entry_price - 1.9 * move
        else:
            hi, lo = float(h[-1]), float(low[-1])
            exit_price = None
            if want_call:
                if lo <= stop:
                    exit_price = stop
                elif hi >= target:
                    exit_price = target
            else:
                if hi >= stop:
                    exit_price = stop
                elif lo <= target:
                    exit_price = target
            flip = (want_call and net < -0.3) or (not want_call and net > 0.3)
            if exit_price is None and flip:
                exit_price = close

            if exit_price is not None:
                pts = (exit_price - entry_price) if want_call else (entry_price - exit_price)
                pnl = pts * _NOMINAL_DELTA * spec.lot_size - brokerage_per_trade
                equity += pnl
                gross += pts * _NOMINAL_DELTA * spec.lot_size
                curve.append(round(equity, 1))
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                trades += 1
                hold_bars_total += i - entry_i
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
                in_trade = False

    win_amt = sum(x for x in np.diff(curve) if x > 0) if len(curve) > 1 else 0.0
    loss_amt = -sum(x for x in np.diff(curve) if x < 0) if len(curve) > 1 else 0.0
    pf = float(win_amt / loss_amt) if loss_amt > 0 else float(win_amt and 99.0)

    return BacktestResult(
        instrument=spec.symbol,
        candles_tested=n - start,
        trades=trades,
        wins=wins,
        losses=losses,
        win_rate=round(wins / trades * 100, 1) if trades else 0.0,
        gross_pnl=round(gross, 1),
        net_pnl=round(equity, 1),
        max_drawdown=round(max_dd, 1),
        avg_hold_minutes=round(hold_bars_total / trades * bar_minutes, 1) if trades else 0.0,
        profit_factor=round(pf, 2),
        equity_curve=curve[-120:],
    )
