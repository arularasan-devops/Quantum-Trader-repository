"""Phase 11 §16 — acceptance gates. RESEARCH ONLY.

Phase 10's gates are reused verbatim, because a phase that quietly loosened the
bar it is judged against would be measuring its own optimism. Phase 11 adds the
one failure mode its own questions can suffer that Phase 10's cannot see: a
per-family answer needs a per-family sample, and 336 index trades pooled with 794
MCX trades satisfies a total-row gate while leaving either family's answer
unsupported.

Until every gate passes, the only labels this phase may use are OBSERVATION,
HYPOTHESIS, IN_SAMPLE_ONLY and REQUIRES_MORE_DATA.
"""
from __future__ import annotations

from app.research.phase7.gates import MIN_BUYS, MIN_BUYS_PER_CELL
from app.research.phase10.gates10 import (
    COMPARISON_PERMITTED,
    EXPLORATORY_ONLY,
    IN_SAMPLE_ONLY,
    REQUIRES_MORE_DATA,
)
from app.research.phase10.gates10 import evaluate as evaluate10

from .families import INDEX_OPTIONS, MCX_OPTIONS

# A family answer needs its own resolved trades. Set to the same floor a pooled
# answer needs, because "is the score useful for INDEX?" is a whole question and
# not a fraction of one.
MIN_FAMILY_RESOLVED = MIN_BUYS
# The families Phase 11 is asked about by name. OTHER (single-stock options) is
# reported but is not gated: no question in the spec depends on it.
GATED_FAMILIES = (INDEX_OPTIONS, MCX_OPTIONS)

OBSERVATION = "OBSERVATION"
HYPOTHESIS = "HYPOTHESIS"


def evaluate(*, resolved: int, sessions: int, missing_bar_pct: float,
             book_coverage_pct: float, ladder_coverage_pct: float,
             holdout_sessions: int, smallest_cell: int,
             calibration_train_rows: int, calibration_holdout_rows: int,
             sessions_at_deep_watchlist: int,
             resolved_by_family: dict[str, int],
             smallest_family_cell: dict[str, int]) -> dict:
    base = evaluate10(
        resolved=resolved, sessions=sessions, missing_bar_pct=missing_bar_pct,
        book_coverage_pct=book_coverage_pct,
        ladder_coverage_pct=ladder_coverage_pct,
        holdout_sessions=holdout_sessions, smallest_cell=smallest_cell,
        calibration_train_rows=calibration_train_rows,
        calibration_holdout_rows=calibration_holdout_rows,
        sessions_at_deep_watchlist=sessions_at_deep_watchlist)

    gates = list(base["gates"])
    for fam in GATED_FAMILIES:
        have = int(resolved_by_family.get(fam, 0))
        gates.append({
            "gate": f"FAMILY_SAMPLE_{fam}",
            "need": f">= {MIN_FAMILY_RESOLVED} resolved trades in {fam}",
            "have": have,
            "pass": have >= MIN_FAMILY_RESOLVED,
            "forbids": f"any statement about {fam} — including that the score, the "
                       f"A+ qualifier or an instrument behaves differently there",
        })
        cell = int(smallest_family_cell.get(fam, 0))
        gates.append({
            "gate": f"FAMILY_CELL_SIZE_{fam}",
            "need": f">= {MIN_BUYS_PER_CELL} trades in every reported {fam} cell",
            "have": cell,
            "pass": cell >= MIN_BUYS_PER_CELL,
            "forbids": f"per-bucket and per-instrument breakdowns inside {fam}; the "
                       f"cells are still printed, with their sizes attached",
        })

    failed = [g["gate"] for g in gates if not g["pass"]]
    return {
        "gates": gates,
        "failed": failed,
        "status": EXPLORATORY_ONLY if failed else COMPARISON_PERMITTED,
        "permitted_labels": [OBSERVATION, HYPOTHESIS, IN_SAMPLE_ONLY,
                            REQUIRES_MORE_DATA],
        "family_gates_added_by_phase11": [g["gate"] for g in gates
                                          if g["gate"].startswith("FAMILY_")],
        "verdict": "descriptive statistics are produced for every failing gate. A "
                   "failing gate removes the right to act on the number, and Phase "
                   "11 promotes nothing either way",
    }
