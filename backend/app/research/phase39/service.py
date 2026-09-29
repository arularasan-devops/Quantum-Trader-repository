"""Phase 39 service — build the state, write the two artefacts, nothing else.

Read-only by construction: the only database calls in this package are SELECTs,
and Phase 35's raw tables carry triggers that abort an UPDATE or DELETE, so a
mistake here cannot damage the evidence it reads. Nothing is captured, no
derived table is rebuilt, no gate is evaluated and no order path is touched.

The action line is mechanical rather than editorial — it is derived from the
counts, so the document cannot end on a more encouraging note than its own
coverage supports.
"""
from __future__ import annotations

import os

from app.research.phase39 import (
    ARTEFACT_DIR,
    JSON_NAME,
    MD_NAME,
    NOT_AN_EDGE,
    PAPER_ONLY,
    PHASE,
    READ_ONLY,
    REFERENCE_HORIZON,
    RESEARCH_ONLY,
    VERSION,
)
from app.research.phase39 import cost as p39cost
from app.research.phase39 import feasibility as p39feas
from app.research.phase39 import movement as p39move
from app.research.phase39 import report as p39report

CONTINUE_PAPER = "CONTINUE PAPER"
COLLECT_MORE = "COLLECT MORE DATA"
REQUIRES_DATA_FIX = "REQUIRES DATA FIX"


def action_for(state: dict) -> str:
    """One of three, chosen by coverage — never by how good the ratios look.

    No executable book at all is a data problem, not a market finding: the
    capture wrote quotes the cost model cannot price, and that is fixed in the
    feed rather than concluded about the instrument. Anything measured but
    below the label floor is simply more sessions.
    """
    cov = state.get("coverage") or {}
    if not (state.get("pairs") or {}):
        return REQUIRES_DATA_FIX if not cov.get("executable_quotes") else COLLECT_MORE
    tri = state.get("triage") or {}
    if tri.get("keep_accumulating"):
        return CONTINUE_PAPER
    return COLLECT_MORE


def run(con, *, instrument: str | None = None) -> dict:
    """The whole study for one instrument, or for every instrument captured."""
    inst = (instrument or "").strip().upper() or None
    walked = p39move.legs(con, instrument=inst)
    legs = walked["legs"]
    pairs = p39feas.pair_rows(legs)
    state = {
        "phase": PHASE,
        "version": VERSION,
        "mode": [RESEARCH_ONLY, PAPER_ONLY, READ_ONLY],
        "not_an_edge": NOT_AN_EDGE,
        "instrument_filter": inst,
        "reference_horizon": REFERENCE_HORIZON,
        "sessions": sorted({str(leg["session"]) for leg in legs}),
        "instruments": sorted({str(leg["instrument"]) for leg in legs}),
        "legs": len(legs),
        "coverage": walked["coverage"],
        "quote_coverage": p39cost.coverage(con, instrument=inst),
        "cost": p39cost.cost_table(con, instrument=inst),
        "pairs": pairs,
        "ranking": p39feas.ranking(pairs),
        "triage": p39feas.triage(pairs),
    }
    # Pairs the store holds a quote for but no walkable evidence on are undecided
    # too. Leaving them out of the triage entirely would quietly shrink "every
    # F&O instrument/vehicle" down to the ones that happened to be measurable.
    state["triage"]["undecided"] = sorted(set(
        state["triage"]["undecided"]
        + [k for k in state["cost"] if k not in pairs],
    ))
    state["action"] = action_for(state)
    state["headline"] = headline(state)
    return state


def headline(state: dict) -> str:
    tri = state.get("triage") or {}
    ranked = (state.get("ranking") or {}).get("ranked") or []
    top = ranked[0] if ranked else None
    lead = (
        f"top capability {top['key']} at MOVE/COST {top['move_over_cost']} "
        f"(n={top['n']})"
        if top else "no pair cleared the ranking floor"
    )
    return (
        f"{len(state.get('pairs') or {})} instrument/vehicle pairs measured over "
        f"{len(state.get('sessions') or [])} session(s): {lead}; "
        f"{len(tri.get('keep_accumulating') or [])} can cover their round trip, "
        f"{len(tri.get('deprioritise') or [])} cannot, "
        f"{len(tri.get('undecided') or [])} undecided. {state.get('action')}. "
        "Capability only — not an edge."
    )


def summary(state: dict) -> dict:
    """The short form, for a terminal or an operator's eye."""
    return {
        "phase": state.get("phase"),
        "version": state.get("version"),
        "mode": state.get("mode"),
        "instrument_filter": state.get("instrument_filter"),
        "sessions": state.get("sessions"),
        "legs": state.get("legs"),
        "pairs": len(state.get("pairs") or {}),
        "coverage": state.get("coverage"),
        "ranking": state.get("ranking"),
        "triage": {
            k: v for k, v in (state.get("triage") or {}).items() if k != "meaning"
        },
        "action": state.get("action"),
        "not_an_edge": state.get("not_an_edge"),
    }


def write_artefacts(state: dict, *, directory: str = ARTEFACT_DIR) -> list[str]:
    os.makedirs(directory, exist_ok=True)
    md_path = os.path.join(directory, MD_NAME)
    json_path = os.path.join(directory, JSON_NAME)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(p39report.render(state))
    with open(json_path, "w", encoding="utf-8") as fh:
        fh.write(p39report.payload(state))
    return [md_path, json_path]
