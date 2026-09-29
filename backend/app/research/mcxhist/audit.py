"""Coverage, quality and rollover audit over a collected series.

The audit answers one question — *is this dataset good enough to run a study on,
and where is it not* — and it answers it with counts, not adjectives. Three
properties are worth stating because they are the difference between an audit and
a summary:

* **nothing is repaired.** A missing session stays missing, a duplicated minute
  is reported as a duplicate, and a suspicious overnight jump is flagged where it
  happened. Repair would make the file look better and the research worse;
* **a session is the unit.** Bar counts flatter a dataset: a series can hold a
  million bars and still be unusable if they are the first ninety minutes of
  every session. So coverage is computed per session against the declared
  ``SESSION_MINUTES_FULL``, and a session below the floor is ``NOT_GRADED`` —
  present in the file, counted in the audit, refused as a decision session;
* **the jump threshold is relative to the instrument.** An 8,000-point overnight
  move is nothing in SILVER and impossible in NIFTY, so a boundary is judged
  against the median absolute overnight return of its own series rather than an
  absolute number chosen by hand.

The rollover section deserves its own sentence. There is no roll to validate in
the usual sense, because the provider does not disclose one; what the scan can
do is look at the dates where a bi-monthly contract cycle *would* have rolled and
ask whether the series behaves unusually there compared with every other
overnight boundary. A clean scan does not prove the absence of a stitch — it
bounds how large a stitch artefact could be.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from app.research.mcxhist import (
    IST_OFFSET,
    JUMP_FLAG_MULTIPLE,
    MIN_SESSIONS_DAILY,
    MIN_SESSIONS_INTRADAY,
    SESSION_COVERAGE_OK,
    SESSION_COVERAGE_PARTIAL,
    SESSION_MINUTES_FULL,
    TIMEFRAMES,
    VALID_HISTORY_NO,
    VALID_HISTORY_YES,
)

COVERAGE_OK = "OK"
COVERAGE_PARTIAL = "PARTIAL"
COVERAGE_NOT_GRADED = "NOT_GRADED"

QUALITY_SUFFICIENT = "SUFFICIENT"
QUALITY_PARTIAL = "PARTIAL_COVERAGE"
QUALITY_INSUFFICIENT = "INSUFFICIENT"

ANSWERABLE = "ANSWERABLE"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


def sessions_of(ts: np.ndarray) -> np.ndarray:
    """IST calendar day of each timestamp, as an integer day number."""
    return (np.asarray(ts, dtype=np.int64) + IST_OFFSET) // 86_400


def _date(day: int) -> str:
    return (dt.date(1970, 1, 1) + dt.timedelta(days=int(day))).isoformat()


def session_coverage(ts: np.ndarray, full_minutes: int = SESSION_MINUTES_FULL) -> dict:
    """Per-session bar counts and the coverage band each session falls in."""
    if ts.size == 0:
        return {
            "sessions": 0,
            "bars": 0,
            "median_bars_per_session": 0,
            "ok": 0,
            "partial": 0,
            "not_graded": 0,
            "coverage_pct": 0.0,
            "per_session": [],
        }
    days, counts = np.unique(sessions_of(ts), return_counts=True)
    shares = counts / float(full_minutes)
    bands = np.where(
        shares >= SESSION_COVERAGE_OK,
        COVERAGE_OK,
        np.where(shares >= SESSION_COVERAGE_PARTIAL, COVERAGE_PARTIAL, COVERAGE_NOT_GRADED),
    )
    return {
        "sessions": int(days.size),
        "bars": int(ts.size),
        "median_bars_per_session": int(np.median(counts)),
        "min_bars_per_session": int(counts.min()),
        "max_bars_per_session": int(counts.max()),
        "ok": int((bands == COVERAGE_OK).sum()),
        "partial": int((bands == COVERAGE_PARTIAL).sum()),
        "not_graded": int((bands == COVERAGE_NOT_GRADED).sum()),
        "coverage_pct": round(float(counts.sum()) / (days.size * full_minutes) * 100.0, 2),
        "per_session": [
            {"date": _date(day), "bars": int(count), "band": str(band)}
            for day, count, band in zip(days, counts, bands)
        ],
    }


def by_year(coverage: dict) -> list[dict]:
    """Sessions and coverage band counts per calendar year.

    A five-year average hides a blacked-out quarter; a year row does not.
    """
    years: dict[str, dict] = {}
    for session in coverage["per_session"]:
        row = years.setdefault(
            session["date"][:4],
            {"year": session["date"][:4], "sessions": 0, "bars": 0,
             "ok": 0, "partial": 0, "not_graded": 0},
        )
        row["sessions"] += 1
        row["bars"] += session["bars"]
        band = session["band"]
        key = ("ok" if band == COVERAGE_OK
               else "partial" if band == COVERAGE_PARTIAL
               else "not_graded")
        row[key] += 1
    return [years[key] for key in sorted(years)]


def calendar_gaps(ts: np.ndarray) -> dict:
    """Weekday sessions absent from the series, and the longest run of them.

    Weekday-only because a holiday calendar is not available to this package and
    inventing one would turn a data fact into an assumption. A weekday with no
    bars is therefore "missing or holiday", named that way rather than asserted
    to be a hole.
    """
    if ts.size == 0:
        return {"missing_weekday_sessions": 0, "longest_absent_run": 0, "runs": []}
    days = np.unique(sessions_of(ts))
    present = set(int(d) for d in days)
    first, last = int(days[0]), int(days[-1])
    absent = [
        day
        for day in range(first, last + 1)
        if day not in present
        and (dt.date(1970, 1, 1) + dt.timedelta(days=day)).weekday() < 5
    ]
    runs: list[dict] = []
    start = None
    previous = None
    for day in absent:
        if start is None:
            start = day
        elif previous is not None and day != previous + 1:
            runs.append({"from": _date(start), "to": _date(previous),
                         "sessions": previous - start + 1})
            start = day
        previous = day
    if start is not None and previous is not None:
        runs.append({"from": _date(start), "to": _date(previous),
                     "sessions": previous - start + 1})
    return {
        "missing_weekday_sessions": len(absent),
        "longest_absent_run": max((r["sessions"] for r in runs), default=0),
        "runs": sorted(runs, key=lambda r: -r["sessions"])[:20],
        "basis": (
            "weekday sessions with zero bars; an exchange holiday is "
            "indistinguishable from a collection hole without a holiday "
            "calendar, so both are counted here and neither is asserted"
        ),
    }


def boundary_scan(
    ts: np.ndarray,
    close: np.ndarray,
    open_: np.ndarray,
    *,
    jump_multiple: float = JUMP_FLAG_MULTIPLE,
) -> dict:
    """Overnight discontinuities, and which of them look like stitch artefacts.

    For every pair of consecutive sessions the overnight return is the next
    session's first open against this session's last close. The flag threshold is
    ``jump_multiple`` times the median absolute overnight return of this series,
    so it scales with the instrument instead of being a number chosen by hand.
    """
    if ts.size < 2:
        return {"boundaries": 0, "flagged": 0, "median_abs_overnight_pct": 0.0,
                "threshold_pct": 0.0, "flags": []}
    days = sessions_of(ts)
    change = np.flatnonzero(np.diff(days) != 0)
    if change.size == 0:
        return {"boundaries": 0, "flagged": 0, "median_abs_overnight_pct": 0.0,
                "threshold_pct": 0.0, "flags": []}
    last_close = close[change]
    next_open = open_[change + 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        overnight = np.where(last_close > 0, (next_open - last_close) / last_close * 100.0, np.nan)
    finite = overnight[np.isfinite(overnight)]
    median_abs = float(np.median(np.abs(finite))) if finite.size else 0.0
    threshold = median_abs * jump_multiple
    flagged = np.flatnonzero(np.isfinite(overnight) & (np.abs(overnight) > threshold)) \
        if threshold > 0 else np.array([], dtype=int)
    flags = [
        {
            "session_end": _date(int(days[change[i]])),
            "session_start": _date(int(days[change[i] + 1])),
            "last_close": float(last_close[i]),
            "next_open": float(next_open[i]),
            "overnight_pct": round(float(overnight[i]), 4),
        }
        for i in flagged
    ]
    return {
        "boundaries": int(change.size),
        "flagged": int(len(flags)),
        "median_abs_overnight_pct": round(median_abs, 4),
        "threshold_pct": round(threshold, 4),
        "jump_multiple": jump_multiple,
        "flags": sorted(flags, key=lambda f: -abs(f["overnight_pct"]))[:25],
        "basis": (
            "a flag is a discontinuity, not a proven stitch, and nothing here "
            "adjusts a price to remove one"
        ),
    }


def expiry_cycle_scan(boundary: dict, cycle_months: int = 2) -> dict:
    """Do the flagged discontinuities cluster where a contract would have rolled?

    MCX GOLD and SILVER run a roughly bi-monthly cycle with expiry in the first
    week of the delivery month. If the provider stitched contracts, the artefacts
    should concentrate in those weeks. A concentration is evidence; its absence
    bounds the artefact rather than disproving it.
    """
    flags = boundary.get("flags") or []
    in_window = 0
    for flag in flags:
        day = dt.date.fromisoformat(flag["session_start"])
        if day.month % cycle_months == 0 and day.day <= 8:
            in_window += 1
    share = (in_window / len(flags)) if flags else 0.0
    return {
        "flagged": len(flags),
        "flagged_in_expiry_week": in_window,
        "share_in_expiry_week": round(share, 4),
        "cycle_months": cycle_months,
        "reading": (
            "NO_FLAGS" if not flags else
            "CLUSTERED_AT_EXPIRY_WEEK" if share >= 0.5 else
            "NOT_CLUSTERED_AT_EXPIRY_WEEK"
        ),
        "basis": (
            "a clean scan does not prove the absence of a provider stitch; it "
            "bounds how large one could be"
        ),
    }


def duplicate_scan(ts: np.ndarray) -> dict:
    if ts.size == 0:
        return {"bars": 0, "duplicate_timestamps": 0, "monotonic": True}
    unique = np.unique(ts)
    return {
        "bars": int(ts.size),
        "duplicate_timestamps": int(ts.size - unique.size),
        "monotonic": bool(np.all(np.diff(np.asarray(ts, dtype=np.int64)) > 0)),
    }


def timeframe_answerability(coverage: dict) -> dict:
    """Which timeframes this dataset can carry, by graded session count."""
    graded = coverage["ok"] + coverage["partial"]
    out: dict[str, dict] = {}
    for timeframe in TIMEFRAMES:
        floor = MIN_SESSIONS_DAILY if timeframe in ("daily", "multi_day") else MIN_SESSIONS_INTRADAY
        out[timeframe] = {
            "status": ANSWERABLE if graded >= floor else INSUFFICIENT_HISTORY,
            "graded_sessions": graded,
            "required_sessions": floor,
        }
    return out


def audit_series(
    instrument: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    full_minutes: int = SESSION_MINUTES_FULL,
    source: str = "",
    tokens: list[str] | None = None,
) -> dict:
    """The whole audit for one instrument's 1-minute series."""
    ts = np.asarray(ts, dtype=np.int64)
    coverage = session_coverage(ts, full_minutes)
    duplicates = duplicate_scan(ts)
    gaps = calendar_gaps(ts)
    boundary = boundary_scan(ts, np.asarray(close, float), np.asarray(open_, float))
    cycle = expiry_cycle_scan(boundary)
    answerability = timeframe_answerability(coverage)
    bad_bars = int(
        np.count_nonzero(
            (np.asarray(high, float) < np.asarray(low, float))
            | (np.asarray(close, float) > np.asarray(high, float) + 1e-9)
            | (np.asarray(close, float) < np.asarray(low, float) - 1e-9)
        )
    ) if ts.size else 0

    graded = coverage["ok"] + coverage["partial"]
    if graded >= MIN_SESSIONS_DAILY and coverage["ok"] >= graded * 0.75:
        quality = QUALITY_SUFFICIENT
    elif graded >= MIN_SESSIONS_INTRADAY:
        quality = QUALITY_PARTIAL
    else:
        quality = QUALITY_INSUFFICIENT

    return {
        "instrument": instrument,
        "source": source,
        "tokens": tokens or [],
        "history_start": _date(int(sessions_of(ts)[0])) if ts.size else None,
        "history_end": _date(int(sessions_of(ts)[-1])) if ts.size else None,
        "bars": int(ts.size),
        "sessions": coverage["sessions"],
        "graded_sessions": graded,
        "median_bars_per_session": coverage["median_bars_per_session"],
        "coverage_pct": coverage["coverage_pct"],
        "sessions_ok": coverage["ok"],
        "sessions_partial": coverage["partial"],
        "sessions_not_graded": coverage["not_graded"],
        "duplicate_timestamps": duplicates["duplicate_timestamps"],
        "monotonic": duplicates["monotonic"],
        "impossible_bars": bad_bars,
        "missing_weekday_sessions": gaps["missing_weekday_sessions"],
        "longest_absent_run": gaps["longest_absent_run"],
        "absent_runs": gaps["runs"],
        "by_year": by_year(coverage),
        "boundary_scan": boundary,
        "expiry_cycle_scan": cycle,
        "timeframes": answerability,
        "data_quality": quality,
        "valid_history": VALID_HISTORY_YES if quality != QUALITY_INSUFFICIENT else VALID_HISTORY_NO,
    }
