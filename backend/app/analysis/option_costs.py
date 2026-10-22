"""Round-trip option cost, in premium points — the one model every book charges.

Why this exists: the Flow paper book charged nothing at all. Over 1,169 recorded
legs its gross travel was -276 points while the unpriced round trips came to
~3,120 points, so the book that looked like a small loss was a large one and the
cost was 10x the per-leg gross edge. A paper P&L that omits costs cannot be used
to decide anything, because the cheapest way to improve it is to trade more.

Three components, all of which are real money:

* **brokerage** — flat per order, so it is a rounding error on an expensive
  contract and a wall on a cheap one; charged twice (entry + exit);
* **statutory charges** — a percentage of TURNOVER, with STT on the sell side
  only, which is why they are computed from the actual premiums rather than
  bolted on as a constant;
* **the spread** — a marketable round trip buys at the ask and sells at the bid,
  so it pays the spread once. This is the largest term and the only one that is
  not knowable from the fill prices, so it is either MEASURED from a quoted book
  or taken from the family median and *labelled as an assumption*. It is never
  silently zero: a zero spread is the one number that cannot be true.

The family medians come from the 25 Aug recorded book (0.80% of premium on index
options, 1.61% on MCX, 9.52% on single stocks) — the same seeds the tradability
study uses, so a leg cannot look economic in one place and uneconomic in another.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.analysis import instrument_family as fam
from app.config import settings

MEASURED = "MEASURED"
ASSUMED = "ASSUMED_FAMILY_MEDIAN"

# Identifies which cost model produced a P&L figure. Rows written before the
# per-order correction carry ``COST_MODEL_OLD`` so a reader can tell that their
# brokerage was multiplied by the lot count; their stored numbers are left
# exactly as they were rather than rewritten under new assumptions.
COST_MODEL = "PER_ORDER_V2"
COST_MODEL_OLD = "COST_MODEL_OLD"
COST_MODEL_ANGEL = "ANGEL_ITEMIZED_V3"

# Both legs of a round trip are one order each.
ORDERS_PER_ROUND_TRIP = 2


@dataclass(frozen=True)
class Charges:
    """Brokerage and statutory charges on a round trip, itemised, in rupees.

    Separated because they behave differently and a single "cost" number hides
    which one is hurting: brokerage is flat per order (a wall on a cheap
    contract, a rounding error on an expensive one) while the statutory charges
    scale with turnover.
    """

    brokerage: float
    statutory: float
    total: float
    orders: int
    per_order: float
    entry_statutory: float
    exit_statutory: float
    model: str = COST_MODEL
    stt: float = 0.0
    exchange_txn: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0
    exchange: str = ""

    def as_dict(self) -> dict:
        return {
            "brokerage": self.brokerage,
            "statutory": self.statutory,
            "total": self.total,
            "orders": self.orders,
            "brokerage_per_order": self.per_order,
            "entry_statutory": self.entry_statutory,
            "exit_statutory": self.exit_statutory,
            "cost_model": self.model,
            "stt": self.stt,
            "exchange_txn": self.exchange_txn,
            "sebi": self.sebi,
            "stamp": self.stamp,
            "gst": self.gst,
            "exchange": self.exchange,
        }


def _exchange(instrument: str | None) -> str:
    from app.market.instruments import REGISTRY

    spec = REGISTRY.get((instrument or "").upper())
    return spec.exchange.upper() if spec is not None else ""


def charges(entry_premium: float | None, exit_premium: float | None,
            qty: int, instrument: str | None = None) -> Charges:
    """Rupee charges for buying ``qty`` at ``entry_premium`` and selling at
    ``exit_premium``, with the entry and exit orders computed separately.

    Brokerage is charged once per order regardless of how many lots the order
    carries. STT is on the sell side only; the transaction charge applies to
    both sides. An absent exit premium is costed as the entry, because the round
    trip is committed the moment the position is opened.
    """
    per_order = max(0.0, float(settings.brokerage_per_lot))
    brokerage = per_order * ORDERS_PER_ROUND_TRIP
    try:
        entry = max(0.0, float(entry_premium or 0.0))
        quantity = max(0, int(qty))
    except (TypeError, ValueError):
        entry, quantity = 0.0, 0
    exit_px = (
        max(0.0, float(exit_premium))
        if isinstance(exit_premium, (int, float)) else entry
    )
    exch = _exchange(instrument) if instrument else ""
    if settings.option_charges_itemized and exch in ("NFO", "BFO", "MCX"):
        return _angel_charges(entry * quantity, exit_px * quantity, per_order, brokerage, exch)
    txn = float(settings.option_txn_pct) / 100.0
    stt = float(settings.option_stt_sell_pct) / 100.0
    entry_stat = entry * quantity * txn
    exit_stat = exit_px * quantity * (txn + stt)
    return Charges(
        brokerage=round(brokerage, 2),
        statutory=round(entry_stat + exit_stat, 2),
        total=round(brokerage + entry_stat + exit_stat, 2),
        orders=ORDERS_PER_ROUND_TRIP,
        per_order=round(per_order, 2),
        entry_statutory=round(entry_stat, 2),
        exit_statutory=round(exit_stat, 2),
    )


def _angel_charges(buy_val: float, sell_val: float, per_order: float,
                   brokerage: float, exch: str) -> Charges:
    """Angel One option charges, itemised per contract-note line."""
    txn_pct = {"NFO": settings.option_nse_txn_pct, "BFO": settings.option_bse_txn_pct,
               "MCX": settings.option_mcx_txn_pct}[exch] / 100.0
    tax_pct = (settings.option_mcx_ctt_sell_pct if exch == "MCX"
               else settings.option_stt_sell_pct) / 100.0
    sebi_pct = settings.option_sebi_pct / 100.0
    gst_pct = settings.option_gst_pct / 100.0
    stt = sell_val * tax_pct
    txn_b, txn_s = buy_val * txn_pct, sell_val * txn_pct
    sebi_b, sebi_s = buy_val * sebi_pct, sell_val * sebi_pct
    stamp = buy_val * settings.option_stamp_buy_pct / 100.0
    gst_b = (per_order + txn_b + sebi_b) * gst_pct
    gst_s = (per_order + txn_s + sebi_s) * gst_pct
    entry_stat = txn_b + sebi_b + stamp + gst_b
    exit_stat = stt + txn_s + sebi_s + gst_s
    return Charges(
        brokerage=round(brokerage, 2),
        statutory=round(entry_stat + exit_stat, 2),
        total=round(brokerage + entry_stat + exit_stat, 2),
        orders=ORDERS_PER_ROUND_TRIP,
        per_order=round(per_order, 2),
        entry_statutory=round(entry_stat, 2),
        exit_statutory=round(exit_stat, 2),
        model=COST_MODEL_ANGEL,
        stt=round(stt, 2),
        exchange_txn=round(txn_b + txn_s, 2),
        sebi=round(sebi_b + sebi_s, 2),
        stamp=round(stamp, 2),
        gst=round(gst_b + gst_s, 2),
        exchange=exch,
    )


@dataclass(frozen=True)
class OptionCost:
    """Round-trip cost of one option leg, in premium points and in rupees."""

    cost_points: float
    cost_rupees: float
    brokerage_points: float
    tax_points: float
    spread_points: float
    spread_pct: float
    spread_source: str

    def as_dict(self) -> dict:
        return {
            "cost_points": self.cost_points,
            "cost_rupees": self.cost_rupees,
            "brokerage_points": self.brokerage_points,
            "tax_points": self.tax_points,
            "spread_points": self.spread_points,
            "spread_pct": self.spread_pct,
            "spread_source": self.spread_source,
        }


def assumed_spread_pct(instrument: str) -> float:
    """Family median spread as a % of premium, used when no book was quoted."""
    f = fam.family(instrument)
    if f == fam.INDEX:
        return float(settings.cost_spread_index_pct)
    if f == fam.MCX:
        return float(settings.cost_spread_mcx_pct)
    if f == fam.STOCK:
        return float(settings.cost_spread_stock_pct)
    # An instrument the registry cannot classify is charged the widest of the
    # three rather than the narrowest: an unknown name is more likely to be an
    # illiquid single stock than an index.
    return float(settings.cost_spread_stock_pct)


def round_trip(
    instrument: str,
    entry_premium: float,
    exit_premium: float | None,
    lot_size: int,
    lots: int = 1,
    *,
    quoted_spread: float | None = None,
) -> OptionCost | None:
    """Cost of buying at ``entry_premium`` and selling at ``exit_premium``.

    ``quoted_spread`` is an actual ask-minus-bid in premium points, when the feed
    supplied a book at entry. When it is absent the family median is charged and
    ``spread_source`` says so, so a reader can tell a measured cost from a
    modelled one instead of having to trust the total.

    Returns ``None`` when the inputs cannot support a cost at all (no premium, no
    lot size) — never a zero, which would read as a free trade.
    """
    try:
        entry = float(entry_premium)
        qty = int(lot_size) * max(1, int(lots))
    except (TypeError, ValueError):
        return None
    if entry <= 0 or qty <= 0:
        return None
    # An unresolved leg is costed against its entry, because the round trip is
    # already committed the moment the position is opened.
    exit_px = float(exit_premium) if isinstance(exit_premium, (int, float)) else entry
    if exit_px < 0:
        exit_px = 0.0

    ch = charges(entry, exit_px, qty)
    brokerage_rupees = ch.brokerage
    tax_rupees = ch.statutory
    if isinstance(quoted_spread, (int, float)) and float(quoted_spread) >= 0:
        spread_points = float(quoted_spread)
        spread_pct = round(100.0 * spread_points / entry, 3)
        source = MEASURED
    else:
        spread_pct = assumed_spread_pct(instrument)
        spread_points = entry * spread_pct / 100.0
        source = ASSUMED

    cost_points = brokerage_rupees / qty + tax_rupees / qty + spread_points
    return OptionCost(
        cost_points=round(cost_points, 2),
        cost_rupees=round(cost_points * qty, 2),
        brokerage_points=round(brokerage_rupees / qty, 3),
        tax_points=round(tax_rupees / qty, 3),
        spread_points=round(spread_points, 2),
        spread_pct=round(spread_pct, 3),
        spread_source=source,
    )
