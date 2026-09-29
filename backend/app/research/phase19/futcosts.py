"""Futures round-trip cost, decomposed — Phase 19 §3.

The futures paper tool already charges a round trip (``futures_paper._cost_points``)
but returns one number, and one number cannot answer the question this phase
exists to answer: when a futures paper trade nets a loss, was it the spread, the
statutory charges or the slippage that took it? So the same arithmetic is
restated here as its parts, and :func:`round_trip` asserts nothing the existing
helper does not already charge — the total is cross-checked against it in the
smoke test rather than trusted.

Three properties are deliberate:

* charges are on TURNOVER, not on the flat fee. STT/CTT on one index lot is
  several times the brokerage, and a paper book that charges only brokerage
  reads better than any real fill;
* the **spread** is charged only when a two-sided futures book was actually
  recorded. The futures feed here publishes candles, not depth, so the honest
  value is ``None`` and the row is marked ``COST_MODELLED`` rather than having a
  spread invented for it;
* slippage never improves a result. It is added to the cost in both directions.
"""
from __future__ import annotations

from app.config import settings
from app.market.instruments import get_spec

# Cost provenance, mirroring Phase 17's vocabulary so the two books can be read
# side by side without a translation table.
COST_MEASURED = "COST_MEASURED"    # a real two-sided futures book was recorded
COST_MODELLED = "COST_MODELLED"    # statutory + brokerage + slippage only
COST_UNKNOWN = "COST_UNKNOWN"      # not enough to charge anything honestly

SPREAD_BOOK = "BOOK"
SPREAD_NONE = "UNAVAILABLE"


def _is_mcx(instrument: str) -> bool:
    """MCX pays CTT, the index exchanges pay STT. get_spec falls back rather than
    raising, so an unknown symbol is charged as an index contract."""
    return get_spec(instrument).exchange.upper() == "MCX"


def round_trip(
    instrument: str,
    *,
    entry: float | None,
    exit_price: float | None,
    lot_size: int | None,
    lots: int = 1,
    spread_points: float | None = None,
    slippage_points: float | None = None,
) -> dict:
    """Round trip for one futures paper trade, in points and in rupees.

    ``spread_points`` is the quoted spread at entry when a book existed; when it
    is ``None`` the spread component is ``None`` (not zero) and the status
    degrades to ``COST_MODELLED``.
    """
    if (
        not isinstance(entry, (int, float))
        or not isinstance(exit_price, (int, float))
        or not isinstance(lot_size, int)
        or lot_size <= 0
        or float(entry) <= 0
    ):
        return {
            "cost_points": None,
            "cost_rupees": None,
            "brokerage_rupees": None,
            "tax_rupees": None,
            "txn_rupees": None,
            "spread_points": None,
            "slippage_points": None,
            "cost_status": COST_UNKNOWN,
            "spread_source": SPREAD_NONE,
            "note": "entry, exit and lot size are required to charge a round trip",
        }

    qty = float(lot_size * max(1, int(lots)))
    buy_value = float(entry) * qty
    sell_value = float(exit_price) * qty
    tax_pct = (
        settings.futures_ctt_sell_pct if _is_mcx(instrument)
        else settings.futures_stt_sell_pct
    )
    brokerage = float(settings.futures_brokerage_per_order) * 2.0
    tax = sell_value * float(tax_pct) / 100.0
    txn = (buy_value + sell_value) * float(settings.futures_txn_pct) / 100.0
    slip = (
        float(settings.futures_slippage_points)
        if slippage_points is None else float(slippage_points)
    )
    # Both legs slip against the trade. A negative configured value would pay the
    # trader for crossing the spread, which is not a thing, so it is floored.
    slip_points = 2.0 * max(0.0, slip)

    spread = (
        float(spread_points)
        if isinstance(spread_points, (int, float)) and float(spread_points) >= 0
        else None
    )
    statutory_points = (brokerage + tax + txn) / qty
    cost_points = statutory_points + slip_points + (spread or 0.0)
    return {
        "cost_points": round(cost_points, 4),
        "cost_rupees": round(cost_points * qty, 2),
        "brokerage_rupees": round(brokerage, 2),
        "tax_rupees": round(tax, 2),
        "txn_rupees": round(txn, 2),
        "statutory_points": round(statutory_points, 4),
        "spread_points": None if spread is None else round(spread, 4),
        "slippage_points": round(slip_points, 4),
        "cost_status": COST_MEASURED if spread is not None else COST_MODELLED,
        "spread_source": SPREAD_BOOK if spread is not None else SPREAD_NONE,
        "note": (
            "spread charged from the recorded book"
            if spread is not None
            else "no two-sided futures book was recorded; spread is unmeasured, "
                 "not zero — net is therefore optimistic by one spread"
        ),
    }
