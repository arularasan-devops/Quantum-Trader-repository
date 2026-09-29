"""Phase 41 — the fingerprint of the Phase 40 definition. Reads code, not data.

The fingerprint has to move when an answer could move, and stay still when it
could not. That rules out both of the easy implementations. Hashing the declared
constants alone would have missed the error that mattered most here — the race
origin moved from the entry fill to the exit side inside a function body, with
every constant untouched. Hashing the file text would move on a typo in a
docstring and cry wolf until nobody reads it.

So it is taken over the *behaviour*: each function that decides the race, the
money or the verdict is parsed, its docstrings dropped, and the resulting syntax
tree dumped. Comments and formatting never reach the hash because they are gone
before the parser finishes; a renamed local variable does reach it, which is the
conservative direction to err in. The declared thresholds, horizons, caps and
test parameters are hashed beside them.

Each component is hashed separately as well as together, so a changed
fingerprint says *which* part of the definition moved rather than only that
something did.

The serialisation and hashing themselves live in :mod:`app.research.fingerprint`
because a second phase now freezes a definition of its own; what stays here is
only *which* functions and numbers this definition consists of.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from app.research import fingerprint as fp
from app.research import phase39, phase40
from app.research.phase35 import book as p35book
from app.research.phase39 import cost as p39cost
from app.research.phase39 import movement as p39movement
from app.research.phase40 import firstevent as fe
from app.research.phase40 import metrics as m
from app.research.phase40 import verdict as v

# The functions whose bodies decide an answer, grouped by the question they
# decide. A function absent from here cannot change a number — that claim is
# checked by the smoke, which walks the modules and fails on an unlisted
# public function rather than trusting this list to stay complete.
RACE: tuple[Callable, ...] = (
    fe.walk, fe.legs, fe.independent, fe.cohort, fe.selections, fe._key,
    p35book.entry_fill, p35book.exit_fill, p35book.gross_sign,
    p39movement.instants, p39movement.Paths.get,
    p39movement._ContractSession.__init__, p39movement._first_after,
)
COST: tuple[Callable, ...] = (
    p39cost.required_move, p39cost.fee_points, p39cost.resolve_lot,
    p39cost.executable_quotes, p39cost.stride, p39cost.leg_direction,
    p35book.option_cost, p35book.futures_cost,
)
MONEY: tuple[Callable, ...] = (
    fe.leg_gross_pct, fe.leg_net_pct,
    m.horizon_row, m.giveback_row, m.adverse_row, m.waterfall,
    m.vehicle_comparison, m.by_key, m.median, m.p75, m._pct,
    m._profit_factor,
)
TEST: tuple[Callable, ...] = (v.sign_test, v.classify)

COMPONENTS: dict[str, tuple[Callable, ...]] = {
    "race": RACE, "cost": COST, "money": MONEY, "test": TEST,
}


# The serialisation moved to :mod:`app.research.fingerprint` so Phase 42 hashes
# its definition the same way rather than growing a second implementation that
# would drift. These names stay because the hash they produce is unchanged and
# the Phase 41 smoke checks the serialisation through them.
VERSION_SPECIFIC = fp.VERSION_SPECIFIC
_canonical = fp.canonical


def _behaviour(fn: Callable) -> str:
    """The behaviour hash input for one function. See :mod:`..fingerprint`."""
    return fp.behaviour(fn)


def _digest(parts: list[str]) -> str:
    return fp.digest(parts)


def declared() -> dict:
    """The declared numbers the diagnostic runs on, read from their modules.

    Read rather than restated: a copy of a threshold in this file would be a
    second threshold, and the fingerprint would go on agreeing while the two
    drifted apart.
    """
    return {
        "horizons": list(phase40.FIRST_EVENT_HORIZONS),
        "session_close": phase40.CLOSE,
        "reference_horizon": phase40.REFERENCE,
        "materiality_z": phase40.MATERIALITY_Z,
        "min_classified_for_verdict": phase40.MIN_CLASSIFIED_FOR_VERDICT,
        "min_instants_for_label": phase39.MIN_INSTANTS_FOR_LABEL,
        "max_instants_per_session_key": (
            phase39.MAX_MOVE_INSTANTS_PER_SESSION_KEY
        ),
        "max_cost_samples_per_key": phase39.MAX_COST_SAMPLES_PER_KEY,
        "cost_fixed_point_iterations": phase39.COST_FIXED_POINT_ITERATIONS,
        "phase40_version": phase40.VERSION,
        "phase39_version": phase39.VERSION,
    }


def fingerprint() -> dict:
    """The frozen definition: one hash overall, one per component.

    ``definition`` is what a payload carries and a reader compares. The
    per-component hashes exist so that a mismatch is diagnosable: "the race
    changed" and "the report changed" call for different reactions, and only
    the first invalidates a comparison.
    """
    hashed = fp.components(COMPONENTS)
    parts: dict[str, str] = dict(hashed["components"])
    members: dict[str, list[str]] = hashed["covers"]
    numbers = declared()
    parts["declared"] = _digest([f"{k}={numbers[k]}" for k in sorted(numbers)])
    return {
        "definition": _digest([f"{k}={parts[k]}" for k in sorted(parts)]),
        "components": parts,
        "covers": members,
        "declared": numbers,
        "hashed": (
            "syntax trees of the functions that decide the race, the cost, the "
            "money and the test, with docstrings removed, plus the declared "
            "thresholds, horizons, caps and deviate"
        ),
        "not_hashed": (
            "docstrings, comments, formatting, report rendering and CLI "
            "output — none of which can change a measured number"
        ),
        "interpreter": "%d.%d.%d" % sys.version_info[:3],
        "portable": (
            "the same code hashes the same on any interpreter: the tree is "
            "serialised here rather than by ast.dump or ast.unparse, whose "
            "spelling changes between versions. The interpreter is recorded "
            "for diagnosis only and is not hashed"
        ),
    }
