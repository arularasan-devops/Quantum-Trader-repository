"""Phase 6 pass 2 — train, validate and export the trade-probability artefact.

Offline only. scikit-learn stays in ``requirements-research.txt`` and is never
imported by the running application: this script exports a JSON artefact
(standardisation constants, logistic coefficients, calibration table, metrics)
that :mod:`app.ai.probability` scores with plain arithmetic.

Methodology, fixed and not negotiable per phase:

* **Chronological** split — 60% train, 20% validation, 20% out-of-sample. No
  shuffle, no k-fold over time, no peeking. Both sides of a bar go into the same
  split so one side cannot leak the other.
* **Calibration is fitted on VALIDATION and reported on OOS.** Fitting it on the
  data it is measured on is the standard way to publish a beautifully calibrated
  model that is calibrated to nothing.
* **Walk-forward** over 10 expanding folds, reported per fold. A model that is
  positive in 6 of 10 folds is not a model.
* Promotion is manual. ``--promote`` copies the artefact to where the live engine
  reads it, and a human has to run it. Nothing in the application does this.

    .venv/bin/python phase6_train.py --data ~/p6_crude.jsonl ~/p6_nifty.jsonl \\
        --out ~/p6_model.json --report ~/p6_model_report.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import time

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from app.ai import features as F

TRAIN_FRAC = 0.6
VAL_FRAC = 0.2
BUCKETS = 10
FOLDS = 10


def load(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        with open(os.path.expanduser(p)) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    rows.sort(key=lambda r: int(r["ts"]))
    return rows


def matrix(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(X, y, r) with one row per (bar, side). Order follows time, then side."""
    xs: list[list[float]] = []
    ys: list[int] = []
    rs: list[float] = []
    for row in rows:
        for side in ("CE", "PE"):
            out = row.get(side) or {}
            if out.get("result") not in ("TARGET", "STOP", "OPEN"):
                continue
            xs.append(F.vector(row["features"], side))
            ys.append(int(out.get("target_before_stop") or 0))
            rs.append(float(out.get("r") or 0.0))
    return np.array(xs, dtype=float), np.array(ys, dtype=int), np.array(rs, dtype=float)


def fit(x: np.ndarray, y: np.ndarray) -> tuple[LogisticRegression, np.ndarray, np.ndarray]:
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0
    model = LogisticRegression(max_iter=2000, C=0.5)
    model.fit((x - mean) / scale, y)
    return model, mean, scale


def predict(model: LogisticRegression, mean: np.ndarray, scale: np.ndarray,
            x: np.ndarray) -> np.ndarray:
    return model.predict_proba((x - mean) / scale)[:, 1]


def calibration(p: np.ndarray, y: np.ndarray, buckets: int = BUCKETS) -> list[dict]:
    """Equal-count buckets: predicted mean vs realised frequency."""
    order = np.argsort(p)
    out: list[dict] = []
    chunks = np.array_split(order, buckets)
    for i, idx in enumerate(chunks):
        if len(idx) == 0:
            continue
        out.append({
            "bucket": i,
            "n": int(len(idx)),
            "p_low": round(float(p[idx].min()), 4),
            "p_high": round(float(p[idx].max()), 4),
            "predicted": round(float(p[idx].mean()), 4),
            "actual": round(float(y[idx].mean()), 4),
        })
    return out


def apply_calibration(p: np.ndarray, table: list[dict]) -> np.ndarray:
    pts = sorted(((b["predicted"], b["actual"]) for b in table), key=lambda t: t[0])
    xs = np.array([a for a, _ in pts])
    ys = np.array([b for _, b in pts])
    return np.interp(p, xs, ys)


def population(p: np.ndarray, y: np.ndarray, r: np.ndarray,
               threshold: float) -> dict:
    sel = p >= threshold
    n = int(sel.sum())
    if not n:
        return {"threshold": threshold, "trades": 0}
    rr = r[sel]
    wins = rr[rr > 0]
    losses = rr[rr <= 0]
    return {
        "threshold": threshold,
        "trades": n,
        "share_pct": round(100.0 * n / len(p), 2),
        "target_before_stop_pct": round(100.0 * float(y[sel].mean()), 2),
        "expectancy_r": round(float(rr.mean()), 4),
        "profit_factor": (round(float(wins.sum() / -losses.sum()), 3)
                          if len(losses) and losses.sum() else None),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default="")
    ap.add_argument("--promote", action="store_true",
                    help="copy the artefact to the path the live engine reads "
                         "(manual step, on purpose)")
    args = ap.parse_args()

    t0 = time.time()
    rows = load(args.data)
    if len(rows) < 2000:
        raise SystemExit(f"only {len(rows)} rows — not enough to train honestly")
    instruments = sorted({str(r.get("instrument")) for r in rows})

    n = len(rows)
    i_tr = int(n * TRAIN_FRAC)
    i_va = int(n * (TRAIN_FRAC + VAL_FRAC))
    split_ts = {
        "train": [int(rows[0]["ts"]), int(rows[i_tr - 1]["ts"])],
        "validation": [int(rows[i_tr]["ts"]), int(rows[i_va - 1]["ts"])],
        "oos": [int(rows[i_va]["ts"]), int(rows[-1]["ts"])],
    }
    x_tr, y_tr, r_tr = matrix(rows[:i_tr])
    x_va, y_va, r_va = matrix(rows[i_tr:i_va])
    x_oos, y_oos, r_oos = matrix(rows[i_va:])

    model, mean, scale = fit(x_tr, y_tr)
    p_tr = predict(model, mean, scale, x_tr)
    p_va = predict(model, mean, scale, x_va)
    p_oos = predict(model, mean, scale, x_oos)

    # Calibration fitted on validation, measured on OOS.
    table = calibration(p_va, y_va)
    c_oos = apply_calibration(p_oos, table)

    metrics = {
        "train": {"rows": int(len(y_tr)), "base_rate": round(float(y_tr.mean()), 4),
                  "auc": round(float(roc_auc_score(y_tr, p_tr)), 4)},
        "validation": {"rows": int(len(y_va)), "base_rate": round(float(y_va.mean()), 4),
                       "auc": round(float(roc_auc_score(y_va, p_va)), 4)},
        "oos": {
            "rows": int(len(y_oos)), "base_rate": round(float(y_oos.mean()), 4),
            "auc": round(float(roc_auc_score(y_oos, p_oos)), 4),
            "brier": round(float(np.mean((c_oos - y_oos) ** 2)), 5),
            "p_range": [round(float(c_oos.min()), 4), round(float(c_oos.max()), 4)],
            "mean_abs_calibration_error": round(float(np.mean(np.abs(
                np.array([b["predicted"] - b["actual"]
                          for b in calibration(c_oos, y_oos)])))), 4),
        },
        "oos_calibration": calibration(c_oos, y_oos),
        "oos_populations": [population(c_oos, y_oos, r_oos, t)
                            for t in (0.45, 0.5, 0.52, 0.55, 0.6)],
    }

    # Walk-forward: expanding train, next slice OOS, chronological throughout.
    folds = []
    step = n // (FOLDS + 1)
    for k in range(1, FOLDS + 1):
        tr = rows[: step * k]
        te = rows[step * k: step * (k + 1)]
        if len(te) < 200 or len(tr) < 1000:
            continue
        xa, ya, _ra = matrix(tr)
        xb, yb, rb = matrix(te)
        if len(set(ya.tolist())) < 2 or len(set(yb.tolist())) < 2:
            continue
        m2, mu2, sd2 = fit(xa, ya)
        pb = predict(m2, mu2, sd2, xb)
        top = pb >= np.quantile(pb, 0.9)
        folds.append({
            "fold": k,
            "train_rows": int(len(ya)), "test_rows": int(len(yb)),
            "auc": round(float(roc_auc_score(yb, pb)), 4),
            "top_decile_expectancy_r": round(float(rb[top].mean()), 4),
            "all_expectancy_r": round(float(rb.mean()), 4),
        })
    positive = sum(1 for f in folds if f["top_decile_expectancy_r"] > 0)

    now = time.time()
    artefact = {
        "version": f"p6-{dt.datetime.utcfromtimestamp(now):%Y%m%d-%H%M%S}",
        "model": "logistic_regression_standardised",
        "trained_at": int(now),
        "trained_at_ist": (dt.datetime.utcfromtimestamp(now)
                           + dt.timedelta(hours=5, minutes=30)).strftime(
                               "%Y-%m-%d %H:%M:%S IST"),
        "features": list(F.FEATURES),
        "coef": [round(float(c), 6) for c in model.coef_[0]],
        "intercept": round(float(model.intercept_[0]), 6),
        "mean": [round(float(v), 6) for v in mean],
        "scale": [round(float(v), 6) for v in scale],
        "calibration": table,
        "train_rows": int(len(y_tr)),
        "instruments": instruments,
        "levels": {"stop_atr": 0.8, "reward_risk": 1.2, "horizon_bars": 30},
        "outcome_basis": "UNDERLYING move (no premium, no theta, no spread)",
        "split": {"method": "chronological 60/20/20, no shuffle", **split_ts},
        "metrics": metrics,
        "walk_forward": {"folds": folds, "positive_folds": positive,
                         "total_folds": len(folds)},
        "limitations": [
            "Trained on underlying moves, not option premiums: no real option "
            "chain exists in the archive (0 REAL_BROKER snapshots), so IV, OI, "
            "volume and spread are absent as features.",
            "Theta, spread and slippage push realised option outcomes BELOW these "
            "numbers; nothing here is a P&L forecast.",
            "Discrimination is weak by construction of the problem (~0.53 AUC in "
            "Phase 5). Use it to remove trades, not to justify them.",
            "Approved for PAPER trading and research only. Promotion to anything "
            "else is a manual human decision.",
        ],
    }

    out = os.path.expanduser(args.out)
    with open(out, "w") as fh:
        json.dump(artefact, fh, indent=2)
    if args.report:
        rep = os.path.expanduser(args.report)
        with open(rep, "w") as fh:
            json.dump({k: v for k, v in artefact.items()
                       if k not in ("coef", "mean", "scale")}, fh, indent=2)

    promoted = None
    if args.promote:
        from app.config import settings

        dest = (settings.ai_model_path
                or os.path.join(settings.data_dir, "ai_model.json"))
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        shutil.copyfile(out, dest)
        promoted = dest

    print(json.dumps({
        "rows": n, "instruments": instruments,
        "oos_auc": metrics["oos"]["auc"],
        "oos_brier": metrics["oos"]["brier"],
        "oos_p_range": metrics["oos"]["p_range"],
        "walk_forward_positive": f"{positive}/{len(folds)}",
        "artefact": out, "promoted_to": promoted,
        "seconds": round(time.time() - t0, 1),
    }, indent=2))


if __name__ == "__main__":
    main()
