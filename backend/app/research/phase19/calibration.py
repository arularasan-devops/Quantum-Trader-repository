"""T1 calibration harness — Phase 19 §10.

The A+ layer publishes a T1 **score** and a rank band, deliberately not a
percentage. This module is the only route by which that score could ever earn the
right to be printed as a probability, and it is built to refuse.

How it works, and why in this order:

1. rows are ordered chronologically by session and split dev / holdout on a
   session boundary. No shuffling — a shuffled split leaks tomorrow into the
   estimate of today, and a leaked calibration is worse than none because it
   looks trustworthy;
2. the score→frequency map is fitted **on development only**: within each score
   bin, the observed T1-before-stop frequency;
3. that frozen map is applied to holdout rows to produce predictions, and the
   metrics — Brier, ECE, reliability curve, monotonicity, and the Brier skill
   score against the base rate — are computed there;
4. the gate. A percentage is published only when every condition holds:
   enough holdout rows, enough populated bins, ECE within tolerance, monotone
   bins, and a Brier that actually beats predicting the base rate for everyone.
   Otherwise the verdict is ``NOT_CALIBRATED`` and the caller must keep showing
   the score and the rank.

The base-rate comparison is the part that matters most: a model that ignores its
inputs and predicts the overall T1 rate for every row is already well calibrated.
A score earns a percentage only by beating that, not by being close to it.
"""
from __future__ import annotations

# Score bins. Fixed and coarse on purpose: with a few hundred outcomes, ten bins
# would each hold a handful of rows and the reliability curve would be noise.
BINS: tuple[tuple[float, float], ...] = (
    (0.0, 35.0), (35.0, 50.0), (50.0, 65.0), (65.0, 80.0), (80.0, 100.01)
)

CALIBRATED = "CALIBRATED"
NOT_CALIBRATED = "NOT_CALIBRATED"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"

# Gate thresholds.
MIN_HOLDOUT = 100        # resolved chronological holdout outcomes
MIN_BIN_ROWS = 15        # per populated bin, in both dev and holdout
MIN_POPULATED_BINS = 3   # a two-bin curve is a threshold, not a calibration
MAX_ECE = 0.10           # 10 percentage points of average miscalibration
MIN_BRIER_SKILL = 0.02   # must beat the base rate by this much

DEV_SHARE = 0.6


def bin_of(score: float) -> int | None:
    for idx, (lo, hi) in enumerate(BINS):
        if lo <= score < hi:
            return idx
    return None


def bin_label(idx: int) -> str:
    lo, hi = BINS[idx]
    return f"{lo:.0f}-{min(hi, 100.0):.0f}"


def to_rows(rows: list[dict], *, score_key: str = "t1_score") -> list[dict]:
    """Keep the rows that can be calibrated at all.

    A row needs a score, a resolved outcome and a session. Rows without a score
    are dropped rather than scored zero: an unscored row is not a low-scoring
    row, and pooling the two would flatten the very curve being measured.
    """
    out: list[dict] = []
    for row in rows:
        score = row.get(score_key)
        outcome = row.get("outcome") or row.get("exit_reason")
        session = row.get("session")
        if not isinstance(score, (int, float)) or not isinstance(outcome, str):
            continue
        if not isinstance(session, str) or not session:
            continue
        idx = bin_of(float(score))
        if idx is None:
            continue
        hit = row.get("t1_before_stop")
        if not isinstance(hit, bool):
            hit = outcome in ("T1", "T2", "T3")
        out.append({
            "session": session,
            "score": float(score),
            "bin": idx,
            "rank": row.get("t1_rank"),
            "hit": bool(hit),
            "instrument": row.get("instrument"),
            "vehicle": row.get("vehicle"),
        })
    out.sort(key=lambda r: r["session"])
    return out


def split(rows: list[dict], *, dev_share: float = DEV_SHARE) -> dict:
    """Chronological dev/holdout split, cut on a session boundary."""
    sessions = sorted({r["session"] for r in rows})
    if len(sessions) < 2:
        return {"dev": rows, "holdout": [], "dev_sessions": sessions,
                "holdout_sessions": [], "note": "one session — no holdout exists"}
    cut = max(1, int(round(len(sessions) * dev_share)))
    cut = min(cut, len(sessions) - 1)
    dev_sessions = set(sessions[:cut])
    return {
        "dev": [r for r in rows if r["session"] in dev_sessions],
        "holdout": [r for r in rows if r["session"] not in dev_sessions],
        "dev_sessions": sessions[:cut],
        "holdout_sessions": sessions[cut:],
        "note": "chronological, cut on a session boundary, never shuffled",
    }


def fit(dev: list[dict]) -> dict:
    """Score bin → observed T1 frequency, from development rows only."""
    table: dict[int, dict] = {}
    for idx in range(len(BINS)):
        rows = [r for r in dev if r["bin"] == idx]
        n = len(rows)
        hits = sum(1 for r in rows if r["hit"])
        table[idx] = {
            "bin": bin_label(idx),
            "n": n,
            "hits": hits,
            "frequency": round(hits / n, 4) if n else None,
            "usable": n >= MIN_BIN_ROWS,
        }
    base = (
        round(sum(1 for r in dev if r["hit"]) / len(dev), 4) if dev else None
    )
    return {
        "table": {bin_label(i): table[i] for i in table},
        "by_index": table,
        "base_rate": base,
        "n": len(dev),
        "note": (
            "fitted on development sessions only; the holdout has not been read "
            "by this step"
        ),
    }


def _brier(pairs: list[tuple[float, bool]]) -> float | None:
    if not pairs:
        return None
    total = sum((p - (1.0 if hit else 0.0)) ** 2 for p, hit in pairs)
    return round(total / len(pairs), 5)


def evaluate(rows: list[dict], *, dev_share: float = DEV_SHARE) -> dict:
    """Fit on development, score the holdout, and decide whether to publish.

    With no data this returns ``REQUIRES_MORE_DATA`` and a reason per unmet gate,
    which is the correct first output of this module rather than a failure of it.
    """
    parts = split(rows, dev_share=dev_share)
    dev, hold = parts["dev"], parts["holdout"]
    model = fit(dev)
    by_index = model["by_index"]
    base = model["base_rate"]

    pairs: list[tuple[float, bool]] = []
    base_pairs: list[tuple[float, bool]] = []
    curve: list[dict] = []
    for idx in range(len(BINS)):
        h_rows = [r for r in hold if r["bin"] == idx]
        predicted = by_index[idx]["frequency"]
        observed = (
            round(sum(1 for r in h_rows if r["hit"]) / len(h_rows), 4)
            if h_rows else None
        )
        curve.append({
            "bin": bin_label(idx),
            "predicted": predicted,
            "observed": observed,
            "n": len(h_rows),
            "gap": (
                None if predicted is None or observed is None
                else round(observed - predicted, 4)
            ),
        })
        if predicted is None:
            continue
        for r in h_rows:
            pairs.append((float(predicted), bool(r["hit"])))
            if base is not None:
                base_pairs.append((float(base), bool(r["hit"])))

    brier = _brier(pairs)
    brier_base = _brier(base_pairs)
    skill = (
        None if brier is None or not brier_base
        else round((brier_base - brier) / brier_base, 4)
    )
    scored = len(pairs)
    ece = None
    if scored:
        ece = round(sum(
            (row["n"] / scored) * abs(row["gap"])
            for row in curve
            if row["gap"] is not None and row["n"]
        ), 4)

    populated = [
        row for row in curve
        if row["n"] >= MIN_BIN_ROWS and row["predicted"] is not None
    ]
    observed_seq = [row["observed"] for row in populated if row["observed"] is not None]
    monotone = all(
        observed_seq[i] <= observed_seq[i + 1] + 1e-9
        for i in range(len(observed_seq) - 1)
    ) if len(observed_seq) >= 2 else None

    blockers: list[str] = []
    if len(hold) < MIN_HOLDOUT:
        blockers.append(
            f"holdout outcomes {len(hold)} < {MIN_HOLDOUT}"
        )
    if len(populated) < MIN_POPULATED_BINS:
        blockers.append(
            f"populated bins {len(populated)} < {MIN_POPULATED_BINS} "
            f"(>= {MIN_BIN_ROWS} holdout rows each)"
        )
    if ece is None or ece > MAX_ECE:
        blockers.append(f"ECE {ece} > {MAX_ECE}" if ece is not None else "ECE unmeasured")
    if monotone is not True:
        blockers.append(
            "observed T1 rate is not monotone in the score — a higher score did "
            "not mean a higher hit rate"
        )
    if skill is None or skill < MIN_BRIER_SKILL:
        blockers.append(
            f"Brier skill {skill} < {MIN_BRIER_SKILL} against the base rate"
            if skill is not None else "Brier skill unmeasured"
        )

    if not rows:
        verdict = REQUIRES_MORE_DATA
    elif blockers:
        verdict = REQUIRES_MORE_DATA if len(hold) < MIN_HOLDOUT else NOT_CALIBRATED
    else:
        verdict = CALIBRATED

    return {
        "verdict": verdict,
        "may_publish_probability": verdict == CALIBRATED,
        "display": "T1_PROBABILITY" if verdict == CALIBRATED else "T1_SCORE_AND_RANK",
        "blockers": blockers,
        "rows": len(rows),
        "dev_rows": len(dev),
        "holdout_rows": len(hold),
        "dev_sessions": parts["dev_sessions"],
        "holdout_sessions": parts["holdout_sessions"],
        "scored_holdout_rows": scored,
        "base_rate": base,
        "brier": brier,
        "brier_base_rate": brier_base,
        "brier_skill": skill,
        "ece": ece,
        "monotone": monotone,
        "reliability_curve": curve,
        "model": model["table"],
        "gates": {
            "min_holdout": MIN_HOLDOUT,
            "min_bin_rows": MIN_BIN_ROWS,
            "min_populated_bins": MIN_POPULATED_BINS,
            "max_ece": MAX_ECE,
            "min_brier_skill": MIN_BRIER_SKILL,
        },
        "note": (
            "Until the verdict is CALIBRATED the T1 percentage is not published: "
            "the score and the rank band are shown instead. A probability that "
            "has not been measured against chronological out-of-sample outcomes "
            "is a number, not a probability."
        ),
    }
