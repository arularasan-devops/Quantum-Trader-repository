"""Phase 42 — the definition, frozen before anything is measured.

The order matters and is the whole reason this file exists. §19's room bands
were cut after looking at the data, which is why a monotone table across four
bands carries so little weight: with the outcome in hand, a cut that separates
winners from losers can always be found. Here the multiples, the window, the
floors and the split are declared in :mod:`app.research.phase42` before the
sweep is run, and this module hashes them together with the behaviour of the
functions that turn them into an answer.

The hash therefore moves if the ratio changes, if the trailing range changes, if
the arms change or if the chooser changes — and stays still for a corrected
docstring or a reflowed line. What it protects against is comparing a stored
artefact with a later one measured under a different rule, which is precisely
the mistake Phase 41 was created to prevent after two Phase 40 releases
reported different answers with nothing in either payload saying which was
which.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from app.research import fingerprint as fp
from app.research import phase42
from app.research.phase42 import arms, exante, giveback, validate

# What decides the ex-ante quantity itself. A change in here invalidates every
# arm, because the number being thresholded is no longer the same number.
RATIO: tuple[Callable, ...] = (
    exante.ratio, exante.leg_delta, exante.admits,
    exante.TrailingRange.before, exante.trailing_ranges,
)
# What turns the quantity into pools and statistics.
POOLS: tuple[Callable, ...] = (
    arms.one_session, arms._legs, arms.Arm.observe, arms.Tally.add,
    arms.Tally.stats, arms.SessionResult.arm_rows,
)
# What chooses a multiple and labels the result. Separated because a change here
# changes which answer is published without changing any measured figure.
CHOICE: tuple[Callable, ...] = (
    validate.choose, validate.split, validate.walk_forward, validate.verdict,
    validate._mean, validate.with_evidence, validate.cost_bases,
    validate.dominant_basis, validate.basis_across_cut,
)
# The §2 giveback decomposition, hashed apart: it answers a different question
# and a change to it cannot move an arm.
GIVEBACK: tuple[Callable, ...] = (
    giveback.decompose, giveback.HorizonTally.add, giveback.HorizonTally.stats,
    giveback._rows, giveback.peak_and_time, giveback.share,
)

COMPONENTS: dict[str, tuple[Callable, ...]] = {
    "ratio": RATIO, "pools": POOLS, "choice": CHOICE, "giveback": GIVEBACK,
}


def declared() -> dict:
    """The declared numbers, read from the phase module rather than restated."""
    return {
        "thresholds": list(phase42.THRESHOLDS),
        "live_multiple": phase42.LIVE_MULTIPLE,
        "range_window": phase42.RANGE_WINDOW,
        "min_range_samples": phase42.MIN_RANGE_SAMPLES,
        "futures_delta": phase42.FUTURES_DELTA,
        "min_arm_legs": phase42.MIN_ARM_LEGS,
        "min_sessions": phase42.MIN_SESSIONS,
        "min_sessions_for_split": phase42.MIN_SESSIONS_FOR_SPLIT,
        "min_session_legs": phase42.MIN_SESSION_LEGS,
        "dev_share": phase42.DEV_SHARE,
        "ratio_inputs": list(exante.RATIO_INPUTS),
        "outcome_fields_forbidden": list(exante.OUTCOME_FIELDS),
        "version": phase42.VERSION,
    }


def fingerprint() -> dict:
    """One hash for the definition, one per component so a move is diagnosable."""
    hashed = fp.components(COMPONENTS)
    parts: dict[str, str] = dict(hashed["components"])
    numbers = declared()
    parts["declared"] = fp.digest([f"{k}={numbers[k]}" for k in sorted(numbers)])
    return {
        "definition": fp.digest([f"{k}={parts[k]}" for k in sorted(parts)]),
        "components": parts,
        "covers": hashed["covers"],
        "declared": numbers,
        "hashed": (
            "syntax trees of the functions that form the ex-ante ratio, pool "
            "the legs, choose the multiple and decompose the giveback, with "
            "docstrings removed, plus the declared multiples, window, floors "
            "and split"
        ),
        "not_hashed": (
            "docstrings, comments, formatting, report rendering and CLI output "
            "— none of which can change a measured number"
        ),
        "interpreter": "%d.%d.%d" % sys.version_info[:3],
    }
