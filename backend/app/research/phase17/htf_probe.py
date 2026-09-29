"""Against-HTF candidates the generator never produced — §19, research only.

The 5-year study reported ``htf_alignment`` as a single bucket: 98,377 rows, all
``WITH_HTF``. That is not "HTF alignment has no effect" — it is "the question was
never asked", because the candidate generator only ever emits with-trend setups.
``RANGING`` was simultaneously the lowest-T1, highest-reward:risk regime (40.0%,
1.55), which is exactly where counter-trend candidates would live if they existed.
So this module manufactures the missing arm by mirroring each historical candidate
onto the opposite side of the same bar, at the same distances.

The honesty problem this design has to face head-on, because it is what makes or
breaks the output: the pool stores ``mfe_r`` and ``mae_r`` but NOT the order in
which they happened. Mirroring turns the original trade's adverse excursion into
the mirror's favourable one, so for any bar where the mirror touched both its
target and its stop, the pool cannot say which came first. Guessing would silently
choose the answer — and choosing "target first" is precisely how a counter-trend
book gets sold as an edge.

So every mirrored row carries a resolution:

``TARGET_ONLY`` / ``STOP_ONLY``  unambiguous; the pool contains the answer.
``AMBIGUOUS``                    both levels touched, order unknown.

and every statistic is reported three ways — optimistic (ambiguous rows count as
targets), pessimistic (as stops) and unambiguous-only. If the sign of the finding
changes between the bounds, there is no finding, and the report says so instead of
picking the flattering bound.

Nothing here touches production HTF logic, and every output is UNDERLYING_ONLY:
no option was priced in 2021, so no cost, spread or premium claim can come from
this module.
"""
from __future__ import annotations

WITH_HTF = "WITH_HTF"
AGAINST_HTF = "AGAINST_HTF"

TARGET_ONLY = "TARGET_ONLY"
STOP_ONLY = "STOP_ONLY"
AMBIGUOUS = "AMBIGUOUS"
NEITHER = "NEITHER"

OPTIMISTIC = "OPTIMISTIC"
PESSIMISTIC = "PESSIMISTIC"
UNAMBIGUOUS = "UNAMBIGUOUS_ONLY"

BASIS = "UNDERLYING_ONLY"
MIN_COHORT = 100


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and v == v else None


def mirror(row: dict) -> dict | None:
    """One counter-trend counterfactual for one historical candidate.

    Distances are preserved, not re-derived: the mirror risks and targets the
    same number of points as the original, so a difference in outcome cannot come
    from a difference in geometry — which is the error that made ``room=LOW`` look
    like a 51.7% edge at a reward:risk of 0.92.
    """
    risk = _num(row.get("risk"))
    mfe_r = _num(row.get("mfe_r"))
    mae_r = _num(row.get("mae_r"))
    if risk is None or risk <= 0 or mfe_r is None or mae_r is None:
        return None
    rr = _num(row.get("reward_risk"))
    if rr is None:
        entry, target = _num(row.get("entry")), _num(row.get("target"))
        if entry is None or target is None:
            return None
        rr = abs(target - entry) / risk
    if rr <= 0:
        return None

    # The original's adverse excursion is the mirror's favourable one, in R.
    mirror_mfe_r = abs(mae_r)
    mirror_mae_r = abs(mfe_r)
    hit_target = mirror_mfe_r >= rr
    hit_stop = mirror_mae_r >= 1.0
    if hit_target and hit_stop:
        resolution = AMBIGUOUS
    elif hit_target:
        resolution = TARGET_ONLY
    elif hit_stop:
        resolution = STOP_ONLY
    else:
        resolution = NEITHER

    side = str(row.get("side") or "").upper()
    mirrored_side = "SHORT" if side in ("LONG", "BUY", "CE") else "LONG"
    return {
        "session": row.get("session"),
        "instrument": row.get("instrument"),
        "entry_ts": row.get("entry_ts"),
        "minute": row.get("minute"),
        "regime": row.get("regime"),
        "htf_trend": row.get("htf_trend"),
        "htf_alignment": AGAINST_HTF,
        "side": mirrored_side,
        "risk": risk,
        "reward_risk": round(rr, 4),
        "mfe_r": round(mirror_mfe_r, 4),
        "mae_r": round(-mirror_mae_r, 4),
        "resolution": resolution,
        "source_r": _num(row.get("r")),
        "basis": BASIS,
        "synthetic": True,
    }


def _r_under(row: dict, bound: str) -> float | None:
    """Realised R for a mirrored row under one ordering assumption."""
    rr = _num(row.get("reward_risk")) or 0.0
    res = row.get("resolution")
    if res == TARGET_ONLY:
        return rr
    if res == STOP_ONLY:
        return -1.0
    if res == AMBIGUOUS:
        if bound == OPTIMISTIC:
            return rr
        if bound == PESSIMISTIC:
            return -1.0
        return None
    # Touched neither level: the trade would have been timed out around its
    # last mark, and the pool's own MFE is the closest available proxy.
    mfe = _num(row.get("mfe_r"))
    return round(mfe if mfe is not None else 0.0, 4)


def _stats(rows: list[dict], bound: str) -> dict:
    rs = [
        r for r in (_r_under(row, bound) for row in rows) if r is not None
    ]
    n = len(rs)
    if n == 0:
        return {"n": 0, "status": "REQUIRES_MORE_DATA"}
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    targets = sum(1 for row in rows if _r_under(row, bound) and
                  _r_under(row, bound) > 0)
    return {
        "n": n,
        "status": "OK" if n >= MIN_COHORT else "REQUIRES_MORE_DATA",
        "t1_pct": round(100.0 * targets / n, 2),
        "expectancy_r": round(sum(rs) / n, 4),
        "profit_factor": round(gain / loss, 3) if loss > 0 else None,
        "bound": bound,
    }


def generate(pool: list[dict]) -> dict:
    """Build the against-HTF arm and grade it under all three bounds."""
    mirrored = [m for m in (mirror(r) for r in pool) if m is not None]
    resolutions: dict[str, int] = {
        TARGET_ONLY: 0, STOP_ONLY: 0, AMBIGUOUS: 0, NEITHER: 0,
    }
    for m in mirrored:
        resolutions[str(m["resolution"])] += 1

    with_rows = [
        {
            "reward_risk": _num(r.get("reward_risk")) or 0.0,
            "resolution": (
                TARGET_ONLY if r.get("exit_reason") == "TARGET"
                else STOP_ONLY if r.get("exit_reason") == "STOP" else NEITHER
            ),
            "mfe_r": _num(r.get("mfe_r")),
        }
        for r in pool
        if _num(r.get("risk"))
    ]

    bounds = {b: _stats(mirrored, b) for b in (OPTIMISTIC, PESSIMISTIC, UNAMBIGUOUS)}
    signs = {
        b: (None if s.get("expectancy_r") is None
            else (1 if float(s["expectancy_r"]) > 0 else -1))
        for b, s in bounds.items()
    }
    measured = [v for v in signs.values() if v is not None]
    consistent = bool(measured) and len(set(measured)) == 1
    by_regime = {}
    for name in sorted({str(m.get("regime")) for m in mirrored if m.get("regime")}):
        cohort = [m for m in mirrored if str(m.get("regime")) == name]
        by_regime[name] = {
            b: _stats(cohort, b) for b in (OPTIMISTIC, PESSIMISTIC, UNAMBIGUOUS)
        }
    return {
        "pool_rows": len(pool),
        "mirrored_rows": len(mirrored),
        "unmirrorable_rows": len(pool) - len(mirrored),
        "resolutions": resolutions,
        "ambiguous_pct": (
            round(100.0 * resolutions[AMBIGUOUS] / len(mirrored), 2)
            if mirrored else None
        ),
        "against_htf": bounds,
        "with_htf_reference": _stats(with_rows, UNAMBIGUOUS),
        "by_regime": by_regime,
        "sign_consistent_across_bounds": consistent,
        "verdict": (
            "NO_FINDING_BOUNDS_DISAGREE" if not consistent else
            "AGAINST_HTF_POSITIVE_UNDER_ALL_BOUNDS" if measured and measured[0] > 0
            else "AGAINST_HTF_NEGATIVE_UNDER_ALL_BOUNDS"
        ),
        "basis": BASIS,
        "production_htf_unchanged": True,
        "note": (
            "Synthetic counter-trend rows mirrored from real bars. The pool does "
            "not store whether the target or the stop was touched first, so "
            "ambiguous rows are bounded rather than assumed. A result that "
            "changes sign between the optimistic and pessimistic bounds is not a "
            "result. No option was priced here — UNDERLYING_ONLY."
        ),
    }
