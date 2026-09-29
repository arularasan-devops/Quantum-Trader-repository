"""Phase 11 §5 — the A+ shadow qualifier, per family. RESEARCH / SHADOW ONLY.

Phase 10 ran one qualifier over a pooled population and derived its spread
thresholds from that pool. On an MCX-weighted pool those thresholds describe MCX
books, so applying them to index options meant judging a 2.3%-of-risk book by a
distribution whose terciles were set by a 130% one. §5 forbids exactly that:
identical thresholds must not be assumed to fit both families.

So the qualifier is derived and scored **inside each family**: the tercile
proposal, the instrument tradability classes and the comparison all come from the
family's own rows. The component set is Phase 10's, unchanged, because changing
the qualifier and splitting the population in the same phase would leave no way
to tell which change moved the numbers.

Nothing is blocked, nothing is filtered, no threshold reaches production, and the
Signal tab never sees these labels. A+ output is a label attached to a decision
that already happened.
"""
from __future__ import annotations

from collections.abc import Callable

from app.research.phase9 import tradability
from app.research.phase10 import aplus

from .families import FAMILIES, INDEX_OPTIONS, MCX_OPTIONS, split

# §5's required output vocabulary. QUALIFIED plus Phase 10's rejection reasons,
# which already cover DATA / SPREAD / LIQUIDITY / CHASE / ROOM and UNKNOWN.
LABELS = (aplus.QUALIFIED, *aplus.REASONS)


def _family_block(rows: list[dict], sessions_ordered: list[str],
                  entry_of: Callable[[dict], dict | None],
                  *, score_is_predictive: bool, book_taint: dict) -> dict:
    """Derive and score the qualifier inside one family."""
    n_sessions = len(sessions_ordered)
    if not rows:
        return {"resolved": 0, "measurable": False,
                "reason": "no resolved trade in this family in this window"}

    trad = tradability.study(rows, n_sessions, book_taint)
    classes = aplus.instrument_tradability(rows)
    for r in rows:
        label, trigger = aplus.qualify_detail(
            r, entry_of(r), trad["proposal"], instrument_classes=classes,
            score_component_enabled=score_is_predictive, min_signal_score=None)
        # Phase 10's comparison reads this key; the Phase 11 copy is what the
        # ledger and the shadow preview read, so a later phase re-labelling rows
        # cannot silently change what this report said.
        r["phase10_qualification"] = label
        r["phase10_qualification_trigger"] = trigger
        r["phase11_qualification"] = label
        r["phase11_qualification_trigger"] = trigger

    block = aplus.compare(rows, sessions_ordered,
                          score_is_predictive=score_is_predictive)
    return {
        "resolved": len(rows),
        "measurable": True,
        "instruments": sorted({r["instrument"] for r in rows}),
        "thresholds_derived_from": "this family's own rows only",
        "proposed_thresholds": trad["proposal"],
        "instrument_classes": classes,
        "labels": list(LABELS),
        "comparison": block,
    }


def study_by_family(rows: list[dict], sessions_ordered: list[str],
                    entry_of: Callable[[dict], dict | None],
                    *, score_predictive: dict[str, bool],
                    book_taint: dict) -> dict:
    """§5 — one derived-and-scored qualifier per family, plus the verdicts."""
    by_family = split(rows)
    out: dict[str, dict] = {}
    for fam in FAMILIES:
        out[fam] = _family_block(
            by_family.get(fam) or [], sessions_ordered, entry_of,
            score_is_predictive=bool(score_predictive.get(fam)),
            book_taint=book_taint)

    def verdict(fam: str) -> str:
        block = out[fam]
        if not block.get("measurable"):
            return f"UNMEASURED — {block.get('reason')}"
        cmp_ = block["comparison"]
        return cmp_["answer_to_does_a_plus_beat_baseline"]

    thresholds = {
        fam: (out[fam].get("proposed_thresholds") or {}).get(
            "yellow_max_spread_share_pct")
        for fam in (INDEX_OPTIONS, MCX_OPTIONS)}
    return {
        "by_family": out,
        "answer_index": verdict(INDEX_OPTIONS),
        "answer_mcx": verdict(MCX_OPTIONS),
        "proposed_spread_cut_by_family_pct": thresholds,
        "thresholds_differ_between_families": bool(
            thresholds.get(INDEX_OPTIONS) is not None
            and thresholds.get(MCX_OPTIONS) is not None
            and thresholds[INDEX_OPTIONS] != thresholds[MCX_OPTIONS]),
        "why_split": "one tercile proposal over both families sets the index cut "
                     "from MCX books. Derived per family, the two cuts are "
                     "different numbers describing different products",
        "status": "SHADOW_ONLY",
        "guarantee": "no BUY is emitted or suppressed, no production gate reads a "
                     "label from this module, no threshold here is applied, and no "
                     "instrument is excluded from any production universe",
        "proposed_thresholds_status": "PROPOSED_ONLY",
    }
