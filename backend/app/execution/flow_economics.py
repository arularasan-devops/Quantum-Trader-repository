"""Flow entry economics — is the move this leg needs bigger than its own cost?

The Flow cost audit is the reason this exists. Over 1,169 recorded legs the book
travelled +₹26,251 gross and paid ₹93,512 of brokerage and ₹25,840 of statutory
charges, i.e. brokerage alone was 3.5x the entire gross edge, and 620 of those
legs paid a round trip worth more than a tenth of their own entry premium — none
of which was a net winner. The median hold was 20 seconds. No cap on legs per
instrument per day made the book positive, because at every cap the gross edge
collapsed toward zero while the cost per leg did not move.

So the leg count is the variable, and the honest way to cut it is to refuse the
legs that cannot pay for themselves: a cheap premium with a wide book needs a
move it is not going to get in twenty seconds.

The comparison is made in premium points:

* **cost** — the real round trip from :mod:`app.analysis.option_costs`, using the
  quoted ask-minus-bid when the feed carried a book (``MEASURED``) and the family
  median when it did not (``ASSUMED_FAMILY_MEDIAN``, and it says so).
* **expected move** — the option's own delta times the recent typical one-minute
  range of the underlying. Both terms are measured: delta comes from the chain,
  the range from the candles this engine is already reading. Nothing here models
  a target, because Flow has none.

A leg is refused when the expected move is under ``flow_cost_multiple`` times the
cost. When either term is missing the verdict is ``UNMEASURED`` and the leg is
**not** refused — an absent measurement is not evidence against a trade, and
inventing a delta to manufacture a refusal would be the same sin as inventing a
probability. The count of unmeasured entries is worth watching for that reason.
"""
from __future__ import annotations

from statistics import median

from app.analysis import option_costs as _costs
from app.config import settings
from app.market.instruments import get_spec
from app.models import Candle, OptionQuote

OK = "ECONOMIC"
REFUSED = "UNECONOMIC"
UNMEASURED = "UNMEASURED"


def _lot_size(instrument: str) -> int | None:
    """The contract's lot size, or None when it is unknown.

    None rather than 1: brokerage is charged per ORDER, so dividing it by a made-up
    lot size of one turns ₹20 into 20 premium points and would refuse every leg
    of any caller that did not name its instrument. An unknown contract cannot be
    costed, and this says so instead of guessing.
    """
    if not instrument:
        return None
    try:
        size = int(get_spec(instrument).lot_size)
    except (AttributeError, TypeError, ValueError):
        return None
    return size if size > 0 else None


def _quoted_spread(quote: OptionQuote) -> float | None:
    bid, ask = quote.bid, quote.ask
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return round(float(ask) - float(bid), 2)


def typical_range(candles: list[Candle], bars: int) -> float | None:
    """Median high-minus-low of the last ``bars`` completed one-minute candles.

    The median, not the mean: one gap candle should not license a day of cheap
    legs. Returns ``None`` rather than 0.0 when there is nothing to measure, so a
    warm-up tick reads as unmeasured instead of as a refusal.
    """
    ranges = [float(c.high) - float(c.low) for c in candles[-max(1, bars):]
              if c.high is not None and c.low is not None and c.high >= c.low]
    ranges = [r for r in ranges if r > 0]
    if not ranges:
        return None
    return round(median(ranges), 3)


def assess(instrument: str, quote: OptionQuote,
           candles: list[Candle]) -> dict:
    """Entry economics for one candidate Flow leg. Never raises, never blocks.

    Returns the two measured terms, their ratio and one verdict. The caller
    decides what to do with it — this module has no opinion about state.
    """
    lots = max(1, int(settings.flow_paper_lots))
    lot_size = _lot_size(instrument)
    premium = float(quote.premium) if quote.premium else 0.0
    spread = _quoted_spread(quote)
    cost = (_costs.round_trip(instrument, premium, None, lot_size, lots,
                              quoted_spread=spread)
            if lot_size is not None else None)
    rng = typical_range(candles, int(settings.flow_expected_move_candles))
    # A delta of exactly 0.0 on a leg with a positive premium is a placeholder,
    # not a reading — a traded option always moves with the underlying somewhat.
    # Counting it as measured would refuse the leg on a field the feed never
    # filled, so an exact zero is treated as absent.
    delta = (abs(float(quote.delta))
             if isinstance(quote.delta, (int, float)) and quote.delta != 0
             else None)

    out: dict = {
        "cost_points": cost.cost_points if cost is not None else None,
        "cost_spread_points": cost.spread_points if cost is not None else None,
        "cost_spread_source": cost.spread_source if cost is not None else None,
        "cost_pct_of_premium": (round(100.0 * cost.cost_points / premium, 2)
                                if cost is not None and premium > 0 else None),
        "underlying_range_points": rng,
        "delta": round(delta, 3) if delta is not None else None,
        "expected_move_points": None,
        "required_multiple": round(float(settings.flow_cost_multiple), 2),
        "ratio": None,
        "verdict": UNMEASURED,
        "note": "",
    }

    if cost is None or cost.cost_points <= 0:
        out["note"] = ("this leg cannot be costed — no lot size for "
                       f"{instrument or 'an unnamed instrument'}"
                       if lot_size is None else
                       "no round-trip cost could be computed for this leg")
        return out
    if rng is None or delta is None:
        missing = "the option's delta" if delta is None else "a one-minute range"
        out["note"] = (f"{missing} is not available, so the move this leg needs "
                       "cannot be compared with its cost")
        return out

    expected = delta * rng
    ratio = expected / cost.cost_points
    out["expected_move_points"] = round(expected, 2)
    out["ratio"] = round(ratio, 2)
    out["verdict"] = OK if ratio >= float(settings.flow_cost_multiple) else REFUSED
    out["note"] = (
        f"a typical {rng:.0f}-point candle moves this leg about "
        f"{expected:.1f} pts at delta {delta:.2f}, against a "
        f"{cost.cost_points:.1f} pt round trip "
        f"({'measured book' if cost.spread_source == _costs.MEASURED else 'assumed family spread'})"
    )
    return out
