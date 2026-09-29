"""Phase 8 Part N — the extra gates this study needs. RESEARCH ONLY.

Phase 7's gates already refuse policy comparisons on a thin sample; the expiry study
needs three more, because it can fail in ways Phase 7 cannot see: no expiry-day
trades at all, no recognisable contract expiry, or a book too sparsely recorded to
price an exit. As in Phase 7, a failing gate names what it forbids, in the report
itself rather than in a footnote.
"""
from __future__ import annotations

from app.research.phase7.gates import (
    MIN_BUYS,
    MIN_BUYS_PER_CELL,
    MIN_REAL_SESSIONS,
)

MIN_EXPIRY_SESSIONS = 4        # instrument-sessions classified EXPIRY_DAY
MIN_EXPIRY_TRADES = 30
MIN_SPREAD_COVERAGE_PCT = 90.0
MIN_EXPIRY_COVERAGE_PCT = 90.0


def evaluate(*, resolved: int, sessions: int, expiry_sessions: int,
             expiry_trades: int, spread_coverage_pct: float,
             expiry_coverage_pct: float, smallest_cell: int) -> dict:
    gates = [
        {"gate": "REAL_SESSIONS",
         "need": f">= {MIN_REAL_SESSIONS} recorded sessions",
         "have": sessions, "pass": sessions >= MIN_REAL_SESSIONS,
         "forbids": "any statement that one exit policy is better than another"},
        {"gate": "RESOLVED_TRADES",
         "need": f">= {MIN_BUYS} resolved trades",
         "have": resolved, "pass": resolved >= MIN_BUYS,
         "forbids": "expectancy and profit-factor comparisons"},
        {"gate": "ADEQUATE_EXPIRY_SESSIONS",
         "need": f">= {MIN_EXPIRY_SESSIONS} instrument-sessions on expiry day",
         "have": expiry_sessions, "pass": expiry_sessions >= MIN_EXPIRY_SESSIONS,
         "forbids": "any claim that expiry day behaves differently — one expiry "
                    "session measures that contract's last day, not expiry"},
        {"gate": "EXPIRY_TRADE_COUNT",
         "need": f">= {MIN_EXPIRY_TRADES} resolved trades classified EXPIRY_DAY",
         "have": expiry_trades, "pass": expiry_trades >= MIN_EXPIRY_TRADES,
         "forbids": "expiry-specific exit policy comparison"},
        {"gate": "EXPIRY_CLASSIFIED",
         "need": f">= {MIN_EXPIRY_COVERAGE_PCT}% of trades with a parsed contract "
                 f"expiry",
         "have": round(expiry_coverage_pct, 1),
         "pass": expiry_coverage_pct >= MIN_EXPIRY_COVERAGE_PCT,
         "forbids": "treating the expiry split as complete"},
        {"gate": "SPREAD_COVERAGE",
         "need": f">= {MIN_SPREAD_COVERAGE_PCT}% of trades priced through a real "
                 f"bid/ask at both ends",
         "have": round(spread_coverage_pct, 1),
         "pass": spread_coverage_pct >= MIN_SPREAD_COVERAGE_PCT,
         "forbids": "net-of-cost conclusions on the unpriced trades"},
        {"gate": "CELL_SIZE",
         "need": f">= {MIN_BUYS_PER_CELL} trades in every reported cell",
         "have": smallest_cell, "pass": smallest_cell >= MIN_BUYS_PER_CELL,
         "forbids": "expiry / regime / instrument / entry-quality breakdowns"},
    ]
    failed = [g["gate"] for g in gates if not g["pass"]]
    return {
        "gates": gates,
        "failed": failed,
        "status": "EXPLORATORY_ONLY" if failed else "COMPARISON_PERMITTED",
        "verdict": "descriptive statistics are still produced for every failing "
                   "gate; what a failing gate removes is the right to say a policy "
                   "is better",
    }
