"""The break-even hurdle of one option leg, from a MEASURED book only.

The hurdle answers: what percentage of the premium must this contract travel
before the round trip earns ₹0? It is the cost model already used everywhere
(:mod:`app.analysis.option_costs`) expressed against the entry premium, so a leg
cannot be economic here and uneconomic in a report.

Two decisions in here matter more than the arithmetic:

**The entry premium is the ASK.** A marketable buy pays the offer. Using the last
traded price would understate the hurdle by exactly the amount the filter is
meant to catch.

**No book, no hurdle.** When the feed carried no usable two-sided quote the
result is ``UNMEASURED`` with a ``None`` hurdle, and every shadow arm abstains.
The family median exists for historical reports that label their assumptions; a
live shadow decision taken on an assumed spread would be testing the assumption
rather than the filter.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.analysis import option_costs

MEASURED = "MEASURED"
UNMEASURED = "UNMEASURED"

# The two bracketing candidates the shadow runs in parallel. Neither is a
# production threshold; the discovery sweep in ``report`` reports every cutoff
# between them and the promotion rule decides whether any of them survives.
GATE_STRICT_PCT = 3.0
GATE_LOOSE_PCT = 5.0
GATES: tuple[float, ...] = (GATE_STRICT_PCT, GATE_LOOSE_PCT)

# Cutoffs the sweep evaluates, inclusive of both brackets.
SWEEP_PCT: tuple[float, ...] = (3.0, 3.25, 3.5, 3.75, 4.0, 4.25, 4.5, 4.75, 5.0)

ALLOW = "ALLOW"
REFUSE = "REFUSE"
ABSTAIN = "ABSTAIN"


@dataclass(frozen=True)
class Hurdle:
    """Break-even hurdle of one leg, and the book it was computed from."""

    status: str
    bid: float | None
    ask: float | None
    entry_premium: float | None
    spread_points: float | None
    spread_pct: float | None
    cost_points: float | None
    cost_rupees: float | None
    brokerage_points: float | None
    tax_points: float | None
    hurdle_pct: float | None
    qty: int | None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "hurdle_status": self.status,
            "bid": self.bid,
            "ask": self.ask,
            "entry_premium_ask": self.entry_premium,
            "measured_spread_points": self.spread_points,
            "measured_spread_pct": self.spread_pct,
            "round_trip_cost_points": self.cost_points,
            "round_trip_cost_rupees": self.cost_rupees,
            "brokerage_points": self.brokerage_points,
            "statutory_points": self.tax_points,
            "hurdle_pct": self.hurdle_pct,
            "qty": self.qty,
            "hurdle_reason": self.reason,
        }

    def decide(self, threshold_pct: float) -> str:
        """``ALLOW`` / ``REFUSE`` / ``ABSTAIN`` for one cutoff.

        An unmeasured row abstains. It is not refused — refusing it would credit
        the filter with avoiding trades on the grounds of missing data — and it is
        not allowed either, which would credit it with taking them.
        """
        if self.status != MEASURED or self.hurdle_pct is None:
            return ABSTAIN
        return ALLOW if self.hurdle_pct <= float(threshold_pct) else REFUSE


def _unmeasured(reason: str, bid: float | None = None,
                ask: float | None = None) -> Hurdle:
    return Hurdle(
        status=UNMEASURED, bid=bid, ask=ask, entry_premium=None,
        spread_points=None, spread_pct=None, cost_points=None,
        cost_rupees=None, brokerage_points=None, tax_points=None,
        hurdle_pct=None, qty=None, reason=reason,
    )


def compute(instrument: str, *, bid: float | None, ask: float | None,
            lot_size: int, lots: int = 1) -> Hurdle:
    """Break-even hurdle for buying one leg at the ask, or ``UNMEASURED``.

    A crossed, zero or absent book is unusable — the same rule the fill recorder
    applies — and produces ``UNMEASURED`` rather than a hurdle computed from
    whichever side happened to be present.
    """
    try:
        b = float(bid) if bid is not None else None
        a = float(ask) if ask is not None else None
        qty = int(lot_size) * max(1, int(lots))
    except (TypeError, ValueError):
        return _unmeasured("book not numeric")
    if b is None or a is None:
        return _unmeasured("no two-sided book on the feed", bid=b, ask=a)
    if b <= 0 or a <= 0:
        return _unmeasured("non-positive book", bid=b, ask=a)
    if a < b:
        return _unmeasured("crossed book", bid=b, ask=a)
    if qty <= 0:
        return _unmeasured("no lot size", bid=b, ask=a)

    spread = a - b
    cost = option_costs.round_trip(
        instrument, a, None, int(lot_size), max(1, int(lots)),
        quoted_spread=spread,
    )
    if cost is None or cost.spread_source != option_costs.MEASURED:
        return _unmeasured("cost model could not price the leg", bid=b, ask=a)
    return Hurdle(
        status=MEASURED,
        bid=round(b, 2),
        ask=round(a, 2),
        entry_premium=round(a, 2),
        spread_points=round(spread, 3),
        spread_pct=round(100.0 * spread / a, 3),
        cost_points=cost.cost_points,
        cost_rupees=cost.cost_rupees,
        brokerage_points=cost.brokerage_points,
        tax_points=cost.tax_points,
        hurdle_pct=round(100.0 * cost.cost_points / a, 3),
        qty=qty,
    )
