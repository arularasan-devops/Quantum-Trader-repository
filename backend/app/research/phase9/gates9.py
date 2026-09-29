"""Phase 9 §15 — the extra gates this phase needs. RESEARCH ONLY.

Phase 7's gates refuse policy comparisons on a thin sample and Phase 8's refuse
expiry conclusions. Phase 9 can fail in three further ways that neither can see:

* the neighbouring-strike counterfactual is only measurable where the ladder was
  actually recorded — GOLD records ~4 strikes, so a "better strike existed" claim on
  GOLD is unsupportable even with 20 sessions of it;
* the shadow qualifier is worthless without a chronological holdout, and an
  in-sample improvement must be prevented from reading as a result;
* the room/extension components derive from ATR, and this dataset's one-minute bars
  are 64% incomplete, so their magnitudes are contaminated.

Each gate names what its failure forbids, in the report rather than in a footnote.
"""
from __future__ import annotations

from app.research.phase7.gates import (
    MAX_MISSING_BAR_PCT,
    MIN_BUYS,
    MIN_BUYS_PER_CELL,
    MIN_REAL_SESSIONS,
)

MIN_LADDER_COVERAGE_PCT = 90.0     # trades whose ladder was recorded at signal time
MIN_LADDER_CANDIDATES = 5          # ATM plus two neighbours each side, at least
MIN_BOOK_COVERAGE_PCT = 90.0       # candidate legs carrying a two-sided book
MIN_HOLDOUT_SESSIONS = 5


def evaluate(*, resolved: int, sessions: int, ladder_coverage_pct: float,
             median_candidates: float | None, book_coverage_pct: float,
             holdout_sessions: int, missing_bar_pct: float,
             smallest_cell: int) -> dict:
    cands = 0.0 if median_candidates is None else float(median_candidates)
    gates = [
        {"gate": "REAL_SESSIONS",
         "need": f">= {MIN_REAL_SESSIONS} recorded sessions",
         "have": sessions, "pass": sessions >= MIN_REAL_SESSIONS,
         "forbids": "any claim that a qualifier, threshold or strike rule is better"},
        {"gate": "RESOLVED_TRADES",
         "need": f">= {MIN_BUYS} resolved trades",
         "have": resolved, "pass": resolved >= MIN_BUYS,
         "forbids": "expectancy and profit-factor comparisons"},
        {"gate": "LADDER_COVERAGE",
         "need": f">= {MIN_LADDER_COVERAGE_PCT}% of trades with a same-side ladder "
                 f"recorded at signal time",
         "have": round(ladder_coverage_pct, 1),
         "pass": ladder_coverage_pct >= MIN_LADDER_COVERAGE_PCT,
         "forbids": "treating the neighbour counterfactual as complete"},
        {"gate": "LADDER_DEPTH",
         "need": f"median >= {MIN_LADDER_CANDIDATES} candidates per signal",
         "have": cands, "pass": cands >= MIN_LADDER_CANDIDATES,
         "forbids": "any statement that a better strike existed — on a 4-strike "
                    "recorded window the alternatives were mostly never captured"},
        {"gate": "BOOK_COVERAGE",
         "need": f">= {MIN_BOOK_COVERAGE_PCT}% of candidate legs with a two-sided book",
         "have": round(book_coverage_pct, 1),
         "pass": book_coverage_pct >= MIN_BOOK_COVERAGE_PCT,
         "forbids": "net-of-spread comparisons across the ladder"},
        {"gate": "CHRONOLOGICAL_HOLDOUT",
         "need": f">= {MIN_HOLDOUT_SESSIONS} sessions held out of the qualifier",
         "have": holdout_sessions, "pass": holdout_sessions >= MIN_HOLDOUT_SESSIONS,
         "forbids": "reading the shadow comparison as evidence — an in-sample filter "
                    "avoids losers by construction"},
        {"gate": "DATA_COMPLETENESS",
         "need": f"missing one-minute bars <= {MAX_MISSING_BAR_PCT}%",
         "have": round(missing_bar_pct, 2),
         "pass": missing_bar_pct <= MAX_MISSING_BAR_PCT,
         "forbids": "any claim resting on ATR, VWAP, extension, expected move or "
                    "room; affected blocks are stamped DATA_CONTAMINATED"},
        {"gate": "CELL_SIZE",
         "need": f">= {MIN_BUYS_PER_CELL} trades in every reported cell",
         "have": smallest_cell, "pass": smallest_cell >= MIN_BUYS_PER_CELL,
         "forbids": "sub-cause / tradability / entry-class / instrument breakdowns"},
    ]
    failed = [g["gate"] for g in gates if not g["pass"]]
    return {
        "gates": gates,
        "failed": failed,
        "status": "EXPLORATORY_ONLY" if failed else "COMPARISON_PERMITTED",
        "verdict": "descriptive statistics are still produced for every failing gate; "
                   "what a failing gate removes is the right to act on the number",
    }
