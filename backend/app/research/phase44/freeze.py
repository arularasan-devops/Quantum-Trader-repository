"""Phase 44 §1 — the definition, hashed before any live row exists.

The order is the whole point. Phase 43 found this shape by measuring 42
hypotheses; if the rule were allowed to move after the first live sessions
arrive, "the arm fires too rarely" would quietly become "the arm was 6x all
along". The hash makes that visible: the recorder refuses to add rows to a
sample armed under a different definition, so a change costs the operator an
explicit re-arm and a gap in the log rather than nothing.

What moves the hash: the rule's arithmetic, the cost functions, the switch's
refusal logic, or any declared constant. What does not: docstrings, comments,
formatting, report rendering and CLI text.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from app.research import fingerprint as fp
from app.research import phase44
from app.research.phase44 import gate, rule

# The arm itself: what is computed at a decision instant and what admits it.
RULE: tuple[Callable, ...] = (
    rule.evaluate, rule.measured_cost, rule.modelled_cost, rule.direction_side,
    rule.minute_bars, rule.Context.index_before, rule.Context.window_fault,
    rule.spec_lot,
)
# What decides whether a row may be written at all. Hashed alongside the rule
# because a recorder that writes while dormant is a different instrument from
# one that does not, even when the arithmetic above it is identical.
GATING: tuple[Callable, ...] = (gate.decision,)

COMPONENTS: dict[str, tuple[Callable, ...]] = {"rule": RULE, "gating": GATING}


def declared() -> dict:
    """The declared numbers, read from the phase module rather than restated."""
    return {
        "source_phase": phase44.SOURCE_PHASE,
        "source_candidate": phase44.SOURCE_CANDIDATE,
        "instrument": phase44.INSTRUMENT,
        "vehicle": phase44.VEHICLE,
        "base_conditions": list(phase44.BASE_CONDITIONS),
        "ratio": phase44.RATIO_KEY,
        "threshold": phase44.THRESHOLD,
        "stop_atr": phase44.STOP_ATR,
        "t1_r": phase44.T1_R,
        "min_bars_before": phase44.MIN_BARS_BEFORE,
        "target_trades": phase44.TARGET_TRADES,
        "target_sessions": phase44.TARGET_SESSIONS,
        "rule_inputs": list(rule.RULE_INPUTS),
        "outcome_fields_forbidden": list(rule.OUTCOME_FIELDS),
        "required_live_fields": list(phase44.REQUIRED_LIVE_FIELDS),
        "version": phase44.VERSION,
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
            "syntax trees of the functions that evaluate the arm at a decision "
            "instant and price its two costs, docstrings removed, plus every "
            "declared constant"
        ),
        "not_hashed": (
            "docstrings, comments, formatting, report rendering and CLI output "
            "— none of which can change a recorded number"
        ),
        "interpreter": "%d.%d.%d" % sys.version_info[:3],
    }
