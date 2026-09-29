"""Backtest the Candle-Flow engine over historical 1-minute candles.

It replays the REAL ``app.execution.flow.detect`` decision logic bar-by-bar (the
same "brain" the live Flow tab uses), reconstructing the leg lifecycle exactly
like ``flow_tracker`` does, and measures the outcome of every BUY/SWITCH the
engine would have fired.

Two outcome tracks are reported per trade:

* ``underlying_points`` — the REAL move of the futures underlying between the
  bar Flow said enter and the bar it said exit, signed for the chosen side
  (CE = +up, PE = +down). This is a fully real, measured number.

* ``premium_points`` — a MODELLED option-premium result. Angel does not give a
  reliable 1-year history of the exact CE/PE contract Flow would have picked, so
  the premium is approximated from the underlying move with a fixed delta. It is
  clearly labelled MODELLED and must not be read as a measured option P&L.

Read-only / offline. Places no orders and does not touch the live engine.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import numpy as np

from app.config import settings
from app.execution.flow import detect
from app.models import Candle, OptionQuote, OptionType


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    """Simple EMA over ``values`` (same length output). Used only by the
    optional higher-timeframe trend filter in the backtest — the live engine is
    untouched."""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values, dtype=float)
    acc = values[0]
    for i, v in enumerate(values):
        acc = alpha * v + (1 - alpha) * acc
        out[i] = acc
    return out

# --- modelled option premium (clearly an approximation, not real) -----------
# ATM premium travels ~delta points per 1 point of favourable underlying move.
_MODEL_DELTA = 0.6
_MODEL_TIME_VALUE = 180.0   # nominal ATM premium seed for MCX crude
_MODEL_FLOOR = 5.0


def _modelled_premium(spot: float, strike: float, side: OptionType) -> float:
    signed = (spot - strike) if side == OptionType.CALL else (strike - spot)
    return max(_MODEL_FLOOR, _MODEL_TIME_VALUE + _MODEL_DELTA * signed)


def _build_chain(root: str, spot: float, step: float, rec: dict | None) -> list[OptionQuote]:
    """Synthetic ATM chain for the current bar. Always exposes a fresh ATM CE/PE
    (for pre-entry side pick) and, while in a trade, the exact leg Flow holds."""
    atm = round(spot / step) * step
    quotes: list[OptionQuote] = []
    seen: set[str] = set()

    def add(strike: float, side: OptionType) -> None:
        sym = f"{root}{int(strike)}{side.value}"
        if sym in seen:
            return
        seen.add(sym)
        quotes.append(
            OptionQuote(
                symbol=sym,
                strike=float(strike),
                option_type=side,
                premium=round(_modelled_premium(spot, strike, side), 2),
                iv=0.0, delta=_MODEL_DELTA, gamma=0.0, theta=0.0, vega=0.0,
                oi=0, oi_change=0, volume=0,
            )
        )

    add(atm, OptionType.CALL)
    add(atm, OptionType.PUT)
    if rec is not None:
        side = OptionType.CALL if rec["side"] == OptionType.CALL.value else OptionType.PUT
        add(rec["strike"], side)
    return quotes


@dataclass
class Trade:
    side: str
    entry_ts: int
    exit_ts: int
    entry_spot: float
    exit_spot: float
    entry_premium: float
    peak_premium: float
    exit_premium: float
    close_reason: str
    underlying_points: float
    premium_points: float
    max_premium_gain: float
    hold_min: float


@dataclass
class BacktestResult:
    instrument: str
    interval: str
    bars: int
    first_ts: int
    last_ts: int
    trades: list[Trade] = field(default_factory=list)

    def stats(self) -> dict:
        return _summarise(self)


def _open_rec(root: str, side: OptionType, spot: float, step: float, candle: Candle) -> dict:
    strike = round(spot / step) * step
    prem = round(_modelled_premium(spot, strike, side), 2)
    return {
        "side": side.value,
        "strike": float(strike),
        "option_symbol": f"{root}{int(strike)}{side.value}",
        "entry_spot": float(spot),
        "entry_premium": prem,
        "peak_premium": prem,
        "last_premium": prem,
        "last_spot": float(spot),
        "open_ctime": candle.time,
        "ts_open": candle.time,
    }


def _finalize(rec: dict, reason: str, exit_ts: int, exit_spot: float) -> Trade:
    side = rec["side"]
    up = exit_spot - rec["entry_spot"]
    underlying = up if side == OptionType.CALL.value else -up
    prem_pts = rec["last_premium"] - rec["entry_premium"]
    return Trade(
        side=side,
        entry_ts=rec["ts_open"],
        exit_ts=exit_ts,
        entry_spot=round(rec["entry_spot"], 2),
        exit_spot=round(exit_spot, 2),
        entry_premium=round(rec["entry_premium"], 2),
        peak_premium=round(rec["peak_premium"], 2),
        exit_premium=round(rec["last_premium"], 2),
        close_reason=reason,
        underlying_points=round(underlying, 2),
        premium_points=round(prem_pts, 2),
        max_premium_gain=round(rec["peak_premium"] - rec["entry_premium"], 2),
        hold_min=round((exit_ts - rec["ts_open"]) / 60.0, 1),
    )


def run(
    candles: list[Candle],
    instrument: str,
    interval: str = "ONE_MINUTE",
    *,
    tail: int = 120,
    trend_align: bool = False,
    trend_fast: int = 20,
    trend_slow: int = 60,
) -> BacktestResult:
    """Replay Flow over ``candles`` and collect every trade it would have taken.

    ``detect`` only ever inspects the last few candles (current bar, the recent
    pattern window and the with-flow streak), so each bar is evaluated on a
    trailing window of ``tail`` candles. This keeps the replay O(n) instead of
    O(n^2) and does not change any decision (only the cosmetic ``bars_in_trade``
    counter, which never affects entries/exits)."""
    from app.market.instruments import REGISTRY

    spec = REGISTRY.get(instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else instrument

    res = BacktestResult(
        instrument=instrument,
        interval=interval,
        bars=len(candles),
        first_ts=candles[0].time if candles else 0,
        last_ts=candles[-1].time if candles else 0,
    )
    # Optional higher-timeframe trend filter: only ALLOW a fresh entry whose side
    # agrees with the EMA-fast-vs-EMA-slow trend. Exits are never blocked.
    closes = np.array([c.close for c in candles], dtype=float)
    if trend_align and len(closes) > trend_slow:
        ema_f = _ema(closes, trend_fast)
        ema_s = _ema(closes, trend_slow)
        uptrend = ema_f > ema_s
    else:
        uptrend = None

    rec: dict | None = None
    for i in range(len(candles)):
        window = candles[max(0, i + 1 - tail) : i + 1]
        bar = window[-1]
        spot = bar.close
        chain = _build_chain(root, spot, step, rec)
        sig = detect(window, chain, None, spot, rec)

        if rec is None:
            if sig.state in ("BUY", "SWITCH") and sig.side is not None:
                if uptrend is not None:
                    agrees = (sig.side == OptionType.CALL and uptrend[i]) or (
                        sig.side == OptionType.PUT and not uptrend[i]
                    )
                    if not agrees:
                        continue
                rec = _open_rec(root, sig.side, spot, step, bar)
            continue

        # advance premium travel on the tracked leg (same as flow_tracker)
        leg = next((q for q in chain if q.symbol == rec["option_symbol"]), None)
        if leg is not None:
            rec["last_premium"] = round(leg.premium, 2)
            rec["last_spot"] = float(spot)
            if leg.premium > rec["peak_premium"]:
                rec["peak_premium"] = round(leg.premium, 2)

        if sig.state == "SWITCH":
            res.trades.append(_finalize(rec, "SWITCH", bar.time, spot))
            rec = None
            if sig.side is not None:
                agrees = True
                if uptrend is not None:
                    agrees = (sig.side == OptionType.CALL and uptrend[i]) or (
                        sig.side == OptionType.PUT and not uptrend[i]
                    )
                if agrees:
                    rec = _open_rec(root, sig.side, spot, step, bar)
        elif sig.state == "EXIT":
            res.trades.append(_finalize(rec, "EXIT", bar.time, spot))
            rec = None

    return res


def _agg(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "n": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(values) * 100.0, 1),
        "sum": round(sum(values), 1),
        "avg": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "best": round(max(values), 2),
        "worst": round(min(values), 2),
        "gross_win": round(gross_win, 1),
        "gross_loss": round(gross_loss, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(statistics.mean(values), 2),
    }


def _summarise(res: BacktestResult) -> dict:
    trades = res.trades
    underlying = [t.underlying_points for t in trades]
    premium = [t.premium_points for t in trades]
    holds = [t.hold_min for t in trades]
    switches = sum(1 for t in trades if t.close_reason == "SWITCH")
    return {
        "instrument": res.instrument,
        "interval": res.interval,
        "bars": res.bars,
        "first_ts": res.first_ts,
        "last_ts": res.last_ts,
        "num_trades": len(trades),
        "switches": switches,
        "avg_hold_min": round(statistics.mean(holds), 1) if holds else None,
        "config": {
            "flow_min_body_frac": settings.flow_min_body_frac,
            "flow_confirm_candles": settings.flow_confirm_candles,
            "flow_giveback_points": settings.flow_giveback_points,
            "flow_sticky_candles": settings.flow_sticky_candles,
        },
        "underlying_points": _agg(underlying),
        "premium_points_MODELLED": _agg(premium),
        "model_note": (
            f"premium_points are MODELLED (delta={_MODEL_DELTA}, no theta decay); "
            "underlying_points are the real measured futures move."
        ),
    }
