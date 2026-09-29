"""How far is T1 on the OPTION, not on the index? — §8.

There is a specific reason this is computed on the option and not on the
underlying. On the underlying, "T1 reachability" is a restatement of geometry, and
Phase 15B showed exactly what that produces: the ``room=LOW`` cohort hit T1 51.7%
of the time at a reward:risk of 0.92, while ``momentum=HIGH`` hit 36.9% at 1.70,
and both had the same expectancy. A high hit rate bought by a near target is not
an edge, and a score built from it would rank the worst trades first.

On the option there is something genuinely new to measure, because the premium
does not move point-for-point with the index:

    premium move required  ~=  underlying move required  x  |delta|

so the same target is a 4% premium move on a deep ITM contract and a 40% move on
a far OTM one, and the spread has to be crossed before either starts. That is the
question this scores.

The output is a SCORE and a RANK BAND, never a probability. A percentage here
would be read as "82% chance of T1" by everyone who saw it, and nothing in this
dataset supports that number: calibration needs resolved out-of-sample outcomes,
and until §29 has them, ``not_a_probability`` stays true on every row.

Which T1, though? The production engine publishes stop and target for its own
recommended leg only, and only while it is actually offering a trade. Every other
recorded candidate therefore had no target to be measured against, scored no
``room`` at all, and — because A+ requires every component measured — could never
reach A+ however good its book was. That made A+ a subset of "production is
calling BUY on this exact strike", not a grading of the candidate pool.

So a candidate without a published target is scored against a target DERIVED from
the setup's own expected move on the underlying:

    modelled T1 premium  =  entry  +  |expected move|  x  |delta|

and ``t1_basis`` says which of the two it was. A modelled row is never silently
compared with an engine-target row: the basis travels with the score, promotion
samples are restricted to ``ENGINE_TARGET``, and the ``move_coverage`` component
is DROPPED on a modelled row rather than scored, because a target built from the
expected move trivially covers the expected move and scoring it would hand every
modelled candidate a free 100.
"""
from __future__ import annotations

from app.research.phase17 import quality, schema

# Bands over the 0-100 score. Ordinal labels, deliberately not percentages.
RANK_A = "RANK_A"
RANK_B = "RANK_B"
RANK_C = "RANK_C"
RANK_D = "RANK_D"
RANK_E = "RANK_E"
RANK_UNKNOWN = "RANK_UNKNOWN"
RANKS: tuple[str, ...] = (RANK_A, RANK_B, RANK_C, RANK_D, RANK_E, RANK_UNKNOWN)

# Where the T1 being measured against came from.
BASIS_ENGINE = "ENGINE_TARGET"              # the production plan published it
BASIS_MODELLED = "MODELLED_FROM_EXPECTED_MOVE"  # derived, research only
BASIS_NONE = "NO_T1"

_BANDS: tuple[tuple[str, float], ...] = (
    (RANK_A, 80.0), (RANK_B, 65.0), (RANK_C, 50.0), (RANK_D, 35.0), (RANK_E, 0.0),
)


def band(score: float | None) -> str:
    if not isinstance(score, (int, float)):
        return RANK_UNKNOWN
    for name, floor in _BANDS:
        if float(score) >= floor:
            return name
    return RANK_E


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def assess(q: schema.Quote | None, plan: schema.Plan, econ: dict | None = None,
           *, mfe_history: float | None = None) -> dict:
    """Option-side T1 reachability for one quote.

    ``mfe_history`` is an optional measured favourable excursion for this
    contract from an earlier resolved leg; when absent the score uses only the
    quantities available at signal time, which is the honest default.
    """
    out: dict = {
        "t1_score": None,
        "t1_rank": RANK_UNKNOWN,
        "not_a_probability": True,
        "entry_premium": None,
        "t1_premium": None,
        "premium_distance_to_t1": None,
        "premium_move_required_pct": None,
        "underlying_move_required": None,
        "delta": None,
        "spread": None,
        "spread_over_distance": None,
        "room_after_spread": None,
        "option_mfe": mfe_history,
        "reasons": [],
        "basis": "OPTION_SIDE",
        "t1_basis": BASIS_NONE,
    }
    if q is None:
        out["reasons"].append("NO_QUOTE")
        return out
    entry = q.ask if (q.has_book and isinstance(q.ask, (int, float))) else q.premium
    if not isinstance(entry, (int, float)) or float(entry) <= 0:
        out["reasons"].append("NO_PREMIUM")
        return out
    entry = float(entry)
    t1, basis = _target(entry, q, plan)
    if t1 is None:
        out["reasons"].append("NO_T1")
        return out
    out["t1_basis"] = basis
    if basis == BASIS_MODELLED:
        out["reasons"].append("T1_MODELLED_FROM_EXPECTED_MOVE")
        if isinstance(plan.target1, (int, float)) and float(plan.target1) > 0:
            # There WAS a published target and it sat at or below the ask: a stale
            # plan, not an absent one. Modelling replaces it, but the staleness is
            # a finding in its own right and must not be swallowed.
            out["reasons"].append("ENGINE_T1_NOT_ABOVE_ENTRY")
    t1 = float(t1)
    out["entry_premium"], out["t1_premium"] = entry, t1
    dist = round(t1 - entry, 4)
    out["premium_distance_to_t1"] = dist
    if dist <= 0:
        # T1 at or below the current ask is not reachability, it is a stale plan.
        out["reasons"].append("T1_NOT_ABOVE_ENTRY")
        return out
    out["premium_move_required_pct"] = round(100.0 * dist / entry, 3)
    delta = abs(float(q.delta)) if isinstance(q.delta, (int, float)) else None
    out["delta"] = delta
    if delta and delta > 0:
        out["underlying_move_required"] = round(dist / delta, 4)
    else:
        out["reasons"].append("NO_DELTA")

    spread = q.spread
    out["spread"] = spread
    if spread is not None:
        out["spread_over_distance"] = round(spread / dist, 4)
        out["room_after_spread"] = round(dist - spread, 4)

    if not quality.usable(q.data_quality):
        # A score computed off a stale book would be ranked against scores from
        # live ones on the same board. Refuse rather than mix.
        out["reasons"].append(f"QUALITY_{q.data_quality}")
        return out

    # Score components, each 0-100, each measurable at signal time. Weights are
    # stated here and printed in the report; they are a research ranking, not a
    # fitted model, and no weight was chosen by looking at outcomes.
    comps: dict[str, float] = {}

    # 1. How big a premium move T1 demands. 5% is easy, 40% is a different trade.
    pct = float(out["premium_move_required_pct"])
    comps["premium_move"] = _clamp(100.0 - (pct - 5.0) * (100.0 / 35.0))

    # 2. How much of that move the spread has already taken.
    if spread is not None:
        sod = float(out["spread_over_distance"])
        comps["spread_burden"] = _clamp(100.0 - sod * 300.0)
    else:
        out["reasons"].append("NO_BOOK")

    # 3. Delta: a contract that barely responds to the underlying needs a move
    #    that the setup was never predicting.
    if delta:
        comps["delta"] = _clamp(delta * 200.0)

    # 4. Does the setup's own expected move cover what T1 needs? This is the one
    #    component that can be zero on a plan the engine published, and when it
    #    is, that is the finding. It is not scored on a modelled target, where it
    #    would compare the expected move against itself.
    if basis == BASIS_MODELLED:
        out["reasons"].append("MOVE_COVERAGE_NOT_SCORED_ON_MODELLED_T1")
    elif (
        isinstance(plan.expected_move_points, (int, float))
        and out["underlying_move_required"]
    ):
        need = float(out["underlying_move_required"])
        have = abs(float(plan.expected_move_points))
        comps["move_coverage"] = _clamp(100.0 * have / need) if need > 0 else 0.0

    # 5. Measured favourable excursion on this contract, when one exists.
    if isinstance(mfe_history, (int, float)) and dist > 0:
        comps["measured_mfe"] = _clamp(100.0 * float(mfe_history) / dist)

    if not comps:
        out["reasons"].append("NO_COMPONENTS")
        return out
    score = round(sum(comps.values()) / len(comps), 2)
    out["components"] = {k: round(v, 2) for k, v in comps.items()}
    out["t1_score"] = score
    out["t1_rank"] = band(score)
    if econ and econ.get("vehicle_class") == "RED":
        # The rank is not overridden — a reader comparing rows must see the raw
        # score — but the reason travels with it.
        out["reasons"].append("VEHICLE_RED")
    return out


def _target(entry: float, q: schema.Quote, plan: schema.Plan
            ) -> tuple[float | None, str]:
    """The T1 premium to measure against, and where it came from.

    The engine's own target wins whenever it is usable. Only when there is none —
    the candidate is not the recommended leg, or the engine is not offering a
    trade — is one derived from the expected move, so that a candidate can be
    graded on its book instead of being unmeasurable by construction.
    """
    t1 = plan.target1
    if isinstance(t1, (int, float)) and float(t1) > entry:
        return float(t1), BASIS_ENGINE
    move = plan.expected_move_points
    delta = abs(float(q.delta)) if isinstance(q.delta, (int, float)) else None
    if (
        isinstance(move, (int, float))
        and abs(float(move)) > 0
        and delta
        and delta > 0
    ):
        return round(entry + abs(float(move)) * delta, 4), BASIS_MODELLED
    return None, BASIS_NONE


def rank_table(rows: list[dict]) -> dict:
    """Distribution of ranks over a set of assessed rows, for the reports."""
    counts = {r: 0 for r in RANKS}
    for row in rows:
        r = row.get("t1_rank")
        counts[r if r in counts else RANK_UNKNOWN] += 1
    total = len(rows)
    return {
        "total": total,
        "counts": counts,
        "not_a_probability": True,
        "note": (
            "Ranks order candidates by measurable option-side distance to T1. "
            "They are not calibrated frequencies and must not be read as one "
            f"until {schema.T1}-before-{schema.STOP} outcomes exist out of sample."
        ),
    }
