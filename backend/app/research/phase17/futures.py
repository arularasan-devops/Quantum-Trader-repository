"""The futures leg AT THE SAME SIGNAL as the option legs — Phase 21 §1.

RESEARCH ONLY. Nothing here places, sizes or gates an order.

The 1 Sep capture recorded 10,407 observations, both option sides on every one of
them, and **zero futures legs**. Every same-signal comparison in the Phase 20
report therefore read ``comparable 0 · preferred: WITHHELD``: after a full day of
capture the tool still could not say whether the call, the put or the contract
was the right way to express a market read. The schema had a ``futures`` slot on
every observation from the beginning; nothing ever filled it.

This module fills it, and the whole design is one rule:

    **a futures leg belongs to a signal only if it was quoted AT that signal.**

Not the latest price when the report ran, not the futures candle that closed
nearest to it, not the LTP from the tick after. A futures quote taken at a
different instant describes a different market, and a comparison built from one
would answer "which vehicle was luckier with its timestamp". So when the feed has
no book at the signal instant, the leg is recorded as :data:`MISSING` with the
reason — an absent comparison, stated, is worth more than a manufactured one.

Three capture states are reported separately, because they fail differently:

``EXACT``    a two-sided book within the exact tolerance of the signal.
``NEAR``     a real book, but older than exact — usable for a price, not for a
             costed fill, and counted in its own column.
``MISSING``  no feed, no price, or no two-sided book. Never a fabricated fill.

Geometry
--------
The futures leg is measured against the SAME market move as the option leg, or it
is not a comparison. The option plan's levels are premiums; they are converted to
underlying points through the selected contract's delta (the same conversion
:mod:`app.research.phase17.reach` already uses), and applied to the futures entry
price. For MCX, where the engine publishes no target at all, the levels come from
the measured futures-continuation basis in :mod:`app.research.phase17.mcx`. The
basis travels with every row.
"""
from __future__ import annotations

from app.research.phase17 import capture as p17capture
from app.research.phase17 import mcx, quality, schema

# Capture states for the §78 exact / near / missing rates.
EXACT = "EXACT"
NEAR = "NEAR"
MISSING = "MISSING"
CAPTURE_STATES: tuple[str, ...] = (EXACT, NEAR, MISSING)

# Why a futures leg is absent. Counted per reason: "no futures data" covers a
# feed that cannot quote depth and a contract that stopped trading, and the two
# call for different work.
NO_FEED = "NO_FUTURES_FEED"
NO_CONTRACT = "NO_CONTRACT"
NO_PRICE = "NO_PRICE"
NO_BOOK = "NO_TWO_SIDED_BOOK"
STALE_BOOK = "STALE_BOOK"

# Where the futures target/stop geometry came from.
BASIS_OPTION_PLAN = "SAME_SIGNAL_OPTION_PLAN_VIA_DELTA"
BASIS_MCX = mcx.BASIS
BASIS_NONE = "NO_GEOMETRY"

LONG = "LONG"
SHORT = "SHORT"


def side_of(direction: str | None) -> str | None:
    """LONG / SHORT for a market read, or None when it cannot be read.

    Unlike an option leg, a futures leg is a two-way instrument: a bearish read
    is a SHORT, not a put. Getting this wrong would flip the sign of every net R
    in the comparison, so an unrecognised word refuses.
    """
    word = str(direction or "").upper()
    if word in ("BULLISH", "LONG", "BUY", "UP"):
        return LONG
    if word in ("BEARISH", "SHORT", "SELL", "DOWN"):
        return SHORT
    return None


def quote(
    instrument: str,
    book: dict | None,
    *,
    signal_ts: float,
    capture_ts: float,
    source: str = quality.UNKNOWN_SOURCE,
) -> schema.Quote | None:
    """A :class:`schema.Quote` for the futures contract at the signal instant.

    ``book`` is whatever :meth:`app.market.provider.MarketDataProvider.futures_book`
    returned. ``None`` in, ``None`` out — a feed that cannot quote its own
    contract must leave the slot empty rather than have one invented from the
    last candle close, which is the specific shortcut this module exists to not
    take.
    """
    if not book:
        return None
    symbol = book.get("symbol")
    if not symbol:
        return None
    bid, ask = book.get("bid"), book.get("ask")
    two_sided = quality.two_sided(bid, ask)
    ltp = book.get("ltp")
    if not two_sided and not isinstance(ltp, (int, float)):
        return None
    age_sec = book.get("feed_age_sec")
    age_ms = float(age_sec) * 1000.0 if isinstance(age_sec, (int, float)) else None
    delay_ms = round((float(capture_ts) - float(signal_ts)) * 1000.0, 3)
    return schema.Quote(
        instrument=(instrument or "").upper(),
        vehicle=schema.FUTURES,
        symbol=str(symbol),
        strike=None,
        expiry=book.get("expiry"),
        days_to_expiry=(
            int(book["days_to_expiry"])
            if isinstance(book.get("days_to_expiry"), (int, float))
            else None
        ),
        bid=float(bid) if two_sided else None,
        ask=float(ask) if two_sided else None,
        # Only meaningful beside a two-sided book: a size carried without its
        # own side's price would describe a book that was never quoted.
        bid_size=_size(book.get("bid_size")) if two_sided else None,
        ask_size=_size(book.get("ask_size")) if two_sided else None,
        premium=float(ltp) if isinstance(ltp, (int, float)) else None,
        lot_size=(
            int(book["lot_size"])
            if isinstance(book.get("lot_size"), (int, float))
            and int(book["lot_size"]) > 0
            else None
        ),
        oi=_num(book.get("oi")),
        volume=_num(book.get("volume")),
        source=str(book.get("source") or source),
        feed_age_ms=age_ms,
        book_age_ms=p17capture.book_age_ms(book.get("book_ts"), capture_ts),
        signal_to_snapshot_ms=delay_ms,
        snapshot_ts=float(capture_ts),
        data_quality=quality.classify(
            signal_to_snapshot_ms=delay_ms,
            quote_age_ms=age_ms,
            has_book=two_sided,
        ),
    )


def capture_state(q: schema.Quote | None) -> tuple[str, str | None]:
    """``(EXACT | NEAR | MISSING, reason)`` for one futures quote."""
    if q is None:
        return MISSING, NO_FEED
    if not q.has_book:
        return (MISSING, NO_BOOK) if q.premium else (MISSING, NO_PRICE)
    if q.data_quality == quality.EXACT:
        return EXACT, None
    if quality.usable(q.data_quality):
        return NEAR, None
    return MISSING, STALE_BOOK


def entry_price(q: schema.Quote | None, side: str | None) -> tuple[float | None, str]:
    """What this leg would actually be filled at, and how that price was got.

    A long crosses to the ask and a short is hit on the bid — the same asymmetry
    the option tracker charges. With no book there is no fill: the LTP is
    returned as a REFERENCE price so the row can still be described, and the
    caller must not cost a leg priced from it.
    """
    if q is None or side is None:
        return None, MISSING
    if q.has_book:
        px = q.ask if side == LONG else q.bid
        if isinstance(px, (int, float)) and float(px) > 0:
            return float(px), "BOOK"
    if isinstance(q.premium, (int, float)) and float(q.premium) > 0:
        return float(q.premium), "LTP_REFERENCE_NOT_FILLABLE"
    return None, MISSING


def geometry(
    obs: schema.Observation,
    *,
    mcx_plan: dict | None = None,
) -> dict:
    """Futures entry / stop / T1-T3, in futures points, for one observation.

    The option plan is expressed in premium; the futures leg cannot use those
    numbers, and converting them is not cosmetic — it is what makes the two legs
    answer the same question, namely "the market moves this far: which vehicle
    paid best for it?".
    """
    out: dict = {
        "basis": BASIS_NONE,
        "side": None,
        "capture": MISSING,
        "capture_reason": NO_FEED,
        "entry": None,
        "entry_source": MISSING,
        "stop": None,
        "target1": None,
        "target2": None,
        "target3": None,
        "risk_points": None,
        "t1_points": None,
        "reward_to_risk": None,
        "spread": None,
        "spread_pct": None,
        "oi": None,
        "volume": None,
        "symbol": None,
        "expiry": None,
        "days_to_expiry": None,
        "feed_age_ms": None,
        "signal_to_snapshot_ms": None,
        "data_quality": quality.MISSING,
        "research_only": True,
        "reasons": [],
    }
    q = obs.futures
    state, why = capture_state(q)
    out["capture"], out["capture_reason"] = state, why
    if q is None:
        return out
    out.update({
        "symbol": q.symbol,
        "expiry": q.expiry,
        "days_to_expiry": q.days_to_expiry,
        "spread": q.spread,
        "spread_pct": q.spread_pct,
        "oi": q.oi,
        "volume": q.volume,
        "feed_age_ms": q.feed_age_ms,
        "signal_to_snapshot_ms": q.signal_to_snapshot_ms,
        "data_quality": q.data_quality,
    })
    side = side_of(obs.direction)
    out["side"] = side
    if side is None:
        out["reasons"].append("DIRECTION_UNKNOWN")
        return out
    entry, how = entry_price(q, side)
    out["entry"], out["entry_source"] = entry, how
    if entry is None:
        out["reasons"].append(NO_PRICE)
        return out
    sign = 1.0 if side == LONG else -1.0

    risk, t1, t2, t3, basis = _levels(obs, mcx_plan)
    out["basis"] = basis
    if basis == BASIS_NONE or not risk or not t1:
        out["reasons"].append("NO_UNDERLYING_LEVELS")
        return out
    out["risk_points"] = round(risk, 4)
    out["t1_points"] = round(t1, 4)
    out["stop"] = round(entry - sign * risk, 4)
    out["target1"] = round(entry + sign * t1, 4)
    out["target2"] = round(entry + sign * t2, 4) if t2 else None
    out["target3"] = round(entry + sign * t3, 4) if t3 else None
    out["reward_to_risk"] = round(t1 / risk, 4)
    return out


def _levels(
    obs: schema.Observation, mcx_plan: dict | None,
) -> tuple[float | None, float | None, float | None, float | None, str]:
    """Risk and target distances in UNDERLYING points, with their basis.

    The MCX continuation basis wins where it exists, because for a commodity the
    option plan usually has no engine target to convert in the first place — that
    is the hole §3 was raised to fill.
    """
    if mcx_plan and mcx_plan.get("verdict") == "MEASURED":
        ref = float(mcx_plan["entry_reference"])
        t1 = abs(float(mcx_plan["target1"]) - ref)
        t2 = (abs(float(mcx_plan["target2"]) - ref)
              if mcx_plan.get("target2") else None)
        t3 = (abs(float(mcx_plan["target3"]) - ref)
              if mcx_plan.get("target3") else None)
        return float(mcx_plan["stop_points"]), t1, t2, t3, BASIS_MCX

    q = obs.selected
    plan = obs.plan
    delta = abs(float(q.delta)) if q and isinstance(q.delta, (int, float)) else None
    if not delta:
        return None, None, None, None, BASIS_NONE
    ref = q.ask if (q.has_book and isinstance(q.ask, (int, float))) else q.premium
    if not isinstance(ref, (int, float)) or float(ref) <= 0:
        return None, None, None, None, BASIS_NONE
    ref = float(ref)

    def points(level: float | None) -> float | None:
        if not isinstance(level, (int, float)) or float(level) <= 0:
            return None
        gap = abs(float(level) - ref)
        return round(gap / delta, 4) if gap > 0 else None

    risk = points(plan.stop)
    t1 = points(plan.target1)
    if t1 is None and isinstance(plan.expected_move_points, (int, float)):
        # No engine target: the setup's own expected move IS the market move the
        # option row was graded against, so the futures leg is graded against the
        # same distance. Same modelled status as the option side; it never enters
        # the costed book.
        t1 = abs(float(plan.expected_move_points)) or None
    if risk is None and t1:
        return None, None, None, None, BASIS_NONE
    return risk, t1, points(plan.target2), points(plan.target3), BASIS_OPTION_PLAN


def tally(states: list[str]) -> dict:
    """§78 capture rates over a set of futures capture states."""
    counts = {s: 0 for s in CAPTURE_STATES}
    for s in states:
        counts[s if s in counts else MISSING] += 1
    total = len(states)

    def pct(n: int) -> float | None:
        return round(100.0 * n / total, 2) if total else None

    return {
        "total": total,
        "counts": counts,
        "exact_pct": pct(counts[EXACT]),
        "near_pct": pct(counts[NEAR]),
        "missing_pct": pct(counts[MISSING]),
        # The one number the previous report could not produce: on how many
        # signals a futures leg was priced at all.
        "captured_pct": pct(counts[EXACT] + counts[NEAR]),
    }


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _size(value: object) -> int | None:
    """Resting quantity, or ``None``. Zero and negatives are not sizes."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    qty = int(value)
    return qty if qty > 0 else None
