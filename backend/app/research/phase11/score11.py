"""Phase 11 §4 — is the SIGNAL SCORE useful, per family? RESEARCH ONLY.

The production score formula, its weights and its display are read-only inputs
here. Nothing in this module fits, calibrates or deploys a probability: §4 asks
whether the number *ranks* opportunities, and it asks it separately per family
because the pooled answer was an artefact.

The measurement code is Phase 10's, unchanged, applied to family subsets. Using a
second implementation would make the family comparison a comparison of two pieces
of arithmetic instead of two populations.

What "useful" means here is stated so it cannot drift: the top bucket large enough
to read must be the best-performing one, the rank correlation with the outcome
must be positive, and net expectancy must improve across buckets. A score that
only orders GROSS outcomes is not useful to a trader who pays the book.
"""
from __future__ import annotations

from app.research.phase7.gates import MIN_BUYS_PER_CELL
from app.research.phase10.score import LABEL, TOOLTIP, study

from .families import FAMILIES, INDEX_OPTIONS, MCX_OPTIONS, split

# An instrument-level score table needs enough rows to have buckets at all.
MIN_ROWS_FOR_INSTRUMENT_TABLE = 2 * MIN_BUYS_PER_CELL


def _top_bucket(block: dict) -> dict | None:
    cells = [c for c in (block.get("buckets_quantile")
                         or block.get("buckets_fixed_phase8") or [])
             if c.get("n", 0) >= MIN_BUYS_PER_CELL
             and c.get("observed_target_before_stop") is not None]
    return cells[-1] if cells else None


def _usefulness(block: dict) -> dict:
    """Whether the score is usable as a ranking in this population."""
    if not block.get("measurable"):
        reason = block.get("reason", "not measurable on this sample")
        return {"usable_as_ranking": False,
                "reason": reason,
                "label": "REQUIRES_MORE_DATA",
                "objections": [reason],
                "reading": f"the score cannot be read in this family: {reason}"}
    mono = block.get("monotonicity") or {}
    top = _top_bucket(block)
    spear = block.get("spearman_score_vs_target_before_stop")
    net_spear = block.get("spearman_score_vs_net_r")
    top_positive_net = bool(top and top.get("net_expectancy_r") is not None
                            and top["net_expectancy_r"] > 0)
    reasons: list[str] = []
    if not mono.get("highest_bucket_is_best"):
        reasons.append("the highest readable bucket is not the best one")
    if mono.get("inversions"):
        reasons.append(f"{len(mono['inversions'])} bucket inversion(s)")
    if spear is None or spear <= 0.1:
        reasons.append("rank correlation with target-before-stop is negligible "
                       "or negative")
    if not top_positive_net:
        reasons.append("the top bucket's NET expectancy is not positive")
    return {
        "usable_as_ranking": not reasons,
        "top_readable_bucket": None if top is None else top["bucket"],
        "top_bucket_n": None if top is None else top["n"],
        "top_bucket_target_before_stop": None if top is None
        else top["observed_target_before_stop"],
        "top_bucket_net_expectancy_r": None if top is None
        else top.get("net_expectancy_r"),
        "top_bucket_net_profit_factor": None if top is None
        else top.get("net_profit_factor"),
        "spearman_vs_target_before_stop": spear,
        "spearman_vs_net_r": net_spear,
        "monotone": mono.get("monotone"),
        "objections": reasons,
        "label": "IN_SAMPLE_ONLY" if not reasons else "REQUIRES_MORE_DATA",
        "reading": ("the score ordered outcomes in this family on this sample. "
                    "In-sample ordering is not a validated edge: it becomes one "
                    "only on sessions it has never seen"
                    if not reasons else
                    "the score did NOT order outcomes in this family: "
                    + "; ".join(reasons)),
    }


def study_by_family(rows: list[dict], sessions: int) -> dict:
    """§4 — the score table per family, per instrument, and the comparison."""
    by_family = split(rows)
    families: dict[str, dict] = {}
    for fam in FAMILIES:
        sub = by_family.get(fam) or []
        block = study(sub, sessions) if sub else {
            "measurable": False, "resolved": 0, "label_used": LABEL,
            "reason": "no resolved trade in this family in this window"}
        families[fam] = {
            "resolved": len(sub),
            "instruments": sorted({r["instrument"] for r in sub}),
            "score": block,
            "usefulness": _usefulness(block),
        }

    instruments: dict[str, dict] = {}
    by_inst: dict[str, list[dict]] = {}
    for r in rows:
        by_inst.setdefault(r["instrument"], []).append(r)
    for name, sub in sorted(by_inst.items()):
        if len(sub) < MIN_ROWS_FOR_INSTRUMENT_TABLE:
            instruments[name] = {
                "family": sub[0].get("family"),
                "resolved": len(sub),
                "measured": False,
                "reason": f"{len(sub)} trade(s), below the "
                          f"{MIN_ROWS_FOR_INSTRUMENT_TABLE} needed for two readable "
                          f"buckets",
            }
            continue
        block = study(sub, sessions)
        instruments[name] = {
            "family": sub[0].get("family"),
            "resolved": len(sub),
            "measured": True,
            "buckets": block.get("buckets_quantile")
            or block.get("buckets_fixed_phase8"),
            "usefulness": _usefulness(block),
        }

    index_use = families[INDEX_OPTIONS]["usefulness"]
    mcx_use = families[MCX_OPTIONS]["usefulness"]
    return {
        "label_used": LABEL,
        "tooltip": TOOLTIP,
        "outcome_modelled": "the engine's own target reached before the engine's own "
                            "stop",
        "by_family": families,
        "by_instrument": instruments,
        "answer_index": index_use["reading"],
        "answer_mcx": mcx_use["reading"],
        "families_disagree": bool(index_use.get("usable_as_ranking")
                                 != mcx_use.get("usable_as_ranking")),
        "comparison_note": (
            "the same score, the same formula, the same replay code: the only "
            "difference between these two tables is the product it was scored on. "
            "A single pooled table hides that difference, and the pooled table is "
            "dominated by whichever family recorded more rows"),
        "probability_status": "NOT_FITTED_IN_THIS_BLOCK — §4 forbids a mapping until "
                              "the calibration gates pass. The score remains a "
                              "score",
        "guarantee": "the score formula, its weights, the confidence gate and the "
                     "production display are unchanged",
    }
