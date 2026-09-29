"""5-year parameter sweep for the frozen-engine + Quantum-Signal gate.

One expensive pass per instrument (the decision engine runs once per bar). For
every ACTIONABLE entry we record the entry ADX and simulate the exit under each
target choice (T1/T2/T3) with the engine's regime ATR stop. From that single
pass we aggregate all (adx_min x target) combinations offline — a higher adx_min
just filters recorded trades by their captured ADX; a target choice just picks
the recorded exit. READ-ONLY, underlying points, no orders.

Usage:
    .venv/bin/python engine_sweep.py --instrument NIFTY
"""
from __future__ import annotations

import argparse
import json
import statistics

from app.backtest.flow_backtest import _build_chain
from app.backtest.engine_backtest import _TREND_REGIMES
from app.engine.decision import _atr_multiples, classify_market, compute_indicators, decide
from app.engine.signal_gate import _timeframe_bias
from app.models import Candle, Signal

_ADX_LEVELS = [20.0, 25.0, 30.0]
_TARGETS = [0, 1, 2]  # T1, T2, T3


def _load(path: str) -> list[Candle]:
    out: list[Candle] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            out.append(
                Candle(
                    time=int(d["time"]),
                    open=float(d["open"]),
                    high=float(d["high"]),
                    low=float(d["low"]),
                    close=float(d["close"]),
                    volume=float(d.get("volume", 0.0)),
                )
            )
    return out


def _simulate_exit(
    candles: list[Candle], start_j: int, entry: float, direction: float,
    stop: float, target: float, max_hold_s: float, entry_ts: int,
) -> float:
    n = len(candles)
    j = start_j
    while j < n:
        cj = candles[j]
        if direction > 0:
            if cj.low <= stop:
                return direction * (stop - entry)
            if cj.high >= target:
                return direction * (target - entry)
        else:
            if cj.high >= stop:
                return direction * (stop - entry)
            if cj.low <= target:
                return direction * (target - entry)
        if (cj.time - entry_ts) >= max_hold_s:
            return direction * (cj.close - entry)
        j += 1
    return direction * (candles[-1].close - entry)


def run(path: str, instrument: str, tail: int = 180) -> list[dict]:
    from app.market.instruments import REGISTRY

    candles = _load(path)
    spec = REGISTRY.get(instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else instrument

    # each recorded entry: {adx, points_by_target: [t1,t2,t3], risk, year}
    entries: list[dict] = []
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

        side = dec.option_type.value if dec.option_type else None
        if dec.signal != Signal.BUY or side is None:
            i += 1
            continue
        htf = (dec.htf_trend or "").upper()
        bias15 = _timeframe_bias(window, 15)
        adx = snap.adx
        exp_move = dec.expected_move_points
        gate_ok = (
            status in _TREND_REGIMES
            and adx is not None and adx >= _ADX_LEVELS[0]
            and exp_move is not None and exp_move > 0
            and ((side == "CE" and htf == "UP") or (side == "PE" and htf == "DOWN"))
            and ((side == "CE" and bias15 == "UP") or (side == "PE" and bias15 == "DOWN"))
        )
        if not gate_ok:
            i += 1
            continue

        atr = snap.atr or (0.004 * spot)
        stop_mult, tgt_mults = _atr_multiples(status)
        direction = 1.0 if side == "CE" else -1.0
        entry = spot
        stop = entry - direction * stop_mult * atr
        risk = stop_mult * atr
        max_hold_s = (dec.expected_holding_minutes or 30) * 60.0

        pts_by_target = []
        for t_idx in _TARGETS:
            target = entry + direction * tgt_mults[t_idx] * atr
            pts = _simulate_exit(candles, i + 1, entry, direction, stop, target, max_hold_s, bar.time)
            pts_by_target.append(round(pts, 3))

        import datetime as _dt
        entries.append({
            "adx": float(adx),
            "risk": risk,
            "year": _dt.datetime.fromtimestamp(bar.time).strftime("%Y"),
            "pts": pts_by_target,
        })
        # advance past the T1 exit to avoid heavy overlap (approx; conservative)
        i += max(1, int(max_hold_s / 60))
    return entries


def _agg(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    gw = sum(wins)
    gl = -sum(losses)
    return {
        "n": len(vals),
        "win_rate_pct": round(len(wins) / len(vals) * 100, 1),
        "sum": round(sum(vals), 1),
        "avg": round(statistics.mean(vals), 3),
        "profit_factor": round(gw / gl, 3) if gl > 0 else None,
    }


def summarise(entries: list[dict]) -> dict:
    grid = {}
    for adx_min in _ADX_LEVELS:
        for t_idx in _TARGETS:
            sel = [e["pts"][t_idx] for e in entries if e["adx"] >= adx_min]
            grid[f"adx>={int(adx_min)}|T{t_idx+1}"] = _agg(sel)
    return grid


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", required=True)
    ap.add_argument("--path", default=None)
    args = ap.parse_args()
    path = args.path or f"data/backtest/{args.instrument}_ONE_MINUTE.jsonl"

    ent = run(path, args.instrument)
    grid = summarise(ent)
    out = {"instrument": args.instrument, "total_entries_adx20": len(ent), "grid": grid}
    print(json.dumps(out, indent=2))
    dst = f"data/backtest/{args.instrument}_engine_sweep.json"
    with open(dst, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"\nsaved -> {dst}")
