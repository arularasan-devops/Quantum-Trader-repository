"""Smoke: a paper book must never be able to report a free trade.

The defect this guards against is the one that hid a ₹7.4L paper loss: the Flow
book charged no brokerage, no statutory charges and no spread, so its P&L was
gross, a 20-second leg looked survivable, and the cheapest way to improve the
number on screen was to open more legs. So most of the assertions below are
about what the cost model must REFUSE to return — chiefly zero.
"""
from __future__ import annotations

from app.analysis import option_costs as oc
from app.config import settings
from app.execution import flow_tracker as ft

CHECKS = 0


def ok(cond: bool, msg: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    # --- the cost is never zero and never negative ------------------------
    c = oc.round_trip("NIFTY", 100.0, 105.0, 75)
    ok(c is not None, "a costable leg must return a cost")
    assert c is not None
    ok(c.cost_points > 0, "a round trip cannot cost nothing")
    ok(c.spread_points > 0, "a spread of zero is the one number that cannot be true")
    ok(c.cost_rupees > 0, "cost in rupees must follow the points")
    ok(abs(c.cost_points - (c.brokerage_points + c.tax_points + c.spread_points))
       < 0.02, "the three components must add up to the total")

    # --- an uncostable leg returns None, not a zero -----------------------
    for entry, lot in ((0.0, 75), (-5.0, 75), (100.0, 0)):
        ok(oc.round_trip("NIFTY", entry, 110.0, lot) is None,
           f"entry={entry} lot={lot} must be refused, not costed at zero")
    ok(oc.round_trip("NIFTY", None, 110.0, 75) is None,  # type: ignore[arg-type]
       "a missing premium is an unknown cost, not a free trade")

    # --- family medians: a single stock cannot be charged index costs -----
    idx = oc.round_trip("NIFTY", 100.0, 100.0, 75)
    mcx = oc.round_trip("CRUDEOIL", 100.0, 100.0, 100)
    stk = oc.round_trip("ITC", 100.0, 100.0, 1600)
    assert idx is not None and mcx is not None and stk is not None
    ok(idx.spread_pct < mcx.spread_pct < stk.spread_pct,
       "index < MCX < single stock is the measured ordering of spreads; "
       "pooling them is what hid the 25 Aug loss")
    ok(stk.spread_points > 8.0 * idx.spread_points,
       "a 9.52% single-stock spread must dwarf a 0.80% index spread")
    ok(all(x.spread_source == oc.ASSUMED for x in (idx, mcx, stk)),
       "a modelled spread must be labelled as assumed, never presented as measured")

    # --- an unknown instrument is charged the widest, not the narrowest ---
    unknown = oc.assumed_spread_pct("NOT_A_REAL_SYMBOL_XYZ")
    ok(unknown == settings.cost_spread_stock_pct,
       "an unclassifiable name is more likely illiquid than an index, so it must "
       "be charged the widest median rather than the kindest")

    # --- a quoted book always beats the assumption ------------------------
    q = oc.round_trip("ITC", 100.0, 100.0, 1600, quoted_spread=0.5)
    assert q is not None
    ok(q.spread_source == oc.MEASURED, "a quoted spread must be reported as measured")
    ok(q.spread_points == 0.5, "the measured spread must be charged as quoted")
    ok(q.cost_points < stk.cost_points,
       "a tight quoted book must cost less than the family median it replaces")

    # --- statutory charges follow the premiums, not a constant ------------
    cheap = oc.round_trip("NIFTY", 10.0, 10.0, 75)
    rich = oc.round_trip("NIFTY", 500.0, 500.0, 75)
    assert cheap is not None and rich is not None
    ok(rich.tax_points > cheap.tax_points,
       "taxes are a share of turnover, so they must scale with the premium")
    ok(cheap.brokerage_points == rich.brokerage_points,
       "brokerage is flat per order, so in premium points it cannot depend on "
       "the premium")
    ok(cheap.cost_points / 10.0 > rich.cost_points / 500.0,
       "flat brokerage is a wall on a cheap contract and a rounding error on a "
       "rich one — as a share of premium the round trip must cost more on the "
       "cheap contract")

    # --- an unresolved leg is costed against its entry --------------------
    open_leg = oc.round_trip("NIFTY", 100.0, None, 75)
    assert open_leg is not None
    ok(open_leg.cost_points > 0,
       "the round trip is committed when the position opens, so an open leg "
       "already owes its cost")

    # --- flow_tracker: net = gross - cost, and gross is left alone --------
    fields = ft._cost_fields("NIFTY", 100.0, 110.0, 75, 1)
    ok(fields["net_points"] is not None, "a costable leg must produce net points")
    ok(abs(fields["net_points"] - (10.0 - fields["cost_points"])) < 0.02,
       "net points must be gross minus cost")
    ok(fields["net_points"] < 10.0, "net cannot equal gross once a cost is charged")
    ok(fields["net_rupees"] is not None
       and abs(fields["net_rupees"] - fields["net_points"] * 75) < 0.5,
       "net rupees must be net points times the lot")

    # A leg that gains less than the round trip is a LOSS, however green its
    # premium looks. This is the case the old book reported as a win.
    small = ft._cost_fields("NIFTY", 100.0, 100.5, 75, 1)
    ok(small["net_points"] < 0 and small["net_win"] is False,
       "a +0.5 point leg does not cover a round trip and must not book as a win")

    ok(ft._cost_fields("NIFTY", None, 110.0, 75, 1) == {},
       "an uncostable leg must contribute no cost fields at all, so it cannot "
       "enter a total as a free trade")
    ok(ft._cost_fields("NIFTY", 0.0, 110.0, 75, 1) == {},
       "a zero entry premium is not a costable leg")

    # --- the verdict must name the failure mode it sees -------------------
    # cost per leg above the gross edge is the churn case: the count is the loss.
    churn = ft._cost_block(total=100, costed=100, measured=0, gross_points=50.0,
                           cost_points=200.0, cost_rupees=1000.0, net_wins=10)
    ok(churn["verdict"] == "COST_EXCEEDS_EDGE",
       "cost above the gross edge per leg must be called out, not averaged away")
    ok(churn["cost_to_gross_edge"] == 4.0,
       "the ratio must be cost per leg over gross edge per leg")
    ok(churn["net_points"] == -150.0, "net points must be gross minus cost")

    negative = ft._cost_block(total=10, costed=10, measured=0, gross_points=-5.0,
                              cost_points=20.0, cost_rupees=100.0, net_wins=0)
    ok(negative["verdict"] == "GROSS_NEGATIVE",
       "a book that loses before costs must say so rather than blame the spread")

    covered = ft._cost_block(total=10, costed=10, measured=10, gross_points=100.0,
                             cost_points=10.0, cost_rupees=50.0, net_wins=7)
    ok(covered["verdict"] == "COST_COVERED", "a genuinely paying book must pass")
    ok(covered["measured_spread_legs"] == 10
       and covered["assumed_spread_legs"] == 0,
       "measured and assumed legs must be counted separately so a reader can "
       "tell a priced cost from a modelled one")

    empty = ft._cost_block(total=5, costed=0, measured=0, gross_points=0.0,
                           cost_points=0.0, cost_rupees=0.0, net_wins=0)
    ok(empty["verdict"] == "NOT_COSTED",
       "no costable leg means unknown, which is not the same as free")

    print(f"checked {CHECKS}")
    print("option costs smoke: OK")


if __name__ == "__main__":
    main()
