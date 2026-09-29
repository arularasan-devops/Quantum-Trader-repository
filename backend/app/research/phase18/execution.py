"""The executable-price model: ask in, bid out, and what is left — §13, §14, §27.

This module is the reason the phase exists. The headline that started it —
Rs 1 lakh to Rs 44 lakh between 15:15 and 15:20 — is a *marked* number. It is what
the position was worth if you could sell at the printed price. In an auction
dislocation you cannot, and the three columns here keep that distinction
permanently visible:

    THEORETICAL       marked at the mid, no costs        what a screenshot shows
    BID-EXECUTABLE    bought at ask, sold at bid         what the book allowed
    NET REALIZED      minus slippage, brokerage, taxes   what reaches the account

There is no midpoint fill and no last-traded fill. When bid/ask is missing the
answer is ``EXECUTABILITY = UNKNOWN`` and the row carries no P&L — §13 is explicit
about this, and it is the single rule most likely to be quietly broken by a
convenience fallback, so there is no fallback to break.

Slippage is a research grid (0/1/2/3 ticks), applied against the trader on both
legs. It is labelled research-only because it is an assumption, not a
measurement: nothing in the feed tells us what a real fill would have been.
"""
from __future__ import annotations

from app.analysis import option_costs
from app.config import settings
from app.market.instruments import get_spec
from app.research.phase18 import quality, schema

DEFAULT_TICK = 0.05
DEFAULT_SLIPPAGE_TICKS: tuple[int, ...] = (0, 1, 2, 3)


def tick_size() -> float:
    v = float(settings.phase18_tick_size)
    return v if v > 0 else DEFAULT_TICK


def slippage_grid() -> tuple[int, ...]:
    raw = str(settings.phase18_slippage_ticks or "")
    ticks = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ticks.append(int(part))
    return tuple(sorted(set(ticks))) or DEFAULT_SLIPPAGE_TICKS


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def entry_fill(quote: dict | None) -> dict:
    """Buy side. The ask, or nothing.

    ``quote`` is a stored Quote dict. A missing or one-sided book returns
    ``UNKNOWN`` with no price: the leg is recorded as unfillable rather than
    filled at an invented level.
    """
    if not quote:
        return {"price": None, "side": None,
                "executability": schema.EXECUTABILITY_UNKNOWN,
                "reason": "no quote at this instant"}
    bid, ask = _f(quote.get("bid")), _f(quote.get("ask"))
    if not quality.two_sided(bid, ask):
        return {"price": None, "side": None,
                "executability": schema.EXECUTABILITY_UNKNOWN,
                "reason": "no two-sided book — entry not priceable"}
    state = str(quote.get("data_quality") or quality.MISSING)
    if state not in quality.FILLABLE:
        return {"price": None, "side": None,
                "executability": schema.EXECUTABILITY_UNKNOWN,
                "reason": f"book quality {state} — too old to price a fill"}
    return {
        "price": ask, "side": "ASK", "bid": bid, "ask": ask,
        "spread": round((ask or 0.0) - (bid or 0.0), 4),
        "executability": schema.EXECUTABLE, "reason": "filled at ask",
        "data_quality": state,
    }


def exit_fill(quote: dict | None) -> dict:
    """Sell side. The bid, or nothing. Never the mid, never the LTP."""
    if not quote:
        return {"price": None, "side": None,
                "executability": schema.EXECUTABILITY_UNKNOWN,
                "reason": "no quote at this instant"}
    bid, ask = _f(quote.get("bid")), _f(quote.get("ask"))
    if not quality.two_sided(bid, ask):
        return {"price": None, "side": None,
                "executability": schema.EXECUTABILITY_UNKNOWN,
                "reason": "no two-sided book — exit not priceable"}
    state = str(quote.get("data_quality") or quality.MISSING)
    if state not in quality.FILLABLE:
        return {"price": None, "side": None,
                "executability": schema.EXECUTABILITY_UNKNOWN,
                "reason": f"book quality {state} — too old to price a fill"}
    return {
        "price": bid, "side": "BID", "bid": bid, "ask": ask,
        "spread": round((ask or 0.0) - (bid or 0.0), 4),
        "executability": schema.EXECUTABLE, "reason": "exited at bid",
        "data_quality": state,
    }


def marked_value(quote: dict | None) -> float | None:
    """The theoretical column: the mid, or the LTP when there is no book.

    Only ever used for the THEORETICAL comparison — never to price a fill.
    """
    if not quote:
        return None
    mid = _f(quote.get("mid"))
    return mid if mid is not None else _f(quote.get("premium"))


def apply_slippage(price: float, *, ticks: int, side: str) -> float:
    """Move the fill against the trader by ``ticks``."""
    delta = ticks * tick_size()
    return round(price + delta if side == "BUY" else max(0.0, price - delta), 2)


def round_trip_cost(
    instrument: str, *, entry: float, exit_price: float, lots: int = 1
) -> dict:
    """Brokerage and statutory charges for one paper round trip.

    Reuses the shared cost model so a CAS leg and a normal leg are charged by one
    implementation — two cost models would eventually disagree and the comparison
    between the strategies would become meaningless.

    ``quoted_spread`` is passed as zero deliberately. The spread is *already*
    paid here, in the prices themselves: this book buys at the ask and sells at
    the bid. Letting the shared model add a spread term on top would charge it
    twice and make every CAS leg look worse than it was.
    """
    spec = get_spec((instrument or "").upper())
    lot = int(spec.lot_size) if spec is not None else 0
    if lot <= 0:
        return {"total": 0.0, "brokerage": None, "taxes": None,
                "status": "NO_LOT_SIZE"}
    cost = option_costs.round_trip(
        instrument, entry, exit_price, lot, max(1, lots), quoted_spread=0.0,
    )
    if cost is None:
        return {"total": 0.0, "brokerage": None, "taxes": None,
                "status": "NOT_COSTABLE"}
    qty = lot * max(1, lots)
    return {
        "total": round(cost.cost_rupees, 2),
        "brokerage": round(cost.brokerage_points * qty, 2),
        "taxes": round(cost.tax_points * qty, 2),
        "status": "MEASURED_BOOK",
        "spread_charged_in_price": True,
    }


def settle(
    *,
    instrument: str,
    entry_quote: dict | None,
    exit_quote: dict | None,
    peak_quote: dict | None = None,
    lots: int = 1,
    slippage_ticks: int = 0,
) -> dict:
    """The full three-column settlement for one paper leg.

    Returns ``executability = UNKNOWN`` and no P&L whenever either side of the
    trade could not be priced off a real book. §13: do not invent a result.
    """
    ent = entry_fill(entry_quote)
    ex = exit_fill(exit_quote)
    out: dict[str, object] = {
        "entry": ent, "exit": ex, "slippage_ticks": slippage_ticks,
        "tick_size": tick_size(), "lots": lots,
        "research_only_slippage": True,
    }
    if ent["executability"] != schema.EXECUTABLE or ex["executability"] != schema.EXECUTABLE:
        out.update({
            "executability": schema.EXECUTABILITY_UNKNOWN,
            "gross_points": None, "net_points": None, "net_r": None,
            "reason": ent.get("reason") if ent["executability"] != schema.EXECUTABLE
            else ex.get("reason"),
            "note": (
                "No executable price on at least one leg. No P&L is reported: an "
                "estimate here would be indistinguishable from a measurement."
            ),
        })
        return out

    entry_px = apply_slippage(float(ent["price"]), ticks=slippage_ticks, side="BUY")
    exit_px = apply_slippage(float(ex["price"]), ticks=slippage_ticks, side="SELL")

    spec = get_spec((instrument or "").upper())
    lot = int(spec.lot_size) if spec is not None else 1
    qty = max(1, lot * max(1, lots))

    gross_pts = round(exit_px - entry_px, 2)
    costs = round_trip_cost(
        instrument, entry=entry_px, exit_price=exit_px, lots=lots,
    )
    cost_rs = float(costs.get("total") or 0.0)
    gross_rs = gross_pts * qty
    net_rs = gross_rs - cost_rs

    # The theoretical column, for the same leg, marked at the mid with no cost.
    theo_entry = marked_value(entry_quote)
    theo_peak = marked_value(peak_quote or exit_quote)
    theoretical_rs = (
        None if theo_entry is None or theo_peak is None
        else round((theo_peak - theo_entry) * qty, 2)
    )
    bid_exec_rs = (
        None if ent["price"] is None or (peak_quote is None and ex["price"] is None)
        else round(
            ((_f((peak_quote or {}).get("bid")) or float(ex["price"]))
             - float(ent["price"])) * qty, 2,
        )
    )

    out.update({
        "executability": schema.EXECUTABLE,
        "quantity": qty,
        "entry_price": entry_px,
        "exit_price": exit_px,
        "entry_spread": ent.get("spread"),
        "exit_spread": ex.get("spread"),
        "gross_points": gross_pts,
        "gross_rupees": round(gross_rs, 2),
        "spread_cost_points": round(
            (ent.get("spread") or 0.0) / 2.0 + (ex.get("spread") or 0.0) / 2.0, 2,
        ),
        "slippage_points": round(2 * slippage_ticks * tick_size(), 2),
        "cost_rupees": round(cost_rs, 2),
        "brokerage_rupees": costs.get("brokerage"),
        "tax_rupees": costs.get("taxes"),
        "net_rupees": round(net_rs, 2),
        "net_points": round(net_rs / qty, 2),
        "theoretical_rupees": theoretical_rs,
        "bid_executable_rupees": bid_exec_rs,
        "vanished_rupees": (
            None if theoretical_rs is None
            else round(theoretical_rs - net_rs, 2)
        ),
        "cost_status": costs.get("status"),
    })
    return out


def slippage_table(
    *,
    instrument: str,
    entry_quote: dict | None,
    exit_quote: dict | None,
    lots: int = 1,
) -> dict:
    """The same leg settled across the whole slippage grid — §14."""
    rows = {}
    for ticks in slippage_grid():
        s = settle(
            instrument=instrument, entry_quote=entry_quote,
            exit_quote=exit_quote, lots=lots, slippage_ticks=ticks,
        )
        rows[f"{ticks}_tick"] = {
            "net_rupees": s.get("net_rupees"),
            "net_points": s.get("net_points"),
            "executability": s.get("executability"),
        }
    return {
        "grid": rows,
        "tick_size": tick_size(),
        "note": (
            "Research assumption, not a measurement. Slippage is charged against "
            "the trader on both legs; nothing in the feed states what a real fill "
            "would have been."
        ),
    }
