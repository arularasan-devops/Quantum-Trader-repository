"""Phase 7 Part 24-25 — what this dataset is allowed to conclude. RESEARCH ONLY.

The point of this module is to make an over-claim impossible to write by
accident. Every report runs the gates, and the report's verdict is derived from
them: while a gate fails, the matching class of conclusion is refused in the
output itself rather than in a footnote nobody reads.
"""
from __future__ import annotations

# Minimums are the spec's, and they are deliberately unforgiving. The failures
# of Phases 2-5 were all the same failure: a plausible result from a sample too
# small to have a result.
MIN_REAL_SESSIONS = 20
MIN_BUYS = 100
MIN_BUYS_PER_CELL = 30
MAX_MISSING_BAR_PCT = 5.0
MIN_SHARED_LEGS = 20


def evaluate(chain: dict, candle: dict, events: int, per_cell_min: int,
             shared_legs: int) -> dict:
    """PASS/FAIL per gate, plus what each failure forbids."""
    real = chain.get("REAL_BROKER", {})
    sessions = int(real.get("sessions") or 0)
    missing_pct = float(candle.get("missing_pct") or 0.0)
    spread = bool(chain.get("verdict", {}).get("spread_measurable"))
    gates = [
        {
            "gate": "REAL_SESSIONS",
            "need": f">= {MIN_REAL_SESSIONS} recorded REAL_BROKER sessions",
            "have": sessions,
            "pass": sessions >= MIN_REAL_SESSIONS,
            "forbids": "any statement of the form 'policy X is better' — one "
                       "session measures that session's character",
        },
        {
            "gate": "SAMPLE_SIZE",
            "need": f">= {MIN_BUYS} reconstructed production BUYs",
            "have": events,
            "pass": events >= MIN_BUYS,
            "forbids": "expectancy and profit-factor comparisons between policies",
        },
        {
            "gate": "CELL_SIZE",
            "need": f">= {MIN_BUYS_PER_CELL} BUYs in every reported cell",
            "have": per_cell_min,
            "pass": per_cell_min >= MIN_BUYS_PER_CELL,
            "forbids": "regime / direction / instrument breakdowns",
        },
        {
            "gate": "DATA_COMPLETENESS",
            "need": f"missing one-minute bars <= {MAX_MISSING_BAR_PCT}%",
            "have": missing_pct,
            "pass": missing_pct <= MAX_MISSING_BAR_PCT,
            "forbids": "any claim resting on ATR, VWAP, extension or regime",
        },
        {
            "gate": "SPREAD_AVAILABLE",
            "need": "bid and ask recorded on the option legs",
            "have": spread,
            "pass": spread,
            "forbids": "any absolute expectancy claim — a mid-less premium path "
                       "flatters every entry and every exit",
        },
        {
            "gate": "MANUAL_COMPARISON",
            "need": f">= {MIN_SHARED_LEGS} legs traded by both hand and bot",
            "have": shared_legs,
            "pass": shared_legs >= MIN_SHARED_LEGS,
            "forbids": "treating the manual-vs-bot gap as an effect size",
        },
    ]
    failed = [g["gate"] for g in gates if not g["pass"]]
    return {
        "gates": gates,
        "passed": [g["gate"] for g in gates if g["pass"]],
        "failed": failed,
        "conclusions_permitted": not failed,
        "status": "EXPLORATORY_ONLY" if failed else "READY_FOR_VALIDATION",
        "meaning": (
            "EXPLORATORY_ONLY: every number below describes what happened in the "
            "recorded sample and is a hypothesis, not a finding. No production "
            "rule may change on this basis."
            if failed else
            "READY_FOR_VALIDATION: the sample meets the acceptance criteria; a "
            "proposal may be written up for out-of-sample testing — still not for "
            "direct deployment."
        ),
    }
