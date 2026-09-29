"""Top-of-book recorded at a paper fill — the one number the journal never had.

Every P&L in the journal was computed from a single price per side, so the
spread was charged nowhere. That is not a small omission: the median tagged
trade was held 5.5 minutes and exited on a small trail, so on the recorded book
a 5% round-trip spread turned the PULLBACK cohort's 62% win rate into 17%. A
result that flips on a cost we never recorded is not a result.

So the book is recorded at the moment of the fill, both sides, with how stale it
was. Where the feed did not carry a book, ``cost_status`` says ``UNKNOWN`` and
the spread fields stay ``None``. A spread is never modelled here, because a
modelled spread beside a real fill price reads as if it had been measured — the
family medians in :mod:`app.analysis.option_costs` exist for research reports
that label their assumptions, not for the trade record.
"""
from __future__ import annotations

MEASURED = "MEASURED"
UNKNOWN = "UNKNOWN"
ENTRY_ONLY = "PARTIAL_ENTRY_ONLY"
EXIT_ONLY = "PARTIAL_EXIT_ONLY"

EXACT = "EXACT"
NEAR = "NEAR"
STALE = "STALE"

# A book older than this is labelled rather than trusted: at these hold times a
# 15-second-old quote is a different market.
NEAR_SEC = 2.0
STALE_SEC = 15.0


def book(bid: float | None, ask: float | None, ts: int | float | None) -> dict | None:
    """One side's top-of-book, or ``None`` when the feed carried no usable book.

    A crossed or non-positive book is rejected rather than recorded: it cannot
    have been the price a fill happened at, and averaging it would invent a
    spread.
    """
    try:
        b = float(bid) if bid is not None else None
        a = float(ask) if ask is not None else None
    except (TypeError, ValueError):
        return None
    if b is None or a is None or b <= 0 or a <= 0 or a < b:
        return None
    return {
        "bid": round(b, 2),
        "ask": round(a, 2),
        "mid": round((a + b) / 2.0, 3),
        "spread": round(a - b, 3),
        "spread_pct": round(100.0 * (a - b) / ((a + b) / 2.0), 3),
        "ts": int(ts) if isinstance(ts, (int, float)) else None,
    }


def quality(book_ts: int | None, fill_ts: int | None) -> tuple[str, float | None]:
    """Freshness label and the fill-to-book gap in seconds (``None`` if unknown)."""
    if not isinstance(book_ts, (int, float)) or not isinstance(fill_ts, (int, float)):
        return UNKNOWN, None
    delta = abs(float(fill_ts) - float(book_ts))
    if delta <= NEAR_SEC:
        return EXACT, round(delta, 1)
    if delta <= STALE_SEC:
        return NEAR, round(delta, 1)
    return STALE, round(delta, 1)


def fill_record(entry: dict | None, exit_: dict | None, qty: int,
                entry_ts: int | None = None, exit_ts: int | None = None) -> dict:
    """The book-derived part of one closed round trip.

    ``spread_cost_rupees`` is what a marketable round trip pays away against the
    mid: half the entry spread on the way in and half the exit spread on the way
    out. It is only produced when BOTH books were recorded — a half-measured
    spread understates the cost and would be worse than declaring it unknown.
    """
    if entry and exit_:
        status = MEASURED
    elif entry:
        status = ENTRY_ONLY
    elif exit_:
        status = EXIT_ONLY
    else:
        status = UNKNOWN

    eq, edelta = quality((entry or {}).get("ts"), entry_ts)
    xq, xdelta = quality((exit_ or {}).get("ts"), exit_ts)

    spread_cost = None
    if status == MEASURED and qty > 0:
        half = (float(entry["spread"]) + float(exit_["spread"])) / 2.0
        spread_cost = round(half * int(qty), 2)

    return {
        "cost_status": status,
        "entry_bid": (entry or {}).get("bid"),
        "entry_ask": (entry or {}).get("ask"),
        "entry_spread": (entry or {}).get("spread"),
        "entry_spread_pct": (entry or {}).get("spread_pct"),
        "exit_bid": (exit_ or {}).get("bid"),
        "exit_ask": (exit_ or {}).get("ask"),
        "exit_spread": (exit_ or {}).get("spread"),
        "exit_spread_pct": (exit_ or {}).get("spread_pct"),
        "spread_cost_rupees": spread_cost,
        "entry_book_quality": eq,
        "exit_book_quality": xq,
        "entry_book_age_sec": edelta,
        "exit_book_age_sec": xdelta,
    }
