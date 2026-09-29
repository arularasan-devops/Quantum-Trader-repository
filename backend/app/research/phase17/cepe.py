"""Was the market wrong, or was the vehicle wrong? — §6, §8.

This is the most valuable output of the phase, and it is only answerable because
both sides were recorded at the same instant off the same chain. The distinction:

``WRONG_MARKET``
    the direction was wrong. The chosen side lost AND the opposite side would
    have won. Fixing the option chain does not help; the read was wrong.
``WRONG_VEHICLE``
    the direction was right and the trade still lost. The underlying went the
    predicted way while the premium did not pay for the round trip — spread,
    theta, a strike too far out. This is the failure mode the measured Rs 56
    against Rs 8 predicts, and the one that filters cannot fix.
``BOTH_BAD``
    neither side paid. Usually a market that did not move at all, where both
    premiums decayed — an important and easily-hidden case, because a "small
    loss" on the chosen side looks like bad luck until you see the other side
    lost too.

Two rules keep this honest. Both sides must be present and usable, or the row is
UNKNOWN and excluded from the tally rather than assumed. And the verdict is taken
from NET outcomes where costs were measured; a gross comparison would call a
losing pair a winning direction, which is precisely the error the earlier reports
made.
"""
from __future__ import annotations

from app.research.phase17 import quality, schema

RIGHT_SIDE = "RIGHT_SIDE"
WRONG_SIDE = "WRONG_SIDE"
DIRECTION_FAILURE = "DIRECTION_FAILURE"
BOTH_BAD = "BOTH_BAD"
UNKNOWN = "UNKNOWN"
SIDE_VERDICTS: tuple[str, ...] = (
    RIGHT_SIDE, WRONG_SIDE, DIRECTION_FAILURE, BOTH_BAD, UNKNOWN,
)

WRONG_MARKET = "WRONG_MARKET"
WRONG_VEHICLE = "WRONG_VEHICLE"
RIGHT_BOTH = "RIGHT_MARKET_RIGHT_VEHICLE"
FAULTS: tuple[str, ...] = (WRONG_MARKET, WRONG_VEHICLE, RIGHT_BOTH, BOTH_BAD, UNKNOWN)


def _result(leg: dict | None) -> tuple[float | None, str]:
    """Preferred outcome measure for one leg: net if costed, else gross."""
    if not leg:
        return None, schema.COST_UNKNOWN
    if leg.get("cost_status") == schema.COST_MEASURED and isinstance(
        leg.get("net_points"), (int, float)
    ):
        return float(leg["net_points"]), schema.COST_MEASURED
    if isinstance(leg.get("gross_points"), (int, float)):
        return float(leg["gross_points"]), schema.COST_UNKNOWN
    return None, schema.COST_UNKNOWN


def compare(selected: dict | None, opposite: dict | None) -> dict:
    """Classify one observation's pair of resolved legs."""
    out: dict = {
        "side_verdict": UNKNOWN,
        "fault": UNKNOWN,
        "selected_result": None,
        "opposite_result": None,
        "basis": schema.COST_UNKNOWN,
        "reasons": [],
        "underlying_direction_correct": None,
    }
    sel, sel_basis = _result(selected)
    opp, opp_basis = _result(opposite)
    out["selected_result"], out["opposite_result"] = sel, opp
    if sel is None or opp is None:
        out["reasons"].append("MISSING_SIDE")
        return out
    for leg, name in ((selected, "SELECTED"), (opposite, "OPPOSITE")):
        if leg and not quality.usable(leg.get("data_quality")):
            out["reasons"].append(f"{name}_QUALITY_{leg.get('data_quality')}")
    if out["reasons"]:
        return out
    out["basis"] = (
        schema.COST_MEASURED
        if schema.COST_MEASURED in (sel_basis, opp_basis)
        else schema.COST_UNKNOWN
    )

    # Did the underlying actually go the predicted way? Held separately from the
    # premium result, because those two disagreeing IS the finding.
    if selected:
        u_entry = selected.get("underlying_entry")
        u_best = selected.get("underlying_best")
        u_worst = selected.get("underlying_worst")
        if all(isinstance(v, (int, float)) for v in (u_entry, u_best, u_worst)):
            up = float(u_best) - float(u_entry)
            down = float(u_entry) - float(u_worst)
            if selected.get("vehicle") == schema.CE:
                out["underlying_direction_correct"] = up >= down
            elif selected.get("vehicle") == schema.PE:
                out["underlying_direction_correct"] = down >= up

    if sel > 0 and sel >= opp:
        out["side_verdict"] = RIGHT_SIDE
        out["fault"] = RIGHT_BOTH
    elif opp > 0 and opp > sel:
        out["side_verdict"] = WRONG_SIDE if sel > 0 else DIRECTION_FAILURE
        out["fault"] = WRONG_MARKET
    elif sel <= 0 and opp <= 0:
        out["side_verdict"] = BOTH_BAD
        # Both premiums lost. If the underlying still went the right way, the
        # money was taken by the vehicle, not by the read.
        out["fault"] = (
            WRONG_VEHICLE if out["underlying_direction_correct"] else BOTH_BAD
        )
    else:
        out["side_verdict"] = UNKNOWN
        out["reasons"].append("UNCLASSIFIED")
    return out


def pair_legs(legs: list[dict]) -> dict[str, dict[str, dict]]:
    """Group resolved leg rows by observation and side."""
    out: dict[str, dict[str, dict]] = {}
    for leg in legs:
        oid = leg.get("observation_id")
        side = leg.get("side")
        if not isinstance(oid, str) or not isinstance(side, str):
            continue
        out.setdefault(oid, {})[side] = leg
    return out


def tally(legs: list[dict]) -> dict:
    """§6 counterfactual over every observation with both sides resolved."""
    pairs = pair_legs(legs)
    verdicts = {v: 0 for v in SIDE_VERDICTS}
    faults = {f: 0 for f in FAULTS}
    rows: list[dict] = []
    for oid, sides in pairs.items():
        cmp_ = compare(sides.get("SELECTED"), sides.get("OPPOSITE"))
        cmp_["observation_id"] = oid
        rows.append(cmp_)
        verdicts[cmp_["side_verdict"]] += 1
        faults[cmp_["fault"]] += 1
    complete = sum(v for k, v in verdicts.items() if k != UNKNOWN)
    return {
        "observations": len(pairs),
        "classified": complete,
        "unclassified": verdicts[UNKNOWN],
        "side_verdicts": verdicts,
        "faults": faults,
        "wrong_market_pct": (
            round(100.0 * faults[WRONG_MARKET] / complete, 2) if complete else None
        ),
        "wrong_vehicle_pct": (
            round(100.0 * faults[WRONG_VEHICLE] / complete, 2) if complete else None
        ),
        "rows": rows,
    }
