"""Should this call be taken now, or is a better price likely? — Phase 13A, RESEARCH ONLY.

Phase 12A §11 measures *where in the move* a signal was bought and stops there:
a SEVERELY_CHASED call still reaches the card as a plain BUY. This module turns
that measurement into the recommendation the user actually asked for after the
26 Aug Crude card — BUY NOW versus WAIT FOR PULLBACK — and records the level it
would have waited for, so Part 4 of the phase can later measure what waiting
cost as well as what it saved.

Four states, decided from signal-time facts only:

``BUY_NOW``
    the leg is at or below the plan's own entry zone, or close enough to it that
    there is no cheaper price to name.
``WAIT_PULLBACK``
    the premium has run past the zone; ``pullback_level`` names the price the
    plan wanted, and ``wait_timeout_minutes`` is how long that offer stands.
``WAIT_CONFIRMATION``
    the price is acceptable but the setup has not confirmed (no confirming close,
    or a trap reading high enough that the breakout is more likely a sweep).
``INVALIDATED``
    waiting is pointless because the trade no longer fits: the premium is at or
    past T1, most of the plan's own room to T1 has already been paid for, or the
    premium is further past the zone than the stop is wide, so even a filled
    pullback would be a different trade from the one planned.

Room is judged as a *fraction of the plan's own distance to T1*, and only once the
premium has actually moved above the planned entry. An absolute floor in R was
tried first and was wrong: this engine places T1 around 0.9R by design, so a
fixed "needs 1R of room" rule invalidated 63% of a three-session book at the
moment of the signal, which measures where the engine puts its targets rather
than where the trade was entered. Whether the plan's T1 is close enough to be
worth taking at all is a different question, and ``a_plus_shadow`` already labels
it (``ROOM_BELOW_MIN_R``).

Two things this module deliberately does NOT do:

* it does not gate, delay, re-price or suppress anything. The production signal,
  chase guard, strike selector, premium floor, stops, targets and exits are
  frozen by the phase's safety list, so ``WAIT_PULLBACK`` is a label printed
  beside a BUY that still prints exactly as before;
* it does not claim waiting is better. The measured median time-to-T1 on this
  book is 7.8 minutes, which means a large share of winners never come back to
  any retest level; ``pullback_level`` exists so the miss can be counted, not
  because the miss is assumed to be small. ``fill_rate_measured`` is False here
  on purpose — that number comes from the study, never from this classifier.
"""
from __future__ import annotations

from app.analysis import entry_quality
from app.config import settings

BUY_NOW = "BUY_NOW"
WAIT_PULLBACK = "WAIT_PULLBACK"
WAIT_CONFIRMATION = "WAIT_CONFIRMATION"
INVALIDATED = "INVALIDATED"
UNKNOWN = "UNKNOWN"
STATES = (BUY_NOW, WAIT_PULLBACK, WAIT_CONFIRMATION, INVALIDATED, UNKNOWN)

# Reasons — one measured fact each.
AT_OR_BELOW_ZONE = "AT_OR_BELOW_ENTRY_ZONE"
NEAR_ZONE = "WITHIN_TOLERANCE_OF_ZONE"
PAST_ZONE = "PREMIUM_PAST_ENTRY_ZONE"
FAR_PAST_ZONE = "PREMIUM_FAR_PAST_ENTRY_ZONE"
NO_ROOM = "ROOM_TO_TARGET_COLLAPSED"
TARGET_PASSED = "PREMIUM_AT_OR_ABOVE_FIRST_TARGET"
NO_CONFIRMATION = "NO_CONFIRMING_CLOSE"
TRAP_READING = "TRAP_PROBABILITY_HIGH"
NO_REFERENCE = "NO_ENTRY_REFERENCE"


def _pullback_level(premium: float, zone_high: float | None,
                    risk: float) -> float | None:
    """The price a waiting entry would bid.

    The plan's own zone top is preferred — it is the level the engine already
    published — and a fraction of R below the current premium is the fallback
    when no zone was published, so the level scales with the trade's own risk
    rather than with the rupee size of the premium.
    """
    if zone_high is not None and 0.0 < float(zone_high) < premium:
        return round(float(zone_high), 2)
    level = premium - settings.entry_location_pullback_r * risk
    return round(level, 2) if level > 0.05 else None


def classify(*, premium: float | None,
             risk_points: float | None,
             entry_zone_low: float | None = None,
             entry_zone_high: float | None = None,
             first_target: float | None = None,
             planned_entry: float | None = None,
             entry_quality_state: str | None = None,
             zone_distance_r: float | None = None,
             confirmation_present: bool | None = None,
             trap_probability: float | None = None) -> dict:
    """The entry state of one long-option BUY. Evidence, never a block.

    ``entry_quality_state`` and ``zone_distance_r`` come from
    :mod:`app.analysis.entry_quality` so the two labels can never disagree about
    the same premium; when they are absent the zone is measured here instead.
    ``confirmation_present`` is the caller's confirming-close reading and
    ``trap_probability`` its trap/sweep reading — both optional, and neither is
    invented when missing. ``planned_entry`` is the price the plan published, which
    is what the remaining-room test measures the current premium against.
    """
    reasons: list[str] = []
    if premium is None or float(premium) <= 0 or not risk_points or float(risk_points) <= 0:
        return _payload(UNKNOWN, [NO_REFERENCE], None, None, None, None)

    prem = float(premium)
    risk = float(risk_points)

    zone_r = zone_distance_r
    if zone_r is None:
        zone_r = entry_quality.zone_distance_r(
            prem, entry_zone_low, entry_zone_high, risk
        )

    room_r = None
    room_fraction = None
    if first_target:
        target = float(first_target)
        room_r = round((target - prem) / risk, 3)
        planned = float(planned_entry) if planned_entry else None
        if planned is not None and target > planned:
            # 1.0 at (or below) the price the plan was written at, 0.0 once the
            # premium has reached T1 without us.
            room_fraction = round(min(1.0, (target - prem) / (target - planned)), 3)

    level = _pullback_level(prem, entry_zone_high, risk)
    distance_pct = (round(100.0 * (prem - level) / prem, 2)
                    if level is not None else None)

    # ---- INVALIDATED: waiting cannot recover this call ----------------------
    if room_r is not None and room_r <= 0:
        reasons.append(TARGET_PASSED)
        return _payload(INVALIDATED, reasons, None, None, room_r, zone_r,
                        room_fraction)
    if (room_fraction is not None
            and room_fraction < settings.entry_location_min_room_frac):
        reasons.append(NO_ROOM)
        return _payload(INVALIDATED, reasons, None, None, room_r, zone_r,
                        room_fraction)
    if zone_r is not None and zone_r > settings.entry_location_invalidate_r:
        reasons.append(FAR_PAST_ZONE)
        return _payload(INVALIDATED, reasons, level, distance_pct, room_r, zone_r,
                        room_fraction)

    # ---- WAIT_PULLBACK: the plan's price is below the screen's price --------
    chased = entry_quality_state in entry_quality.EXPANDED
    past_zone = zone_r is not None and zone_r > settings.entry_location_tolerance_r
    if (chased or past_zone) and level is not None:
        reasons.append(PAST_ZONE)
        if chased and entry_quality_state is not None:
            reasons.append(entry_quality_state)
        return _payload(WAIT_PULLBACK, reasons, level, distance_pct, room_r, zone_r,
                        room_fraction)

    # ---- WAIT_CONFIRMATION: price is fine, the setup is not confirmed -------
    if confirmation_present is False:
        reasons.append(NO_CONFIRMATION)
        return _payload(WAIT_CONFIRMATION, reasons, level, distance_pct, room_r,
                        zone_r, room_fraction)
    if (trap_probability is not None
            and float(trap_probability) >= settings.entry_location_trap_pct):
        reasons.append(TRAP_READING)
        return _payload(WAIT_CONFIRMATION, reasons, level, distance_pct, room_r,
                        zone_r, room_fraction)

    reasons.append(AT_OR_BELOW_ZONE if (zone_r or 0.0) <= 0.0 else NEAR_ZONE)
    return _payload(BUY_NOW, reasons, None, None, room_r, zone_r, room_fraction)


def _payload(state: str, reasons: list[str], level: float | None,
             distance_pct: float | None, room_r: float | None,
             zone_r: float | None, room_fraction: float | None = None) -> dict:
    return {
        "state": state,
        "reasons": reasons,
        "pullback_level": level,
        "pullback_distance_pct": distance_pct,
        "wait_timeout_minutes": (settings.entry_location_wait_minutes
                                 if state in (WAIT_PULLBACK, WAIT_CONFIRMATION)
                                 else None),
        "room_to_target_r": room_r,
        "room_fraction_remaining": room_fraction,
        "zone_distance_r": zone_r,
        "research_only": True,
        "fill_rate_measured": False,
        "note": ("entry state is recorded beside the call; the production signal, "
                 "chase guard, strike selector, stop, targets and exits are "
                 "unchanged, and how often a WAIT_PULLBACK level actually fills "
                 "is measured by the Phase 13 study, not asserted here"),
    }
