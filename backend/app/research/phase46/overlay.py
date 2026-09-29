"""Phase 46 — one Phase 45 row in, one overlay row out.

Pure. No I/O, no clock, no journal, no connection, and no access to anything
that exists only after the instant is over: the entire input is the Phase 45
decision-instant row, which is itself built from quantities knowable then.
That is what makes the no-hindsight claim structural rather than a promise, and
:mod:`_smoke_phase46` parses this module to hold it to it.

It also computes no market arithmetic. Every price, cost, expected move and
ratio here is read from the row Phase 45 already journalled under its own
fingerprint. The one thing this module decides is which of the eight words
describes where the research evidence stood relative to the production call.
"""
from __future__ import annotations

import hashlib

from app.research.phase17 import futures as fut_mod
from app.research.phase17 import schema
from app.research.phase45 import (
    BELOW_LIVE_GATE,
    CAPTURE_GAP as P45_CAPTURE_GAP,
    DIRECTION_DISAGREES,
    NOT_A_BUY_CANDIDATE,
    SHADOW_BUY,
    SHADOW_SELL,
    STALE_DECISION_BAR,
    STALE_QUOTE,
)
# The evidence label lives on the evaluator that writes it. Restating the
# string here would be a second definition of "measured".
from app.research.phase45.evaluator import MEASURED as MEASURED_EVIDENCE
from app.research.phase46 import (
    CAPTURE_GAP,
    COST_BLOCKED,
    DISAGREES,
    NO_RESEARCH_EVIDENCE,
    NO_SHADOW_ROW,
    NOT_A_FRESH_CANDIDATE,
    PRODUCTION_BUY,
    PRODUCTION_DID_NOT_CALL_IT,
    PRODUCTION_NONE,
    PRODUCTION_UNCHANGED,
    RESEARCH_OVERLAY,
    RESEARCH_WOULD_ADMIT,
    SHADOW,
    STALE,
    SUPPORTED,
    UNMEASURED,
    VERSION,
    WATCH,
)

# The Phase 45 refusals that mean "a quote was there and was too old", as
# opposed to "there was no quote". Two states rather than one because they need
# opposite fixes and would otherwise leave the same blank column.
_STALE_REASONS: frozenset[str] = frozenset({STALE_QUOTE, STALE_DECISION_BAR})
_GAP_REASONS: frozenset[str] = frozenset({P45_CAPTURE_GAP})

# The production calls that count as a live buy candidate at the instant. The
# engine's own vocabulary, imported rather than restated.
_FRESH: frozenset[str] = frozenset({schema.BUY})


def overlay_id(event_id: str, definition: str) -> str:
    """The journal key: one overlay row per Phase 45 event per definition.

    Derived from provenance rather than from a counter or a clock, so
    re-observing or replaying the same instant converges on the row that is
    already there instead of appending a second opinion about it. The
    definition is part of the key because a row written under a different
    classifier is a row in a different namespace, not an update of this one.
    """
    digest = hashlib.sha256(f"{event_id}|{definition}".encode()).hexdigest()
    return digest[:32]


def _norm_production(row: dict) -> str:
    """The production call at the instant, normalised for comparison only.

    ``production_signal`` is what the engine printed as its market read;
    ``engine_class`` is the candidate class Phase 17 recorded. The market read
    is preferred and the class is the fallback, which is the same precedence
    Phase 17 itself applies. The raw string is kept on the row unchanged.
    """
    for key in ("production_signal", "engine_class"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return PRODUCTION_NONE


def classify(row: dict | None) -> dict:
    """The overlay state for one Phase 45 row, and the reason for it.

    Order matters and is the whole design. "Could the evidence speak at all"
    is asked before "what does it say", so a stale book can never be reported
    as a disagreement and a capture hole can never be reported as a cost
    refusal. Those two mistakes both read as a research opinion about an
    instant when they are facts about the feed.
    """
    if not row:
        return {
            "overlay_state": NO_RESEARCH_EVIDENCE,
            "overlay_reason": NO_SHADOW_ROW,
            "research_reason": None,
        }

    evidence = str(row.get("evidence") or "")
    reason = row.get("reason")
    reason_text = str(reason) if isinstance(reason, str) else None

    if evidence != MEASURED_EVIDENCE:
        if reason_text in _GAP_REASONS:
            state = CAPTURE_GAP
        elif reason_text in _STALE_REASONS:
            state = STALE
        else:
            state = UNMEASURED
        return {
            "overlay_state": state,
            "overlay_reason": reason_text or NO_SHADOW_ROW,
            "research_reason": reason_text,
        }

    if reason_text == DIRECTION_DISAGREES:
        return {
            "overlay_state": DISAGREES,
            "overlay_reason": DIRECTION_DISAGREES,
            "research_reason": reason_text,
        }
    if reason_text == BELOW_LIVE_GATE or row.get("gate_result") is False:
        return {
            "overlay_state": COST_BLOCKED,
            "overlay_reason": BELOW_LIVE_GATE,
            "research_reason": reason_text,
        }
    if reason_text == NOT_A_BUY_CANDIDATE:
        return {
            "overlay_state": WATCH,
            "overlay_reason": NOT_A_FRESH_CANDIDATE,
            "research_reason": reason_text,
        }
    if row.get("shadow_action") in (SHADOW_BUY, SHADOW_SELL):
        supported = _norm_production(row) in _FRESH
        return {
            "overlay_state": SUPPORTED if supported else WATCH,
            "overlay_reason": (
                RESEARCH_WOULD_ADMIT if supported
                else PRODUCTION_DID_NOT_CALL_IT
            ),
            "research_reason": reason_text,
        }
    return {
        "overlay_state": WATCH,
        "overlay_reason": reason_text or NOT_A_FRESH_CANDIDATE,
        "research_reason": reason_text,
    }


def build(row: dict, *, definition: str) -> dict:
    """One overlay journal row: the classification, and the evidence for it.

    Every field the board shows is copied from the Phase 45 row under the name
    the overlay publishes it as. Copied, not recomputed — if the two ever
    disagree it is because someone changed one of them, and that is a bug this
    layer should not be able to hide.
    """
    state = classify(row)
    event_id = str(row.get("event_id") or "")
    return {
        "overlay_id": overlay_id(event_id, definition),
        "event_id": event_id,
        "obs_id": row.get("obs_id"),
        "decision_ts": row.get("decision_ts"),
        "session": row.get("session"),
        "instrument": row.get("instrument"),
        "family": row.get("family"),
        "vehicle": row.get("vehicle"),
        "vehicle_label": row.get("vehicle_label"),
        "contract": row.get("contract"),
        "strike": row.get("strike"),
        "expiry": row.get("expiry"),
        "dte": row.get("dte"),
        # Direction as the research row carried it, and the production read it
        # was derived from, side by side. Collapsing them would hide the case
        # this overlay exists to show: the two disagreeing.
        "direction": row.get("direction"),
        "read_direction": row.get("read_direction"),
        "production_signal": row.get("production_signal"),
        "production_signal_normalised": _norm_production(row),
        "engine_class": row.get("engine_class"),
        "engine_selected_vehicle": row.get("engine_selected_vehicle"),
        "overlay_state": state["overlay_state"],
        "overlay_reason": state["overlay_reason"],
        "research_reason": state["research_reason"],
        "shadow_action": row.get("shadow_action"),
        "evidence": row.get("evidence"),
        "book_state": row.get("book_state"),
        "bid": row.get("bid"),
        "ask": row.get("ask"),
        "bid_size": row.get("bid_size"),
        "ask_size": row.get("ask_size"),
        "premium": row.get("last_traded_price"),
        "spread": row.get("spread"),
        "spread_pct": row.get("spread_pct"),
        "feed_age_ms": row.get("feed_age_ms"),
        "book_age_ms": row.get("book_age_ms"),
        "quote_quality": row.get("quote_quality"),
        "data_quality": row.get("data_quality"),
        "entry_side": row.get("entry_side"),
        "entry_price": row.get("entry_price"),
        "expected_move_points": row.get("expected_move_points"),
        "modelled_cost_points": row.get("modelled_cost_points"),
        "measured_cost_points": row.get("measured_cost_points"),
        "measured_spread_points": row.get("measured_spread_points"),
        "cost_pct_of_entry": row.get("cost_pct_of_entry"),
        "cost_evidence": row.get("cost_evidence"),
        # The user-facing ratio is the modelled one, because that is the figure
        # every historical study in this repository was computed against; the
        # measured one is published beside it, never instead of it, since the
        # difference between the two is exactly what those studies could not
        # see.
        "expected_move_over_modelled_cost": row.get("ratio_modelled"),
        "expected_move_over_measured_cost": row.get("ratio_measured"),
        "gate_multiple": row.get("gate_multiple"),
        "gate_result": row.get("gate_result"),
        "research_candidate": research_candidate(row),
        "shadow_definition": row.get("definition"),
        "definition": definition,
        "classification": RESEARCH_OVERLAY,
        "mode": SHADOW,
        "production_effect": PRODUCTION_UNCHANGED,
        "version": VERSION,
    }


def research_candidate(row: dict) -> str:
    """The candidate identity an overlay row stands on.

    Not a new registry. The research view published here is the Phase 45
    vehicle admission under its own frozen fingerprint, so the candidate is
    named as exactly that, fingerprint included: two overlay rows written under
    different upstream definitions are different candidates and must never pool.
    """
    return f"PHASE45_VEHICLE_ADMISSION@{row.get('definition') or 'UNKNOWN'}"


def vehicle_comparison(rows: list[dict]) -> dict:
    """FUTURES, CE and PE at one decision instant, side by side.

    Only vehicles measured at the *same* instant are compared. A vehicle whose
    book was absent is named as absent rather than filled from a neighbouring
    timestamp, and no winner is selected: the vehicles are listed in a fixed
    order with their own states, because an overlay that picked one would be a
    recommendation.
    """
    by_vehicle = {str(r["vehicle"]): r for r in rows}
    measured = {
        v: r for v, r in by_vehicle.items()
        if r.get("evidence") == MEASURED_EVIDENCE
    }
    first = rows[0] if rows else {}
    return {
        "obs_id": first.get("obs_id"),
        "decision_ts": first.get("decision_ts"),
        "instrument": first.get("instrument"),
        "session": first.get("session"),
        "production_signal": first.get("production_signal"),
        "direction": first.get("direction"),
        "synchronized": sorted(measured),
        "absent": {
            v: r.get("overlay_reason") for v, r in by_vehicle.items()
            if v not in measured
        },
        "complete": len(measured) == 3,
        "no_selection": (
            "VEHICLES_ARE_REPORTED_NOT_RANKED: this is a description of one "
            "instant, not a choice between vehicles."
        ),
        "vehicles": {
            v: {
                "vehicle": v,
                "vehicle_label": r.get("vehicle_label"),
                "contract": r.get("contract"),
                "strike": r.get("strike"),
                "expiry": r.get("expiry"),
                "overlay_state": r.get("overlay_state"),
                "overlay_reason": r.get("overlay_reason"),
                "bid": r.get("bid"),
                "ask": r.get("ask"),
                "spread": r.get("spread"),
                "spread_pct": r.get("spread_pct"),
                "premium": r.get("premium"),
                "entry_side": r.get("entry_side"),
                "entry_price": r.get("entry_price"),
                "expected_move_points": r.get("expected_move_points"),
                "modelled_cost_points": r.get("modelled_cost_points"),
                "measured_cost_points": r.get("measured_cost_points"),
                "expected_move_over_modelled_cost": (
                    r.get("expected_move_over_modelled_cost")
                ),
                "data_quality": r.get("data_quality"),
            }
            for v, r in by_vehicle.items()
        },
    }


def side_of(direction: str | None) -> str | None:
    """The executable side a direction implies, from Phase 17's own mapping."""
    return fut_mod.side_of(direction or "")


def is_buy(production_signal: str | None) -> bool:
    """Whether a production call was a fresh buy candidate at the instant."""
    text = (production_signal or "").strip().upper()
    return text in _FRESH or text == PRODUCTION_BUY
