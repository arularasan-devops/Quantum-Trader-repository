"""READ-ONLY walk-forward backtest for the SUPERTREND-FLIP entry.

Replays the real decision pipeline over cached 1-min candles and takes a trade
whenever the dashboard would show an ACTIONABLE BUY, exactly as
``ignition_backtest.py`` does, so the numbers are comparable with the baseline.

Beyond win rate / profit factor it measures the thing actually complained about:
HOW EARLY the entry lands. ``mfe_capture`` is the fraction of the subsequent
favourable excursion that the entry still had left in front of it — a late entry
near the peak captures little of it even when the trade wins.

Trades are managed on the UNDERLYING with the engine's regime ATR plan, so these
are MEASURED underlying points; no option theta, bid-ask or fill is modelled.

    .venv/bin/python supertrend_backtest.py --instrument NIFTY --limit 150000
"""
from __future__ import annotations

import argparse

from app.backtest import angel_history as ah
from app.backtest.flow_backtest import _build_chain
from app.config import settings
from app.engine.decision import (
    _atr_multiples,
    classify_market,
    compute_indicators,
    decide,
)
from app.engine.signal_gate import _timeframe_bias
from app.market.instruments import REGISTRY
from app.models import MarketStatus, Signal

_TREND = {MarketStatus.TRENDING, MarketStatus.BREAKOUT}


def _pf(vals):
    win = sum(v for v in vals if v > 0)
    loss = -sum(v for v in vals if v <= 0)
    return round(win / loss, 3) if loss > 0 else None


def _stats(vals, captures):
    if not vals:
        return None
    wins = sum(1 for x in vals if x > 0)
    gross_w = sum(v for v in vals if v > 0)
    gross_l = -sum(v for v in vals if v <= 0)
    # equity curve max drawdown, in points
    peak = run = dd = 0.0
    for v in vals:
        run += v
        peak = max(peak, run)
        dd = min(dd, run - peak)
    return {
        "trades": len(vals),
        "win_rate": round(wins / len(vals) * 100, 1),
        "pf": _pf(vals),
        "total": round(sum(vals), 1),
        "avg": round(sum(vals) / len(vals), 2),
        "avg_win": round(gross_w / wins, 2) if wins else 0.0,
        "avg_loss": round(gross_l / (len(vals) - wins), 2) if len(vals) - wins else 0.0,
        "max_dd": round(dd, 1),
        "mfe_capture": round(sum(captures) / len(captures) * 100, 1) if captures else None,
    }


def run(
    candles,
    instrument,
    *,
    supertrend,
    period=10,
    multiplier=3.0,
    require_htf=False,
    adx_min=20.0,
    tail=180,
):
    """Replay and return ``{trigger: ([points], [mfe_capture])}``."""
    settings.supertrend_entry_enabled = supertrend
    settings.supertrend_period = period
    settings.supertrend_multiplier = multiplier
    settings.supertrend_require_htf = require_htf
    settings.ignition_entry_enabled = False
    settings.reversal_entry_enabled = False
    settings.min_reward_risk = 1.2
    settings.veto_premium_explosion = True

    spec = REGISTRY.get(instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else instrument

    n = len(candles)
    i = tail
    out: dict[str, tuple[list, list]] = {}
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

        trigger = (dec.entry_trigger or "NONE").upper()
        if trigger == "SUPERTREND":
            # The flip is a trend-CHANGE signal, so the old trend's gates are
            # deliberately not applied (mirrors signal_gate).
            actionable = True
        else:
            htf = (dec.htf_trend or "").upper()
            bias15 = _timeframe_bias(window, 15)
            adx = snap.adx
            exp_move = dec.expected_move_points
            actionable = (
                status in _TREND
                and adx is not None and adx >= adx_min
                and exp_move is not None and exp_move > 0
                and ((side == "CE" and htf == "UP") or (side == "PE" and htf == "DOWN"))
                and ((side == "CE" and bias15 == "UP") or (side == "PE" and bias15 == "DOWN"))
            )
        if not actionable:
            i += 1
            continue

        atr = snap.atr or (0.004 * spot)
        stop_mult, tgt_mults = _atr_multiples(status)
        direction = 1.0 if side == "CE" else -1.0
        entry = spot
        stop = entry - direction * stop_mult * atr
        target = entry + direction * tgt_mults[0] * atr
        max_hold = dec.expected_holding_minutes or 30

        exit_spot = entry
        best = entry  # most favourable price reached while in the trade
        j = i + 1
        while j < n:
            cj = candles[j]
            best = max(best, cj.high) if direction > 0 else min(best, cj.low)
            if direction > 0:
                if cj.low <= stop:
                    exit_spot = stop
                    break
                if cj.high >= target:
                    exit_spot = target
                    break
            else:
                if cj.high >= stop:
                    exit_spot = stop
                    break
                if cj.low <= target:
                    exit_spot = target
                    break
            if (cj.time - bar.time) >= max_hold * 60:
                exit_spot = cj.close
                break
            j += 1
        else:
            exit_spot = candles[-1].close
            j = n - 1

        pts = direction * (exit_spot - entry)
        # How much of the favourable excursion was still ahead of the entry: the
        # excursion measured from the entry, against the excursion measured from
        # the swing extreme of the 10 bars before it (what an early entry would
        # have had). 100% = entered at the very start; low = entered near the top.
        pre = candles[max(0, i - 10) : i + 1]
        origin = min(x.low for x in pre) if direction > 0 else max(x.high for x in pre)
        full = direction * (best - origin)
        mine = direction * (best - entry)
        cap = (mine / full) if full > 1e-9 else 0.0
        rec = out.setdefault(trigger, ([], []))
        rec[0].append(pts)
        rec[1].append(max(0.0, min(1.0, cap)))
        i = j + 1

    return out


def _report(label, out):
    print(f"\n== {label} ==")
    allp, allc = [], []
    for _t, (p, c) in out.items():
        allp += p
        allc += c
    rows = [("ALL", allp, allc)] + [(t, p, c) for t, (p, c) in sorted(out.items())]
    print(
        f"   {'trigger':<12} {'trades':>6} {'win%':>6} {'PF':>7} "
        f"{'total':>9} {'avgW':>7} {'avgL':>7} {'maxDD':>8} {'earliness':>10}"
    )
    for name, p, c in rows:
        s = _stats(p, c)
        if not s:
            print(f"   {name:<12} (no trades)")
            continue
        print(
            f"   {name:<12} {s['trades']:>6} {s['win_rate']:>6} {str(s['pf']):>7} "
            f"{s['total']:>9} {s['avg_win']:>7} {s['avg_loss']:>7} {s['max_dd']:>8} "
            f"{str(s['mfe_capture']) + '%':>10}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NIFTY")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--limit", type=int, default=0, help="use only the LAST N candles")
    ap.add_argument("--split", action="store_true", help="walk-forward: tune on 1st half, score on 2nd")
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if args.limit and len(candles) > args.limit:
        candles = candles[-args.limit :]
    print(f"{args.instrument}: {len(candles):,} candles")

    if args.split:
        mid = len(candles) // 2
        parts = [("IN-SAMPLE (1st half)", candles[:mid]), ("OUT-OF-SAMPLE (2nd half)", candles[mid:])]
    else:
        parts = [("FULL", candles)]

    for pname, part in parts:
        _report(f"{pname} — BASELINE (supertrend off)", run(part, args.instrument, supertrend=False))
        for period, mult in ((10, 3.0), (10, 2.0), (7, 3.0)):
            _report(
                f"{pname} — SUPERTREND p={period} m={mult} (no HTF filter)",
                run(part, args.instrument, supertrend=True, period=period, multiplier=mult),
            )
        _report(
            f"{pname} — SUPERTREND p=10 m=3.0 + HTF filter",
            run(part, args.instrument, supertrend=True, require_htf=True),
        )


if __name__ == "__main__":
    main()
