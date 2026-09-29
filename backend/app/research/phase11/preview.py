"""Phase 11 §14-15 — research payloads for a preview panel and a dashboard.

RESEARCH ONLY, and the isolation here is the whole point: these are *payload
builders*, pure functions over rows this phase already computed. They are not
routes, the frontend does not read them, and the production Signal tab is
untouched. §14 exists so a future preview can be built from measured fields
instead of from a model, and so the fields it would show are chosen while their
provenance is still visible.

Every field carries what it is: a SIGNAL SCORE stays a score, a spread class stays
a research monitoring label, and a missing probability stays missing.
"""
from __future__ import annotations

from .families import FAMILIES, split
from .tradability11 import UNMEASURED

# What a preview panel may show, and what it must never imply. Held as data so a
# later change to the panel is a change to this list, reviewable on its own.
PREVIEW_FIELDS = (
    "instrument", "family", "signal_score", "signal_score_is_not_a_probability",
    "spread_share_of_risk_pct", "spread_class_research_only",
    "a_plus_shadow_label", "data_flag", "chain_age_sec",
)
FORBIDDEN_IN_PREVIEW = (
    "probability_of_profit", "expected_return", "recommended_size",
    "a_plus_gate_decision",
)


def signal_preview(rows: list[dict], tradability_by_instrument: dict) -> dict:
    """§14 — one research row per resolved signal, for a preview panel only."""
    out = []
    for r in rows[-200:]:
        inst = r.get("instrument")
        cls = (tradability_by_instrument.get(inst) or {}).get("class", UNMEASURED)
        out.append({
            "instrument": inst,
            "family": r.get("family"),
            "session": r.get("session"),
            "at_ist": r.get("ts_ist"),
            "signal_score": r.get("confidence"),
            "signal_score_is_not_a_probability": True,
            "spread_share_of_risk_pct": r.get("spread_share_of_risk_pct"),
            "spread_class_research_only": cls,
            "a_plus_shadow_label": r.get("phase11_qualification"),
            "a_plus_shadow_trigger": r.get("phase11_qualification_trigger"),
            "data_flag": r.get("data_flag"),
            "chain_age_sec": r.get("chain_age_sec"),
            "outcome_net_r": r.get("net_r"),
            "loss_cause": r.get("loss_cause"),
        })
    return {
        "status": "RESEARCH_PREVIEW_ONLY",
        "fields": list(PREVIEW_FIELDS),
        "must_not_display": list(FORBIDDEN_IN_PREVIEW),
        "rows": out,
        "wiring": "no route serves this payload and no component renders it. The "
                  "production Signal tab, its labels and its tooltips are unchanged",
    }


def dashboard(*, families: dict, score: dict, aplus: dict, tradability: dict,
              attribution: dict, feed: dict, funnel: dict, flow: dict,
              gates: dict) -> dict:
    """§15 — the research dashboard payload: one section per question asked."""
    return {
        "status": "RESEARCH_DASHBOARD_ONLY",
        "sections": {
            "instrument_families": {
                "rows_by_family": families.get("rows_by_family"),
                "instruments_by_family": families.get("instruments_by_family"),
            },
            "signal_score_by_family": {
                "index": score["by_family"]["INDEX_OPTIONS"]["usefulness"],
                "mcx": score["by_family"]["MCX_OPTIONS"]["usefulness"],
            },
            "a_plus_shadow_by_family": {
                "index": aplus["answer_index"], "mcx": aplus["answer_mcx"],
                "thresholds": aplus["proposed_spread_cut_by_family_pct"],
            },
            "tradability": {
                "tradable": tradability["tradable"],
                "caution": tradability["caution"],
                "untradable_on_sample": tradability["untradable_on_sample"],
                "unmeasured": tradability["unmeasured"],
            },
            "loss_attribution": {
                fam: attribution["by_family"][fam]["by_side"] for fam in FAMILIES},
            "feed_before_after": {
                "status": feed["status"],
                "before": feed["before"], "after": feed["after"]},
            "execution_funnel": {
                session: {"recorded": block["recorded"],
                          "binding_limit": block["binding_limit"]}
                for session, block in funnel["by_session"].items()},
            "costed_flow_book": ({"available": False, "reason": flow.get("reason")}
                                 if not flow.get("available") else
                                 {"available": True,
                                  "overall": flow["overall"],
                                  "by_family": flow["by_family"]}),
            "gates": {"status": gates["status"], "failed": gates["failed"]},
        },
        "wiring": "payload only. No endpoint exposes it and no page consumes it",
    }


def by_family_counts(rows: list[dict]) -> dict[str, int]:
    """Row counts per family, for the gate block."""
    return {fam: len(sub) for fam, sub in split(rows).items()}
