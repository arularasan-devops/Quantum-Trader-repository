"""§11 — the market-wide ranking, and the right to say NO_TRADE_ANYWHERE.

This is deliberately not a confidence score. Nothing here predicts anything:
each component is a measured or explicitly modelled quantity, the weights are
declared in code before any session is ranked, and the output of the whole
exercise is an eligibility ordering — where the market currently offers
measurable room relative to cost — not a probability that a trade will win.

The distinction matters because a single blended number invites the reading
"0.82 means likely profitable". So every ranked row carries its components
separately, an explicit label from :data:`OPPORTUNITY` / :data:`WATCH` /
:data:`REJECT`, and the reason it got the label.

An instrument with no history and no capture is not ranked low: it is
unranked, with the absence named. Ranking an unmeasured instrument below a
measured one implies the two were compared, and they were not.

When nothing clears the bar the answer is :data:`NO_TRADE_ANYWHERE`, and it is
returned as a first-class verdict rather than as an empty list — an empty list
reads as a missing computation, which is how a scanner with nothing to say
ends up looking broken and gets "fixed" into saying something.
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

from app.research.opportunity import (
    NO_HISTORY,
    NO_TRADE_ANYWHERE,
    OPPORTUNITY,
    REJECT,
    SCHEMA_VERSION,
    UNMEASURED,
    WATCH,
)
from app.research.opportunity import screen, shadow, universe

_IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# Declared before any ranking is computed. Each is a measured or modelled
# quantity, never an opinion about direction.
WEIGHTS: dict[str, float] = {
    "movement_vs_cost": 0.40,   # expected room divided by the round trip
    "historical_support": 0.25,  # did anything survive the screen here
    "capture_quality": 0.20,    # how much of the session was actually observed
    "liquidity": 0.15,          # candle volume; a proxy, and labelled as one
}

# An instrument needs expected movement of at least this multiple of the
# round-trip cost before it is called an opportunity. Three is the existing
# flow gate, reused deliberately so two parts of the platform do not carry two
# different definitions of "worth the cost".
OPPORTUNITY_COST_MULTIPLE = 3.0
WATCH_COST_MULTIPLE = 2.0

# The expected move a candidate is actually reaching for, in units of the
# median one-minute bar range: the smallest target in the generated exit
# geometry. Using the bare bar range instead would compare one minute of
# movement against a whole round trip and reject every instrument in the
# market, which is a statement about the units and not about the market.
TARGET_ATR_MULTIPLE = 3.0


def _capture_quality(rows: list[dict], instrument: str) -> dict:
    """Share of this instrument's shadow rows that were actually priceable."""
    mine = [r for r in rows if str(r.get("instrument")) == instrument]
    wanted = [r for r in mine if r.get("verdict") in (shadow.SIGNAL,
                                                      shadow.UNPRICED)]
    if not wanted:
        return {"observed_signals": 0, "measured_share": None,
                "basis": "NO_SHADOW_SIGNAL_YET_FOR_THIS_INSTRUMENT"}
    measured = sum(1 for r in wanted if r.get("verdict") == shadow.SIGNAL)
    return {
        "observed_signals": len(wanted),
        "measured_share": measured / len(wanted),
        "basis": "SHARE_OF_WANTED_ACTIONS_THE_BOOK_COULD_PRICE",
    }


def _historical_support(instrument: str) -> dict:
    rows = [r for r in screen.results()
            if instrument in (r.get("instrument_scope") or [])]
    if not rows:
        return {"screened": 0, "survivors": 0, "support": None,
                "basis": NO_HISTORY}
    survivors = [r for r in rows if r.get("screenable")
                 and not r.get("kill_reason")]
    return {
        "screened": len(rows),
        "survivors": len(survivors),
        "support": len(survivors) / len(rows),
        "basis": "SHARE_OF_SCREENED_CANDIDATES_THAT_SURVIVED_ON_THIS_NAME",
    }


def rank() -> dict:
    """Rank every screenable instrument, and name the ones that cannot be."""
    funnel = universe.funnel()
    shadow_rows = shadow.observations()
    ranked: list[dict] = []
    for row in funnel["screenable"]:
        inst = str(row["instrument"])
        bar_range = row.get("median_bar_range_pct")
        move = (float(bar_range) * TARGET_ATR_MULTIPLE
                if bar_range is not None else None)
        cost = row.get("modelled_round_trip_pct")
        ratio = (float(move) / float(cost)
                 if move is not None and cost not in (None, 0) else None)
        support = _historical_support(inst)
        capture = _capture_quality(shadow_rows, inst)
        components = {
            "movement_vs_cost": ratio,
            "historical_support": support["support"],
            "capture_quality": capture["measured_share"],
            "liquidity": row.get("median_bar_volume"),
        }
        label, why = _label(ratio, support, capture)
        ranked.append({
            "instrument": inst,
            "label": label,
            "why": why,
            "expected_move_pct": move,
            "expected_move_basis": (
                f"{TARGET_ATR_MULTIPLE}x the median one-minute bar range, "
                f"which is the smallest generated target"),
            "median_bar_range_pct": bar_range,
            "round_trip_cost_pct": cost,
            "movement_cost_multiple": ratio,
            "historical_support": support,
            "capture_quality": capture,
            "liquidity_basis": "CANDLE_VOLUME_A_PROXY_NOT_AN_ORDER_BOOK_DEPTH",
            "components": components,
            "execution_evidence": UNMEASURED if capture["measured_share"] is None
            else "PARTIALLY_MEASURED_SEE_CAPTURE_QUALITY",
        })
    ranked.sort(key=lambda r: (-(r["movement_cost_multiple"] or 0.0),
                               r["instrument"]))
    opportunities = [r for r in ranked if r["label"] == OPPORTUNITY]
    return {
        "verdict": NO_TRADE_ANYWHERE if not opportunities else OPPORTUNITY,
        "opportunities": len(opportunities),
        "ranked": ranked,
        "unranked": [
            {"instrument": r["instrument"], "absence": r.get("status"),
             "reason": r.get("reason")}
            for r in funnel["not_screenable"]
        ],
        "unranked_note": (
            "these instruments are not ranked low; they are unmeasured. "
            "Ranking an instrument with no data below one with data would "
            "imply a comparison that never happened."
        ),
        "weights": WEIGHTS,
        "thresholds": {
            "opportunity_cost_multiple": OPPORTUNITY_COST_MULTIPLE,
            "watch_cost_multiple": WATCH_COST_MULTIPLE,
        },
        "not_a_prediction": (
            "this is an eligibility ordering built from measured and modelled "
            "quantities. No component is a forecast and the ordering is not a "
            "probability that any trade will win."
        ),
        "generated_at": dt.datetime.now(_IST).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }


def _label(ratio: float | None, support: dict, capture: dict) -> tuple[str, str]:
    if ratio is None:
        return REJECT, "expected movement or cost could not be measured here"
    if ratio < WATCH_COST_MULTIPLE:
        return REJECT, (
            f"expected move is {ratio:.2f}x the round trip, below the "
            f"{WATCH_COST_MULTIPLE}x watch floor: the cost eats the move")
    if ratio < OPPORTUNITY_COST_MULTIPLE:
        return WATCH, (
            f"expected move is {ratio:.2f}x the round trip — above the watch "
            f"floor, below the {OPPORTUNITY_COST_MULTIPLE}x opportunity bar")
    if not support.get("survivors"):
        return WATCH, (
            f"movement clears {OPPORTUNITY_COST_MULTIPLE}x cost but no "
            f"candidate has survived a historical screen on this name "
            f"({support.get('basis')})")
    if capture.get("measured_share") is None:
        return WATCH, (
            "movement and historical support are there, but no shadow signal "
            "has yet been priced on a live book for this name")
    return OPPORTUNITY, (
        f"expected move {ratio:.2f}x the round trip, "
        f"{support['survivors']} surviving candidate(s), "
        f"{capture['measured_share']:.0%} of wanted actions priceable")
