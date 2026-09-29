"""Phase 10 §17 — final acceptance gates. RESEARCH ONLY.

Phase 9's gates are reused unchanged; Phase 10 adds the two failures its own
questions can suffer that Phase 9's cannot see:

* a calibration mapping needs THREE chronological blocks (train, development,
  unseen holdout), not two, and each needs enough rows to fit a monotone curve.
  Two blocks lets the curve be tuned on the block it is scored on;
* the tier-split claim needs sessions recorded at BOTH settings. Every session in
  this dataset ran at 50 instruments, so the "after" does not exist and no
  improvement can be claimed however obvious the mechanism is.

The permitted interim labels are Phase 8's: OBSERVATION, HYPOTHESIS,
IN_SAMPLE_ONLY, REQUIRES_MORE_DATA. VALIDATED requires every gate.
"""
from __future__ import annotations

from app.research.phase7.gates import (
    MAX_MISSING_BAR_PCT,
    MIN_BUYS,
    MIN_BUYS_PER_CELL,
    MIN_REAL_SESSIONS,
)
from app.research.phase9.gates9 import (
    MIN_BOOK_COVERAGE_PCT,
    MIN_HOLDOUT_SESSIONS,
    MIN_LADDER_COVERAGE_PCT,
)

# A monotone fit on fewer rows than this reproduces the sample, not the signal.
MIN_CALIBRATION_TRAIN_ROWS = 150
MIN_CALIBRATION_HOLDOUT_ROWS = 60
MIN_CALIBRATION_BLOCKS = 3

IN_SAMPLE_ONLY = "IN_SAMPLE_ONLY"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
EXPLORATORY_ONLY = "EXPLORATORY_ONLY"
COMPARISON_PERMITTED = "COMPARISON_PERMITTED"


def evaluate(*, resolved: int, sessions: int, missing_bar_pct: float,
             book_coverage_pct: float, ladder_coverage_pct: float,
             holdout_sessions: int, smallest_cell: int,
             calibration_train_rows: int, calibration_holdout_rows: int,
             sessions_at_deep_watchlist: int) -> dict:
    gates = [
        {"gate": "REAL_SESSIONS",
         "need": f">= {MIN_REAL_SESSIONS} recorded sessions",
         "have": sessions, "pass": sessions >= MIN_REAL_SESSIONS,
         "forbids": "calling anything VALIDATED, and any claim that a qualifier, "
                    "threshold, watchlist or mapping is better"},
        {"gate": "RESOLVED_TRADES",
         "need": f">= {MIN_BUYS} resolved trades",
         "have": resolved, "pass": resolved >= MIN_BUYS,
         "forbids": "expectancy and profit-factor comparisons"},
        {"gate": "DATA_COMPLETENESS",
         "need": f"missing one-minute bars <= {MAX_MISSING_BAR_PCT}%",
         "have": round(missing_bar_pct, 2),
         "pass": missing_bar_pct <= MAX_MISSING_BAR_PCT,
         "forbids": "any claim resting on ATR, VWAP, extension, expected move or "
                    "room; affected blocks are stamped DATA_CONTAMINATED"},
        {"gate": "BOOK_COVERAGE",
         "need": f">= {MIN_BOOK_COVERAGE_PCT}% of legs with a two-sided book",
         "have": round(book_coverage_pct, 1),
         "pass": book_coverage_pct >= MIN_BOOK_COVERAGE_PCT,
         "forbids": "net-of-spread comparisons and spread-class thresholds"},
        {"gate": "LADDER_COVERAGE",
         "need": f">= {MIN_LADDER_COVERAGE_PCT}% of trades with a recorded ladder",
         "have": round(ladder_coverage_pct, 1),
         "pass": ladder_coverage_pct >= MIN_LADDER_COVERAGE_PCT,
         "forbids": "treating the strike counterfactual as complete"},
        {"gate": "CHRONOLOGICAL_HOLDOUT",
         "need": f">= {MIN_HOLDOUT_SESSIONS} sessions held out",
         "have": holdout_sessions, "pass": holdout_sessions >= MIN_HOLDOUT_SESSIONS,
         "forbids": "reading the A+ comparison as evidence — an in-sample filter "
                    "avoids losers by construction"},
        {"gate": "CELL_SIZE",
         "need": f">= {MIN_BUYS_PER_CELL} trades in every reported cell",
         "have": smallest_cell, "pass": smallest_cell >= MIN_BUYS_PER_CELL,
         "forbids": "per-bucket, per-band, per-instrument and per-class breakdowns"},
        {"gate": "CALIBRATION_SAMPLE",
         "need": f">= {MIN_CALIBRATION_TRAIN_ROWS} training rows and "
                 f">= {MIN_CALIBRATION_HOLDOUT_ROWS} unseen holdout rows across "
                 f">= {MIN_CALIBRATION_BLOCKS} chronological blocks",
         "have": {"train_rows": calibration_train_rows,
                  "holdout_rows": calibration_holdout_rows},
         "pass": (calibration_train_rows >= MIN_CALIBRATION_TRAIN_ROWS
                  and calibration_holdout_rows >= MIN_CALIBRATION_HOLDOUT_ROWS),
         "forbids": "shipping, displaying or acting on a score->probability "
                    "mapping; a curve fitted below this reproduces the sample"},
        {"gate": "TIER_SPLIT_MEASURED",
         "need": ">= 1 session recorded with a deep watchlist configured, so the "
                 "before/after comparison has an after",
         "have": sessions_at_deep_watchlist,
         "pass": sessions_at_deep_watchlist >= 1,
         "forbids": "any claim that reducing the deep watchlist improved bar "
                    "completeness, stale rate, latency or capacity — the mechanism "
                    "is arithmetic, the improvement is not yet measured"},
    ]
    failed = [g["gate"] for g in gates if not g["pass"]]
    return {
        "gates": gates,
        "failed": failed,
        "status": EXPLORATORY_ONLY if failed else COMPARISON_PERMITTED,
        "permitted_labels": ["OBSERVATION", "HYPOTHESIS", IN_SAMPLE_ONLY,
                             REQUIRES_MORE_DATA],
        "verdict": "descriptive statistics are still produced for every failing "
                   "gate; what a failing gate removes is the right to act on the "
                   "number, and nothing here is promoted either way",
    }
