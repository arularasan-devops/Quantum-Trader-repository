"""Phase 50 §2 — what the exchange clock says about one instant. Pure, no I/O.

Deterministic by construction: the same timestamp, instrument and inputs give
the same status on any machine, and nothing here reads a store, a feed or a
description of the day. The holiday list is passed in — loading it is the
service's job — so this module can be tested without a file and cannot silently
depend on one being present.

Two refusals are the whole value of the module.

**HOLIDAY is only ever asserted from a list.** This process has no exchange
holiday feed. A weekday that was a holiday looks exactly like a weekday whose
feed died, and both look like a session. So with no list on disk the status is
never ``HOLIDAY``; it is ``UNKNOWN`` when the evidence contradicts an open
session and ``OPEN`` when it does not.

**A frozen book cannot be called open.** ``moved=False`` means the capture saw
the same bid/ask on every poll of that contract, which no quoting market does.
That reads ``UNKNOWN``, not ``OPEN`` — the existing capture-health module found a
whole weekend of rows written that way, and a status field that called them OPEN
would launder them into trading outcomes.
"""
from __future__ import annotations

import datetime as dt

from app.market.instruments import REGISTRY
from app.research.phase50 import (
    BY_CALENDAR,
    BY_CLOCK,
    BY_FROZEN_BOOK,
    BY_NO_TIMESTAMP,
    BY_WEEKEND,
    CLOSED,
    EQUITY_CLOSE_MIN,
    EQUITY_OPEN_MIN,
    EQUITY_PRE_OPEN_MIN,
    EXCHANGE_MCX,
    HOLIDAY,
    MCX_CLOSE_MIN,
    MCX_OPEN_MIN,
    MCX_PRE_OPEN_MIN,
    NO_HOLIDAY_LIST,
    OPEN,
    POST_CLOSE,
    PRE_OPEN,
    SEGMENT_EQUITY,
    SEGMENT_MCX,
    UNKNOWN,
)

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def segment(instrument: str | None) -> str:
    """Which set of session windows an instrument quotes under.

    Read from the production instrument registry rather than a hand-kept list,
    so a newly configured commodity cannot be graded against equity hours.
    """
    spec = REGISTRY.get((instrument or "").strip().upper())
    if spec is not None and spec.exchange.strip().upper() == EXCHANGE_MCX:
        return SEGMENT_MCX
    return SEGMENT_EQUITY


def windows(seg: str) -> tuple[int, int, int]:
    """``(pre_open_min, open_min, close_min)`` in IST minutes for one segment."""
    if seg == SEGMENT_MCX:
        return (MCX_PRE_OPEN_MIN, MCX_OPEN_MIN, MCX_CLOSE_MIN)
    return (EQUITY_PRE_OPEN_MIN, EQUITY_OPEN_MIN, EQUITY_CLOSE_MIN)


def session_date(ts: float) -> str:
    """The IST calendar date of an instant, as ``YYYY-MM-DD``."""
    return dt.datetime.fromtimestamp(float(ts), _IST).strftime("%Y-%m-%d")


def status(
    ts: object,
    instrument: str | None = None,
    *,
    holidays: frozenset[str] = frozenset(),
    moved: bool | None = None,
) -> dict:
    """The market session status of one instant, with how it was derived.

    ``holidays`` is the exchange holiday list as ``YYYY-MM-DD`` strings, empty
    when none is on disk. ``moved`` is the capture's own evidence that the book
    for this contract changed across polls: ``True`` it moved, ``False`` it never
    did, ``None`` not checked. Only ``False`` changes the answer, and only
    inside the session window, where it is the one thing that contradicts an
    otherwise-open day.
    """
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return _out(UNKNOWN, BY_NO_TIMESTAMP, None, None, holidays, moved)
    stamp = dt.datetime.fromtimestamp(float(ts), _IST)
    day = stamp.strftime("%Y-%m-%d")
    seg = segment(instrument)
    minute = stamp.hour * 60 + stamp.minute
    if day in holidays:
        return _out(HOLIDAY, BY_CALENDAR, day, seg, holidays, moved)
    if stamp.weekday() >= 5:
        return _out(CLOSED, BY_WEEKEND, day, seg, holidays, moved)
    pre, opened, closed = windows(seg)
    if minute < pre:
        return _out(CLOSED, BY_CLOCK, day, seg, holidays, moved)
    if minute < opened:
        return _out(PRE_OPEN, BY_CLOCK, day, seg, holidays, moved)
    if minute < closed:
        if moved is False:
            return _out(UNKNOWN, BY_FROZEN_BOOK, day, seg, holidays, moved)
        return _out(OPEN, BY_CLOCK, day, seg, holidays, moved)
    return _out(POST_CLOSE, BY_CLOCK, day, seg, holidays, moved)


def _out(
    state: str,
    evidence: str,
    day: str | None,
    seg: str | None,
    holidays: frozenset[str],
    moved: bool | None,
) -> dict:
    return {
        "market_session_status": state,
        "status_evidence": evidence,
        "session_date_ist": day,
        "segment": seg,
        "book_moved": moved,
        "holiday_list_loaded": bool(holidays),
        "holiday_note": None if holidays else NO_HOLIDAY_LIST,
    }


def close_ts(ts: object, instrument: str | None = None) -> float | None:
    """The instant this instant's own session closes, for that instrument's segment.

    Derived from the same windows :func:`status` grades against, so a coverage
    diagnostic cannot disagree with the status field about when the day ended.
    ``None`` when there is no usable timestamp: an unknown close is not midnight.
    """
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    stamp = dt.datetime.fromtimestamp(float(ts), _IST)
    _, _, closed = windows(segment(instrument))
    midnight = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight + dt.timedelta(minutes=closed)).timestamp()


def summarise(states: list[str]) -> str:
    """One status for a set of observations, refusing to average two answers.

    An event whose observations disagree about the clock is ``UNKNOWN`` rather
    than the majority answer: an event that straddles the close is not an open
    one, and picking the commoner label would hide exactly that.
    """
    distinct = {s for s in states if s}
    if not distinct:
        return UNKNOWN
    if len(distinct) == 1:
        return distinct.pop()
    return UNKNOWN
