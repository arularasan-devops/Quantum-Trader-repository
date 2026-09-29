"""OFFLINE entry-timing audit (Phase 2A/2B/2D/2G/2H) — research only.

The funnel audit asked "which gate refused?". This asks the harder question:
**when the engine did buy, where in the move was it?** — and, for every refusal,
whether an entry at that price would actually have reached target before stop.

It replays the frozen engine bar by bar (same `compute_indicators` /
`classify_market` / `decide` path as production), reconstructs each BUY's leg
from the swing that preceded it, and attributes the delay between the first bar
the engine wanted that side and the bar it finally committed to the specific
gate that was blocking during each waiting bar.

Same honest limits as funnel_audit.py: premiums are MODELLED from the underlying
at a fixed ATM delta, so nothing here includes theta, spread, IV or liquidity.
These are modelled-premium results and must be labelled as such.

    .venv/bin/python entry_timing_audit.py --instrument CRUDEOIL --bars 120000
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict

from app.backtest import angel_history as ah
from app.config import settings
from app.execution import early_early, early_momentum
from app.engine.decision import (
    classify_market,
    compute_indicators,
    decide,
)
from app.market.instruments import REGISTRY
from app.models import Candle, OptionQuote, OptionType, Signal

_MODEL_DELTA = 0.6
_MODEL_TIME_VALUE = 180.0
_MODEL_FLOOR = 5.0
_WINDOW = 240          # lookback fed to the engine
_LEG_LOOKBACK = 30     # bars searched for the leg origin before a BUY

# --- classification thresholds -------------------------------------------
# Expressed in R (the trade's own stop distance), never in raw points: "10
# points late" means nothing when one instrument's leg is 8 points and
# another's is 200. R is the only unit that compares across instruments and
# across days, and it is the unit the engine already risks in.
_T1_R = 1.2            # the engine's own minimum reward:risk = its T1
_GOOD_R = 2.0          # room for T2 as well as T1
_CONSUMED_LATE = 0.60  # fraction of the leg already gone at entry


def _premium(spot: float, strike: float, side: OptionType) -> float:
    signed = (spot - strike) if side == OptionType.CALL else (strike - spot)
    return max(_MODEL_FLOOR, _MODEL_TIME_VALUE + _MODEL_DELTA * signed)


def _chain(root: str, spot: float, step: float) -> list[OptionQuote]:
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
                    iv=0.0,
                    delta=_MODEL_DELTA if side == OptionType.CALL else -_MODEL_DELTA,
                    gamma=0.0, theta=0.0, vega=0.0, oi=0, oi_change=0, volume=0,
                )
            )
    return out


def classify(remaining_r: float, consumed: float, stopped_first: bool) -> str:
    """EARLY / GOOD / LATE / PEAK for one entry.

    Precedence matters and is deliberate:

    * **PEAK** — with perfect hindsight the leg never offered T1 from this
      entry (``remaining < 1.2R``). Whatever else was true, this entry could
      not have won. Checked first because "it also took heat" is irrelevant
      when there was no money left in the move.
    * **EARLY** — T1 was reachable, but the stop came first: the direction was
      right and the timing was ahead of it.
    * **LATE** — T1 was reached, but most of the leg was already gone
      (``consumed >= 60%``) or there was less than 2R of room left. A win, but
      a fraction of the available one.
    * **GOOD** — target first, at least 2R of room, under 60% of the leg
      consumed.
    """
    if remaining_r < _T1_R:
        return "PEAK"
    if stopped_first:
        return "EARLY"
    if consumed >= _CONSUMED_LATE or remaining_r < _GOOD_R:
        return "LATE"
    return "GOOD"


def _first_hit(
    candles: list[Candle], i: int, horizon: int, want_call: bool,
    strike: float, side: OptionType, stop: float, target: float,
) -> str:
    """STOP / TARGET / OPEN. A bar spanning both is scored STOP — assuming the
    target would flatter every single entry in this report."""
    for c in candles[i + 1 : i + 1 + horizon]:
        lo = _premium(c.low if want_call else c.high, strike, side)
        hi = _premium(c.high if want_call else c.low, strike, side)
        if lo <= stop:
            return "STOP"
        if hi >= target:
            return "TARGET"
    return "OPEN"


def _r_multiple(
    candles: list[Candle], i: int, horizon: int, want_call: bool,
    strike: float, side: OptionType, entry: float, stop: float, target: float,
) -> float:
    """Realised R of the trade: +1.2R at target, -1R at stop, marked to the
    last bar if neither is reached inside the window."""
    risk = max(0.01, entry - stop)
    hit = _first_hit(candles, i, horizon, want_call, strike, side, stop, target)
    if hit == "TARGET":
        return (target - entry) / risk
    if hit == "STOP":
        return -1.0
    seg = candles[i + 1 : i + 1 + horizon]
    if not seg:
        return 0.0
    last = _premium(seg[-1].close, strike, side)
    return (last - entry) / risk


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--bars", type=int, default=60000)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--horizon", type=int, default=30)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    horizon = args.horizon

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if not candles:
        raise SystemExit(f"no cached candles at {path}")
    candles = candles[-args.bars :]
    spec = REGISTRY.get(args.instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else args.instrument

    # ---- pass 1: replay, keeping only what later passes need ----
    rows: list[dict] = []
    for i in range(_WINDOW, len(candles) - horizon - 1, args.stride):
        window = candles[i - _WINDOW : i + 1]
        spot = float(window[-1].close)
        chain = _chain(root, spot, step)
        snap = compute_indicators(window, chain)
        status = classify_market(snap, False)
        dec, _ = decide(window, chain, snap, 0.0, False, status, False, None, 1.0, spot=spot)
        if dec.gates is None or dec.option_type is None or dec.strike is None:
            continue
        # The two advisory engines run on the same bar, so EARLY and CONFIRMED
        # can be scored on identical terms (Phase 2C).
        em = early_momentum.detect(
            window, chain, snap, spot, now=candles[i].time, is_mcx=True
        )
        ee = early_early.detect(window, chain, snap, spot)
        want_call = dec.option_type == OptionType.CALL
        rows.append(
            {
                "i": i,
                "ts": candles[i].time,
                "spot": spot,
                "side": dec.option_type.value,
                "want_call": want_call,
                "strike": float(dec.strike),
                "signal": dec.signal.value,
                "premium": float(dec.current_premium or 0.0),
                "stop": dec.stop_loss,
                "target": dec.target1,
                "blocker": dec.gates.primary_blocker,
                "blockers": list(dec.gates.blockers),
                "conf": dec.gates.confidence,
                # confirmation clocks (2A): when each condition first became true
                "vwap_ok": bool(
                    snap.vwap is not None
                    and ((spot > snap.vwap) if want_call else (spot < snap.vwap))
                ),
                "ema_ok": bool(
                    snap.ema9 is not None and snap.ema20 is not None
                    and ((snap.ema9 > snap.ema20) if want_call else (snap.ema9 < snap.ema20))
                ),
                "momentum_ok": bool(
                    snap.momentum is not None
                    and ((snap.momentum > 0) if want_call else (snap.momentum < 0))
                ),
                "volume_ok": bool(snap.volume_spike or snap.volume_level in ("High", "Very High")),
                "structure_ok": bool(
                    snap.market_structure == ("HH_HL" if want_call else "LH_LL")
                ),
                "chain_ok": bool(
                    snap.institutional == ("BULLISH" if want_call else "BEARISH")
                ),
                "htf_ok": bool(snap.trend == ("UP" if want_call else "DOWN")),
                "em_active": bool(em.active),
                "em_side": em.side.value if em.side else None,
                "em_stage": em.stage_num,
                "ee_active": bool(ee.active),
                "ee_side": ee.side.value if ee.side else None,
            }
        )

    # ---- 2A/2B: entry timing for every BUY ----
    classes: Counter[str] = Counter()
    consumed_all: list[float] = []
    remaining_all: list[float] = []
    delay_bars: list[int] = []
    delay_bars_by_class: dict[str, list[int]] = defaultdict(list)
    delay_paid: list[float] = []
    delay_paid_by_class: dict[str, list[float]] = defaultdict(list)
    # premium the market gave away while each gate was the one blocking
    delay_cost: dict[str, float] = defaultdict(float)
    delay_bars_by_gate: Counter[str] = Counter()
    peak_causes: Counter[str] = Counter()
    late_causes: Counter[str] = Counter()
    examples: list[dict] = []
    conf_lag: dict[str, list[int]] = defaultdict(list)

    for n, r in enumerate(rows):
        if r["signal"] != Signal.BUY.value:
            continue
        i = r["i"]
        want_call = r["want_call"]
        strike = r["strike"]
        side = OptionType.CALL if want_call else OptionType.PUT
        entry = r["premium"]
        if entry <= 0:
            continue

        # --- the leg behind this entry: lowest (for CE) premium in the lookback
        pre = candles[max(0, i - _LEG_LOOKBACK) : i + 1]
        pre_prems = [_premium(c.close, strike, side) for c in pre]
        leg_low = min(pre_prems)
        # --- the leg ahead: best premium inside the forward window
        post = candles[i + 1 : i + 1 + horizon]
        if not post:
            continue
        peak = max(
            _premium(c.high if want_call else c.low, strike, side) for c in post
        )
        total = max(0.01, peak - leg_low)
        consumed = min(1.0, max(0.0, (entry - leg_low) / total))

        stop = r["stop"] if r["stop"] is not None else entry * (1 - settings.min_stop_pct_of_premium / 100.0)
        target = r["target"] if r["target"] is not None else entry + (entry - stop) * settings.min_reward_risk
        risk = max(0.01, entry - stop)
        remaining_r = (peak - entry) / risk
        hit = _first_hit(candles, i, horizon, want_call, strike, side, stop, target)
        cls = classify(remaining_r, consumed, hit == "STOP")
        classes[cls] += 1
        consumed_all.append(consumed)
        remaining_all.append(remaining_r)

        # --- how long did the engine want this side before it committed? ---
        first = n
        while (
            first - 1 >= 0
            and rows[first - 1]["side"] == r["side"]
            and rows[first - 1]["signal"] != Signal.BUY.value
            and r["i"] - rows[first - 1]["i"] <= _LEG_LOOKBACK
        ):
            first -= 1
        delay = r["i"] - rows[first]["i"]
        delay_bars.append(delay)
        delay_bars_by_class[cls].append(delay)
        # Points the BUY paid for the wait: premium at the BUY minus premium at
        # the first bar the engine wanted this side. Counted only where it did
        # wait, so same-bar BUYs cannot dilute the median.
        if delay > 0:
            paid = entry - _premium(candles[rows[first]["i"]].close, strike, side)
            delay_paid.append(paid)
            delay_paid_by_class[cls].append(paid)

        # --- attribute the delay: each waiting bar's blocker is charged with the
        # premium the market moved during that bar. A gate that blocks through a
        # flat stretch costs nothing; one that blocks through the launch costs
        # the launch.
        this_trade_cost: dict[str, float] = defaultdict(float)
        for k in range(first, n):
            g = rows[k]["blocker"]
            if not g:
                continue
            p0 = _premium(candles[rows[k]["i"]].close, strike, side)
            nxt = rows[k + 1]["i"] if k + 1 <= n else rows[k]["i"]
            p1 = _premium(candles[nxt].close, strike, side)
            delay_bars_by_gate[g] += 1
            if p1 > p0:
                delay_cost[g] += p1 - p0
                this_trade_cost[g] += p1 - p0

        # --- when did each confirmation first turn on, relative to the BUY? ---
        for flag in ("vwap_ok", "ema_ok", "momentum_ok", "volume_ok", "structure_ok",
                     "chain_ok", "htf_ok"):
            for k in range(first, n + 1):
                if rows[k][flag]:
                    conf_lag[flag].append(r["i"] - rows[k]["i"])
                    break

        if cls in ("PEAK", "LATE"):
            worst = max(this_trade_cost, key=lambda g: this_trade_cost[g], default=None)
            blocking = [rows[k]["blocker"] for k in range(first, n) if rows[k]["blocker"]]
            ranked = Counter(blocking).most_common(2)
            cause = ranked[0][0] if ranked else "NO_DELAY"
            second = ranked[1][0] if len(ranked) > 1 else None
            (peak_causes if cls == "PEAK" else late_causes)[cause] += 1
            if len(examples) < 12 and cls == "PEAK" and delay > 0:
                examples.append(
                    {
                        "class": cls,
                        "earlier_opportunity_premium": round(leg_low, 2),
                        "buy_premium": round(entry, 2),
                        "peak_premium": round(peak, 2),
                        "move_before_buy": round(entry - leg_low, 2),
                        "move_after_buy": round(peak - entry, 2),
                        "captured_pct": round(100.0 * (peak - entry) / total, 1),
                        "remaining_R": round(remaining_r, 2),
                        "delay_bars": delay,
                        "primary_cause": cause,
                        "secondary_cause": second,
                        "worst_paying_gate": worst,
                    }
                )

    # ---- 2D/2G: identical entry/stop/target/window for refused AND accepted ----
    per_gate: dict[str, dict] = {}
    thresholds: dict[str, dict] = {}
    for n, r in enumerate(rows):
        entry = r["premium"]
        if entry <= 0:
            continue
        want_call = r["want_call"]
        side = OptionType.CALL if want_call else OptionType.PUT
        strike = r["strike"]
        # the SAME synthetic contract for every population — this is the whole
        # point of 2D: a refused setup and an accepted one must be scored on
        # identical terms or the comparison is meaningless.
        risk = entry * settings.min_stop_pct_of_premium / 100.0
        stop = entry - risk
        target = entry + risk * settings.min_reward_risk
        i = r["i"]
        post = candles[i + 1 : i + 1 + horizon]
        if not post:
            continue
        mfe = max(_premium(c.high if want_call else c.low, strike, side) for c in post) - entry
        mae = entry - min(_premium(c.low if want_call else c.high, strike, side) for c in post)
        rmult = _r_multiple(candles, i, horizon, want_call, strike, side, entry, stop, target)
        key = "ACCEPTED_BUY" if r["signal"] == Signal.BUY.value else (r["blocker"] or "UNBLOCKED_NON_BUY")
        # 2C: the advisory engines are scored on exactly the same contract,
        # stop, target and window as the engine's own BUYs.
        pops = [key]
        if r["em_active"] and r["em_side"] == r["side"]:
            pops.append("EARLY_MOMENTUM")
        if r["ee_active"] and r["ee_side"] == r["side"]:
            pops.append("EARLY_EARLY")
        if r["signal"] == Signal.BUY.value:
            pops.append("CONFIRMED_BUY_ALL")
        for pop in pops:
            self_agg = per_gate.setdefault(
                pop, {"n": 0, "wins": 0, "losses": 0, "r_sum": 0.0, "gross_win": 0.0,
                      "gross_loss": 0.0, "mfe": [], "mae": []}
            )
            if pop != key:
                self_agg["n"] += 1
                self_agg["r_sum"] += rmult
                self_agg["mfe"].append(mfe / max(0.01, risk))
                self_agg["mae"].append(mae / max(0.01, risk))
                if rmult > 0:
                    self_agg["wins"] += 1
                    self_agg["gross_win"] += rmult
                else:
                    self_agg["losses"] += 1
                    self_agg["gross_loss"] += abs(rmult)
        # absolute-point thresholds, for the P(+5/+10/+15/+20) question
        for pop in pops:
            th = thresholds.setdefault(pop, {"n": 0, 5: 0, 10: 0, 15: 0, 20: 0})
            th["n"] += 1
            for t in (5, 10, 15, 20):
                if mfe >= t:
                    th[t] += 1
        agg = per_gate.setdefault(
            key, {"n": 0, "wins": 0, "losses": 0, "r_sum": 0.0, "gross_win": 0.0,
                  "gross_loss": 0.0, "mfe": [], "mae": []}
        )
        agg["n"] += 1
        agg["r_sum"] += rmult
        agg["mfe"].append(mfe / max(0.01, risk))
        agg["mae"].append(mae / max(0.01, risk))
        if rmult > 0:
            agg["wins"] += 1
            agg["gross_win"] += rmult
        else:
            agg["losses"] += 1
            agg["gross_loss"] += abs(rmult)

    def med(xs: list[float]) -> float:
        return round(statistics.median(xs), 2) if xs else 0.0

    gate_table = {}
    for k, a in sorted(per_gate.items(), key=lambda kv: -kv[1]["n"]):
        n = max(1, a["n"])
        gate_table[k] = {
            "setups": a["n"],
            "win_rate_pct": round(100.0 * a["wins"] / n, 1),
            "expectancy_R": round(a["r_sum"] / n, 3),
            "profit_factor": round(a["gross_win"] / a["gross_loss"], 2) if a["gross_loss"] else None,
            "median_MFE_R": med(a["mfe"]),
            "median_MAE_R": med(a["mae"]),
        }

    # ---- 2H: which gates fail together? ----
    fails: dict[str, set[int]] = defaultdict(set)
    for n, r in enumerate(rows):
        for g in r["blockers"]:
            fails[g].add(n)
    pairs = []
    names = sorted(fails)
    for a_i in range(len(names)):
        for b_i in range(a_i + 1, len(names)):
            a, b = names[a_i], names[b_i]
            inter = len(fails[a] & fails[b])
            union = len(fails[a] | fails[b])
            if union and inter:
                pairs.append(
                    {
                        "gate_a": a, "gate_b": b,
                        "jaccard": round(inter / union, 3),
                        "a_given_b_pct": round(100.0 * inter / len(fails[b]), 1),
                        "b_given_a_pct": round(100.0 * inter / len(fails[a]), 1),
                    }
                )
    pairs.sort(key=lambda p: -p["jaccard"])

    total_buys = max(1, sum(classes.values()))
    report = {
        "instrument": args.instrument,
        "premium_basis": "MODELLED (fixed 0.6 ATM delta) — not real option prices",
        "bars_replayed": len(rows),
        "buys_classified": sum(classes.values()),
        "class_counts": dict(classes),
        "class_pct": {k: round(100.0 * v / total_buys, 1) for k, v in classes.items()},
        "median_move_consumed_before_buy_pct": round(100.0 * med(consumed_all), 1),
        "median_remaining_R_at_buy": med(remaining_all),
        "median_delay_bars_setup_to_buy": med([float(d) for d in delay_bars]),
        "delayed_buys_pct": round(
            100.0 * sum(1 for d in delay_bars if d > 0) / max(1, len(delay_bars)), 1
        ),
        "median_delay_bars_by_class": {
            k: med([float(x) for x in v]) for k, v in delay_bars_by_class.items()
        },
        "premium_points_paid_for_the_wait": {
            "delayed_buys": len(delay_paid),
            "median": med(delay_paid),
            "by_class": {k: med(v) for k, v in delay_paid_by_class.items()},
        },
        "premium_given_away_while_gate_blocked": {
            k: round(v, 1) for k, v in sorted(delay_cost.items(), key=lambda kv: -kv[1])
        },
        "waiting_bars_by_gate": dict(delay_bars_by_gate.most_common()),
        "peak_buy_causes": dict(peak_causes.most_common()),
        "late_buy_causes": dict(late_causes.most_common()),
        "confirmation_lag_bars_before_buy_median": {
            k: med([float(x) for x in v]) for k, v in conf_lag.items()
        },
        "identical_terms_by_population": gate_table,
        "probability_of_reaching_points": {
            k: {
                "n": v["n"],
                "p_plus_5_pct": round(100.0 * v[5] / max(1, v["n"]), 1),
                "p_plus_10_pct": round(100.0 * v[10] / max(1, v["n"]), 1),
                "p_plus_15_pct": round(100.0 * v[15] / max(1, v["n"]), 1),
                "p_plus_20_pct": round(100.0 * v[20] / max(1, v["n"]), 1),
            }
            for k, v in sorted(thresholds.items(), key=lambda kv: -kv[1]["n"])
        },
        "gate_overlap_top": pairs[:15],
        "peak_examples": examples,
        "definitions": {
            "PEAK": f"remaining < {_T1_R}R at entry — T1 unreachable even in hindsight",
            "EARLY": "T1 was reachable but the stop came first",
            "LATE": f"target first, but >={int(_CONSUMED_LATE*100)}% of the leg consumed or <{_GOOD_R}R left",
            "GOOD": f"target first, >={_GOOD_R}R left, <{int(_CONSUMED_LATE*100)}% consumed",
        },
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
