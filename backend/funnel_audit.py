"""OFFLINE signal-funnel audit — research only, never imported by the app.

Answers the question the live dashboard cannot: *when the engine says WAIT and
the move then happens anyway, which gate refused it?*

It replays cached 1-minute candles through the SAME `compute_indicators` /
`classify_market` / `decide` path the live engine uses, captures the gate trace
of every bar, and then measures what the market did over the following bars.

Honest scope — read this before quoting any number it prints:

* Option premiums are MODELLED from the underlying with a fixed ATM delta (the
  same approximation `flow_backtest` uses). Real chains for a 5-year window are
  not stored, so premium figures are directional estimates, not ticks. Theta,
  spread and IV changes are NOT modelled, which flatters every entry equally.
* Forward excursion is measured on the UNDERLYING and converted at that delta.
* Gate statistics are associations over historical bars, not a claim of causal
  edge, and every bar is treated as an independent observation even though
  neighbouring bars overlap heavily. Treat rates, not significance.

    .venv/bin/python funnel_audit.py --instrument CRUDEOIL --bars 40000 --stride 3
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict

from app.backtest import angel_history as ah
from app.config import settings
from app.engine.decision import (
    _gate_states,
    classify_market,
    compute_indicators,
    decide,
)
from app.market.instruments import REGISTRY
from app.models import Candle, OptionQuote, OptionType, Signal

# Same modelled-premium constants as app/backtest/flow_backtest.py, so the two
# research tools cannot disagree about what a premium was.
_MODEL_DELTA = 0.6
_MODEL_TIME_VALUE = 180.0
_MODEL_FLOOR = 5.0

# Lookback fed to the engine each bar. Long enough for the 5-min aggregation,
# EMA50 and the volume profile; short enough to keep a 40k-bar replay finite.
_WINDOW = 240


def _premium(spot: float, strike: float, side: OptionType) -> float:
    signed = (spot - strike) if side == OptionType.CALL else (strike - spot)
    return max(_MODEL_FLOOR, _MODEL_TIME_VALUE + _MODEL_DELTA * signed)


def _chain(root: str, spot: float, step: float) -> list[OptionQuote]:
    """A minimal ATM chain (CE + PE at the ATM strike and one strike either side)."""
    atm = round(spot / step) * step
    out: list[OptionQuote] = []
    for k in (atm - step, atm, atm + step):
        for side in (OptionType.CALL, OptionType.PUT):
            out.append(
                OptionQuote(
                    symbol=f"{root}{int(k)}{side.value}",
                    strike=float(k),
                    option_type=side,
                    premium=round(_premium(spot, k, side), 2),
                    iv=0.0, delta=_MODEL_DELTA if side == OptionType.CALL else -_MODEL_DELTA,
                    gamma=0.0, theta=0.0, vega=0.0, oi=0, oi_change=0, volume=0,
                )
            )
    return out


def _forward(candles: list[Candle], i: int, horizon: int, want_call: bool) -> tuple[float, float]:
    """(favourable, adverse) excursion in UNDERLYING points over the next bars."""
    entry = candles[i].close
    seg = candles[i + 1 : i + 1 + horizon]
    if not seg:
        return 0.0, 0.0
    hi = max(c.high for c in seg)
    lo = min(c.low for c in seg)
    if want_call:
        return hi - entry, entry - lo
    return entry - lo, hi - entry


def _first_hit(
    candles: list[Candle], i: int, horizon: int, want_call: bool,
    strike: float, side: OptionType, stop: float, target: float,
) -> str:
    """Which of the modelled premium's stop / target is reached first.

    Bar order inside a candle is unknown, so when a single bar spans both levels
    the STOP is assumed first. Assuming the target would inflate every result.
    """
    for c in candles[i + 1 : i + 1 + horizon]:
        prem_lo = _premium(c.low if want_call else c.high, strike, side)
        prem_hi = _premium(c.high if want_call else c.low, strike, side)
        if prem_lo <= stop:
            return "STOP"
        if prem_hi >= target:
            return "TARGET"
    return "OPEN"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--bars", type=int, default=40000, help="most recent N bars to replay")
    ap.add_argument("--stride", type=int, default=3, help="evaluate every Nth bar")
    ap.add_argument("--horizon", type=int, default=30, help="forward bars for MFE/MAE")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if not candles:
        raise SystemExit(f"no cached candles at {path}")
    candles = candles[-args.bars :]
    spec = REGISTRY.get(args.instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else args.instrument

    evaluated = 0
    signals: Counter[str] = Counter()
    fail_any: Counter[str] = Counter()
    primary: Counter[str] = Counter()
    # forward excursion grouped by primary blocker, and by each gate's state
    mfe_by_primary: dict[str, list[float]] = defaultdict(list)
    gate_pass_mfe: dict[str, list[float]] = defaultdict(list)
    gate_fail_mfe: dict[str, list[float]] = defaultdict(list)
    outcome_by_primary: dict[str, Counter[str]] = defaultdict(Counter)
    buy_outcomes: Counter[str] = Counter()
    buy_synth_outcomes: Counter[str] = Counter()
    # Bars where exactly ONE gate refused the trade: removing that gate would
    # have produced a trade, so this is the honest "what if it were removed?"
    # population. A bar blocked by three gates says nothing about any one.
    sole_outcome: dict[str, Counter[str]] = defaultdict(Counter)
    sole_mfe: dict[str, list[float]] = defaultdict(list)
    buy_mfe: list[float] = []
    missed10: Counter[str] = Counter()
    missed20: Counter[str] = Counter()

    for i in range(_WINDOW, len(candles) - args.horizon - 1, args.stride):
        window = candles[i - _WINDOW : i + 1]
        spot = float(window[-1].close)
        chain = _chain(root, spot, step)
        snap = compute_indicators(window, chain)
        status = classify_market(snap, False)
        dec, _ = decide(window, chain, snap, 0.0, False, status, False, None, 1.0, spot=spot)
        trace = dec.gates
        if trace is None or dec.option_type is None or dec.strike is None:
            continue
        evaluated += 1
        signals[dec.signal.value] += 1
        want_call = dec.option_type == OptionType.CALL
        fav, _adv = _forward(candles, i, args.horizon, want_call)
        # premium-equivalent of the favourable underlying excursion
        fav_prem = fav * _MODEL_DELTA

        for label in trace.blockers:
            fail_any[label] += 1
        blocker = trace.primary_blocker or "NONE"
        primary[blocker] += 1
        mfe_by_primary[blocker].append(fav_prem)
        if fav_prem >= 10:
            missed10[blocker] += 1
        if fav_prem >= 20:
            missed20[blocker] += 1

        for label, ok in _gate_states(trace):
            (gate_pass_mfe if ok else gate_fail_mfe)[label].append(fav_prem)

        # The engine only computes levels for a BUY/HOLD, so a blocked bar has
        # none. To compare like with like, a blocked bar is given the SAME shape
        # of trade the engine would have imposed: its premium-stop floor and a
        # target at its minimum reward:risk — the engine's own discipline, not a
        # hand-picked pair of levels that would flatter the removed gate.
        entry_prem = dec.current_premium
        stop_px = dec.stop_loss
        target_px = dec.target1
        synth_stop = synth_target = None
        if entry_prem:
            risk = entry_prem * settings.min_stop_pct_of_premium / 100.0
            synth_stop = entry_prem - risk
            synth_target = entry_prem + risk * settings.min_reward_risk
        if stop_px is None or target_px is None:
            stop_px, target_px = synth_stop, synth_target
        # A refused bar has no engine levels, so it is scored on the synthetic
        # pair above. Scoring the TAKEN buys on the same synthetic pair as well
        # is the only way the two populations are comparable — an engine target
        # is further away than a 1.2R one, so comparing them directly would make
        # every refused setup look better than every taken one for free.
        if synth_stop is not None and synth_target is not None:
            synth_hit = _first_hit(
                candles, i, args.horizon, want_call, float(dec.strike),
                dec.option_type, float(synth_stop), float(synth_target),
            )
            if dec.signal == Signal.BUY:
                buy_synth_outcomes[synth_hit] += 1
        if stop_px is not None and target_px is not None:
            hit = _first_hit(
                candles, i, args.horizon, want_call, float(dec.strike),
                dec.option_type, float(stop_px), float(target_px),
            )
            outcome_by_primary[blocker][hit] += 1
            if len(trace.blockers) == 1:
                sole_outcome[trace.blockers[0]][hit] += 1
                sole_mfe[trace.blockers[0]].append(fav_prem)
            if dec.signal == Signal.BUY:
                buy_outcomes[hit] += 1
                buy_mfe.append(fav_prem)

    def med(xs: list[float]) -> float:
        return round(statistics.median(xs), 2) if xs else 0.0

    report = {
        "instrument": args.instrument,
        "bars_replayed": evaluated,
        "horizon_bars": args.horizon,
        "signals": dict(signals),
        "gate_failures_any": dict(fail_any.most_common()),
        "primary_blockers": dict(primary.most_common()),
        "median_forward_premium_move_by_primary_blocker": {
            k: med(v) for k, v in sorted(mfe_by_primary.items(), key=lambda kv: -len(kv[1]))
        },
        "missed_10pt_by_primary_blocker": dict(missed10.most_common()),
        "missed_20pt_by_primary_blocker": dict(missed20.most_common()),
        "gate_discrimination": {
            label: {
                "n_pass": len(gate_pass_mfe[label]),
                "median_mfe_pass": med(gate_pass_mfe[label]),
                "n_fail": len(gate_fail_mfe[label]),
                "median_mfe_fail": med(gate_fail_mfe[label]),
                "edge": round(med(gate_pass_mfe[label]) - med(gate_fail_mfe[label]), 2),
            }
            for label in sorted(set(gate_pass_mfe) | set(gate_fail_mfe))
        },
        "target_vs_stop_first_by_primary_blocker": {
            k: dict(v) for k, v in sorted(
                outcome_by_primary.items(), key=lambda kv: -sum(kv[1].values())
            )
        },
        "taken_buys": {
            "outcomes_engine_levels": dict(buy_outcomes),
            "outcomes_synthetic_levels": dict(buy_synth_outcomes),
            "target_rate_synthetic_pct": round(
                100.0 * buy_synth_outcomes.get("TARGET", 0)
                / max(1, buy_synth_outcomes.get("TARGET", 0) + buy_synth_outcomes.get("STOP", 0)),
                1,
            ),
            "median_mfe": med(buy_mfe),
        },
        "if_gate_removed_sole_blocker": {
            label: {
                "n": sum(counts.values()),
                "target_first": counts.get("TARGET", 0),
                "stop_first": counts.get("STOP", 0),
                "unresolved": counts.get("OPEN", 0),
                "target_rate_pct": round(
                    100.0 * counts.get("TARGET", 0)
                    / max(1, counts.get("TARGET", 0) + counts.get("STOP", 0)), 1
                ),
                "median_mfe": med(sole_mfe[label]),
            }
            for label, counts in sorted(
                sole_outcome.items(), key=lambda kv: -sum(kv[1].values())
            )
        },
        "caveats": [
            "premiums are modelled from the underlying at a fixed ATM delta",
            "no theta, spread, IV or liquidity modelling",
            "overlapping windows: bars are not independent observations",
            "stop assumed first when one bar spans both stop and target",
        ],
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
