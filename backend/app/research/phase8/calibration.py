"""Phase 8 Part 4-5 — does the displayed confidence predict anything? RESEARCH ONLY.

The board shows a confidence number on every signal. Nothing has ever checked
whether it orders outcomes, and a number that does not order outcomes is worse
than no number: it is read as information on every screen, all day.

This module answers exactly one question and refuses to do anything else with the
value: it buckets the *already reconstructed* production BUYs by the confidence
the engine displayed, and reports the outcome of each bucket using Phase 7's own
metric block. The displayed confidence is never modified, never recalibrated, and
no gate anywhere is changed.

Calibration failure here means one thing precisely: the buckets are not monotone
in outcome. That is measurable on a small sample when the inversion is large, and
it is the one result from a single day that is worth acting on as an *observation*
— stop reading the number as a ranking — without touching production logic.
"""
from __future__ import annotations

from app.research.phase7.policies import median, summarise

from .findings import label, note

# The spec's buckets. Everything below 70 is kept as one bucket rather than
# dropped: the production confidence gate sits in the 80s, so sub-70 rows are
# reconstruction artefacts and their presence is itself worth seeing.
BUCKETS = ((0.0, 70.0), (70.0, 75.0), (75.0, 80.0), (80.0, 85.0),
           (85.0, 90.0), (90.0, 95.0), (95.0, 1e9))


def bucket_name(lo: float, hi: float) -> str:
    if lo == 0.0:
        return "<70"
    if hi >= 1e9:
        return "95+"
    return f"{lo:.0f}-{hi:.0f}"


def bucket_of(confidence: float) -> str:
    for lo, hi in BUCKETS:
        if lo <= confidence < hi:
            return bucket_name(lo, hi)
    return bucket_name(*BUCKETS[-1])


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation between confidence and realised R, ties averaged.

    Reported alongside the buckets because a bucket table can hide a monotone
    relationship behind bucket boundaries, and a rank correlation can hide a
    non-monotone one — the two disagreeing is itself informative.
    """
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


def _inversions(values: list[tuple[str, float | None]]) -> list[dict]:
    """Adjacent bucket pairs where a higher bucket did worse than a lower one."""
    out: list[dict] = []
    seen = [(name, v) for name, v in values if v is not None]
    for (lo_name, lo_v), (hi_name, hi_v) in zip(seen, seen[1:], strict=False):
        if hi_v < lo_v:
            out.append({"from": lo_name, "to": hi_name,
                        "drop": round(lo_v - hi_v, 3)})
    return out


def study(pairs: list[tuple], sessions: int) -> dict:
    """``pairs`` is [(BuyEvent, Fill)] for the production baseline population.

    The Fill must be CONTROL|A_BASELINE — the engine's own entry and its own exit.
    Anything else would be measuring a research policy's calibration, not the
    board's.
    """
    resolved = [(ev, f) for ev, f in pairs if f.entered]
    order = [bucket_name(lo, hi) for lo, hi in BUCKETS]
    grouped: dict[str, list[tuple]] = {name: [] for name in order}
    for ev, f in resolved:
        grouped[bucket_of(float(ev.confidence))].append((ev, f))

    rows = []
    for name in order:
        cell = grouped[name]
        if not cell:
            rows.append({"bucket": name, "n": 0})
            continue
        block = summarise([f for _, f in cell], len(cell))
        confs = [float(ev.confidence) for ev, _ in cell]
        rows.append({
            "bucket": name,
            "n": len(cell),
            "predicted_confidence_mean": round(sum(confs) / len(confs), 1),
            "predicted_confidence_median": median(confs),
            # "actual" is target-before-stop on the recorded premium path: the
            # engine's own target reached before the engine's own stop.
            "actual_target_before_stop_pct": block["target_before_stop_pct"],
            "stopped_pct": block["stopped_pct"],
            "win_rate_pct": block["win_rate_pct"],
            "expectancy_r": block["expectancy_r"],
            "profit_factor": block["profit_factor"],
            "median_mfe_r": block["median_mfe_r"],
            "median_mae_r": block["median_mae_r"],
            "label": label(len(cell), sessions),
        })

    populated = [r for r in rows if r["n"] > 0]
    hit_seq = [(r["bucket"], r["actual_target_before_stop_pct"]) for r in populated]
    exp_seq = [(r["bucket"], r["expectancy_r"]) for r in populated]
    hit_inv = _inversions(hit_seq)
    exp_inv = _inversions(exp_seq)
    rho = _spearman([float(ev.confidence) for ev, _ in resolved],
                    [float(f.r) for _, f in resolved])

    # A single adjacent inversion on a 12-trade bucket is noise; the verdict below
    # is deliberately blunt about which of the two it is, and never upgrades to a
    # claim about the next trade.
    comparable = [r for r in populated if r["n"] >= 10]
    worst = max((i["drop"] for i in hit_inv), default=0.0)
    monotone = not hit_inv and not exp_inv
    if len(comparable) < 2:
        verdict = "UNTESTABLE"
        reading = ("fewer than two buckets carry 10+ trades, so monotonicity is "
                   "not testable on this sample")
    elif monotone:
        verdict = "MONOTONE_ON_THIS_SAMPLE"
        reading = ("higher confidence buckets did better on both target-before-stop "
                   "and expectancy in this sample — consistent with the number "
                   "carrying information, not proof that it does")
    else:
        verdict = "NON_MONOTONE"
        reading = ("CALIBRATION FAILURE on this sample: at least one higher "
                   "confidence bucket performed worse than a lower one, so the "
                   "displayed number did not rank outcomes here. The immediate "
                   "consequence is interpretive — do not read a higher number as "
                   "a better trade — not a change to how confidence is computed")

    return {
        "population": "CONTROL entry | A_BASELINE exit (the engine's own trade)",
        "resolved": len(resolved),
        "buckets": rows,
        "monotonicity": {
            "verdict": verdict,
            "calibration_failure": verdict == "NON_MONOTONE",
            "hit_rate_inversions": hit_inv,
            "expectancy_inversions": exp_inv,
            "largest_hit_rate_drop_pct": round(worst, 2),
            "buckets_with_10_plus": len(comparable),
            "spearman_confidence_vs_realised_r": rho,
            "reading": reading,
        },
        "label": label(min((r["n"] for r in populated), default=0), sessions),
        "label_note": note(min((r["n"] for r in populated), default=0), sessions),
        "guarantee": "the displayed confidence is read here and never written; no "
                     "gate, threshold or confidence formula is touched",
    }


def splits(pairs: list[tuple], sessions: int) -> dict:
    """The same table split by expiry class, instrument and direction (Part J).

    Split first, then bucket: the buckets in a split are the same buckets, so a
    split cell that is too small is visible as an ``n`` of 3 rather than hidden
    inside an aggregate. Every one of these cells is expected to fail the cell-size
    gate on a single session — that failure is the point of reporting them.
    """
    out: dict[str, dict] = {}
    for key, extract in (("by_expiry_class", lambda ev: str(
                              getattr(ev, "expiry_class", "EXPIRY_UNKNOWN"))),
                         ("by_instrument", lambda ev: ev.instrument),
                         ("by_direction", lambda ev: ev.side)):
        groups: dict[str, list[tuple]] = {}
        for ev, f in pairs:
            groups.setdefault(extract(ev), []).append((ev, f))
        out[key] = {}
        for name, sub in sorted(groups.items()):
            block = study(sub, sessions)
            out[key][name] = {
                "resolved": block["resolved"],
                "buckets": [r for r in block["buckets"] if r["n"]],
                "monotonicity": {
                    k: block["monotonicity"][k] for k in
                    ("verdict", "calibration_failure", "buckets_with_10_plus",
                     "spearman_confidence_vs_realised_r")},
                "label": block["label"],
            }
    return out
