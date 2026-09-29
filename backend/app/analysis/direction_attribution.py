"""Was the market read actually directional? — five definitions, RESEARCH ONLY.

Phase 12 §6. The number this replaces was ``market_right = best_excursion > 0``:
a call counted as a correct market read if the underlying ever moved a single
paisa the called way before it resolved. On the 25 Aug session that produced
"85% of market signals correct" while the same underlying path finished the
called way only 45% of the time — the first number is nearly unfalsifiable and
should never have been displayed as one figure.

So there is no single figure here. Five explicitly-named definitions are computed
from the same recorded underlying path and reported side by side:

``A_ANY_FAVORABLE_MOVE``
    the underlying moved the called way by any amount at any time. The old
    definition, kept only so the drop from it to the others is visible.
``B_HALF_ATR_BEFORE_ADVERSE``
    a favourable move of >= 0.5 ATR happened *before* the adverse threshold
    (1.0 ATR against) was reached.
``C_ONE_ATR_BEFORE_ADVERSE``
    the same with a >= 1.0 ATR favourable move.
``D_UNDERLYING_TARGET_BEFORE_STOP``
    on the underlying alone, a 1R target was reached before the plan's own
    invalidation level (``Decision.underlying_stop``) was breached. 1R is the
    plan's own stop distance, so no new level is invented here; when the plan
    stated no underlying stop this definition is UNKNOWN rather than assumed.
``E_FINISH_DIRECTION``
    the underlying finished the called way at the end of the follow window.

Each definition returns True, False or None. None means the recorded path cannot
answer it (no ATR, no underlying stop, underlying never recorded) and it is
counted as ``unknown``, never as a failure and never as a pass. A definition with
too few answerable samples reports ``INSUFFICIENT_SAMPLE`` instead of a rate.

Measurement only: nothing here sizes, gates, blocks or scores a trade.
"""
from __future__ import annotations

import json
import os

from app.config import settings

A = "A_ANY_FAVORABLE_MOVE"
B = "B_HALF_ATR_BEFORE_ADVERSE"
C = "C_ONE_ATR_BEFORE_ADVERSE"
D = "D_UNDERLYING_TARGET_BEFORE_STOP"
E = "E_FINISH_DIRECTION"
DEFINITIONS = (A, B, C, D, E)

# The adverse move that ends the "did the favourable move come first" question.
# One ATR against, the same unit the favourable side is measured in, so B and C
# are not comparing a favourable move against an arbitrarily wide leash.
ADVERSE_ATR = 1.0
# Below this many answerable samples a rate is not reported at all.
MIN_SAMPLE = 30

REPORT_JSON = "direction_attribution.json"

_LEGACY_NOTE = (
    "the retired definition was 'the underlying ever moved the called way by "
    "any amount', which is definition A below; it is reported for comparison "
    "only and must not be presented as the system's directional accuracy"
)


def marks_template(atr: float | None, underlying_entry: float | None,
                   underlying_stop: float | None, direction: str) -> dict:
    """The path facts a follow loop must record for §6, as a fresh state dict."""
    sign = -1.0 if str(direction).upper().startswith("BEAR") else 1.0
    stop_dist = None
    if underlying_entry and underlying_stop:
        d = sign * (float(underlying_entry) - float(underlying_stop))
        stop_dist = round(d, 4) if d > 0 else None
    return {
        "atr": float(atr) if atr else None,
        "sign": sign,
        "stop_distance": stop_dist,
        # first time each threshold was crossed, seconds since the signal
        "fav_half_atr_sec": None,
        "fav_one_atr_sec": None,
        "adverse_atr_sec": None,
        "target_1r_sec": None,
        "stop_sec": None,
        "favorable_seen": False,
    }


def observe_underlying(marks: dict, underlying: float, entry: float,
                       elapsed_sec: float) -> None:
    """Update the recorded path with one underlying observation.

    Thresholds are latched on first crossing: the questions are about which came
    first, so a later crossing must not overwrite an earlier one.
    """
    if not entry:
        return
    sign = float(marks.get("sign") or 1.0)
    move = sign * (float(underlying) - float(entry))
    if move > 0:
        marks["favorable_seen"] = True
    atr = marks.get("atr")
    if atr:
        if marks["fav_half_atr_sec"] is None and move >= 0.5 * float(atr):
            marks["fav_half_atr_sec"] = round(elapsed_sec, 1)
        if marks["fav_one_atr_sec"] is None and move >= float(atr):
            marks["fav_one_atr_sec"] = round(elapsed_sec, 1)
        if marks["adverse_atr_sec"] is None and move <= -ADVERSE_ATR * float(atr):
            marks["adverse_atr_sec"] = round(elapsed_sec, 1)
    dist = marks.get("stop_distance")
    if dist:
        if marks["target_1r_sec"] is None and move >= float(dist):
            marks["target_1r_sec"] = round(elapsed_sec, 1)
        if marks["stop_sec"] is None and move <= -float(dist):
            marks["stop_sec"] = round(elapsed_sec, 1)


def _first(a: float | None, b: float | None) -> bool | None:
    """True when ``a`` happened and happened before ``b``."""
    if a is None and b is None:
        return False
    if a is None:
        return False
    if b is None:
        return True
    return a < b


def verdicts(marks: dict, finish_move: float | None) -> dict:
    """The five definitions for one resolved signal: True / False / None."""
    atr = marks.get("atr")
    dist = marks.get("stop_distance")
    return {
        A: bool(marks.get("favorable_seen")),
        B: (None if not atr
            else _first(marks.get("fav_half_atr_sec"), marks.get("adverse_atr_sec"))),
        C: (None if not atr
            else _first(marks.get("fav_one_atr_sec"), marks.get("adverse_atr_sec"))),
        D: (None if not dist
            else _first(marks.get("target_1r_sec"), marks.get("stop_sec"))),
        E: (None if finish_move is None else finish_move > 0),
    }


def summarise(rows: list[dict], *, min_sample: int = MIN_SAMPLE) -> dict:
    """Aggregate per-signal verdict dicts into the §6 report body.

    ``rows`` are dicts keyed by definition name (as ``verdicts`` returns), each
    optionally carrying ``instrument``, ``family`` and ``market``.
    """
    out: dict = {}
    for name in DEFINITIONS:
        answered = [r for r in rows if r.get(name) is not None]
        correct = [r for r in answered if r.get(name)]
        rate = (round(100.0 * len(correct) / len(answered), 1)
                if len(answered) >= min_sample else None)
        out[name] = {
            "definition": name,
            "sample": len(answered),
            "correct": len(correct),
            "unknown": len(rows) - len(answered),
            "correct_pct": rate,
            "status": "REPORTED" if rate is not None else "INSUFFICIENT_SAMPLE",
            "min_sample": min_sample,
        }
    return {
        "resolved_signals": len(rows),
        "min_sample": min_sample,
        "adverse_threshold_atr": ADVERSE_ATR,
        "retired_definition_note": _LEGACY_NOTE,
        "single_number_withheld": True,
        "single_number_reason": (
            "no one definition is validated yet; a directional accuracy figure "
            "requires the >=20 sessions and >=5 holdout sessions of Phase 12 §15"
        ),
        "definitions": out,
        "research_only": True,
    }


def summarise_outcomes(outcomes: list[dict], *,
                       min_sample: int = MIN_SAMPLE) -> dict:
    """Summarise resolved journal outcome rows (``event == "RESOLVED"``).

    Rows resolved before this build carry no ``direction_attribution`` block and
    are counted as ``rows_without_path`` rather than being back-filled from the
    retired definition, which would reproduce the number being replaced.
    """
    resolved = [r for r in outcomes if r.get("event") == "RESOLVED"]
    withpath = [r["direction_attribution"] for r in resolved
                if isinstance(r.get("direction_attribution"), dict)]
    body = summarise(withpath, min_sample=min_sample)
    body["resolved_rows"] = len(resolved)
    body["rows_without_path"] = len(resolved) - len(withpath)
    body["legacy_any_favorable_pct"] = _legacy_rate(resolved)
    return body


def _legacy_rate(resolved: list[dict]) -> float | None:
    """The retired figure, reported once so the size of the correction is visible."""
    answered = [r for r in resolved if r.get("market_signal_correct") is not None]
    if not answered:
        return None
    hits = sum(1 for r in answered if r.get("market_signal_correct"))
    return round(100.0 * hits / len(answered), 1)


def write_report(body: dict) -> str:
    os.makedirs(settings.data_dir, exist_ok=True)
    path = os.path.join(settings.data_dir, REPORT_JSON)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2, default=str)
    os.replace(tmp, path)
    return path
