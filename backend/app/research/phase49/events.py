"""Phase 49 — grouping observations into events. Pure, deterministic, no I/O.

No store, no clock, no connection: the same rows in, the same events out, on
any machine and at any later date. That is the property the phase is for — an
event count that could change between two readings of the same journal would be
no better than the leg count it replaces.

The one thing worth stating plainly about the rule: it is a **reporting-time
reduction**, not a decision-time label. Whether an observation extends the
previous event or starts a new one depends on the gap to the previous
observation, so it is settled once the rows exist, not at the instant itself.
Every field it reads is a decision-instant field — session, arm, instrument,
vehicle, contract, expiry, strike, direction and the decision timestamp — and no
mark, excursion, cost, exit or profit-and-loss is consulted, so the grouping of
a session is fixed when its rows are journalled and cannot move when the market
does.
"""
from __future__ import annotations

import hashlib

from app.research.phase49 import (
    EVENT_GAP_SEC,
    EVENT_KEY,
    GROUPING_RULE,
    NOT_A_RESULT,
    RULE_TEXT,
    rule_fingerprint,
)

# Fields copied onto the event from its first observation. Anything not in the
# key is descriptive only and is never used to decide membership.
_CARRIED: tuple[str, ...] = (
    "session", "arm", "instrument", "vehicle", "contract", "expiry", "strike",
    "direction", "overlay_state", "research_candidate",
)

# Named so a caller cannot mistake a leg total for an opportunity total.
LEG_COUNT = "leg_count"
EVENT_COUNT = "event_count"


def key(row: dict) -> tuple[str, ...]:
    """The grouping key of one observation: decision-instant fields only."""
    return tuple(_text(row.get(field)) for field in EVENT_KEY)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def event_id(key_parts: tuple[str, ...], first_ts: float | None) -> str:
    """A deterministic id for one event.

    Derived from the rule fingerprint, the key and the event's **first**
    decision timestamp — first rather than last, so an id is fixed when the
    event opens and does not change as further observations join it. An id
    that moved would make two reports of the same session impossible to
    reconcile row by row.
    """
    stamp = "" if first_ts is None else f"{first_ts:.3f}"
    raw = "|".join([rule_fingerprint(), *key_parts, stamp])
    return "E" + hashlib.sha256(raw.encode()).hexdigest()[:15]


def group(rows: list[dict], *, gap_sec: float = EVENT_GAP_SEC) -> list[dict]:
    """Reduce observations to events, newest event first.

    Observations with no usable decision timestamp are not silently dropped and
    not silently merged: each becomes its own event, because the rule cannot
    say whether it continues anything and guessing would be an invented join.
    """
    buckets: dict[tuple[str, ...], list[dict]] = {}
    for row in rows:
        buckets.setdefault(key(row), []).append(row)
    events: list[dict] = []
    for key_parts, group_rows in buckets.items():
        ordered = sorted(
            group_rows,
            key=lambda r: (_num(r.get("decision_ts")) is None,
                           _num(r.get("decision_ts")) or 0.0,
                           _text(r.get("overlay_id")) or _text(r.get("event_id"))),
        )
        run: list[dict] = []
        previous: float | None = None
        for row in ordered:
            ts = _num(row.get("decision_ts"))
            broken = (
                not run
                or ts is None
                or previous is None
                or (ts - previous) > float(gap_sec)
            )
            if broken and run:
                events.append(_event(key_parts, run))
                run = []
            run.append(row)
            previous = ts
        if run:
            events.append(_event(key_parts, run))
    events.sort(key=lambda e: (e["first_decision_ts"] or 0.0), reverse=True)
    return events


def _event(key_parts: tuple[str, ...], rows: list[dict]) -> dict:
    stamps = [t for t in (_num(r.get("decision_ts")) for r in rows)
              if t is not None]
    first = min(stamps) if stamps else None
    last = max(stamps) if stamps else None
    head = rows[0]
    out = {name: head.get(name) for name in _CARRIED}
    generated = event_id(key_parts, first)
    out.update({
        "event_id": generated,
        # The same id under the name the board and the panel read it by. One
        # value, two names, because "event_id" already means something upstream
        # — a Phase 45 shadow event, one row — and an alias is cheaper than a
        # reader silently comparing the two.
        "event_group_id": generated,
        # Phase 46 journals the candidate as ``research_candidate``; there is no
        # ``candidate_id`` column. Carried under both names rather than
        # inventing an id, so a null here means the journal held none.
        "candidate_id": head.get("research_candidate"),
        "first_decision_ts": first,
        "last_decision_ts": last,
        "span_sec": None if first is None or last is None else round(last - first, 3),
        "observation_count": len(rows),
        # Every leg that was folded in, by all three of the ids the upstream
        # journals use, so an event can always be taken apart again.
        "overlay_ids": [r.get("overlay_id") for r in rows],
        "source_event_ids": [r.get("event_id") for r in rows],
        "obs_ids": [r.get("obs_id") for r in rows],
        "grouping_rule": GROUPING_RULE,
        "rule_fingerprint": rule_fingerprint(),
    })
    return out


def counts(rows: list[dict], *, gap_sec: float = EVENT_GAP_SEC) -> dict:
    """Both counts, together, with the rule that produced the second one.

    The two are returned from one call on purpose: a caller that wants the
    event count has to carry the leg count with it, and cannot quietly report
    the smaller number as though it were the larger one's replacement.
    """
    events = group(rows, gap_sec=gap_sec)
    observed = [e["observation_count"] for e in events]
    return {
        LEG_COUNT: len(rows),
        EVENT_COUNT: len(events),
        "observations_per_event_max": max(observed) if observed else 0,
        "observations_per_event_mean": (
            round(sum(observed) / len(observed), 3) if observed else None
        ),
        "repeated_observation_legs": len(rows) - len(events),
        "grouping_rule": GROUPING_RULE,
        "rule_text": RULE_TEXT,
        "rule_fingerprint": rule_fingerprint(),
        "gap_sec": float(gap_sec),
        "leg_count_is": (
            "ONE_ROW_PER_ADMITTED_OBSERVATION_USE_FOR_EXECUTION_AND_PATH_WORK"
        ),
        "event_count_is": (
            "ONE_ROW_PER_DISTINCT_OPPORTUNITY_USE_FOR_ANY_CLAIM_OF_"
            "INDEPENDENT_SAMPLE"
        ),
        "not_a_result": NOT_A_RESULT,
    }
