"""Separated capture and vehicle reporting — Phase 21 §78. RESEARCH ONLY.

The 1 Sep report is the reason this module exists. It said "data quality EXACT on
10,339 of 10,407 rows", which was true of the OPTION books and said nothing at
all about the futures column — which was empty on every single row. One headline
number covered two different measurements, and the missing one was invisible.

So every count here is reported per asset family AND per vehicle, and the two are
never merged:

    OPTION   CE/PE books at the signal   -> quality EXACT/GOOD/DEGRADED/...
    FUTURES  the contract at the signal  -> capture EXACT/NEAR/MISSING

    INDEX / MCX / STOCK, each on its own row, because pooling a NIFTY spread with
    a GOLD spread produces a number that describes neither.

Two further rules the earlier reports needed and did not have:

* **measured is separated from missing.** A component that was never measured is
  counted in its own column, not averaged in as a zero and not dropped. "9 of 12
  legs" and "9 of 12 legs, 3 unmeasurable" are different findings.
* **a vehicle preference is WITHHELD** until enough same-signal, costed, resolved
  comparisons exist (:data:`MIN_COMPARISONS`). The point of the futures leg is to
  answer "CE, PE or the contract?" — and the answer has to wait for the evidence
  rather than be produced from whatever the first few rows happened to do.
"""
from __future__ import annotations

from app.analysis import instrument_family as fam
from app.research.phase17 import futures as fut_mod, quality, schema
from app.research.phase20 import vehicle as p20vehicle

OPTION = "OPTION"
FUTURES = "FUTURES"
VEHICLES: tuple[str, str] = (OPTION, FUTURES)

# How many resolved, costed, same-signal comparisons before a vehicle preference
# is published at all. The SAME bar Phase 20 already withholds behind, not a
# second easier one: §1 changed how the two legs are joined (one observation id
# rather than two paper books within a time window), not how much evidence a
# preference needs.
MIN_COMPARISONS = p20vehicle.MIN_COMPARISONS

WITHHELD = "WITHHELD"

# Read view scope. A panel-sized read describes the tail; an audit read describes
# the series. Every rate carries which one it came from.
BOUNDED = "RECENT_BOUNDED_TAIL"
COMPLETE = "COMPLETE_SERIES"


def _pct(n: int, total: int) -> float | None:
    return round(100.0 * n / total, 2) if total else None


def _futures_state(obs: dict) -> str:
    plan = obs.get("futures_plan")
    if isinstance(plan, dict) and plan.get("capture") in fut_mod.CAPTURE_STATES:
        return str(plan["capture"])
    # A row written before §1, or one whose geometry could not be built: the
    # futures book is either absent or unusable, and either way it is MISSING
    # rather than assumed.
    return fut_mod.MISSING


def _reasons(observations: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for obs in observations:
        plan = obs.get("futures_plan") or {}
        why = plan.get("capture_reason") if isinstance(plan, dict) else None
        if _futures_state(obs) == fut_mod.MISSING:
            key = str(why or fut_mod.NO_FEED)
            out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def option_capture(observations: list[dict]) -> dict:
    """OPTION-side capture health: book quality and whether both sides existed."""
    qualities = [str(o.get("data_quality") or quality.MISSING) for o in observations]
    both = sum(1 for o in observations if o.get("both_sides"))
    return {
        "vehicle": OPTION,
        "rows": len(observations),
        "quality": quality.tally(qualities),
        "both_sides": both,
        "both_sides_pct": _pct(both, len(observations)),
    }


def futures_capture(observations: list[dict]) -> dict:
    """FUTURES-side capture health, with why each absent leg was absent."""
    out = dict(fut_mod.tally([_futures_state(o) for o in observations]))
    out["vehicle"] = FUTURES
    out["rows"] = len(observations)
    out["missing_reasons"] = _reasons(observations)
    out["basis_counts"] = _basis_counts(observations)
    return out


def _basis_counts(observations: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for obs in observations:
        plan = obs.get("futures_plan")
        if not isinstance(plan, dict):
            continue
        key = str(plan.get("basis") or fut_mod.BASIS_NONE)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def market_edge_coverage(observations: list[dict]) -> dict:
    """Measured vs missing market edge, and on which basis it was measured.

    Separated because the two bases are not interchangeable: an INDEX row's edge
    comes from the five-year cash pool, an MCX row's from measured futures
    continuation, and a report that prints them in one column has to say which
    number is which.
    """
    measured = 0
    missing = 0
    bases: dict[str, int] = {}
    for obs in observations:
        ap = obs.get("aplus")
        comp = (ap or {}).get("components", {}).get("market_edge", {})
        if comp.get("measured"):
            measured += 1
            key = str((ap or {}).get("market_edge_basis") or comp.get("note") or "?")
            bases[key] = bases.get(key, 0) + 1
        else:
            missing += 1
    return {
        "measured": measured,
        "missing": missing,
        "measured_pct": _pct(measured, measured + missing),
        "bases": dict(sorted(bases.items(), key=lambda kv: -kv[1])),
    }


def mcx_geometry_coverage(observations: list[dict]) -> dict:
    """How many MCX rows got a continuation-basis target, and why the rest did not."""
    measured = 0
    refused: dict[str, int] = {}
    for obs in observations:
        plan = obs.get("mcx_plan")
        if not isinstance(plan, dict):
            continue
        if plan.get("verdict") == "MEASURED":
            measured += 1
            continue
        for why in (plan.get("reasons") or ["NO_REASON_GIVEN"]):
            refused[str(why)] = refused.get(str(why), 0) + 1
    total = sum(
        1 for o in observations if isinstance(o.get("mcx_plan"), dict)
    )
    return {
        "basis": fut_mod.BASIS_MCX,
        "not_validated": True,
        "rows_with_plan": total,
        "measured": measured,
        "measured_pct": _pct(measured, total),
        "refused_reasons": dict(sorted(refused.items(), key=lambda kv: -kv[1])),
    }


def _comparable(legs: list[dict]) -> dict[str, list[dict]]:
    """Resolved, costed legs grouped by observation id.

    ``COST_MEASURED`` only: a leg whose exit was marked from an LTP because no
    book was quoted has no comparable net R, and letting it into the comparison
    would decide the vehicle question on the rows where the measurement failed.
    """
    out: dict[str, list[dict]] = {}
    for leg in legs:
        if not leg.get("resolved"):
            continue
        if leg.get("cost_status") != schema.COST_MEASURED:
            continue
        if leg.get("net_r") is None:
            continue
        key = str(leg.get("observation_id") or "")
        if key:
            out.setdefault(key, []).append(leg)
    return out


def same_signal(legs: list[dict]) -> dict:
    """§1b: CE vs PE vs FUTURES on the SAME signal, costed, or nothing.

    A comparison needs at least two vehicles resolved from one observation id —
    which is the only way both sides of "was the contract the better expression?"
    are measured against the same market move.
    """
    groups = _comparable(legs)
    per_vehicle: dict[str, list[float]] = {}
    comparisons = 0
    wins: dict[str, int] = {}
    for rows in groups.values():
        best: dict[str, dict] = {}
        for leg in rows:
            veh = str(leg.get("vehicle") or "?")
            prev = best.get(veh)
            if prev is None or float(leg["net_r"]) > float(prev["net_r"]):
                best[veh] = leg
        for veh, leg in best.items():
            per_vehicle.setdefault(veh, []).append(float(leg["net_r"]))
        if len(best) >= 2:
            comparisons += 1
            top = max(best.items(), key=lambda kv: float(kv[1]["net_r"]))[0]
            wins[top] = wins.get(top, 0) + 1
    summary = {
        veh: {
            "legs": len(vals),
            "mean_net_r": round(sum(vals) / len(vals), 4) if vals else None,
            "positive": sum(1 for v in vals if v > 0),
        }
        for veh, vals in sorted(per_vehicle.items())
    }
    leader = max(wins.items(), key=lambda kv: kv[1])[0] if wins else None
    lead_mean = (summary.get(leader) or {}).get("mean_net_r") if leader else None
    refusals: list[str] = []
    if comparisons < MIN_COMPARISONS:
        refusals.append(
            f"{comparisons} costed same-signal comparisons, "
            f"{MIN_COMPARISONS} required"
        )
    if leader is None:
        refusals.append("no vehicle led a costed comparison")
    elif lead_mean is None or float(lead_mean) <= 0:
        # It leads on count and loses money. "Won more signals" is not a
        # preference: a vehicle preferred here would be one the evidence says to
        # lose slowly with, which is exactly the claim this tool must not make.
        refusals.append(
            f"{leader} led on comparisons won but its own mean net R is "
            f"{lead_mean} — leading and profitable are different findings"
        )
    if schema.FUTURES not in summary:
        # CE vs PE is answerable without a futures leg; "futures or an option"
        # is not, and that is the question §1 was built for. Reported as its own
        # gap so a CE/PE ordering is never read as the vehicle answer.
        refusals.append(
            "no costed FUTURES leg in scope — the futures-vs-option question is "
            "still unanswered whatever the CE/PE ordering says"
        )
    return {
        "comparisons": comparisons,
        "min_comparisons": MIN_COMPARISONS,
        "by_vehicle": summary,
        "vehicles_compared": sorted(summary),
        "wins": dict(sorted(wins.items(), key=lambda kv: -kv[1])),
        "leader_by_comparisons_won": leader,
        "preferred_vehicle": WITHHELD if refusals else leader,
        "note": (
            "WITHHELD: " + "; ".join(refusals) if refusals else
            "Costed, resolved, same-signal legs only. A leg whose exit had no "
            "quoted book is excluded rather than counted at its LTP."
        ),
    }


def build(
    observations: list[dict],
    legs: list[dict],
    *,
    complete: bool = True,
    rows_dropped: int = 0,
) -> dict:
    """The separated report: per family, per vehicle, measured vs missing.

    ``complete`` is whether the caller read the whole series or a bounded tail,
    and it travels into every section — a capture rate from a tail is a statement
    about the last few minutes, and the earlier reports did not say so.
    """
    scope = COMPLETE if complete else BOUNDED
    families: dict[str, dict] = {}
    for name in fam.FAMILIES:
        rows = [o for o in observations
                if str(o.get("family") or "") == name
                or (not o.get("family")
                    and fam.family(str(o.get("instrument") or "")) == name)]
        ids = {str(o.get("observation_id") or "") for o in rows}
        # By the leg's OWN instrument, not by whether its observation is in this
        # read. A leg outlives the observation window it was opened in — the
        # 1 Sep legs were resolved against a rolled observations file — and
        # matching on membership silently dropped every one of them from its
        # family while the pooled total still counted them.
        fam_legs = [
            leg for leg in legs
            if fam.family(str(leg.get("instrument") or "")) == name
        ]
        orphans = sum(
            1 for leg in fam_legs
            if str(leg.get("observation_id") or "") not in ids
        )
        section = {
            "family": name,
            "rows": len(rows),
            "legs": len(fam_legs),
            # Legs whose originating observation is outside this read: their
            # economics are still measured, but the row that justified them
            # cannot be shown alongside.
            "legs_without_observation_in_scope": orphans,
            "instruments": sorted({
                str(o.get("instrument") or "?") for o in rows
            } | {
                str(leg.get("instrument") or "?") for leg in fam_legs
            }),
            OPTION: option_capture(rows),
            FUTURES: futures_capture(rows),
            "market_edge": market_edge_coverage(rows),
            "same_signal": same_signal(fam_legs),
        }
        if name == fam.MCX:
            section["mcx_geometry"] = mcx_geometry_coverage(rows)
        families[name] = section
    return {
        "evidence_coverage": {
            "rows_read": len(observations),
            "rows_dropped": rows_dropped,
            "legs_read": len(legs),
            "complete": bool(complete),
            "scope": scope,
        },
        "totals": {
            "rows": len(observations),
            OPTION: option_capture(observations),
            FUTURES: futures_capture(observations),
            "market_edge": market_edge_coverage(observations),
            "same_signal": same_signal(legs),
        },
        "families": families,
        "research_only": True,
        "note": (
            "OPTION quality and FUTURES capture are separate measurements and "
            "are never pooled; neither are asset families. Missing evidence is "
            "counted, not averaged away."
        ),
    }
