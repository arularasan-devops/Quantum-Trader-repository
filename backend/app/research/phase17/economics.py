"""Can this vehicle pay for itself? — §9, §10, §12.

The measured position before this phase: on the 48 legs that had a real book, a
Rs 56 round trip was chasing Rs 8 of gross travel. That is not a directional
problem and no filter fixes it. It is arithmetic between three numbers — the
spread, the risk being taken, and the move the setup can reasonably expect — and
this module computes exactly those ratios and nothing else.

The classification is DESCRIPTIVE. GREEN/YELLOW/RED are cut at round numbers
chosen before any outcome was read, and they are not fitted to this dataset,
because fitting a band on the rows that then measure its benefit is the trap
Phase 15B was built to avoid — 218 comparisons, 3.68 sigma, nothing survived.
When the matched sample is large enough these seeds get tested through the same
dev/validation/holdout discipline as anything else; until then they are a sorting
aid on a research panel and every report says so.

``cost_over_expected_move`` is the number that matters most and the one the
earlier reports never printed. It answers: of the move this setup can expect, how
much is already spent on the round trip before the market does anything?
"""
from __future__ import annotations

from app.analysis import instrument_family as fam
from app.analysis import option_costs
from app.config import settings
from app.research.phase17 import quality, schema

GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"
UNKNOWN = "UNKNOWN"
CLASSES: tuple[str, ...] = (GREEN, YELLOW, RED, UNKNOWN)

# Seeds, stated openly. A round trip that eats more than a fifth of the risk
# being taken, or more than a third of the move the setup expects, is not a
# vehicle worth researching further; under a tenth of each is clean.
GREEN_COST_OVER_RISK = 0.10
RED_COST_OVER_RISK = 0.20
GREEN_COST_OVER_MOVE = 0.10
RED_COST_OVER_MOVE = 0.33
# A spread wider than this share of the premium is red on its own, whatever the
# ratios say: it is the family median for a single stock (9.52%), and a book that
# wide cannot be entered and exited at the prices it advertises.
RED_SPREAD_PCT = 5.0


def _lot_size(instrument: str) -> int | None:
    from app.market.instruments import REGISTRY

    spec = REGISTRY.get((instrument or "").upper())
    if spec is None:
        return None
    return int(spec.lot_size) if spec.lot_size > 0 else None


def assess(
    q: schema.Quote | None,
    plan: schema.Plan,
    *,
    lots: int = 1,
) -> dict:
    """Vehicle economics for one quote against one plan.

    Returns a dict in all cases. An unmeasurable vehicle is UNKNOWN with the
    reason named — never GREEN by default, which is how an unquoted strike ends
    up on a board as though it were liquid.
    """
    out: dict = {
        "vehicle_class": UNKNOWN,
        "reasons": [],
        "spread": None,
        "spread_pct": None,
        "spread_over_risk": None,
        "spread_over_expected_move": None,
        "cost_points": None,
        "cost_rupees": None,
        "cost_over_risk": None,
        "cost_over_expected_move": None,
        "room_after_spread": None,
        "risk_after_spread": None,
        "cost_status": schema.COST_UNKNOWN,
        "spread_source": None,
        "data_quality": q.data_quality if q else quality.MISSING,
    }
    if q is None:
        out["reasons"].append("NO_QUOTE")
        return out
    entry = q.ask if (q.has_book and isinstance(q.ask, (int, float))) else q.premium
    if not isinstance(entry, (int, float)) or float(entry) <= 0:
        out["reasons"].append("NO_PREMIUM")
        return out
    entry = float(entry)

    spread = q.spread
    out["spread"] = spread
    out["spread_pct"] = q.spread_pct
    measured = spread is not None and quality.usable(q.data_quality)
    if not measured:
        out["reasons"].append(
            "NO_BOOK" if spread is None else f"QUALITY_{q.data_quality}"
        )

    lot = _lot_size(q.instrument) or plan.lot_size
    cost = option_costs.round_trip(
        q.instrument,
        entry,
        None,
        int(lot) if lot else 0,
        lots,
        quoted_spread=spread if measured else None,
    )
    if cost is not None:
        out["cost_points"] = round(cost.cost_points, 4)
        out["cost_rupees"] = round(cost.cost_rupees, 2)
        out["spread_source"] = cost.spread_source
        out["cost_status"] = (
            schema.COST_MEASURED
            if cost.spread_source == option_costs.MEASURED
            else schema.COST_UNKNOWN
        )
    elif lot is None:
        out["reasons"].append("NO_LOT_SIZE")

    risk = plan.risk if isinstance(plan.risk, (int, float)) and plan.risk > 0 else None
    if risk is None and isinstance(plan.stop, (int, float)) and plan.stop > 0:
        risk = entry - float(plan.stop)
        risk = risk if risk > 0 else None

    # The expected move is expressed in UNDERLYING points by the engine; on the
    # option it has to be converted through delta or it is comparing rupees of
    # premium against points of index. No delta, no conversion, no claim.
    expected_premium_move: float | None = None
    if (
        isinstance(plan.expected_move_points, (int, float))
        and isinstance(q.delta, (int, float))
        and abs(float(q.delta)) > 0
    ):
        expected_premium_move = round(
            abs(float(plan.expected_move_points)) * abs(float(q.delta)), 4
        )
    elif isinstance(plan.target1, (int, float)) and float(plan.target1) > entry:
        # Fall back to the published T1 distance in premium terms, which is a
        # plan quantity rather than a forecast. Named in the reasons so the two
        # are never confused.
        expected_premium_move = round(float(plan.target1) - entry, 4)
        out["reasons"].append("MOVE_FROM_T1_DISTANCE")

    if spread is not None:
        if risk:
            out["spread_over_risk"] = round(spread / risk, 4)
            out["risk_after_spread"] = round(risk - spread, 4)
        if expected_premium_move:
            out["spread_over_expected_move"] = round(spread / expected_premium_move, 4)
            out["room_after_spread"] = round(expected_premium_move - spread, 4)
    if out["cost_points"] is not None:
        cp = float(out["cost_points"])
        if risk:
            out["cost_over_risk"] = round(cp / risk, 4)
        if expected_premium_move:
            out["cost_over_expected_move"] = round(cp / expected_premium_move, 4)
    out["expected_premium_move"] = expected_premium_move
    out["risk"] = risk

    out["vehicle_class"] = classify(out)
    return out


def classify(econ: dict) -> str:
    """GREEN / YELLOW / RED / UNKNOWN from computed ratios.

    UNKNOWN whenever the cost was not measured from a real book: a modelled
    spread can produce a GREEN that no one could have traded, which is the exact
    way the previous gross reports flattered themselves.
    """
    if econ.get("cost_status") != schema.COST_MEASURED:
        return UNKNOWN
    cor = econ.get("cost_over_risk")
    com = econ.get("cost_over_expected_move")
    spct = econ.get("spread_pct")
    if cor is None and com is None:
        return UNKNOWN
    if isinstance(spct, (int, float)) and float(spct) > RED_SPREAD_PCT:
        return RED
    if isinstance(cor, (int, float)) and float(cor) > RED_COST_OVER_RISK:
        return RED
    if isinstance(com, (int, float)) and float(com) > RED_COST_OVER_MOVE:
        return RED
    green = True
    if isinstance(cor, (int, float)) and float(cor) > GREEN_COST_OVER_RISK:
        green = False
    if isinstance(com, (int, float)) and float(com) > GREEN_COST_OVER_MOVE:
        green = False
    return GREEN if green else YELLOW


def cost_leg(
    instrument: str,
    entry_premium: float | None,
    exit_premium: float | None,
    *,
    quoted_spread: float | None,
    lots: int = 1,
    lot_size: int | None = None,
) -> dict:
    """Gross, cost and net for one resolved leg — §12.

    Slippage is charged from configuration on top of the spread, because buying
    at the ask does not guarantee the ask: the book moves between the quote and
    the fill. The three components stay separate in the output so a reader can
    remove the modelled part and keep the measured one.
    """
    out = {
        "gross_points": None,
        "cost_points": None,
        "cost_rupees": None,
        "slippage_points": None,
        "net_points": None,
        "cost_status": schema.COST_UNKNOWN,
        "spread_source": None,
    }
    if not isinstance(entry_premium, (int, float)) or float(entry_premium) <= 0:
        return out
    entry = float(entry_premium)
    lot = lot_size or _lot_size(instrument)
    if not lot:
        return out
    exit_px = float(exit_premium) if isinstance(exit_premium, (int, float)) else None
    cost = option_costs.round_trip(
        instrument, entry, exit_px, int(lot), lots, quoted_spread=quoted_spread,
    )
    if cost is None:
        return out
    # Two sides, because the round trip pays it on the way in and on the way out.
    slip = round(2.0 * entry * float(settings.ai_slippage_pct) / 100.0, 4)
    out["slippage_points"] = slip
    out["cost_points"] = round(cost.cost_points + slip, 4)
    out["cost_rupees"] = round(cost.cost_rupees + slip * int(lot) * max(1, lots), 2)
    out["spread_source"] = cost.spread_source
    if exit_px is not None:
        gross = round(exit_px - entry, 4)
        out["gross_points"] = gross
        out["net_points"] = round(gross - float(out["cost_points"]), 4)
    # MEASURED means "this leg's NET is real": a measured book on entry AND a
    # resolved exit. An unresolved leg keeps its cost figures but stays UNKNOWN,
    # so the OOS sample can filter on one field and never admit a half-row.
    out["cost_status"] = (
        schema.COST_MEASURED
        if cost.spread_source == option_costs.MEASURED and exit_px is not None
        else schema.COST_UNKNOWN
    )
    return out


def family_spread_reference(instrument: str) -> dict:
    """The modelled spread this instrument would be charged with no book.

    Printed beside measured spreads in the reports so the two are comparable and
    a reader can see how far the assumption was from the truth.
    """
    return {
        "family": fam.family(instrument),
        "assumed_spread_pct": option_costs.assumed_spread_pct(instrument),
    }
