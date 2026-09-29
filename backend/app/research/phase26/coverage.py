"""Phase 26 §0 — eligibility, delegated to Phase 25 rather than re-derived.

The eligibility question — does this instrument have enough real two-sided books,
across enough sessions, with enough forward-resolvable quotes — was answered and
corrected once in Phase 25, including the fix that stopped a captured ladder a
few strikes wide from being refused for lacking distinct contracts. Re-deriving
it here would give two studies two different universes and make their tables
uncomparable, so this module calls the Phase 25 implementation and only re-labels
the claim it carries.

Read-only: it opens the store with Phase 25's paged reader, which retries rather
than failing when the live engine holds the write lock, and reports an unreadable
store as **unmeasured** rather than as zero.
"""
from __future__ import annotations

from app.research.phase25 import report as p25report
from app.research.phase26 import VERSION, WINDOW_CLAIM


def coverage(*, db_path: str | None = None) -> dict:
    """Phase 25's coverage, carrying Phase 26's version and window claim."""
    cov = dict(p25report.coverage(db_path=db_path))
    cov["version"] = VERSION
    cov["window_claim"] = WINDOW_CLAIM
    cov["eligibility_source"] = (
        "app.research.phase25.report.coverage — one universe for both studies, so "
        "the two reports can be read side by side"
    )
    return cov
