"""Phase 8 — the evidence label attached to every reported block. RESEARCH ONLY.

The spec's acceptance criterion is that nothing from one day may be called
predictive. Rather than trusting the prose to say so, every block in the report
carries a label computed from its own sample, and the label is the only thing a
reader needs to see to know what the block is worth:

OBSERVATION         a measurement of this dataset. Always safe: it describes what
                    happened, and makes no claim about the next trade.
HYPOTHESIS          a relationship that the data is consistent with, on a sample
                    large enough to be worth testing but too small to trust.
REQUIRES MORE DATA  the cell is too small (or the sessions too few) for the
                    comparison to mean anything at all.
VALIDATED           reserved, and deliberately unreachable from a single dataset:
                    it requires the gates to pass AND the effect to hold on a
                    chronological holdout the study did not see. ``validated()``
                    below is the only way to earn it, and it is never called with a
                    holdout on a one-day sample — so the label is absent from the
                    report rather than quietly redefined.

A block never earns a stronger label than its smallest cell.
"""
from __future__ import annotations

from app.research.phase7.gates import MIN_BUYS_PER_CELL, MIN_REAL_SESSIONS

OBSERVATION = "OBSERVATION"
HYPOTHESIS = "HYPOTHESIS"
NEEDS_DATA = "REQUIRES MORE DATA"
VALIDATED = "VALIDATED"

# A cell this small cannot separate a 40% and a 60% hit rate at any useful
# confidence, so a comparison across such cells is not a weak finding, it is no
# finding. MIN_BUYS_PER_CELL is Phase 7's gate and is reused rather than retuned.
MIN_CELL_FOR_HYPOTHESIS = 10


def label(cell_n: int, sessions: int, *, comparative: bool = True) -> str:
    """The strongest label a block with ``cell_n`` samples may carry.

    ``comparative`` is False for a block that only describes the sample (a count,
    a median, a total): those are observations at any size, because they claim
    nothing beyond the rows they counted.
    """
    if not comparative:
        return OBSERVATION
    if cell_n < MIN_CELL_FOR_HYPOTHESIS:
        return NEEDS_DATA
    if cell_n < MIN_BUYS_PER_CELL or sessions < MIN_REAL_SESSIONS:
        return HYPOTHESIS
    # Even with both satisfied Phase 8 stops at HYPOTHESIS: a passing gate makes a
    # comparison permissible, not conclusive, and promotion to a trading rule is
    # a decision for a human with a contract note and out-of-sample evidence.
    return HYPOTHESIS


def validated(cell_n: int, sessions: int, *, holdout_sessions: int,
              holdout_agrees: bool) -> str:
    """VALIDATED only when the gates pass *and* an unseen chronological holdout
    reproduces the effect. Anything less returns the honest weaker label."""
    if (cell_n >= MIN_BUYS_PER_CELL and sessions >= MIN_REAL_SESSIONS
            and holdout_sessions >= 5 and holdout_agrees):
        return VALIDATED
    return label(cell_n, sessions)


def note(cell_n: int, sessions: int) -> str:
    """One sentence explaining why a block carries the label it carries."""
    if cell_n < MIN_CELL_FOR_HYPOTHESIS:
        return (f"{cell_n} trades in the smallest reported cell — below "
                f"{MIN_CELL_FOR_HYPOTHESIS}, so the split is reported for "
                f"transparency and must not be read as a difference")
    if sessions < MIN_REAL_SESSIONS:
        return (f"{sessions} recorded session(s) — a single session measures that "
                f"session's character, so any relationship here is a candidate to "
                f"test over {MIN_REAL_SESSIONS}+ sessions, not a finding")
    if cell_n < MIN_BUYS_PER_CELL:
        return (f"smallest cell {cell_n} < {MIN_BUYS_PER_CELL} — direction of the "
                f"effect may be real, its size is not measurable here")
    return ("gates satisfied for a comparison; still a hypothesis until it holds "
            "out of sample")
