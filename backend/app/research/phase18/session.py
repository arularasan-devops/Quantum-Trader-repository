"""Which CAS window are we in, and is this even a CAS day? — §2.

The window is 15:10–15:30 IST, cut into four five-minute sub-windows and five
named states. Two things in here are deliberately conservative.

**A weekday is not a session.** The spec is explicit that a calendar must decide
this rather than an assumption, and this process has no exchange holiday feed. So
``is_cas_day`` is answered from *observed* evidence — the feed reported the market
open during the window — and the field that says how it was answered travels with
every row (``day_source``). A row whose CAS-day status was assumed is marked
``ASSUMED`` and the reports count those separately, because a holiday recorded as
a flat CAS session would quietly dilute every average.

**Not every instrument behaves the same.** CAS discovers the closing price of the
~200 stocks with derivatives; an index has no auction of its own and moves
because its constituents do. That mechanism difference is real and is recorded
per instrument (``cas_mechanism``) rather than smoothed away — a Sensex print and
a single-stock print are not the same event and must never be pooled by accident.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.research.phase17 import schema as p17schema

_IST_OFFSET_SEC = 19_800

# §2 states, in order of the clock.
CAS_PREP = "CAS_PREP"          # 15:10–15:15, before the auction opens
CAS_OPEN = "CAS_OPEN"          # 15:15–15:20, order pooling begins
CAS_ACTIVE = "CAS_ACTIVE"      # 15:20–15:25, the window the 2,200-point print fell in
CAS_CLOSE = "CAS_CLOSE"        # 15:25–15:30, equilibrium price forming
POST_CAS = "POST_CAS"          # after 15:30
OUTSIDE = "OUTSIDE"            # any other time of day
STATES: tuple[str, ...] = (CAS_PREP, CAS_OPEN, CAS_ACTIVE, CAS_CLOSE, POST_CAS, OUTSIDE)

# The state a paper leg may still be opened in. Nothing opens in CAS_CLOSE by
# default: an entry at 15:26 has four minutes to work and then holds overnight
# whether or not that was intended, which is a different strategy (§16) and is
# recorded as one.
ENTRY_STATES: frozenset[str] = frozenset({CAS_PREP, CAS_OPEN, CAS_ACTIVE})

# Sub-window labels, kept separate from the state names so a later change to the
# state machine cannot silently re-bucket a stored row.
SUB_WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("W_1510_1515", 15 * 60 + 10, 15 * 60 + 15),
    ("W_1515_1520", 15 * 60 + 15, 15 * 60 + 20),
    ("W_1520_1525", 15 * 60 + 20, 15 * 60 + 25),
    ("W_1525_1530", 15 * 60 + 25, 15 * 60 + 30),
)

WINDOW_START_MIN = 15 * 60 + 10
WINDOW_END_MIN = 15 * 60 + 30
# The reference mark the whole window is measured against (§6) and the minute
# marks the underlying is sampled at.
MARK_MINUTES: tuple[int, ...] = (
    15 * 60 + 10, 15 * 60 + 15, 15 * 60 + 20, 15 * 60 + 25, 15 * 60 + 30,
)
MARK_LABELS: tuple[str, ...] = ("T1510", "T1515", "T1520", "T1525", "T1530")

# Session open, used by §16's next-morning resolution.
OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30

# How the CAS-day answer was reached. OBSERVED beats ASSUMED everywhere.
OBSERVED = "OBSERVED"
ASSUMED = "ASSUMED"

# §2's "do not assume all instruments share identical CAS behaviour".
AUCTION_DIRECT = "AUCTION_DIRECT"      # the instrument itself is auctioned
AUCTION_DERIVED = "AUCTION_DERIVED"    # an index moved by its auctioned constituents
AUCTION_NONE = "AUCTION_NONE"          # no CAS mechanism applies (e.g. MCX)

_NSE_INDICES: frozenset[str] = frozenset({
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
})
_BSE_INDICES: frozenset[str] = frozenset({"SENSEX", "BANKEX"})
_MCX: frozenset[str] = frozenset({
    "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER",
})


def ist(ts: float) -> datetime:
    """The IST wall clock for a POSIX timestamp, as a naive datetime."""
    return datetime.fromtimestamp(float(ts) + _IST_OFFSET_SEC, tz=timezone.utc).replace(
        tzinfo=None
    )


def minute_of_day(ts: float) -> int:
    d = ist(ts)
    return d.hour * 60 + d.minute


def session_date(ts: float) -> str:
    return ist(ts).strftime("%Y-%m-%d")


def weekday(ts: float) -> str:
    return ist(ts).strftime("%A").upper()


def seconds_of_day(ts: float) -> float:
    d = ist(ts)
    return d.hour * 3600.0 + d.minute * 60.0 + d.second + d.microsecond / 1e6


def state(ts: float) -> str:
    """The §2 state for a timestamp."""
    m = minute_of_day(ts)
    if m < WINDOW_START_MIN:
        return OUTSIDE
    if m < 15 * 60 + 15:
        return CAS_PREP
    if m < 15 * 60 + 20:
        return CAS_OPEN
    if m < 15 * 60 + 25:
        return CAS_ACTIVE
    if m < WINDOW_END_MIN:
        return CAS_CLOSE
    # POST_CAS only for the rest of the trading afternoon; overnight and the next
    # morning are OUTSIDE, so a 09:20 tick cannot be mistaken for a CAS tail.
    if m < 16 * 60:
        return POST_CAS
    return OUTSIDE


def sub_window(ts: float) -> str | None:
    m = minute_of_day(ts)
    for name, lo, hi in SUB_WINDOWS:
        if lo <= m < hi:
            return name
    return None


def in_window(ts: float) -> bool:
    return WINDOW_START_MIN <= minute_of_day(ts) < WINDOW_END_MIN


def remaining_seconds(ts: float) -> float | None:
    """Seconds left in the CAS window, or ``None`` outside it."""
    if not in_window(ts):
        return None
    return round(WINDOW_END_MIN * 60.0 - seconds_of_day(ts), 1)


def nearest_mark(ts: float, *, tolerance_sec: float = 30.0) -> str | None:
    """The §6 minute mark this timestamp stands for, if it is close enough.

    A mark taken 90 seconds late is not the 15:20 price and is not recorded as
    one; the tolerance is explicit so a report can state it.
    """
    sec = seconds_of_day(ts)
    best_label: str | None = None
    best_gap = tolerance_sec
    for label, minute in zip(MARK_LABELS, MARK_MINUTES):
        gap = abs(sec - minute * 60.0)
        if gap <= best_gap:
            best_label, best_gap = label, gap
    return best_label


def mechanism(instrument: str, exchange: str | None = None) -> str:
    """How CAS reaches this instrument — §2."""
    name = (instrument or "").upper()
    if name in _MCX:
        return AUCTION_NONE
    if name in _NSE_INDICES or name in _BSE_INDICES:
        return AUCTION_DERIVED
    if (exchange or "").upper() in ("NFO", "NSE", "BFO", "BSE"):
        return AUCTION_DIRECT
    return AUCTION_NONE


def exchange_of(instrument: str) -> str | None:
    from app.market.instruments import REGISTRY

    spec = REGISTRY.get((instrument or "").upper())
    return spec.exchange if spec is not None else None


def expiry_class(days_to_expiry: int | None) -> str | None:
    """EXPIRY_DAY / PRE_EXPIRY / NON_EXPIRY — shared with Phase 17 so a CAS row
    and a normal row land in the same bucket without a mapping table."""
    if days_to_expiry is None:
        return None
    d = int(days_to_expiry)
    if d <= 0:
        return p17schema.EXPIRY_DAY
    if d == 1:
        return p17schema.PRE_EXPIRY
    return p17schema.NON_EXPIRY


def cas_day(
    ts: float,
    *,
    market_open: bool | None = None,
) -> dict:
    """Is this a CAS day, and how do we know? — §2.

    ``market_open`` is the feed's own statement that the market was trading. When
    it is present the answer is OBSERVED; when it is absent the answer falls back
    to "a weekday", which is exactly the assumption §2 warns about, so the row is
    marked ASSUMED and every report counts it apart.
    """
    d = ist(ts)
    is_weekday = d.weekday() < 5
    if market_open is None:
        return {
            "is_cas_day": is_weekday,
            "day_source": ASSUMED,
            "note": (
                "No session calendar available; a weekday was assumed. A holiday "
                "recorded this way would look like a flat CAS session."
            ),
        }
    return {
        "is_cas_day": bool(market_open) and is_weekday,
        "day_source": OBSERVED,
        "note": "Feed reported the market state during the window.",
    }


def describe(
    ts: float,
    instrument: str,
    *,
    market_open: bool | None = None,
    expiry: str | None = None,
    days_to_expiry: int | None = None,
) -> dict:
    """The full §2 record for one instant."""
    exch = exchange_of(instrument)
    day = cas_day(ts, market_open=market_open)
    return {
        "session_date": session_date(ts),
        "weekday": weekday(ts),
        "instrument": (instrument or "").upper(),
        "exchange": exch,
        "cas_mechanism": mechanism(instrument, exch),
        "cas_start": "15:10",
        "cas_end": "15:30",
        "cas_state": state(ts),
        "sub_window": sub_window(ts),
        "in_window": in_window(ts),
        "remaining_seconds": remaining_seconds(ts),
        "minute_of_day": minute_of_day(ts),
        "is_cas_day": day["is_cas_day"],
        "day_source": day["day_source"],
        "is_expiry_day": (
            None if days_to_expiry is None else int(days_to_expiry) <= 0
        ),
        "expiry_date": expiry,
        "days_to_expiry": days_to_expiry,
        "expiry_class": expiry_class(days_to_expiry),
    }


def next_session_after(session: str) -> str:
    """The next weekday after a session date.

    Used only to say which date an overnight leg is WAITING for. It is not a
    holiday-aware calendar and the overnight resolver never requires the guess to
    be right: it resolves against whichever session actually arrives next.
    """
    d = date.fromisoformat(session)
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.isoformat()
