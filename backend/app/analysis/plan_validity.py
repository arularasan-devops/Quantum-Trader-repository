"""Is a printed entry plan a trade at all?

One definition, shared by the engine's plan-freshness stamp, the signal journal
and the shadow book, so the dashboard, the outcome tracker and the research
ledgers cannot disagree about what counts as an enterable call.

A plan is unusable when it contradicts the premium it is quoted against: a first
target at or below the entry has no upside to reach, and a stop at or above the
entry is already breached. Both cases resolve instantly and divide R by a
risk of zero, which is how a signal came to be recorded with R = 1e8.
"""

from __future__ import annotations

NO_LEVELS = "NO_LEVELS"
NO_UPSIDE = "TARGET_NOT_ABOVE_ENTRY"
STOP_ABOVE_ENTRY = "STOP_NOT_BELOW_ENTRY"

REASON_TEXT = {
    NO_LEVELS: "no stop or target was produced for this leg",
    NO_UPSIDE: "the first target is not above the live premium",
    STOP_ABOVE_ENTRY: "the stop is not below the live premium",
}


def unusable(premium: float | None, stop: float | None,
             target1: float | None) -> str | None:
    """Why this plan cannot be entered at ``premium``, or None if it can."""
    if premium is None or stop is None or target1 is None:
        return NO_LEVELS
    if float(stop) >= float(premium):
        return STOP_ABOVE_ENTRY
    if float(target1) <= float(premium):
        return NO_UPSIDE
    return None


def describe(reason: str | None) -> str | None:
    """Plain-language form of a reason code, or None when the plan is fine."""
    if reason is None:
        return None
    return REASON_TEXT.get(reason, reason)
