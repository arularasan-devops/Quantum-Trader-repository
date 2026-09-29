"""Phase 20 §4 — per-instrument underlying direction study. RESEARCH ONLY.

Five-year history can answer one question — did this market's setups have
direction — and the answer is per instrument. Pooling is what produced the
comfortable reading that the tool was roughly flat; separated, the same pool says
eleven instruments are individually ~zero and one is a validated negative.

What this module adds over the prior A/B is coverage honesty. The universe is now
66 optionable names, the pool holds eleven, and MCX commodities have no cash
series at all — so a table of eleven rows must say that the other 55 were not
measured, and which of them CANNOT be measured on these terms. Silence there is
how "the study covers the market" gets said about 17% of it.

Underlying only. Nothing here says anything about option economics: see
:mod:`app.research.phase20.universe` for why that is unclaimable from history.
"""
from __future__ import annotations

from app.research.phase19 import grading_ab as ab
from app.research.phase20 import universe as p20universe

NOT_COLLECTED = "NOT_COLLECTED"
NO_CASH_SERIES = "NO_CASH_SERIES"

INSUFFICIENT_DATA = ab.INSUFFICIENT_DATA


def study(pool_rows: list[dict], *, folds: int = ab.FOLDS) -> list[dict]:
    """One direction verdict per instrument present in the pool.

    ``pool_rows`` are historical outcomes carrying ``instrument``, ``session``, R
    and exit reason — validated by the same implementation, chronological holdout
    and walk-forward folds the prior A/B uses, so no instrument is judged on a
    looser rule than another.
    """
    by_inst: dict[str, list[dict]] = {}
    for row in pool_rows:
        key = str(row.get("instrument") or "").upper()
        if key:
            by_inst.setdefault(key, []).append(row)
    out: list[dict] = []
    for name, own in sorted(by_inst.items()):
        verdict = ab.validate_instrument(name, own, folds=folds)
        info = p20universe.plan(name)
        out.append({
            "instrument": name,
            "family": info["family"],
            "history_basis": info["history_basis"],
            "rows": len(own),
            "mean_r": verdict["pool_mean_r"],
            "sigma": verdict["sigma"],
            "t1_before_sl_pct": verdict["t1_before_sl_pct"],
            "profit_factor": verdict["profit_factor"],
            "holdout": verdict.get("holdout"),
            "walk_forward": verdict.get("walk_forward"),
            "label": verdict["verdict"],
            # Direction evidence only. Stated on every row because the same table
            # was previously read as "this instrument is profitable to trade".
            "claims": "UNDERLYING_DIRECTION_ONLY",
            "option_economics": p20universe.may_claim(
                name, p20universe.CLAIM_OPTION_ECONOMICS)[1],
        })
    out.sort(key=lambda r: (r["mean_r"] if r["mean_r"] is not None else -9e9),
             reverse=True)
    return out


def coverage(pool_rows: list[dict]) -> dict:
    """Which universe names the study covers, and why the rest are missing."""
    measured = {str(r.get("instrument") or "").upper()
                for r in pool_rows if r.get("instrument")}
    unmeasurable: dict[str, str] = {}
    uncollected: list[str] = []
    for info in p20universe.universe():
        name = info["instrument"]
        if name in measured:
            continue
        if info["five_year_direction_study"]:
            uncollected.append(name)
        else:
            unmeasurable[name] = NO_CASH_SERIES
    universe_names = [p["instrument"] for p in p20universe.universe()]
    return {
        "universe": len(universe_names),
        "measured": sorted(measured & set(universe_names)),
        "measured_count": len(measured & set(universe_names)),
        "not_collected_yet": sorted(uncollected),
        "cannot_be_measured_on_these_terms": dict(sorted(unmeasurable.items())),
        "off_universe_in_pool": sorted(measured - set(universe_names)),
        "reading": ("a verdict exists only for the measured names; the rest are "
                    "unmeasured, which is not a pass and not a fail"),
    }


def report(pool_rows: list[dict], *, folds: int = ab.FOLDS) -> dict:
    """The study plus its own coverage, grouped by family and never pooled."""
    rows = study(pool_rows, folds=folds)
    families: dict[str, list[dict]] = {}
    for row in rows:
        families.setdefault(row["family"], []).append(row)
    return {
        "per_instrument": rows,
        "by_family": families,
        "coverage": coverage(pool_rows),
        "pooled_statistic": "REFUSED",
        "validated_positive": [r["instrument"] for r in rows
                               if r["label"] == ab.VALIDATED_POSITIVE],
        "validated_negative": [r["instrument"] for r in rows
                               if r["label"] == ab.VALIDATED_NEGATIVE],
    }
