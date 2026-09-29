"""Phase 20 §2 — per-instrument ranking of the research universe. RESEARCH ONLY.

Kept apart from :mod:`app.research.phase20.universe` so the universe stays a
description of the market (importable from the capture path) and this module owns
the part that reads outcomes.

The rule this module exists to enforce: an instrument is ranked on its OWN costed
paper, and "profitable" is a label no instrument may wear before it has enough
resolved outcomes AND a positive that survived a chronological holdout. A
flattering mean over a dozen legs ranks as INSUFFICIENT_DATA, not as a win.
"""
from __future__ import annotations

from app.analysis import instrument_family as fam
from app.research.phase19 import grading_ab as ab
from app.research.phase20.universe import (
    OPTION_EVIDENCE_SOURCE,
    plan,
)

# Below this many resolved costed-paper outcomes an instrument has no economics
# verdict at all — not a flat one. Kept equal to the validation floor the prior
# A/B uses so one instrument cannot be judged on a looser rule than another.
MIN_COSTED_PAPER_ROWS = ab.MIN_VALIDATION_ROWS

# What the ranking may say about one instrument, reusing the prior A/B's six
# labels rather than inventing a second vocabulary for the same question.
INSUFFICIENT_DATA = ab.INSUFFICIENT_DATA
VALIDATED_POSITIVE = ab.VALIDATED_POSITIVE


def rank(rows: list[dict], *, folds: int = ab.FOLDS) -> list[dict]:
    """Rank instruments individually on their own costed paper outcomes.

    ``rows`` are resolved costed-paper legs carrying ``instrument``, ``session``,
    net ``r`` and ``exit_reason`` — the same shape the prior A/B validates, reused
    so chronological development/validation/holdout, walk-forward folds, PF and
    drawdown are computed by one implementation for every family.

    Returns one record per instrument. There is deliberately no total row: see
    :func:`pooled_statistic_refused`.
    """
    by_inst: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row.get("instrument") or "").upper()
        if key:
            by_inst.setdefault(key, []).append(row)
    out: list[dict] = []
    for name, own in sorted(by_inst.items()):
        verdict = ab.validate_instrument(name, own, folds=folds)
        enough = len(own) >= MIN_COSTED_PAPER_ROWS
        info = plan(name)
        out.append({
            "instrument": name,
            "family": info["family"],
            "research_admitted": info["research_admitted"],
            "prior_label": info["prior_label"],
            "costed_rows": len(own),
            "enough_costed_paper": enough,
            "mean_net_r": verdict["pool_mean_r"],
            "t1_before_sl_pct": verdict["t1_before_sl_pct"],
            "profit_factor": verdict["profit_factor"],
            "holdout_keeps_the_sign": verdict.get("holdout_keeps_the_sign"),
            "walk_forward": verdict.get("walk_forward"),
            "verdict": verdict["verdict"] if enough else INSUFFICIENT_DATA,
            # The only combination that permits calling an instrument profitable:
            # enough costed outcomes AND a validated positive that held out of
            # sample. Everything else — including a healthy-looking mean on 12
            # legs — reads as INSUFFICIENT_DATA.
            "may_be_called_profitable": bool(
                enough and verdict["verdict"] == VALIDATED_POSITIVE),
            "basis": OPTION_EVIDENCE_SOURCE,
        })
    # Best first, but an instrument without enough evidence never outranks one
    # with a measured result, however flattering its handful of legs looks.
    out.sort(key=lambda r: (r["enough_costed_paper"],
                            r["mean_net_r"] if r["mean_net_r"] is not None
                            else -9e9),
             reverse=True)
    for i, rec in enumerate(out, start=1):
        rec["rank"] = i
    return out


def pooled_statistic_refused(rows: list[dict]) -> dict:
    """Why no single number is reported across instruments.

    Returned rather than raised so a report can print the refusal where a reader
    would otherwise look for a total.
    """
    instruments = sorted({str(r.get("instrument") or "").upper()
                          for r in rows if r.get("instrument")})
    families = sorted({fam.family(i) for i in instruments})
    return {
        "pooled_statistic": "REFUSED",
        "instruments": instruments,
        "families": families,
        "reason": ("one mean over several instruments hides the instrument that "
                   "produced the loss; rank() reports each separately"),
    }
