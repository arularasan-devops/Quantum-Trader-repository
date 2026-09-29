"""Phase 39 §4-§9 — MOVE/COST, the ranking, and what it may not be read as.

The ratio is per leg and then aggregated, never the other way round:

    move_over_cost(leg) = favourable excursion (mid frame, %) / required move (%)

and the pair's figure is the **median of those per-leg ratios** at the reference
horizon. Taking a ratio of two separately-taken medians would silently pair the
median move with the median spread, which no instant experienced; pairing them
inside the leg keeps the wide-spread instants attached to their own movement.

Two things this module refuses to do, both of which would make the table look
better and mean less.

*It does not choose a horizon.* Ranking and labelling happen at
:data:`app.research.phase39.REFERENCE_HORIZON` only, declared before the first
run. The per-pair best horizon is computed and reported with
``used_for_ranking: false``, because selecting each row's own best hold from the
results is a future-outcome threshold choice.

*It does not turn a ratio into an edge.* The numerator is MFE — the best exit
available with hindsight — so the ratio is a **ceiling** on what any strategy
could have extracted, not an expectation. That makes the two directions
asymmetric and the report says so wherever a label appears: below 1 is a strong
negative (a perfect exit could not pay the costs), above 3 is only permission to
keep collecting evidence.

Reach rates are reported at 1x, 2x and the 3x the live flow gate already
requires, as a distribution rather than as a chosen threshold: nothing
downstream selects one of them.
"""
from __future__ import annotations

import statistics

from app.research.phase39 import (
    CANNOT_PAY,
    CAPABILITY_BANDS,
    CLEARS_GATE,
    CLOSE,
    FEASIBILITY_HORIZONS,
    GATE_MULTIPLE,
    INSUFFICIENT,
    MIN_INSTANTS_FOR_LABEL,
    MIN_INSTANTS_FOR_RANK,
    REACH_MULTIPLES,
    REFERENCE_HORIZON,
    STRESS_COST_MULTIPLES,
)
from app.research.phase39 import cost as p39cost

# What the triage column may say. "Undecided" is a first-class answer: a pair
# with nine executable quotes has not been shown to be uneconomic, it has been
# shown to be uncaptured.
KEEP = "KEEP_ACCUMULATING_PAPER_EVIDENCE"
DEPRIORITISE = "DEPRIORITISE_CANNOT_COVER_ITS_OWN_COSTS"
UNDECIDED = "UNDECIDED_NEEDS_MORE_CAPTURE_TO_ANSWER_EVEN_THIS"

HORIZON_KEYS: tuple[str, ...] = tuple(
    [str(h) for h in FEASIBILITY_HORIZONS] + [CLOSE],
)


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": round(statistics.median(ordered), 4),
        "mean": round(statistics.fmean(ordered), 4),
        "p25": round(p39cost.percentile(ordered, 25.0), 4),
        "p75": round(p39cost.percentile(ordered, 75.0), 4),
        "p90": round(p39cost.percentile(ordered, 90.0), 4),
    }


def _ratio(mfe_pct: float, required_pct: float) -> float | None:
    if required_pct <= 0:
        return None
    return mfe_pct / required_pct


def horizon_row(legs: list[dict], horizon: str) -> dict:
    """One instrument/vehicle at one horizon: movement, cost, ratio, reach.

    Legs with no quote at this horizon are absent from ``n`` rather than counted
    as zero movement — a contract that stopped being quoted did not stand still.
    """
    mfe: list[float] = []
    end: list[float] = []
    required: list[float] = []
    ratios: list[float] = []
    net_mid: list[float] = []
    reach = {f"{m}x": 0 for m in REACH_MULTIPLES}
    for leg in legs:
        snap = (leg.get("horizons") or {}).get(horizon)
        if not isinstance(snap, dict):
            continue
        req = float(leg["required_pct"])
        offered = float(snap["mfe_pct"])
        r = _ratio(offered, req)
        if r is None:
            continue
        mfe.append(offered)
        end.append(float(snap["end_pct"]))
        required.append(req)
        ratios.append(r)
        net_mid.append(float(snap["end_pct"]) - req)
        for m in REACH_MULTIPLES:
            if offered >= m * req:
                reach[f"{m}x"] += 1
    n = len(ratios)
    return {
        "horizon": horizon,
        "n": n,
        "move_over_cost": (
            round(statistics.median(ratios), 4) if ratios else None
        ),
        "move_over_cost_p25": (
            round(p39cost.percentile(sorted(ratios), 25.0), 4) if ratios else None
        ),
        "move_over_cost_p75": (
            round(p39cost.percentile(sorted(ratios), 75.0), 4) if ratios else None
        ),
        "favourable_move_pct": _stats(mfe),
        "end_of_horizon_move_pct": _stats(end),
        "required_move_pct": _stats(required),
        "mid_frame_net_at_horizon_pct": _stats(net_mid),
        "reach_rate_pct": {
            k: (round(100.0 * v / n, 2) if n else None) for k, v in reach.items()
        },
        "note": (
            "favourable move is MFE — the best exit available with hindsight — "
            "so move_over_cost is a ceiling, not an expectation"
        ),
    }


def label_of(ratio: float | None, n: int) -> str:
    """The capability band, or why no band was assigned."""
    if ratio is None or n < MIN_INSTANTS_FOR_LABEL:
        return INSUFFICIENT
    for name, lo, hi in CAPABILITY_BANDS:
        if lo <= ratio < hi:
            return name
    return CANNOT_PAY if ratio < 0 else CLEARS_GATE


def triage_of(label: str) -> str:
    if label == INSUFFICIENT:
        return UNDECIDED
    return DEPRIORITISE if label == CANNOT_PAY else KEEP


def stress_rows(legs: list[dict], horizon: str) -> list[dict]:
    """§9 — the same ratio when the round trip costs more than it was measured at.

    A pair that only clears the gate at exactly measured cost is not a pair to
    plan on: real fills slip, and the multiples here are Phase 36's own stress
    grid so the two studies stress by the same amounts.
    """
    out = []
    for multiple in STRESS_COST_MULTIPLES:
        ratios = []
        for leg in legs:
            snap = (leg.get("horizons") or {}).get(horizon)
            if not isinstance(snap, dict):
                continue
            r = _ratio(float(snap["mfe_pct"]), float(leg["required_pct"]) * multiple)
            if r is not None:
                ratios.append(r)
        out.append({
            "cost_multiple": multiple,
            "n": len(ratios),
            "move_over_cost": (
                round(statistics.median(ratios), 4) if ratios else None
            ),
            "label": label_of(
                statistics.median(ratios) if ratios else None, len(ratios),
            ),
        })
    return out


def reconciliation(legs: list[dict], horizon: str) -> dict:
    """§10 — the mid frame against Phase 36's executable frame, on the same legs.

    ``mid_net = end_of_horizon_mid_move - required_move`` charges the full
    quoted width as cost. ``realised_net = executable_move - fees`` has the width
    already inside its two fills. They differ by however much the quoted width
    changed between entry and exit, and that difference is reported rather than
    assumed away: it is the error bar on every mid-frame number above.
    """
    residuals: list[float] = []
    mid_nets: list[float] = []
    realised_nets: list[float] = []
    missing = 0
    for leg in legs:
        snap = (leg.get("horizons") or {}).get(horizon)
        if not isinstance(snap, dict):
            continue
        realised_end = snap.get("realised_end_pct")
        if realised_end is None:
            missing += 1
            continue
        required = float(leg["required_pct"])
        fees = required - float(leg["spread_pct"])
        mid_net = float(snap["end_pct"]) - required
        realised_net = float(realised_end) - fees
        mid_nets.append(mid_net)
        realised_nets.append(realised_net)
        residuals.append(mid_net - realised_net)
    return {
        "n": len(residuals),
        "legs_without_executable_exit": missing,
        "mid_frame_net_pct": _stats(mid_nets),
        "realised_frame_net_pct": _stats(realised_nets),
        "residual_pct": _stats(residuals),
        "residual_abs_pct": _stats([abs(r) for r in residuals]),
        "note": (
            "residual = mid frame minus executable frame; it is the change in "
            "the quoted width over the hold, not a disagreement about cost"
        ),
    }


def pair_rows(legs: list[dict]) -> dict:
    """Every instrument/vehicle pair, at every horizon, with its label."""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for leg in legs:
        grouped.setdefault(
            (str(leg["instrument"]), str(leg["vehicle"])), [],
        ).append(leg)

    out: dict[str, dict] = {}
    for (inst, vehicle), rows in sorted(grouped.items()):
        horizons = {h: horizon_row(rows, h) for h in HORIZON_KEYS}
        ref = horizons.get(REFERENCE_HORIZON) or {"n": 0, "move_over_cost": None}
        ratio = ref.get("move_over_cost")
        n = int(ref.get("n") or 0)
        label = label_of(ratio, n)
        measured = [
            (h, r) for h, r in horizons.items()
            if r.get("move_over_cost") is not None
            and int(r.get("n") or 0) >= MIN_INSTANTS_FOR_LABEL
        ]
        best = max(measured, key=lambda kv: kv[1]["move_over_cost"], default=None)
        out[f"{inst}|{vehicle}"] = {
            "instrument": inst,
            "vehicle": vehicle,
            "legs": len(rows),
            "sessions": sorted({str(r["session"]) for r in rows}),
            "reference_horizon": REFERENCE_HORIZON,
            "reference": ref,
            "capability": label,
            "triage": triage_of(label),
            "rankable": n >= MIN_INSTANTS_FOR_RANK,
            "horizons": horizons,
            "stress": stress_rows(rows, REFERENCE_HORIZON),
            "reconciliation": reconciliation(rows, REFERENCE_HORIZON),
            "best_horizon": (
                None if best is None else {
                    "horizon": best[0],
                    "move_over_cost": best[1]["move_over_cost"],
                    "n": best[1]["n"],
                    "used_for_ranking": False,
                    "note": (
                        "reported as sensitivity only. Selecting this horizon "
                        "per pair from the results would be a threshold chosen "
                        "with hindsight"
                    ),
                }
            ),
        }
    return out


def ranking(pairs: dict) -> dict:
    """The MOVE/COST order at the reference horizon, in three tiers.

    Tiers rather than one list because a ratio from forty instants and a ratio
    from four thousand are not comparable, and sorting them together would put
    a thin row at the top of a table people will read as a shortlist.
    """
    ranked, thin, unlabelled = [], [], []
    for key, row in pairs.items():
        entry = {
            "key": key,
            "instrument": row["instrument"],
            "vehicle": row["vehicle"],
            "n": row["reference"].get("n"),
            "sessions": len(row["sessions"]),
            "move_over_cost": row["reference"].get("move_over_cost"),
            "required_move_pct_median": (
                row["reference"].get("required_move_pct") or {}
            ).get("median"),
            "favourable_move_pct_median": (
                row["reference"].get("favourable_move_pct") or {}
            ).get("median"),
            "reach_rate_pct": row["reference"].get("reach_rate_pct"),
            "capability": row["capability"],
            "triage": row["triage"],
        }
        if row["capability"] == INSUFFICIENT:
            unlabelled.append(entry)
        elif row["rankable"]:
            ranked.append(entry)
        else:
            thin.append(entry)

    def order(rows: list[dict]) -> list[dict]:
        return sorted(
            rows, key=lambda r: (-(r["move_over_cost"] or 0.0), r["key"]),
        )

    return {
        "reference_horizon": REFERENCE_HORIZON,
        "gate_multiple": GATE_MULTIPLE,
        "ranked": order(ranked),
        "measured_below_rank_floor": order(thin),
        "below_label_floor": sorted(unlabelled, key=lambda r: r["key"]),
        "floors": {
            "instants_for_a_label": MIN_INSTANTS_FOR_LABEL,
            "instants_to_be_ranked": MIN_INSTANTS_FOR_RANK,
        },
        "ordering": "descending median MOVE/COST at the reference horizon",
    }


def triage(pairs: dict) -> dict:
    """The answer the phase was commissioned for, as three lists."""
    buckets: dict[str, list[str]] = {KEEP: [], DEPRIORITISE: [], UNDECIDED: []}
    for key, row in sorted(pairs.items()):
        buckets[row["triage"]].append(key)
    return {
        "keep_accumulating": buckets[KEEP],
        "deprioritise": buckets[DEPRIORITISE],
        "undecided": buckets[UNDECIDED],
        "meaning": {
            KEEP: (
                "typical favourable movement covers the round trip at least "
                "once at the reference horizon. This is permission to keep "
                "measuring, not evidence of an edge"
            ),
            DEPRIORITISE: (
                "even a hindsight-perfect exit did not cover the round trip "
                "for most instants. This is the decisive direction of the "
                "measurement"
            ),
            UNDECIDED: (
                "too few executable instants to answer even the cost question; "
                "nothing about the instrument has been shown either way"
            ),
        },
    }
