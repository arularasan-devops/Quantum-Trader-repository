"""Phase 45 — the fingerprint every shadow row carries.

Two hashes, and the difference between them is the point. ``definition`` covers
this phase's own behaviour: how a vehicle row is built, which book states it
refuses, how a size is read. ``inherited`` records the Phase 41/42 fingerprints
*as they are*, read and never written, so a later reader can tell a Phase 45 row
measured under today's Phase 42 arithmetic from one measured under a corrected
version — without which the two would pool silently and the pooled number would
belong to neither definition.

That failure has already happened here: two Phase 40 releases published
different answers with nothing in either payload saying which was which. A row
whose fingerprint moves is a row in a new namespace, and the journal keys on it.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from app.research import fingerprint as fp
from app.research import phase41, phase45
from app.research.phase41 import freeze as p41freeze
from app.research.phase42 import freeze as p42freeze
from app.research.phase45 import evaluator, ranges

# How a vehicle row is decided. A change here changes what the board says at an
# unchanged instant, so it must move the namespace.
DECISION: tuple[Callable, ...] = (
    evaluator.evaluate, evaluator._row, evaluator._action, evaluator._option,
    evaluator._unmeasured, evaluator._lot, evaluator._vehicle_agrees,
    evaluator._quote_dict, evaluator._px, evaluator._size,
)
# How the pre-decision range is accumulated live. Separated because a defect
# here shows up as UNMEASURED rows rather than as different admissions.
RANGE: tuple[Callable, ...] = (
    ranges.LiveRanges.note, ranges.LiveRanges.before, ranges._empty,
)
# How the vehicles are compared at one instant. A change here cannot move an
# admission, only which vehicle is reported as the better-priced one.
COMPARE: tuple[Callable, ...] = (evaluator.comparison, evaluator.event_id)

COMPONENTS: dict[str, tuple[Callable, ...]] = {
    "decision": DECISION, "range": RANGE, "compare": COMPARE,
}


def declared() -> dict:
    """The declared constants, read from the phase module rather than restated."""
    return {
        "version": phase45.VERSION,
        "actions": list(phase45.ACTIONS),
        "unmeasured_reasons": list(phase45.UNMEASURED_REASONS),
        "wait_reasons": list(phase45.WAIT_REASONS),
        "lifecycle": list(phase45.LIFECYCLE),
        "vehicles": list(phase45.VEHICLES),
        "max_decision_bar_age_sec": phase45.MAX_DECISION_BAR_AGE_SEC,
        "outcome_fields_forbidden": list(phase45.OUTCOME_FIELDS),
        "paper_only": phase45.PAPER_ONLY,
        "no_order_path": phase45.NO_ORDER_PATH,
    }


def inherited() -> dict:
    """The Phase 41/42 fingerprints, as they are. Read-only by construction."""
    return {
        "phase41": p41freeze.fingerprint()["definition"],
        "phase42": p42freeze.fingerprint()["definition"],
        "phase41_version": phase41.VERSION,
    }


def fingerprint() -> dict:
    """One hash for this phase, one per component, plus what it inherits."""
    hashed = fp.components(COMPONENTS)
    parts: dict[str, str] = dict(hashed["components"])
    numbers = declared()
    parts["declared"] = fp.digest([f"{k}={numbers[k]}" for k in sorted(numbers)])
    upstream = inherited()
    parts["inherited"] = fp.digest(
        [f"{k}={upstream[k]}" for k in sorted(upstream)]
    )
    return {
        "definition": fp.digest([f"{k}={parts[k]}" for k in sorted(parts)]),
        "components": parts,
        "covers": hashed["covers"],
        "declared": numbers,
        "inherited": upstream,
        "hashed": (
            "syntax trees of the functions that build a vehicle row, accumulate "
            "the pre-decision range and compare the vehicles, with docstrings "
            "removed, plus the declared actions, reasons, lifecycle and "
            "freshness bar, plus the Phase 41 and Phase 42 definition hashes "
            "this phase's arithmetic is borrowed from"
        ),
        "not_hashed": (
            "docstrings, comments, formatting, board rendering, journal SQL and "
            "outcome resolution — none of which can change what the board "
            "admits at an instant"
        ),
        "interpreter": "%d.%d.%d" % sys.version_info[:3],
    }


def definition() -> str:
    """The short hash a journal row stores."""
    return str(fingerprint()["definition"])
