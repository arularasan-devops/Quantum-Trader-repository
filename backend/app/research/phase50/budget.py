"""Phase 50 Step 1 — the real poll budget, counted rather than estimated.

Read-only. This module opens journals, counts what is in them, and writes
nothing. It changes no scheduler, no interval, no filter, no signal and no order
path; the proposed schedule it reports is a deterministic replay over recorded
timestamps and never a poll.

**Why it exists.** The previous increment costed the proposed 15-second schedule
from two cohort medians — "selected shows 201 observations over 596s, declined 4
over 128s, so the flat schedule is cheaper". Both medians are over events that
were never concurrent, so the arithmetic answers a question about one event and
was read as a question about the feed. It would have supported a cadence either
way, which is the definition of an estimate that cannot fail.

**What one poll attempt is.** One distinct recorded tick instant for one
instrument: ``(instrument, signal_ts)``. Not one observation row — a tick that
held four candidates writes four rows and made one visit, and counting rows would
inflate today's budget by the candidate count and make any proposal look cheap.
Not one liveness line either: that journal is rate-limited to twice a minute per
name, so it is a *floor* on visits and is reported as such beside the count.

**What this cannot measure, and says so.** Nothing on this path journals an HTTP
call, a timeout, a socket failure, a CPU sample or a duplicate poll. Those five
fields are returned as :data:`BUDGET_NOT_RECORDED` rather than modelled from row
counts, because a safety verdict reached on a modelled cost is not a measurement
— and a budget that must not be exceeded is exactly where an invented number
does the most damage.
"""
from __future__ import annotations

import os
import re

from app.research.phase17 import store as p17store
from app.research.phase50 import (
    BUDGET_CURRENT,
    BUDGET_DERIVED,
    BUDGET_MEASURED,
    BUDGET_NOT_RECORDED,
    BUDGET_PROPOSED,
    BUDGET_RULE,
    BUDGET_SAFE,
    BUDGET_SAFE_RULE,
    BUDGET_TIER_FAST,
    BUDGET_TIER_SLOW,
    BUDGET_TIER_UNMEASURED,
    BUDGET_TRUNCATED,
    BUDGET_UNSAFE,
    EXHAUSTIVE,
    SHADOW_CADENCE_SEC,
    SHADOW_INFEASIBLE,
    SHADOW_IS_READ_ONLY,
    SHADOW_MAX_GAP_SEC,
    SHADOW_WINDOWS_SEC,
    poll_budget_fingerprint,
)
from app.research.phase50 import calendar as p50calendar
from app.research.phase50 import observe

# The journals are read with a byte-level regex for the two or three fields the
# count needs. A session of observation rows is ~450 MB of JSON whose rows carry
# an entire option chain each; parsing them would spend minutes to reach a
# timestamp that sits in the first eighty bytes of the line.
TS_RE = re.compile(rb'"signal_ts":\s*([0-9.]+)')
INST_RE = re.compile(rb'"instrument":\s*"([A-Z0-9_&\-]+)"')
VEHICLE_RE = re.compile(rb'"selected_vehicle":\s*"([A-Z_]+)"')
J_TS_RE = re.compile(rb'"ts":\s*([0-9.]+)')
REASON_RE = re.compile(rb'"reason":\s*"([A-Z0-9_]+)"')
EVENT_RE = re.compile(rb'"event":\s*"([A-Z0-9_]+)"')

# A bound, stated the way the coverage reader's is: a reading that reached it
# reports floors and no verdict. 40M lines is several times the largest session
# on disk, so reaching it means something is wrong rather than large.
MAX_LINES = 40_000_000

MINUTE = 60.0


def _files(name: str) -> list[str]:
    """Rolled journals then the live one, oldest first, existing only."""
    return [p for p in p17store.series_paths(name) if os.path.exists(p)]


def scan_ticks(
    session: str,
    *,
    paths: list[str] | None = None,
    max_lines: int = MAX_LINES,
) -> dict:
    """Every recorded tick instant of one session, per instrument.

    Streams the observation journals and keeps, per instrument, the SET of
    distinct ``signal_ts`` values and the row count. The set is the point: the
    tick instant is the unit of feed cost, and rows per instant are a property of
    how many candidates the universe held at that moment.

    Session membership is decided by the row's own timestamp through the exchange
    calendar, never by the file it sits in: the journal rolls on size, so an MCX
    evening and the next morning share a file and a file-level filter would
    charge one session's polls to the other.
    """
    files = _files(p17store.OBSERVATIONS) if paths is None else list(paths)
    ticks: dict[str, set[float]] = {}
    rows: dict[str, int] = {}
    vehicles: dict[str, int] = {}
    lines = 0
    truncated = False
    for path in files:
        with p17store.open_series(path) as fh:
            for raw in fh:
                lines += 1
                if lines > max_lines:
                    truncated = True
                    break
                m = TS_RE.search(raw)
                if not m:
                    continue
                ts = float(m.group(1))
                if p50calendar.session_date(ts) != session:
                    continue
                mi = INST_RE.search(raw)
                if not mi:
                    continue
                inst = mi.group(1).decode()
                ticks.setdefault(inst, set()).add(ts)
                rows[inst] = rows.get(inst, 0) + 1
                mv = VEHICLE_RE.search(raw)
                veh = mv.group(1).decode() if mv else "UNSPECIFIED"
                vehicles[veh] = vehicles.get(veh, 0) + 1
        if truncated:
            break
    return {
        "ticks": {k: sorted(v) for k, v in ticks.items()},
        "rows_by_instrument": rows,
        "rows_by_vehicle": vehicles,
        "lines_read": lines,
        "files_read": len(files),
        "read_status": BUDGET_TRUNCATED if truncated else EXHAUSTIVE,
        "exhaustive": not truncated,
    }


def _count_journal(name: str, session: str, *,
                   by: re.Pattern[bytes] | None = None) -> dict:
    """Lines of one small journal that belong to a session, optionally split.

    ``None`` when the journal does not exist, never zero: a report that shipped
    after the session it is reading has no lines in that session, and "the file
    is not there" must not read as "the capture never did this".
    """
    files = _files(name)
    if not files:
        return {"lines": None, "absent": True}
    total = 0
    split: dict[str, int] = {}
    for path in files:
        with p17store.open_series(path) as fh:
            for raw in fh:
                m = J_TS_RE.search(raw)
                if not m:
                    continue
                if p50calendar.session_date(float(m.group(1))) != session:
                    continue
                total += 1
                if by is not None:
                    mk = by.search(raw)
                    key = mk.group(1).decode() if mk else "UNLABELLED"
                    split[key] = split.get(key, 0) + 1
    out: dict = {"lines": total, "absent": False}
    if by is not None:
        out["by"] = dict(sorted(split.items(), key=lambda kv: -kv[1]))
    return out


def _minute_peak(stamps: list[float]) -> int:
    """The worst single clock minute, which is what a rate limit actually sees."""
    buckets: dict[int, int] = {}
    for ts in stamps:
        key = int(ts // MINUTE)
        buckets[key] = buckets.get(key, 0) + 1
    return max(buckets.values()) if buckets else 0


def _instrument_row(inst: str, stamps: list[float], rows: int,
                    cadence_sec: float) -> dict:
    """One instrument's measured visit rate, and which tier the journal puts it in.

    The tier is read off the measured median interval rather than off the feed
    router's DEEP/BROAD configuration. The configuration says where the capture
    was *meant* to be spent; the journal says where it *was*, and the budget is
    spent by the recorder.
    """
    stats = observe.cadence(stamps)
    median = stats["median_inter_observation_seconds"]
    span = stats["last_observation_ts"] - stats["first_observation_ts"] if len(stamps) > 1 else None
    if median is None:
        tier = BUDGET_TIER_UNMEASURED
    elif median < cadence_sec:
        tier = BUDGET_TIER_FAST
    else:
        tier = BUDGET_TIER_SLOW
    return {
        "instrument": inst,
        "poll_attempts": len(stamps),
        "observation_rows": rows,
        "rows_per_attempt": round(rows / len(stamps), 2) if stamps else None,
        "first_attempt_ts": stats["first_observation_ts"],
        "last_attempt_ts": stats["last_observation_ts"],
        "span_seconds": round(span, 1) if span else None,
        "median_poll_interval_sec": median,
        "mean_poll_interval_sec": (
            round(span / (len(stamps) - 1), 3) if span and len(stamps) > 1 else None
        ),
        "p25_poll_interval_sec": stats["p25_inter_observation_seconds"],
        "p75_poll_interval_sec": stats["p75_inter_observation_seconds"],
        "p95_poll_interval_sec": stats["p95_inter_observation_seconds"],
        "max_poll_interval_sec": stats["max_inter_observation_seconds"],
        "attempts_per_minute": (
            round(len(stamps) / (span / MINUTE), 3) if span and span > 0 else None
        ),
        "peak_attempts_in_one_minute": _minute_peak(stamps),
        "tier": tier,
    }


def measure(
    session: str,
    *,
    cadence_sec: float = SHADOW_CADENCE_SEC,
    scan: dict | None = None,
) -> dict:
    """CURRENT_POLICY: what the recorder actually spent on one completed session.

    Every field is labelled MEASURED, DERIVED or NOT_RECORDED. The three
    liveness journals are counted beside the attempts as corroboration and as
    floors — rate-limited journals cannot be summed into a visit count — and the
    five fields nothing records are named rather than filled.
    """
    scan = scan_ticks(session) if scan is None else scan
    ticks: dict[str, list[float]] = scan["ticks"]
    rows_by_inst: dict[str, int] = scan["rows_by_instrument"]
    per_instrument = [
        _instrument_row(inst, stamps, rows_by_inst.get(inst, 0), cadence_sec)
        for inst, stamps in sorted(ticks.items())
    ]
    all_stamps = sorted(ts for stamps in ticks.values() for ts in stamps)
    attempts = len(all_stamps)
    span = round(all_stamps[-1] - all_stamps[0], 1) if len(all_stamps) > 1 else None
    fast = [r for r in per_instrument if r["tier"] == BUDGET_TIER_FAST]
    slow = [r for r in per_instrument if r["tier"] == BUDGET_TIER_SLOW]
    unmeasured = [r for r in per_instrument if r["tier"] == BUDGET_TIER_UNMEASURED]
    intervals = [
        r["median_poll_interval_sec"] for r in per_instrument
        if r["median_poll_interval_sec"] is not None
    ]
    gaps = _count_journal(p17store.GAPS, session, by=REASON_RE)
    stalls = _count_journal(p17store.STALLS, session, by=EVENT_RE)
    return {
        "policy": BUDGET_CURRENT,
        "session": session,
        "budget_rule": BUDGET_RULE,
        "read_status": scan["read_status"],
        "exhaustive": scan["exhaustive"],
        "lines_read": scan["lines_read"],
        "files_read": scan["files_read"],
        # ---- measured
        "total_poll_attempts": attempts,
        "attempt_count_is": (
            "DISTINCT_RECORDED_TICK_INSTANTS_THAT_PRODUCED_AT_LEAST_ONE_"
            "OBSERVATION_ROW_A_VISIT_THAT_PRODUCED_NO_ROW_IS_JOURNALLED_ONLY_"
            "UNDER_A_PER_MINUTE_LIMITER_SO_THIS_IS_AN_EXACT_COUNT_OF_PRODUCTIVE_"
            "VISITS_AND_A_FLOOR_ON_ALL_VISITS"
        ),
        "total_observation_rows": sum(rows_by_inst.values()),
        "instruments_polled": len(per_instrument),
        "wall_span_seconds": span,
        "first_attempt_ts": all_stamps[0] if all_stamps else None,
        "last_attempt_ts": all_stamps[-1] if all_stamps else None,
        "peak_attempts_in_one_minute": _minute_peak(all_stamps),
        "fast_tier_instruments": len(fast),
        "fast_tier_attempts": sum(r["poll_attempts"] for r in fast),
        "slow_tier_instruments": len(slow),
        "slow_tier_attempts": sum(r["poll_attempts"] for r in slow),
        "unmeasured_tier_instruments": len(unmeasured),
        "unmeasured_tier_attempts": sum(r["poll_attempts"] for r in unmeasured),
        "attempts_by_instrument": {
            r["instrument"]: r["poll_attempts"] for r in per_instrument
        },
        "attempts_by_vehicle_rows": scan["rows_by_vehicle"],
        "per_instrument": per_instrument,
        # ---- derived
        "attempts_per_minute": (
            round(attempts / (span / MINUTE), 3) if span and span > 0 else None
        ),
        "rows_per_attempt": (
            round(sum(rows_by_inst.values()) / attempts, 2) if attempts else None
        ),
        "mean_poll_interval_sec": (
            round(sum(intervals) / len(intervals), 3) if intervals else None
        ),
        "median_poll_interval_sec": observe._percentile(intervals, 50.0),
        "measurement_basis": {
            "total_poll_attempts": BUDGET_MEASURED,
            "total_observation_rows": BUDGET_MEASURED,
            "attempts_per_minute": BUDGET_DERIVED,
            "median_poll_interval_sec": BUDGET_DERIVED,
            "peak_attempts_in_one_minute": BUDGET_MEASURED,
        },
        # ---- corroboration, and floors rather than counts
        "liveness_lines": _count_journal(p17store.LIVENESS, session),
        "process_lines": _count_journal(p17store.PROCESS, session),
        "heartbeat_lines": _count_journal(p17store.COVERAGE, session),
        "tick_ran_no_row_lines": gaps,
        "stall_lines": stalls,
        "rate_limited_journals_are_floors": (
            "LIVENESS_PROCESS_HEARTBEAT_AND_GAP_JOURNALS_ARE_RATE_LIMITED_PER_"
            "NAME_SO_THEIR_LINE_COUNTS_ARE_FLOORS_ON_ACTIVITY_AND_ARE_NEVER_"
            "SUMMED_INTO_THE_ATTEMPT_COUNT"
        ),
        # ---- not recorded anywhere on this path
        "cpu_seconds": BUDGET_NOT_RECORDED,
        "http_request_count": BUDGET_NOT_RECORDED,
        "timeout_count": BUDGET_NOT_RECORDED,
        "failed_request_count": BUDGET_NOT_RECORDED,
        "skipped_or_duplicate_polls": BUDGET_NOT_RECORDED,
        "fingerprint": poll_budget_fingerprint(),
    }


# The budget replay and the per-event policy record schedule from ONE definition.
# Two copies of a grid function is how a cost report and a fairness report come to
# disagree about what the policy is, and the cost would be the one nobody rechecks.
grid = observe.grid
satisfy = observe.satisfy


def proposed(
    events: list[dict],
    *,
    ticks: dict[str, list[float]],
    cadence_sec: float = SHADOW_CADENCE_SEC,
    window_sec: float = max(SHADOW_WINDOWS_SEC),
    max_gap_sec: float = SHADOW_MAX_GAP_SEC,
) -> dict:
    """PROPOSED_15S_SHADOW_POLICY: the same schedule replayed over the same events.

    Two attempt counts, and the difference between them is the whole reason to
    compute this rather than multiply medians:

    ``attempts_per_event`` sums each event's own grid, which is what a naive
    per-event poller would cost. ``attempts_deduped`` counts distinct
    ``(instrument, instant)`` pairs, which is what the feed would actually spend,
    because two events on one instrument at one instant are one visit. The
    comparison against the measured current budget uses the deduped number,
    since that is the unit the current one is counted in.

    Feasibility is reported per event and never repaired: an instrument the
    recorder visited once a minute cannot answer a 15-second grid, and saying so
    is the finding rather than an inconvenience.
    """
    per_event: list[dict] = []
    seen: set[tuple[str, int]] = set()
    all_instants: list[float] = []
    naive = 0
    by_instrument: dict[str, int] = {}
    by_vehicle: dict[str, int] = {}
    for ev in events:
        start = ev.get("decision_ts")
        if not isinstance(start, (int, float)):
            continue
        inst = str(ev.get("instrument") or "UNKNOWN")
        close = ev.get("session_close_ts")
        if not isinstance(close, (int, float)):
            close = p50calendar.close_ts(start, inst)
        end = float(start) + float(window_sec)
        truncated_by_close = False
        if isinstance(close, (int, float)) and float(close) < end:
            end = float(close)
            truncated_by_close = True
        instants = grid(float(start), end, cadence_sec)
        recorded = ticks.get(inst, [])
        fit = satisfy(instants, recorded, max_gap_sec=max_gap_sec)
        naive += len(instants)
        by_instrument[inst] = by_instrument.get(inst, 0) + len(instants)
        veh = str(ev.get("vehicle") or "UNSPECIFIED")
        by_vehicle[veh] = by_vehicle.get(veh, 0) + len(instants)
        for t in instants:
            key = (inst, int(round(t)))
            if key not in seen:
                seen.add(key)
                all_instants.append(t)
        stats = observe.cadence(fit["satisfied_stamps"])
        feasible = (
            fit["satisfied"] >= 2
            and stats["median_inter_observation_seconds"] is not None
            and stats["median_inter_observation_seconds"] <= cadence_sec * 2
        )
        per_event.append({
            "event_id": ev.get("event_id"),
            "instrument": inst,
            "vehicle": veh,
            "cohort": ev.get("cohort"),
            "decision_ts": float(start),
            "window_end_ts": end,
            "window_truncated_by_close": truncated_by_close,
            "scheduled_instants": len(instants),
            "satisfiable_instants": fit["satisfied"],
            "missed_instants": fit["missed"],
            "satisfied_pct": fit["satisfied_pct"],
            "policy_median_inter_observation_seconds":
                stats["median_inter_observation_seconds"],
            "policy_observation_count": fit["satisfied"],
            "policy_observation_duration_seconds": (
                None if stats["first_observation_ts"] is None
                or stats["last_observation_ts"] is None
                else round(stats["last_observation_ts"]
                           - stats["first_observation_ts"], 1)
            ),
            "feasible_from_raw_capture": bool(feasible),
            "infeasible_reason": None if feasible else SHADOW_INFEASIBLE,
        })
    deduped = len(seen)
    span = (
        round(max(all_instants) - min(all_instants), 1)
        if len(all_instants) > 1 else None
    )
    return {
        "policy": BUDGET_PROPOSED,
        "cadence_sec": float(cadence_sec),
        "window_sec": float(window_sec),
        "max_gap_sec": float(max_gap_sec),
        "eligible_events": len(per_event),
        "total_poll_attempts": deduped,
        "attempts_per_event_naive": naive,
        "attempts_saved_by_dedupe": naive - deduped,
        "attempts_per_minute": (
            round(deduped / (span / MINUTE), 3) if span and span > 0 else None
        ),
        "peak_attempts_in_one_minute": _minute_peak(all_instants),
        "wall_span_seconds": span,
        "attempts_by_instrument": dict(
            sorted(by_instrument.items(), key=lambda kv: -kv[1])),
        "attempts_by_vehicle": dict(
            sorted(by_vehicle.items(), key=lambda kv: -kv[1])),
        "total_observation_rows": sum(
            r["satisfiable_instants"] for r in per_event),
        "feasible_events": sum(
            1 for r in per_event if r["feasible_from_raw_capture"]),
        "infeasible_events": sum(
            1 for r in per_event if not r["feasible_from_raw_capture"]),
        "per_event": per_event,
        "fast_tier_attempts": deduped,
        "slow_tier_attempts": 0,
        "feasibility_is_measured_against": (
            "THE_INSTRUMENTS_OWN_RECORDED_VISITS_BECAUSE_A_VISIT_IS_THE_UNIT_OF_"
            "FEED_COST_THE_EVENT_LEVEL_FAIRNESS_TABLE_MEASURES_THE_SAME_GRID_"
            "AGAINST_EACH_EVENTS_OWN_EXECUTABLE_BOOKS_WHICH_IS_STRICTER_AND_THE_"
            "TWO_COUNTS_ARE_NOT_INTERCHANGEABLE"
        ),
        "tiering": (
            "THE_PROPOSED_SCHEDULE_HAS_ONE_TIER_BY_CONSTRUCTION_EVERY_ELIGIBLE_"
            "EVENT_RECEIVES_THE_SAME_GRID"
        ),
        "cpu_seconds": BUDGET_NOT_RECORDED,
        "http_request_count": BUDGET_NOT_RECORDED,
        "timeout_count": BUDGET_NOT_RECORDED,
        "failed_request_count": BUDGET_NOT_RECORDED,
        "skipped_or_duplicate_polls": BUDGET_NOT_RECORDED,
        "read_only": SHADOW_IS_READ_ONLY,
        "fingerprint": poll_budget_fingerprint(),
    }


def _delta(current: object, prop: object) -> dict:
    cur = current if isinstance(current, (int, float)) else None
    new = prop if isinstance(prop, (int, float)) else None
    if cur is None or new is None:
        return {"current": current, "proposed": prop, "delta": None,
                "pct": None, "basis": BUDGET_NOT_RECORDED}
    return {
        "current": cur,
        "proposed": new,
        "delta": round(new - cur, 3),
        "pct": round(100.0 * (new - cur) / cur, 2) if cur else None,
        "basis": BUDGET_DERIVED,
    }


def compare(current: dict, prop: dict) -> dict:
    """CURRENT vs PROPOSED, and the safety verdict with its own reason.

    Safety is two-sided and both sides must pass: the total attempts and the
    worst single minute have to sit at or under the measured current ones. A
    research comparison is never a reason to spend more feed than production
    already does, so a proposal that wins on the total and loses on the peak is
    refused — the peak is what a rate limit sees.

    A third refusal outranks both: if the raw capture cannot satisfy the grid for
    most events, the policy is not cheap, it is *not implementable read-only*,
    and the honest answer is a redesign rather than a padded recording.
    """
    attempts = _delta(current.get("total_poll_attempts"),
                      prop.get("total_poll_attempts"))
    peak = _delta(current.get("peak_attempts_in_one_minute"),
                  prop.get("peak_attempts_in_one_minute"))
    per_min = _delta(current.get("attempts_per_minute"),
                     prop.get("attempts_per_minute"))
    rows = _delta(current.get("total_observation_rows"),
                  prop.get("total_observation_rows"))
    reasons: list[str] = []
    cheaper = (
        attempts["delta"] is not None and attempts["delta"] <= 0
        and peak["delta"] is not None and peak["delta"] <= 0
    )
    if attempts["delta"] is not None and attempts["delta"] > 0:
        reasons.append("PROPOSED_TOTAL_ATTEMPTS_EXCEED_MEASURED_CURRENT")
    if peak["delta"] is not None and peak["delta"] > 0:
        reasons.append("PROPOSED_WORST_MINUTE_EXCEEDS_MEASURED_CURRENT_WORST_MINUTE")
    if not current.get("exhaustive", False):
        reasons.append("CURRENT_BUDGET_READING_REACHED_ITS_BOUND")
    eligible = prop.get("eligible_events") or 0
    feasible = prop.get("feasible_events") or 0
    if eligible and feasible * 2 < eligible:
        reasons.append(
            "RAW_CAPTURE_CANNOT_SATISFY_THE_GRID_FOR_MOST_ELIGIBLE_EVENTS")
    verdict = BUDGET_SAFE if (cheaper and not reasons) else BUDGET_UNSAFE
    return {
        "verdict": verdict,
        "safe_rule": BUDGET_SAFE_RULE,
        "blocking_reasons": reasons,
        "poll_attempts": attempts,
        "peak_attempts_in_one_minute": peak,
        "attempts_per_minute": per_min,
        "observation_rows": {
            **rows,
            "note": (
                "CURRENT_COUNTS_EVERY_CANDIDATE_ROW_THE_RECORDER_WROTE_PROPOSED_"
                "COUNTS_ONE_OBSERVATION_PER_SATISFIED_GRID_INSTANT_PER_EVENT_SO_"
                "THE_DELTA_IS_A_STORAGE_AND_WORK_DELTA_AND_NOT_A_SECOND_FEED_"
                "COST_MEASUREMENT"
            ),
        },
        "requests": {
            "current": BUDGET_NOT_RECORDED, "proposed": BUDGET_NOT_RECORDED,
            "note": (
                "ONE_VISIT_ISSUES_THE_SAME_CALLS_UNDER_EITHER_POLICY_SO_THE_"
                "REQUEST_RATIO_FOLLOWS_THE_ATTEMPT_RATIO_BUT_NEITHER_COUNT_IS_"
                "JOURNALLED_AND_NEITHER_IS_REPORTED_AS_A_NUMBER"
            ),
        },
        "cpu": {
            "current": BUDGET_NOT_RECORDED, "proposed": BUDGET_NOT_RECORDED,
            "note": (
                "NO_CPU_SAMPLE_IS_JOURNALLED_ON_THIS_PATH_SO_NO_CPU_DELTA_IS_"
                "REPORTED_THE_ATTEMPT_DELTA_IS_NOT_A_CPU_DELTA"
            ),
        },
        "network": {
            "current": BUDGET_NOT_RECORDED, "proposed": BUDGET_NOT_RECORDED,
            "note": (
                "NO_BYTE_COUNT_IS_JOURNALLED_THE_ATTEMPT_DELTA_BOUNDS_THE_"
                "NETWORK_DELTA_ONLY_IF_A_VISIT_COSTS_THE_SAME_BYTES_UNDER_BOTH_"
                "POLICIES_WHICH_IS_UNVERIFIED_HERE"
            ),
        },
        "feasibility": {
            "eligible_events": eligible,
            "feasible_events": feasible,
            "infeasible_events": prop.get("infeasible_events"),
            "infeasible_reason": SHADOW_INFEASIBLE,
        },
        "fingerprint": poll_budget_fingerprint(),
    }
