"""Phase 41 — one pass: fingerprint the definition, race the store, accumulate.

Read-only over the store throughout. The one write is the definition ledger, and
it goes to the artefact directory rather than the database.

Phase 40 is called, not copied. :func:`app.research.phase40.firstevent.legs` is
the same function the fingerprint hashes, so the figures below cannot drift from
the ones Phase 40's own CLI prints — if they ever did, one of the two would be a
reimplementation and the freeze would be worthless.
"""
from __future__ import annotations

from app.research.phase40 import firstevent as fe
from app.research.phase40 import service as p40service
from app.research.phase41 import (
    GOVERNING_FRAME,
    HORIZON,
    MIN_CLASSIFIED,
    NOT_A_STRATEGY,
    PAPER_ONLY,
    PHASE,
    READ_ONLY,
    REPORTING_RULE,
    RESEARCH_ONLY,
    VERSION,
    Z,
    Z_NOT_A_TEST,
    accumulate,
    freeze,
    ledger,
)


def per_instrument(legs: list[dict]) -> list[dict]:
    """The accumulation summary per instrument, in Phase 40's own order.

    Kept deliberately thin: the sequence view per instrument, not a second copy
    of Phase 40's per-instrument tables. Reading whether an instrument's answer
    is settling is a different question from reading what its answer is, and
    Phase 40 already answers the latter.

    ``verdict`` is the non-overlapping one here for the same reason it is at the
    top level: per instrument the overlap is worse rather than better, because
    one contract's windows are sampled from one session's single price path.
    """
    names = p40service._order(sorted({str(x["instrument"]) for x in legs}))
    out = []
    for name in names:
        rows = [x for x in legs if str(x["instrument"]) == name]
        built = accumulate.build(rows)
        gov = built[GOVERNING_FRAME]
        out.append({
            "instrument": name,
            "legs": len(rows),
            "sessions": len(accumulate.sessions_of(rows)),
            "verdict": gov["pooled"]["verdict"],
            "classified": gov["pooled"]["classified"],
            "verdict_all_legs_descriptive": (
                built["all_legs"]["pooled"]["verdict"]
            ),
            "stability": gov["stability"]["stability"],
            "cumulative": gov["cumulative"],
            "cumulative_all_legs_descriptive": built["all_legs"]["cumulative"],
            "resolution": gov["resolution"],
        })
    return out


def run(con, *, instrument: str | None = None,
        directory: str | None = None) -> dict:
    """The frozen diagnostic over every session in the store.

    ``directory`` is where the ledger lives; when it is omitted the run is not
    recorded, which is what the read-only checks and the API use.
    """
    fingerprint = freeze.fingerprint()
    raced = fe.legs(con, instrument=instrument)
    legs = raced["legs"]
    payload = {
        "phase": PHASE,
        "version": VERSION,
        "mode": [RESEARCH_ONLY, PAPER_ONLY, READ_ONLY],
        "not_a_strategy": NOT_A_STRATEGY,
        "instrument_filter": instrument,
        "reference_horizon": HORIZON,
        "materiality_z": Z,
        "min_classified_for_verdict": MIN_CLASSIFIED,
        "reporting_rule": REPORTING_RULE,
        "governing_frame": GOVERNING_FRAME,
        "descriptive_frame_caveat": Z_NOT_A_TEST,
        "frozen_definition": fingerprint,
        "sessions": accumulate.sessions_of(legs),
        "legs": len(legs),
        "coverage": raced["coverage"],
        "accumulation": accumulate.build(legs),
        "per_instrument": per_instrument(legs),
    }
    history = ledger.read(directory) if directory else []
    payload["definition_state"] = ledger.compare(history, fingerprint)
    payload["reporting_rule_state"] = ledger.reporting_rule_state(history)
    payload["definition_history"] = [
        {k: r.get(k) for k in
         ("at_iso", "definition", "reporting_rule", "sessions", "legs",
          "verdict")}
        for r in history
    ]
    if directory:
        payload["recorded"] = ledger.append(
            directory, fingerprint=fingerprint, payload=payload,
        )
    return payload
