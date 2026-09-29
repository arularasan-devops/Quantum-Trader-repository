"""How close to the intended instant a CAS quote actually was — §5, §24.

Phase 17's tolerances were written for a decision that happens once. CAS is
sampled every few seconds inside a twenty-minute window where the underlying can
travel 2,200 points in five minutes, so a ten-second-old quote is not a small
error here — it can be a different market. The bands are therefore tighter than
Phase 17's, and the fifth state the spec adds, ``NEAR_EXACT``, is what makes the
tighter bands usable rather than merely punitive.

    EXACT       <= 1s     the quote is the instant
    NEAR_EXACT  <= 3s     one sampling interval late; usable, and labelled
    DEGRADED    <= 15s    keep for description, never for a fill
    STALE       > 15s     recorded, excluded from every measured table
    MISSING     no book at all

The §24 gate is ``EXACT + NEAR_EXACT >= 90%``. Below it the phase reports
``CAS_DATA_INSUFFICIENT`` and refuses to draw a conclusion — the point of the
KPI is that it can fail, and Phase 17's own capture came back at 41.4%.
"""
from __future__ import annotations

EXACT = "EXACT"
NEAR_EXACT = "NEAR_EXACT"
DEGRADED = "DEGRADED"
STALE = "STALE"
MISSING = "MISSING"

STATES: tuple[str, ...] = (EXACT, NEAR_EXACT, DEGRADED, STALE, MISSING)

EXACT_MS = 1_000
NEAR_EXACT_MS = 3_000
DEGRADED_MS = 15_000

# Usable in a descriptive table.
USABLE: frozenset[str] = frozenset({EXACT, NEAR_EXACT, DEGRADED})
# Good enough to price a paper fill against. DEGRADED is not: a fill priced off a
# fifteen-second-old book inside the auction is a fabrication.
FILLABLE: frozenset[str] = frozenset({EXACT, NEAR_EXACT})

MATCH_TARGET_PCT = 90.0
INSUFFICIENT = "CAS_DATA_INSUFFICIENT"
SUFFICIENT = "CAS_DATA_SUFFICIENT"

_ORDER: dict[str, int] = {EXACT: 0, NEAR_EXACT: 1, DEGRADED: 2, STALE: 3, MISSING: 4}


def classify_delay(delay_ms: float | None) -> str:
    if delay_ms is None:
        return MISSING
    d = abs(float(delay_ms))
    if d <= EXACT_MS:
        return EXACT
    if d <= NEAR_EXACT_MS:
        return NEAR_EXACT
    if d <= DEGRADED_MS:
        return DEGRADED
    return STALE


def worst_of(*states: str) -> str:
    return max(states, key=lambda s: _ORDER.get(s, 99)) if states else MISSING


def classify(
    *,
    intent_to_snapshot_ms: float | None,
    quote_age_ms: float | None = None,
    has_book: bool = True,
) -> str:
    """The state for one captured quote.

    ``intent_to_snapshot_ms`` is the gap between the instant we meant to sample
    and the instant the snapshot carries; ``quote_age_ms`` is how old the feed
    says the book itself is. The worse of the two wins, because a fresh call
    against a stale book is a stale observation.
    """
    if not has_book:
        return MISSING
    a = classify_delay(intent_to_snapshot_ms)
    b = classify_delay(quote_age_ms) if quote_age_ms is not None else EXACT
    return worst_of(a, b)


def two_sided(bid: float | None, ask: float | None) -> bool:
    """A real two-sided book: both sides quoted, positive, ask not below bid."""
    if bid is None or ask is None:
        return False
    b, a = float(bid), float(ask)
    return b > 0 and a > 0 and a >= b


def tally(states: list[str]) -> dict[str, int]:
    out = {s: 0 for s in STATES}
    for s in states:
        out[s] = out.get(s, 0) + 1
    return out


def match_rate_pct(states: list[str]) -> float | None:
    """Share of observations that were EXACT or NEAR_EXACT — the §24 KPI."""
    if not states:
        return None
    good = sum(1 for s in states if s in (EXACT, NEAR_EXACT))
    return round(100.0 * good / len(states), 1)


def gate(states: list[str]) -> dict:
    rate = match_rate_pct(states)
    ok = rate is not None and rate >= MATCH_TARGET_PCT
    return {
        "observations": len(states),
        "match_rate_pct": rate,
        "target_pct": MATCH_TARGET_PCT,
        "meets_target": bool(ok),
        "status": SUFFICIENT if ok else INSUFFICIENT,
        "counts": tally(states),
        "note": (
            "Capture quality clears the bar; the measured tables below stand on "
            "their own."
            if ok else
            "Below target. Every economic table in this phase is descriptive "
            "only until capture improves — a conclusion drawn from this sample "
            "would be a conclusion about the feed."
        ),
    }
