"""Phase 10 §13 — the shadow paper book, decoupled from the AI model. RESEARCH ONLY.

§13's requirement is easy to state and easy to get wrong: the three-column record

    PRODUCTION SIGNAL -> SHADOW PAPER DECISION -> REAL OUTCOME

must keep being written even when no validated model exists to fill the middle
column. The failure mode it guards against is an empty middle column silently
becoming an invented one — a model that is unavailable emitting a number anyway
because a report expected one.

So the middle column has an explicit unavailable state. When no model is validated,
the shadow decision is ``WATCH`` with reason ``MODEL_UNAVAILABLE``: the row is still
written, the production signal is still recorded, the real outcome is still
attached, and no probability is invented. Only the middle column goes blank, and it
goes blank loudly.

The book is a record, not a trader. It places no orders, paper or otherwise: the
"real outcome" column is the outcome of the *production* trade that was actually
recorded, and the shadow column is a label placed beside it after the fact.
"""
from __future__ import annotations

WATCH = "WATCH"
QUALIFY = "QUALIFY"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
NO_VALIDATED_MODEL = "NO_VALIDATED_MODEL"


def shadow_decision(trade: dict, *, model_status: str,
                    a_plus_label: str) -> dict:
    """The middle column for one production signal.

    ``a_plus_label`` is the A+ qualifier's label, which is a rule and not a model:
    it is reported as the rule's opinion regardless of model availability, because
    a threshold on a recorded bid/ask needs no model. What the missing model costs
    is the *probability*, and that is what goes unavailable.
    """
    available = model_status not in (MODEL_UNAVAILABLE, NO_VALIDATED_MODEL)
    return {
        "decision": QUALIFY if (available and a_plus_label == "QUALIFIED") else WATCH,
        "reason": None if available else MODEL_UNAVAILABLE,
        "model_status": model_status,
        "rule_label_a_plus": a_plus_label,
        "probability": None,
        "probability_note": ("no validated score->probability mapping exists, so no "
                            "probability is emitted for this row. The row is still "
                            "recorded"
                            if not available else
                            "a mapping exists but is not deployed in this phase, so "
                            "no probability is attached to a book row"),
    }


def book(trade_rows: list[dict], *, model_status: str) -> dict:
    """The full three-column book over the replayed population."""
    rows = []
    for t in trade_rows:
        label = str(t.get("phase10_qualification", "UNKNOWN"))
        rows.append({
            "production_signal": {
                "ts_ist": t.get("ts_ist"),
                "session": t.get("session"),
                "instrument": t.get("instrument"),
                "symbol": t.get("symbol"),
                "side": t.get("side"),
                "signal_score": t.get("confidence"),
                "decision": "BUY",
                "data_flag": t.get("data_flag"),
            },
            "shadow_paper_decision": shadow_decision(
                t, model_status=model_status, a_plus_label=label),
            "real_outcome": {
                "exit_reason": t.get("exit_reason"),
                "realised_r": t.get("realised_r"),
                "net_r": t.get("net_r"),
                "target_before_stop_hit": t.get("target_before_stop_hit"),
                "held_min": t.get("held_min"),
            },
        })
    watched = sum(1 for r in rows
                  if r["shadow_paper_decision"]["decision"] == WATCH)
    unavailable = sum(1 for r in rows
                      if r["shadow_paper_decision"]["reason"] == MODEL_UNAVAILABLE)
    return {
        "rows_recorded": len(rows),
        "model_status": model_status,
        "shadow_watch": watched,
        "shadow_qualify": len(rows) - watched,
        "rows_with_model_unavailable": unavailable,
        "book": rows,
        "independence": "the book records every production signal and its real "
                        "outcome whether or not a model is available. A missing "
                        "model empties the shadow column and nothing else",
        "invented_probabilities": 0,
        "guarantee": "no order, paper or live, is placed by this module. The real "
                     "outcome column is the recorded production trade's own outcome",
    }
