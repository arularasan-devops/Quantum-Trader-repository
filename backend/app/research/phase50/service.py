"""Phase 50 — the read layer: events, their session status, their outcomes, the two columns.

Every number here is derived from journals other phases wrote. Phase 46 holds
the observations, Phase 47 selects the two arms (through its own corrected
selection, called rather than copied), Phase 49 groups them under the frozen
300-second rule with its fingerprint untouched, Phase 35 holds the raw quotes and
owns the executable arithmetic. This module adds the clock, the resolution and
the comparison, and journals event summaries beside — never over — the
observations.

Three decisions worth reading before the code.

**When an event is finished is a property of the rule, not of the wall clock.**
Under a 300-second gap an event that has been silent for longer than 300 seconds
can no longer gain an observation: any later quote starts a new event by
definition. So "completed" is ``now - last_observation > 300`` and needs no
assumption about whether the session is over. A younger event is
``UNRESOLVED_THE_EVENT_IS_STILL_ACCRUING_OBSERVATIONS``, which is a statement
about time, not about money.

**Arm B is re-grouped, not added up.** The overlay's events and production's
events overlap, and summing two event lists double-counts every opportunity both
admitted. So arm B is built by pooling the two arms' legs, dropping duplicate
observations by id, and grouping *once* under the same frozen rule. The union is
therefore an honest event count rather than an arithmetic one, and A is a strict
subset of B.

**An unresolved event is not a flat event.** It is excluded from every total and
counted in its own column with its reason. A zero would be an outcome; there
isn't one.
"""
from __future__ import annotations

import bisect
import json
import os
import threading
import time

from app.research.phase17 import quality as p17quality
from app.research.phase35 import store as p35store
from app.research.phase35 import path as p35path
from app.research.phase46 import SUPPORTED
from app.research.phase46 import service as p46service
from app.research.phase47 import ARM_PRODUCTION, ARM_RESEARCH
from app.research.phase47 import mark as p47mark
from app.research.phase47 import service as p47service
from app.research.phase47 import store as p47store
from app.research.phase49 import (
    EVENT_GAP_SEC,
    GROUPING_RULE,
    REASON_ACCRUAL,
    REASON_BOUND,
    REASON_SELECTION,
    REASON_SELECTION_SAME_COUNT,
    RULE_TEXT,
)
from app.research.phase49 import events as p49events
from app.research.phase49 import rule_fingerprint
from app.research.phase49 import service as p49service
from app.research.phase50 import (
    APPEND_ONLY,
    ARM_PRODUCTION_ONLY,
    ARM_PRODUCTION_PLUS_RESEARCH,
    CADENCE_ASYMMETRIC,
    CADENCE_COMPARABLE,
    CADENCE_GUARD_RULE,
    CADENCE_IS_NOT_PERFORMANCE,
    CADENCE_MAX_RATIO,
    CADENCE_NOT_MEASURABLE,
    CADENCE_RULE,
    CAUSE_OVERLAY_IS_A_RESIDUAL,
    CAUSES,
    CLASSIFICATION,
    COST_BAND_BASIS,
    COST_BAND_IS_EX_ANTE,
    COST_BAND_UNMEASURED,
    COVERAGE_ASYMMETRIC,
    COVERAGE_COMPARABLE,
    COVERAGE_ENDPOINT,
    COVERAGE_GUARD_RULE,
    COVERAGE_HORIZONS_SEC,
    COVERAGE_MAX_RATE_GAP_PP,
    COVERAGE_MAX_RATE_RATIO,
    COVERAGE_NOT_AN_EXIT,
    COVERAGE_NOT_MEASURABLE,
    DEGENERATE,
    DELTA_WITHHELD,
    DELTA_WITHHELD_CADENCE,
    DELTA_WITHHELD_TOO_FEW,
    DESIGN_NOT_IMPLEMENTED,
    DIAGNOSTIC_HAS_NO_OUTCOME,
    DIAGNOSTIC_IS_READ_ONLY,
    DIRECTIONALLY_UNHELPFUL,
    DIRECTIONALLY_USEFUL,
    EARLY_SHADOW_RESULT,
    EQUAL_OBSERVATION_DESIGN,
    EXHAUSTIVE,
    FILTER_GROUPS,
    FROM_COVERAGE,
    FROM_NOTHING,
    FROM_OBSERVATIONS,
    FROM_QUOTE_STORE,
    FROM_REGISTRY,
    GROUP_DECLINED,
    GROUP_SELECTED,
    GROUP_UNMEASURED,
    HOLIDAY_FILE,
    MARKET_SESSION_STATUS,
    MIN_RESOLVED_EVENTS,
    NO_HINDSIGHT,
    NO_HOLIDAY_LIST,
    NO_MIDPOINT,
    NO_ORDER_PATH,
    NOT_A_PROMOTION,
    NOT_SUPERSEDED,
    OBSERVATION_DIAGNOSTIC,
    OPEN,
    OVERLAP,
    PAPER_ONLY,
    PARTITION_IS_EXHAUSTIVE,
    PARTITION_RULE,
    PRODUCTION_UNCHANGED,
    RESOLUTION_IS_NOT_PERFORMANCE,
    RESOLVED,
    SAME_EXIT_BOTH_GROUPS,
    SESSION_CLOSE_TOLERANCE_SEC,
    SHADOW_CADENCE_SEC,
    SHADOW_IS_READ_ONLY,
    SHADOW_MAX_GAP_SEC,
    SHADOW_POLICY,
    SHADOW_POOLING_FORBIDDEN,
    SHADOW_WINDOWS_SEC,
    SNAPSHOT_ACCRUAL,
    SNAPSHOT_BOUNDED,
    SNAPSHOT_CURRENT,
    SNAPSHOT_MIS_SELECTED,
    SNAPSHOT_RE_RECORDED,
    SNAPSHOT_STATE_TEXT,
    SNAPSHOT_UNDETERMINED,
    STATE_IS_A_DECLINE,
    STOP_REASON_CAUSE,
    STOP_REASON_EVIDENCE,
    STOP_REASONS,
    TOO_FEW_EVENTS,
    TRUNCATED,
    TRUNCATION_BLOCKS_CONCLUSIONS,
    UNKNOWN,
    UNMEASURED_IS_NOT_A_DECLINE,
    UNRESOLVED_EVENT_OPEN,
    VERSION,
    cadence_guard_fingerprint,
    cost_band,
    cost_bands,
    coverage_guard_fingerprint,
    definition_fingerprint,
    observation_diagnostic_fingerprint,
    observation_policy_fingerprint,
    partition_fingerprint,
    poll_budget_fingerprint,
)
from app.research.phase50 import budget as p50budget
from app.research.phase50 import calendar as p50calendar
from app.research.phase50 import exits as p50exits
from app.research.phase50 import observe as p50observe
from app.research.phase50 import resolve as p50resolve
from app.research.phase50 import store as store_mod
from app.research.phase50 import tiers as p50tiers

_LOCK = threading.Lock()
_CON = None

DEFAULT_LIMIT = 5000


def _connection():
    global _CON
    if _CON is None:
        _CON = store_mod.connect()
    return _CON


def holiday_path() -> str:
    """Where an exchange holiday list is looked for. Optional by design."""
    return os.path.join(store_mod.data_dir(), "phase50", HOLIDAY_FILE)


def holidays() -> frozenset[str]:
    """The exchange holiday list, or empty when none is on disk.

    A list is used when it exists and never invented when it does not: with no
    list, ``HOLIDAY`` is simply never asserted, and a weekday whose book never
    moved reads ``UNKNOWN``. Inferring holidays from missing data would produce
    a calendar built out of feed outages.
    """
    path = holiday_path()
    if not os.path.exists(path):
        return frozenset()
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return frozenset()
    days = raw.get("holidays") if isinstance(raw, dict) else raw
    if not isinstance(days, list):
        return frozenset()
    return frozenset(str(d).strip()[:10] for d in days if str(d).strip())


def _moved(legs: list[dict]) -> bool | None:
    """Whether the book for this event's contract ever changed across observations.

    ``None`` when there is only one observation: a single quote is no evidence
    either way, and a status must not be downgraded by the absence of a second
    sample.
    """
    seen = {
        (leg.get("bid"), leg.get("ask")) for leg in legs
        if leg.get("bid") is not None or leg.get("ask") is not None
    }
    if len(seen) <= 1 and len(legs) <= 1:
        return None
    if not seen:
        return None
    return len(seen) > 1


def _quality(legs: list[dict]) -> str:
    """One data-quality label for an event, or MIXED when its legs disagree."""
    labels = {str(leg.get("data_quality") or "").strip() for leg in legs}
    labels.discard("")
    if not labels:
        return "NOT_RECORDED"
    if len(labels) == 1:
        return labels.pop()
    return "MIXED_" + "_".join(sorted(labels))


def _completed(event: dict, *, now: float, gap_sec: float = EVENT_GAP_SEC) -> bool:
    """True when no later observation can join this event under the frozen rule.

    Derived from the rule rather than from the session clock: after one gap of
    silence a further quote on the same contract starts a new event, so the
    event is closed whatever the exchange is doing.
    """
    last = event.get("last_decision_ts")
    if not isinstance(last, (int, float)) or isinstance(last, bool):
        return True
    return (float(now) - float(last)) > float(gap_sec)


def events(
    *,
    session: str | None = None,
    instrument: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: float | None = None,
    resolve_outcomes: bool = True,
) -> dict:
    """Both arms as events, each with its session status and its outcome.

    Leg counts and event counts are published together for every arm, as they
    have been since Phase 49: an event count is the honest denominator for an
    independent-opportunity claim, and a leg count is what execution and capture
    analysis needs. Neither replaces the other here.
    """
    clock = float(now if now is not None else time.time())
    selected = p47service.arms(
        session=session, instrument=instrument, limit=limit,
    )
    legs = selected["legs"]
    holiday_days = holidays()
    con = _quote_connection()
    try:
        out_arms: dict[str, dict] = {}
        for arm in (ARM_RESEARCH, ARM_PRODUCTION):
            out_arms[arm] = _arm(
                legs[arm], arm=arm, holiday_days=holiday_days, con=con,
                now=clock, resolve_outcomes=resolve_outcomes,
            )
    finally:
        if con is not None:
            con.close()
    return {
        "session": selected["session"],
        "instrument": instrument,
        "arms": out_arms,
        "journalled_rows_in_session": selected["journalled_rows_in_session"],
        "bound": selected["bound"],
        "selection": selected["selection"],
        "definition": selected["definition"],
        "grouping_rule": GROUPING_RULE,
        "grouping_rule_text": RULE_TEXT,
        "rule_fingerprint": rule_fingerprint(),
        "event_gap_sec": EVENT_GAP_SEC,
        "resolution_fingerprint": definition_fingerprint(),
        "market_session_statuses": list(MARKET_SESSION_STATUS),
        "holiday_list_loaded": bool(holiday_days),
        "holiday_note": None if holiday_days else NO_HOLIDAY_LIST,
        "no_midpoint": NO_MIDPOINT,
        "as_of_ts": clock,
        "classification": CLASSIFICATION,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def _quote_connection():
    """A connection to the raw quote store, or None when it does not exist yet."""
    path = p35store.db_path()
    if not os.path.exists(path):
        return None
    return p35store.connect(path)


def _arm(
    legs: list[dict], *, arm: str, holiday_days: frozenset[str], con,
    now: float, resolve_outcomes: bool,
) -> dict:
    """One column: its legs, its events, and each event's status and outcome."""
    grouped = p49events.group(legs)
    by_id = {str(leg.get("overlay_id")): leg for leg in legs}
    rows: list[dict] = []
    for event in grouped:
        members = [
            by_id[str(i)] for i in (event.get("overlay_ids") or [])
            if str(i) in by_id
        ]
        rows.append(_event_row(
            event, members, arm=arm, holiday_days=holiday_days, con=con,
            now=now, resolve_outcomes=resolve_outcomes,
        ))
    return {
        "arm": arm,
        "leg_count": len(legs),
        "event_count": len(rows),
        "counts": p49events.counts(legs),
        "events": rows,
        "session_status_counts": _tally_by(rows, "market_session_status"),
        "resolution_counts": _tally_by(rows, "resolution_status"),
        "metrics": metrics(rows),
    }


def _event_row(
    event: dict, legs: list[dict], *, arm: str, holiday_days: frozenset[str],
    con, now: float, resolve_outcomes: bool,
) -> dict:
    """One event summary: identity, clock, and outcome or the reason there is none."""
    instrument = event.get("instrument")
    moved = _moved(legs)
    per_leg = [
        p50calendar.status(
            leg.get("decision_ts"), instrument, holidays=holiday_days,
            moved=moved,
        )
        for leg in legs
    ] or [p50calendar.status(
        event.get("first_decision_ts"), instrument, holidays=holiday_days,
        moved=moved,
    )]
    session_status = p50calendar.summarise(
        [s["market_session_status"] for s in per_leg]
    )
    head = per_leg[0]
    # Fields the frozen Phase 49 payload does not carry are read from the
    # event's own first observation rather than added to that payload: the
    # grouping contract is fingerprinted, and widening it here would change a
    # definition this phase promised to leave alone.
    lead = legs[0] if legs else {}
    completed = _completed(event, now=now)
    outcome: dict = {}
    if not completed:
        outcome = {**p50resolve.open_row(event),
                   "resolution_status": UNRESOLVED_EVENT_OPEN}
    elif resolve_outcomes:
        outcome = p50resolve.resolve_event(
            event, legs, _forward(event, legs, con=con),
        )
    else:
        outcome = {"resolution_status": UNRESOLVED_EVENT_OPEN,
                   "reason": "outcomes were not requested for this reading"}
    first = event.get("first_decision_ts")
    last = event.get("last_decision_ts")
    return {
        "event_id": event.get("event_id"),
        "event_group_id": event.get("event_group_id"),
        "candidate_id": event.get("candidate_id"),
        "arm": arm,
        "session": event.get("session"),
        "instrument": instrument,
        "vehicle": event.get("vehicle"),
        "vehicle_label": lead.get("vehicle_label"),
        "contract": event.get("contract"),
        "expiry": event.get("expiry"),
        "strike": event.get("strike"),
        "direction": event.get("direction"),
        "overlay_state": event.get("overlay_state"),
        "shadow_action": lead.get("shadow_action"),
        "first_ts": first,
        # The instant the partition was decided at, named as §1 names it: the
        # same number as first_ts, spelled so a reader cannot mistake which
        # observation the cohort was read from.
        "first_decision_ts": first,
        "last_ts": last,
        "duration_seconds": event.get("span_sec"),
        "observation_count": event.get("observation_count"),
        "underlying_leg_ids": event.get("overlay_ids"),
        "source_event_ids": event.get("source_event_ids"),
        "obs_ids": event.get("obs_ids"),
        "market_session_status": session_status,
        "status_evidence": head.get("status_evidence"),
        "segment": head.get("segment"),
        "session_date_ist": head.get("session_date_ist"),
        "book_moved": moved,
        "data_quality": _quality(legs),
        "event_completed": completed,
        "grouping_rule": GROUPING_RULE,
        "rule_fingerprint": event.get("rule_fingerprint"),
        "definition": lead.get("definition"),
        "research_fingerprint": lead.get("shadow_definition")
        or lead.get("definition"),
        "fingerprint": lead.get("shadow_definition") or lead.get("definition"),
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        **partition_of(legs),
        **outcome,
    }


def partition_of(legs: list[dict]) -> dict:
    """Which side of the overlay's filter this event's admitting instant fell on.

    Read from the overlay state the capture recorded at the **first**
    observation. That instant is the one a decision to take or skip the
    opportunity would have been made at, and it is the only instant whose
    information was available then: an event that the overlay declined at entry
    and supported nine minutes later was declined, and grading it as selected
    would be a decision made with the following nine minutes in hand.

    A mixed event — supported at some of its observations and not at others — is
    still assigned by its first, and the supported count travels beside it so the
    mixing is visible in the row rather than resolved silently.

    Only a state the overlay actually reached counts as a decline. ``DISAGREES``,
    ``COST_BLOCKED`` and ``WATCH`` are refusals it made and belong in the declined
    cohort; ``UNMEASURED``, ``STALE``, ``CAPTURE_GAP`` and ``NO_RESEARCH_
    EVIDENCE`` are the overlay having nothing to say, and treating those as
    refusals would hand the filter credit for declining opportunities it never
    saw. On a capture whose every state is ``UNMEASURED`` that would build a
    51-event decline cohort out of a recording gap and then compare it against
    nothing.
    """
    ordered = sorted(legs, key=lambda r: _ts(r.get("decision_ts")) or 0.0)
    first = ordered[0] if ordered else {}
    state = str(first.get("overlay_state") or "").strip()
    supported = sum(
        1 for leg in legs
        if str(leg.get("overlay_state") or "").strip() == SUPPORTED
    )
    if state == SUPPORTED:
        group = GROUP_SELECTED
    elif state in STATE_IS_A_DECLINE:
        group = GROUP_DECLINED
    else:
        group = GROUP_UNMEASURED
    return {
        "overlay_partition": group,
        "overlay_state_at_first_observation": state or None,
        "overlay_partition_reason": (
            state if state else "NO_OVERLAY_STATE_RECORDED"
        ),
        "overlay_state_is_a_decision": (
            state == SUPPORTED or state in STATE_IS_A_DECLINE
        ),
        "overlay_supported_observations": supported,
        "overlay_partition_is_mixed": 0 < supported < len(legs),
        "production_signal": (
            first.get("production_signal_normalised")
            or first.get("production_signal")
        ),
        "partition_rule": PARTITION_RULE,
    }


def _forward(event: dict, legs: list[dict], *, con) -> list[dict]:
    """Every measured book this event could have exited at, from both sources.

    A leg is opened at the first observation and held, so its path runs from
    that instant onward — and the event's **own later observations** are books
    on that path, recorded at decision instants by the capture that priced the
    entry. Reading only the raw quote store reported ``NO_LATER_EXECUTABLE_
    QUOTE`` for events holding hundreds of later two-sided books of their own:
    true of where this function looked, published as a fact about the capture.

    So both sources are read and unioned by timestamp: the event's later
    observations, and the quote store after the last of them for anything the
    board kept quoting once it stopped re-observing it. Neither source is
    inferred — no midpoint, no print, no other session — and an observed book
    is admitted only at the freshness bar that prices an entry fill, because a
    fill priced off a minute-old book is a fabricated fill wherever it is read
    from.
    """
    contract = event.get("contract")
    vehicle = event.get("vehicle")
    first = event.get("first_decision_ts")
    last = event.get("last_decision_ts")
    if not contract or not vehicle:
        return []
    samples: dict[float, dict] = {}
    entry_ts = float(first) if isinstance(first, (int, float)) and not isinstance(
        first, bool) else None
    for leg in legs:
        ts = _ts(leg.get("decision_ts"))
        if ts is None or entry_ts is None or ts <= entry_ts:
            continue
        if str(leg.get("data_quality") or "") not in p17quality.FILLABLE:
            continue
        samples[ts] = {
            "ts": ts,
            "bid": leg.get("bid"),
            "ask": leg.get("ask"),
            "traded": None,
            "evidence": leg.get("evidence"),
            "sample_source": FROM_OBSERVATIONS,
        }
    if con is not None and isinstance(last, (int, float)) and not isinstance(
            last, bool):
        for row in p35path.forward_samples(
            con, symbol=str(contract), vehicle=str(vehicle),
            after_ts=float(last),
        ):
            ts = _ts(row.get("ts"))
            if ts is None or ts in samples:
                continue
            samples[ts] = {**row, "sample_source": FROM_QUOTE_STORE}
    return [samples[ts] for ts in sorted(samples)]


def _ts(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _tally_by(rows: list[dict], field: str) -> dict:
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or UNKNOWN)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def metrics(rows: list[dict]) -> dict:
    """Event-level statistics over resolved events only, with the rest counted.

    Every average here has ``resolved`` as its denominator, never ``events``:
    dividing by the events an outcome could not be measured for would report a
    capture gap as a smaller result. ``net_pct_total`` is a sum of per-event
    percentages on unsized, overlapping legs — not a portfolio return — and is
    labelled that way wherever it is printed.
    """
    resolved = [r for r in rows if r.get("resolution_status") == RESOLVED
                and _num(r.get("net_pct")) is not None]
    ordered = sorted(resolved, key=lambda r: _num(r.get("first_ts")) or 0.0)
    nets = [float(r["net_pct"]) for r in ordered]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    pain = -sum(losses)
    t1 = [bool(r.get("t1_hit")) for r in resolved if r.get("t1_hit") is not None]
    return {
        "events": len(rows),
        "resolved_events": len(resolved),
        "unresolved_events": len(rows) - len(resolved),
        "unresolved_reasons": _tally_by(
            [r for r in rows if r.get("resolution_status") != RESOLVED],
            "resolution_status",
        ),
        "net_pct_total": round(sum(nets), 4) if nets else None,
        "net_pct_mean_per_event": (
            round(sum(nets) / len(nets), 4) if nets else None
        ),
        # Gross beside net so a group can be read as direction and cost
        # separately: two groups with the same net can differ entirely in
        # whether the move was there and the round trip ate it.
        "gross_pct_mean_per_event": _mean(resolved, "gross_pct"),
        "gross_pct_total": (
            round(sum(v for v in (
                _num(r.get("gross_pct")) for r in resolved) if v is not None), 4)
            if resolved else None
        ),
        "profit_factor": round(sum(wins) / pain, 3) if pain > 0 else None,
        "win_rate_pct": round(100.0 * len(wins) / len(nets), 2) if nets else None,
        "winners": len(wins),
        "losers": len(losses),
        "flat": len(nets) - len(wins) - len(losses),
        "max_drawdown_pct": p47mark.drawdown(nets),
        "avg_cost_points": _mean(resolved, "cost_points"),
        "avg_cost_pct_of_entry": _mean(resolved, "cost_pct_of_entry"),
        "mfe_pct_mean": _mean(resolved, "mfe_pct"),
        "mae_pct_mean": _mean(resolved, "mae_pct"),
        "giveback_pct_mean": _mean(resolved, "giveback_pct"),
        "hold_minutes_mean": _mean(resolved, "hold_minutes"),
        "time_to_favorable_sec_mean": _mean(resolved, "time_to_favorable_sec"),
        "time_to_adverse_sec_mean": _mean(resolved, "time_to_adverse_sec"),
        "t1_hit_rate_pct": (
            round(100.0 * sum(1 for h in t1 if h) / len(t1), 2) if t1 else None
        ),
        "observations": sum(int(r.get("observation_count") or 0) for r in rows),
        # Cadence over *all* the rows, not the resolved ones: how often an event
        # was looked at is what decided whether it could be resolved at all, so
        # measuring it on the survivors would ask the question after the
        # selection it is meant to detect. Mean interval per event rather than
        # median-of-intervals, because a flattened policy row carries its span
        # and its count and not its individual instants — the coverage
        # diagnostic measures the finer version.
        "median_inter_observation_sec": _median_of([
            _event_interval(row) for row in rows
        ]),
        "cadence_is": "MEDIAN_ACROSS_EVENTS_OF_SPAN_DIVIDED_BY_INTERVALS",
        "cadence_is_not_performance": CADENCE_IS_NOT_PERFORMANCE,
        "net_is": "SUM_OF_PER_EVENT_NET_PERCENTAGES_UNSIZED_AND_OVERLAPPING",
        "denominator_is": "RESOLVED_EVENTS_ONLY_UNRESOLVED_ARE_NOT_FLAT",
    }


def _mean(rows: list[dict], field: str) -> float | None:
    values = [v for v in (_num(r.get(field)) for r in rows) if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def _event_interval(row: dict) -> float | None:
    """One event's mean seconds between observations, from its span and its count.

    ``None`` on a single observation: an event looked at once has no interval,
    and calling that zero would report the least-observed events as the most
    densely polled ones.
    """
    first, last = _num(row.get("first_ts")), _num(row.get("last_ts"))
    count = int(row.get("observation_count") or 0)
    if first is None or last is None or count < 2:
        return None
    return round((last - first) / float(count - 1), 3)


def _median_of(values: list) -> float | None:
    present = sorted(v for v in (_num(v) for v in values) if v is not None)
    if not present:
        return None
    mid = len(present) // 2
    if len(present) % 2:
        return round(present[mid], 3)
    return round((present[mid - 1] + present[mid]) / 2.0, 3)


def compare(
    *,
    session: str | None = None,
    instrument: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: float | None = None,
) -> dict:
    """Arm A (production) against arm B (production plus the research overlay).

    Both arms are built by grouping legs under the frozen rule, B from the
    union of the two arms' observations with duplicates dropped by id. A is
    therefore a subset of B, the columns overlap, and the difference between
    them is what the overlay added — not one column tested against the other.
    """
    clock = float(now if now is not None else time.time())
    selected = p47service.arms(
        session=session, instrument=instrument, limit=limit,
    )
    production = selected["legs"][ARM_PRODUCTION]
    research = selected["legs"][ARM_RESEARCH]
    pooled: dict[str, dict] = {}
    for leg in [*production, *research]:
        pooled.setdefault(str(leg.get("overlay_id")), leg)
    holiday_days = holidays()
    con = _quote_connection()
    try:
        arm_a = _arm(
            [{**leg, "arm": ARM_PRODUCTION_ONLY} for leg in production],
            arm=ARM_PRODUCTION_ONLY, holiday_days=holiday_days, con=con,
            now=clock, resolve_outcomes=True,
        )
        arm_b = _arm(
            [{**leg, "arm": ARM_PRODUCTION_PLUS_RESEARCH}
             for leg in pooled.values()],
            arm=ARM_PRODUCTION_PLUS_RESEARCH, holiday_days=holiday_days,
            con=con, now=clock, resolve_outcomes=True,
        )
    finally:
        if con is not None:
            con.close()
    # What the overlay added is decided by the observations inside each arm B
    # event, not by comparing event ids or contracts across the arms. Ids
    # cannot be compared: the frozen key includes the arm, so the same
    # opportunity is a different id in A and in B by construction. Contracts
    # cannot either: the production arm may hold a different event on the same
    # contract an hour earlier. An event is the overlay's only when none of the
    # legs it was built from is a production leg.
    production_ids = {str(leg.get("overlay_id")) for leg in production}
    added = [
        row for row in arm_b["events"]
        if not (
            {str(i) for i in (row.get("underlying_leg_ids") or [])}
            & production_ids
        )
    ]
    return {
        "label": EARLY_SHADOW_RESULT,
        "session": selected["session"],
        "instrument": instrument,
        "arms": {
            ARM_PRODUCTION_ONLY: _column(arm_a),
            ARM_PRODUCTION_PLUS_RESEARCH: _column(arm_b),
        },
        "delta": _delta(arm_a["metrics"], arm_b["metrics"]),
        "verdict": _verdict(arm_a["metrics"], arm_b["metrics"]),
        "min_resolved_events": MIN_RESOLVED_EVENTS,
        "events_only_in_b": len(added),
        "events_only_in_b_note": (
            "an arm B event none of whose observations came from the "
            "production arm: the opportunity the overlay admitted and the "
            "production board did not"
        ),
        "resolved_events_only_in_b": sum(
            1 for r in added if r.get("resolution_status") == RESOLVED
        ),
        "contracts_only_in_b": sorted(
            {str(r.get("contract")) for r in added if r.get("contract")}
        ),
        "metrics_only_in_b": metrics(added),
        "breakdowns": {
            ARM_PRODUCTION_ONLY: _breakdowns(arm_a["events"]),
            ARM_PRODUCTION_PLUS_RESEARCH: _breakdowns(arm_b["events"]),
        },
        "bound": selected["bound"],
        "selection": selected["selection"],
        "definition": selected["definition"],
        "rule_fingerprint": rule_fingerprint(),
        "resolution_fingerprint": definition_fingerprint(),
        "overlap": OVERLAP,
        # Kept for the record and no longer readable as a performance
        # comparison: while the overlay is a subset of production the union
        # column is the production column, and every delta below is zero because
        # of set arithmetic rather than because the overlay changed nothing. The
        # filter comparison is where this question is now answered.
        "degenerate_note": DEGENERATE,
        "is_a_performance_comparison": False,
        "answered_instead_by": "filter_compare",
        "no_midpoint": NO_MIDPOINT,
        "as_of_ts": clock,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def _column(arm: dict) -> dict:
    return {
        "arm": arm["arm"],
        "leg_count": arm["leg_count"],
        "event_count": arm["event_count"],
        "metrics": arm["metrics"],
        "session_status_counts": arm["session_status_counts"],
        "resolution_counts": arm["resolution_counts"],
        "events": arm["events"],
    }


def _delta(a: dict, b: dict) -> dict:
    """B minus A on the headline figures, or None where either side is unmeasured."""
    fields = (
        "events", "resolved_events", "unresolved_events", "net_pct_total",
        "net_pct_mean_per_event", "gross_pct_mean_per_event",
        "win_rate_pct", "profit_factor",
        "max_drawdown_pct", "avg_cost_points", "mfe_pct_mean", "mae_pct_mean",
        "giveback_pct_mean", "t1_hit_rate_pct",
    )
    out: dict[str, float | None] = {}
    for field in fields:
        left, right = _num(a.get(field)), _num(b.get(field))
        out[field] = None if left is None or right is None else round(
            right - left, 4)
    return out


def _verdict(a: dict, b: dict) -> dict:
    """Whether the overlay looks directionally useful, or that it cannot be said.

    The bar is on resolved events per arm and is a constant in this package, so
    it cannot be adjusted to the sample it is applied to. Below it the verdict
    is ``TOO_FEW_RESOLVED_EVENTS_TO_SAY_EITHER_WAY`` however the numbers look:
    three events with a positive sum is not a direction.
    """
    resolved_a = int(a.get("resolved_events") or 0)
    resolved_b = int(b.get("resolved_events") or 0)
    mean_a, mean_b = _num(a.get("net_pct_mean_per_event")), _num(
        b.get("net_pct_mean_per_event"))
    if min(resolved_a, resolved_b) < MIN_RESOLVED_EVENTS:
        state = TOO_FEW_EVENTS
    elif mean_a is None or mean_b is None:
        state = TOO_FEW_EVENTS
    elif mean_b > mean_a:
        state = DIRECTIONALLY_USEFUL
    else:
        state = DIRECTIONALLY_UNHELPFUL
    return {
        "state": state,
        "resolved_a": resolved_a,
        "resolved_b": resolved_b,
        "mean_net_pct_a": mean_a,
        "mean_net_pct_b": mean_b,
        "label": EARLY_SHADOW_RESULT,
        "not_a_promotion": NOT_A_PROMOTION,
    }


ALL_PRODUCTION = "PRODUCTION_ALL_EVENTS"


def filter_compare(
    *,
    session: str | None = None,
    instrument: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: float | None = None,
) -> dict:
    """The overlay read as a filter: the production events it selected against the ones it declined.

    The union comparison this replaces could not answer the question. Every one
    of the overlay's legs was already a production leg, so ``A ∪ overlay`` was
    ``A`` on every metric and the difference was zero by construction — a
    property of set arithmetic, not a measurement of the overlay. What a subset
    *can* be asked is whether the events it keeps behave differently from the
    events it throws away.

    So the same production event universe is partitioned once, at each event's
    admitting instant, and both groups are then measured **under the same
    policies with the same fingerprints**. Giving the selected group one exit and
    the declined group another would manufacture whatever difference was wanted,
    which is why the policy set is built once here and applied to every row.

    Declined events are measured, never dropped: the whole claim of a filter is
    about what it refuses, and a report that only prices what it kept cannot
    check that claim.
    """
    clock = float(now if now is not None else time.time())
    selected = p47service.arms(
        session=session, instrument=instrument, limit=limit,
    )
    production = [
        {**leg, "arm": ARM_PRODUCTION_ONLY}
        for leg in selected["legs"][ARM_PRODUCTION]
    ]
    holiday_days = holidays()
    con = _quote_connection()
    try:
        rows = _policy_rows(
            production, holiday_days=holiday_days, con=con, now=clock,
        )
    finally:
        if con is not None:
            con.close()
    groups = {
        group: [row for row in rows if row.get("overlay_partition") == group]
        for group in FILTER_GROUPS
    }
    return {
        "label": EARLY_SHADOW_RESULT,
        "session": selected["session"],
        "instrument": instrument,
        "production_events": len(rows),
        "production_legs": len(production),
        "group_counts": {group: len(members) for group, members in groups.items()},
        "group_leg_counts": {
            group: sum(int(r.get("observation_count") or 0) for r in members)
            for group, members in groups.items()
        },
        "partition_check": _partition_check(rows, groups),
        "unmeasured_reasons": _tally_by(
            groups[GROUP_UNMEASURED], "overlay_partition_reason"),
        "declining_states": list(STATE_IS_A_DECLINE),
        "unmeasured_is_not_a_decline": UNMEASURED_IS_NOT_A_DECLINE,
        "mixed_events": sum(
            1 for row in rows if row.get("overlay_partition_is_mixed")),
        "policies": _by_policy(rows, groups),
        "exit_policies_declared": p50exits.declared(),
        "exit_policy_set_fingerprint": p50exits.set_fingerprint(),
        "primary_policies": list(p50exits.POLICIES),
        "coverage_endpoint": COVERAGE_ENDPOINT,
        "coverage_note": COVERAGE_NOT_AN_EXIT,
        "breakdowns": {
            group: _breakdowns(members) for group, members in groups.items()
        },
        "events": rows,
        "min_resolved_events": MIN_RESOLVED_EVENTS,
        "coverage_guard": {
            "rule": COVERAGE_GUARD_RULE,
            "max_resolution_rate_gap_pp": COVERAGE_MAX_RATE_GAP_PP,
            "max_resolution_rate_ratio": COVERAGE_MAX_RATE_RATIO,
            "pre_registered": (
                "the threshold is a constant in app/research/phase50/__init__.py "
                "and is inside coverage_guard_fingerprint(), so it cannot be "
                "loosened after the cohorts are seen without the fingerprint "
                "moving"
            ),
            "asymmetric_status": COVERAGE_ASYMMETRIC,
            "comparable_status": COVERAGE_COMPARABLE,
            "unmeasured_status": COVERAGE_NOT_MEASURABLE,
            "withheld_note": DELTA_WITHHELD,
            "resolution_is_not_performance": RESOLUTION_IS_NOT_PERFORMANCE,
            "fingerprint": coverage_guard_fingerprint(),
        },
        # Beside the coverage guard and not inside it: the two ask different
        # questions of the same row, and a reader has to be able to see which
        # one refused a subtraction.
        "cadence_guard": {
            "rule": CADENCE_GUARD_RULE,
            "max_ratio": CADENCE_MAX_RATIO,
            "cadence_rule": CADENCE_RULE,
            "asymmetric_status": CADENCE_ASYMMETRIC,
            "comparable_status": CADENCE_COMPARABLE,
            "unmeasured_status": CADENCE_NOT_MEASURABLE,
            "withheld_note": DELTA_WITHHELD_CADENCE,
            "additive_not_a_replacement": (
                "THE_RESOLUTION_RATE_GUARD_IS_UNCHANGED_AND_STILL_BINDING_THIS_"
                "CONDITION_IS_REQUIRED_IN_ADDITION_TO_IT"
            ),
            "cadence_is_not_performance": CADENCE_IS_NOT_PERFORMANCE,
            "fingerprint": cadence_guard_fingerprint(),
        },
        "equal_observation_policy_design": EQUAL_OBSERVATION_DESIGN,
        "design_is_not_implemented": DESIGN_NOT_IMPLEMENTED,
        "strata_fields": ["instrument", "vehicle", "cost_band"],
        "cost_bands_declared": list(cost_bands()),
        "cost_band_basis": COST_BAND_BASIS,
        "cost_band_is_ex_ante": COST_BAND_IS_EX_ANTE,
        "cost_band_unmeasured": COST_BAND_UNMEASURED,
        "partition_rule": PARTITION_RULE,
        "partition_fingerprint": partition_fingerprint(),
        "partition_is_exhaustive": PARTITION_IS_EXHAUSTIVE,
        "same_exit_both_groups": SAME_EXIT_BOTH_GROUPS,
        "no_hindsight": NO_HINDSIGHT,
        "degenerate_note": DEGENERATE,
        "subset_note": (
            "the selected group is a subset of the production events, so it is a "
            "filter of the current signal and not an independent strategy: its "
            "column may not be read as an additive one"
        ),
        "bound": selected["bound"],
        "selection": selected["selection"],
        "definition": selected["definition"],
        "rule_fingerprint": rule_fingerprint(),
        "resolution_fingerprint": definition_fingerprint(),
        "no_midpoint": NO_MIDPOINT,
        "as_of_ts": clock,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def coverage_diagnostic(
    *,
    session: str | None = None,
    instrument: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: float | None = None,
) -> dict:
    """Why the two cohorts were not observed forward at comparable rates. Read-only.

    The guard refuses a delta whenever the cohorts' resolution rates diverge, and
    on the first market-open capture it refused all seven policies: the selected
    events resolved at 60-90% and the declined ones at 3-36%. A refusal is not a
    diagnosis, and accumulating sessions under the same recording accumulates the
    same incomparability. So this reads the recording itself.

    Nothing here is written, no policy is applied, no outcome is consulted and no
    fingerprint moves. What each event contributes is its identity, how long the
    capture kept looking at it, how many later executable books it holds, and the
    recorded fact that says what stopped the looking.
    """
    clock = float(now if now is not None else time.time())
    selected = p47service.arms(
        session=session, instrument=instrument, limit=limit,
    )
    production = [
        {**leg, "arm": ARM_PRODUCTION_ONLY}
        for leg in selected["legs"][ARM_PRODUCTION]
    ]
    holiday_days = holidays()
    con = _quote_connection()
    try:
        records = _observation_records(
            production, holiday_days=holiday_days, con=con, now=clock,
        )
    finally:
        if con is not None:
            con.close()
    groups = {
        group: [row for row in records if row.get("overlay_partition") == group]
        for group in FILTER_GROUPS
    }
    chosen = groups[GROUP_SELECTED]
    declined = groups[GROUP_DECLINED]
    return {
        "label": OBSERVATION_DIAGNOSTIC,
        "session": selected["session"],
        "instrument": instrument,
        "production_events": len(records),
        "production_legs": len(production),
        "read_bound": _read_bound(selected, len(production), limit),
        "group_counts": {g: len(members) for g, members in groups.items()},
        "group_leg_counts": {
            g: sum(int(r.get("observation_count") or 0) for r in members)
            for g, members in groups.items()
        },
        "partition_check": _partition_check(records, groups),
        "taxonomy_check": p50observe.taxonomy_check(records),
        "cohorts": {
            group: p50observe.cohort(members)
            for group, members in groups.items()
        },
        "comparison": p50observe.compare(chosen, declined),
        "attribution": p50observe.attribute(chosen, declined),
        "bias_sources": p50observe.bias_sources(chosen, declined),
        "strata": {
            f"by_{field}": _coverage_strata(groups, field)
            for field in ("instrument", "vehicle", "cost_band", "direction")
        },
        "cadence_guard_fingerprint": cadence_guard_fingerprint(),
        "cadence_rule": CADENCE_RULE,
        "cadence_guard_rule": CADENCE_GUARD_RULE,
        "cadence_is_not_performance": CADENCE_IS_NOT_PERFORMANCE,
        "equal_observation_policy_design": EQUAL_OBSERVATION_DESIGN,
        "design_is_not_implemented": DESIGN_NOT_IMPLEMENTED,
        "stop_reasons_declared": list(STOP_REASONS),
        "stop_reason_evidence": dict(STOP_REASON_EVIDENCE),
        "stop_reason_cause": dict(STOP_REASON_CAUSE),
        "causes_declared": list(CAUSES),
        "overlay_cause_note": CAUSE_OVERLAY_IS_A_RESIDUAL,
        "coverage_horizons_sec": list(COVERAGE_HORIZONS_SEC),
        "session_close_tolerance_sec": SESSION_CLOSE_TOLERANCE_SEC,
        "event_gap_sec": EVENT_GAP_SEC,
        "events": records,
        "diagnostic_is_read_only": DIAGNOSTIC_IS_READ_ONLY,
        "diagnostic_has_no_outcome": DIAGNOSTIC_HAS_NO_OUTCOME,
        "observation_diagnostic_fingerprint": observation_diagnostic_fingerprint(),
        "partition_rule": PARTITION_RULE,
        "partition_fingerprint": partition_fingerprint(),
        "rule_fingerprint": rule_fingerprint(),
        "resolution_fingerprint": definition_fingerprint(),
        "exit_policy_set_fingerprint": p50exits.set_fingerprint(),
        "coverage_guard_fingerprint": coverage_guard_fingerprint(),
        "bound": selected["bound"],
        "selection": selected["selection"],
        "as_of_ts": clock,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def poll_budget(
    *,
    session: str | None = None,
    instrument: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cadence_sec: float = SHADOW_CADENCE_SEC,
    now: float | None = None,
) -> dict:
    """Step 1 and Step 2 in one reading: the real budget, the replay, the fairness.

    ``CURRENT_POLICY`` is counted from the observation journals' own tick instants
    for the session the events belong to. ``PROPOSED_15S_SHADOW_POLICY`` replays
    the same 15-second grid over the same eligible events. Neither polls: the
    replay is arithmetic over timestamps already on disk, which is what makes the
    comparison possible without touching a live cadence.

    The fairness block is the answer to §8, and it is computed from the same
    per-event policy fields the coverage diagnostic now carries — one policy
    implementation, so a cheap-looking budget and an equalised-looking cohort
    cannot come from two different schedules.
    """
    clock = float(now if now is not None else time.time())
    diagnostic = coverage_diagnostic(
        session=session, instrument=instrument, limit=limit, now=clock,
    )
    records = diagnostic["events"]
    resolved_session = str(diagnostic["session"] or "")
    events = [
        {
            "event_id": r.get("event_id"),
            "instrument": r.get("instrument"),
            "vehicle": r.get("vehicle"),
            "cohort": r.get("overlay_partition"),
            "decision_ts": r.get("first_decision_ts"),
            "session_close_ts": r.get("session_close_ts"),
        }
        for r in records
    ]
    scan = p50budget.scan_ticks(resolved_session)
    current = p50budget.measure(
        resolved_session, cadence_sec=cadence_sec, scan=scan)
    prop = p50budget.proposed(
        events, ticks=scan["ticks"], cadence_sec=cadence_sec)
    groups = {
        group: [r for r in records if r.get("overlay_partition") == group]
        for group in FILTER_GROUPS
    }
    return {
        "label": SHADOW_POLICY,
        "session": resolved_session,
        "instrument": instrument,
        "read_bound": diagnostic["read_bound"],
        "production_events": diagnostic["production_events"],
        "group_counts": diagnostic["group_counts"],
        "current_policy": current,
        "proposed_policy": prop,
        "comparison": p50budget.compare(current, prop),
        "fairness": {
            group: p50observe.policy_cohort(members)
            for group, members in groups.items()
        },
        "equalisation": p50observe.equalised(
            groups[GROUP_SELECTED], groups[GROUP_DECLINED],
            cadence_sec=cadence_sec,
        ),
        "within_instrument": {
            inst: {
                group: p50observe.policy_cohort(
                    [r for r in members if r.get("instrument") == inst])
                for group, members in groups.items()
            }
            for inst in sorted({
                str(r.get("instrument")) for r in records
                if r.get("instrument")
            })
        },
        "policy_windows_sec": list(SHADOW_WINDOWS_SEC),
        "policy_max_gap_sec": SHADOW_MAX_GAP_SEC,
        "policy_is_read_only": SHADOW_IS_READ_ONLY,
        "pooling_forbidden": SHADOW_POOLING_FORBIDDEN,
        "observation_policy_fingerprint": observation_policy_fingerprint(),
        "poll_budget_fingerprint": poll_budget_fingerprint(),
        "rule_fingerprint": rule_fingerprint(),
        "resolution_fingerprint": definition_fingerprint(),
        "exit_policy_set_fingerprint": p50exits.set_fingerprint(),
        "partition_fingerprint": partition_fingerprint(),
        "coverage_guard_fingerprint": coverage_guard_fingerprint(),
        "cadence_guard_fingerprint": cadence_guard_fingerprint(),
        "as_of_ts": clock,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def tier_cadence(
    *,
    session: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: float | None = None,
) -> dict:
    """What cadence the research recorder is on, and how much fresh sample exists.

    Three separate things, deliberately not averaged together:

    * the frozen tier policy and its fingerprint;
    * the sessions journalled under each cadence fingerprint. Sessions captured
      under the old 60-second gate are counted and reported, never added to the
      fresh ones — that is the fresh-sample rule in arithmetic rather than in
      prose;
    * when a session is named, its **measured** capture health: attempts, the
      worst single minute, rows, and the stall and no-row lines. Measured, not
      the pre-change estimate, which is the §11 requirement; and the five
      quantities nothing on this path records stay absent.
    """
    clock = float(now if now is not None else time.time())
    policy = p50tiers.policy()
    current = str(policy["tier_fingerprint"])
    conn = store_mod.connect()
    try:
        rows = store_mod.cadence_sessions(conn, limit=max(1, int(limit)))
    finally:
        conn.close()
    by_fingerprint: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("tier_fingerprint") or "")
        block = by_fingerprint.setdefault(key, {
            "tier_fingerprint": key,
            "policy": row.get("policy"),
            "slow_tier_sec": row.get("slow_tier_sec"),
            "is_current_policy": key == current,
            "sessions": [],
        })
        name = str(row.get("session") or "")
        if name and name not in block["sessions"]:
            block["sessions"].append(name)
    for block in by_fingerprint.values():
        block["sessions"].sort()
        block["session_count"] = len(block["sessions"])
    fresh = by_fingerprint.get(current) or {
        "tier_fingerprint": current, "sessions": [], "session_count": 0,
        "is_current_policy": True,
    }
    required = int(policy["sessions_required"])
    captured = int(fresh["session_count"])
    health: dict | None = None
    if session:
        measured = p50budget.measure(session)
        health = {
            "session": session,
            "captured_under_current_policy": session in fresh["sessions"],
            "read_status": measured["read_status"],
            "exhaustive": measured["exhaustive"],
            "total_poll_attempts": measured["total_poll_attempts"],
            "attempts_per_minute": measured["attempts_per_minute"],
            "peak_attempts_in_one_minute": measured["peak_attempts_in_one_minute"],
            "total_observation_rows": measured["total_observation_rows"],
            "median_poll_interval_sec": measured["median_poll_interval_sec"],
            "fast_tier_attempts": measured["fast_tier_attempts"],
            "slow_tier_attempts": measured["slow_tier_attempts"],
            "stall_lines": measured["stall_lines"],
            "tick_ran_no_row_lines": measured["tick_ran_no_row_lines"],
            "attempt_count_is": measured["attempt_count_is"],
            "cpu_seconds": measured["cpu_seconds"],
            "http_request_count": measured["http_request_count"],
            "timeout_count": measured["timeout_count"],
            "failed_request_count": measured["failed_request_count"],
            "skipped_or_duplicate_polls": measured["skipped_or_duplicate_polls"],
        }
    return {
        "label": policy["policy"],
        "tier_policy": policy,
        "fresh_sample": {
            "tier_fingerprint": current,
            "sessions_captured": captured,
            "sessions_required": required,
            "sessions_remaining": max(0, required - captured),
            "checkpoint_reached": captured >= required,
            "sessions": fresh["sessions"],
            "rule": policy["fresh_sample_rule"],
        },
        "by_cadence_fingerprint": by_fingerprint,
        "pre_change_sessions": sum(
            int(b["session_count"]) for key, b in by_fingerprint.items()
            if key != current
        ),
        "capture_health": health,
        "pooling_forbidden": SHADOW_POOLING_FORBIDDEN,
        "observation_policy_fingerprint": observation_policy_fingerprint(),
        "poll_budget_fingerprint": poll_budget_fingerprint(),
        "rule_fingerprint": rule_fingerprint(),
        "resolution_fingerprint": definition_fingerprint(),
        "exit_policy_set_fingerprint": p50exits.set_fingerprint(),
        "partition_fingerprint": partition_fingerprint(),
        "coverage_guard_fingerprint": coverage_guard_fingerprint(),
        "cadence_guard_fingerprint": cadence_guard_fingerprint(),
        "as_of_ts": clock,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def _read_bound(selected: dict, legs_read: int, limit: int) -> dict:
    """Whether this reading saw the whole journal or stopped at its own ceiling.

    Reported before any depth figure is interpreted, because every quantity in
    this diagnostic is a *length of observation* and truncation removes exactly
    the tail it measures. A count taken at the bound is a floor; the previous run
    read 5,000 legs under a 5,000 bound and its 201-vs-4 depth medians could not
    be told from the limit's own shape.
    """
    bound = ((selected.get("bound") or {}).get(ARM_PRODUCTION) or {})
    available = bound.get("available")
    ceiling = int(p47service.MAX_READ)
    requested = max(1, int(limit))
    effective = min(requested, ceiling)
    reached = bool(bound.get("truncated")) or legs_read >= effective
    return {
        "requested_limit": requested,
        "effective_limit": effective,
        "read_ceiling": ceiling,
        "legs_read": int(legs_read),
        "legs_available_in_session": available,
        "limit_was_reached": reached,
        "exhaustive": not reached,
        "status": TRUNCATED if reached else EXHAUSTIVE,
        "count_is": (
            "A_FLOOR_NOT_THE_SESSIONS_COUNT" if reached
            else "THE_SESSIONS_OWN_COUNT"
        ),
        "conclusion_rule": TRUNCATION_BLOCKS_CONCLUSIONS,
    }


def _coverage_strata(groups: dict, field: str) -> dict:
    """The same coverage comparison inside each stratum of one field.

    Same cut as the performance strata — instrument, vehicle, ex-ante cost band,
    direction — so a coverage finding and a performance row can be read against
    each other without one of them having been sliced differently.
    """
    chosen = groups[GROUP_SELECTED]
    declined = groups[GROUP_DECLINED]
    keys = sorted({
        str(row.get(field) or UNKNOWN) for row in [*chosen, *declined]
    })
    out: dict[str, dict] = {}
    for key in keys:
        left = [r for r in chosen if str(r.get(field) or UNKNOWN) == key]
        right = [r for r in declined if str(r.get(field) or UNKNOWN) == key]
        out[key] = {
            "stratum": key,
            "field": field,
            **p50observe.compare(left, right),
            "attribution": p50observe.attribute(left, right),
        }
    return out


def _observation_records(
    legs: list[dict], *, holiday_days: frozenset[str], con, now: float,
) -> list[dict]:
    """One forward-observation record per production event, from the recording only.

    Two capture-wide timelines are built first — every observation instant, and
    every instant per contract — because the interesting reasons are not
    properties of the event at all. "The recorder went silent" and "this contract
    left the observed set while the recorder carried on" are the same event row
    and different facts about the capture, and neither is visible from inside one
    event.
    """
    all_ts = sorted(
        ts for ts in (_ts(leg.get("decision_ts")) for leg in legs)
        if ts is not None
    )
    per_contract: dict[str, list[float]] = {}
    for leg in legs:
        ts = _ts(leg.get("decision_ts"))
        contract = str(leg.get("contract") or "")
        if ts is None or not contract:
            continue
        per_contract.setdefault(contract, []).append(ts)
    for series in per_contract.values():
        series.sort()
    grouped = p49events.group(legs)
    by_id = {str(leg.get("overlay_id")): leg for leg in legs}
    records: list[dict] = []
    for event in grouped:
        members = [
            by_id[str(i)] for i in (event.get("overlay_ids") or [])
            if str(i) in by_id
        ]
        records.append(_observation_record(
            event, members, holiday_days=holiday_days, con=con, now=now,
            all_ts=all_ts, per_contract=per_contract,
        ))
    return records


def _observation_record(
    event: dict, legs: list[dict], *, holiday_days: frozenset[str], con,
    now: float, all_ts: list[float], per_contract: dict[str, list[float]],
) -> dict:
    """One event's coverage row: identity, observation shape, and what stopped it."""
    ordered = sorted(legs, key=lambda r: _ts(r.get("decision_ts")) or 0.0)
    first = _ts(event.get("first_decision_ts"))
    last = _ts(event.get("last_decision_ts"))
    lead = ordered[0] if ordered else {}
    tail = ordered[-1] if ordered else {}
    stamps = [
        ts for ts in (_ts(leg.get("decision_ts")) for leg in ordered)
        if ts is not None
    ]
    intra = max(
        (b - a for a, b in zip(stamps, stamps[1:])), default=None,
    )
    samples = _forward(event, ordered, con=con)
    kept, _dropped = p50resolve.executable_only(
        samples,
        vehicle=str(event.get("vehicle") or ""),
        direction=str(event.get("direction") or ""),
    )
    kept_ts = sorted(
        ts for ts in (_ts(s.get("ts")) for s in kept) if ts is not None
    )
    quality = str(tail.get("data_quality") or "")
    contract = str(event.get("contract") or "")
    identity = {
        "event_id": event.get("event_id"),
        "candidate_id": event.get("candidate_id"),
        "session": event.get("session"),
        "instrument": event.get("instrument"),
        "vehicle": event.get("vehicle"),
        "contract": event.get("contract"),
        "expiry": event.get("expiry"),
        "strike": event.get("strike"),
        "direction": event.get("direction"),
        "market_session_status": p50calendar.summarise([
            p50calendar.status(
                leg.get("decision_ts"), event.get("instrument"),
                holidays=holiday_days, moved=_moved(ordered),
            )["market_session_status"]
            for leg in ordered
        ]),
        **partition_of(ordered),
        **{k: v for k, v in _ex_ante_cost(ordered).items()
           if k in ("cost_band", "ex_ante_cost_pct_of_entry")},
    }
    return p50observe.record(
        identity,
        first_ts=first,
        last_ts=last,
        observation_count=int(event.get("observation_count") or len(ordered)),
        observation_stamps=stamps,
        executable_stamps=kept_ts,
        max_intra_event_gap_sec=None if intra is None else round(intra, 1),
        last_quality=quality or None,
        last_quality_is_fillable=quality in p17quality.FILLABLE,
        later_books=len(samples),
        later_executable_books=len(kept),
        first_later_executable_ts=kept_ts[0] if kept_ts else None,
        last_later_executable_ts=kept_ts[-1] if kept_ts else None,
        next_observation_ts_any_contract=_next_after(all_ts, last),
        next_observation_ts_same_contract=_next_after(
            per_contract.get(contract) or [], last),
        session_close_ts=p50calendar.close_ts(
            first if first is not None else lead.get("decision_ts"),
            event.get("instrument"),
        ),
        gap_sec=EVENT_GAP_SEC,
    )


def _next_after(stamps: list[float], after: float | None) -> float | None:
    """The first recorded instant strictly after one instant, or None."""
    if after is None:
        return None
    index = bisect.bisect_right(stamps, float(after))
    return stamps[index] if index < len(stamps) else None


def _policy_rows(
    legs: list[dict], *, holiday_days: frozenset[str], con, now: float,
) -> list[dict]:
    """Production events with their partition group and every policy's outcome.

    One grouping pass, one forward-sample assembly per event, and the whole
    declared policy set walked over those same books — so a difference between
    two policies is a difference of rule and never of input.
    """
    grouped = p49events.group(legs)
    by_id = {str(leg.get("overlay_id")): leg for leg in legs}
    rows: list[dict] = []
    for event in grouped:
        members = [
            by_id[str(i)] for i in (event.get("overlay_ids") or [])
            if str(i) in by_id
        ]
        row = _event_row(
            event, members, arm=ARM_PRODUCTION_ONLY, holiday_days=holiday_days,
            con=con, now=now, resolve_outcomes=True,
        )
        row.update(_ex_ante_cost(members))
        if not row.get("event_completed"):
            # An event that can still gain observations has no outcome under any
            # policy: a 15-minute exit measured while minute 12 is still being
            # recorded would be graded on a path that is not finished.
            row["outcomes"] = {
                policy: {
                    "exit_policy_id": policy,
                    "exit_policy_fingerprint": p50exits.fingerprint(policy),
                    "resolution_status": UNRESOLVED_EVENT_OPEN,
                    "reason": "the event is still accruing observations",
                }
                for policy in p50exits.ALL_POLICIES
            }
        else:
            row["outcomes"] = p50resolve.resolve_policies(
                event, members, _forward(event, members, con=con),
            )
        rows.append(row)
    return rows


def _ex_ante_cost(legs: list[dict]) -> dict:
    """The cost band this event's entry falls in, read at the admitting instant.

    Both inputs come off the event's first leg: the executable entry price the
    capture recorded there and the round trip it measured or modelled for it.
    Nothing later than that leg is consulted, which is what keeps the band a
    property of the decision instead of a summary of the outcome — a band cut on
    realised P&L would sort the winners into one stratum and then discover that
    that stratum wins.

    Expressed as a percentage of the premium rather than in points, because 170
    points is most of a NIFTY option and a rounding error on a SILVER contract,
    and a stratum that holds both is not comparable.
    """
    ordered = sorted(legs, key=lambda r: _ts(r.get("decision_ts")) or 0.0)
    first = ordered[0] if ordered else {}
    entry = _num(first.get("entry_price"))
    cost, basis = p50resolve.cost_of(first)
    pct: float | None = None
    if entry is not None and entry > 0 and cost is not None:
        pct = round(100.0 * float(cost) / float(entry), 4)
    return {
        "ex_ante_cost_points": cost,
        "ex_ante_cost_basis": basis,
        "ex_ante_entry_price": entry,
        "ex_ante_cost_pct_of_entry": pct,
        "cost_band": cost_band(pct),
        "cost_band_basis": COST_BAND_BASIS,
        "cost_band_is_ex_ante": COST_BAND_IS_EX_ANTE,
    }


def _partition_check(rows: list[dict], groups: dict) -> dict:
    """That every event is in exactly one group, checked rather than asserted."""
    assigned = sum(len(members) for members in groups.values())
    seen: dict[str, int] = {}
    for row in rows:
        key = str(row.get("event_id"))
        seen[key] = seen.get(key, 0) + 1
    return {
        "events": len(rows),
        "assigned_to_a_group": assigned,
        "unassigned": len(rows) - assigned,
        "in_more_than_one_group": 0,
        "duplicate_event_ids": sum(1 for n in seen.values() if n > 1),
        "exhaustive_and_disjoint": assigned == len(rows) and all(
            n == 1 for n in seen.values()),
        "groups": list(FILTER_GROUPS),
    }


def _flatten(row: dict, policy: str) -> dict:
    """One event as a metrics row under one policy: its identity, that policy's outcome."""
    outcome = (row.get("outcomes") or {}).get(policy) or {}
    hold = _num(outcome.get("hold_seconds"))
    return {
        **{k: v for k, v in row.items() if k != "outcomes"},
        **outcome,
        "hold_minutes": None if hold is None else round(hold / 60.0, 2),
    }


def _by_policy(rows: list[dict], groups: dict) -> dict:
    """Selected against declined under each declared policy, plus the whole column.

    ``ALL_PRODUCTION`` is included for §8 and is explicitly the same events as
    the two groups combined — a reference column, not a third arm.
    """
    out: dict[str, dict] = {}
    for policy in p50exits.ALL_POLICIES:
        columns = {
            group: metrics([_flatten(row, policy) for row in members])
            for group, members in groups.items()
        }
        columns[ALL_PRODUCTION] = metrics(
            [_flatten(row, policy) for row in rows])
        chosen = columns[GROUP_SELECTED]
        declined = columns[GROUP_DECLINED]
        compared = _compare(chosen, declined)
        out[policy] = {
            "policy_id": policy,
            "rule": p50exits.RULES.get(policy),
            "params": dict(p50exits.PARAMS.get(policy) or {}),
            "fingerprint": p50exits.fingerprint(policy),
            "is_coverage_diagnostic": policy == COVERAGE_ENDPOINT,
            "columns": columns,
            "coverage": compared["coverage"],
            "cadence": compared["cadence"],
            "selected_minus_declined": compared["selected_minus_declined"],
            "delta_withheld_because": compared["delta_withheld_because"],
            "verdict": compared["verdict"],
            "strata": {
                "by_instrument": _strata(groups, policy, "instrument"),
                "by_vehicle": _strata(groups, policy, "vehicle"),
                "by_cost_band": _strata(groups, policy, "cost_band"),
            },
            "measurable": bool(
                chosen.get("resolved_events") or declined.get("resolved_events")),
            "same_exit_both_groups": SAME_EXIT_BOTH_GROUPS,
        }
    return out


def _coverage_column(column: dict) -> dict:
    """One cohort's forward-resolution coverage: how often a later book existed.

    Deliberately no P&L in this block. How many events could be priced is a
    property of the recording, and the moment it sits in the same row as a net
    figure somebody reads a wider capture as a better filter.
    """
    eligible = int(column.get("events") or 0)
    resolved = int(column.get("resolved_events") or 0)
    unresolved = int(column.get("unresolved_events") or 0)
    rate = None if eligible <= 0 else round(100.0 * resolved / eligible, 2)
    return {
        "eligible_events": eligible,
        "resolved_events": resolved,
        "unresolved_events": unresolved,
        "resolution_rate_pct": rate,
    }


def _coverage(chosen: dict, declined: dict) -> dict:
    """Whether the two cohorts were resolved at comparable rates.

    Both pre-registered conditions must hold — the gap in percentage points and
    the ratio — because either alone lets a case through: a gap alone passes 4%
    against 12%, and a ratio alone passes 40% against 60%.
    """
    left, right = _coverage_column(chosen), _coverage_column(declined)
    rate_a, rate_b = left["resolution_rate_pct"], right["resolution_rate_pct"]
    gap: float | None = None
    ratio: float | None = None
    if rate_a is None or rate_b is None or min(
            left["resolved_events"], right["resolved_events"]) <= 0:
        status = COVERAGE_NOT_MEASURABLE
    else:
        gap = round(abs(rate_a - rate_b), 2)
        high, low = max(rate_a, rate_b), min(rate_a, rate_b)
        ratio = None if low <= 0 else round(high / low, 3)
        comparable = gap <= COVERAGE_MAX_RATE_GAP_PP and (
            ratio is not None and ratio <= COVERAGE_MAX_RATE_RATIO)
        status = COVERAGE_COMPARABLE if comparable else COVERAGE_ASYMMETRIC
    return {
        "status": status,
        "comparable": status == COVERAGE_COMPARABLE,
        GROUP_SELECTED: left,
        GROUP_DECLINED: right,
        "resolution_rate_gap_pp": gap,
        "resolution_rate_ratio": ratio,
        "max_gap_pp": COVERAGE_MAX_RATE_GAP_PP,
        "max_ratio": COVERAGE_MAX_RATE_RATIO,
        "guard_rule": COVERAGE_GUARD_RULE,
        "guard_fingerprint": coverage_guard_fingerprint(),
        "resolution_is_not_performance": RESOLUTION_IS_NOT_PERFORMANCE,
    }


def _compare(chosen: dict, declined: dict) -> dict:
    """The two cohorts side by side, with the delta published only if it may be.

    The columns are always shown, resolved outcomes and all: withholding the
    numbers would hide the coverage problem rather than report it. What is
    withheld is the *subtraction*, because that single figure is the one that
    gets quoted as an overlay effect.
    """
    coverage = _coverage(chosen, declined)
    verdict = _filter_verdict(chosen, declined, coverage)
    # The third condition, and the one the first two could not see: cohorts
    # resolved at comparable rates from a 3s and a 43s polling schedule did not
    # have comparable opportunity to resolve, and the sparser one resolved
    # whichever of its events happened to be quoted when it was looked at.
    cadence = p50observe.cadence_comparable(
        chosen.get("median_inter_observation_sec"),
        declined.get("median_inter_observation_sec"),
        selected_events=int(chosen.get("events") or 0),
        declined_events=int(declined.get("events") or 0),
    )
    resolved = min(
        int(chosen.get("resolved_events") or 0),
        int(declined.get("resolved_events") or 0),
    )
    enough = resolved >= MIN_RESOLVED_EVENTS
    publish = bool(coverage["comparable"]) and enough and bool(
        cadence["comparable"])
    delta = _delta(declined, chosen) if publish else None
    if delta is not None:
        reason = None
    elif coverage["status"] == COVERAGE_ASYMMETRIC:
        reason = DELTA_WITHHELD
    elif cadence["status"] == CADENCE_ASYMMETRIC:
        reason = DELTA_WITHHELD_CADENCE
    elif not enough and coverage["status"] == COVERAGE_COMPARABLE:
        reason = DELTA_WITHHELD_TOO_FEW
    elif not cadence["comparable"] and coverage["status"] == COVERAGE_COMPARABLE:
        reason = cadence["status"]
    else:
        reason = coverage["status"]
    return {
        "columns": {GROUP_SELECTED: chosen, GROUP_DECLINED: declined},
        "coverage": coverage,
        "cadence": cadence,
        "selected_minus_declined": delta,
        "delta_withheld_because": reason,
        "min_resolved_events_either_cohort": resolved,
        "resolved_event_floor": MIN_RESOLVED_EVENTS,
        "verdict": verdict,
    }


def _stratify(members: list[dict], policy: str, field: str) -> dict:
    """Flattened rows for one policy, keyed by the stratum they belong to."""
    out: dict[str, list[dict]] = {}
    for row in members:
        out.setdefault(str(row.get(field) or "NOT_RECORDED"), []).append(
            _flatten(row, policy))
    return out


def _strata(groups: dict, policy: str, field: str) -> dict:
    """Selected against declined inside each stratum, under one policy.

    A stratum where only one cohort appears is reported with its own numbers and
    no comparison: the overlay declining every SILVER event it saw is a fact
    about the overlay, and inventing a counterpart for it would not be.
    """
    chosen = _stratify(groups[GROUP_SELECTED], policy, field)
    declined = _stratify(groups[GROUP_DECLINED], policy, field)
    out: dict[str, dict] = {}
    for key in sorted(set(chosen) | set(declined)):
        left, right = chosen.get(key, []), declined.get(key, [])
        block = _compare(metrics(left), metrics(right))
        block["stratum"] = key
        block["leg_counts"] = {
            GROUP_SELECTED: sum(
                int(r.get("observation_count") or 0) for r in left),
            GROUP_DECLINED: sum(
                int(r.get("observation_count") or 0) for r in right),
        }
        block["both_cohorts_present"] = bool(left) and bool(right)
        out[key] = block
    return out


def _filter_verdict(chosen: dict, declined: dict, coverage: dict) -> dict:
    """Whether the overlay kept the better events, or that it cannot yet be said.

    The bar applies to **both** groups: a filter judged on twenty events it kept
    and three it declined has not been tested against the thing it refuses.
    """
    resolved_selected = int(chosen.get("resolved_events") or 0)
    resolved_declined = int(declined.get("resolved_events") or 0)
    mean_selected = _num(chosen.get("net_pct_mean_per_event"))
    mean_declined = _num(declined.get("net_pct_mean_per_event"))
    status = str(coverage.get("status") or COVERAGE_NOT_MEASURABLE)
    if status == COVERAGE_ASYMMETRIC:
        # Ahead of the event floor deliberately. A row can clear twenty resolved
        # events in each cohort and still be two different universes, and that
        # row is the dangerous one: it looks like the comparison the design asked
        # for.
        state = COVERAGE_ASYMMETRIC
    elif min(resolved_selected, resolved_declined) < MIN_RESOLVED_EVENTS:
        state = TOO_FEW_EVENTS
    elif mean_selected is None or mean_declined is None:
        state = TOO_FEW_EVENTS
    elif mean_selected > mean_declined:
        state = DIRECTIONALLY_USEFUL
    else:
        state = DIRECTIONALLY_UNHELPFUL
    return {
        "state": state,
        "coverage_status": status,
        "comparable": bool(coverage.get("comparable")),
        "resolution_rate_pct_selected": (
            coverage.get(GROUP_SELECTED) or {}).get("resolution_rate_pct"),
        "resolution_rate_pct_declined": (
            coverage.get(GROUP_DECLINED) or {}).get("resolution_rate_pct"),
        "resolution_is_not_performance": RESOLUTION_IS_NOT_PERFORMANCE,
        "resolved_selected": resolved_selected,
        "resolved_declined": resolved_declined,
        "mean_net_pct_selected": mean_selected,
        "mean_net_pct_declined": mean_declined,
        "question": (
            "at the same exit policy, does the overlay select events with "
            "better net economics than the events it declines?"
        ),
        "label": EARLY_SHADOW_RESULT,
        "not_a_promotion": NOT_A_PROMOTION,
    }


def _breakdowns(rows: list[dict]) -> dict:
    """The same metrics per vehicle, instrument, direction and research fingerprint."""
    return {
        "by_vehicle": _split(rows, "vehicle"),
        "by_instrument": _split(rows, "instrument"),
        "by_direction": _split(rows, "direction"),
        "by_research_fingerprint": _split(rows, "research_fingerprint"),
        "by_candidate": _split(rows, "candidate_id"),
        "by_market_session_status": _split(rows, "market_session_status"),
        "by_cost_band": _split(rows, "cost_band"),
    }


def _split(rows: list[dict], field: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row.get(field) or "NOT_RECORDED")
        groups.setdefault(key, []).append(row)
    return {
        key: metrics(group)
        for key, group in sorted(groups.items(), key=lambda kv: kv[0])
    }


def record(
    *,
    session: str | None = None,
    instrument: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: float | None = None,
) -> dict:
    """Journal event summaries, per-policy outcomes and one tally reading per arm. Append-only.

    Three writes, all additive. Event resolutions append one row per event per
    outcome, so an event that could not be priced this morning and can be priced
    tonight keeps both readings. Policy outcomes append one row per event per
    frozen policy, each carrying the partition group and the policy fingerprint
    it was measured under — a filter result whose exit rule is not on the row is
    not re-checkable. Tally readings append what the journal held at the instant
    each arm was read — the field whose absence made an earlier honest count look
    like a mis-selected one.
    """
    clock = float(now if now is not None else time.time())
    payload = events(
        session=session, instrument=instrument, limit=limit, now=clock,
    )
    target = payload.get("session")
    if not target:
        return {"session": None, "written": 0, "duplicate": 0, "readings": 0,
                "note": "NO_SESSION_IN_THE_CALL_JOURNAL", **_chrome()}
    rows: list[dict] = []
    for arm, column in payload["arms"].items():
        for event in column["events"]:
            rows.append(_event_record(event, arm=arm, session=str(target),
                                     clock=clock))
    # The tally is recorded through Phase 47's own recorder and the reading is
    # attached to the snapshot ids it returns. Recomputing those ids here would
    # mean guessing the marked-through instant they are built from, and a
    # coverage row joined to the wrong tally is worse than no coverage row.
    tally = p47service.record(
        session=str(target), instrument=instrument, limit=limit, now=clock,
    )
    readings_written = _record_readings(
        payload, snapshot_ids=tally.get("snapshot_ids") or {}, clock=clock,
    )
    filtered = filter_compare(
        session=str(target), instrument=instrument, limit=limit, now=clock,
    )
    policy_rows = [
        _policy_record(event, outcome, session=str(target), clock=clock)
        for event in filtered["events"]
        for outcome in (event.get("outcomes") or {}).values()
    ]
    with _LOCK:
        con = _connection()
        written = store_mod.insert_events(con, rows)
        policies = store_mod.insert_policy_outcomes(con, policy_rows)
    return {
        "session": target,
        "written": written["written"],
        "duplicate": written["duplicate"],
        "events": len(rows),
        "policy_outcomes_written": policies["written"],
        "policy_outcomes_duplicate": policies["duplicate"],
        "policy_outcomes": len(policy_rows),
        "exit_policy_set_fingerprint": p50exits.set_fingerprint(),
        "partition_counts": filtered["group_counts"],
        "readings": readings_written,
        "tally": {"written": tally.get("written"),
                  "duplicate": tally.get("duplicate"),
                  "snapshot_ids": tally.get("snapshot_ids")},
        "leg_counts": {arm: col["leg_count"]
                       for arm, col in payload["arms"].items()},
        "event_counts": {arm: col["event_count"]
                         for arm, col in payload["arms"].items()},
        **_chrome(),
    }


def _policy_record(
    event: dict, outcome: dict, *, session: str, clock: float,
) -> dict:
    """One journal row: this event, under this frozen policy, in its group."""
    policy = str(outcome.get("exit_policy_id"))
    finger = str(outcome.get("exit_policy_fingerprint")
                 or p50exits.fingerprint(policy))
    return {
        "outcome_id": store_mod.outcome_id(
            str(event.get("event_id")), finger,
            str(outcome.get("resolution_status")), outcome.get("exit_ts"),
        ),
        "event_id": event.get("event_id"),
        "session": session,
        "arm": event.get("arm"),
        "overlay_partition": event.get("overlay_partition"),
        "overlay_state_at_first_observation": event.get(
            "overlay_state_at_first_observation"),
        "production_signal": event.get("production_signal"),
        "partition_rule": PARTITION_RULE,
        "exit_policy_id": policy,
        "exit_policy_fingerprint": finger,
        "exit_policy_set_fingerprint": p50exits.set_fingerprint(),
        "is_coverage_diagnostic": policy == COVERAGE_ENDPOINT,
        "candidate_id": event.get("candidate_id"),
        "instrument": event.get("instrument"),
        "vehicle": event.get("vehicle"),
        "contract": event.get("contract"),
        "expiry": event.get("expiry"),
        "strike": _num(event.get("strike")),
        "direction": event.get("direction"),
        "first_ts": _num(event.get("first_ts")),
        "market_session_status": event.get("market_session_status") or UNKNOWN,
        "observation_count": int(event.get("observation_count") or 0),
        "resolution_status": outcome.get("resolution_status"),
        "resolution_reason": outcome.get("reason"),
        "entry_side": outcome.get("entry_side"),
        "entry_price": _num(outcome.get("entry_price")),
        "entry_ts": _num(outcome.get("entry_ts")),
        "exit_side": outcome.get("exit_side"),
        "exit_price": _num(outcome.get("exit_price")),
        "exit_ts": _num(outcome.get("exit_ts")),
        "exit_reason": outcome.get("exit_reason"),
        "exit_sample_source": outcome.get("exit_sample_source"),
        "hold_seconds": _num(outcome.get("hold_seconds")),
        "gross_pct": _num(outcome.get("gross_pct")),
        "cost_points": _num(outcome.get("cost_points")),
        "cost_basis": outcome.get("cost_basis"),
        "net_pct": _num(outcome.get("net_pct")),
        "net_points": _num(outcome.get("net_points")),
        "mfe_pct": _num(outcome.get("mfe_pct")),
        "mae_pct": _num(outcome.get("mae_pct")),
        "t1_hit": outcome.get("t1_hit"),
        "t2_hit": outcome.get("t2_hit"),
        "t3_hit": outcome.get("t3_hit"),
        "giveback_pct": _num(outcome.get("giveback_pct")),
        "forward_samples": outcome.get("forward_samples"),
        "forward_samples_dropped": outcome.get("forward_samples_dropped"),
        "read_ts": clock,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "version": VERSION,
    }


def _event_record(event: dict, *, arm: str, session: str, clock: float) -> dict:
    return {
        "resolution_id": store_mod.resolution_id(
            str(event.get("event_id")), str(event.get("resolution_status")),
            event.get("exit_ts"),
        ),
        "event_id": event.get("event_id"),
        "candidate_id": event.get("candidate_id"),
        "session": event.get("session") or session,
        "arm": arm,
        "definition": str(event.get("definition") or ""),
        "rule_fingerprint": str(event.get("rule_fingerprint")
                                or rule_fingerprint()),
        "resolution_fingerprint": str(event.get("resolution_fingerprint")
                                      or definition_fingerprint()),
        "instrument": event.get("instrument"),
        "vehicle": event.get("vehicle"),
        "contract": event.get("contract"),
        "expiry": event.get("expiry"),
        "strike": event.get("strike"),
        "direction": event.get("direction"),
        "overlay_state": event.get("overlay_state"),
        "first_ts": event.get("first_ts"),
        "last_ts": event.get("last_ts"),
        "duration_seconds": event.get("duration_seconds"),
        "observation_count": int(event.get("observation_count") or 0),
        "market_session_status": str(event.get("market_session_status")
                                    or UNKNOWN),
        "status_evidence": event.get("status_evidence"),
        "data_quality": event.get("data_quality"),
        "resolution_status": str(event.get("resolution_status") or ""),
        "resolution_reason": event.get("reason"),
        "entry_side": event.get("entry_side"),
        "entry_price": event.get("entry_price"),
        "entry_ts": event.get("entry_ts"),
        "exit_side": event.get("exit_side"),
        "exit_price": event.get("exit_price"),
        "exit_ts": event.get("exit_ts"),
        "gross_pct": event.get("gross_pct"),
        "cost_points": event.get("cost_points"),
        "cost_basis": event.get("cost_basis"),
        "charges_points": event.get("charges_points"),
        "spread_points": event.get("spread_points"),
        "net_pct": event.get("net_pct"),
        "net_points": event.get("net_points"),
        "mfe_pct": event.get("mfe_pct"),
        "mae_pct": event.get("mae_pct"),
        "t1_hit": event.get("t1_hit"),
        "t2_hit": event.get("t2_hit"),
        "t3_hit": event.get("t3_hit"),
        "hold_minutes": event.get("hold_minutes"),
        "time_to_favorable_sec": event.get("time_to_favorable_sec"),
        "time_to_adverse_sec": event.get("time_to_adverse_sec"),
        "giveback_pct": event.get("giveback_pct"),
        "pct_of_mfe_given_back": event.get("pct_of_mfe_given_back"),
        "forward_samples": event.get("forward_samples"),
        "forward_samples_dropped": event.get("forward_samples_dropped"),
        "underlying_leg_ids": event.get("underlying_leg_ids"),
        "read_ts": clock,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "version": VERSION,
    }


def _record_readings(
    payload: dict, *, snapshot_ids: dict, clock: float,
) -> int:
    """Append one reading row per arm: what the journal held when it was read.

    This is the field whose absence made the registry call an earlier honest
    count a mis-selected one. It is recorded at the moment of the reading and
    never back-filled: what a journal held last Tuesday cannot be reconstructed
    from what it holds now, which is precisely why the rows already on the
    record stay ``UNDETERMINED``.
    """
    session = str(payload.get("session") or "")
    if not session:
        return 0
    rows: list[dict] = []
    for arm, column in payload["arms"].items():
        arm_bound = payload["bound"].get(arm) or {}
        snapshot = str(snapshot_ids.get(arm) or "")
        if not snapshot:
            continue
        rows.append({
            "reading_id": store_mod.reading_id(snapshot, clock),
            "snapshot_id": snapshot,
            "reading_ts": clock,
            "session": session,
            "arm": arm,
            "definition": str(payload["definition"]),
            "journal_rows_available_at_read": int(
                payload.get("journalled_rows_in_session") or 0),
            "selected_rows_at_read": int(arm_bound.get("selected") or 0),
            "event_count_at_read": int(column.get("event_count") or 0),
            "selection_bound": int(arm_bound.get("limit") or 0),
            "count_is": arm_bound.get("count_is"),
            "selection_fingerprint": str(payload["selection"]),
            "session_status": _reading_status(column),
            "superseded_by": None,
            "supersession_reason": None,
            "tally_state": SNAPSHOT_CURRENT,
            "status": PAPER_ONLY,
            "order_path": NO_ORDER_PATH,
            "version": VERSION,
        })
    with _LOCK:
        written = store_mod.insert_readings(_connection(), rows)
    return int(written["written"])


def _reading_status(column: dict) -> str:
    """The market session status the reading was taken in, from its own events."""
    counts = column.get("session_status_counts") or {}
    if not counts:
        return UNKNOWN
    if len(counts) == 1:
        return next(iter(counts))
    return OPEN if counts.get(OPEN) else UNKNOWN


def readings(*, session: str | None = None, limit: int = 500) -> dict:
    """Every recorded tally with its accrual state, derived and never assumed.

    The state comes from the registry's reason where the registry has one, and
    from recorded coverage where it does not. Where neither exists the row is
    ``UNDETERMINED_NO_COVERAGE_RECORDED``: a row written before coverage was
    stored cannot be sorted into accrual or mis-selection, and choosing one
    would be inventing the evidence that is missing.
    """
    with p47service._LOCK:
        tallies = p47store.snapshots(
            p47service._connection(), session=session, limit=int(limit),
        )
    annotated = p49service.annotate(tallies)
    with _LOCK:
        coverage = store_mod.coverage_by_snapshot(_connection())
        recorded = store_mod.readings(_connection(), session=session,
                                      limit=int(limit))
    by_reason = {
        REASON_ACCRUAL: SNAPSHOT_ACCRUAL,
        REASON_SELECTION: SNAPSHOT_MIS_SELECTED,
        REASON_SELECTION_SAME_COUNT: SNAPSHOT_RE_RECORDED,
        REASON_BOUND: SNAPSHOT_BOUNDED,
    }
    rows: list[dict] = []
    for row in annotated:
        rows.append({**row, **_state_of(row, coverage, by_reason)})
    return {
        "session": session,
        "readings": rows,
        "recorded_readings": recorded,
        "states": {state: SNAPSHOT_STATE_TEXT[state]
                   for state in SNAPSHOT_STATE_TEXT},
        "state_counts": _tally_by(rows, "tally_state"),
        "append_only": APPEND_ONLY,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "version": VERSION,
    }


def _state_of(
    row: dict, coverage: dict[str, dict], by_reason: dict[str, str],
) -> dict:
    """One tally's accrual state, the evidence for it, and its coverage fields."""
    snapshot = str(row.get("snapshot_id"))
    mine = coverage.get(snapshot)
    fields = {
        "reading_ts": (mine or {}).get("reading_ts") or row.get("snapshot_ts"),
        "journal_rows_available_at_read": (
            (mine or {}).get("journal_rows_available_at_read")
        ),
        "selected_rows_at_read": (
            (mine or {}).get("selected_rows_at_read")
            if mine else row.get("calls")
        ),
        "event_count_at_read": (
            (mine or {}).get("event_count_at_read") if mine
            else row.get("events")
        ),
        "selection_fingerprint": (mine or {}).get("selection_fingerprint"),
        "session_status": (mine or {}).get("session_status"),
        "superseded_by": row.get("superseded_by"),
        "supersession_reason": row.get("superseded_reason"),
    }
    if not row.get("superseded_by"):
        return {**fields, "tally_state": SNAPSHOT_CURRENT,
                "tally_state_evidence": NOT_SUPERSEDED,
                "tally_state_text": SNAPSHOT_STATE_TEXT[SNAPSHOT_CURRENT]}
    reason = str(row.get("superseded_reason") or "")
    named = by_reason.get(reason)
    later = coverage.get(str(row.get("superseded_by")))
    mine_held = _num((mine or {}).get("journal_rows_available_at_read"))
    later_held = _num((later or {}).get("journal_rows_available_at_read"))
    grew = (
        mine_held is not None and later_held is not None
        and later_held > mine_held
    )
    # Coverage outranks the reason string, and deliberately. The registry's
    # reason is what was known when the supersession was written; recorded
    # coverage is what the journal actually held at each reading. Where the
    # journal grew between them, part of the difference in the counts is rows
    # that did not exist yet — so the earlier reading is not called wrong on the
    # strength of a number taken later, whatever else was also true of it.
    if grew:
        return {**fields, "tally_state": SNAPSHOT_ACCRUAL,
                "tally_state_evidence": FROM_COVERAGE,
                "registry_reason": reason or None,
                "confounded": bool(named and named != SNAPSHOT_ACCRUAL),
                "confounded_note": (
                    "the registry also names a selection fault for this row, so "
                    "the gap between the two counts is part accrual and part "
                    "selection and neither part is separately measured"
                ) if named and named != SNAPSHOT_ACCRUAL else None,
                "tally_state_text": SNAPSHOT_STATE_TEXT[SNAPSHOT_ACCRUAL]}
    if named:
        return {**fields, "tally_state": named,
                "tally_state_evidence": FROM_REGISTRY,
                "registry_reason": reason,
                "tally_state_text": SNAPSHOT_STATE_TEXT[named]}
    return {**fields, "tally_state": SNAPSHOT_UNDETERMINED,
            "tally_state_evidence": FROM_NOTHING,
            "tally_state_text": SNAPSHOT_STATE_TEXT[SNAPSHOT_UNDETERMINED]}


def dashboard(
    *,
    session: str | None = None,
    instrument: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: float | None = None,
) -> dict:
    """The active-event rows §10 asks for: one line per opportunity, not per quote.

    "Active" is the frozen rule's own definition of unfinished — an event that
    can still gain an observation — with the completed ones listed after it, so
    the board does not empty out the moment a session goes quiet.
    """
    payload = events(
        session=session, instrument=instrument, limit=limit, now=now,
    )
    active: list[dict] = []
    settled: list[dict] = []
    for arm, column in payload["arms"].items():
        for event in column["events"]:
            row = {
                "arm": arm,
                "event_id": event.get("event_id"),
                "instrument": event.get("instrument"),
                "vehicle": event.get("vehicle"),
                "contract": event.get("contract"),
                "direction": event.get("direction"),
                "shadow_state": event.get("overlay_state"),
                "shadow_action": event.get("shadow_action"),
                "first_observation_ts": event.get("first_ts"),
                "latest_observation_ts": event.get("last_ts"),
                "observation_count": event.get("observation_count"),
                "event_duration_sec": event.get("duration_seconds"),
                "market_session_status": event.get("market_session_status"),
                "status_evidence": event.get("status_evidence"),
                "data_quality": event.get("data_quality"),
                "resolution_status": event.get("resolution_status"),
                "net_pct": event.get("net_pct"),
                "mfe_pct": event.get("mfe_pct"),
                "mae_pct": event.get("mae_pct"),
                "observations_are_one_event": (
                    f"{event.get('observation_count')} observations = 1 event"
                ),
                "status": PAPER_ONLY,
            }
            (settled if event.get("event_completed") else active).append(row)

    def key(row: dict) -> float:
        return float(row.get("latest_observation_ts") or 0.0)

    return {
        "session": payload["session"],
        "active_events": sorted(active, key=key, reverse=True),
        "completed_events": sorted(settled, key=key, reverse=True),
        "leg_counts": {arm: col["leg_count"]
                       for arm, col in payload["arms"].items()},
        "event_counts": {arm: col["event_count"]
                         for arm, col in payload["arms"].items()},
        "grouping_rule": GROUPING_RULE,
        "rule_fingerprint": rule_fingerprint(),
        "event_gap_sec": EVENT_GAP_SEC,
        "as_of_ts": payload["as_of_ts"],
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def status() -> dict:
    """What this phase is, what it reads, and what it cannot reach."""
    with _LOCK:
        counts = store_mod.counts(_connection())
    return {
        "phase": "PHASE50",
        "version": VERSION,
        "journal": counts,
        "definition": p46service.definition(),
        "rule_fingerprint": rule_fingerprint(),
        "grouping_rule": GROUPING_RULE,
        "resolution_fingerprint": definition_fingerprint(),
        "tier_policy": p50tiers.policy(),
        "market_session_statuses": list(MARKET_SESSION_STATUS),
        "holiday_list": holiday_path(),
        "holiday_list_loaded": bool(holidays()),
        "no_midpoint": NO_MIDPOINT,
        "append_only": APPEND_ONLY,
        "classification": CLASSIFICATION,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
    }


def _chrome() -> dict:
    return {
        "append_only": APPEND_ONLY,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
