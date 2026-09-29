"""Phase 46 — the fingerprint every overlay row carries.

Two hashes again, for the same reason Phase 45 has two. ``definition`` covers
how this layer classifies an instant: which reason maps to which of the eight
words, and in what order the questions are asked. ``inherited`` records the
Phase 41, 42 and 45 fingerprints *as they are*, read and never written, so a
later reader can tell an overlay row built on today's Phase 45 admission from
one built on a corrected version.

Without that, the two would pool silently and the pooled column would belong to
neither classifier. That is not hypothetical here: two Phase 40 releases
published different answers with nothing in either payload saying which was
which, and the journal keys on this hash precisely so it cannot happen again.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from app.research import fingerprint as fp
from app.research import phase46
from app.research.phase41 import freeze as p41freeze
from app.research.phase42 import freeze as p42freeze
from app.research.phase45 import freeze as p45freeze
from app.research.phase46 import compare, overlay

# How an instant is classified. A change here changes what the overlay says at
# an unchanged instant, so it must move the namespace.
CLASSIFY: tuple[Callable, ...] = (
    overlay.classify, overlay._norm_production, overlay.build,
    overlay.research_candidate, overlay.overlay_id, overlay.is_buy,
)
# How the vehicles are laid beside each other. Cannot move a classification,
# only what the comparison strip shows.
COMPARE: tuple[Callable, ...] = (overlay.vehicle_comparison,)
# How the two arms are measured once outcomes exist. Separated because this
# runs long after admission and can never feed back into it.
OUTCOMES: tuple[Callable, ...] = (
    compare.metrics, compare.drawdown, compare.arms, compare.report,
)

COMPONENTS: dict[str, tuple[Callable, ...]] = {
    "classify": CLASSIFY, "compare": COMPARE, "outcomes": OUTCOMES,
}

_CACHE: dict | None = None


def declared() -> dict:
    """The declared constants, read from the phase module rather than restated."""
    return {
        "version": phase46.VERSION,
        "states": list(phase46.STATES),
        "silent_states": sorted(phase46.SILENT),
        "overlay_reasons": list(phase46.OVERLAY_REASONS),
        "arms": list(phase46.ARMS),
        "min_resolved_for_a_comparison": phase46.MIN_RESOLVED_FOR_A_COMPARISON,
        "outcome_fields_forbidden": list(phase46.OUTCOME_FIELDS),
        "classification": phase46.RESEARCH_OVERLAY,
        "mode": phase46.SHADOW,
        "paper_only": phase46.PAPER_ONLY,
        "no_order_path": phase46.NO_ORDER_PATH,
        "production_effect": phase46.PRODUCTION_UNCHANGED,
    }


def inherited() -> dict:
    """The upstream fingerprints, as they are. Read-only by construction."""
    return {
        "phase41": p41freeze.fingerprint()["definition"],
        "phase42": p42freeze.fingerprint()["definition"],
        "phase45": p45freeze.definition(),
    }


def fingerprint(*, cached: bool = True) -> dict:
    """One hash for this phase, one per component, plus what it inherits.

    Memoised for the same measured reason Phase 45 memoises its own: the inputs
    are syntax trees that cannot change while the process runs, and re-parsing
    them once per journalled observation was the entire cost of the shadow
    board. ``cached=False`` recomputes, for a test that edits a classifier and
    expects the namespace to move.
    """
    global _CACHE
    if cached and _CACHE is not None:
        return dict(_CACHE)
    hashed = fp.components(COMPONENTS)
    parts: dict[str, str] = dict(hashed["components"])
    numbers = declared()
    parts["declared"] = fp.digest([f"{k}={numbers[k]}" for k in sorted(numbers)])
    upstream = inherited()
    parts["inherited"] = fp.digest(
        [f"{k}={upstream[k]}" for k in sorted(upstream)]
    )
    out = {
        "definition": fp.digest([f"{k}={parts[k]}" for k in sorted(parts)]),
        "components": parts,
        "covers": hashed["covers"],
        "declared": numbers,
        "inherited": upstream,
        "hashed": (
            "syntax trees of the functions that classify an instant, lay the "
            "vehicles beside each other and measure the two arms, with "
            "docstrings removed, plus the declared states, reasons and arms, "
            "plus the Phase 41, Phase 42 and Phase 45 definition hashes whose "
            "evidence this overlay describes"
        ),
        "not_hashed": (
            "docstrings, comments, formatting, board rendering, journal SQL "
            "and the API surface — none of which can change which word the "
            "overlay publishes at an instant"
        ),
        "interpreter": "%d.%d.%d" % sys.version_info[:3],
    }
    if cached:
        _CACHE = dict(out)
    return out


def definition(*, cached: bool = True) -> str:
    """The short hash an overlay journal row stores."""
    return str(fingerprint(cached=cached)["definition"])
