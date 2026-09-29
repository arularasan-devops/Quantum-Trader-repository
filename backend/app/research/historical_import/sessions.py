"""§10 — the session model, and what a candle file is not allowed to conclude.

A dataset can say, about itself: these are the days I contain, this is when my
first and last bar of each day sits, this many bars are here and this many
spacings inside my own first-to-last window are absent.

It cannot say a day *should* have traded. A candle export carries no holiday
calendar, so an absent Tuesday is indistinguishable from a closed exchange, a
vendor who dropped the day, and an export whose range simply excluded it. Every
absent weekday is therefore :data:`NOT_GRADED` rather than a missing session,
and coverage is only ever quoted over days the dataset actually contains.

The one thing measured strictly is *within* a day: between a session's own
first and last bar the expected spacings are known, so an interior hole is a
real absence and is counted as one.
"""
from __future__ import annotations

import datetime as dt

from app.research.historical_import import NOT_GRADED

IST_OFFSET = 19_800  # +05:30, the timezone the reports are read in


def _day(ts: int, tz_offset: int) -> str:
    return dt.datetime.utcfromtimestamp(ts + tz_offset).strftime("%Y-%m-%d")


def _hhmm(ts: int, tz_offset: int) -> str:
    return dt.datetime.utcfromtimestamp(ts + tz_offset).strftime("%H:%M")


def sessions(ts: list[int], timeframe_min: int,
             *, tz_offset: int = IST_OFFSET) -> dict:
    """Per-day bar counts, interior holes, and the days nothing can be said about.

    ``incomplete`` is a comparison against the *dataset's own* modal bars-per-day,
    not against an exchange's published hours: a file may legitimately hold a
    partial first or last day, and a session model built from the file cannot
    know the difference between a short day and a short export.
    """
    if not ts:
        return {
            "sessions": [], "session_count": 0, "bars": 0,
            "missing_bars": 0, "gap_pct": None, "first": None, "last": None,
            "incomplete_sessions": [], "not_graded_weekdays": [],
            "grading": NOT_GRADED,
        }
    step = max(1, int(timeframe_min)) * 60
    per_day: dict[str, list[int]] = {}
    for t in sorted(ts):
        per_day.setdefault(_day(t, tz_offset), []).append(t)

    rows: list[dict] = []
    total_missing = 0
    total_expected = 0
    for day, stamps in sorted(per_day.items()):
        first, last = stamps[0], stamps[-1]
        expected = ((last - first) // step) + 1
        missing = max(0, expected - len(stamps))
        total_missing += missing
        total_expected += expected
        rows.append({
            "session": day,
            "start_ist": _hhmm(first, tz_offset),
            "end_ist": _hhmm(last, tz_offset),
            "bars": len(stamps),
            "expected_bars_within_span": int(expected),
            "missing_bars_within_span": int(missing),
        })

    modal = _modal([r["bars"] for r in rows])
    incomplete = [
        r["session"] for r in rows
        if modal and r["bars"] < 0.8 * modal
    ]
    for r in rows:
        r["complete"] = r["session"] not in incomplete

    # Weekdays inside the span that hold no bars at all. Named, counted, and
    # explicitly not called a failure.
    present = set(per_day)
    absent: list[str] = []
    day0 = dt.datetime.strptime(min(present), "%Y-%m-%d").date()
    day1 = dt.datetime.strptime(max(present), "%Y-%m-%d").date()
    cursor = day0
    while cursor <= day1:
        key = cursor.strftime("%Y-%m-%d")
        if cursor.weekday() < 5 and key not in present:
            absent.append(key)
        cursor += dt.timedelta(days=1)

    return {
        "sessions": rows,
        "session_count": len(rows),
        "bars": len(ts),
        "missing_bars": int(total_missing),
        "expected_bars_within_spans": int(total_expected),
        "gap_pct": (round(100.0 * total_missing / total_expected, 3)
                    if total_expected else None),
        "first": _day(min(ts), tz_offset) + " " + _hhmm(min(ts), tz_offset),
        "last": _day(max(ts), tz_offset) + " " + _hhmm(max(ts), tz_offset),
        "modal_bars_per_session": modal,
        "incomplete_sessions": incomplete,
        "not_graded_weekdays": absent,
        "not_graded_weekday_count": len(absent),
        "grading": NOT_GRADED,
        "grading_note": (
            "coverage is quoted over sessions the dataset contains; an absent "
            "weekday is not counted as a missing session because a candle file "
            "carries no holiday calendar"
        ),
    }


def _modal(counts: list[int]) -> int | None:
    if not counts:
        return None
    from collections import Counter

    return int(Counter(counts).most_common(1)[0][0])
