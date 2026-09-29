"""Strike audit on STORED option chains rather than a modelled ladder — research only.

``strike_audit.py`` prices its ATM±2 ladder itself, which is the only option when
no chain history exists — but it also means every strike is a smooth function of
the same underlying path, so it cannot see the things that actually separate one
strike from another: skew, spread, thin OI, a leg that simply does not trade.

This variant replays against ``chain_snapshots`` in ``history.db``, so each
candidate's premium, IV, OI and volume are whatever was really quoted at that
minute. Run ``chain_provenance.py`` first: if it reports zero real broker
snapshots for the instrument, this tool will replay *simulator* chains and its
output is worth nothing beyond a plumbing test — the ``chain_source`` field in
the report says which you got.

Limits that remain, and they are not small:

* Premiums are snapshot **closes at ~60s cadence**, so MFE/MAE and stop/target
  hits are measured on the snapshot series, not on intrabar highs and lows. A
  stop touched and recovered inside a minute is invisible here.
* Only strikes present in the snapshot can be candidates.
* Spread is still absent — the quote carries a premium, not a bid and an ask.

    .venv/bin/python strike_audit_chain.py --instrument CRUDEOIL
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict

import numpy as np

from app.config import settings
from app.engine.decision import classify_market, compute_indicators, decide
from app.market.instruments import REGISTRY
from app.models import Candle, OptionQuote, OptionType, Signal
from chain_provenance import classify as classify_provenance
from strike_audit import (
    FEATURES,
    _EXPANDED_PCT,
    _IST_OFFSET,
    _LEG_LOOKBACK,
    _TIE_R,
    _WINDOW,
    auc,
    expectancy,
    logistic,
    max_drawdown,
    med,
    pearson,
    predict,
    profit_factor,
    standardise,
)

_MATCH_TOLERANCE_SEC = 90   # how far a bar may reach for a snapshot


def load_chains(db_path: str, instrument: str) -> tuple[list[int], list[dict], Counter]:
    """Snapshot timestamps + ``{symbol: leg}`` maps, ascending, with provenance."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    prov: Counter[str] = Counter()
    tss: list[int] = []
    maps: list[dict] = []
    for ts, payload in db.execute(
        "SELECT ts, payload FROM chain_snapshots WHERE instrument=? ORDER BY ts",
        (instrument,),
    ):
        kind = classify_provenance(payload)
        prov[kind] += 1
        if kind not in ("REAL", "SIMULATED"):
            continue
        legs = json.loads(payload)
        tss.append(int(ts))
        maps.append({leg["symbol"]: leg for leg in legs})
    db.close()
    return tss, maps, prov


def load_candles(db_path: str, instrument: str) -> list[Candle]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT ts, open, high, low, close, volume FROM candles WHERE instrument=? "
        "ORDER BY ts", (instrument,),
    ).fetchall()
    db.close()
    return [Candle(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows]


def snapshot_at(tss: list[int], maps: list[dict], ts: int) -> dict | None:
    """The snapshot closest to ``ts`` within tolerance, or None."""
    if not tss:
        return None
    k = bisect.bisect_left(tss, ts)
    best = None
    for j in (k - 1, k):
        if 0 <= j < len(tss) and abs(tss[j] - ts) <= _MATCH_TOLERANCE_SEC:
            if best is None or abs(tss[j] - ts) < abs(tss[best] - ts):
                best = j
    return maps[best] if best is not None else None


def as_quote(leg: dict) -> OptionQuote:
    return OptionQuote(
        symbol=leg["symbol"], strike=float(leg["strike"]),
        option_type=OptionType(leg["option_type"]),
        premium=float(leg["premium"]), iv=float(leg.get("iv") or 0.0),
        delta=float(leg.get("delta") or 0.0), gamma=float(leg.get("gamma") or 0.0),
        theta=float(leg.get("theta") or 0.0), vega=float(leg.get("vega") or 0.0),
        oi=int(leg.get("oi") or 0), oi_change=int(leg.get("oi_change") or 0),
        volume=int(leg.get("volume") or 0),
    )


def walk_real(tss: list[int], maps: list[dict], candles: list[Candle], i: int,
              horizon: int, symbol: str, entry: float, stop: float,
              target: float) -> dict:
    """Forward-walk one candidate on its own quoted premium series."""
    risk = max(0.01, entry - stop)
    mfe = 0.0
    mae = 0.0
    hit = "OPEN"
    bars_to_target = None
    last = entry
    seen = 0
    for n, c in enumerate(candles[i + 1 : i + 1 + horizon], start=1):
        snap = snapshot_at(tss, maps, c.time)
        leg = snap.get(symbol) if snap else None
        if leg is None:
            continue
        seen += 1
        prem = float(leg["premium"])
        last = prem
        mfe = max(mfe, prem - entry)
        mae = min(mae, prem - entry)
        if hit == "OPEN":
            if prem <= stop:
                hit = "STOP"
            elif prem >= target:
                hit = "TARGET"
                bars_to_target = n
    if hit == "TARGET":
        realised = (target - entry) / risk
    elif hit == "STOP":
        realised = -1.0
    else:
        realised = (last - entry) / risk
    return {
        "hit": hit, "r": realised, "mfe_r": mfe / risk, "mae_r": mae / risk,
        "mfe_pct": 100.0 * mfe / max(0.01, entry),
        "mae_pct": 100.0 * mae / max(0.01, entry),
        "bars_to_target": bars_to_target, "target_first": hit == "TARGET",
        "quotes_seen": seen,
    }


def main() -> None:  # noqa: PLR0912, PLR0915 — one research report, read top-down
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--db", default="")
    ap.add_argument("--horizon", type=int, default=30)
    ap.add_argument("--steps", type=int, default=2,
                    help="candidate strikes each side of ATM")
    ap.add_argument("--min-quotes", type=int, default=10,
                    help="minimum forward quotes for a candidate to be scored")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    horizon = args.horizon
    db_path = args.db or os.path.join(settings.data_dir, "history.db")
    if not os.path.exists(db_path):
        raise SystemExit(f"no history db at {db_path}")

    tss, maps, prov = load_chains(db_path, args.instrument)
    if not tss:
        raise SystemExit(f"no chain snapshots for {args.instrument} in {db_path}")
    candles = load_candles(db_path, args.instrument)
    if len(candles) < _WINDOW + horizon + 10:
        raise SystemExit(
            f"only {len(candles)} stored candles for {args.instrument} — need "
            f"{_WINDOW + horizon + 10}; the chain history is useless without the "
            "underlying series recorded alongside it"
        )
    spec = REGISTRY.get(args.instrument)
    step = spec.strike_step if spec else 50.0

    chain_source = "REAL_BROKER" if prov.get("REAL", 0) >= prov.get("SIMULATED", 0) \
        else "SIMULATOR"

    buys: list[dict] = []
    matched = 0
    for i in range(_WINDOW, len(candles) - horizon - 1):
        c = candles[i]
        snap = snapshot_at(tss, maps, c.time)
        if snap is None:
            continue
        matched += 1
        window = candles[i - _WINDOW : i + 1]
        spot = float(window[-1].close)
        chain = [as_quote(leg) for leg in snap.values() if float(leg["premium"]) > 0]
        if not chain:
            continue
        ind = compute_indicators(window, chain)
        status = classify_market(ind, False)
        dec, _ = decide(window, chain, ind, 0.0, False, status, False, None, 1.0,
                        spot=spot)
        if dec.signal != Signal.BUY or dec.option_type is None:
            continue
        entry = float(dec.current_premium or 0.0)
        if entry <= 0 or dec.stop_loss is None or dec.target1 is None:
            continue
        side = dec.option_type
        stop_pct = max(0.01, (entry - dec.stop_loss) / entry)
        rr = max(0.1, (dec.target1 - entry) / max(0.01, entry - dec.stop_loss))
        atr = float(ind.atr or 0.0) or max(0.01, 0.001 * spot)
        vwap = float(ind.vwap) if ind.vwap is not None else spot
        mom5 = (spot - candles[i - 5].close) / atr
        mom15 = (spot - candles[i - 15].close) / atr
        sess_min = ((c.time + _IST_OFFSET) % 86400) / 60.0
        dirn = 1.0 if side == OptionType.CALL else -1.0
        atm_strike = round(spot / step) * step
        past = snapshot_at(tss, maps, candles[max(0, i - _LEG_LOOKBACK)].time)
        base_spot = candles[max(0, i - _LEG_LOOKBACK)].close
        und_move_pct = dirn * 100.0 * (spot - base_spot) / max(1e-9, base_spot)
        expected_move = atr * math.sqrt(horizon)

        cands: list[dict] = []
        for q in chain:
            if q.option_type != side or q.premium <= 0.05:
                continue
            k_off = int(round((q.strike - atm_strike) / step))
            if abs(k_off) > args.steps:
                continue
            c_entry = q.premium
            c_stop = c_entry * (1.0 - stop_pct)
            c_target = c_entry + (c_entry - c_stop) * rr
            out = walk_real(tss, maps, candles, i, horizon, q.symbol, c_entry,
                            c_stop, c_target)
            if out["quotes_seen"] < args.min_quotes:
                continue
            base_leg = past.get(q.symbol) if past else None
            base_prem = float(base_leg["premium"]) if base_leg else c_entry
            prem_move_pct = 100.0 * (c_entry - base_prem) / max(0.01, base_prem)
            dist_to_strike = (q.strike - spot) if side == OptionType.CALL else (spot - q.strike)
            cands.append({
                "symbol": q.symbol, "strike": q.strike, "k_off": k_off,
                "entry": round(c_entry, 2), "stop": round(c_stop, 2),
                "target": round(c_target, 2), "delta": abs(q.delta),
                "iv": q.iv, "oi": q.oi, "volume": q.volume,
                "chosen": q.symbol == dec.recommended_option,
                "und_move_pct_before": und_move_pct,
                "prem_move_pct_before": prem_move_pct,
                "features": {
                    "steps_from_atm": float(k_off),
                    "moneyness_pct": 100.0 * (spot - q.strike) / spot * dirn,
                    "abs_delta": abs(q.delta),
                    "theta_pct_per_day": 100.0 * abs(q.theta) / max(0.01, c_entry),
                    "gamma_leverage": q.gamma * spot * spot / max(0.01, c_entry) / 100.0,
                    "premium_pct_of_spot": 100.0 * c_entry / spot,
                    "room_atr": (expected_move - dist_to_strike) / atr,
                    "mom5_atr": mom5, "mom15_atr": mom15,
                    "vwap_dist_atr": (spot - vwap) / atr * dirn,
                    "atr_pct": 100.0 * atr / spot,
                    "minute_of_session": sess_min,
                    "dte_days": 0.0,
                    "confidence": float(dec.gates.confidence if dec.gates else 0.0),
                    "pre_expansion_ratio": max(-50.0, min(50.0, (
                        prem_move_pct / und_move_pct if abs(und_move_pct) > 0.01 else 0.0))),
                    # real-chain-only features, absent from the modelled run
                    "iv_pct": 100.0 * q.iv,
                    "log_oi": math.log10(max(1, q.oi)),
                    "log_volume": math.log10(max(1, q.volume)),
                },
                **out,
            })
        if not cands or not any(x["chosen"] for x in cands):
            continue
        buys.append({"i": i, "ts": c.time, "spot": spot, "side": side.value,
                     "regime": status.value if hasattr(status, "value") else str(status),
                     "candidates": cands})

    if not buys:
        raise SystemExit(
            f"no BUYs with a usable candidate set ({matched} bars matched a snapshot) "
            "— not enough overlapping chain and candle history"
        )

    real_features = FEATURES + ("iv_pct", "log_oi", "log_volume")

    def block(rows: list[dict]) -> dict:
        rs = [c["r"] for c in rows]
        return {
            "n": len(rows),
            "target_before_stop_pct": round(
                100.0 * sum(1 for c in rows if c["target_first"]) / max(1, len(rows)), 1),
            "expectancy_r": expectancy(rs),
            "profit_factor": profit_factor(rs),
            "max_drawdown_r": max_drawdown(rs),
            "median_mfe_r": med([c["mfe_r"] for c in rows]),
            "median_mae_r": med([c["mae_r"] for c in rows]),
            "median_entry_premium": med([c["entry"] for c in rows]),
            "median_delta": med([c["delta"] for c in rows]),
            "median_iv": med([c["iv"] for c in rows]),
            "median_oi": med([float(c["oi"]) for c in rows]),
            "median_volume": med([float(c["volume"]) for c in rows]),
        }

    chosen_rows = [next(c for c in b["candidates"] if c["chosen"]) for b in buys]
    by_off: dict[int, list[dict]] = defaultdict(list)
    for b in buys:
        for c in b["candidates"]:
            by_off[c["k_off"]].append(c)
    all_c = [c for b in buys for c in b["candidates"]]
    labels = [1.0 if c["target_first"] else 0.0 for c in all_c]

    bands = ((0.30, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70),
             (0.70, 0.80), (0.80, 1.01))
    delta_bands = {}
    for lo, hi in bands:
        rows = [c for c in all_c if lo <= c["delta"] < hi]
        if rows:
            delta_bands[f"{lo:.2f}-{hi:.2f}"] = block(rows)
    low = [c for c in all_c if c["delta"] < 0.30]
    if low:
        delta_bands["<0.30"] = block(low)

    def bucket(k_off: int, side: str) -> str:
        signed = k_off if side == "CE" else -k_off
        return {-2: "ITM2", -1: "ITM1", 0: "ATM", 1: "OTM1", 2: "OTM2"}.get(signed, "FAR")

    moneyness = {}
    for side in ("CE", "PE"):
        for name in ("ITM2", "ITM1", "ATM", "OTM1", "OTM2"):
            rows = [c for b in buys if b["side"] == side
                    for c in b["candidates"] if bucket(c["k_off"], side) == name]
            if rows:
                moneyness[f"{side}_{name}"] = block(rows)

    grade: Counter[str] = Counter()
    all_tied = 0
    left_on_table: list[float] = []
    for b in buys:
        rows = b["candidates"]
        ch = next(c for c in rows if c["chosen"])
        best_r = max(c["r"] for c in rows)
        if best_r - min(c["r"] for c in rows) <= _TIE_R:
            all_tied += 1
        pos = sum(1 for c in rows if c["r"] > ch["r"] + _TIE_R)
        grade[("BEST", "GOOD", "ACCEPTABLE", "SUBOPTIMAL", "SUBOPTIMAL")[min(pos, 4)]] += 1
        if pos > 0:
            left_on_table.append(best_r - ch["r"])

    model: dict = {"note": "too few BUYs for a chronological split"}
    selector: dict = {}
    n_buy = len(buys)
    if n_buy >= 60:
        x_all = np.asarray([[c["features"][f] for f in real_features] for c in all_c],
                           dtype=float)
        y_all = np.asarray(labels, dtype=float)
        starts = np.cumsum([0] + [len(b["candidates"]) for b in buys])
        cut_tr, cut_va, cut_oos = int(0.5 * n_buy), int(0.7 * n_buy), int(0.85 * n_buy)

        def rows_for(a: int, b: int) -> np.ndarray:
            return np.arange(starts[a], starts[b])

        i_tr, i_va, i_oos = (rows_for(0, cut_tr), rows_for(cut_tr, cut_va),
                            rows_for(cut_va, cut_oos))
        mu, sd = x_all[i_tr].mean(axis=0), x_all[i_tr].std(axis=0)
        w = logistic(standardise(x_all[i_tr], mu, sd), y_all[i_tr])
        model = {
            "train_auc": auc(list(predict(w, standardise(x_all[i_tr], mu, sd))), list(y_all[i_tr])),
            "validation_auc": auc(list(predict(w, standardise(x_all[i_va], mu, sd))), list(y_all[i_va])),
            "out_of_sample_auc": auc(list(predict(w, standardise(x_all[i_oos], mu, sd))), list(y_all[i_oos])),
            "coefficients": {f: round(float(w[n + 1]), 4) for n, f in enumerate(real_features)},
            "split_buys": {"train": cut_tr, "validation": cut_va - cut_tr,
                           "out_of_sample": cut_oos - cut_va,
                           "walk_forward": n_buy - cut_oos},
        }
        cur, res, atm_p, cheap = [], [], [], []
        for b in buys[cut_va:]:
            rows = b["candidates"]
            xb = standardise(np.asarray(
                [[c["features"][f] for f in real_features] for c in rows], dtype=float),
                mu, sd)
            p = predict(w, xb)
            cur.append(next(c for c in rows if c["chosen"]))
            res.append(rows[int(np.argmax(p))])
            atm_p.append(min(rows, key=lambda c: abs(c["k_off"])))
            cheap.append(min(rows, key=lambda c: c["entry"]))
        selector = {
            "note": "out-of-sample segment only",
            "current_selector": block(cur),
            "research_selector": block(res),
            "baseline_always_atm": block(atm_p),
            "baseline_cheapest_leg": block(cheap),
            "agreement_pct": round(100.0 * sum(
                1 for a, b2 in zip(cur, res) if a["symbol"] == b2["symbol"]
            ) / max(1, len(cur)), 1),
        }

    report = {
        "instrument": args.instrument,
        "premium_basis": "STORED CHAIN QUOTES — snapshot closes at ~60s cadence, no "
                         "intrabar highs/lows, no bid/ask spread",
        "chain_source": chain_source,
        "provenance_counts": dict(prov),
        "warning": (
            "chain_source is SIMULATOR: these premiums were generated by the "
            "built-in simulator, so nothing here describes real strike behaviour — "
            "it only proves the pipeline runs. Re-run against a history.db recorded "
            "from a live Angel session."
            if chain_source == "SIMULATOR" else
            "chain_source is REAL_BROKER: premiums, IV, OI and volume are as quoted."
        ),
        "stored_candles": len(candles),
        "bars_matched_to_a_snapshot": matched,
        "buys": n_buy,
        "horizon_bars": horizon,
        "chosen_leg": block(chosen_rows),
        "chosen_steps_from_atm": {str(k): v for k, v in sorted(
            Counter(c["k_off"] for c in chosen_rows).items())},
        "by_steps_from_atm": {str(k): block(v) for k, v in sorted(by_off.items())},
        "delta_bands": delta_bands,
        "moneyness_by_side": moneyness,
        "feature_correlations": {
            f: {"r_vs_target_before_stop": pearson([c["features"][f] for c in all_c], labels),
                "r_vs_realised_r": pearson([c["features"][f] for c in all_c],
                                           [c["r"] for c in all_c])}
            for f in real_features
        },
        "premium_expansion_before_entry": {
            "median_underlying_move_pct_before_entry": med(
                [c["und_move_pct_before"] for c in chosen_rows]),
            "median_premium_move_pct_before_entry": med(
                [c["prem_move_pct_before"] for c in chosen_rows]),
            "already_expanded_share_pct": round(100.0 * sum(
                1 for c in chosen_rows if c["prem_move_pct_before"] >= _EXPANDED_PCT
            ) / n_buy, 1),
        },
        "strike_grade": {
            "note": "hindsight rank of the chosen leg among candidates by realised R",
            "counts": dict(grade),
            "pct": {k: round(100.0 * v / n_buy, 1) for k, v in grade.items()},
            "buys_where_every_candidate_tied_pct": round(100.0 * all_tied / n_buy, 1),
            "median_r_left_on_the_table_when_not_best": med(left_on_table),
        },
        "entry_quality_model": model,
        "selector_comparison": selector,
    }
    out = json.dumps(report, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(out)


if __name__ == "__main__":
    main()
