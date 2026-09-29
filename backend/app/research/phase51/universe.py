"""Phase 51 §2 — which instruments the engine may search, and on what data.

Two rules, both of which exist to stop the search widening itself onto data that
cannot carry it:

* an instrument enters only if it can carry the chronological split. Three
  discovery years, one validation year and one untouched holdout year is five
  years; an instrument with three months cannot be split and is reported
  ``INSUFFICIENT_HISTORY`` rather than searched on a shorter window and then
  ranked beside an instrument with five years;
* the source label travels with the instrument. Everything here is
  ``HISTORICAL_CANDLE_DATA``, and the engine is not permitted to forget that
  downstream: the label is on the universe row, on every candidate row and in
  the artefact header.

Imported instruments need no code change. ``phase24.data.load_series`` already
falls back to the historical-import adapter, so a registered, eligible CSV
dataset appears here automatically — and the adapter, not this module, decides
whether a dataset is eligible.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase51 import (
    HISTORICAL_CANDLE_DATA,
    INSUFFICIENT_HISTORY,
    NOT_EXECUTABLE_BOOK,
)

IST_OFFSET = 19_800

# A five-way chronological split needs five years of sessions. Below this the
# holdout is either empty or too small to refuse anything, which is worse than
# not running: it produces a holdout column that always agrees.
MIN_SESSIONS = 750
MIN_BARS = 100_000

# The instruments the engine is asked to start on. Not a restriction: anything
# with a shipped or imported series joins the run.
INITIAL = ("NIFTY", "CRUDEOIL")


def sessions_of(ts: np.ndarray) -> np.ndarray:
    """IST calendar-day index per bar."""
    return (ts + IST_OFFSET) // 86_400


def candidates() -> list[str]:
    """Every instrument with a series on disk, shipped or imported."""
    names = set(p24data.BACKTEST_FILES)
    try:
        from app.research.historical_import import registry
    except ImportError:
        registry_rows = []
    else:
        registry_rows = registry.datasets()
    for row in registry_rows:
        inst = str(row.get("instrument") or "").upper()
        if inst and int(row.get("timeframe") or 0) == 1:
            names.add(inst)
    return sorted(names)


def describe(instrument: str) -> dict:
    """One universe row: is this instrument searchable, and on what."""
    series = p24data.load_series(instrument)
    if series is None or len(series) == 0:
        return {
            "instrument": instrument.upper(),
            "eligible": False,
            "status": INSUFFICIENT_HISTORY,
            "bars": 0,
            "sessions": 0,
            "source": HISTORICAL_CANDLE_DATA,
            "reason": "no 1-minute series on disk, shipped or imported",
        }
    sessions = int(np.unique(sessions_of(series.ts)).size)
    bars = len(series)
    shipped = instrument.upper() in p24data.BACKTEST_FILES
    eligible = bars >= MIN_BARS and sessions >= MIN_SESSIONS
    row = {
        "instrument": instrument.upper(),
        "eligible": eligible,
        "status": None if eligible else INSUFFICIENT_HISTORY,
        "bars": bars,
        "sessions": sessions,
        "first_ts": int(series.ts[0]),
        "last_ts": int(series.ts[-1]),
        "volume_present": bool(np.any(series.volume > 0.0)),
        "source": HISTORICAL_CANDLE_DATA,
        "provenance": "shipped_five_year_file" if shipped else "operator_import",
        "execution_note": NOT_EXECUTABLE_BOOK,
    }
    if not eligible:
        row["reason"] = (
            f"{sessions} sessions and {bars} bars; a chronological "
            f"discovery/validation/untouched-holdout split needs at least "
            f"{MIN_SESSIONS} sessions and {MIN_BARS} bars"
        )
    return row


def survey() -> dict:
    """The whole universe, searchable and refused, with the counts behind each."""
    rows = [describe(name) for name in candidates()]
    eligible = [r for r in rows if r["eligible"]]
    return {
        "requested_initial": list(INITIAL),
        "eligible": eligible,
        "refused": [r for r in rows if not r["eligible"]],
        "min_sessions": MIN_SESSIONS,
        "min_bars": MIN_BARS,
        "source": HISTORICAL_CANDLE_DATA,
        "execution_note": NOT_EXECUTABLE_BOOK,
        "import_note": (
            "a registered, eligible 1-minute CSV dataset joins this universe "
            "with no code change; eligibility is decided by the historical "
            "import layer and is not re-judged here"
        ),
    }


def load(instrument: str) -> p24data.Series | None:
    """The series for an eligible instrument, or ``None``."""
    row = describe(instrument)
    if not row["eligible"]:
        return None
    return p24data.load_series(instrument)
