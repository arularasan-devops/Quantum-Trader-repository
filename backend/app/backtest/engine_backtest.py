"""Backtest the FROZEN rule-engine as gated by Quantum Signal (READ-ONLY).

Unlike ``flow_backtest`` (which replays the fast 1-min Flow), this replays the
real decision pipeline the single-page dashboard uses:

    compute_indicators → classify_market → decide  →  Quantum-Signal gate

A trade is opened ONLY when the gate would show an ACTIONABLE BUY — i.e. the
frozen engine says BUY *and* all filters agree:
  * regime is TRENDING or BREAKOUT
  * ADX ≥ adx_min
  * a positive expected move exists
  * the 5-min trend backs the CE/PE side
  * the 15-min bias backs the CE/PE side

Each trade is then managed on the UNDERLYING using the engine's own regime-based
ATR stop/target plan (``_atr_multiples``) and its expected holding time, so the
measured win-rate / profit-factor reflect the tool's actual behaviour. Underlying
points are real; no options are modelled here and no orders are placed.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.engine.decision import (
    _atr_multiples,
    classify_market,
    compute_indicators,
    decide,
)
from app.engine.signal_gate import _timeframe_bias
from app.models import Candle, MarketStatus, Signal
from app.backtest.flow_backtest import _build_chain

_TREND_REGIMES = {MarketStatus.TRENDING, MarketStatus.BREAKOUT}


@dataclass
class ETrade:
    side: str
    entry_ts: int
    exit_ts: int
    entry_spot: float
    exit_spot: float
    points: float
    r_multiple: float
    exit_reason: str
    hold_min: float
    regime: str
    confidence: float


@dataclass
class EResult:
    instrument: str
    bars: int
    first_ts: int
    last_ts: int
    evaluated: int = 0
    buys_seen: int = 0
    actionable: int = 0
    trades: list[ETrade] = field(default_factory=list)

    def stats(self) -> dict:
        return _summarise(self)


def run(
    candles: list[Candle],
    instrument: str,
    *,
    adx_min: float = 20.0,
    tail: int = 180,
    target_idx: int = 0,
    max_hold_default: int = 30,
) -> EResult:
    from app.market.instruments import REGISTRY

    spec = REGISTRY.get(instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else instrument

    res = EResult(
        instrument=instrument,
        bars=len(candles),
        first_ts=candles[0].time if candles else 0,
        last_ts=candles[-1].time if candles else 0,
    )

    n = len(candles)
    i = tail
    while i < n - 1:
        window = candles[i + 1 - tail : i + 1]
        bar = window[-1]
        spot = bar.close
        chain = _build_chain(root, spot, step, None)
        snap = compute_indicators(window, chain, 0.0)
        status = classify_market(snap, False)
        dec, _ = decide(window, chain, snap, 0.0, False, status, False, None, 1.0, spot)
        res.evaluated += 1

        side = dec.option_type.value if dec.option_type else None
        if dec.signal != Signal.BUY or side is None:
            i += 1
            continue
        res.buys_seen += 1

        # --- Quantum-Signal gate ---
        htf = (dec.htf_trend or "").upper()
        bias15 = _timeframe_bias(window, 15)
        adx = snap.adx
        exp_move = dec.expected_move_points
        gate_ok = (
            status in _TREND_REGIMES
            and adx is not None and adx >= adx_min
            and exp_move is not None and exp_move > 0
            and ((side == "CE" and htf == "UP") or (side == "PE" and htf == "DOWN"))
            and ((side == "CE" and bias15 == "UP") or (side == "PE" and bias15 == "DOWN"))
        )
        if not gate_ok:
            i += 1
            continue
        res.actionable += 1

        # --- manage on the underlying with the engine's regime ATR plan ---
        atr = snap.atr or (0.004 * spot)
        stop_mult, tgt_mults = _atr_multiples(status)
        direction = 1.0 if side == "CE" else -1.0
        entry = spot
        stop = entry - direction * stop_mult * atr
        target = entry + direction * tgt_mults[target_idx] * atr
        risk = stop_mult * atr
        max_hold = dec.expected_holding_minutes or max_hold_default

        exit_spot = entry
        exit_reason = "hold_end"
        exit_ts = bar.time
        j = i + 1
        while j < n:
            cj = candles[j]
            if direction > 0:
                if cj.low <= stop:
                    exit_spot, exit_reason, exit_ts = stop, "stop", cj.time
                    break
                if cj.high >= target:
                    exit_spot, exit_reason, exit_ts = target, "target", cj.time
                    break
            else:
                if cj.high >= stop:
                    exit_spot, exit_reason, exit_ts = stop, "stop", cj.time
                    break
                if cj.low <= target:
                    exit_spot, exit_reason, exit_ts = target, "target", cj.time
                    break
            if (cj.time - bar.time) >= max_hold * 60:
                exit_spot, exit_reason, exit_ts = cj.close, "hold_end", cj.time
                break
            j += 1
        else:
            exit_spot = candles[-1].close
            exit_ts = candles[-1].time
            j = n - 1

        points = direction * (exit_spot - entry)
        res.trades.append(
            ETrade(
                side=side,
                entry_ts=bar.time,
                exit_ts=exit_ts,
                entry_spot=round(entry, 2),
                exit_spot=round(exit_spot, 2),
                points=round(points, 2),
                r_multiple=round(points / risk, 3) if risk > 0 else 0.0,
                exit_reason=exit_reason,
                hold_min=round((exit_ts - bar.time) / 60.0, 1),
                regime=status.value,
                confidence=round(dec.confidence, 1),
            )
        )
        i = j + 1  # no overlapping trades

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
        "avg": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "best": round(max(values), 2),
        "worst": round(min(values), 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
    }


def _summarise(res: EResult) -> dict:
    import datetime as _dt

    trades = res.trades
    pts = [t.points for t in trades]
    rs = [t.r_multiple for t in trades]
    holds = [t.hold_min for t in trades]
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    # walk-forward: points/PF per calendar year (out-of-sample robustness check)
    by_year: dict[str, list[float]] = {}
    for t in trades:
        yr = _dt.datetime.fromtimestamp(t.entry_ts).strftime("%Y")
        by_year.setdefault(yr, []).append(t.points)
    yearly = {yr: _agg(v) for yr, v in sorted(by_year.items())}

    return {
        "yearly": yearly,
        "instrument": res.instrument,
        "bars": res.bars,
        "first_ts": res.first_ts,
        "last_ts": res.last_ts,
        "evaluated": res.evaluated,
        "buys_seen": res.buys_seen,
        "actionable": res.actionable,
        "num_trades": len(trades),
        "avg_hold_min": round(statistics.mean(holds), 1) if holds else None,
        "exit_reasons": reasons,
        "underlying_points": _agg(pts),
        "r_multiple": _agg(rs),
    }
