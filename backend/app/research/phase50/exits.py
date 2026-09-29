"""Phase 50 §2 — pre-registered exit policies, frozen and fingerprinted before measurement.

The previous board exited every event at the last executable book on its path.
That is where the capture stopped looking, not where a decision closed the leg:
on the first market-open session it produced hold times from 0 to 1,702 seconds
on identical logic, so its net graded the recorder's coverage rather than any
rule a trader could follow. It stays, as a coverage diagnostic, under
``COVERAGE_ENDPOINT`` — and it is never the primary net.

What replaces it is a small set of policies written down here, each with its
parameters in the source and its own fingerprint, applied identically to the
selected and the declined group. The set is deliberately small: six policies is
a comparison, and a grid of two hundred is a search for the one that happens to
win on 53 events.

**How each policy avoids hindsight.** Every policy walks the executable books in
time order and stops at the first one satisfying a condition written before the
walk. A condition may read only what the walk has already passed — the running
peak, the running trough, whether T1 has been reached — never the final outcome,
the eventual MFE, or a later spread. The path metrics reported with an exit are
computed over the books **up to that exit** for the same reason: an MAE that
includes an excursion the policy had already closed out of describes a trade
nobody held.

**What a policy is not allowed to do to get an answer.** A horizon is answered by
the first executable book at or after it and only while that book is within
``HORIZON_TOLERANCE_SEC`` of it. There is no forward-fill, no interpolation, no
nearest-stale substitution, no other strike and no other contract: an
unanswerable horizon is ``UNRESOLVED``, which is a fact about the capture rather
than a fill nobody could have got.
"""
from __future__ import annotations

import hashlib

from app.research.phase35 import FUTURES, SHORT
from app.research.phase35 import book
from app.research.phase35 import path as p35path
from app.research.phase50 import (
    COVERAGE_ENDPOINT,
    HORIZON_TOLERANCE_SEC,
    MAX_HOLD_SEC,
    NO_HINDSIGHT,
    RESOLVED,
    STOP_COST_MULTIPLE,
    TRAIL_GIVEBACK_FRACTION_OF_PEAK,
    UNRESOLVED_NO_QUOTE_AT_HORIZON,
    UNRESOLVED_POLICY_NEVER_TRIGGERED,
)

# --------------------------------------------------------------- the policy set
FIXED_5M = "FIXED_TIME_5M"
FIXED_15M = "FIXED_TIME_15M"
FIXED_30M = "FIXED_TIME_30M"
FIXED_60M = "FIXED_TIME_60M"
T1_THEN_BREAKEVEN = "T1_THEN_BREAKEVEN"
T1_THEN_TRAIL = "T1_THEN_TRAIL"
STOP_THEN_TIMEOUT = "STOP_AT_2X_COST_ELSE_TIMEOUT_60M"

# Ordered so every table prints the policies in the same order, shortest hold
# first. The coverage endpoint is last and labelled, never mixed in among them.
POLICIES: tuple[str, ...] = (
    FIXED_5M, FIXED_15M, FIXED_30M, FIXED_60M,
    T1_THEN_BREAKEVEN, T1_THEN_TRAIL, STOP_THEN_TIMEOUT,
)
ALL_POLICIES: tuple[str, ...] = (*POLICIES, COVERAGE_ENDPOINT)

_HOLD_MIN: dict[str, float] = {
    FIXED_5M: 5.0, FIXED_15M: 15.0, FIXED_30M: 30.0, FIXED_60M: 60.0,
}

RULES: dict[str, str] = {
    FIXED_5M: "exit at the first executable book at or after 5 minutes held",
    FIXED_15M: "exit at the first executable book at or after 15 minutes held",
    FIXED_30M: "exit at the first executable book at or after 30 minutes held",
    FIXED_60M: "exit at the first executable book at or after 60 minutes held",
    T1_THEN_BREAKEVEN: (
        "hold; once the frozen T1 has been reached, exit at the first later "
        "book at or below cost-adjusted breakeven; otherwise exit at the "
        "60-minute timeout"
    ),
    T1_THEN_TRAIL: (
        "hold; once the frozen T1 has been reached, exit at the first later "
        "book that has given back half of the running peak; otherwise exit at "
        "the 60-minute timeout"
    ),
    STOP_THEN_TIMEOUT: (
        "exit at the first book at or below twice the measured round trip "
        "against the position; otherwise exit at the 60-minute timeout"
    ),
    COVERAGE_ENDPOINT: (
        "exit at the last executable book on the path — a coverage diagnostic, "
        "not a trading exit, because the path ends where the capture stopped "
        "observing the contract"
    ),
}

PARAMS: dict[str, dict] = {
    FIXED_5M: {"hold_minutes": 5.0, "tolerance_sec": HORIZON_TOLERANCE_SEC},
    FIXED_15M: {"hold_minutes": 15.0, "tolerance_sec": HORIZON_TOLERANCE_SEC},
    FIXED_30M: {"hold_minutes": 30.0, "tolerance_sec": HORIZON_TOLERANCE_SEC},
    FIXED_60M: {"hold_minutes": 60.0, "tolerance_sec": HORIZON_TOLERANCE_SEC},
    T1_THEN_BREAKEVEN: {
        "target": "PHASE35_FROZEN_T1", "then": "COST_ADJUSTED_BREAKEVEN",
        "timeout_sec": MAX_HOLD_SEC, "tolerance_sec": HORIZON_TOLERANCE_SEC,
    },
    T1_THEN_TRAIL: {
        "target": "PHASE35_FROZEN_T1",
        "giveback_fraction_of_peak": TRAIL_GIVEBACK_FRACTION_OF_PEAK,
        "timeout_sec": MAX_HOLD_SEC, "tolerance_sec": HORIZON_TOLERANCE_SEC,
    },
    STOP_THEN_TIMEOUT: {
        "stop_cost_multiple": STOP_COST_MULTIPLE,
        "timeout_sec": MAX_HOLD_SEC, "tolerance_sec": HORIZON_TOLERANCE_SEC,
    },
    COVERAGE_ENDPOINT: {"exit_at": "LAST_EXECUTABLE_BOOK_ON_THE_PATH"},
}

# Why an exit happened, on the row. Two policies can exit at the same instant for
# different reasons and a column of prices cannot tell them apart.
BY_HORIZON = "THE_POLICYS_HOLD_TIME_ELAPSED"
BY_BREAKEVEN = "GAVE_BACK_TO_COST_ADJUSTED_BREAKEVEN_AFTER_REACHING_T1"
BY_TRAIL = "GAVE_BACK_HALF_OF_THE_RUNNING_PEAK_AFTER_REACHING_T1"
BY_STOP = "REACHED_THE_PRE_REGISTERED_STOP"
BY_TIMEOUT = "THE_POLICYS_TIMEOUT_ELAPSED_WITHOUT_ITS_CONDITION_FIRING"
BY_PATH_END = "THE_LAST_BOOK_THE_CAPTURE_RECORDED_FOR_THIS_CONTRACT"


def fingerprint(policy_id: str) -> str:
    """One policy's frozen identity: its id, its rule and its parameters."""
    params = PARAMS.get(policy_id) or {}
    flat = ";".join(f"{k}={params[k]}" for k in sorted(params))
    raw = "|".join([policy_id, RULES.get(policy_id, ""), flat, NO_HINDSIGHT])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def set_fingerprint() -> str:
    """The whole declared set, so a report cannot silently gain a policy."""
    raw = "|".join(f"{p}:{fingerprint(p)}" for p in ALL_POLICIES)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def declared() -> list[dict]:
    """The policy set as data, for the report and the API to publish verbatim."""
    return [
        {
            "policy_id": policy,
            "rule": RULES.get(policy),
            "params": dict(PARAMS.get(policy) or {}),
            "fingerprint": fingerprint(policy),
            "is_coverage_diagnostic": policy == COVERAGE_ENDPOINT,
        }
        for policy in ALL_POLICIES
    ]


def apply(
    policy_id: str,
    *,
    entry_price: float,
    entry_ts: float,
    vehicle: str,
    direction: str,
    cost_points: float | None,
    samples: list[dict],
) -> dict:
    """One event's outcome under one policy, or an explicit unresolved reason.

    ``samples`` must already be the executable books after the entry, in time
    order, as :func:`app.research.phase50.resolve.executable_only` returns them:
    a policy never sees a book whose required side was unquoted, so it cannot
    exit at a midpoint or a print however its condition is written.

    The walk is single-pass and stops at the exit. Everything reported —
    ``mfe_pct``, ``mae_pct``, the target flags, the giveback — is measured over
    the books up to that instant, because a leg the policy had already closed
    was not exposed to what came later.
    """
    base = {
        "exit_policy_id": policy_id,
        "exit_policy_fingerprint": fingerprint(policy_id),
        "exit_policy_rule": RULES.get(policy_id),
        "resolution_status": RESOLVED,
        "exit_side": None,
        "exit_price": None,
        "exit_ts": None,
        "exit_reason": None,
        "exit_sample_source": None,
        "hold_seconds": None,
        "gross_pct": None,
        "net_pct": None,
        "mfe_pct": None,
        "mae_pct": None,
        "t1_hit": None,
        "t2_hit": None,
        "t3_hit": None,
        "giveback_pct": None,
        "samples_walked": 0,
        "forward_samples": len(samples),
        "no_hindsight": NO_HINDSIGHT,
        "reason": None,
    }
    if entry_price <= 0 or not samples:
        return {**base, "resolution_status": UNRESOLVED_NO_QUOTE_AT_HORIZON,
                "reason": "no executable book after the entry to price any "
                          "policy against"}
    cost_pct = (
        None if cost_points is None
        else round(100.0 * float(cost_points) / float(entry_price), 4)
    )
    t1, t2, t3 = p35path.targets_pct(vehicle, cost_pct=cost_pct)
    sign = -1.0 if (vehicle == FUTURES and str(direction) == SHORT) else 1.0
    horizon = _horizon_ts(policy_id, entry_ts=entry_ts)

    peak = 0.0
    trough = 0.0
    hit = [False, False, False]
    walked = 0
    chosen: dict | None = None
    reason: str | None = None
    for sample in samples:
        fill = sample.get("fill") or book.exit_fill(
            sample, vehicle=vehicle, direction=direction)
        price = fill.get("price")
        ts = _num(sample.get("ts"))
        if price is None or ts is None:
            continue
        gross = round(
            100.0 * sign * (float(price) - float(entry_price)) / float(entry_price),
            4,
        )
        walked += 1
        # State the conditions may read: only what this walk has already seen.
        armed = hit[0]
        peak_before = peak
        if gross > peak:
            peak = gross
        if gross < trough:
            trough = gross
        for i, level in enumerate((t1, t2, t3)):
            if level > 0 and gross >= level:
                hit[i] = True
        reason = _triggered(
            policy_id, ts=ts, gross=gross, horizon=horizon, armed=armed,
            peak_before=peak_before, cost_pct=cost_pct,
        )
        if reason is not None:
            chosen = {"fill": fill, "ts": ts, "gross": gross,
                      "source": sample.get("sample_source")}
            break
    if chosen is None:
        return {
            **base,
            "resolution_status": (
                UNRESOLVED_NO_QUOTE_AT_HORIZON if policy_id in _HOLD_MIN
                else UNRESOLVED_POLICY_NEVER_TRIGGERED
            ),
            "samples_walked": walked,
            "reason": (
                "the path holds no executable book within tolerance of this "
                "policy's exit instant"
            ),
        }
    if reason in (BY_HORIZON, BY_TIMEOUT) and horizon is not None and (
            chosen["ts"] - horizon) > HORIZON_TOLERANCE_SEC:
        # The first book at or after the clock's expiry arrived too late to be
        # its fill. Answering the 15-minute row from a book 20 minutes late
        # would be a forward-fill wearing a horizon's name — and the same is
        # true of a timeout, which is why this applies to both.
        return {
            **base,
            "resolution_status": UNRESOLVED_NO_QUOTE_AT_HORIZON,
            "samples_walked": walked,
            "reason": (
                f"the first executable book after this horizon arrived "
                f"{round(chosen['ts'] - horizon)}s late, beyond the declared "
                f"{round(HORIZON_TOLERANCE_SEC)}s tolerance"
            ),
        }
    gross = chosen["gross"]
    return {
        **base,
        "exit_side": chosen["fill"].get("side"),
        "exit_price": chosen["fill"].get("price"),
        "exit_ts": chosen["ts"],
        "exit_reason": reason,
        "exit_sample_source": chosen["source"],
        "hold_seconds": round(chosen["ts"] - float(entry_ts), 3),
        "gross_pct": gross,
        "net_pct": book.net_pct(gross, cost_points, float(entry_price)),
        "mfe_pct": round(peak, 4),
        "mae_pct": round(trough, 4),
        "t1_hit": hit[0],
        "t2_hit": hit[1],
        "t3_hit": hit[2],
        "giveback_pct": round(max(0.0, peak - gross), 4),
        "samples_walked": walked,
        "targets_pct": {"t1": t1, "t2": t2, "t3": t3},
        "cost_pct_of_entry": cost_pct,
    }


def _horizon_ts(policy_id: str, *, entry_ts: float) -> float | None:
    """The instant this policy's clock runs out: its hold, or its timeout."""
    held = _HOLD_MIN.get(policy_id)
    if held is not None:
        return float(entry_ts) + held * 60.0
    if policy_id in (T1_THEN_BREAKEVEN, T1_THEN_TRAIL, STOP_THEN_TIMEOUT):
        return float(entry_ts) + MAX_HOLD_SEC
    return None


def _triggered(
    policy_id: str, *, ts: float, gross: float, horizon: float | None,
    armed: bool, peak_before: float, cost_pct: float | None,
) -> str | None:
    """Whether this book closes the leg under this policy, and why.

    ``armed`` and ``peak_before`` are the state *before* this book was applied,
    so a single book cannot both reach T1 and be the giveback that T1 armed —
    which would let one instant satisfy a two-step rule.
    """
    if policy_id == COVERAGE_ENDPOINT:
        return None
    if policy_id in _HOLD_MIN:
        return BY_HORIZON if horizon is not None and ts >= horizon else None
    if policy_id == T1_THEN_BREAKEVEN:
        if armed and cost_pct is not None and gross <= cost_pct:
            return BY_BREAKEVEN
    elif policy_id == T1_THEN_TRAIL:
        if armed and peak_before > 0 and gross <= peak_before * (
                1.0 - TRAIL_GIVEBACK_FRACTION_OF_PEAK):
            return BY_TRAIL
    elif policy_id == STOP_THEN_TIMEOUT:
        if cost_pct is not None and gross <= -STOP_COST_MULTIPLE * cost_pct:
            return BY_STOP
    if horizon is not None and ts >= horizon:
        return BY_TIMEOUT
    return None


def coverage_endpoint(
    *, entry_price: float, entry_ts: float, vehicle: str, direction: str,
    cost_points: float | None, samples: list[dict],
) -> dict:
    """The old behaviour, kept and labelled: exit at the last executable book.

    Reported so path coverage stays visible — how far the capture followed the
    contract, and what the leg was worth there. Never the primary net: the
    instant it exits at is a property of the recorder.
    """
    if entry_price <= 0 or not samples:
        return {
            "exit_policy_id": COVERAGE_ENDPOINT,
            "exit_policy_fingerprint": fingerprint(COVERAGE_ENDPOINT),
            "resolution_status": UNRESOLVED_NO_QUOTE_AT_HORIZON,
            "reason": "no executable book after the entry",
        }
    last = samples[-1]
    fill = last.get("fill") or book.exit_fill(
        last, vehicle=vehicle, direction=direction)
    sign = -1.0 if (vehicle == FUTURES and str(direction) == SHORT) else 1.0
    price = fill.get("price")
    ts = _num(last.get("ts"))
    if price is None or ts is None:
        return {
            "exit_policy_id": COVERAGE_ENDPOINT,
            "exit_policy_fingerprint": fingerprint(COVERAGE_ENDPOINT),
            "resolution_status": UNRESOLVED_NO_QUOTE_AT_HORIZON,
            "reason": "the last book on the path quoted no executable side",
        }
    gross = round(
        100.0 * sign * (float(price) - float(entry_price)) / float(entry_price), 4)
    return {
        "exit_policy_id": COVERAGE_ENDPOINT,
        "exit_policy_fingerprint": fingerprint(COVERAGE_ENDPOINT),
        "exit_policy_rule": RULES[COVERAGE_ENDPOINT],
        "resolution_status": RESOLVED,
        "exit_side": fill.get("side"),
        "exit_price": price,
        "exit_ts": ts,
        "exit_reason": BY_PATH_END,
        "exit_sample_source": last.get("sample_source"),
        "hold_seconds": round(ts - float(entry_ts), 3),
        "gross_pct": gross,
        "net_pct": book.net_pct(gross, cost_points, float(entry_price)),
        "forward_samples": len(samples),
        "is_coverage_diagnostic": True,
    }


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
