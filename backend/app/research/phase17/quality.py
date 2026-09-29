"""How good is this observation? — Phase 17 §2/§3/§27.

The previous option study collected 3,448 legs and could use 48 of them. The
reason was never published in a field: rows carried a premium and a spread with
no statement of how far the quote was from the signal, so a book 60 seconds late
was indistinguishable from one taken at the instant of the decision, and the
whole sample had to be thrown away after the fact.

Every quality label here is a measured millisecond distance against a named
tolerance. Two distances matter and they are different questions:

``signal_to_snapshot_ms``
    how far the snapshot is from the moment the decision was made. Captured at
    the tick instant this is single-digit milliseconds, because the chain used is
    the one the engine has just computed on.
``quote_age_ms``
    how old the broker's own quote was when we read it. A snapshot can be taken
    at the exact signal instant and still describe a book that stopped updating
    two minutes ago, which is the failure mode a REST fallback produces.

The label is the WORSE of the two, because an observation is only as good as its
weakest leg, and MISSING beats every other label: no book at all is not a
degraded book.
"""
from __future__ import annotations

EXACT = "EXACT"
GOOD = "GOOD"
DEGRADED = "DEGRADED"
STALE = "STALE"
MISSING = "MISSING"

# Ordered worst-last, so ``worst_of`` is a max over indices.
STATES: tuple[str, ...] = (EXACT, GOOD, DEGRADED, STALE, MISSING)

# Tolerances in milliseconds. These are research seeds, not fitted values, and
# they are stated in the reports beside every rate they produce so a reader can
# recompute a rate under a different tolerance instead of trusting this one.
#
# EXACT is 2s because that is the cadence at which an option quote itself
# refreshes: below it, the "distance" is smaller than the resolution of the thing
# being measured. GOOD is 10s, roughly one entry decision. DEGRADED is 60s, which
# is exactly the median gap of the failed sample — a row at that distance is
# recorded and reported and must never be counted as evidence of a spread.
EXACT_MS = 2_000
GOOD_MS = 10_000
DEGRADED_MS = 60_000

# Quality labels that may be used as evidence of vehicle economics. STALE and
# MISSING rows are stored and counted, never analysed as a spread.
USABLE: frozenset[str] = frozenset({EXACT, GOOD, DEGRADED})
# The stricter set for a costed paper fill: a fill priced off a minute-old book
# is a fabricated fill.
FILLABLE: frozenset[str] = frozenset({EXACT, GOOD})

# Sources, kept as recorded rather than normalised: a push feed and a REST poll
# fail differently and the difference must survive into the report.
WEBSOCKET = "WEBSOCKET"
REST = "REST"
CACHE = "CACHE"
SIMULATED = "SIMULATED"
UNKNOWN_SOURCE = "UNKNOWN"
SOURCES: tuple[str, ...] = (WEBSOCKET, REST, CACHE, SIMULATED, UNKNOWN_SOURCE)

# §27's primary KPI target. A rate below this does not invalidate the capture; it
# invalidates any claim that vehicle economics have been VALIDATED.
EXACT_RATE_TARGET_PCT = 90.0


def classify_delay(ms: float | None) -> str:
    """Quality implied by one millisecond distance.

    ``None`` is MISSING rather than EXACT: an unmeasured distance is the
    condition that produced the unusable sample, so it is never optimistic.
    """
    if ms is None:
        return MISSING
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return MISSING
    if v < 0:
        # A snapshot that claims to precede its own signal is a clock fault, not
        # a fresh quote.
        return MISSING
    if v <= EXACT_MS:
        return EXACT
    if v <= GOOD_MS:
        return GOOD
    if v <= DEGRADED_MS:
        return DEGRADED
    return STALE


def worst_of(*states: str | None) -> str:
    """The weakest of several labels. An unrecognised label counts as MISSING."""
    worst = 0
    for s in states:
        idx = STATES.index(s) if s in STATES else STATES.index(MISSING)
        worst = max(worst, idx)
    return STATES[worst]


def classify(
    *,
    signal_to_snapshot_ms: float | None,
    quote_age_ms: float | None = None,
    has_book: bool = True,
) -> str:
    """Quality of one captured quote.

    ``has_book`` false is MISSING outright — a row with no two-sided quote has no
    spread to be fresh about.
    """
    if not has_book:
        return MISSING
    return worst_of(
        classify_delay(signal_to_snapshot_ms),
        # An unmeasured quote age is not held against the row: some providers do
        # not publish one. It downgrades nothing, and the report says how many
        # rows had no age at all.
        classify_delay(quote_age_ms) if quote_age_ms is not None else EXACT,
    )


def usable(state: str | None) -> bool:
    """May this row be read as evidence of a spread?"""
    return state in USABLE


def fillable(state: str | None) -> bool:
    """May a paper fill be priced off this row?"""
    return state in FILLABLE


def two_sided(bid: float | None, ask: float | None) -> bool:
    """Is this a real two-sided quote?

    Zero, negative and crossed books are rejected. A crossed book is the one
    case where both numbers look present and the spread computed from them is
    negative, which would flatter every cost in the study.
    """
    if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
        return False
    b, a = float(bid), float(ask)
    if b <= 0 or a <= 0:
        return False
    return a >= b


def tally(states: list[str]) -> dict:
    """Counts and the §27 KPI over a list of quality labels."""
    counts = {s: 0 for s in STATES}
    for s in states:
        counts[s if s in counts else MISSING] += 1
    total = len(states)
    exact_pct = round(100.0 * counts[EXACT] / total, 2) if total else None
    usable_pct = (
        round(100.0 * sum(counts[s] for s in USABLE) / total, 2) if total else None
    )
    return {
        "total": total,
        "counts": counts,
        "exact_match_rate_pct": exact_pct,
        "usable_rate_pct": usable_pct,
        "unmatched": counts[MISSING] + counts[STALE],
        "unmatched_pct": (
            round(100.0 * (counts[MISSING] + counts[STALE]) / total, 2)
            if total
            else None
        ),
        "target_pct": EXACT_RATE_TARGET_PCT,
        "meets_target": (
            None if exact_pct is None else exact_pct >= EXACT_RATE_TARGET_PCT
        ),
        "tolerances_ms": {
            "exact": EXACT_MS,
            "good": GOOD_MS,
            "degraded": DEGRADED_MS,
        },
    }
