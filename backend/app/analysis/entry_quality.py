"""Where in the move was this signal bought? — Phase 12A §11, RESEARCH ONLY.

The 26 Aug card the user photographed printed BUY at ~328 after the premium had
already run from ~300, reached ~348, and round-tripped to 325. Nothing in the
recorded data said "this was bought late": the plan's entry zone existed, but the
distance between the zone and what was actually paid was never measured, so a
chased purchase reached the screen looking identical to a clean one.

This module measures that distance at signal time and nothing else:

* what the plan wants to pay (the entry zone) against what the leg costs now;
* how far the premium has already expanded from the move's own base;
* how much of the expected move is already consumed;
* the distance from the local premium high, so buying the top is visible;
* ATR extension of the underlying, which is the same question in the underlying;
* the room that is left to T1 after paying today's price.

and returns one of IDEAL / GOOD / ACCEPTABLE / CHASED / SEVERELY_CHASED with the
inputs that produced it.

It classifies. It does not gate, delay, re-price, or suppress anything: the
production chase guard, strike selector, premium floor and entry logic are frozen
by the phase's own safety list, so a SEVERELY_CHASED signal still reaches the
card exactly as before, now carrying the label. Whether a chased entry should be
delayed is Phase 13's question, and it needs the outcome table this label makes
possible before it can be answered honestly — pullback-waiting is not assumed to
be better, and on the measured 7.8-minute median time-to-T1 many winners never
offer a retest at all.

Deliberately separate from ``app.research.phase7.paths.classify_chase``, which
answers the same question *after the fact*: it ranks a signal against the best
entry that became available in the following bars, so it cannot run live, and its
cut points are quintiles of whichever dataset it was handed. This one uses only
facts available at the moment the signal prints, and shares its label vocabulary
exactly, so a live label and a backtest label can be compared without a mapping
table.

Missing inputs return UNKNOWN. Nothing here infers a premium path it was not
given.
"""
from __future__ import annotations

from app.config import settings

IDEAL = "IDEAL_ENTRY"
GOOD = "GOOD_ENTRY"
ACCEPTABLE = "ACCEPTABLE_ENTRY"
CHASED = "CHASED_ENTRY"
SEVERELY_CHASED = "SEVERELY_CHASED"
UNKNOWN = "UNKNOWN"
STATES = (IDEAL, GOOD, ACCEPTABLE, CHASED, SEVERELY_CHASED, UNKNOWN)

# States that mean the move being paid for has already happened. §7 reads this to
# raise PREMIUM_ALREADY_EXPANDED as a tradability reason of its own.
EXPANDED = (CHASED, SEVERELY_CHASED)

# Reasons — each names one measured fact.
ABOVE_ZONE = "ABOVE_ENTRY_ZONE"
FAR_ABOVE_ZONE = "FAR_ABOVE_ENTRY_ZONE"
AT_LOCAL_HIGH = "AT_LOCAL_PREMIUM_HIGH"
PREMIUM_EXPANDED = "PREMIUM_EXPANDED_BEFORE_SIGNAL"
MOVE_CONSUMED = "MOVE_ALREADY_CONSUMED"
ATR_EXTENDED = "UNDERLYING_ATR_EXTENDED"
IN_ZONE = "INSIDE_ENTRY_ZONE"
BELOW_ZONE = "BELOW_ENTRY_ZONE"
NO_DATA = "NO_ENTRY_REFERENCE"

# Research bands, in fractions of the plan's own risk (1R) unless stated. They
# are deliberately expressed in R rather than rupees so an index leg and an MCX
# leg are judged on the same scale.
ZONE_GOOD_R = 0.15
ZONE_ACCEPTABLE_R = 0.35
ZONE_CHASED_R = 0.75
# Fraction of the premium's own recent range the signal sits in. 1.0 is the high.
LOCAL_HIGH_FRACTION = 0.85
# Fraction of the distance to T1 already travelled before the signal printed.
MOVE_CONSUMED_FRACTION = 0.5


def _zone_distance_r(premium: float, zone_low: float | None,
                     zone_high: float | None, risk: float) -> tuple[float | None, str]:
    """How far above the plan's own zone the leg is being bought, in R."""
    if zone_high is None and zone_low is None:
        return None, NO_DATA
    high = zone_high if zone_high is not None else zone_low
    low = zone_low if zone_low is not None else zone_high
    if high is None or low is None:
        return None, NO_DATA
    if premium > high:
        return round((premium - high) / risk, 3), ABOVE_ZONE
    if premium < low:
        return round((premium - low) / risk, 3), BELOW_ZONE
    return 0.0, IN_ZONE


def zone_distance_r(premium: float, zone_low: float | None,
                    zone_high: float | None, risk: float) -> float | None:
    """Distance above the published entry zone in R, for callers that only need
    the number (Phase 13A reads it so both labels judge the same premium)."""
    return _zone_distance_r(premium, zone_low, zone_high, risk)[0]


def _local_position(premium: float, low: float | None,
                    high: float | None) -> float | None:
    """Where in the premium's own recent range this purchase sits, 0..1."""
    if low is None or high is None:
        return None
    span = float(high) - float(low)
    if span <= 0:
        return None
    return round(max(0.0, min(1.0, (premium - float(low)) / span)), 3)


def classify(*, premium: float | None,
             entry_zone_low: float | None = None,
             entry_zone_high: float | None = None,
             risk_points: float | None = None,
             first_target: float | None = None,
             premium_recent_low: float | None = None,
             premium_recent_high: float | None = None,
             premium_at_setup: float | None = None,
             underlying_price: float | None = None,
             underlying_ref: float | None = None,
             atr: float | None = None) -> dict:
    """Entry location of one option signal. Evidence, never a block.

    ``premium_at_setup`` is the premium when the setup formed, if the caller has
    it; ``underlying_ref`` with ``atr`` gives the same reading in the underlying
    (how many ATRs past the reference the price already is).
    """
    reasons: list[str] = []
    if premium is None or float(premium) <= 0 or not risk_points or float(risk_points) <= 0:
        return {
            "state": UNKNOWN,
            "reasons": [NO_DATA],
            "premium": None if premium is None else float(premium),
            "entry_zone": [entry_zone_low, entry_zone_high],
            "zone_distance_r": None,
            "premium_expansion_pct": None,
            "local_range_position": None,
            "move_consumed_pct": None,
            "atr_extension": None,
            "room_to_target_r": None,
            "research_only": True,
        }

    prem = float(premium)
    risk = float(risk_points)

    zone_r, zone_reason = _zone_distance_r(prem, entry_zone_low, entry_zone_high, risk)
    reasons.append(zone_reason)
    if zone_r is not None and zone_r > ZONE_CHASED_R:
        reasons.append(FAR_ABOVE_ZONE)

    expansion_pct = None
    if premium_at_setup and float(premium_at_setup) > 0:
        expansion_pct = round(100.0 * (prem - float(premium_at_setup))
                              / float(premium_at_setup), 2)
        if expansion_pct >= settings.entry_quality_expansion_pct:
            reasons.append(PREMIUM_EXPANDED)

    local = _local_position(prem, premium_recent_low, premium_recent_high)
    if local is not None and local >= LOCAL_HIGH_FRACTION:
        reasons.append(AT_LOCAL_HIGH)

    consumed = None
    if first_target and premium_at_setup:
        total = float(first_target) - float(premium_at_setup)
        if total > 0:
            consumed = round(100.0 * (prem - float(premium_at_setup)) / total, 1)
            if consumed >= 100.0 * MOVE_CONSUMED_FRACTION:
                reasons.append(MOVE_CONSUMED)

    atr_ext = None
    if underlying_price and underlying_ref and atr and float(atr) > 0:
        atr_ext = round((float(underlying_price) - float(underlying_ref)) / float(atr), 3)
        if abs(atr_ext) >= settings.entry_quality_atr_extension:
            reasons.append(ATR_EXTENDED)

    room_r = None
    if first_target:
        room_r = round((float(first_target) - prem) / risk, 3)

    state = _state(zone_r, zone_reason, reasons)
    return {
        "state": state,
        "reasons": reasons,
        "premium": prem,
        "entry_zone": [entry_zone_low, entry_zone_high],
        "zone_distance_r": zone_r,
        "premium_expansion_pct": expansion_pct,
        "local_range_position": local,
        "move_consumed_pct": consumed,
        "atr_extension": atr_ext,
        "room_to_target_r": room_r,
        "research_only": True,
        "note": ("entry location is recorded beside the call as evidence; the "
                 "production chase guard, strike selector and entry logic are "
                 "unchanged and no signal is delayed or suppressed by this label"),
    }


def _state(zone_r: float | None, zone_reason: str, reasons: list[str]) -> str:
    """The label, decided by distance from the plan's own zone first.

    Corroborating evidence (local high, expansion, consumed move, ATR extension)
    can only worsen the label by one step. It cannot invent a chase on its own:
    a leg bought inside its zone in a strong trend is an extended market, not a
    chased entry, and conflating the two is how a good trend trade gets labelled
    a mistake.
    """
    if zone_r is None:
        return UNKNOWN
    corroborating = sum(1 for r in (AT_LOCAL_HIGH, PREMIUM_EXPANDED,
                                    MOVE_CONSUMED, ATR_EXTENDED) if r in reasons)
    if zone_reason == BELOW_ZONE:
        # Cheaper than the plan asked for. That is not a chase whatever the
        # underlying is doing.
        return IDEAL
    if zone_r <= 0.0:
        base = IDEAL if corroborating == 0 else GOOD
    elif zone_r <= ZONE_GOOD_R:
        base = GOOD
    elif zone_r <= ZONE_ACCEPTABLE_R:
        base = ACCEPTABLE
    elif zone_r <= ZONE_CHASED_R:
        base = CHASED
    else:
        return SEVERELY_CHASED
    if corroborating >= 2:
        return _worse(base)
    return base


def _worse(state: str) -> str:
    order = [IDEAL, GOOD, ACCEPTABLE, CHASED, SEVERELY_CHASED]
    idx = order.index(state)
    return order[min(idx + 1, len(order) - 1)]
