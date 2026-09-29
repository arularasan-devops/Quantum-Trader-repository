"""Phase 10 §7 — SIGNAL SCORE -> calibrated probability. RESEARCH ONLY, GATED.

This is the module most likely to do damage, so it is built to refuse.

A mapping from score to probability is trivially easy to fit and trivially easy to
fit *wrongly*: with 393 trades over 4 sessions, an isotonic curve will reproduce
this sample's noise and report a beautiful in-sample fit. Shipping that would put a
number on the screen that looks like a probability, is called a probability, and is
worth less than the score it replaced — which is precisely the defect Phase 10
exists to correct, reintroduced with more authority.

So the code enforces three things rather than recommending them:

1. **Chronological three-way split.** Earliest sessions TRAIN, the next block
   DEVELOPS (picks between methods), the latest sessions are an untouched HOLDOUT
   scored once. Never a random split: trades within a session are correlated
   through the same regime and the same feed, so a random split leaks tomorrow
   into today and every metric improves for the wrong reason.
2. **A sample gate.** Below ``gates10.MIN_CALIBRATION_TRAIN_ROWS`` training rows or
   ``MIN_CALIBRATION_HOLDOUT_ROWS`` holdout rows, no curve is fitted at all and the
   status is ``MODEL_UNAVAILABLE``. Not a weak curve with a warning — no curve.
3. **A skill test on the holdout.** Even a fitted curve is only reported as usable
   if it beats the constant base-rate forecast on data it never saw. A mapping that
   cannot beat "always say 10.7%" has no business being displayed.

Methods are Platt (logistic, 2 parameters, hard to overfit) and isotonic
(non-parametric, monotone, easy to overfit). Both are fitted on TRAIN, compared on
DEV, and only the winner is scored on HOLDOUT — once.

Nothing here is deployed. The returned mapping is data in a report; no production
module imports this file and no display reads it.
"""
from __future__ import annotations

import math

from .gates10 import (
    MIN_CALIBRATION_BLOCKS,
    MIN_CALIBRATION_HOLDOUT_ROWS,
    MIN_CALIBRATION_TRAIN_ROWS,
)

MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
FITTED_NOT_DEPLOYABLE = "FITTED_NOT_DEPLOYABLE"
FITTED_HOLDOUT_TESTED = "FITTED_HOLDOUT_TESTED"

PLATT = "PLATT"
ISOTONIC = "ISOTONIC"


def xy(rows: list[dict]) -> tuple[list[float], list[float]]:
    xs = [min(1.0, max(0.0, float(r["confidence"]) / 100.0)) for r in rows]
    ys = [1.0 if r.get("target_before_stop_hit") else 0.0 for r in rows]
    return xs, ys


def _brier(ps: list[float], ys: list[float]) -> float | None:
    if not ps:
        return None
    return sum((p - y) ** 2 for p, y in zip(ps, ys, strict=True)) / len(ps)


def _log_loss(ps: list[float], ys: list[float]) -> float | None:
    if not ps:
        return None
    eps = 1e-6
    total = 0.0
    for p, y in zip(ps, ys, strict=True):
        q = min(1.0 - eps, max(eps, p))
        total += -(y * math.log(q) + (1.0 - y) * math.log(1.0 - q))
    return total / len(ps)


def fit_platt(xs: list[float], ys: list[float], *, iters: int = 400,
              lr: float = 0.5) -> dict | None:
    """Logistic calibration p = sigma(a*x + b), fitted by gradient descent.

    Two parameters on purpose. Gradient descent rather than a Newton step because
    a separable sample makes the Hessian singular, and a silent divide is a worse
    failure than a slower fit.
    """
    if len(xs) < 10 or len(set(ys)) < 2:
        return None
    a, b = 1.0, 0.0
    n = len(xs)
    for _ in range(iters):
        ga = gb = 0.0
        for x, y in zip(xs, ys, strict=True):
            p = 1.0 / (1.0 + math.exp(-(a * x + b)))
            d = p - y
            ga += d * x
            gb += d
        a -= lr * ga / n
        b -= lr * gb / n
    return {"method": PLATT, "a": round(a, 6), "b": round(b, 6)}


def apply_platt(model: dict, x: float) -> float:
    return 1.0 / (1.0 + math.exp(-(model["a"] * x + model["b"])))


def fit_isotonic(xs: list[float], ys: list[float]) -> dict | None:
    """Monotone non-decreasing step fit by pool-adjacent-violators."""
    if len(xs) < 10 or len(set(ys)) < 2:
        return None
    pts = sorted(zip(xs, ys, strict=True))
    blocks = [[x, y, 1.0] for x, y in pts]  # [x_right, mean_y, weight]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][1] <= blocks[i + 1][1]:
            i += 1
            continue
        w = blocks[i][2] + blocks[i + 1][2]
        mean = (blocks[i][1] * blocks[i][2] + blocks[i + 1][1] * blocks[i + 1][2]) / w
        blocks[i] = [blocks[i + 1][0], mean, w]
        del blocks[i + 1]
        if i:
            i -= 1
    return {"method": ISOTONIC,
            "steps": [{"score_upto": round(x, 4), "probability": round(y, 4),
                       "weight": int(w)} for x, y, w in blocks]}


def apply_isotonic(model: dict, x: float) -> float:
    for step in model["steps"]:
        if x <= step["score_upto"]:
            return float(step["probability"])
    return float(model["steps"][-1]["probability"]) if model["steps"] else 0.0


def apply(model: dict, x: float) -> float:
    return apply_platt(model, x) if model["method"] == PLATT else apply_isotonic(model, x)


def _score(model: dict, rows: list[dict]) -> dict:
    xs, ys = xy(rows)
    ps = [apply(model, x) for x in xs]
    base = sum(ys) / len(ys) if ys else None
    ref = _brier([base] * len(ys), ys) if base is not None else None
    brier = _brier(ps, ys)
    return {
        "n": len(rows),
        "brier_score": None if brier is None else round(brier, 4),
        "log_loss": None if _log_loss(ps, ys) is None else round(_log_loss(ps, ys), 4),
        "base_rate": None if base is None else round(base, 4),
        "brier_of_constant_base_rate_forecast": None if ref is None else round(ref, 4),
        "beats_constant_forecast": bool(brier is not None and ref is not None
                                        and brier < ref),
        "raw_score_brier": round(_brier(xs, ys), 4) if xs else None,
    }


def split(rows: list[dict], sessions_ordered: list[str]) -> dict:
    """Chronological TRAIN / DEV / HOLDOUT by session, never by trade."""
    n = len(sessions_ordered)
    if n < MIN_CALIBRATION_BLOCKS:
        return {"usable": False, "sessions": n,
                "blocks_required": MIN_CALIBRATION_BLOCKS,
                "train_sessions": sessions_ordered, "dev_sessions": [],
                "holdout_sessions": [],
                "reason": f"{n} session(s) cannot form {MIN_CALIBRATION_BLOCKS} "
                          f"chronological blocks"}
    a = max(1, int(0.5 * n))
    b = max(a + 1, int(0.75 * n))
    return {"usable": True, "sessions": n,
            "train_sessions": sessions_ordered[:a],
            "dev_sessions": sessions_ordered[a:b],
            "holdout_sessions": sessions_ordered[b:]}


def build(rows: list[dict], sessions_ordered: list[str]) -> dict:
    """Fit only if the sample supports it; otherwise say so and stop."""
    rows = [r for r in rows if r.get("confidence") is not None]
    sp = split(rows, sessions_ordered)
    train = [r for r in rows if r["session"] in set(sp["train_sessions"])]
    dev = [r for r in rows if r["session"] in set(sp.get("dev_sessions") or [])]
    hold = [r for r in rows if r["session"] in set(sp.get("holdout_sessions") or [])]

    enough = (sp["usable"] and len(train) >= MIN_CALIBRATION_TRAIN_ROWS
              and len(hold) >= MIN_CALIBRATION_HOLDOUT_ROWS)
    common = {
        "split": sp,
        "train_rows": len(train),
        "dev_rows": len(dev),
        "holdout_rows": len(hold),
        "required": {"train_rows": MIN_CALIBRATION_TRAIN_ROWS,
                     "holdout_rows": MIN_CALIBRATION_HOLDOUT_ROWS,
                     "chronological_blocks": MIN_CALIBRATION_BLOCKS},
        "methods_considered": [PLATT, ISOTONIC],
        "outcome_modelled": "the engine's own target reached before its own stop",
        "guarantee": "no mapping produced here is deployed, displayed or imported by "
                     "any production module. The score formula is unchanged",
    }
    if not enough:
        return {
            **common,
            "status": MODEL_UNAVAILABLE,
            "mapping": None,
            "reason": (
                f"the sample does not support a calibration mapping: "
                f"{len(train)} training rows against {MIN_CALIBRATION_TRAIN_ROWS} "
                f"required and {len(hold)} unseen holdout rows against "
                f"{MIN_CALIBRATION_HOLDOUT_ROWS} required, over "
                f"{sp['sessions']} session(s)"),
            "what_would_change_this": (
                "more recorded sessions. A curve fitted below these thresholds "
                "reproduces this sample's noise and would be displayed as a "
                "probability — the precise error this phase was created to fix, so "
                "it is refused rather than caveated"),
            "interim_guidance": "keep reading the number as a SIGNAL SCORE. Rank "
                                "setups with it if the monotonicity test supports "
                                "that; do not read it as a chance of profit",
        }

    xs_tr, ys_tr = xy(train)
    models = [m for m in (fit_platt(xs_tr, ys_tr), fit_isotonic(xs_tr, ys_tr)) if m]
    if not models:
        return {**common, "status": MODEL_UNAVAILABLE, "mapping": None,
                "reason": "the training block has only one outcome class, so no "
                          "mapping is identifiable"}

    dev_scores = {m["method"]: _score(m, dev) for m in models} if dev else {}
    if dev:
        chosen = min(models, key=lambda m: dev_scores[m["method"]]["brier_score"]
                     if dev_scores[m["method"]]["brier_score"] is not None else 9.9)
    else:
        chosen = models[0]
    holdout = _score(chosen, hold)
    deployable = bool(holdout["beats_constant_forecast"])
    return {
        **common,
        "status": FITTED_HOLDOUT_TESTED if deployable else FITTED_NOT_DEPLOYABLE,
        "mapping": chosen,
        "chosen_by": "lowest Brier score on the development block" if dev
                     else "only method that fitted",
        "development_scores": dev_scores,
        "holdout_scores": holdout,
        "holdout_beats_constant_forecast": deployable,
        "example_mapping": [
            {"signal_score": s,
             "calibrated_probability": round(apply(chosen, s / 100.0), 3)}
            for s in (70, 75, 80, 85, 90, 95, 100)],
        "reading": (
            "the mapping beat a constant base-rate forecast on sessions it never "
            "saw. That makes it a candidate, not a deployment: promotion needs the "
            "full acceptance gates, and this phase promotes nothing"
            if deployable else
            "the mapping did NOT beat a constant base-rate forecast on unseen "
            "sessions, so it is worse than useless — displaying it would replace an "
            "uncalibrated score with an uncalibrated probability"),
    }
