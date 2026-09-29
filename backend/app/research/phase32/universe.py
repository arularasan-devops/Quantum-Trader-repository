"""Phase 32 — every instrument that has candles, and what its history supports.

"Test it on all the instruments" is answerable only after saying, per instrument,
what the data allows. Two tiers, and the difference is not cosmetic:

``FIVE_YEAR``
    Enough bars and sessions to split 60/20/20 chronologically, so a T1 chosen on
    the development window can be confirmed on an untouched holdout.
``SHORT_CAPTURE``
    Weeks of captured candles. Reach rates can be described, but a T1 selected on
    them would be fitted to a few sessions, so none is selected and the verdict
    stays ``REQUIRES_MORE_DATA``.

No series is widened, gap-filled or resampled to reach a tier.
"""
from __future__ import annotations

import sqlite3

from app.research.phase24 import data as p24data
from app.research.phase32 import MIN_BARS_FOR_SPLIT, MIN_SESSIONS_FOR_SPLIT

FIVE_YEAR = "FIVE_YEAR"
SHORT_CAPTURE = "SHORT_CAPTURE"

# Reading a short capture out of the live store is cheap per instrument but the
# store is being written by the engine, so the shared read handle is used.
CANDLE_SQL = (
    "SELECT ts, open, high, low, close, volume FROM candles "
    "WHERE instrument = ? ORDER BY ts"
)


def _sessions_of(ts_list: list[int]) -> int:
    return len({t // 86_400 for t in ts_list})


def load_short_capture(instrument: str) -> p24data.Series | None:
    """One instrument's captured 1-minute candles from the live store.

    Read-only, parameterised, and returns ``None`` rather than raising when the
    store is unavailable: a locked store must not fail a whole study.
    """
    path = p24data._resolve(p24data.HISTORY_DB)
    try:
        con = p24data.connect_readonly(path)
    except sqlite3.Error:
        return None
    try:
        rows = con.execute(CANDLE_SQL, (instrument,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    out = [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "volume": float(r[5] or 0.0)}
        for r in rows
        if r[1] and r[2] and r[3] and r[4]
    ]
    if not out:
        return None
    return p24data.Series(instrument.upper(), out)


def load(instrument: str) -> tuple[p24data.Series | None, str]:
    """The best available series for an instrument, with its tier."""
    s = p24data.load_series(instrument)
    if s is not None and len(s) >= MIN_BARS_FOR_SPLIT:
        if _sessions_of([int(t) for t in s.ts]) >= MIN_SESSIONS_FOR_SPLIT:
            return s, FIVE_YEAR
    if s is None:
        s = load_short_capture(instrument)
    return s, SHORT_CAPTURE


def inventory() -> list[dict]:
    """Every instrument with candles, its tier and why."""
    out: list[dict] = []
    names = set(p24data.BACKTEST_FILES)
    thin = p24data._thin_instruments()
    names |= set(thin)
    for name in sorted(names):
        if name in p24data.BACKTEST_FILES:
            s = p24data.load_series(name)
            bars = len(s) if s is not None else 0
            sessions = _sessions_of([int(t) for t in s.ts]) if s is not None else 0
        else:
            bars = int(thin[name]["bars"])
            sessions = 0
        tier = (
            FIVE_YEAR
            if bars >= MIN_BARS_FOR_SPLIT and sessions >= MIN_SESSIONS_FOR_SPLIT
            else SHORT_CAPTURE
        )
        out.append({
            "instrument": name,
            "bars": bars,
            "sessions": sessions or None,
            "tier": tier,
            "why": (
                "enough history for a chronological 60/20/20 split"
                if tier == FIVE_YEAR else
                f"{bars} bars is below the {MIN_BARS_FOR_SPLIT} needed to split "
                "history, so reach rates are descriptive only"
            ),
        })
    return out
