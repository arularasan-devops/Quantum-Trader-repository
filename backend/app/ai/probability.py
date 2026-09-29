"""Trade-probability engine — calibrated P(target before stop).

The model is trained offline by ``phase6_train.py`` and shipped as a plain JSON
artefact: standardisation constants, logistic coefficients and a calibration
table. Scoring here is ~30 lines of arithmetic, which buys three things that
matter more than the last point of AUC:

* **No ML runtime dependency.** scikit-learn stays an offline research package,
  so the live process cannot import a model stack and the artefact is auditable
  by reading it.
* **No silent fallback.** If the artefact is missing, stale or its feature list
  does not match :data:`app.ai.features.FEATURES`, this engine reports
  UNAVAILABLE and the orchestrator cannot emit BUY_NOW. A guessed probability is
  worse than none.
* **Calibration is explicit.** ``p_calibrated`` comes from the measured
  out-of-sample frequency for the bucket the raw score falls in, so the number on
  the dashboard means what it says.

Honesty note carried from Phase 5 and reprinted by :func:`status`: the OOS AUC of
this family of model was ~0.53 and predictions live in a narrow band. It is a
weak signal used as one input among several, not an oracle.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time

from app.ai import features as F

_lock = threading.Lock()
_cache: dict = {"path": None, "mtime": 0.0, "artefact": None, "error": ""}

UNAVAILABLE = "UNAVAILABLE"


def _path() -> str:
    from app.config import settings

    return (settings.ai_model_path
            or os.path.join(settings.data_dir, "ai_model.json"))


def validate(art: dict) -> str | None:
    """Reason the artefact is unusable, or None when it is usable."""
    if not isinstance(art, dict):
        return "artefact is not an object"
    for key in ("features", "coef", "intercept", "mean", "scale", "calibration"):
        if key not in art:
            return f"artefact missing '{key}'"
    if list(art["features"]) != list(F.FEATURES):
        return ("artefact feature list does not match app.ai.features.FEATURES "
                "— retrain with phase6_train.py")
    n = len(art["features"])
    if not (len(art["coef"]) == len(art["mean"]) == len(art["scale"]) == n):
        return "artefact coefficient/scaling length mismatch"
    if not art["calibration"]:
        return "artefact has an empty calibration table"
    return None


def artefact() -> tuple[dict | None, str]:
    """The loaded model artefact and an error string. Reloads when the file changes."""
    path = _path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        with _lock:
            _cache.update({"path": path, "artefact": None, "mtime": 0.0,
                           "error": f"no model artefact at {path} — run phase6_train.py"})
            return None, _cache["error"]
    with _lock:
        if _cache["artefact"] is not None and _cache["path"] == path \
                and _cache["mtime"] == mtime:
            return _cache["artefact"], _cache["error"]
    try:
        with open(path) as fh:
            art = json.load(fh)
    except Exception as exc:
        with _lock:
            _cache.update({"path": path, "artefact": None, "mtime": mtime,
                           "error": f"model artefact unreadable: {exc}"})
            return None, _cache["error"]
    err = validate(art) or ""
    with _lock:
        _cache.update({"path": path, "mtime": mtime,
                       "artefact": None if err else art, "error": err})
    return (None if err else art), err


def _calibrate(raw: float, table: list[dict]) -> tuple[float, int | None]:
    """Map a raw score to the measured OOS frequency of its bucket.

    Linear interpolation between bucket centres, clamped at the ends: the model
    has never been observed outside the range it produced in training, so
    extrapolating there would be inventing precision.
    """
    if not table:
        return raw, None
    pts = sorted(((float(b["predicted"]), float(b["actual"]), int(b.get("bucket", i)))
                  for i, b in enumerate(table)), key=lambda t: t[0])
    if raw <= pts[0][0]:
        return pts[0][1], pts[0][2]
    if raw >= pts[-1][0]:
        return pts[-1][1], pts[-1][2]
    for (x0, y0, b0), (x1, y1, _b1) in zip(pts, pts[1:]):
        if x0 <= raw <= x1:
            w = 0.0 if x1 == x0 else (raw - x0) / (x1 - x0)
            return y0 + w * (y1 - y0), b0
    return raw, None


def score(feats: dict, side: str) -> dict:
    """P(target before stop) for one side at this bar.

    Returns ``{"available": bool, ...}``; when unavailable the caller must treat
    it as "no probability", never as 0.5.
    """
    art, err = artefact()
    if art is None:
        return {"available": False, "reason": err or UNAVAILABLE,
                "p_target_before_stop": None, "p_stop_before_target": None}

    x = F.vector(feats, side)
    z = float(art["intercept"])
    for xi, m, s, w in zip(x, art["mean"], art["scale"], art["coef"]):
        s = float(s) or 1.0
        z += float(w) * ((xi - float(m)) / s)
    raw = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
    cal, bucket = _calibrate(raw, art.get("calibration") or [])
    lv = art.get("levels", {})
    rr = float(lv.get("reward_risk", 1.2))
    return {
        "available": True,
        "p_target_before_stop": round(cal, 4),
        "p_stop_before_target": round(1.0 - cal, 4),
        "p_raw": round(raw, 4),
        "calibration_bucket": bucket,
        # Expected R implied by the calibrated probability at the trained levels.
        # Underlying-only, before option costs — the same caveat as all Phase 5 work.
        "expected_r": round(cal * rr - (1.0 - cal), 4),
        "model_version": art.get("version") or art.get("trained_at"),
        "oos_auc": (art.get("metrics", {}).get("oos", {}) or {}).get("auc"),
    }


def status() -> dict:
    """What model is loaded, how it was validated, and how much to trust it."""
    art, err = artefact()
    if art is None:
        return {
            "available": False, "reason": err, "path": _path(),
            "note": ("Without an artefact the AI engine cannot emit BUY_NOW. "
                     "Build one offline: phase6_dataset.py then phase6_train.py."),
        }
    m = art.get("metrics", {})
    return {
        "available": True,
        "path": _path(),
        "version": art.get("version"),
        "trained_at": art.get("trained_at"),
        "trained_at_ist": art.get("trained_at_ist"),
        "train_rows": art.get("train_rows"),
        "instruments": art.get("instruments"),
        "features": len(art.get("features", [])),
        "levels": art.get("levels"),
        "outcome_basis": art.get("outcome_basis"),
        "split": art.get("split"),
        "metrics": m,
        "calibration": art.get("calibration"),
        "loaded_at": int(time.time()),
        "limitations": art.get("limitations"),
    }


def reset() -> None:
    """Drop the cached artefact so the next score re-reads it from disk."""
    with _lock:
        _cache.update({"path": None, "mtime": 0.0, "artefact": None, "error": ""})
