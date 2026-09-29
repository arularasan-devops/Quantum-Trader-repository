"""Phase 38 §11/§13 — rank the causes by rupees, then say what to do.

The ranking is arithmetic, not judgement: each cause is credited with the rupees
the measured sections attribute to it, and they are sorted. That matters because
the natural failure of a loss post-mortem is to name the cause that is easiest to
fix rather than the one that cost the most.

Two attributions are deliberately *not* additive with the rest and are labelled
so:

* WRONG_VEHICLE is the money a different vehicle on the same instant would have
  made. It is an opportunity, not a component of the realised loss — counting it
  in the waterfall would double-count the same rupee.
* EXIT_PROBLEM and ENTRY_TIMING_PROBLEM are likewise counterfactual: they are the
  improvement a different hold or a later fill would have produced on the same
  legs.

The realised loss decomposes into MOVE_TOO_SMALL, COST_TOO_HIGH and
GIVEBACK_PROBLEM. Those three are the only causes carrying realised rupees, but
they do not sum to the final net on their own: the legs that ended non-negative
sit in §2's OTHER category, which is not a cause and is therefore not ranked.
The identity that does hold is the four §2 categories against the final net, and
that is what the waterfall closes on.
"""
from __future__ import annotations

from app.research.phase35 import CE, FUTURES, PE
from app.research.phase38 import (
    CAUSE_COST,
    CAUSE_ENTRY,
    CAUSE_EXIT,
    CAUSE_GIVEBACK,
    CAUSE_MOVE,
    CAUSE_NONE,
    CAUSE_VEHICLE,
    COLLECT_MORE,
    CONTINUE_PAPER,
    COST_DOMINATED,
    DATA_FIX,
    ENTRY_IMPROVEMENT_PCT,
    EXIT_IMPROVEMENT_PCT,
    GIVEBACK,
    MIN_COHORT,
    MIN_SESSIONS_FOR_A_CLAIM,
    MOVE_TOO_SMALL,
    TOO_EARLY,
    UNMEASURED_COST_TOLERANCE_PCT,
)
from app.research.phase38 import money as p38money
from app.research.phase38 import sections as p38sections

REALISED_CAUSES: tuple[str, ...] = (CAUSE_MOVE, CAUSE_COST, CAUSE_GIVEBACK)


def _cat_rupees(mvc: dict, category: str) -> float:
    body = (mvc.get("by_category") or {}).get(category) or {}
    val = body.get("net_rupees")
    return float(val) if isinstance(val, (int, float)) else 0.0


def _cat_n(mvc: dict, category: str) -> int:
    body = (mvc.get("by_category") or {}).get(category) or {}
    return int(body.get("n") or 0)


def vehicle_opportunity(rows: list[dict], *, horizon: str) -> dict:
    """The rupees a different vehicle on the same instant would have made.

    Only counted where the vehicle actually taken lost and another *directional*
    vehicle at the same instant did not. The counterfactual option side is
    excluded: "the put would have paid on a long signal" is a direction
    statement, and Phase 36 §14 already refuses to call it a vehicle error.
    """
    by_obs: dict[str, dict[str, dict]] = {}
    for r in p38sections.directional(rows):
        if r.get("entry_price") is not None:
            by_obs.setdefault(r["obs_id"], {})[r["vehicle"]] = r
    gain = 0.0
    instants = 0
    engine_lost_n = 0
    for group in by_obs.values():
        monies = {}
        for v in (FUTURES, CE, PE):
            row = group.get(v)
            if row is None:
                continue
            m = p38money.leg_money(row, horizon)
            if m is not None:
                monies[v] = float(m["net_rupees"])
        if len(monies) < 2:
            continue
        instants += 1
        taken = monies.get(FUTURES)
        best_v = max(monies, key=lambda v: monies[v])
        best = monies[best_v]
        if taken is None:
            continue
        if taken < 0 and best > 0:
            engine_lost_n += 1
            gain += best - taken
    return {
        "instants_with_two_vehicles": instants,
        "instants_where_another_vehicle_paid": engine_lost_n,
        "improvement_rupees": round(gain, 2),
        "baseline_vehicle": FUTURES,
        "note": (
            "measured against the FUTURES leg as the cleanest expression of "
            "the view, because the engine's own vehicle is not recorded on "
            "every triple; counterfactual, so it is not part of the waterfall"
        ),
    }


def exit_opportunity(hold: dict, *, horizon: str) -> dict:
    """The rupees a different single horizon would have added on the same legs."""
    best: list[dict] = []
    total = 0.0
    for vehicle, body in hold.items():
        per = body.get("by_horizon") or {}
        ref = (per.get(horizon) or {}).get("net_rupees")
        chosen = body.get("least_negative_horizon")
        got = (per.get(chosen) or {}).get("net_rupees") if chosen else None
        if not isinstance(ref, (int, float)) or not isinstance(
            got, (int, float),
        ):
            continue
        delta = round(float(got) - float(ref), 2)
        per_trade_ref = (per.get(horizon) or {}).get("net_rupees_per_trade")
        per_trade_got = (per.get(chosen) or {}).get("net_rupees_per_trade")
        if delta > 0:
            total += delta
        best.append({
            "vehicle": vehicle,
            "reference_horizon": horizon,
            "best_horizon": chosen,
            "improvement_rupees": delta,
            "reference_net_per_trade": per_trade_ref,
            "best_net_per_trade": per_trade_got,
        })
    return {
        "improvement_rupees": round(total, 2),
        "by_vehicle": best,
        "note": (
            "one horizon for the whole sample, chosen with hindsight on one "
            "session; counterfactual and not an exit rule"
        ),
    }


def rank(
    *, move_cost: dict, giveback: dict, hold: dict, entry: dict,
    vehicle_gain: dict, exit_gain: dict, sessions: int,
) -> list[dict]:
    """Every cause with its rupees, sorted by financial impact."""
    realised = {
        CAUSE_MOVE: (_cat_rupees(move_cost, MOVE_TOO_SMALL),
                     _cat_n(move_cost, MOVE_TOO_SMALL)),
        CAUSE_COST: (_cat_rupees(move_cost, COST_DOMINATED),
                     _cat_n(move_cost, COST_DOMINATED)),
        CAUSE_GIVEBACK: (_cat_rupees(move_cost, GIVEBACK),
                         _cat_n(move_cost, GIVEBACK)),
    }
    out: list[dict] = []
    for cause, (rupees, n) in realised.items():
        out.append({
            "cause": cause,
            "kind": "REALISED_LOSS",
            "rupees": round(rupees, 2),
            "impact_rupees": round(abs(rupees), 2),
            "legs": n,
            "evidence": "§2 category net; the three realised causes plus §2's "
                        "non-negative OTHER category sum to the final net",
            "sufficient": n >= MIN_COHORT,
        })
    ent = float(
        (entry.get("improved_by_waiting") or [{}])[0].get("improvement_pct")
        or 0.0
    )
    out.append({
        "cause": CAUSE_ENTRY,
        "kind": "COUNTERFACTUAL",
        "rupees": None,
        "impact_rupees": 0.0 if entry.get("label") != TOO_EARLY else None,
        "improvement_pct_of_entry": round(ent, 4) if ent else None,
        "legs": (entry.get("by_offset", {}).get("0.0") or {}).get("n"),
        "evidence": f"§7 label {entry.get('label')}",
        "sufficient": entry.get("label") == TOO_EARLY,
    })
    out.append({
        "cause": CAUSE_VEHICLE,
        "kind": "COUNTERFACTUAL",
        "rupees": vehicle_gain.get("improvement_rupees"),
        "impact_rupees": abs(float(vehicle_gain.get(
            "improvement_rupees",
        ) or 0.0)),
        "legs": vehicle_gain.get("instants_where_another_vehicle_paid"),
        "evidence": "§6 — another vehicle on the same instant was positive",
        "sufficient": int(vehicle_gain.get(
            "instants_where_another_vehicle_paid",
        ) or 0) >= MIN_COHORT,
    })
    out.append({
        "cause": CAUSE_EXIT,
        "kind": "COUNTERFACTUAL",
        "rupees": exit_gain.get("improvement_rupees"),
        "impact_rupees": abs(float(exit_gain.get("improvement_rupees") or 0.0)),
        "legs": None,
        "evidence": "§3 — a different single horizon on the same legs",
        "sufficient": float(
            exit_gain.get("improvement_rupees") or 0.0
        ) > EXIT_IMPROVEMENT_PCT,
    })
    for row in out:
        row["session_count"] = sessions
        row["descriptive_only"] = sessions < MIN_SESSIONS_FOR_A_CLAIM
    out.sort(key=lambda r: -(r.get("impact_rupees") or 0.0))
    return out


def action(*, sessions: int, coverage: dict, ranked: list[dict],
           unmeasured_pct: float | None) -> dict:
    """§13 — one of three actions, and the reason it was chosen.

    A data problem outranks a market problem: if a material share of the legs
    could not be costed, or no leg could be, then the tables above describe the
    capture rather than the market and fixing the capture comes first.
    """
    if isinstance(unmeasured_pct, (int, float)) and float(
        unmeasured_pct,
    ) > UNMEASURED_COST_TOLERANCE_PCT:
        return {
            "action": DATA_FIX,
            "reason": (
                f"{unmeasured_pct:.1f}% of entered legs had no measurable "
                "round-trip cost, which is above the "
                f"{UNMEASURED_COST_TOLERANCE_PCT:.0f}% tolerance"
            ),
        }
    if not ranked or all((r.get("legs") or 0) < MIN_COHORT for r in ranked):
        return {
            "action": COLLECT_MORE,
            "reason": "no cause has a cohort above the evidence floor",
        }
    if sessions < MIN_SESSIONS_FOR_A_CLAIM:
        return {
            "action": COLLECT_MORE,
            "reason": (
                f"{sessions} session(s) collected; {MIN_SESSIONS_FOR_A_CLAIM} "
                "are required before this ranking is more than a description "
                "of one day"
            ),
        }
    return {
        "action": CONTINUE_PAPER,
        "reason": (
            "the sample supports a description of the loss and nothing in it "
            "requires a capture fix"
        ),
    }


def primary_and_secondary(ranked: list[dict]) -> dict:
    """The two lines the report ends with, taken from the ranking only."""
    usable = [r for r in ranked if (r.get("impact_rupees") or 0.0) > 0]
    if not usable:
        return {
            "primary": CAUSE_NONE,
            "secondary": CAUSE_NONE,
            "reason": "no cause carried measurable rupees in this session",
        }
    primary = usable[0]
    secondary = usable[1] if len(usable) > 1 else None
    return {
        "primary": primary["cause"],
        "primary_rupees": primary.get("rupees"),
        "primary_sufficient": primary.get("sufficient"),
        "secondary": secondary["cause"] if secondary else CAUSE_NONE,
        "secondary_rupees": secondary.get("rupees") if secondary else None,
        "secondary_sufficient": (
            secondary.get("sufficient") if secondary else False
        ),
        "reason": "ranked by absolute rupee impact over the measured legs",
    }


def entry_note(entry: dict) -> str:
    """A sentence about entry timing that cannot overstate what was measured."""
    if entry.get("label") == TOO_EARLY:
        rows = entry.get("improved_by_waiting") or []
        first = rows[0] if rows else {}
        return (
            f"waiting {first.get('offset')} minute(s) improved mean net by "
            f"{first.get('improvement_pct')}% of entry on this session, which "
            f"is above the {ENTRY_IMPROVEMENT_PCT}% reporting threshold and is "
            "still one session"
        )
    return (
        "no delayed entry beat the decision instant by more than "
        f"{ENTRY_IMPROVEMENT_PCT}% of entry, so entry timing is not the main "
        "problem in this session"
    )
