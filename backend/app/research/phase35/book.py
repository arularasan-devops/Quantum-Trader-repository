"""Phase 35 §5/§6/§7 — the two paper books and their executable economics.

Both books share this module, and that is the point: CURRENT_ENGINE_PAPER and
FULL_MARKET_PAPER must be priced by identical arithmetic or the comparison
between them measures the cost model rather than the decisions.

The one piece of arithmetic worth reading closely is the spread. A marketable
round trip pays the spread exactly once, and where the fill comes from an
executable side it is *already paid inside the two prices*: buying at 100.50 and
selling at 99.50 has paid a ₹1 spread with no further term to add. So on
executable fills this module charges brokerage and statutory charges only, and
labels the result MEASURED_EXECUTABLE. Only when a fill is taken from a traded
price (no book quoted) is a spread modelled on top, and then the result is
labelled SPREAD_MODELLED so nobody can mistake the two. §6's "never
double-charge spread" is therefore a property of the code path, not a note.

Futures are costed by the Phase 19 model and options by the Phase 17/analysis
model, kept separate as §7 requires: an index futures round trip is dominated by
STT on turnover, an option round trip on a ₹20 premium by a flat per-order fee,
and pooling them produces a number that describes neither.

No order path is imported anywhere in this module. A "paper leg" here is a row
in a research database and nothing else.
"""
from __future__ import annotations

import hashlib

from app.analysis import option_costs
from app.research.phase19 import futcosts
from app.research.phase35 import (
    CE,
    FUTURES,
    LONG,
    LOT_FROM_SPEC,
    MEASURED_EXECUTABLE,
    MEASURED_EXECUTABLE_SPEC_LOT,
    MEASURED_TRADED_PRICE,
    NO_ASK,
    NO_BID,
    NO_LOT_SIZE,
    NO_QUOTE,
    PE,
    SHORT,
    SPREAD_MODELLED,
    UNMEASURED,
)

ASK = "ASK"
BID = "BID"
TRADED = "TRADED"

# A long option is the only option expression this phase papers. Selling premium
# has a margin and an assignment profile the store does not carry, and a short
# option priced as "the reverse of a long" would understate its risk by a wide
# margin — so it is out of scope rather than approximated.
OPTION_VEHICLES = (CE, PE)


def _cost_evidence(measured: str, lot_source: str | None) -> str:
    """Degrade an executable cost label when the lot size came from the spec.

    Only the fully measured label is degraded. A cost that already reads
    SPREAD_MODELLED is below it on the same scale, and the lot provenance is
    recorded on the leg either way, so there is nothing to add there.
    """
    if measured == MEASURED_EXECUTABLE and lot_source == LOT_FROM_SPEC:
        return MEASURED_EXECUTABLE_SPEC_LOT
    return measured


def _num(v: object) -> float | None:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")) or f <= 0:
        return None
    return f


def leg_id(obs_id: str, book: str, vehicle: str) -> str:
    return hashlib.sha256(f"{obs_id}|{book}|{vehicle}".encode()).hexdigest()[:16]


def entry_fill(quote: dict, *, vehicle: str, direction: str) -> dict:
    """The price a paper entry could actually have been done at.

    Long option and long futures lift the ask; a short futures hits the bid.
    Where the required side was not quoted the answer is UNMEASURED with the
    reason, never the traded price — an LTP fill is the substitution §4 forbids
    and it is also the one that flatters every result.

    When the quote itself was already disqualified upstream (a simulator or
    fixture book), that reason is carried through instead of "no ask": the two
    look the same in a column and mean different things about the feed.
    """
    if not quote:
        return {"price": None, "side": None, "evidence": UNMEASURED,
                "reason": NO_QUOTE}
    upstream = quote.get("reason") if quote.get("evidence") == UNMEASURED else None
    bid, ask = _num(quote.get("bid")), _num(quote.get("ask"))
    if vehicle in OPTION_VEHICLES or direction == LONG:
        if ask is None:
            return {"price": None, "side": None, "evidence": UNMEASURED,
                    "reason": upstream or NO_ASK}
        return {"price": ask, "side": ASK, "evidence": MEASURED_EXECUTABLE,
                "reason": None}
    if bid is None:
        return {"price": None, "side": None, "evidence": UNMEASURED,
                "reason": upstream or NO_BID}
    return {"price": bid, "side": BID, "evidence": MEASURED_EXECUTABLE,
            "reason": None}


def exit_fill(quote: dict, *, vehicle: str, direction: str) -> dict:
    """The price a paper exit could actually have been done at.

    Falls back to the traded price only for the *path*, and says so: a traded
    exit is MEASURED_TRADED_PRICE, which downstream forces the leg's net figure
    to be labelled SPREAD_MODELLED rather than presented as executable.
    """
    if not quote:
        return {"price": None, "side": None, "evidence": UNMEASURED,
                "reason": NO_QUOTE}
    bid, ask = _num(quote.get("bid")), _num(quote.get("ask"))
    traded = _num(quote.get("traded"))
    want_bid = vehicle in OPTION_VEHICLES or direction == LONG
    px = bid if want_bid else ask
    if px is not None:
        return {"price": px, "side": BID if want_bid else ASK,
                "evidence": MEASURED_EXECUTABLE, "reason": None}
    if traded is not None:
        return {"price": traded, "side": TRADED,
                "evidence": MEASURED_TRADED_PRICE, "reason": None}
    return {"price": None, "side": None, "evidence": UNMEASURED,
            "reason": NO_BID if want_bid else NO_ASK}


def option_cost(
    instrument: str,
    *,
    entry: float,
    exit_price: float | None,
    lot_size: int | None,
    executable: bool,
    lot_source: str | None = None,
) -> dict:
    """Round-trip option cost in premium points, decomposed.

    ``executable`` decides whether the spread has already been paid by the fills.
    When it has, only brokerage and statutory charges are added. When it has not,
    the family-median spread is charged and the evidence degrades.

    ``lot_source`` is where the multiplier came from (see
    :mod:`app.research.phase35.lots`). A spec-sourced lot still produces a cost
    — the charges are arithmetic on a published contract property — but the
    evidence label says so.
    """
    lots = lot_size if isinstance(lot_size, int) and lot_size > 0 else None
    if lots is None:
        return {"cost_points": None, "evidence": UNMEASURED, "reason": NO_LOT_SIZE,
                "brokerage_points": None, "statutory_points": None,
                "spread_points": None}
    exit_px = exit_price if isinstance(exit_price, (int, float)) else entry
    ch = option_costs.charges(entry, float(exit_px), lots)
    brokerage_points = ch.brokerage / lots
    statutory_points = ch.statutory / lots
    if executable:
        return {
            "cost_points": round(brokerage_points + statutory_points, 4),
            "brokerage_points": round(brokerage_points, 4),
            "statutory_points": round(statutory_points, 4),
            "spread_points": 0.0,
            "spread_note": "already paid inside the ask-in/bid-out fills",
            "evidence": _cost_evidence(MEASURED_EXECUTABLE, lot_source),
            "lot_source": lot_source,
            "reason": None,
        }
    spread_pct = option_costs.assumed_spread_pct(instrument)
    spread_points = entry * spread_pct / 100.0
    return {
        "cost_points": round(brokerage_points + statutory_points + spread_points, 4),
        "brokerage_points": round(brokerage_points, 4),
        "statutory_points": round(statutory_points, 4),
        "spread_points": round(spread_points, 4),
        "spread_note": f"family median {spread_pct}% of premium, modelled",
        "evidence": SPREAD_MODELLED,
        "lot_source": lot_source,
        "reason": None,
    }


def futures_cost(
    instrument: str,
    *,
    entry: float,
    exit_price: float | None,
    lot_size: int | None,
    executable: bool,
    lot_source: str | None = None,
) -> dict:
    """Round-trip futures cost in index points, decomposed — §7, kept separate.

    On executable sides both the spread and the slippage are already in the
    fills, so both are passed as zero; on a traded-price fill the configured
    slippage is charged in both directions and the row is SPREAD_MODELLED.
    """
    lots = lot_size if isinstance(lot_size, int) and lot_size > 0 else None
    if lots is None:
        return {"cost_points": None, "evidence": UNMEASURED, "reason": NO_LOT_SIZE,
                "brokerage_points": None, "statutory_points": None,
                "spread_points": None}
    out = futcosts.round_trip(
        instrument,
        entry=entry,
        exit_price=exit_price if isinstance(exit_price, (int, float)) else entry,
        lot_size=lots,
        spread_points=0.0 if executable else None,
        slippage_points=0.0 if executable else None,
    )
    points = out.get("cost_points")
    qty = float(lots)
    return {
        "cost_points": points,
        "brokerage_points": (
            None if out.get("brokerage_rupees") is None
            else round(float(out["brokerage_rupees"]) / qty, 4)
        ),
        "statutory_points": out.get("statutory_points"),
        "spread_points": out.get("spread_points"),
        "slippage_points": out.get("slippage_points"),
        "spread_note": (
            "already paid inside the executable sides" if executable
            else "futures feed publishes candles, not depth; slippage modelled"
        ),
        "evidence": (
            _cost_evidence(MEASURED_EXECUTABLE, lot_source) if executable
            else SPREAD_MODELLED
        ),
        "lot_source": lot_source,
        "reason": None,
    }


def cost_of(vehicle: str, instrument: str, *, entry: float,
            exit_price: float | None, lot_size: int | None,
            executable: bool, lot_source: str | None = None) -> dict:
    if vehicle == FUTURES:
        return futures_cost(instrument, entry=entry, exit_price=exit_price,
                            lot_size=lot_size, executable=executable,
                            lot_source=lot_source)
    return option_cost(instrument, entry=entry, exit_price=exit_price,
                       lot_size=lot_size, executable=executable,
                       lot_source=lot_source)


def gross_sign(vehicle: str, direction: str) -> float:
    """+1 when the leg profits from a rising price, -1 when from a falling one.

    A long option is long its own premium whichever way the underlying is
    expressed — a PE bought on a SHORT view still profits when its premium
    rises — so the option sign comes from the premium, not from the view. Only
    futures carry the direction into the sign.
    """
    return -1.0 if (vehicle == FUTURES and direction == SHORT) else 1.0


def gross_pct(entry: float, exit_price: float, *, vehicle: str,
              direction: str) -> float:
    """Signed gross move in percent of the entry price."""
    sign = gross_sign(vehicle, direction)
    return round(100.0 * sign * (exit_price - entry) / entry, 4)


def net_pct(gross: float, cost_points: float | None, entry: float) -> float | None:
    """Gross minus the round trip, both as a percentage of the entry price."""
    if cost_points is None or entry <= 0:
        return None
    return round(gross - 100.0 * cost_points / entry, 4)
