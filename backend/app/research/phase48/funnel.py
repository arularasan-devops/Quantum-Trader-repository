"""Pure attribution: one journalled overlay row in, one funnel stage out.

No I/O, no clock, no connection, no threshold. Every stage below is decided
from the state and reason Phase 46 already wrote at the instant, so this module
cannot disagree with the classifier — it can only group it. The reason
vocabulary is imported from Phase 45, where it is defined; restating those
strings here would create a second spelling of the same refusal, and the day
one of them was corrected the funnel would quietly stop counting it.
"""
from __future__ import annotations

from app.research.phase45 import (
    CONTRACT_MISMATCH,
    CROSSED_BOOK,
    MISSING_ASK,
    MISSING_BID,
    NO_DELTA,
    NO_DIRECTION,
    NO_LOT_SIZE,
    NO_QUOTE,
    NO_RANGE,
    ONE_SIDED_BOOK,
)
from app.research.phase46 import (
    CAPTURE_GAP as P46_CAPTURE_GAP,
    COST_BLOCKED,
    DISAGREES,
    NO_RESEARCH_EVIDENCE,
    STALE as P46_STALE,
    SUPPORTED,
    UNMEASURED,
    WATCH,
)
from app.research.phase48 import (
    ADMITTED,
    CAPTURE_GAP,
    COST,
    DEFINITION_STAGES,
    DIRECTION,
    DISTANCE_BINS,
    DOMINANT_UNDECIDED,
    FEED_STAGES,
    INSUFFICIENT,
    MIN_ROWS_FOR_A_SHARE,
    NO_BOOK,
    NO_EVIDENCE,
    NOT_FRESH,
    OBSERVED,
    STAGES,
    STALE,
    UNMEASURED_OTHER,
    WARMING,
)

# An unmeasured instant is one of two different problems wearing one word.
# These reasons mean the book itself was absent or unusable.
_BOOK_REASONS: frozenset[str] = frozenset({
    NO_QUOTE, MISSING_BID, MISSING_ASK, ONE_SIDED_BOOK, CROSSED_BOOK,
    CONTRACT_MISMATCH,
})
# And these mean the book was fine but the definition had nothing to compute
# with yet. The distinction is the whole point of this phase: the first is
# fixed by capture uptime, the second only by session history.
_WARMUP_REASONS: frozenset[str] = frozenset({
    NO_DIRECTION, NO_RANGE, NO_DELTA, NO_LOT_SIZE,
})

_BY_STATE: dict[str, str] = {
    NO_RESEARCH_EVIDENCE: NO_EVIDENCE,
    P46_CAPTURE_GAP: CAPTURE_GAP,
    P46_STALE: STALE,
    DISAGREES: DIRECTION,
    COST_BLOCKED: COST,
    WATCH: NOT_FRESH,
    SUPPORTED: ADMITTED,
}


def stage(row: dict) -> str:
    """The stage one journalled instant reached.

    ``UNMEASURED`` is the only state that splits, and it splits on the reason
    Phase 45 recorded rather than on anything this module decides.
    """
    state = str(row.get("overlay_state") or "")
    if state == UNMEASURED:
        reason = str(row.get("research_reason") or row.get("overlay_reason") or "")
        if reason in _WARMUP_REASONS:
            return WARMING
        if reason in _BOOK_REASONS or not reason:
            return NO_BOOK
        return UNMEASURED_OTHER
    return _BY_STATE.get(state, UNMEASURED_OTHER)


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def distance(row: dict) -> float | None:
    """How much of the gate a cost-blocked instant did reach, as a fraction.

    ``0.4`` means the expected move was four tenths of the round trip the gate
    demands. Reported, never optimised: see ``NOT_A_THRESHOLD_SEARCH``. Only
    the modelled ratio is used, because that is the ratio the gate itself is
    applied to; the measured one is a different quantity and mixing the two is
    the defect that ruined the Phase 42 holdout.
    """
    ratio = _num(row.get("expected_move_over_modelled_cost"))
    gate = _num(row.get("gate_multiple"))
    if ratio is None or gate is None or gate <= 0:
        return None
    return round(ratio / gate, 4)


def _shares(counts: dict[str, int], total: int) -> dict[str, float] | str:
    if total < MIN_ROWS_FOR_A_SHARE:
        return INSUFFICIENT
    return {
        k: round(100.0 * v / total, 2) for k, v in counts.items() if v
    }


def _dominant(counts: dict[str, int], total: int) -> str:
    """The stage that stopped most instants, when one clearly did.

    A near-tie is reported as undecided rather than as a winner by one row:
    "the blocker is X" is a sentence people act on, and it should need a
    margin. Half of everything, and at least a third more than the runner-up.
    """
    blockers = {k: v for k, v in counts.items() if k != ADMITTED and v}
    if not blockers or total < MIN_ROWS_FOR_A_SHARE:
        return DOMINANT_UNDECIDED
    ranked = sorted(blockers.items(), key=lambda kv: -kv[1])
    top, top_n = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0
    if top_n < 0.5 * total or top_n < 1.33 * runner:
        return DOMINANT_UNDECIDED
    return top


def summarise(rows: list[dict]) -> dict:
    """The funnel over one group of journalled instants."""
    counts = dict.fromkeys(STAGES, 0)
    del counts[OBSERVED]
    reasons: dict[str, int] = {}
    distances: list[float] = []
    for row in rows:
        st = stage(row)
        counts[st] = counts.get(st, 0) + 1
        reason = row.get("overlay_reason")
        if isinstance(reason, str) and reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        if st == COST:
            d = distance(row)
            if d is not None:
                distances.append(d)
    total = len(rows)
    feed = sum(v for k, v in counts.items() if k in FEED_STAGES)
    spoke = sum(v for k, v in counts.items() if k in DEFINITION_STAGES)
    return {
        "observed": total,
        "stages": counts,
        "shares_pct": _shares(counts, total),
        "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "feed_could_not_speak": feed,
        "definition_said_no": spoke,
        "admitted": counts[ADMITTED],
        "dominant_blocker": _dominant(counts, total),
        "cost_distance": distance_profile(distances),
    }


def distance_profile(values: list[float]) -> dict:
    """How short the cost-refused instants fell, in coarse bins.

    ``at_or_above`` counts instants that reached each fraction of the gate. It
    is a distance description and nothing else: the counts are cumulative
    hindsight over instants whose forward result is unknown, so a bin that
    "would have admitted more" says nothing about whether those instants would
    have paid. The gate is not moved here and no multiple is proposed.
    """
    if not values:
        return {"n": 0, "median": None, "at_or_above": {}, "max": None}
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = (
        ordered[mid] if len(ordered) % 2
        else round((ordered[mid - 1] + ordered[mid]) / 2.0, 4)
    )
    return {
        "n": len(ordered),
        "median": median,
        "max": ordered[-1],
        "at_or_above": {
            str(b): sum(1 for v in ordered if v >= b) for b in DISTANCE_BINS
        },
    }


def by_key(rows: list[dict], key: str) -> dict[str, dict]:
    """The same funnel, grouped — per instrument, per vehicle, per session.

    Groups are never merged across the key, and a group too small to quote a
    share says so rather than reporting 100% of three rows.
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        value = row.get(key)
        groups.setdefault(str(value) if value is not None else "UNKNOWN", []) \
            .append(row)
    return {
        k: summarise(v)
        for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    }
