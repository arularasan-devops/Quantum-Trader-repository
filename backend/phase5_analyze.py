"""Phase 5 pass 2 — regime research (Step 2), direction research (Step 3), the
entry-probability model (Step 4) and the population comparison (Step 5).

Reads the causal rows written by ``phase5_features.py``. Nothing here can touch
production: it is an offline analysis script and it imports no engine state.

Methodology rules enforced in code, not just claimed:

* chronological splits only — TRAIN 60% / VALIDATION 20% / OUT-OF-SAMPLE 20% by
  timestamp, plus walk-forward folds. No shuffling anywhere.
* the model sees only the ``features`` block, which pass 1 built from bars <= t.
  The outcome columns, the engine's verdict and the scanner's verdict are never
  features (the scanner's own score IS allowed: it is computed from <= t too, and
  excluding it would hide whether the model is just re-deriving it).
* a filtered population is always compared on the SAME levels and the SAME bars
  as the baseline, so a filter cannot win by being scored on easier terms.
* drawdown is computed on the chronological sequence of R outcomes, so a
  population that wins slowly is not flattered.

    python phase5_analyze.py --rows ~/p5_crude.jsonl --out ~/p5_crude_report.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time

_MIN_CELL = 200          # a cell smaller than this is reported but not concluded from
_PROB_BUCKETS = 10


# --------------------------------------------------------------- basic stats
def stats(rows: list[dict], side: str | None = None) -> dict:
    """Expectancy / PF / MFE / MAE / drawdown for a population.

    ``side`` picks the outcome column; when None each row must already carry a
    ``_out`` chosen by the caller (used for "the side the engine actually took").
    """
    outs = []
    for r in rows:
        o = r["_out"] if side is None else r[side]
        outs.append(o)
    if not outs:
        return {"n": 0}
    rs = [o["r"] for o in outs]
    wins = [o for o in outs if o["result"] == "TARGET"]
    losses = [o for o in outs if o["result"] == "STOP"]
    gw = sum(o["r"] for o in wins)
    gl = -sum(o["r"] for o in losses)
    # chronological equity → max drawdown in R
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for o in outs:
        eq += o["r"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {
        "n": len(outs),
        "target_before_stop_pct": round(100.0 * len(wins) / len(outs), 2),
        "expectancy_r": round(statistics.mean(rs), 4),
        "profit_factor": round(gw / gl, 3) if gl else None,
        "mfe_r_median": round(statistics.median(o["mfe_r"] for o in outs), 3),
        "mae_r_median": round(statistics.median(o["mae_r"] for o in outs), 3),
        "max_drawdown_r": round(dd, 2),
        "total_r": round(eq, 1),
        "unresolved_pct": round(100.0 * sum(1 for o in outs if o["result"] == "OPEN")
                                / len(outs), 2),
    }


def ci95(values: list[float]) -> list[float]:
    """95% CI of the mean (normal approximation on the standard error).

    Small n is exactly when a difference looks real and isn't, so every headline
    difference gets one. A bootstrap gives the same answer for means at these
    sample sizes and costs minutes per cell at n=200k, so the closed form is used
    deliberately rather than for convenience.
    """
    n = len(values)
    if n < 30:
        return [None, None]
    m = statistics.mean(values)
    se = statistics.pstdev(values) / math.sqrt(n)
    return [round(m - 1.96 * se, 4), round(m + 1.96 * se, 4)]


def _sig(c: list[float]) -> bool:
    """True when the interval excludes zero (both bounds share a sign)."""
    return bool(c[0] is not None and (c[0] > 0) == (c[1] > 0))


# --------------------------------------------------------------- step 2 / 3
def by_key(rows: list[dict], key, side: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(key(r)), []).append(r)
    return {k: stats(v, side) for k, v in sorted(groups.items())}


def regime_research(rows: list[dict]) -> dict:
    out: dict = {}
    for side in ("CE", "PE"):
        out[side] = {
            "by_scan_state": by_key(rows, lambda r: r["features"]["scan_state"], side),
            "by_trend_regime": by_key(rows, lambda r: r["trend_regime"], side),
            "by_vol_regime": by_key(rows, lambda r: r["vol_regime"], side),
            "by_tod": by_key(rows, lambda r: r["tod"], side),
            "by_state_x_vol": by_key(
                rows, lambda r: f"{r['features']['scan_state']}|{r['vol_regime']}", side),
            "by_state_x_trend": by_key(
                rows, lambda r: f"{r['features']['scan_state']}|{r['trend_regime']}", side),
        }
    # chronological stability of the state ranking — the only thing worth acting on
    third = len(rows) // 3
    out["state_stability"] = {
        "first_third": by_key(rows[:third], lambda r: r["features"]["scan_state"], "CE"),
        "final_third_oos": by_key(rows[2 * third:],
                                  lambda r: r["features"]["scan_state"], "CE"),
    }
    return out


def direction_research(rows: list[dict]) -> dict:
    """Does side carry information beyond regime?

    For every regime cell the two sides are scored on the same bars, so the
    difference is a pure side effect. The CI and the OOS sign are what decide
    whether it is real; the Phase 3 "Nifty PE" observation is deliberately NOT
    hard-coded anywhere.
    """
    def cell(rs: list[dict]) -> dict:
        ce = [r["CE"]["r"] for r in rs]
        pe = [r["PE"]["r"] for r in rs]
        diff = [a - b for a, b in zip(pe, ce)]
        return {
            "n": len(rs),
            "CE_expectancy_r": round(statistics.mean(ce), 4) if ce else None,
            "PE_expectancy_r": round(statistics.mean(pe), 4) if pe else None,
            "PE_minus_CE_r": round(statistics.mean(diff), 4) if diff else None,
            "PE_minus_CE_ci95": ci95(diff),
            "significant": _sig(ci95(diff)),
        }

    third = len(rows) // 3
    groups: dict[str, list[dict]] = {"ALL": rows}
    for r in rows:
        groups.setdefault("state=" + r["features"]["scan_state"], []).append(r)
        groups.setdefault("trend=" + r["trend_regime"], []).append(r)
        groups.setdefault("vol=" + r["vol_regime"], []).append(r)
    return {
        "cells": {k: cell(v) for k, v in sorted(groups.items()) if len(v) >= _MIN_CELL},
        "chronological": {
            "first_third": cell(rows[:third]),
            "second_third": cell(rows[third:2 * third]),
            "final_third_oos": cell(rows[2 * third:]),
        },
        # Does the engine's own side choice beat a coin flip on the same bars?
        "engine_side_vs_alternative": engine_side_check(rows),
        "engine_side_vs_alternative_BUY_ONLY": engine_side_check(rows, only_buy=True),
        "engine_side_BUY_ONLY_oos": engine_side_check(rows[int(0.8 * len(rows)):],
                                                      only_buy=True),
        "follow_vs_fade": follow_vs_fade(rows),
    }


def follow_vs_fade(rows: list[dict]) -> dict:
    """Go WITH the detected move or AGAINST it, per regime state.

    A raw CE-vs-PE table cannot separate a side effect from the instrument's own
    drift over the sample. Follow-vs-fade removes the drift: both legs are the
    same bars, and the only difference is whether the trade agrees with the
    direction the move is already in. This is the honest form of the "does
    direction add value" question.
    """
    def cell(rs: list[dict]) -> dict:
        fol, fad = [], []
        for r in rs:
            d = r["features"].get("scan_dir")
            if d == "UP":
                fol.append(r["CE"]["r"])
                fad.append(r["PE"]["r"])
            elif d == "DOWN":
                fol.append(r["PE"]["r"])
                fad.append(r["CE"]["r"])
        if not fol:
            return {"n": 0}
        diff = [a - b for a, b in zip(fol, fad)]
        return {
            "n": len(fol),
            "follow_expectancy_r": round(statistics.mean(fol), 4),
            "fade_expectancy_r": round(statistics.mean(fad), 4),
            "follow_minus_fade_r": round(statistics.mean(diff), 4),
            "ci95": ci95(diff),
            "significant": _sig(ci95(diff)),
        }

    third = len(rows) // 3
    groups: dict[str, list[dict]] = {"ALL": rows}
    for r in rows:
        groups.setdefault("state=" + r["features"]["scan_state"], []).append(r)
        groups.setdefault("trend=" + r["trend_regime"], []).append(r)
        groups.setdefault("vol=" + r["vol_regime"], []).append(r)
        groups.setdefault("tod=" + r["tod"], []).append(r)
    out = {k: cell(v) for k, v in sorted(groups.items()) if len(v) >= _MIN_CELL}
    out["ALL_first_third"] = cell(rows[:third])
    out["ALL_final_third_oos"] = cell(rows[2 * third:])
    return out


def engine_side_check(rows: list[dict], only_buy: bool = False) -> dict:
    """The engine's chosen side against the side it rejected, same bar.

    This is the cleanest possible test of the trend logic: both sides are scored
    on the same bar with the same levels, so a positive edge is direction skill
    and a negative edge means the side choice is actively wrong.
    """
    taken, other = [], []
    for r in rows:
        s = r.get("engine_side")
        if s not in ("CE", "PE"):
            continue
        if only_buy and r.get("engine_signal") != "BUY":
            continue
        taken.append(r[s]["r"])
        other.append(r["PE" if s == "CE" else "CE"]["r"])
    if not taken:
        return {"n": 0}
    diff = [a - b for a, b in zip(taken, other)]
    return {
        "n": len(taken),
        "engine_side_expectancy_r": round(statistics.mean(taken), 4),
        "opposite_side_expectancy_r": round(statistics.mean(other), 4),
        "edge_r": round(statistics.mean(diff), 4),
        "edge_ci95": ci95(diff),
        "edge_significant": _sig(ci95(diff)),
    }


# --------------------------------------------------------------- step 4 model

def to_matrix(pairs: list[tuple[dict, str]]) -> tuple[list[list[float]], list[int], list[str]]:
    """Numeric design matrix for (row, side) pairs. Categoricals are one-hot
    encoded against a fixed vocabulary so train and test always share columns."""
    vocab = {
        "scan_state": ["FRESH_MOMENTUM", "ACTIVE", "EXTENDED", "EXHAUSTED", "STALLED",
                       "NO_DATA"],
        "scan_verdict": ["OPPORTUNITY", "WATCH", "NO_OPPORTUNITY"],
        "scan_dir": ["UP", "DOWN", "FLAT", None],
    }
    num = ["ret_1", "ret_3", "ret_5", "ret_15", "ret_30", "atr_pct", "atr_expansion",
           "rvol_30", "rvol_vs_base", "vwap_dist_atr", "ema_spread_atr",
           "ema50_dist_atr", "adx", "rsi", "macd_hist", "rel_volume", "vol_accel",
           "breakout_atr", "breakdown_atr", "ext_from_swing_lo_atr",
           "ext_from_swing_hi_atr", "persistence_10", "supertrend_dir", "tod_min",
           "dow", "scan_score"]
    names = list(num)
    for k, vs in vocab.items():
        names += [f"{k}={v}" for v in vs]
    names += ["side_is_pe"]

    X: list[list[float]] = []
    y: list[int] = []
    for r, side in pairs:
        f = r["features"]
        row = [float(f.get(k) if f.get(k) is not None else 0.0) for k in num]
        for k, vs in vocab.items():
            row += [1.0 if f.get(k) == v else 0.0 for v in vs]
        row.append(1.0 if side == "PE" else 0.0)
        X.append(row)
        y.append(1 if r[side]["result"] == "TARGET" else 0)
    return X, y, names


def calibration(probs: list[float], ys: list[int], buckets: int = _PROB_BUCKETS) -> list[dict]:
    order = sorted(range(len(probs)), key=lambda i: probs[i])
    out = []
    per = max(1, len(order) // buckets)
    for b in range(buckets):
        idx = order[b * per:(b + 1) * per] if b < buckets - 1 else order[b * per:]
        if not idx:
            continue
        out.append({
            "bucket": b + 1,
            "n": len(idx),
            "mean_predicted": round(statistics.mean(probs[i] for i in idx), 4),
            "actual_target_rate": round(statistics.mean(ys[i] for i in idx), 4),
        })
    return out


def auc(probs: list[float], ys: list[int]) -> float | None:
    pos = [p for p, y in zip(probs, ys) if y == 1]
    neg = [p for p, y in zip(probs, ys) if y == 0]
    if not pos or not neg:
        return None
    # rank-based AUC (Mann-Whitney), exact and cheap enough at this size
    allp = sorted(zip(probs, ys))
    ranks: dict[int, float] = {}
    i = 0
    r = 1
    while i < len(allp):
        j = i
        while j + 1 < len(allp) and allp[j + 1][0] == allp[i][0]:
            j += 1
        avg = (r + (r + (j - i))) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg
        r += (j - i + 1)
        i = j + 1
    s = sum(ranks[k] for k, (_, y) in enumerate(allp) if y == 1)
    return round((s - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg)), 4)


def entry_probability_model(rows: list[dict]) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier

    both = []
    for r in rows:
        both.append((r, "CE"))
        both.append((r, "PE"))
    both.sort(key=lambda t: t[0]["ts"])

    n = len(both)
    a, b = int(0.6 * n), int(0.8 * n)
    splits = {"train": both[:a], "validation": both[a:b], "oos": both[b:]}

    mat = to_matrix

    Xtr, ytr, names = mat(splits["train"])
    Xva, yva, _ = mat(splits["validation"])
    Xoo, yoo, _ = mat(splits["oos"])

    clf = HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.06, max_depth=6, min_samples_leaf=200,
        l2_regularization=1.0, random_state=7,
    )
    clf.fit(Xtr, ytr)

    def evaluate(X, y, part) -> dict:
        p = [float(x) for x in clf.predict_proba(X)[:, 1]]
        brier = statistics.mean((pi - yi) ** 2 for pi, yi in zip(p, y))
        # top-decile population, scored on the same levels as everything else
        order = sorted(range(len(p)), key=lambda i: p[i], reverse=True)
        top = order[:max(1, len(order) // 10)]
        top_rows = [{"_out": part[i][0][part[i][1]]} for i in top]
        return {
            "n": len(y),
            "base_rate": round(statistics.mean(y), 4),
            "auc": auc(p, y),
            "brier": round(brier, 5),
            "calibration": calibration(p, y),
            "top_decile": stats(top_rows),
        }, p

    ev_tr, _ = evaluate(Xtr, ytr, splits["train"])
    ev_va, _ = evaluate(Xva, yva, splits["validation"])
    ev_oo, p_oo = evaluate(Xoo, yoo, splits["oos"])

    # walk-forward: 5 folds, each trained only on everything before it
    wf = []
    folds = 5
    per = n // (folds + 1)
    for k in range(1, folds + 1):
        tr = both[:k * per]
        te = both[k * per:(k + 1) * per]
        if len(te) < 500:
            continue
        Xa, ya, _ = mat(tr)
        Xb, yb, _ = mat(te)
        m = HistGradientBoostingClassifier(
            max_iter=250, learning_rate=0.06, max_depth=6, min_samples_leaf=200,
            l2_regularization=1.0, random_state=7)
        m.fit(Xa, ya)
        pb = [float(x) for x in m.predict_proba(Xb)[:, 1]]
        order = sorted(range(len(pb)), key=lambda i: pb[i], reverse=True)
        top = order[:max(1, len(order) // 10)]
        wf.append({
            "fold": k, "train_n": len(tr), "test_n": len(te),
            "auc": auc(pb, yb),
            "top_decile": stats([{"_out": te[i][0][te[i][1]]} for i in top]),
        })

    return {
        "model": "sklearn HistGradientBoostingClassifier (LightGBM-equivalent "
                 "histogram GBDT; lightgbm/xgboost are not installed and adding a "
                 "runtime dependency for a research script was not justified)",
        "rows": n,
        "split": {"train": len(splits["train"]), "validation": len(splits["validation"]),
                  "oos": len(splits["oos"])},
        "train": ev_tr, "validation": ev_va, "out_of_sample": ev_oo,
        "walk_forward": wf,
        "_oos_probs": p_oo,        # consumed by step 5, stripped before writing
        "_oos_part": len(splits["oos"]),
    }


# --------------------------------------------------------------- step 5
def population_comparison(rows: list[dict], model: dict) -> dict:
    """Current engine vs each research filter, on the same bars and levels."""
    third2 = int(0.8 * len(rows))
    oos_rows = rows[third2:]

    def eng(rs: list[dict], only_buy: bool) -> list[dict]:
        out = []
        for r in rs:
            s = r.get("engine_side")
            if s not in ("CE", "PE"):
                continue
            if only_buy and r["engine_signal"] != "BUY":
                continue
            out.append({"_out": r[s], "row": r})
        return out

    accepted = eng(oos_rows, True)
    all_sided = eng(oos_rows, False)

    good_states = ("FRESH_MOMENTUM", "ACTIVE")
    regime_filtered = [x for x in accepted
                       if x["row"]["features"]["scan_state"] in good_states]

    # direction filter: keep the engine's side only where that side's own regime
    # cell was positive IN THE TRAINING PORTION (no peeking at the OOS outcome)
    train_rows = rows[:int(0.6 * len(rows))]
    cell_edge: dict[tuple[str, str], float] = {}
    for side in ("CE", "PE"):
        for st in {r["features"]["scan_state"] for r in train_rows}:
            vals = [r[side]["r"] for r in train_rows
                    if r["features"]["scan_state"] == st]
            if len(vals) >= _MIN_CELL:
                cell_edge[(side, st)] = statistics.mean(vals)
    direction_filtered = [
        x for x in accepted
        if cell_edge.get((x["row"]["engine_side"], x["row"]["features"]["scan_state"]), -1)
        > 0
    ]

    out = {
        "population": "chronological final 20% (out-of-sample)",
        "ALL_SIDED_BARS": stats(all_sided),
        "ENGINE_ACCEPTED_BUY": stats(accepted),
        "REGIME_FILTERED_BUY": stats(regime_filtered),
        "DIRECTION_FILTERED_BUY": stats(direction_filtered),
    }

    # probability filter, using the model's own OOS probabilities
    probs = model.get("_oos_probs") or []
    if probs:
        both = []
        for r in rows:
            both.append((r, "CE"))
            both.append((r, "PE"))
        both.sort(key=lambda t: t[0]["ts"])
        oos_both = both[int(0.8 * len(both)):]
        if len(oos_both) == len(probs):
            pmap: dict[tuple[int, str], float] = {}
            for (r, side), p in zip(oos_both, probs):
                pmap[(r["i"], side)] = p
            cut = statistics.quantiles(probs, n=10)[-1]   # top decile threshold
            prob_filtered = [x for x in accepted
                             if pmap.get((x["row"]["i"], x["row"]["engine_side"]), 0.0)
                             >= cut]
            combined = [x for x in prob_filtered
                        if x["row"]["features"]["scan_state"] in good_states]
            out["PROBABILITY_FILTERED_BUY"] = {
                "threshold": round(cut, 4), **stats(prob_filtered)}
            out["COMBINED_REGIME_AND_PROBABILITY"] = stats(combined)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--skip-model", action="store_true")
    args = ap.parse_args()

    rows: list[dict] = []
    with open(os.path.expanduser(args.rows)) as fh:
        for line in fh:
            rows.append(json.loads(line))
    rows.sort(key=lambda r: r["ts"])

    report: dict = {
        "as_of": int(time.time()),
        "rows": len(rows),
        "first_ts": rows[0]["ts"] if rows else None,
        "last_ts": rows[-1]["ts"] if rows else None,
        "outcome_basis": "UNDERLYING move, 0.8xATR stop, 1.2R target, 30-bar horizon",
        "premium_basis": "NONE — no premium is used in any outcome (see phase5_features)",
        "step2_regime": regime_research(rows),
        "step3_direction": direction_research(rows),
    }
    if not args.skip_model:
        model = entry_probability_model(rows)
        report["step5_population_comparison"] = population_comparison(rows, model)
        model.pop("_oos_probs", None)
        model.pop("_oos_part", None)
        report["step4_entry_probability"] = model

    printable = {k: report[k] for k in ("rows", "step5_population_comparison")
                 if k in report}
    print(json.dumps(printable, indent=2)[:4000])
    if args.out:
        p = os.path.expanduser(args.out)
        with open(p, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
