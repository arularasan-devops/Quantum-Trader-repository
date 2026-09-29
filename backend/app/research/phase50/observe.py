"""Phase 50 §A — why forward observation stopped, per event. Pure: no store, no clock.

A read-only diagnostic about **data coverage**, built because the coverage guard
refused every policy row and refusing is not diagnosing. The guard proved the two
cohorts were not resolved at comparable rates; this module asks what the recorded
data says produced that difference.

Three rules hold everywhere in here.

*Nothing about an outcome is read.* Not net, not gross, not MFE, MAE, giveback,
win rate or the final status. Explaining why an event was observed by what it
earned would be the same hindsight the exit policies are forbidden to use, one
layer down — and it would produce exactly the story a reader wants: that the
capture watched the winners.

*A reason is asserted only from a recorded fact.* Each reason in
:data:`~app.research.phase50.STOP_REASONS` names the field that asserts it, the
reasons are tested in the frozen order of that tuple, and the first whose
evidence is present is the answer. So every event gets exactly one reason and
"unresolved" — which is a consequence, not a reason — is never one of them.

*The overlay is never blamed from a stop reason.* No recorded field says the
recorder watched a contract because the overlay supported it, so that cause is
reported only as a residual depth asymmetry that survives inside every stop
reason. Assigning it from a reason would be inventing the finding.
"""
from __future__ import annotations

import math

from app.research.phase50 import (
    BIAS_CONTRACT_TRACKING,
    BIAS_EVENT_TERMINATION,
    BIAS_G_IS_CORRELATION_ONLY,
    BIAS_INSTRUMENT_MIX,
    BIAS_MIN_CADENCE_RATIO,
    BIAS_MIN_DEPTH_RATIO,
    BIAS_MIN_EVENTS_PER_COHORT,
    BIAS_MIN_STOP_SHARE_GAP_PP,
    BIAS_OTHER,
    BIAS_OVERLAY_CORRELATED,
    BIAS_POLL_CADENCE,
    BIAS_QUOTE_AVAILABILITY,
    BIAS_SESSION_BOUNDARY,
    BIAS_SOURCE_FROM_CAUSE,
    BIAS_SOURCES,
    BIAS_UNRANKED,
    CADENCE_ASYMMETRIC,
    CADENCE_COMPARABLE,
    CADENCE_CONTINUOUS,
    CADENCE_CONTINUOUS_MAX_SEC,
    CADENCE_GUARD_RULE,
    CADENCE_INTERMITTENT,
    CADENCE_IS_NOT_PERFORMANCE,
    CADENCE_LABELS,
    CADENCE_MAX_RATIO,
    CADENCE_NOT_MEASURABLE,
    CADENCE_RULE,
    CADENCE_SINGLE,
    CADENCE_SPARSE,
    CADENCE_SPARSE_MIN_SEC,
    CAUSE_OVERLAY,
    CAUSE_OVERLAY_IS_A_RESIDUAL,
    CAUSES,
    COVERAGE_HORIZONS_SEC,
    DIAGNOSTIC_HAS_NO_OUTCOME,
    SESSION_CLOSE_TOLERANCE_SEC,
    SHADOW_CADENCE_SEC,
    SHADOW_EQUALIZED,
    SHADOW_EQUALIZED_RULE,
    SHADOW_GRID_FILLED,
    SHADOW_GRID_MISSED,
    SHADOW_INFEASIBLE,
    SHADOW_IS_READ_ONLY,
    SHADOW_MAX_GAP_SEC,
    SHADOW_NO_BACKFILL,
    SHADOW_NO_BRANCH,
    SHADOW_NOT_EQUALIZED,
    SHADOW_POLICY,
    SHADOW_POLICY_RULE,
    SHADOW_WINDOWS_SEC,
    STOP_CAPTURE_GAP,
    STOP_CONTRACT_DROPPED,
    STOP_EVENT_ENDED,
    STOP_FEED_STOPPED,
    STOP_GROUP_ENDED,
    STOP_NO_LATER_QUOTE,
    STOP_OTHER,
    STOP_REASON_CAUSE,
    STOP_REASON_EVIDENCE,
    STOP_REASONS,
    STOP_SESSION_ENDED,
    STOP_SIDE_UNAVAILABLE,
    STOP_STALE_QUOTE,
    cadence_guard_fingerprint,
    observation_diagnostic_fingerprint,
)

# The per-event fields whose whole distribution is reported, not only a median.
# A cohort holding one 630-observation event and twenty single-observation ones
# has a median that hides both, and the question "was this cohort watched" is a
# question about the spread.
DISTRIBUTION_FIELDS: tuple[str, ...] = (
    "observation_count",
    "observation_duration_seconds",
    "median_inter_observation_seconds",
    "observations_per_minute",
    "executable_observation_count",
    "executable_observation_density",
    "forward_coverage_seconds",
)

# Fields no record produced here may carry. Enforced rather than promised: the
# smoke asserts the intersection is empty, so a later edit that reaches for a
# net figure to explain a coverage number fails a test instead of shipping.
FORBIDDEN_FIELDS: tuple[str, ...] = (
    "net_pct", "gross_pct", "mfe_pct", "mae_pct", "giveback_pct", "net_points",
    "win_rate_pct", "profit_factor", "t1_hit", "resolution_status",
)


def stop_reason(
    *,
    first_ts: float | None,
    last_ts: float | None,
    last_quality_is_fillable: bool,
    later_books: int,
    later_executable_books: int,
    next_observation_ts_any_contract: float | None,
    next_observation_ts_same_contract: float | None,
    session_close_ts: float | None,
    gap_sec: float,
) -> dict:
    """Why forward observation of one event stopped, with the fact that says so.

    Every argument is a measured property of the recording: timestamps the
    capture wrote, the quality it recorded, how many later books exist and
    whether it went on observing this contract or any contract afterwards. The
    tests run in the frozen order of :data:`STOP_REASONS` and the first that
    fires wins, so the answer is one reason and is reproducible.
    """
    reason = _reason(
        first_ts=first_ts,
        last_ts=last_ts,
        last_quality_is_fillable=last_quality_is_fillable,
        later_books=later_books,
        later_executable_books=later_executable_books,
        next_any=next_observation_ts_any_contract,
        next_same=next_observation_ts_same_contract,
        session_close_ts=session_close_ts,
        gap_sec=float(gap_sec),
    )
    return {
        "observation_stopped_because": reason,
        "stop_reason_evidence": STOP_REASON_EVIDENCE[reason],
        "stop_reason_cause": STOP_REASON_CAUSE[reason],
    }


def _reason(
    *,
    first_ts: float | None,
    last_ts: float | None,
    last_quality_is_fillable: bool,
    later_books: int,
    later_executable_books: int,
    next_any: float | None,
    next_same: float | None,
    session_close_ts: float | None,
    gap_sec: float,
) -> str:
    if first_ts is None or last_ts is None:
        return STOP_OTHER
    if not last_quality_is_fillable:
        return STOP_STALE_QUOTE
    if later_books > 0 and later_executable_books <= 0:
        return STOP_SIDE_UNAVAILABLE
    if session_close_ts is not None and (
            float(session_close_ts) - float(last_ts)) <= SESSION_CLOSE_TOLERANCE_SEC:
        return STOP_SESSION_ENDED
    if next_any is None:
        return STOP_FEED_STOPPED
    if (float(next_any) - float(last_ts)) > gap_sec:
        return STOP_CAPTURE_GAP
    if next_same is not None:
        return STOP_GROUP_ENDED
    if later_books <= 0:
        return STOP_CONTRACT_DROPPED
    if later_executable_books > 0:
        return STOP_EVENT_ENDED
    return STOP_NO_LATER_QUOTE


def cadence(stamps: list[float] | None) -> dict:
    """How often one event was looked at, from its own observation instants.

    Duration answers "for how long", which is the question the coverage table
    already answered; this answers "how often", which it could not. An event
    observed for ten minutes four times and one observed for ten minutes two
    hundred times are the same row there and different recordings.

    Percentiles are nearest-rank and never interpolated, so every figure printed
    is an interval that actually occurred between two recorded observations. A
    single observation yields no interval at all and is labelled that way rather
    than given a zero, because "never polled again" is not "polled instantly".
    """
    ordered = sorted(
        float(s) for s in (stamps or []) if isinstance(s, (int, float))
        and not isinstance(s, bool)
    )
    gaps = [round(b - a, 3) for a, b in zip(ordered, ordered[1:])]
    duration = (
        None if len(ordered) < 2 else round(ordered[-1] - ordered[0], 1)
    )
    median_gap = _percentile(gaps, 50.0)
    per_minute = (
        None if duration is None or duration <= 0
        else round(len(ordered) / (duration / 60.0), 3)
    )
    return {
        "first_observation_ts": ordered[0] if ordered else None,
        "last_observation_ts": ordered[-1] if ordered else None,
        "observation_intervals": len(gaps),
        "median_inter_observation_seconds": median_gap,
        "p25_inter_observation_seconds": _percentile(gaps, 25.0),
        "p75_inter_observation_seconds": _percentile(gaps, 75.0),
        "p95_inter_observation_seconds": _percentile(gaps, 95.0),
        "max_inter_observation_seconds": _percentile(gaps, 100.0),
        "observations_per_minute": per_minute,
        "cadence_label": _cadence_label(median_gap),
        "cadence_rule": CADENCE_RULE,
        "cadence_is_not_performance": CADENCE_IS_NOT_PERFORMANCE,
    }


def _cadence_label(median_gap: float | None) -> str:
    if median_gap is None:
        return CADENCE_SINGLE
    if median_gap <= CADENCE_CONTINUOUS_MAX_SEC:
        return CADENCE_CONTINUOUS
    if median_gap >= CADENCE_SPARSE_MIN_SEC:
        return CADENCE_SPARSE
    return CADENCE_INTERMITTENT


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile: an observed value, never an interpolated one."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(float(pct) / 100.0 * len(ordered)) - 1
    return round(ordered[max(0, min(rank, len(ordered) - 1))], 2)


def record(
    identity: dict,
    *,
    first_ts: float | None,
    last_ts: float | None,
    observation_count: int,
    observation_stamps: list[float] | None = None,
    executable_stamps: list[float] | None = None,
    max_intra_event_gap_sec: float | None,
    last_quality: str | None,
    last_quality_is_fillable: bool,
    later_books: int,
    later_executable_books: int,
    first_later_executable_ts: float | None,
    last_later_executable_ts: float | None,
    next_observation_ts_any_contract: float | None,
    next_observation_ts_same_contract: float | None,
    session_close_ts: float | None,
    gap_sec: float,
) -> dict:
    """One event's forward-observation record: what was observed and what stopped it.

    ``identity`` carries the §1 identity fields the caller already holds. The
    rest is coverage, and there is deliberately no room in the shape for an
    outcome — see :data:`FORBIDDEN_FIELDS`.
    """
    duration = (
        None if first_ts is None or last_ts is None
        else round(float(last_ts) - float(first_ts), 1)
    )
    covered = (
        None if first_ts is None or last_later_executable_ts is None
        else round(float(last_later_executable_ts) - float(first_ts), 1)
    )
    observable_after = later_books > 0 or next_observation_ts_same_contract is not None
    # An event with no later executable book is *not* covered to five minutes —
    # it is False here rather than unanswerable, because "could this policy have
    # been measured on this event" has an answer for such an event and the answer
    # is no. Whether a later book existed at all is its own column above.
    horizons = {
        _horizon_key(h): covered is not None and covered >= float(h)
        for h in COVERAGE_HORIZONS_SEC
    }
    reached_close = (
        None if session_close_ts is None or last_ts is None
        else (float(session_close_ts) - float(last_ts)) <= SESSION_CLOSE_TOLERANCE_SEC
    )
    poll = cadence(observation_stamps)
    # Executable density is per minute of the *forward coverage* the event
    # actually holds, not per minute of the window it might have had: the
    # question is how thickly the priceable books arrived while they were
    # arriving, and dividing by a window nobody observed would report a short
    # dense recording as a sparse one.
    density = (
        None if covered is None or covered <= 0
        else round(int(later_executable_books) / (covered / 60.0), 3)
    )
    return {
        **identity,
        "first_decision_ts": first_ts,
        "last_observed_ts": last_ts,
        "observation_count": int(observation_count),
        "observation_duration_seconds": duration,
        **poll,
        # The same event measured a second way: what the 15-second policy of §4
        # can see in the evidence already on disk. Reported beside the recording's
        # own cadence rather than replacing it — §11 keeps the raw capture, and a
        # reader has to be able to see both numbers to judge the policy.
        **policy_record(
            decision_ts=first_ts,
            executable_stamps=executable_stamps,
            session_close_ts=session_close_ts,
        ),
        "executable_observation_count": int(later_executable_books),
        "executable_observation_density": density,
        "executable_observation_density_is": (
            "EXECUTABLE_BOOKS_PER_MINUTE_OF_MEASURED_FORWARD_COVERAGE"
        ),
        "max_intra_event_gap_sec": max_intra_event_gap_sec,
        "last_observation_quality": last_quality,
        "last_observation_is_fillable": bool(last_quality_is_fillable),
        "later_books_after_entry": int(later_books),
        "number_of_executable_quotes_after_entry": int(later_executable_books),
        "first_later_executable_quote_ts": first_later_executable_ts,
        "last_later_executable_quote_ts": last_later_executable_ts,
        "forward_coverage_seconds": covered,
        "remained_observable_after_decision": bool(observable_after),
        "has_any_later_executable_quote": later_executable_books > 0,
        **horizons,
        "reached_session_close": reached_close,
        "session_close_ts": session_close_ts,
        "next_observation_ts_any_contract": next_observation_ts_any_contract,
        "next_observation_ts_same_contract": next_observation_ts_same_contract,
        **stop_reason(
            first_ts=first_ts,
            last_ts=last_ts,
            last_quality_is_fillable=last_quality_is_fillable,
            later_books=later_books,
            later_executable_books=later_executable_books,
            next_observation_ts_any_contract=next_observation_ts_any_contract,
            next_observation_ts_same_contract=next_observation_ts_same_contract,
            session_close_ts=session_close_ts,
            gap_sec=gap_sec,
        ),
        "diagnostic": DIAGNOSTIC_HAS_NO_OUTCOME,
    }


def _horizon_key(seconds: float) -> str:
    return f"covered_to_{int(float(seconds) // 60)}m"


# ------------------------------------------------------------------ the policy
#
# Step 2. One schedule, both cohorts, applied to observations that are already on
# disk. Read-only by construction: :func:`grid` is a function of three numbers
# and :func:`satisfy` only ever *selects* from a recorded list, so there is no
# parameter here through which an overlay state, an instrument, a vehicle, a cost
# band or an outcome could reach the schedule — which is the §3 requirement
# expressed in a signature rather than asserted in a comment.


def grid(
    start: float, end: float, cadence_sec: float = SHADOW_CADENCE_SEC,
) -> list[float]:
    """The observation instants of one event: the decision, then fixed steps, to end.

    Deterministic and total. Two events decided at the same instant with the same
    window receive the same list on any machine and in any order, which is the
    regression the §13 test pins.
    """
    if cadence_sec <= 0:
        return [float(start)]
    out = [float(start)]
    step = 1
    while True:
        nxt = float(start) + step * float(cadence_sec)
        if nxt > float(end) + 1e-9:
            break
        out.append(round(nxt, 3))
        step += 1
    return out


def satisfy(
    instants: list[float],
    recorded: list[float],
    *,
    max_gap_sec: float = SHADOW_MAX_GAP_SEC,
) -> dict:
    """Which grid instants the recorded evidence can answer, and which it cannot.

    An instant is satisfied by the first recorded observation **at or after** it
    and no later than ``max_gap_sec``. Never by the nearest earlier one: reaching
    backwards for a look that happened before the instant is the substitution the
    pricing rules already refuse, one axis over, and it would manufacture a
    15-second recording out of a once-a-minute one. An unsatisfied instant stays
    unsatisfied — see :data:`SHADOW_NO_BACKFILL`.

    One recorded look answers at most one instant. Letting a single look fill
    every instant it happens to be within a minute of would turn a once-a-minute
    capture into a four-times-a-minute one on paper: four observations, three of
    them the same quote, and a median interval of zero seconds that no feed
    produced.
    """
    ordered = sorted(
        float(r) for r in (recorded or [])
        if isinstance(r, (int, float)) and not isinstance(r, bool)
    )
    used: list[float] = []
    idx = 0
    for want in instants:
        while idx < len(ordered) and ordered[idx] < float(want) - 1e-9:
            idx += 1
        if idx < len(ordered) and (ordered[idx] - float(want)) <= max_gap_sec + 1e-9:
            used.append(ordered[idx])
            idx += 1
    return {
        "grid_instants": len(instants),
        "satisfied": len(used),
        "missed": len(instants) - len(used),
        "satisfied_pct": (
            round(100.0 * len(used) / len(instants), 2) if instants else None
        ),
        "satisfied_stamps": used,
        "filled": SHADOW_GRID_FILLED,
        "missed_is": SHADOW_GRID_MISSED,
        "no_backfill": SHADOW_NO_BACKFILL,
    }


def policy_record(
    *,
    decision_ts: float | None,
    executable_stamps: list[float] | None,
    session_close_ts: float | None,
    cadence_sec: float = SHADOW_CADENCE_SEC,
    max_gap_sec: float = SHADOW_MAX_GAP_SEC,
    windows_sec: tuple[float, ...] = SHADOW_WINDOWS_SEC,
) -> dict:
    """One event's observation under the 15-second policy, from its own evidence.

    The window runs from the decision instant to ``decision + max(windows)`` or
    this instrument's own close, whichever comes first, and the event's grouping
    identity does not end it — §6's separation of *what the opportunity is* from
    *how long its later outcome is measured*.

    Coverage per horizon is asserted from the last **satisfied** instant, so an
    event whose contract stopped being quoted at four minutes is not covered to
    five however long its window nominally ran.
    """
    horizon = max(windows_sec)
    if decision_ts is None:
        return {
            "policy": SHADOW_POLICY,
            "policy_cadence_sec": float(cadence_sec),
            "policy_scheduled_instants": 0,
            "policy_observation_count": 0,
            "policy_feasible": False,
            "policy_infeasible_reason": SHADOW_INFEASIBLE,
            "policy_is_read_only": SHADOW_IS_READ_ONLY,
        }
    start = float(decision_ts)
    end = start + float(horizon)
    truncated = False
    if isinstance(session_close_ts, (int, float)) and float(session_close_ts) < end:
        end = float(session_close_ts)
        truncated = True
    instants = grid(start, end, cadence_sec)
    fit = satisfy(instants, executable_stamps or [], max_gap_sec=max_gap_sec)
    stamps = fit["satisfied_stamps"]
    stats = cadence(stamps)
    last = stats["last_observation_ts"]
    covered = None if last is None else round(float(last) - start, 1)
    horizons = {
        f"policy_covered_to_{int(float(w) // 60)}m": (
            covered is not None and covered >= float(w)
            # A window the session closed inside is not "not covered" — it was
            # never offered, and False there would charge an exchange clock to
            # the recording. It is None, and the fairness table counts it as
            # unavailable in BOTH cohorts or in neither.
            if not (truncated and start + float(w) > end + 1e-9) else None
        )
        for w in windows_sec
    }
    feasible = (
        len(stamps) >= 2
        and stats["median_inter_observation_seconds"] is not None
        and stats["median_inter_observation_seconds"] <= float(cadence_sec) * 2.0
    )
    return {
        "policy": SHADOW_POLICY,
        "policy_rule": SHADOW_POLICY_RULE,
        "policy_no_branch": SHADOW_NO_BRANCH,
        "policy_is_read_only": SHADOW_IS_READ_ONLY,
        "policy_cadence_sec": float(cadence_sec),
        "policy_max_gap_sec": float(max_gap_sec),
        "policy_window_sec": float(horizon),
        "policy_window_start_ts": start,
        "policy_window_end_ts": end,
        "policy_window_truncated_by_close": truncated,
        "policy_scheduled_instants": len(instants),
        "policy_observation_count": len(stamps),
        "policy_missed_instants": fit["missed"],
        "policy_satisfied_pct": fit["satisfied_pct"],
        "policy_first_observation_ts": stats["first_observation_ts"],
        "policy_last_observation_ts": stats["last_observation_ts"],
        "policy_observation_duration_seconds": (
            None if stats["first_observation_ts"] is None
            or stats["last_observation_ts"] is None
            else round(stats["last_observation_ts"]
                       - stats["first_observation_ts"], 1)
        ),
        "policy_median_inter_observation_seconds":
            stats["median_inter_observation_seconds"],
        "policy_p25_inter_observation_seconds":
            stats["p25_inter_observation_seconds"],
        "policy_p75_inter_observation_seconds":
            stats["p75_inter_observation_seconds"],
        "policy_p95_inter_observation_seconds":
            stats["p95_inter_observation_seconds"],
        "policy_max_inter_observation_seconds":
            stats["max_inter_observation_seconds"],
        "policy_observations_per_minute": stats["observations_per_minute"],
        "policy_forward_coverage_seconds": covered,
        **horizons,
        "policy_feasible": bool(feasible),
        "policy_infeasible_reason": None if feasible else SHADOW_INFEASIBLE,
    }


def policy_cohort(records: list[dict]) -> dict:
    """One cohort measured under the policy: eligibility, resolution, cadence.

    §8's table. ``resolved`` here means the policy grid was satisfiable at all —
    two or more observations inside the declared window — and nothing about a
    price or an outcome enters it.
    """
    total = len(records)
    feasible = [r for r in records if r.get("policy_feasible")]
    observed = [r for r in records if int(r.get("policy_observation_count") or 0) > 0]
    return {
        "eligible_events": total,
        "observed_events": len(observed),
        "resolved_events": len(feasible),
        "unresolved_events": total - len(feasible),
        "resolution_rate_pct": (
            None if not total else round(100.0 * len(feasible) / total, 2)
        ),
        "unresolved_reason": SHADOW_INFEASIBLE,
        "median_policy_observation_count": _median(
            records, "policy_observation_count"),
        "median_policy_inter_observation_seconds": _median(
            records, "policy_median_inter_observation_seconds"),
        "median_policy_observation_duration_seconds": _median(
            records, "policy_observation_duration_seconds"),
        "median_policy_scheduled_instants": _median(
            records, "policy_scheduled_instants"),
        "median_policy_satisfied_pct": _median(records, "policy_satisfied_pct"),
        **{
            f"pct_policy_covered_to_{int(float(w) // 60)}m": _pct(
                records, f"policy_covered_to_{int(float(w) // 60)}m")
            for w in SHADOW_WINDOWS_SEC
        },
    }


def equalised(selected: list[dict], declined: list[dict],
              *, cadence_sec: float = SHADOW_CADENCE_SEC) -> dict:
    """Whether the policy actually equalised the two cohorts' cadence.

    Checked rather than assumed, because the policy can only select from what was
    recorded: a cohort whose instruments were visited once a minute comes back at
    60 seconds under a 15-second schedule, and the correct output then is
    ``CADENCE_NOT_EQUALIZED`` with no performance comparison — not a schedule
    that claims an equality the evidence does not hold.
    """
    sel = _median(selected, "policy_median_inter_observation_seconds")
    dec = _median(declined, "policy_median_inter_observation_seconds")
    bar = float(cadence_sec) * 2.0
    ok = (
        sel is not None and dec is not None
        and sel <= bar and dec <= bar
    )
    return {
        "verdict": SHADOW_EQUALIZED if ok else SHADOW_NOT_EQUALIZED,
        "rule": SHADOW_EQUALIZED_RULE,
        "declared_cadence_sec": float(cadence_sec),
        "tolerated_median_sec": bar,
        "selected_median_inter_observation_seconds": sel,
        "declined_median_inter_observation_seconds": dec,
        "selected_feasible_events": sum(
            1 for r in selected if r.get("policy_feasible")),
        "declined_feasible_events": sum(
            1 for r in declined if r.get("policy_feasible")),
        "publishable": bool(ok),
        "withheld_reason": None if ok else SHADOW_NOT_EQUALIZED,
    }


def cohort(records: list[dict]) -> dict:
    """One cohort's coverage shape: medians, coverage percentages, stop reasons.

    Medians rather than means throughout. A cohort holding one 630-observation
    event and twenty single-observation ones has a mean that describes neither,
    and the question here is what a typical event's coverage looked like.
    """
    total = len(records)
    legs = sum(int(r.get("observation_count") or 0) for r in records)
    reasons = {
        reason: sum(
            1 for r in records if r.get("observation_stopped_because") == reason)
        for reason in STOP_REASONS
    }
    return {
        "events": total,
        "legs": legs,
        "median_observation_duration_sec": _median(
            records, "observation_duration_seconds"),
        "median_later_book_count": _median(records, "later_books_after_entry"),
        "median_executable_quote_count": _median(
            records, "number_of_executable_quotes_after_entry"),
        "median_forward_coverage_sec": _median(records, "forward_coverage_seconds"),
        "median_observation_count": _median(records, "observation_count"),
        "median_inter_observation_sec": _median(
            records, "median_inter_observation_seconds"),
        "median_p95_inter_observation_sec": _median(
            records, "p95_inter_observation_seconds"),
        "median_max_inter_observation_sec": _median(
            records, "max_inter_observation_seconds"),
        "median_observations_per_minute": _median(
            records, "observations_per_minute"),
        "median_executable_observation_count": _median(
            records, "executable_observation_count"),
        "median_executable_observations_per_minute": _median(
            records, "executable_observation_density"),
        "cadence_label_counts": _cadence_counts(records),
        "cadence_label_pct": {
            label: (None if total <= 0 else round(100.0 * count / total, 2))
            for label, count in _cadence_counts(records).items()
        },
        "distributions": {
            field: distribution(records, field) for field in DISTRIBUTION_FIELDS
        },
        "cadence_rule": CADENCE_RULE,
        "cadence_is_not_performance": CADENCE_IS_NOT_PERFORMANCE,
        "pct_with_any_later_executable_quote": _pct(
            records, "has_any_later_executable_quote"),
        "pct_remained_observable": _pct(
            records, "remained_observable_after_decision"),
        **{
            f"pct_{_horizon_key(h)}": _pct(records, _horizon_key(h))
            for h in COVERAGE_HORIZONS_SEC
        },
        "pct_reached_session_close": _pct(records, "reached_session_close"),
        "stopped_by": reasons,
        "stopped_by_pct": {
            reason: (None if total <= 0 else round(100.0 * count / total, 2))
            for reason, count in reasons.items()
        },
        "cause_counts": _cause_counts(records),
        "cause_pct": {
            cause: (None if total <= 0 else round(100.0 * count / total, 2))
            for cause, count in _cause_counts(records).items()
        },
    }


def _cadence_counts(records: list[dict]) -> dict:
    counts = {label: 0 for label in CADENCE_LABELS}
    for row in records:
        label = str(row.get("cadence_label") or "")
        if label in counts:
            counts[label] += 1
    return counts


def distribution(records: list[dict], field: str) -> dict:
    """One field's whole shape across a cohort, not its middle.

    Reported beside every median the cadence table prints, because a median of
    four intervals and a median of two hundred are indistinguishable in a single
    column and the spread is what says whether a cohort was watched or sampled.
    """
    values = [
        v for v in (_num(r.get(field)) for r in records) if v is not None
    ]
    return {
        "field": field,
        "n": len(values),
        "unmeasured_events": len(records) - len(values),
        "min": _percentile(values, 0.0001) if values else None,
        "p10": _percentile(values, 10.0),
        "p25": _percentile(values, 25.0),
        "median": _percentile(values, 50.0),
        "p75": _percentile(values, 75.0),
        "p90": _percentile(values, 90.0),
        "max": _percentile(values, 100.0),
        "percentiles_are": "NEAREST_RANK_OBSERVED_VALUES_NOT_INTERPOLATED",
    }


def cadence_guard(selected: list[dict], declined: list[dict]) -> dict:
    """Whether the two cohorts were *polled* on comparable schedules.

    The coverage guard asks whether they were resolved at comparable rates; this
    asks whether they were given comparable opportunity to be. Both must hold
    before a performance subtraction is published, because equal resolution
    reached from unequal polling is a coincidence: the sparser cohort resolved
    the events that happened to be quoted when it was looked at.

    One condition, not two, unlike the coverage guard: cadence is a rate rather
    than a percentage, so a ratio is scale-free already and a fixed second bar in
    seconds would mean different things at 3s and at 300s.
    """
    return cadence_comparable(
        _median(selected, "median_inter_observation_seconds"),
        _median(declined, "median_inter_observation_seconds"),
        selected_events=len(selected),
        declined_events=len(declined),
    )


def cadence_comparable(
    selected_median_sec: float | None,
    declined_median_sec: float | None,
    *,
    selected_events: int,
    declined_events: int,
) -> dict:
    """The same guard from two already-measured intervals.

    Split out so the performance table can consult it without holding coverage
    records: the filter path knows each event's span and observation count and
    can state its own interval, and the guard must be the *same* guard in both
    places or the two tables can disagree about whether a row may be published.
    """
    left, right = _num(selected_median_sec), _num(declined_median_sec)
    ratio: float | None = None
    if not selected_events or not declined_events or left is None or (
            right is None) or left <= 0 or right <= 0:
        status = CADENCE_NOT_MEASURABLE
    else:
        ratio = round(max(left, right) / min(left, right), 3)
        status = (
            CADENCE_COMPARABLE if ratio <= CADENCE_MAX_RATIO
            else CADENCE_ASYMMETRIC
        )
    return {
        "status": status,
        "comparable": status == CADENCE_COMPARABLE,
        "selected_median_inter_observation_sec": left,
        "declined_median_inter_observation_sec": right,
        "cadence_ratio": ratio,
        "max_ratio": CADENCE_MAX_RATIO,
        "selected_events": int(selected_events),
        "declined_events": int(declined_events),
        "guard_rule": CADENCE_GUARD_RULE,
        "guard_fingerprint": cadence_guard_fingerprint(),
        "cadence_is_not_performance": CADENCE_IS_NOT_PERFORMANCE,
    }


def _cause_counts(records: list[dict]) -> dict:
    counts = {cause: 0 for cause in CAUSES}
    for row in records:
        cause = str(row.get("stop_reason_cause") or "")
        if cause in counts:
            counts[cause] += 1
    return counts


def compare(selected: list[dict], declined: list[dict]) -> dict:
    """The two cohorts' coverage side by side, with the gap on each row.

    Published without a guard, unlike the performance table: this *is* the
    coverage measurement, so suppressing the difference between two coverage
    figures because the coverage differs would be circular. Nothing here is a
    trading quantity, so nothing here can be misread as one.
    """
    left, right = cohort(selected), cohort(declined)
    keys = [
        "median_observation_duration_sec", "median_later_book_count",
        "median_executable_quote_count", "median_forward_coverage_sec",
        "median_observation_count", "median_inter_observation_sec",
        "median_p95_inter_observation_sec", "median_max_inter_observation_sec",
        "median_observations_per_minute",
        "median_executable_observation_count",
        "median_executable_observations_per_minute",
        "pct_with_any_later_executable_quote",
        "pct_remained_observable",
        *[f"pct_{_horizon_key(h)}" for h in COVERAGE_HORIZONS_SEC],
        "pct_reached_session_close",
    ]
    return {
        "columns": {"selected": left, "declined": right},
        "cadence_guard": cadence_guard(selected, declined),
        "gaps": {
            key: _gap(left.get(key), right.get(key)) for key in keys
        },
        "stop_reason_gaps_pp": {
            reason: _gap(
                (left["stopped_by_pct"] or {}).get(reason),
                (right["stopped_by_pct"] or {}).get(reason),
            )
            for reason in STOP_REASONS
        },
        "no_guard_note": (
            "the coverage guard withholds a *performance* delta when coverage "
            "is asymmetric; this table is the coverage itself, and withholding "
            "it because coverage differs would hide the measurement being asked "
            "for"
        ),
    }


def attribute(selected: list[dict], declined: list[dict]) -> dict:
    """Which measured cause the selected/declined coverage asymmetry sits in.

    A cause's contribution is the difference between the share of each cohort
    whose observation stopped for a reason belonging to it, in percentage points
    of that cohort. The dominant cause is the largest absolute contribution — a
    ranking of measured shares, not an opinion about mechanism.

    Two honesty constraints. With either cohort empty nothing is attributed at
    all, because a share of nothing is not a share. And ``CAUSE_OVERLAY`` is
    never assigned here: it appears only as ``residual_depth_asymmetry``, the
    part of the depth difference that survives inside every stop reason, with the
    note that a capture whose policy already differs between cohorts cannot
    separate it.
    """
    left, right = cohort(selected), cohort(declined)
    if not selected or not declined:
        return {
            "attributable": False,
            "reason": (
                "one cohort is empty, so no share can be compared and no cause "
                "can be ranked"
            ),
            "contributions_pp": {},
            "dominant_cause": None,
            "residual_depth_asymmetry": None,
            "overlay_cause_note": CAUSE_OVERLAY_IS_A_RESIDUAL,
        }
    contributions = {
        cause: _gap(
            (right["cause_pct"] or {}).get(cause),
            (left["cause_pct"] or {}).get(cause),
        )
        for cause in CAUSES
        if cause != CAUSE_OVERLAY
    }
    ranked = sorted(
        ((cause, value) for cause, value in contributions.items()
         if value is not None),
        key=lambda kv: (-abs(kv[1]), kv[0]),
    )
    dominant = ranked[0][0] if ranked else None
    return {
        "attributable": True,
        "contributions_pp": contributions,
        "contribution_is": (
            "DECLINED_SHARE_MINUS_SELECTED_SHARE_OF_EVENTS_WHOSE_OBSERVATION_"
            "STOPPED_FOR_A_REASON_IN_THIS_CAUSE_IN_PERCENTAGE_POINTS"
        ),
        "ranked": [{"cause": c, "contribution_pp": v} for c, v in ranked],
        "dominant_cause": dominant,
        "dominant_contribution_pp": ranked[0][1] if ranked else None,
        "residual_depth_asymmetry": _residual(selected, declined),
        "overlay_cause_note": CAUSE_OVERLAY_IS_A_RESIDUAL,
        "fingerprint": observation_diagnostic_fingerprint(),
    }


def _residual(selected: list[dict], declined: list[dict]) -> dict:
    """Depth asymmetry inside each stop reason, where the reason cannot explain it.

    Two cohorts can stop for the same reasons in the same proportions and still
    be observed to different depths — 40 books against 4 before an event group
    ends is the same reason and a different recording. That surviving difference
    is what a stop-reason table cannot see, so it is measured per reason and
    reported with the count it rests on.
    """
    rows: list[dict] = []
    for reason in STOP_REASONS:
        left = [r for r in selected
                if r.get("observation_stopped_because") == reason]
        right = [r for r in declined
                 if r.get("observation_stopped_because") == reason]
        if not left or not right:
            continue
        sel = _median(left, "observation_count")
        dec = _median(right, "observation_count")
        rows.append({
            "stop_reason": reason,
            "selected_events": len(left),
            "declined_events": len(right),
            "selected_median_observations": sel,
            "declined_median_observations": dec,
            "ratio": (
                None if not sel or not dec or dec == 0
                else round(float(sel) / float(dec), 3)
            ),
        })
    survives = [
        row for row in rows
        if row["ratio"] is not None and (row["ratio"] >= 2.0 or row["ratio"] <= 0.5)
    ]
    return {
        "per_stop_reason": rows,
        "reasons_compared": len(rows),
        "reasons_with_depth_ratio_beyond_2x": len(survives),
        "survives_inside_every_compared_reason": bool(rows) and len(survives) == len(rows),
        "means": (
            "a depth difference that survives inside a stop reason cannot be "
            "explained by that reason, and no recorded field says whether the "
            "overlay state caused it — so it is reported and not attributed"
        ),
    }


def bias_sources(selected: list[dict], declined: list[dict]) -> dict:
    """Which mechanisms the recorded data supports as sources of the asymmetry.

    A second-level question, distinct from :func:`attribute`: that ranks stop
    reasons, and the largest mechanism in this capture is invisible to a stop
    reason because it is a property of *which contracts each cohort contains*.
    Eight declined SILVER events observed once each and eight selected CRUDEOIL
    events observed two hundred times each stop for the same reasons in the same
    proportions and are not the same recording.

    Each mechanism carries a measured quantity, the pre-registered bar it had to
    cross, and whether it crossed. Nothing is named because it is plausible, and
    the ranking is by how far past its own bar each measurement sits — the only
    scale on which a percentage-point gap and a ratio can be ordered at all.

    Two constraints. Instrument mix is tested by re-measuring the depth ratio
    *inside* single instruments, so a collapse from 53x to 2x is evidence about
    the mix and not about the overlay. And ``G`` is asserted only as a
    correlation surviving inside one instrument: no recorded field says the
    overlay state caused the recorder to keep looking.
    """
    rows: list[dict] = []
    depth = _ratio(
        _median(selected, "observation_count"),
        _median(declined, "observation_count"),
    )
    poll = _ratio(
        _median(declined, "median_inter_observation_seconds"),
        _median(selected, "median_inter_observation_seconds"),
    )
    within = _within_instrument(selected, declined)
    mix = _mix_evidence(selected, declined, overall_depth=depth, within=within)
    rows.append(mix)
    rows.append({
        "source": BIAS_POLL_CADENCE,
        "measured": poll,
        "measured_is": (
            "DECLINED_MEDIAN_INTER_OBSERVATION_INTERVAL_DIVIDED_BY_SELECTED"
        ),
        "bar": BIAS_MIN_CADENCE_RATIO,
        # Strictly past the bar, not at it: the publication guard calls a ratio
        # of exactly CADENCE_MAX_RATIO comparable, so a mechanism asserted at
        # equality would be named from a cadence gap the same code treats as no
        # gap at all.
        "crossed": poll is not None and poll > BIAS_MIN_CADENCE_RATIO,
        "evidence": {
            "selected_median_inter_observation_sec": _median(
                selected, "median_inter_observation_seconds"),
            "declined_median_inter_observation_sec": _median(
                declined, "median_inter_observation_seconds"),
            "selected_median_observations_per_minute": _median(
                selected, "observations_per_minute"),
            "declined_median_observations_per_minute": _median(
                declined, "observations_per_minute"),
        },
    })
    left, right = cohort(selected), cohort(declined)
    for source in (
        BIAS_EVENT_TERMINATION, BIAS_QUOTE_AVAILABILITY,
        BIAS_CONTRACT_TRACKING, BIAS_SESSION_BOUNDARY, BIAS_OTHER,
    ):
        causes = [
            cause for cause, mapped in BIAS_SOURCE_FROM_CAUSE.items()
            if mapped == source
        ]
        share_left = _share(left, causes)
        share_right = _share(right, causes)
        gap = _gap(share_right, share_left)
        rows.append({
            "source": source,
            "measured": None if gap is None else abs(gap),
            "measured_is": (
                "ABSOLUTE_PERCENTAGE_POINT_GAP_BETWEEN_THE_COHORT_SHARES_OF_"
                "EVENTS_WHOSE_OBSERVATION_STOPPED_FOR_A_REASON_IN_THIS_MECHANISM"
            ),
            "bar": BIAS_MIN_STOP_SHARE_GAP_PP,
            "crossed": gap is not None and abs(gap) >= BIAS_MIN_STOP_SHARE_GAP_PP,
            "evidence": {
                "causes": sorted(causes),
                "selected_share_pct": share_left,
                "declined_share_pct": share_right,
                "declined_minus_selected_pp": gap,
            },
        })
    rows.append(_overlay_correlation(within))
    for row in rows:
        row["strength"] = (
            None if row["measured"] is None or not row.get("bar")
            else round(float(row["measured"]) / float(row["bar"]), 3)
        )
    crossed = sorted(
        (row for row in rows if row["crossed"] and row["strength"] is not None),
        key=lambda r: (-r["strength"], r["source"]),
    )
    return {
        "sources_declared": list(BIAS_SOURCES),
        "within_instrument": within,
        "all_market_depth_ratio": depth,
        "all_market_cadence_ratio": poll,
        "rows": sorted(rows, key=lambda r: r["source"]),
        "crossed": [row["source"] for row in crossed],
        "dominant_source": crossed[0]["source"] if crossed else None,
        "dominant_strength": crossed[0]["strength"] if crossed else None,
        "unranked_because": None if crossed else BIAS_UNRANKED,
        "ranked_by": (
            "HOW_FAR_EACH_MEASURED_QUANTITY_SITS_PAST_ITS_OWN_PRE_REGISTERED_BAR"
        ),
        "overlay_correlation_note": BIAS_G_IS_CORRELATION_ONLY,
        "cadence_is_not_performance": CADENCE_IS_NOT_PERFORMANCE,
        "fingerprint": cadence_guard_fingerprint(),
    }


def _share(column: dict, causes: list[str]) -> float | None:
    """The share of one cohort whose stop reason belongs to any of these causes."""
    pct = column.get("cause_pct") or {}
    values = [_num(pct.get(cause)) for cause in causes]
    present = [v for v in values if v is not None]
    return round(sum(present), 2) if present else None


def _within_instrument(selected: list[dict], declined: list[dict]) -> dict:
    """The same depth and cadence ratios measured inside single instruments.

    Only instruments holding at least the pre-registered event floor in *both*
    cohorts qualify. An instrument with eight selected events and none declined
    says nothing about the overlay and everything about the mix, which is the
    distinction this function exists to make.
    """
    names = sorted({
        str(row.get("instrument") or "") for row in [*selected, *declined]
        if row.get("instrument")
    })
    rows: list[dict] = []
    for name in names:
        left = [r for r in selected if str(r.get("instrument") or "") == name]
        right = [r for r in declined if str(r.get("instrument") or "") == name]
        comparable = (
            len(left) >= BIAS_MIN_EVENTS_PER_COHORT
            and len(right) >= BIAS_MIN_EVENTS_PER_COHORT
        )
        rows.append({
            "instrument": name,
            "selected_events": len(left),
            "declined_events": len(right),
            "both_cohorts_present": bool(left) and bool(right),
            "clears_event_floor_both_cohorts": comparable,
            "event_floor": BIAS_MIN_EVENTS_PER_COHORT,
            "selected_median_observations": _median(left, "observation_count"),
            "declined_median_observations": _median(right, "observation_count"),
            "depth_ratio": _ratio(
                _median(left, "observation_count"),
                _median(right, "observation_count"),
            ),
            "selected_median_inter_observation_sec": _median(
                left, "median_inter_observation_seconds"),
            "declined_median_inter_observation_sec": _median(
                right, "median_inter_observation_seconds"),
            "cadence_ratio": _ratio(
                _median(right, "median_inter_observation_seconds"),
                _median(left, "median_inter_observation_seconds"),
            ),
            "cadence_guard": cadence_guard(left, right),
            "comparison": compare(left, right) if comparable else None,
            "stopped_by": {
                "selected": cohort(left)["stopped_by"],
                "declined": cohort(right)["stopped_by"],
            },
        })
    usable = [row for row in rows if row["clears_event_floor_both_cohorts"]]
    return {
        "per_instrument": rows,
        "instruments_clearing_floor": [row["instrument"] for row in usable],
        "median_within_instrument_depth_ratio": _median_of(
            [row["depth_ratio"] for row in usable]),
        "median_within_instrument_cadence_ratio": _median_of(
            [row["cadence_ratio"] for row in usable]),
        "event_floor": BIAS_MIN_EVENTS_PER_COHORT,
        "only_these_are_answerable": (
            "AN_INSTRUMENT_HOLDING_EVENTS_IN_ONE_COHORT_ONLY_MEASURES_THE_MIX_"
            "AND_NOT_THE_OVERLAY_SO_IT_IS_REPORTED_AND_NEVER_COMPARED"
        ),
    }


def _mix_evidence(
    selected: list[dict], declined: list[dict], *,
    overall_depth: float | None, within: dict,
) -> dict:
    """Instrument mix: how much of the depth gap is which contracts each cohort holds.

    Two independent measurements, either of which crosses the bar. How much of
    each cohort sits in an instrument the other cohort never appears in — a
    cohort that is 80% single-cohort instruments is a different universe, not a
    different filter decision. And how far the depth ratio collapses once the
    instrument is held fixed, which is the direct test.
    """
    sel_names = {str(r.get("instrument") or "") for r in selected}
    dec_names = {str(r.get("instrument") or "") for r in declined}
    sel_only = sum(
        1 for r in selected
        if str(r.get("instrument") or "") not in dec_names)
    dec_only = sum(
        1 for r in declined
        if str(r.get("instrument") or "") not in sel_names)
    sel_pct = (
        None if not selected else round(100.0 * sel_only / len(selected), 2))
    dec_pct = (
        None if not declined else round(100.0 * dec_only / len(declined), 2))
    inner = within.get("median_within_instrument_depth_ratio")
    collapse = _ratio(overall_depth, inner)
    disjoint = max(v for v in (sel_pct or 0.0, dec_pct or 0.0))
    crossed = bool(
        (collapse is not None and collapse >= BIAS_MIN_DEPTH_RATIO)
        or disjoint >= 50.0
    )
    measured = max(
        v for v in (
            collapse if collapse is not None else 0.0,
            disjoint / 50.0 * BIAS_MIN_DEPTH_RATIO,
        )
    )
    return {
        "source": BIAS_INSTRUMENT_MIX,
        "measured": round(measured, 3),
        "measured_is": (
            "THE_LARGER_OF_THE_FACTOR_BY_WHICH_THE_DEPTH_RATIO_COLLAPSES_WHEN_"
            "INSTRUMENT_IS_HELD_FIXED_AND_THE_SINGLE_COHORT_INSTRUMENT_SHARE_"
            "SCALED_TO_THE_SAME_BAR"
        ),
        "bar": BIAS_MIN_DEPTH_RATIO,
        "crossed": crossed,
        "evidence": {
            "all_market_depth_ratio": overall_depth,
            "median_within_instrument_depth_ratio": inner,
            "collapse_factor": collapse,
            "pct_selected_events_in_instruments_with_no_declined_event": sel_pct,
            "pct_declined_events_in_instruments_with_no_selected_event": dec_pct,
            "instruments_clearing_floor": within.get(
                "instruments_clearing_floor"),
        },
    }


def _overlay_correlation(within: dict) -> dict:
    """Whether a cadence gap survives inside a single instrument. Correlation only.

    The strongest statement the recording supports about the overlay, and it is
    still not causation: the selected cohort sits on the continuously polled
    contracts, and nothing recorded says which of the two came first.
    """
    ratios = [
        row for row in (within.get("per_instrument") or [])
        if row.get("clears_event_floor_both_cohorts")
        and row.get("cadence_ratio") is not None
    ]
    worst = max(
        (row for row in ratios), key=lambda r: float(r["cadence_ratio"]),
        default=None,
    )
    measured = None if worst is None else float(worst["cadence_ratio"])
    return {
        "source": BIAS_OVERLAY_CORRELATED,
        "measured": measured,
        "measured_is": (
            "THE_LARGEST_WITHIN_INSTRUMENT_DECLINED_OVER_SELECTED_CADENCE_RATIO_"
            "AMONG_INSTRUMENTS_CLEARING_THE_EVENT_FLOOR_IN_BOTH_COHORTS"
        ),
        "bar": BIAS_MIN_CADENCE_RATIO,
        "crossed": measured is not None and measured > BIAS_MIN_CADENCE_RATIO,
        "evidence": {
            "instrument": None if worst is None else worst["instrument"],
            "selected_events": None if worst is None else worst["selected_events"],
            "declined_events": None if worst is None else worst["declined_events"],
            "instruments_tested": [row["instrument"] for row in ratios],
            "note": BIAS_G_IS_CORRELATION_ONLY,
        },
    }


def _ratio(left: object, right: object) -> float | None:
    a, b = _num(left), _num(right)
    if a is None or b is None or b == 0:
        return None
    return round(a / b, 3)


def _median_of(values: list) -> float | None:
    present = sorted(v for v in (_num(v) for v in values) if v is not None)
    if not present:
        return None
    mid = len(present) // 2
    if len(present) % 2:
        return round(present[mid], 3)
    return round((present[mid - 1] + present[mid]) / 2.0, 3)


def taxonomy_check(records: list[dict]) -> dict:
    """That every event carries exactly one reason drawn from the frozen taxonomy."""
    unclassified = sum(
        1 for r in records
        if str(r.get("observation_stopped_because") or "") not in STOP_REASONS
    )
    seen: dict[str, int] = {}
    for row in records:
        key = str(row.get("event_id"))
        seen[key] = seen.get(key, 0) + 1
    outcome_fields = sorted(
        {field for row in records for field in FORBIDDEN_FIELDS if field in row}
    )
    return {
        "events": len(records),
        "classified": len(records) - unclassified,
        "unclassified": unclassified,
        "duplicate_event_ids": sum(1 for n in seen.values() if n > 1),
        "reasons_declared": list(STOP_REASONS),
        "every_event_classified_exactly_once": (
            unclassified == 0 and all(n == 1 for n in seen.values())
        ),
        "outcome_fields_present": outcome_fields,
        "no_outcome_field_present": not outcome_fields,
    }


def _median(rows: list[dict], field: str) -> float | None:
    values = sorted(
        v for v in (_num(r.get(field)) for r in rows) if v is not None
    )
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return round(values[mid], 2)
    return round((values[mid - 1] + values[mid]) / 2.0, 2)


def _pct(rows: list[dict], field: str) -> float | None:
    """Percentage of the events the field is answerable for, not of all events.

    ``None`` means the question could not be asked of that event — an event with
    no executable book has no coverage horizon to be inside or outside — and
    counting those as ``False`` would report an unanswerable question as a
    negative answer.
    """
    answered = [r for r in rows if r.get(field) is not None]
    if not answered:
        return None
    true = sum(1 for r in answered if bool(r.get(field)))
    return round(100.0 * true / len(answered), 2)


def _gap(left: object, right: object) -> float | None:
    a, b = _num(left), _num(right)
    if a is None or b is None:
        return None
    return round(a - b, 2)


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
