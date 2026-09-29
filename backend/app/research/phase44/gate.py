"""Phase 44 §3 — the refusal decision, as one pure function.

Separated from :mod:`switch` so the freeze can hash it: a recorder that writes
while dormant is a different instrument from one that does not, even when the
arithmetic above it is identical, so the gating belongs inside the frozen
definition. :mod:`switch` owns the database and calls this; this owns the
decision and knows nothing about storage, which also makes the refusals
testable without a journal.
"""
from __future__ import annotations

from app.research import phase44


def decision(event: dict | None, definition: str) -> dict:
    """Whether recording is permitted, given the last switch event.

    Every path that is not an explicit, current arming returns ``False``. The
    default is a refusal, so an unreadable, empty or half-restored journal
    records nothing rather than something.
    """
    if event is None:
        return {"state": phase44.DORMANT, "may_record": False,
                "refusal": phase44.REFUSED_DORMANT, "armed_under": None,
                "since": None, "definition": definition}
    armed_under = str(event.get("definition") or "")
    since = event.get("ts")
    if str(event.get("state") or "") != phase44.ARMED:
        return {"state": phase44.DORMANT, "may_record": False,
                "refusal": "REFUSED: the recorder was explicitly disarmed",
                "armed_under": armed_under, "since": since,
                "definition": definition}
    if armed_under != definition:
        return {"state": phase44.DORMANT, "may_record": False,
                "refusal": phase44.REFUSED_DEFINITION_CHANGED,
                "armed_under": armed_under, "since": since,
                "definition": definition}
    return {"state": phase44.ARMED, "may_record": True, "refusal": None,
            "armed_under": armed_under, "since": since,
            "definition": definition}
