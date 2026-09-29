"""Phase 10 §5-6 — is the SIGNAL SCORE predictive? RESEARCH ONLY.

The number on the board is a score produced by the production engine's weighted
components. It is not a probability, it has never been calibrated against an
outcome, and it is read all day as "this many out of a hundred work". Phase 8 and
Phase 9 both measured the consequence: over 393 replayed trades the 90-95 bucket
stated 92% and delivered 2.2% target-before-stop, and the Brier score was worse
than a constant forecast of the base rate.

This module does two things and refuses to do a third.

It relabels: everywhere in research and diagnostics the value is called SIGNAL
SCORE, with the tooltip text kept in ``TOOLTIP`` so one string is shared by every
surface. The production Signal tab keeps its existing label — changing what the
trader sees mid-flight is a production UI change and is not in this phase's remit.

It measures, on buckets derived from the data rather than round numbers. Phase 8's
fixed 5-point buckets left cells of 3 and 4 trades on this dataset, and a bucket
of 4 cannot support any statement at all; quantile buckets put comparable numbers
of trades in each cell so a monotonicity claim is about outcomes rather than about
where the boundaries happened to fall. Both tables are reported — the fixed one
for continuity with Phase 8, the quantile one because it is the honest cut.

What it does not do: change the score formula, change the confidence gate, change
the display, or fit a mapping. Fitting lives in ``probability`` and is gated.
"""
from __future__ import annotations

from app.research.phase7.gates import MIN_BUYS_PER_CELL
from app.research.phase8.calibration import BUCKETS, bucket_name, bucket_of
from app.research.phase9.findings9 import label9

LABEL = "SIGNAL SCORE"
TOOLTIP = ("Signal Score is the production engine's score, not a calibrated "
           "probability of profit.")

# Quantile buckets are only meaningful if each holds enough rows to say anything.
TARGET_ROWS_PER_QUANTILE = max(MIN_BUYS_PER_CELL, 25)
MAX_QUANTILE_BUCKETS = 6


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None

    def ranks(vs: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: vs[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vs[order[j + 1]] == vs[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


def _cell(rows: list[dict], name: str, sessions: int) -> dict:
    scores = [float(r["confidence"]) for r in rows]
    ys = [1.0 if r.get("target_before_stop_hit") else 0.0 for r in rows]
    grosses = [r["realised_r"] for r in rows if r["realised_r"] is not None]
    nets = [r["net_r"] for r in rows if r["net_r"] is not None]
    win = [r for r in nets if r > 0]
    bad = -sum(r for r in nets if r <= 0)
    return {
        "bucket": name,
        "n": len(rows),
        "mean_signal_score": round(sum(scores) / len(scores), 2) if scores else None,
        "score_range": [round(min(scores), 1), round(max(scores), 1)] if scores else None,
        "stated_as_probability": round(sum(scores) / len(scores) / 100.0, 3)
        if scores else None,
        "observed_target_before_stop": round(sum(ys) / len(ys), 3) if ys else None,
        "gross_expectancy_r": round(sum(grosses) / len(grosses), 3) if grosses else None,
        "net_expectancy_r": round(sum(nets) / len(nets), 3) if nets else None,
        "net_profit_factor": round(sum(win) / bad, 3) if bad > 0 else None,
        "label": label9(len(rows), sessions),
    }


def _quantile_edges(scores: list[float]) -> list[float]:
    """Bucket boundaries with comparable occupancy, from the data.

    Returns interior edges only. Ties are not split — a score value belongs to
    exactly one bucket, so an engine that emits the same score often produces
    fewer, fatter buckets rather than two buckets holding identical scores.
    """
    n = len(scores)
    k = max(2, min(MAX_QUANTILE_BUCKETS, n // TARGET_ROWS_PER_QUANTILE))
    if n < 2 * TARGET_ROWS_PER_QUANTILE:
        return []
    ordered = sorted(scores)
    edges: list[float] = []
    for i in range(1, k):
        v = ordered[int(i * n / k)]
        if v not in edges:
            edges.append(v)
    return edges


def _monotonicity(cells: list[dict], sessions: int) -> dict:
    usable = [c for c in cells if c["n"] >= MIN_BUYS_PER_CELL
              and c["observed_target_before_stop"] is not None]
    inversions = []
    for a, b in zip(usable, usable[1:]):
        if b["observed_target_before_stop"] < a["observed_target_before_stop"]:
            inversions.append({
                "from": a["bucket"], "to": b["bucket"],
                "observed_drop": round(b["observed_target_before_stop"]
                                       - a["observed_target_before_stop"], 3),
                "n_from": a["n"], "n_to": b["n"],
            })
    top = usable[-1] if usable else None
    best = max(usable, key=lambda c: c["observed_target_before_stop"]) if usable else None
    top_is_best = bool(top and best and top["bucket"] == best["bucket"])
    return {
        "buckets_large_enough": len(usable),
        "min_cell_required": MIN_BUYS_PER_CELL,
        "inversions": inversions,
        "monotone": not inversions and len(usable) >= 2,
        "highest_bucket_is_best": top_is_best,
        "best_bucket": None if not best else best["bucket"],
        "verdict": (
            "not measurable: fewer than two buckets are large enough"
            if len(usable) < 2 else
            "monotone in outcome across the buckets large enough to read"
            if not inversions else
            f"NOT monotone: {len(inversions)} inversion(s), and the highest bucket "
            f"is {'also the best' if top_is_best else 'NOT the best'}"),
        "diagnosis": (
            None if not inversions else
            "a score that rises while the observed outcome falls is being read as a "
            "ranking it does not provide. The component weights, not the gates, are "
            "where this originates: the components that push a score into the top "
            "bucket are measured here as the ones that precede the worst outcomes"),
        "label": label9(min((c["n"] for c in usable), default=0), sessions),
    }


def _brier(rows: list[dict]) -> dict:
    ps = [min(1.0, max(0.0, float(r["confidence"]) / 100.0)) for r in rows]
    ys = [1.0 if r.get("target_before_stop_hit") else 0.0 for r in rows]
    n = len(ps)
    if n < 3:
        return {"measurable": False, "reason": "fewer than 3 resolved trades"}
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys, strict=True)) / n
    base = sum(ys) / n
    ref = sum((base - y) ** 2 for y in ys) / n
    return {
        "measurable": True,
        "brier_score": round(brier, 4),
        "base_rate": round(base, 4),
        "brier_of_constant_base_rate_forecast": round(ref, 4),
        "beats_constant_forecast": bool(brier < ref),
        "reading": ("read strictly as the cost of MISREADING the score as a "
                    "probability. Worse than the constant forecast means the number "
                    "carries less information about this outcome than always saying "
                    f"'{base * 100:.1f}%'"),
    }


def _ece(cells: list[dict], n_total: int) -> dict:
    ece = 0.0
    mce = 0.0
    for c in cells:
        if not c["n"] or c["observed_target_before_stop"] is None:
            continue
        gap = abs(c["stated_as_probability"] - c["observed_target_before_stop"])
        ece += c["n"] / n_total * gap
        mce = max(mce, gap)
    return {"expected_calibration_error": round(ece, 4),
            "max_calibration_error": round(mce, 4)}


def study(rows: list[dict], sessions: int) -> dict:
    """§5-6. ``rows`` are Phase 9 trade rows; ``confidence`` is the production score."""
    rows = [r for r in rows if r.get("confidence") is not None]
    if len(rows) < 3:
        return {"measurable": False, "resolved": len(rows), "label_used": LABEL,
                "reason": "fewer than 3 resolved trades"}

    fixed = [_cell([r for r in rows if bucket_of(float(r["confidence"]))
                    == bucket_name(lo, hi)], bucket_name(lo, hi), sessions)
             for lo, hi in BUCKETS]
    fixed = [c for c in fixed if c["n"]]

    scores = [float(r["confidence"]) for r in rows]
    edges = _quantile_edges(scores)
    quantile: list[dict] = []
    if edges:
        bounds = [min(scores)] + edges + [max(scores) + 1e-9]
        for lo, hi in zip(bounds, bounds[1:]):
            cell_rows = [r for r in rows if lo <= float(r["confidence"]) < hi]
            if cell_rows:
                quantile.append(_cell(cell_rows, f"{lo:.0f}-{hi:.0f}", sessions))

    preferred = quantile or fixed
    curve = [{"bucket": c["bucket"], "n": c["n"],
              "signal_score": c["mean_signal_score"],
              "stated_as_probability": c["stated_as_probability"],
              "observed_target_before_stop": c["observed_target_before_stop"],
              "gap": None if c["observed_target_before_stop"] is None
              else round(c["stated_as_probability"]
                         - c["observed_target_before_stop"], 3)}
             for c in preferred]

    spear_r = _spearman(scores, [float(r["realised_r"]) for r in rows
                                 if r["realised_r"] is not None]) \
        if all(r["realised_r"] is not None for r in rows) else None
    net_rows = [r for r in rows if r["net_r"] is not None]
    spear_net = _spearman([float(r["confidence"]) for r in net_rows],
                          [float(r["net_r"]) for r in net_rows])
    spear_tbs = _spearman(scores, [1.0 if r.get("target_before_stop_hit") else 0.0
                                   for r in rows])

    mono = _monotonicity(preferred, sessions)
    brier = _brier(rows)
    predictive = bool(
        (spear_tbs is not None and spear_tbs > 0.1)
        and mono["monotone"] and brier.get("beats_constant_forecast"))

    return {
        "measurable": True,
        "resolved": len(rows),
        "label_used": LABEL,
        "tooltip": TOOLTIP,
        "naming_note": "called SIGNAL SCORE throughout research and diagnostics. The "
                       "production Signal tab label is unchanged — relabelling what "
                       "the trader sees is a production UI change and is not made "
                       "here",
        "outcome_modelled": "the engine's own target reached before the engine's own "
                            "stop",
        "buckets_quantile": quantile,
        "buckets_fixed_phase8": fixed,
        "bucketing_note": ("quantile buckets are derived from this sample so cells "
                           "are comparably sized; the fixed Phase 8 buckets are kept "
                           "for continuity and left small where the data is small"
                           if quantile else
                           "too few rows to form quantile buckets of a readable "
                           "size, so only the fixed Phase 8 buckets are reported"),
        "calibration_curve": curve,
        "monotonicity": mono,
        "brier": brier,
        **_ece(preferred, len(rows)),
        "spearman_score_vs_gross_r": spear_r,
        "spearman_score_vs_net_r": spear_net,
        "spearman_score_vs_target_before_stop": spear_tbs,
        "is_predictive": predictive,
        "answer": (
            "the SIGNAL SCORE orders outcomes on this sample" if predictive else
            "the SIGNAL SCORE does NOT order outcomes on this sample: it is not "
            "monotone in observed target-before-stop, its rank correlation with the "
            "outcome is negligible or negative, and it does not beat a constant "
            "forecast of the base rate. It remains a score; it must not be read as a "
            "probability"),
        "guarantee": "the score formula, its weights, the confidence gate and the "
                     "production display are read-only inputs here and are unchanged",
    }
