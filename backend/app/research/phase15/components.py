"""§16 the A+ components, derived only from fields the row actually carries.

The rule that makes this module honest is what it *refuses* to score. On
underlying-only history there is no chain, so vehicle edge, tradability and the
premium half of entry quality are not measurable — and the correct output for an
unmeasurable dimension is ``None`` with a reason, not a neutral 50. A neutral 50
is invented evidence, and six of them average to a confident-looking 50 that
means nothing.

Each component returns its value and the fields it was derived from, so a score
can always be traced back to the data behind it.
"""
from __future__ import annotations

# Reasons a dimension could not be scored. Named rather than blank so a report
# can say which data would have to be captured to fill the gap.
NEEDS_CHAIN = "needs the live option chain (bid/ask, OI, IV, delta); the "\
    "underlying-only pool has none"
NEEDS_PREMIUM = "needs the option's own premium at setup; not present on this row"


def _score_from(value: float | None, low: float, high: float) -> float | None:
    """Linear 0-100 placement of a reading between two reference points."""
    if value is None:
        return None
    if high == low:
        return None
    pos = (float(value) - low) / (high - low)
    return round(100.0 * max(0.0, min(1.0, pos)), 1)


def market_edge(row: dict) -> tuple[float | None, str]:
    """Regime and higher-timeframe agreement, which history can measure."""
    regime = (row.get("regime") or "").upper()
    htf = (row.get("htf_trend") or "").upper()
    side = row.get("side")
    strength = row.get("htf_strength")
    if not regime and not htf:
        return None, "no regime or HTF state recorded on this candidate"
    score = 0.0
    parts = []
    if regime in ("TRENDING", "BREAKOUT"):
        score += 50.0
        parts.append(f"regime {regime}")
    elif regime:
        parts.append(f"regime {regime} (no credit)")
    aligned = (htf in ("UP", "CE", "LONG") and side == "LONG") or \
              (htf in ("DOWN", "PE", "SHORT") and side == "SHORT")
    if aligned:
        score += 30.0
        parts.append(f"HTF {htf} agrees with {side}")
    elif htf:
        parts.append(f"HTF {htf} does not agree with {side}")
    if strength is not None:
        score += 0.2 * min(100.0, max(0.0, float(strength)))
        parts.append(f"htf_strength {strength}")
    return round(min(100.0, score), 1), "; ".join(parts)


def entry_edge(row: dict) -> tuple[float | None, str]:
    """How much of the move is left and whether the stop sits outside noise.

    ``risk_over_noise`` is the measurable half of entry quality: a stop inside
    one candle's ordinary range is resolved by noise regardless of direction.
    The premium half — was the option chased — is not on an underlying row.
    """
    ron = row.get("risk_over_noise")
    trigger = (row.get("entry_trigger") or "").upper()
    if ron is None and not trigger:
        return None, NEEDS_PREMIUM
    score = 0.0
    parts = []
    placed = _score_from(ron, 0.5, 2.0)
    if placed is not None:
        score += 0.7 * placed
        parts.append(f"stop {ron}x one-candle noise")
    if "PULLBACK" in trigger:
        score += 30.0
        parts.append("pullback entry")
    elif trigger:
        parts.append(f"{trigger} entry")
    return round(min(100.0, score), 1), "; ".join(parts) or NEEDS_PREMIUM


def room(row: dict) -> tuple[float | None, str]:
    """Reward:risk actually available to the plan."""
    rr = row.get("reward_risk")
    placed = _score_from(rr, 1.0, 2.5)
    if placed is None:
        return None, "no reward:risk on this candidate"
    return placed, f"reward:risk {rr}"


def vehicle_edge(row: dict) -> tuple[float | None, str]:
    """CE vs PE vs no-trade economics. Chain-only; never approximated."""
    if row.get("vehicle_edge_score") is not None:
        return round(float(row["vehicle_edge_score"]), 1), "scored from a captured chain"
    return None, NEEDS_CHAIN


def tradability(row: dict) -> tuple[float | None, str]:
    """Spread, liquidity and premium economics. Chain-only.

    This is the component the cost audit made non-negotiable: a round trip
    costing more than a tenth of the premium removes any realistic edge, and
    that fact is knowable only from a real book.
    """
    if row.get("tradability_score") is not None:
        return round(float(row["tradability_score"]), 1), "scored from a captured chain"
    return None, NEEDS_CHAIN


def data_quality(row: dict) -> tuple[float | None, str]:
    """How much of the candidate's own evidence was present.

    Scored from the share of the graded dimensions that were measurable, so a
    row scored on two components out of six cannot present the same confidence
    as one scored on six.
    """
    measured = sum(1 for fn in (market_edge, entry_edge, room, vehicle_edge,
                                tradability)
                   if fn(row)[0] is not None)
    return round(100.0 * measured / 5.0, 1), f"{measured}/5 dimensions measurable"


def score_row(row: dict) -> dict:
    """Every component for one candidate, with the reason for each gap."""
    values: dict[str, float | None] = {}
    reasons: dict[str, str] = {}
    for name, fn in (("market_edge", market_edge), ("vehicle_edge", vehicle_edge),
                     ("entry_edge", entry_edge), ("tradability", tradability),
                     ("room", room), ("data_quality", data_quality)):
        value, why = fn(row)
        values[name] = value
        reasons[name] = why
    return {"components": values, "reasons": reasons}
